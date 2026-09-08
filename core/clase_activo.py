"""`core/clase_activo.py` — `clase_activo` de `portafolio.assets`, PURO.

Doc: `docs/AGENT.md` §0.ei → habilidad `ficha_incompleta`, regla `sin_clase_activo`.

Cinco reglas DETERMINÍSTICAS, pedidas por el user. **Sin base, sin red, sin
imports del proyecto**: recibe lo que necesita y devuelve un string, o "" si
la regla no aplica. La escritura y la lista cerrada las maneja `agente/clase.py`.

1. **DERIVADOS con C/P.** La unidad o el ticker de un derivado en cartera
   `DERIVADOS` puede traer la letra al final del contrato:

       [OTC - SOJ.ROS/NOV26 380 C]   → CALL OPCIONES
       [SOJ.ROS/MAY27 340 P]         → PUT OPCIONES
       SOJ.ROS/MAY27                 → "" (un futuro, no una opción: nada que decir)

2. **FCI por Primary.** El `subyacente` y la `moneda` de la ficha del fondo
   en Primary (`core.instrumentos_validos.fichas()`):

       Mercado de Dinero + ARS → MM ARS     Mercado de Dinero + USD → MM USD
       Renta Fija        + ARS → ARS T1     Renta Fija        + USD → HD T1
       Renta Variable    + (ARS|USD)        → RENTA VARIABLE
       Renta Mixta, cualquier otro subyacente, o moneda que no sea ARS/USD → ""

3. **Copia de la cartera.** Para las carteras que SON la clase (comparación
   upper/strip):

       RENTA VARIABLE → RENTA VARIABLE     HD → HD     DL → DL

4. **Cartera ARS, por la CURVA del bono en el master** (`mercado.curvas`, doc
   por `ticker_corto`). Solo si `moneda_eje` es `ARS` (los EJES del bono,
   `core/curvas_ejes.py`):

       ajuste_alt no vacío → DUAL   (un dual es la consecuencia de tener dos ajustes)
       ajuste == cer   → CER
       ajuste == fija  → FIJA
       ajuste == tamar → TAMAR
       badlar, tpm, caucion, dolar_linked, sin ejes, o bono no encontrado → ""

5. **DERIVADOS que NO son opciones: futuros y OTC de agro/dólar**, por el
   PREFIJO del contrato (las 3 letras antes del primer `.`, o el `DLR` inicial
   para el dólar). La regla 1 va PRIMERO: un contrato con C/P al final ya es
   una opción y esta regla no lo toca.

       [MAI.ROS/JUL27]            → FUTUROS DE MAIZ
       [SOJ.ROS.P/DIS26]          → FUTUROS DE SOJA
       [SOY.CME/ABR27]            → FUTUROS DE SOJA
       [TRI.MIN/DIC26]            → FUTUROS DE TRIGO
       [OTC - CRN.CME/NOV26]      → OTC MAIZ
       [OTC - DLR012027]          → OTC DOLAR
       MAI.ROS/SEP27 (ticker)     → FUTUROS DE MAIZ
       DLR/ENE27 (sin OTC)        → ""   (el dólar solo se propone bajo OTC)
       prefijo desconocido (GFG)  → ""
"""
from __future__ import annotations

import re
import unicodedata

CALL, PUT = "CALL OPCIONES", "PUT OPCIONES"
MM = {"ARS": "MM ARS", "USD": "MM USD"}
T1 = {"ARS": "ARS T1", "USD": "HD T1"}
RENTA_VARIABLE = "RENTA VARIABLE"

# El PRODUCTO por el prefijo del contrato (`de_futuro`), pedido por el user
# 2026-09-08. El dólar (`DLR`) solo se propone bajo OTC — no se pidió un
# "futuros de dólar".
PRODUCTO = {"SOJ": "SOJA", "SOY": "SOJA", "MAI": "MAIZ", "CRN": "MAIZ",
            "TRI": "TRIGO", "DLR": "DOLAR"}
_DOLAR = "DOLAR"

# Carteras que SON la clase: copia directa, comparación upper/strip.
CARTERA_COPIA = {"RENTA VARIABLE": "RENTA VARIABLE", "HD": "HD", "DL": "DL"}

# El `ajuste` de los EJES (`core/curvas_ejes.py::AJUSTES`) que sí tiene clase.
# `badlar`, `tpm`, `caucion`, `dolar_linked` no están: no se propone nada.
POR_AJUSTE = {"cer": "CER", "fija": "FIJA", "tamar": "TAMAR"}
DUAL = "DUAL"

_CARTERA_DERIVADOS = "DERIVADOS"
_CARTERA_ARS = "ARS"

# El punto ES parte del contrato (`SOJ.ROS`, `MAI.ROS`), igual que en
# `jobs/assets_autofill._PREFIJOS_AGRO`: no es un separador cualquiera. La
# letra final es lo único que esta regla necesita — cualquier otro derivado
# (un futuro sin C/P) no matchea y no se propone nada.
_RE_OPCION = re.compile(
    r"^(?:OTC\s*-\s*)?[A-Z]{2,4}\.[A-Z]{2,4}/[A-Z]{3}\d{2}\s+\d+(?:[.,]\d+)?\s+([CP])$")

# La forma OTC de un contrato: `OTC - <resto>`. Lo que sigue del guion es el
# contrato — sea agro (`SOJ.ROS/...`) o dólar (`DLR012027`).
_RE_OTC = re.compile(r"^OTC\s*-\s*(.+)$")

