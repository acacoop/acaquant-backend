"""Helpers para mercado.adhoc_subscriptions — suscripciones live efímeras.

Aísla los tickers que el user pidió desde el Dashboard de Operar pero que
NO están en Trading.Curvas ni en config.TICKERS_EXTRA_PRECIOS. El motor
los suscribe vía pyRofex en runtime (cada 5s polleando esta tabla) y los
persiste en MarketSnapshot como cualquier otro.

La definición vive en `mercado.adhoc_subscriptions`. La tabla vieja
fue migrada → este módulo escribe/lee SOLO Postgres.

Diseño:
- PK = ticker full ROFEX. Único.
- TTL: Postgres NO tiene TTL nativo → los reads filtran `expires_at > now()`
  (semántica TTL en lectura) y un cron (`prune_expired`) borra las vencidas.
- Cap TTL_DAYS desde `last_used_at`: cada subscribe()/bump_last_used() refresca
  expires_at = now + TTL_DAYS. Si nadie lo usa en 7 días, vence.
- Cap CAP global con DESALOJO LRU: si al pedir una nueva ya hay CAP filas vivas,
  se desaloja la MENOS usada recientemente (la de `last_used_at` más viejo) para
  hacerle lugar — SIEMPRE que esa no esté en uso ahora (ver PROTEGIDA abajo).
  Solo se rechaza con 429 si las CAP están todas activas de verdad.

Por qué LRU y no rechazo (fix 2026-07-23): cada ticker que alguien abre queda
suscripto 7 días. El cupo se llenaba de papeles que la mesa miró hace días y ya
no usa, y rechazar el ticker NUEVO era exactamente al revés — la card no traía
nada aunque el mercado estuviera abierto (se midió 50/50 con
todas las filas de ayer). Desalojar la más vieja recupera esos slots muertos
sin tocar lo que se está usando.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.postgres import get_pool

TABLE = "adhoc_subscriptions"            # mercado.adhoc_subscriptions (search_path)

TTL_DAYS = 7
CAP = 50
# Una card abierta pollea el book cada ~1s y eso refresca `last_used_at`. Por
# encima de este umbral, una fila ya NO puede ser una card abierta → es basura
# desalojable. Debajo, está PROTEGIDA: preferimos rechazar (429) antes que
# tirar abajo la suscripción de algo que alguien está mirando en vivo.
PROTEGIDA_S = 60


def ensure_indexes() -> None:
    """No-op: el índice (PK ticker + ix expires_at) lo crea schema.sql.
    Se mantiene por compat con los callers de startup (motor/API)."""
    return None


def subscribe(ticker: str) -> dict:
    """Upsert: agrega o refresca un ticker adhoc.

    Si ya existe (vivo), refresca last_used_at y expires_at (rolling TTL).
    Si es nuevo y se alcanzó el cap, devuelve {"ok": False, "reason": "cap"}.
    """
    ticker = (ticker or "").strip()
    if not ticker:
        return {"ok": False, "reason": "ticker vacío"}

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=TTL_DAYS)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE ticker = %s AND expires_at > now()", (ticker,))
        existing = cur.fetchone() is not None
        if existing:
            cur.execute(
                f"UPDATE {TABLE} SET last_used_at = %s, expires_at = %s WHERE ticker = %s",
                (now, expires_at, ticker))
            cur.execute(f"SELECT count(*) FROM {TABLE} WHERE expires_at > now()")
            return {
                "ok": True, "ticker": ticker, "created": False,
                "expires_at": expires_at, "active_count": cur.fetchone()[0],
            }

        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE expires_at > now()")
        active = cur.fetchone()[0]
        if active >= CAP:
            # Cupo lleno: hacer lugar desalojando la fila MENOS usada, pero solo
            # si es basura (nadie la tocó en PROTEGIDA_S). Si TODAS las CAP están
            # activas de verdad, no hay nada que tirar → 429 honesto.
            cur.execute(
                f"DELETE FROM {TABLE} WHERE ticker = ("
                f"  SELECT ticker FROM {TABLE} "
                f"  WHERE expires_at > now() AND last_used_at < %s "
                f"  ORDER BY last_used_at ASC LIMIT 1)",
                (now - timedelta(seconds=PROTEGIDA_S),))
            if (cur.rowcount or 0) == 0:
                return {"ok": False, "reason": "cap", "ticker": ticker,
                        "active_count": active, "cap": CAP}
            active -= 1

        # ON CONFLICT cubre el caso de una fila vencida (aún no pruneada) con el mismo ticker.
        cur.execute(
            f"INSERT INTO {TABLE} (ticker, created_at, last_used_at, expires_at) "
            f"VALUES (%s, %s, %s, %s) "
            f"ON CONFLICT (ticker) DO UPDATE SET "
            f"created_at = EXCLUDED.created_at, last_used_at = EXCLUDED.last_used_at, "
            f"expires_at = EXCLUDED.expires_at",
            (ticker, now, now, expires_at))
        return {"ok": True, "ticker": ticker, "created": True,
                "expires_at": expires_at, "active_count": active + 1}


def bump_last_used(ticker: str) -> bool:
    """Extiende el TTL si la fila existe (viva). No crea nada nuevo."""
    now = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET last_used_at = %s, expires_at = %s "
            f"WHERE ticker = %s AND expires_at > now()",
            (now, now + timedelta(days=TTL_DAYS), ticker))
        return (cur.rowcount or 0) > 0


def list_active_tickers() -> list[str]:
    """Tickers vivos (expires_at > now())."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT ticker FROM {TABLE} WHERE expires_at > now()")
            return [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        return []


def count_active() -> int:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {TABLE} WHERE expires_at > now()")
            return cur.fetchone()[0]
    except Exception:
        return 0


def remove(ticker: str) -> bool:
    """Borrado manual (no esperar al TTL)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE ticker = %s", (ticker,))
        return (cur.rowcount or 0) > 0


def prune_expired() -> int:
    """Borra las filas vencidas (lo llama un cron — reemplaza el TTL de Mongo)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE expires_at <= now()")
        return cur.rowcount or 0
