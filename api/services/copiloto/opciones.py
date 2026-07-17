"""copiloto/opciones.py — vista OPCIONES (derivados): cadena GGAL, IV, griegas."""
from __future__ import annotations

import logging

from .base import _fmtn

logger = logging.getLogger(__name__)

def _fetch_opciones(params: dict | None = None) -> list[dict]:
    """La chain vigente (mercado.options_snapshot vía opciones_sql, @cached 60s).
    Mismo filtro que la vista: solo contratos con ALGÚN precio (bid/offer/last) —
    un strike sin cotizar no aporta nada y quema tokens. IV en FRACCIÓN → %."""
    from api.services import opciones_sql

    filas: list[dict] = []
    for o in opciones_sql.get_opciones() or []:
        if not ((o.get("last") or 0) > 0 or (o.get("bid") or 0) > 0
                or (o.get("offer") or 0) > 0):
            continue
        f = dict(o)
        if f.get("iv") is not None:
            f["iv"] = float(f["iv"]) * 100
        filas.append(f)
    filas.sort(key=lambda f: (str(f.get("vence") or ""),
                              float(f.get("strike") or 0), str(f.get("tipo") or "")))
    return filas


def _extras_opciones(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    partes: list[str] = []
    try:
        from api.services import opciones_sql

        meta = opciones_sql.get_opciones_meta() or {}
        tasa = meta.get("tasa")
        vrl, vra = meta.get("vr_local"), meta.get("vr_adr")
        partes.append(
            "[referencias] tasa libre de riesgo "
            f"{_fmtn(tasa * 100 if tasa else None, 1)}% · vol realizada del subyacente "
            f"(40 ruedas): local {_fmtn(vrl * 100 if vrl else None, 1)}% · "
            f"ADR {_fmtn(vra * 100 if vra else None, 1)}%"
        )
    except Exception as e:
        logger.warning("copiloto opciones: meta falló (%s)", e)

    # [resumen por vencimiento] — agregados deterministas (regla de oro 1)
    spot = next((f.get("spot") for f in filas if f.get("spot")), None)
    por_vto: dict[str, dict] = {}
    for f in filas:
        v = str(f.get("vence") or "?")
        g = por_vto.setdefault(v, {"calls": 0, "puts": 0, "vol": 0.0,
                                   "strikes": [], "atm": None, "atm_dist": None})
        if str(f.get("tipo") or "").upper().startswith("C"):
            g["calls"] += 1
        else:
            g["puts"] += 1
        g["vol"] += float(f.get("ev") or 0)
        st = f.get("strike")
        if st is not None:
            g["strikes"].append(float(st))
            if spot and f.get("iv") is not None:
                dist = abs(float(st) - float(spot))
                if g["atm_dist"] is None or dist < g["atm_dist"]:
                    g["atm_dist"], g["atm"] = dist, float(f["iv"])
    if por_vto:
        partes.append(f"[resumen por vencimiento — spot del subyacente {_fmtn(spot)}]")
        for v, g in sorted(por_vto.items()):
            rng = (f"{min(g['strikes']):.0f} a {max(g['strikes']):.0f}"
                   if g["strikes"] else "—")
            partes.append(
                f"{v}: {g['calls']} calls · {g['puts']} puts · strikes {rng} · "
                f"volumen efectivo {g['vol']:.0f} · IV del strike más cercano al spot "
                f"{_fmtn(g['atm'], 1)}%"
            )
    return partes


_REGLAS_OPCIONES = """Sos el copiloto de la vista OPCIONES (derivados) — la cadena de \
opciones sobre GGAL (el spot de cada fila es el precio del subyacente).

Columnas: contrato · tipo (CALL/PUT) · strike (precio de ejercicio) · vence · compra/venta/\
ultima_prima (lo que COTIZA LA OPCIÓN — la prima, no el subyacente) · cierre_ant · \
vol_efectivo (cuánto se operó ese contrato) · iv% (volatilidad implícita anualizada) · \
delta/gamma/theta/vega (griegas ya calculadas por el sistema — JAMÁS las recalcules) · \
spot_subyacente.

Cómo se lee acá:
- Fuera de rueda la chain muestra el último cierre — si preguntan por "ahora", aclaralo.
- IV vs la vol realizada del bloque [referencias]: IV bien arriba de la realizada = el \
mercado paga caro el seguro; abajo = lo paga barato. Compará SOLO con esos números dados.
- Las griegas se traducen a lenguaje de mesa: delta ≈ cuánto acompaña al subyacente (y una \
idea de probabilidad de terminar en el dinero), theta = lo que la prima pierde por día, \
vega = cuánto la mueve un cambio de vol. La letra griega pelada solo si el usuario la usa.
- "El strike más operado" / "dónde está la actividad" sale de vol_efectivo — jamás lo \
inventes de la cantidad de filas.
- PROHIBIDO armar la recomendación de una operatoria concreta ("comprá el call X", \
"vendé la put Y", lanzamientos cubiertos con nombres). Podés describir qué strikes \
concentran actividad, qué IV paga cada vencimiento y qué implica, con números.
- El análisis del papel GGAL como ACCIÓN (retornos, pivots, sector) vive en Renta \
Variable / Trading — si la pregunta es sobre la acción y no sobre las opciones, derivá."""


# ─────────────────────────────────────────────────────────────────────────────
# Vista ONs — obligaciones negociables (deuda corporativa, curva on_<sector>)
# ─────────────────────────────────────────────────────────────────────────────

