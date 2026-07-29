"""core/estrategia_sql.py — I/O SQL del modelo ESTRATEGIA QUANT.

Doc vivo: docs/ESTRATEGIA_QUANT.md. Dos responsabilidades:
  1. READERS de los inputs del motor (OHLC último, snapshot live, costumbre,
     correlaciones diarias) — todo de tablas que YA existen.
  2. LEDGER (schema `estrategia`): señales append-only, resultados, pesos
     versionados y la evaluación live por ticker.

Lo importan engines/estrategia.py (escribe) y api/services/estrategia.py
(lee) — por eso vive en core/ (única capa común a ambos).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.postgres import get_pool

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Readers de inputs (tablas existentes)
# ──────────────────────────────────────────────────────────────────────────
def master_cedears(tickers: list[str]) -> dict[str, dict]:
    """{ticker_corto: {ticker (largo BYMA), underlying}} del master activo."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT ticker_corto, ticker, underlying FROM mercado.cedears
               WHERE activo IS TRUE AND ticker_corto = ANY(%s)""",
            (tickers,),
        )
        return {r["ticker_corto"]: r for r in cur.fetchall()}


def ohlc_ultima_rueda(tickers: list[str]) -> dict[str, dict]:
    """{ticker_corto: {fecha, high, low, close}} de la última rueda guardada
    (mercado.cedears_ohlc_daily) — la base de los pivots ARS del día."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT DISTINCT ON (ticker_corto) ticker_corto, fecha, high, low, close
               FROM mercado.cedears_ohlc_daily
               WHERE ticker_corto = ANY(%s)
               ORDER BY ticker_corto, fecha DESC""",
            (tickers,),
        )
        return {r["ticker_corto"]: r for r in cur.fetchall()}


def snapshot_live(tickers_largos: list[str]) -> dict[str, dict]:
    """{ticker_largo: {last, open, high, low}} del snapshot live de CEDEARs
    (mercado.cedears_snapshot, data jsonb del motor_cedears)."""
    if not tickers_largos:
        return {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ticker, data FROM mercado.cedears_snapshot WHERE ticker = ANY(%s)",
            (tickers_largos,),
        )
        out: dict[str, dict] = {}
        for r in cur.fetchall():
            d = r["data"] or {}
            out[r["ticker"]] = {
                "last": float(d.get("last") or 0) or None,
                "open": float(d.get("open") or 0) or None,
                "high": float(d.get("high") or 0) or None,
                "low": float(d.get("low") or 0) or None,
            }
        return out


def rango_promedio(tickers: list[str], ventana_ruedas: int = 20) -> dict[str, float]:
    """{ticker: rango_pct promedio de las últimas ~N ruedas} desde
    mercado.day_trading_stats (data jsonb → rango_pct). La "costumbre"."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """WITH ult AS (
                   SELECT DISTINCT fecha FROM mercado.day_trading_stats
                   ORDER BY fecha DESC LIMIT %s
               )
               SELECT ticker, avg((data->>'rango_pct')::numeric) AS rango_prom
               FROM mercado.day_trading_stats
               WHERE fecha IN (SELECT fecha FROM ult)
                 AND ticker = ANY(%s)
                 AND (data->>'rango_pct') IS NOT NULL
               GROUP BY ticker""",
            (ventana_ruedas, tickers),
        )
        return {r["ticker"]: float(r["rango_prom"]) for r in cur.fetchall()
                if r["rango_prom"] is not None}


def closes_diarios(ticker: str, n: int = 90) -> list[float]:
    """Últimos N closes diarios del subyacente USD (mercado.precios_acciones),
    asc. Para la correlación papel↔índice."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT close FROM (
                   SELECT fecha, close FROM mercado.precios_acciones
                   WHERE ticker = %s AND close IS NOT NULL
                   ORDER BY fecha DESC LIMIT %s
               ) t ORDER BY fecha ASC""",
            (ticker.upper(), n),
        )
        return [float(r[0]) for r in cur.fetchall()]


def minutos_desde(ticker_corto: str, desde: datetime) -> list[tuple[datetime, float]]:
    """[(minuto, close)] asc del tape de HOY (mercado.cedears_time_sales) desde
    `desde`. Lo usa el resolver — ¡la tabla se vacía al cierre!"""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT date_trunc('minute', ts) AS m,
                      (array_agg(price ORDER BY ts DESC, id DESC))[1] AS c
               FROM mercado.cedears_time_sales
               WHERE ticker_corto = %s AND ts >= %s AND price > 0
               GROUP BY date_trunc('minute', ts)
               ORDER BY m""",
            (ticker_corto.upper(), desde),
        )
        return [(r[0], float(r[1])) for r in cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────
# Pesos del modelo (versionados)
# ──────────────────────────────────────────────────────────────────────────
def pesos_activos() -> tuple[str, dict[str, float]]:
    """(version, pesos) de la fila activa de estrategia.modelo_pesos. Si la
    tabla está vacía, SIEMBRA v1 (quant.estrategia.PESOS_V1) y la devuelve."""
    from quant.estrategia import PESOS_V1, PESOS_V1_VERSION
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version, pesos FROM estrategia.modelo_pesos WHERE activo LIMIT 1")
        row = cur.fetchone()
        if row:
            return row["version"], dict(row["pesos"])
        cur.execute(
            """INSERT INTO estrategia.modelo_pesos (version, pesos, activo, notas)
               VALUES (%s, %s, true, 'seed inicial — hipótesis manual (sin calibrar)')
               ON CONFLICT (version) DO UPDATE SET activo = true""",
            (PESOS_V1_VERSION, Jsonb(PESOS_V1)),
        )
        conn.commit()
        return PESOS_V1_VERSION, dict(PESOS_V1)


# ──────────────────────────────────────────────────────────────────────────
# Ledger — escritura (engine / resolver)
# ──────────────────────────────────────────────────────────────────────────
def insertar_senal(
    *, ticker: str, indice_ref: str, direccion: str, score: float,
    precio: float | None, pesos_version: str, factores: dict[str, Any],
) -> int:
    """Appendea una señal al ledger (INMUTABLE — jamás se edita). → id."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO estrategia.senales
                   (ticker, indice_ref, direccion, score, precio, pesos_version, factores)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (ticker, indice_ref, direccion, score, precio, pesos_version, Jsonb(factores)),
        )
        senal_id = cur.fetchone()[0]
        conn.commit()
        return int(senal_id)


def upsert_eval_live(ticker: str, data: dict[str, Any]) -> None:
    """Última evaluación por ticker (se emita señal o no) → zona LIVE de la vista."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO estrategia.eval_live (ticker, ts, data)
               VALUES (%s, now(), %s)
               ON CONFLICT (ticker) DO UPDATE SET ts = now(), data = EXCLUDED.data""",
            (ticker, Jsonb(data)),
        )
        conn.commit()


def ultima_senal_reciente(ticker: str, minutos: int) -> dict | None:
    """La señal más reciente del ticker dentro de los últimos `minutos`
    (para el cooldown de emisión del engine). None si no hay."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id, ts, direccion, score FROM estrategia.senales
               WHERE ticker = %s AND ts >= %s
               ORDER BY ts DESC LIMIT 1""",
            (ticker, datetime.now(UTC) - timedelta(minutes=minutos)),
        )
        return cur.fetchone()


def senales_pendientes(horizonte_min: int) -> list[dict]:
    """Señales de HOY cuyo horizonte ya venció y NO tienen resultado para ese
    horizonte. Para el resolver (idempotente vía PK + ON CONFLICT)."""
    corte = datetime.now(UTC) - timedelta(minutes=horizonte_min)
    hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT s.id, s.ts, s.ticker, s.direccion, s.precio
               FROM estrategia.senales s
               LEFT JOIN estrategia.resultados r
                 ON r.senal_id = s.id AND r.horizonte_min = %s
               WHERE s.ts >= %s AND s.ts <= %s AND r.senal_id IS NULL
               ORDER BY s.ts""",
            (horizonte_min, hoy, corte),
        )
        return cur.fetchall()


