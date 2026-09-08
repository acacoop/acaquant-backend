"""`core/cartera.py` — la CARTERA de `portafolio.assets`, derivada de los EJES
de un bono. PURO: sin base, sin red, sin imports del proyecto.

Doc: `docs/AGENT.md` §0.ej → habilidad `ficha_incompleta`, regla `sin_cartera`.

Hoy ninguna regla de `jobs/assets_autofill` sabe qué es un BONO: conoce
FINANCIAMIENTO, FCI y DERIVADOS, y el resto del catálogo —los bonos en
cartera `ARS`, `HD`, `DL`— queda sin nada que lo derive. La única
clasificación de un bono son sus EJES (`core/curvas_ejes.py`): la moneda de
denominación y el ajuste, que ya viven en dos lugares que el agente lee — el
master de curvas (`agente/fuentes.py::master`) y el catálogo local de 1816
(`agente/fuentes.py::universo_1816`, traducido con
`core/curvas_ejes.py::desde_1816`).

La tabla (pedida por el user):

    ajuste == dolar_linked        → DL   (paga en pesos contra el dólar, pero
                                           está denominado en USD)
    moneda_eje en (USD, EUR)      → HD   (denominado y paga en moneda dura)
    moneda_eje == ARS             → ARS
    cualquier otra cosa           → ""   (sin ejes, o una moneda que no es
                                           ninguna de las tres — no se inventa)
"""
from __future__ import annotations

HD, DL, ARS = "HD", "DL", "ARS"


def de_ejes(moneda_eje: str, ajuste: str) -> str:
    """La cartera de un bono según sus ejes, o "" si no se puede afirmar.
    **PURA.**"""
    if (ajuste or "").strip().lower() == "dolar_linked":
        return DL
    m = (moneda_eje or "").strip().upper()
    if m in ("USD", "EUR"):
        return HD
    if m == "ARS":
        return ARS
    return ""
