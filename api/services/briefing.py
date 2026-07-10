"""api/services/briefing.py — Briefing de apertura (QuantAI P1, v1 DETERMINISTA).

Arma el contenido del modal de las 10:00 ART de HOME: futuros de índices US,
dólar oficial (mayorista live MAE + A3500 BCRA) y cierres MEP/CCL con variación
vs la rueda anterior. TODO calculado de datos ya ingeridos — sin LLM: son solo
números, y la regla de oro 1 del roadmap dice que la IA nunca es la fuente de
un número. La capa de redacción IA se agrega ARRIBA de esto cuando el briefing
sume narrativa (noticias/calendario); este service es, a la vez, su fallback
determinista.

Manejo de feriados/fin de semana gratis: "cierre anterior" = los dos últimos
días CON DATOS (solo hay filas en días de rueda), no ayer calendario.

Fuentes (verificadas con scripts/diag 2026-07-10):
  - home.market_quotes 'S&P FUT'/'NASDAQ FUT' (Yahoo, cron 1min; pct_day listo)
  - valuaciones.dolar_oficial_live UST$T M/000 (MAE via PC oficina; variacion lista)
  - macro.series_macro serie DOLAR (A3500 BCRA, fixing diario)
  - valuaciones.dolar (histórico MEP/CCL → último valor por día = cierre)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from core.postgres import get_pool

_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_FUTUROS_STALE_MIN = 30  # el poller corre cada 1 min; >30 min = feed caído


def _var_pct(actual: float | None, anterior: float | None) -> float | None:
    if actual is None or not anterior:
        return None
    return round((float(actual) / float(anterior) - 1) * 100, 2)


def _futuros(cur) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT data FROM home.market_quotes WHERE symbol IN ('S&P FUT', 'NASDAQ FUT')"
    )
    out = []
    ahora = datetime.now(UTC)
    for fila in cur.fetchall():
        data = fila["data"]
        upd = data.get("updated_at")
        stale = True
        if upd:
            try:
                ts = datetime.fromisoformat(upd)
                stale = (ahora - ts) > timedelta(minutes=_FUTUROS_STALE_MIN)
            except ValueError:
                pass
        out.append({
            "label":      data.get("symbol"),
            "last":       data.get("last"),
            "pct_day":    round(data["pct_day"], 2) if data.get("pct_day") is not None else None,
            "updated_at": upd,
            "stale":      stale,
        })
    # S&P primero, NASDAQ después (orden fijo de lectura de la mesa)
    out.sort(key=lambda r: 0 if r["label"] == "S&P FUT" else 1)
    return out


def _oficial_live(cur, hoy_art) -> dict[str, Any] | None:
    """Mayorista MAE de HOY. Si el feed no operó hoy (PC apagada / sin ruedas),
    devuelve None — el front muestra 'aún sin operaciones' (nunca un número
    viejo disfrazado de vivo)."""
    cur.execute(
        """
        SELECT data, updated_at FROM valuaciones.dolar_oficial_live
        WHERE ticker = 'UST$T' AND codigo_segmento = 'M' AND codigo_plazo = '000'
        """
    )
    fila = cur.fetchone()
    if not fila:
        return None
    data, upd = fila["data"], fila["updated_at"]
    if upd is None or upd.astimezone(_ART).date() != hoy_art:
        return None
    return {
        "valor":         data.get("precioUltimo"),
        "variacion_pct": data.get("variacion"),
        "maximo":        data.get("precioMaximo"),
        "minimo":        data.get("precioMinimo"),
        "ts":            upd,
    }


def _a3500(cur) -> dict[str, Any] | None:
    cur.execute(
        "SELECT fecha, valor FROM macro.series_macro"
        " WHERE serie = 'DOLAR' ORDER BY fecha DESC LIMIT 2"
    )
    filas = cur.fetchall()
    if not filas:
        return None
    ultimo = filas[0]
    anterior = filas[1] if len(filas) > 1 else None
    return {
        "fecha":         ultimo["fecha"],
        "valor":         ultimo["valor"],
        "variacion_pct": _var_pct(ultimo["valor"], anterior["valor"] if anterior else None),
    }


def _mep_ccl(cur) -> dict[str, Any]:
    """Cierre del último día con rueda + variación vs la rueda anterior, para
    mep y ccl. 'Cierre' = último valor del día en valuaciones.dolar."""
    cur.execute(
        """
        SELECT DISTINCT ON (date(timestamp)) date(timestamp) AS dia, mep, ccl
        FROM valuaciones.dolar
        WHERE mep IS NOT NULL AND timestamp >= now() - interval '14 days'
        ORDER BY date(timestamp) DESC, timestamp DESC
        LIMIT 2
        """
    )
    filas = cur.fetchall()
    if not filas:
        return {"mep": None, "ccl": None}
    ult = filas[0]
    ant = filas[1] if len(filas) > 1 else None
    out = {}
    for campo in ("mep", "ccl"):
        out[campo] = {
            "fecha":         ult["dia"],
            "cierre":        ult[campo],
            "variacion_pct": _var_pct(ult[campo], ant[campo] if ant else None),
        }
    return out


def briefing_hoy() -> dict[str, Any]:
    """Payload completo del modal. Cada bloque puede venir None/[] si su fuente
    no tiene datos — el front decide qué renderizar (failing gracefully)."""
    hoy_art = datetime.now(_ART).date()
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        futuros = _futuros(cur)
        oficial_live = _oficial_live(cur, hoy_art)
        a3500 = _a3500(cur)
        cambios = _mep_ccl(cur)
    return {
        "fecha":        hoy_art.isoformat(),
        "generado":     datetime.now(UTC),
        "futuros":      futuros,
        "oficial_live": oficial_live,   # None → "aún sin operaciones hoy"
        "a3500":        a3500,          # último fixing BCRA + var vs anterior
        "mep":          cambios.get("mep"),
        "ccl":          cambios.get("ccl"),
    }
