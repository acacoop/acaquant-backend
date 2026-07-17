"""copiloto/ons.py — vista ONs (deuda corporativa): curva por sector, pagos."""
from __future__ import annotations

import logging

from .base import _fmtn

logger = logging.getLogger(__name__)

_SECTOR_ON_LABEL = {"on_energia": "energia", "on_finanzas": "finanzas",
                    "on_otros": "otros", "on": "otros"}


def _fetch_ons(params: dict | None = None) -> list[dict]:
    """Las ONs de la vista /ons: renta_fija_sql.listar_curva(curva='on') — el
    MISMO service @cached(10s) que la página (kwargs SIEMPRE: es @cached).
    TEA viene en FRACCIÓN → % (paridad ya está en escala 100)."""
    from api.services import renta_fija_sql

    filas: list[dict] = []
    for b in renta_fija_sql.listar_curva(curva="on", ordenar_por="vencimiento") or []:
        f = dict(b)
        f["sector_label"] = _SECTOR_ON_LABEL.get(str(f.get("sector") or ""), "otros")
        if f.get("tea") is not None:
            f["tea"] = float(f["tea"]) * 100
        filas.append(f)
    return filas


def _extras_ons(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    partes: list[str] = []

    # [TEA promedio por sector y moneda] — precalculado por código
    grupos: dict[tuple, list[float]] = {}
    for f in filas:
        if f.get("tea") is None:
            continue
        clave = (f.get("sector_label") or "otros", f.get("moneda") or "?")
        grupos.setdefault(clave, []).append(float(f["tea"]))
    if grupos:
        partes.append("[TEA promedio por sector y moneda — calculado por código]")
        for (sec, mon), teas in sorted(grupos.items()):
            partes.append(
                f"{sec} ({mon}): {len(teas)} ONs · TEA promedio "
                f"{sum(teas) / len(teas):.2f}%"
            )

    # [próximos pagos] — cupones/amortizaciones que vienen (90 días)
    try:
        from api.services import renta_fija

        pagos = renta_fija.calendario_ons(meses=3) or []
        lin = []
        for p in pagos[:15]:
            s = f"{p.get('fecha')}: {p.get('ticker')} ({p.get('emisor') or '—'}) — paga "
            s += f"{_fmtn(p.get('monto'))} por 100 VN"
            if p.get("amortizacion"):
                s += f" (amortiza {_fmtn(p.get('amortizacion'))})"
            if p.get("moneda"):
                s += f" · {p['moneda']}"
            lin.append(s)
        if lin:
            partes.append(
                f"[próximos pagos de ONs (90 días) — {len(pagos)} pagos"
                + (f", muestro los primeros {len(lin)}" if len(pagos) > len(lin) else "")
                + "]"
            )
            partes.extend(lin)
    except Exception as e:
        logger.warning("copiloto ons: calendario falló (%s)", e)
    return partes


_REGLAS_ONS = """Sos el copiloto de la vista ONs — obligaciones negociables: deuda \
CORPORATIVA argentina, agrupada por sector (energía / finanzas / otros).

Columnas: ticker · emisor (la empresa que debe — acá el riesgo es CREDITICIO además de \
tasa) · sector · moneda · vence · meses · precio · tea% (rendimiento anual efectivo, YA \
en %) · dur (duration en años: sensibilidad a tasa) · paridad% · nominales_dia (cuánto \
operó HOY).

Cómo se lee acá:
- LA ILIQUIDEZ ES EL TEMA CENTRAL: muchas ONs operan poco o nada en el día. nominales_dia \
bajo o vacío = el precio puede ser VIEJO y su TEA, engañosa. Cualquier ranking de \
rendimiento prioriza papeles CON volumen y lleva la advertencia de liquidez pegada.
- Una TEA altísima contra el resto de su sector suele ser precio viejo O riesgo del \
emisor que el mercado está cobrando — decí que puede ser cualquiera de los dos, sin \
inventar cuál.
- MONEDA MANDA: una TEA en USD y una en ARS NO son comparables — jamás las mezcles en un \
mismo ranking; siempre separá por moneda como hace el bloque de promedios.
- [próximos pagos] responde "¿qué cupones/amortizaciones vienen?" con fecha, emisor y \
monto por 100 VN.
- LA LEY DE EMISIÓN (argentina / Nueva York) NO ESTÁ EN TUS DATOS: jamás afirmes bajo \
qué ley está emitida una ON ni digas "acá son todas ley local" — muchas ONs argentinas \
se emiten bajo ley extranjera y no tenés cómo saber cuál es cuál. Si preguntan, podés \
explicar el CONCEPTO (la ley extranjera suele dar más protección al acreedor en una \
reestructuración) y decir derecho que el dato no está cargado en el sistema.
- Los soberanos, lecaps y CER viven en Renta Fija — derivá si preguntan por bonos del \
Tesoro. Acá solo deuda corporativa."""


# ── Vista REUTERS (tablero live de subyacentes US, feed Eikon de oficina) ────

