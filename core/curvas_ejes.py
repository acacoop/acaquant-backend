"""core/curvas_ejes.py — los EJES de una curva de renta fija (rediseño 2026-08-15).

Doc madre: `docs/RENTA_FIJA.md` §0. Acá vive la traducción de **el nombre de una
curva de 1816** a los ejes del modelo nuevo. Es lógica PURA: sin red, sin base,
testeable de punta a punta.

**El problema que resuelve.** Hoy `mercado.curvas.curva` es un string que
contesta tres preguntas a la vez (quién emite, en qué moneda, con qué ajuste) y
para las ONs mete además el sector del emisor. Por eso `on_otros` terminó siendo
un cajón con BOPREALes y provinciales adentro, y por eso los duales no existen
como concepto (están repartidos entre `cer` y `tamar`). Los ejes separan esas
preguntas:

    nivel 1 — EMISOR   soberano · provincial · corporativo · bcra
    nivel 2 — MONEDA   ARS · USD · EUR
    nivel 3 — AJUSTE   fija · cer · tamar · badlar · dolar_linked · dual · tpm · caucion

    + LEY          local · ny      (Bonar vs Global — misma pill, curvas distintas)

La CURVA es el camino completo (`soberano › ARS › cer`), que es exactamente cómo
1816 nombra las suyas. La PILL de la vista es OTRA cosa: un corte por AJUSTE que
puede juntar varias curvas (`HARD DOLAR` = USD + fija junta Bonares, Globales,
Corporativos USD y BCRA).

**Por qué una TABLA y no un parser.** El catálogo de 1816 son 28 nombres, cerrado
y conocido. Un parser por tokens parece más elegante pero adivina, y adivina mal
justo en los casos que importan: `Bonares`/`Globales` no es un ajuste sino la LEY,
y 1816 le dice `Inflación` a lo mismo que en soberanos llama `CER`. La tabla
explícita no puede equivocarse en eso, y una curva nueva que no esté en la tabla
se reporta como DESCONOCIDA en vez de clasificarse mal en silencio.

**Lo que no se afirma queda en None, no se inventa** (mismo criterio que el resto
del sistema: "sin dato" ≠ "cero"). `ley` solo se completa donde el nombre lo dice
(Bonares/Globales).

**Eje ELIMINADO — bono/letra (2026-08-15).** Existió un quinto eje `instrumento`
(bono · letra) que 1816 solo afirma en 3 de sus 28 curvas (Botes, Letras CER,
Lelink). Se persistía en `mercado.curvas.tipo_instrumento` y quedó **vacío en los
221 bonos** (medido con `scripts/diag_curvas_columnas`): un eje que casi nunca se
puede completar no parte el universo en dos, lo parte en "algunos" y "no sé". Se
borró la columna y el campo. Si algún día hace falta, la forma correcta no es
adivinarlo del prefijo del ticker (`S…` LECAP vs `T…` BONCAP es una heurística sin
medir) sino traerlo de una fuente que lo afirme para todos.
"""
from __future__ import annotations

from typing import NamedTuple


class Ejes(NamedTuple):
    """Los ejes de un instrumento. `None` = la fuente no lo afirma."""
    emisor_tipo: str          # soberano | provincial | corporativo | bcra
    moneda: str               # ARS | USD | EUR  (moneda de DENOMINACIÓN)
    ajuste: str               # fija | cer | tamar | badlar | dolar_linked | dual | tpm | caucion
    ley: str | None = None    # local | ny


EMISORES = ("soberano", "provincial", "corporativo", "bcra")
MONEDAS = ("ARS", "USD", "EUR")
AJUSTES = ("fija", "cer", "tamar", "badlar", "dolar_linked", "dual", "tpm", "caucion")