# El prefijo del contrato: 3 letras seguidas de `.`, un dígito o `/` — nunca
# más largo (`_RE_OPCION` ya usa `{2,4}`, pero el prefijo de PRODUCTO es
# siempre de 3, `SOJ`/`MAI`/`TRI`/`DLR`/etc.).
_RE_PREFIJO = re.compile(r"^([A-Z]{3})(?=[.\d/])")


def _sin_corchetes(s: str) -> str:
    """Saca los corchetes EXTERIORES si TODA la cadena está entre ellos.

    La `unidad` de una opción viaja como `[OTC - SOJ.ROS/NOV26 380 C]`; la de
    un FCI como `[1024] CAFCI643-1024 - SBS Pesos Plus - Clase A`, donde el
    corchete es un ID y no envuelve toda la unidad — ahí no se toca nada.
    """
    t = (s or "").strip()
    if t.startswith("[") and t.endswith("]"):
        return t[1:-1].strip()
    return t


def de_derivado(cartera: str, unidad: str, ticker: str) -> str:
    """`CALL OPCIONES` / `PUT OPCIONES`, o "" si no aplica. **PURA.**

    Prueba la `unidad` y, si no matchea, el `ticker` — el contrato puede venir
    en cualquiera de los dos según cómo se cargó el asset.
    """
    if (cartera or "").strip().upper() != _CARTERA_DERIVADOS:
        return ""
    for candidato in (unidad, ticker):
        m = _RE_OPCION.match(_sin_corchetes(candidato).upper())
        if m:
            return CALL if m.group(1) == "C" else PUT
    return ""


def de_futuro(cartera: str, unidad: str, ticker: str) -> str:
    """`FUTUROS DE <producto>` / `OTC <producto>`, o "" si no aplica. **PURA.**

    Cartera `DERIVADOS` sin la letra C/P al final (eso ya lo resuelve
    `de_derivado`, y va primero en la cadena de `agente/clase.py`). Prueba la
    `unidad` y, si no matchea, el `ticker` — mismo criterio que `de_derivado`.
    El producto sale del PREFIJO del contrato (`PRODUCTO`); el dólar (`DLR`)
    solo se propone bajo OTC.
    """
    if (cartera or "").strip().upper() != _CARTERA_DERIVADOS:
        return ""
    for candidato in (unidad, ticker):
        c = _sin_corchetes(candidato).upper()
        if not c:
            continue
        if _RE_OPCION.match(c):
            return ""
        m_otc = _RE_OTC.match(c)
        es_otc = bool(m_otc)
        contrato = m_otc.group(1) if m_otc else c
        m_pref = _RE_PREFIJO.match(contrato)
        if not m_pref:
            continue
        producto = PRODUCTO.get(m_pref.group(1), "")
        if not producto:
            continue
        if not es_otc and producto == _DOLAR:
            continue
        return f"OTC {producto}" if es_otc else f"FUTUROS DE {producto}"
    return ""


def en_lista_cerrada(valor: str, usadas: list[str]) -> str:
    """La grafía YA existente en `usadas` que normaliza igual a `valor`
    (`normalizar_nombre`), o "" si ninguna coincide. **PURA.** Así una regla
    que dice `MAIZ` no queda bloqueada por una base que ya tiene `MAÍZ` — y se
    escribe SIEMPRE la grafía que ya existe, nunca la de la regla, para no
    crear una segunda grafía del mismo valor."""
    clave = normalizar_nombre(valor)
    if not clave:
        return ""
    for u in usadas or []:
        if normalizar_nombre(u) == clave:
            return u
    return ""


def normalizar_nombre(s: str) -> str:
    """upper, sin acentos, espacios colapsados, strip. **PURA.**"""
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.upper()).strip()


def de_fci(subyacente: str, moneda: str) -> str:
    """La clase de un FCI según su ficha de Primary, o "" si no aplica.
    **PURA.** `Renta Mixta` y cualquier otro subyacente no se proponen; una
    moneda distinta de ARS/USD tampoco.
    """
    sub = (subyacente or "").strip().upper()
    m = (moneda or "").strip().upper()
    if sub == "MERCADO DE DINERO":
        return MM.get(m, "")
    if sub == "RENTA FIJA":
        return T1.get(m, "")
    if sub == "RENTA VARIABLE":
        return RENTA_VARIABLE if m in MM else ""
    return ""


def de_cartera(cartera: str) -> str:
    """Copia directa de la cartera cuando la cartera ES la clase, o "" si no
    aplica. **PURA.** Comparación upper/strip."""
    return CARTERA_COPIA.get((cartera or "").strip().upper(), "")


def de_curva(cartera: str, moneda_eje: str, ajuste: str, ajuste_alt: str | None) -> str:
    """La clase de un bono en pesos, por los EJES de su curva en el master, o
    "" si no aplica. **PURA.** Solo cartera `ARS` (upper/strip) Y
    `moneda_eje` `ARS`: un `ajuste_alt` no vacío es un DUAL —la consecuencia
    de tener dos ajustes—, y si no hay, `ajuste` decide CER/FIJA/TAMAR.
    `badlar`, `tpm`, `caucion`, `dolar_linked`, sin ejes, o `moneda_eje`
    distinto de ARS no se proponen.
    """
    if (cartera or "").strip().upper() != _CARTERA_ARS:
        return ""
    if (moneda_eje or "").strip().upper() != "ARS":
        return ""
    if (ajuste_alt or "").strip():
        return DUAL
    return POR_AJUSTE.get((ajuste or "").strip().lower(), "")
