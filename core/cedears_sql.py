"""core/cedears_sql.py — el MASTER de CEDEARs (`mercado.cedears`): leer y dar de alta.

**Una fila en `mercado.cedears` (activo=true) cablea TODO el universo de renta
variable**, y nadie más decide qué CEDEAR existe para el sistema:

  - `engines/motor_cedears` lo suscribe (lee el master al arrancar y, desde
    2026-09-04, lo RELEE cada `RELECTURA_S` y suma lo nuevo sin reiniciar).
  - `jobs/precios_acciones_daily` trae los cierres EOD del `underlying` (Yahoo).
  - `jobs/adr_live` trae el ADR live del `underlying` (Finnhub, cada 15').
  - `jobs/cedears_ohlc_daily` / `cedears_bars_1m` archivan lo que el motor escribe.
  - el Scanner (`/renta-variable`) y Manager → TÍTULOS → RENTA VARIABLE lo listan.

Hasta el 2026-09-04 el alta la hacían DOS scripts a mano (`add_cedear`,
`add_cedears_bulk`) con el mismo `INSERT … ON CONFLICT` copiado, y el editor de
Manager sólo edita lo que ya existe. Acá vive **la puerta única de escritura**
(REGLA #9 B del repo): el arreglo `alta_cedear` del agente y el script entran
por acá, así no puede haber dos ideas distintas de qué es «dar de alta».

⚠️ `ON CONFLICT (ticker)` **no pisa** `rubro`/`es_ia`/`ric`/`ratio`: son columnas
que carga la mesa en Manager y un alta repetida no las puede borrar. Lo que sí
refresca es `ticker_corto`/`underlying`/`activo` y el blob `data`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS = ("ticker", "ticker_corto", "underlying", "activo", "rubro", "es_ia",
         "ric", "ratio", "data")


def cargar_master(*, solo_activos: bool = False) -> list[dict]:
    """Todas las filas del master, con las columnas materializadas y el blob.

    `sin_cedear` (ADR/ETF sin pata BYMA) vive SOLO en el blob: se sube a la fila
    para que quien filtre no tenga que abrir `data`.
    """
    sql = f"SELECT {', '.join(_COLS)} FROM mercado.cedears"
    if solo_activos:
        sql += " WHERE activo IS TRUE"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        filas = cur.fetchall()
    out = []
    for f in filas:
        d = dict(zip(_COLS, f, strict=True))
        blob = dict(d.get("data") or {})
        d["data"] = blob
        d["sin_cedear"] = bool(blob.get("sin_cedear"))
        d["nombre"] = blob.get("nombre") or d.get("ticker_corto") or ""
        out.append(d)
    return out


def simbolo_de(ticker_corto: str, *, plazo: str = "24hs") -> str:
    """El símbolo BYMA con la GRAMÁTICA de Primary: `MERV - XMEV - <ticker> - <plazo>`.

    Es gramática, no identidad: arma el string que después se VERIFICA contra la
    ficha de Primary (`core.instrumentos_validos.fichas`). Nunca se afirma que un
    CEDEAR existe porque este string se pudo armar.
    """
    return f"MERV - XMEV - {ticker_corto.strip().upper()} - {plazo}"


def alta(ticker_corto: str, *, simbolo: str, underlying: str | None = None,
         nombre: str | None = None, sector: str | None = None,
         ratio_cedear: float | None = None, actor: str = "") -> dict:
    """Da de alta (o reactiva) UN CEDEAR en `mercado.cedears`. Idempotente.

    Devuelve `{ticker_corto, simbolo, underlying, existia, antes, despues}` para
    que quien escribe pueda dejar la línea del libro con el valor viejo y el
    nuevo. No valida contra Primary: eso lo hace el que llama ANTES (el agente
    con la ficha; el script confía en quien lo corre).
    """
    tc = (ticker_corto or "").strip().upper()
    if not tc:
        raise ValueError("ticker_corto vacío")
    sim = (simbolo or "").strip()
    if not sim:
        raise ValueError(f"{tc}: sin símbolo de mercado no hay nada que suscribir")
    und = (underlying or tc).strip().upper()
    doc = {
        "ticker": sim, "ticker_corto": tc, "underlying": und,
        "nombre": (nombre or tc).strip(), "sector": sector,
        "ratio_cedear": ratio_cedear, "sin_cedear": False, "activo": True,
        "alta_por": actor or None,
        "alta_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ticker_corto, underlying, activo "
                    "FROM mercado.cedears WHERE ticker = %s", (sim,))
        previo = cur.fetchone()
        cur.execute(
            "INSERT INTO mercado.cedears (ticker, ticker_corto, underlying, activo, data) "
            "VALUES (%s, %s, %s, true, %s) "
            "ON CONFLICT (ticker) DO UPDATE SET ticker_corto = EXCLUDED.ticker_corto, "
            "  underlying = EXCLUDED.underlying, activo = true, "
            # El blob se MERGEA: lo que ya estaba (sector, ratio cargados a mano)
            # sobrevive, lo nuevo pisa sólo sus claves.
            "  data = COALESCE(mercado.cedears.data, '{}'::jsonb) || EXCLUDED.data",
            (sim, tc, und, Jsonb({k: v for k, v in doc.items() if v is not None})))
        conn.commit()
    antes = ("no estaba" if previo is None
             else f"{previo[1]} → {previo[2]} · activo={'sí' if previo[3] else 'no'}")
    logger.info("cedears_sql.alta: %s (%s) underlying=%s por=%s existía=%s",
                tc, sim, und, actor or "?", previo is not None)
    return {"ticker_corto": tc, "simbolo": sim, "underlying": und,
            "existia": previo is not None, "antes": antes,
            "despues": f"{tc} → {und} · activo=sí"}