def senales_de_hoy_sin_resultado(horizonte_min: int) -> list[dict]:
    """TODAS las señales de hoy sin resultado a ese horizonte (incluye las que
    aún no vencieron) — para el cierre parcial de fin de rueda."""
    hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT s.id, s.ts, s.ticker, s.direccion, s.precio
               FROM estrategia.senales s
               LEFT JOIN estrategia.resultados r
                 ON r.senal_id = s.id AND r.horizonte_min = %s
               WHERE s.ts >= %s AND r.senal_id IS NULL
               ORDER BY s.ts""",
            (horizonte_min, hoy),
        )
        return cur.fetchall()


def insertar_resultado(
    *, senal_id: int, horizonte_min: int, ret_pct: float | None,
    mfe_pct: float | None, mae_pct: float | None,
    toco_objetivo: bool | None, gano: bool | None, parcial: bool = False,
) -> None:
    """Resultado de una señal a un horizonte. ON CONFLICT DO NOTHING →
    re-correr el resolver no pisa nada (el primer resultado es el que vale)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO estrategia.resultados
                   (senal_id, horizonte_min, ret_pct, mfe_pct, mae_pct,
                    toco_objetivo, gano, parcial)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (senal_id, horizonte_min) DO NOTHING""",
            (senal_id, horizonte_min, ret_pct, mfe_pct, mae_pct,
             toco_objetivo, gano, parcial),
        )
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# Ledger — lectura (API)
# ──────────────────────────────────────────────────────────────────────────
def evals_live() -> list[dict]:
    """Todas las evaluaciones live (zona LIVE). [{ticker, ts, ...data}]."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT ticker, ts, data FROM estrategia.eval_live ORDER BY ticker")
        out = []
        for r in cur.fetchall():
            d = dict(r["data"]) if r["data"] else {}
            d["ticker"] = r["ticker"]
            d["ts"] = r["ts"].isoformat() if r["ts"] else None
            out.append(d)
        return out


def senales_resueltas(dias: int = 30, limite: int = 300) -> list[dict]:
    """Señales con sus resultados (todas las filas de resultados por señal),
    desc por ts. Para la tabla del track-record y la evaluación de edge."""
    desde = datetime.now(UTC) - timedelta(days=dias)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT s.id, s.ts, s.ticker, s.indice_ref, s.direccion, s.score,
                      s.precio, s.pesos_version, s.factores,
                      r.horizonte_min, r.ret_pct, r.mfe_pct, r.mae_pct,
                      r.toco_objetivo, r.gano, r.parcial
               FROM estrategia.senales s
               JOIN estrategia.resultados r ON r.senal_id = s.id
               WHERE s.ts >= %s
               ORDER BY s.ts DESC, r.horizonte_min
               LIMIT %s""",
            (desde, limite * 3),  # ~3 horizontes por señal
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ts"] = d["ts"].isoformat() if d["ts"] else None
        for k in ("score", "precio", "ret_pct", "mfe_pct", "mae_pct"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        if isinstance(d.get("factores"), str):
            d["factores"] = json.loads(d["factores"])
        out.append(d)
    return out