# Las 28 curvas del catálogo de 1816 (relevado 2026-08-15, `diag_1816_cashflow`).
# Si 1816 agrega una, cae en `desconocidas()` y se suma acá A MANO — es la única
# forma de no clasificar mal en silencio.
EJES_1816: dict[str, Ejes] = {
    # ── Soberanos ───────────────────────────────────────────────────────────
    "Soberanos ARS CER":            Ejes("soberano", "ARS", "cer"),
    "Soberanos ARS Letras CER":     Ejes("soberano", "ARS", "cer"),
    "Soberanos ARS tasa fija":      Ejes("soberano", "ARS", "fija"),
    "Soberanos ARS Botes":          Ejes("soberano", "ARS", "fija"),
    "Soberanos ARS Tamar":          Ejes("soberano", "ARS", "tamar"),
    "Soberanos ARS Badlar":         Ejes("soberano", "ARS", "badlar"),
    "Soberanos Duales":             Ejes("soberano", "ARS", "dual"),
    # Bonar vs Global: MISMO emisor/moneda/ajuste, cambia la LEY. Van a la misma
    # pill (HARD DOLAR) pero son curvas distintas y su spread es lo que se mira.
    "Soberanos USD Bonares":        Ejes("soberano", "USD", "fija", ley="local"),
    "Soberanos USD Globales":       Ejes("soberano", "USD", "fija", ley="ny"),
    "Soberanos EUR Globales":       Ejes("soberano", "EUR", "fija", ley="ny"),
    # Dollar-linked: DENOMINA en USD (por eso moneda=USD y cae del lado USD de la
    # vista) y el ajuste dice que paga en pesos contra el TC.
    "Soberanos USD Linked":         Ejes("soberano", "USD", "dolar_linked"),
    "Soberanos USD Linked Lelink":  Ejes("soberano", "USD", "dolar_linked"),
    # ── BCRA (BOPREALes) ────────────────────────────────────────────────────
    "BCRA USD":                     Ejes("bcra", "USD", "fija"),
    # ── Provinciales ────────────────────────────────────────────────────────
    "Provinciales USD":             Ejes("provincial", "USD", "fija"),
    "Provinciales USD Linked":      Ejes("provincial", "USD", "dolar_linked"),
    "Provinciales ARS Tamar":       Ejes("provincial", "ARS", "tamar"),
    "Provinciales ARS Badlar":      Ejes("provincial", "ARS", "badlar"),
    "Provinciales ARS Fijo":        Ejes("provincial", "ARS", "fija"),
    # OJO: 1816 le dice "Inflación" a lo que en soberanos llama "CER". Es el
    # MISMO ajuste — prueba de que el nombre no sirve como clave y los ejes sí.
    "Provinciales ARS Inflación":   Ejes("provincial", "ARS", "cer"),
    "Provinciales Duales":          Ejes("provincial", "ARS", "dual"),
    # ── Corporativos (ONs) ──────────────────────────────────────────────────
    "Corporativos USD":             Ejes("corporativo", "USD", "fija"),
    "Corporativos USD Linked":      Ejes("corporativo", "USD", "dolar_linked"),
    "Corporativos ARS Tamar":       Ejes("corporativo", "ARS", "tamar"),
    "Corporativos ARS Badlar":      Ejes("corporativo", "ARS", "badlar"),
    "Corporativos ARS Fijo":        Ejes("corporativo", "ARS", "fija"),
    "Corporativos ARS Inflación":   Ejes("corporativo", "ARS", "cer"),
    "Corporativos ARS TPM":         Ejes("corporativo", "ARS", "tpm"),
    "Corporativos ARS Caución":     Ejes("corporativo", "ARS", "caucion"),
}


def desde_1816(nombre_curva: str | None) -> Ejes | None:
    """Nombre de curva de 1816 → `Ejes`. None si no está en el catálogo conocido
    (no se adivina: el caller lo reporta y se suma a mano a `EJES_1816`)."""
    return EJES_1816.get((nombre_curva or "").strip())


def desconocidas(nombres) -> list[str]:
    """Las curvas del iterable que NO están en la tabla — para que el diag las
    cante en vez de dejarlas pasar."""
    return sorted({(n or "").strip() for n in nombres
                   if (n or "").strip() and (n or "").strip() not in EJES_1816})


# ── PILLS de la vista /renta-fija ────────────────────────────────────────────
# La pill NO es la curva: es un corte por AJUSTE sobre los ejes, y puede juntar
# varias curvas. Definidas acá UNA sola vez para que el backend, el front y el
# test de equivalencia no puedan contradecirse.
#
# `cer_fijado` es un ESTADO, no un eje: un bono CER cuyo CER de liquidación ya
# publicó el BCRA se comporta como tasa fija y hoy la vista lo muestra en TASA
# FIJA. Eso NO se puede perder en la migración → la pill lo contempla explícito.

PILLS = ("tasa_fija", "cer", "hard_dolar", "dolar_linked", "tamar", "duales")

DISPLAY = {
    "tasa_fija": "TASA FIJA", "cer": "CER", "hard_dolar": "HARD DOLAR",
    "dolar_linked": "DOLAR LINKED", "tamar": "TAMAR", "duales": "DUALES",
}

# Columna de la tab CURVAS: la MONEDA deja de ser pill y pasa a ser el layout.
LADO = {"tasa_fija": "ARS", "cer": "ARS", "tamar": "ARS", "duales": "ARS",
        "hard_dolar": "USD", "dolar_linked": "USD"}


def pill(ejes: Ejes | None, cer_fijado: bool = False) -> str | None:
    """Qué pill le toca a un instrumento. None = no entra en ninguna."""
    if ejes is None:
        return None
    # CER ya fijado → se comporta como tasa fija (regla vigente de la vista).
    if ejes.ajuste == "cer" and cer_fijado:
        return "tasa_fija"
    if ejes.ajuste == "dual":
        return "duales"
    if ejes.ajuste == "dolar_linked":
        return "dolar_linked"
    if ejes.ajuste == "cer":
        return "cer"
    if ejes.ajuste == "tamar":
        return "tamar"
    if ejes.ajuste == "fija":
        return "hard_dolar" if ejes.moneda in ("USD", "EUR") else "tasa_fija"
    return None      # badlar / tpm / caucion todavía no tienen pill
