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

# ── LOS NOMBRES DE CARTERA, EN UN SOLO LUGAR ────────────────────────────────
#
# `portafolio.assets.cartera` es lo que dice QUÉ ES cada título, y su nombre se
# compara por string en media docena de archivos. Estaban escritos en cuatro:
# acá (HD/DL/ARS), `core/clase_activo.py`, `agente/clase.py` y
# `jobs/assets_autofill.py`. Cuatro copias de la misma verdad, sin árbitro: el
# día que se agregue una cartera, la mitad se entera y la otra no, y ninguna
# falla (REGLA #9). Se declaran ACÁ y se importan; no se vuelven a tipear.
HD, DL, ARS = "HD", "DL", "ARS"
RENTA_VARIABLE = "RENTA VARIABLE"
DERIVADOS = "DERIVADOS"
MONEDAS = "MONEDAS"
# El FCI está cargado con DOS grafías en la base y las dos son la misma cosa.
FCI = ("FCI", "CARTERA FCI")

# QUÉ ES UN BONO, según la mesa: son TRES carteras y no una. Fijado con el user
# — estas tres agarran todo lo que hay. Un título sin datos de mercado hoy sigue
# siendo un bono si su cartera lo dice: la pantalla CURVAS descarta los que no
# tienen ejes acordados, y esa vista filtra para poder DIBUJAR, no para definir.
BONOS = (HD, ARS, DL)


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
