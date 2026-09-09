"""core/simbolos_cuarentena.py — cuarentena PERSISTENTE de símbolos que ROFEX rechaza.

Playbook determinista (nivel 1 del auto-control, 2026-07-09): cuando ROFEX
responde "Product X don't exist" a una suscripción, el WS purga el símbolo en
memoria (core/websocket) — pero esa lección se perdía en cada reinicio y el
motor volvía a pedir el símbolo muerto TODOS los días. Acá la lección persiste:

  * agregar(bad, motivo)  — el WS registra el rechazo (upsert, cuenta rechazos).
  * excluidos()           — el armado de suscripciones filtra lo cuarentenado.

Diseño anti-falso-positivo (NUNCA un delete, siempre reversible):
  * REINTENTO AUTOMÁTICO: la exclusión solo aplica si el último rechazo tiene
    menos de _REINTENTAR_DIAS. Pasado eso el símbolo se vuelve a intentar solo:
    si ROFEX ya lo acepta (era transitorio), fluye de nuevo sin tocar nada;
    si sigue muerto, el WS lo re-registra y la cuarentena se renueva.
  * GUARDRAIL DE MASA: si un solo evento trae más de _MAX_POR_EVENTO símbolos,
    NO se persiste (huele a caída del proveedor, no a símbolos muertos) — la
    purga en memoria del WS igual salva la rueda de hoy.

Todo best-effort: un fallo de Postgres jamás tumba el WS ni frena un motor.
La tabla NO tiene pantalla: el control de Manager → OBSERVABILIDAD → CONTROLES
se dio de baja (2026-08-27) y la tab entera el 2026-09-09. Se la lee desde el AV
AGENT (la causa de fondo suele ser un ticker mal cargado o un bono vencido en el
master → corregirlo ahí es el fix definitivo).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_REINTENTAR_DIAS = 7
_MAX_POR_EVENTO = 30

_DDL = """
CREATE SCHEMA IF NOT EXISTS mercado;
CREATE TABLE IF NOT EXISTS mercado.simbolos_cuarentena (
    ticker     text PRIMARY KEY,
    motivo     text,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    rechazos   integer NOT NULL DEFAULT 1
);
"""

_ensured = False


def _ensure() -> None:
    global _ensured
    if _ensured:
        return
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()
    _ensured = True


def agregar(tickers: list[str], motivo: str) -> int:
    """Registra símbolos rechazados (upsert por ticker; renueva last_seen y
    acumula rechazos). Best-effort: devuelve cuántos persistió (0 si falló o
    saltó el guardrail de masa)."""
    tickers = [t for t in (tickers or []) if t]
    if not tickers:
        return 0
    if len(tickers) > _MAX_POR_EVENTO:
        logger.warning(
            "cuarentena: %d símbolos en UN evento (> %d) — NO se persisten "
            "(posible caída del proveedor, no símbolos muertos)",
            len(tickers), _MAX_POR_EVENTO)
        return 0
    try:
        _ensure()
        from core.postgres import get_pool
        now = datetime.now(UTC)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mercado.simbolos_cuarentena (ticker, motivo, first_seen, last_seen) "
                "VALUES (%(t)s, %(m)s, %(now)s, %(now)s) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "  last_seen = EXCLUDED.last_seen, motivo = EXCLUDED.motivo, "
                "  rechazos = mercado.simbolos_cuarentena.rechazos + 1",
                [{"t": t, "m": motivo[:300], "now": now} for t in tickers])
            conn.commit()
        return len(tickers)
    except Exception as e:
        logger.warning("cuarentena: no pude persistir %s: %s", tickers, e)
        return 0


def excluidos(reintentar_dias: int = _REINTENTAR_DIAS) -> set[str]:
    """Símbolos a EXCLUIR de las suscripciones: rechazados hace menos de
    `reintentar_dias`. Los más viejos no se devuelven → se reintentan solos.
    Best-effort: si Postgres no responde, set vacío (se suscribe todo)."""
    try:
        _ensure()
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM mercado.simbolos_cuarentena "
                "WHERE last_seen >= now() - make_interval(days => %s)",
                (int(reintentar_dias),))
            return {r[0] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("cuarentena: no pude leer excluidos: %s", e)
        return set()
