"""Universo dinámico para motor_portfolio_snapshot.

Función única `tickers_de_tenencia()` que devuelve el set de symbols
pyRofex (formato `MERV - XMEV - X - 24hs`) a los que el motor de
portfolio se debe suscribir para tener `last_price` live.

Composición:
  - portafolio.assets.instrumento de unidades con tenencia hoy
    (portafolio.tenencia último snapshot, aum='si', qty != 0).
  - Tickers de boletos operados hoy en SQL operaciones.negocio_movimientos
    (vía join con portafolio.assets.instrumento si está cargado).

Filtrado: cada candidato se valida contra manager.pyrofex_instruments
(SQL, poblada por scripts/discovery_pyrofex.py) — si no existe en la lista
canónica primary 24hs, se descarta y se loguea (visible para corrección manual).

Read-only (todo SQL). Excepciones se capturan en el caller.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.postgres import get_pool

ART_OFFSET = timedelta(hours=-3)
_PLACEHOLDERS = {"", "NO APLICA"}


def _hoy_art_iso() -> str:
    return (datetime.now(UTC) + ART_OFFSET).strftime("%Y-%m-%d")


def _set_pyrofex_24hs() -> set[str]:
    """Symbols pyRofex con formato `MERV - XMEV - * - 24hs` desde
    manager.pyrofex_instruments (SQL) — la fuente canónica para validación.

    Equivale al find Mongo `{}` proyectando `instruments.ticker`: desanida el
    array jsonb `instruments` de cada CFI (`jsonb_array_elements`) y filtra el
    formato 24hs primary. El `trim` replica el `.strip()` del path Mongo; los
    patrones LIKE van como parámetros para no colisionar con el `%` de psycopg."""
    out: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT trim(inst->>'ticker') AS ticker "
            "FROM manager.pyrofex_instruments p, "
            "     jsonb_array_elements(p.instruments) AS inst "
            "WHERE trim(inst->>'ticker') LIKE %s "
            "  AND trim(inst->>'ticker') LIKE %s",
            ("MERV - XMEV - %", "% - 24hs"),
        )
        for (t,) in cur.fetchall():
            if t:
                out.add(t)
    return out


def _instrumentos_de_tenencia() -> set[str]:
    """INSTRUMENTOs de portafolio.assets para unidades con qty != 0 en el último
    snapshot de portafolio.tenencia (aum='si')."""
    out: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        fecha = cur.fetchone()[0]
        if not fecha:
            return set()
        cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si' AND COALESCE(cantidad, 0) <> 0 "
                    "AND unidad IS NOT NULL", (fecha,))
        unidades = [r[0] for r in cur.fetchall()]
        if not unidades:
            return set()
        cur.execute("SELECT instrumento FROM portafolio.assets WHERE unidad = ANY(%s)",
                    (unidades,))
        for (inst,) in cur.fetchall():
            inst = (inst or "").strip()
            if inst and inst not in _PLACEHOLDERS:
                out.add(inst)
    return out


def _instrumentos_de_boletos_hoy() -> set[str]:
    """INSTRUMENTOs de los tickers operados hoy. Join boleto.ticker →
    portafolio.assets.ticker → assets.instrumento. Si el boleto opera un
    ticker que aún no tiene Asset, queda fuera (caso edge sin solución
    automática — se reporta en sin_match).

    Lee SQL `operaciones.negocio_movimientos` (writer SQL-native
    jobs/negocio_movimientos.py). La colección Mongo CashFlow.NegocioMovimientos
    quedó CONGELADA tras el cutover del writer → leerla daría boletos stale."""
    fecha = _hoy_art_iso()
    out: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        # DISTINCT ticker de los boletos de hoy (equivale al .distinct() Mongo).
        cur.execute(
            "SELECT DISTINCT ticker FROM operaciones.negocio_movimientos "
            "WHERE fecha = %s::date AND ticker IS NOT NULL",
            (fecha,),
        )
        tickers_hoy = [t for (t,) in cur.fetchall() if t]
        if not tickers_hoy:
            return set()
        # Match directo por TICKER corto humano de portafolio.assets.
        cur.execute("SELECT instrumento FROM portafolio.assets WHERE ticker = ANY(%s)",
                    (tickers_hoy,))
        for (inst,) in cur.fetchall():
            inst = (inst or "").strip()
            if inst and inst not in _PLACEHOLDERS:
                out.add(inst)
    return out


def tickers_de_tenencia() -> tuple[set[str], set[str]]:
    """Devuelve (universo_validado, sin_match).

    universo_validado: instrumentos de tenencia + boletos hoy, filtrados
                       contra manager.pyrofex_instruments (SQL).
    sin_match: candidatos que NO existen en pyRofex — visible en log
               para corrección manual.

    Si SQL falla, ambos sets se devuelven vacíos.
    """
    try:
        canonico = _set_pyrofex_24hs()
        if not canonico:
            return set(), set()
        candidatos = _instrumentos_de_tenencia() | _instrumentos_de_boletos_hoy()
        validados = candidatos & canonico
        sin_match = candidatos - canonico
        return validados, sin_match
    except Exception:
        return set(), set()
