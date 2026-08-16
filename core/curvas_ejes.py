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
    # La SEGUNDA pata de un dual. Un dual no es una familia aparte: es un bono con
    # dos rendimientos, y el trader lo mira en las DOS tablas. `None` = no es dual.
    ajuste_alt: str | None = None


EMISORES = ("soberano", "provincial", "corporativo", "bcra")
MONEDAS = ("ARS", "USD", "EUR")
LEYES = ("local", "ny")
# `dual` NO está y no puede volver: dejó de ser un ajuste y pasó a ser la
# CONSECUENCIA de tener dos (`ajuste_alt IS NOT NULL`). Mientras estuviera en esta
# tupla, el editor de Manager podría re-introducir a mano el valor que la
# migración está sacando, y el bono volvería a esconderse de sus dos tablas.
AJUSTES = ("fija", "cer", "tamar", "badlar", "dolar_linked", "tpm", "caucion")

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


# Curvas que 1816 SÍ tiene pero que NO se pueden traducir a ejes, con el motivo.
# No es lo mismo que una curva desconocida: acá la conocemos y sabemos por qué no
# alcanza. Están separadas de `EJES_1816` para que `desde_1816` devuelva None y el
# caller REPORTE en vez de escribir un valor inventado.
#
# Los duales: 1816 los agrupa bajo un nombre que dice "Duales" y nada más — no
# nombra las DOS patas (verificado 2026-08-16: la denominación de los ocho es
# `GOB ARS ARG DUAL (<ticker>)`, y su ficha trae 9 campos, todos ya guardados).
# Mientras estas dos filas estuvieron en `EJES_1816` con `ajuste='dual'`,
# `clasificar_curvas --aplicar` le devolvía `dual` a cada dual ya migrado: el
# pipeline de 1816 peleando contra el modelo, en silencio y para siempre.
CURVAS_SIN_EJES: dict[str, str] = {
    "Soberanos Duales":   "dual: 1816 no dice contra qué ajustan las dos patas",
    "Provinciales Duales": "dual: 1816 no dice contra qué ajustan las dos patas",
}


# ── Validación de los ejes que llegan de un editor ───────────────────────────
# Vive acá, junto al vocabulario, para que el editor de bonos y el de ONs no
# tengan cada uno su propia idea de qué es válido.

EJES_EDITABLES = ("emisor_tipo", "moneda_eje", "ajuste", "ajuste_alt", "ley")

_DOMINIOS = {"emisor_tipo": EMISORES, "moneda_eje": MONEDAS,
             "ajuste": AJUSTES, "ajuste_alt": AJUSTES, "ley": LEYES}


def normalizar_ejes(payload: dict) -> dict:
    """Los ejes de un payload, validados y normalizados. Levanta `ValueError`.

    Devuelve SOLO las claves presentes: lo que el editor no manda no se toca, y
    mandar `""` o `None` es la forma de BORRAR un eje (queda en None, que es un
    estado válido y visible — "sin clasificar" no es un error).

    Dos reglas que no son de tipeo sino de modelo:

      · `ajuste_alt` sin `ajuste` no existe: la segunda pata de nada.
      · `ajuste_alt == ajuste` es un dual consigo mismo. Ya casi entra a la base
        una vez (TMVE8/TTD26/TTS26, donde las dos fuentes decían TAMAR) y el
        daño es MUDO: el bono se ve en una sola tabla y todo suma bien.
    """
    out: dict = {}
    for k in EJES_EDITABLES:
        if k not in payload:
            continue
        v = payload[k]
        if v is None or str(v).strip() == "":
            out[k] = None
            continue
        v = str(v).strip()
        v = v.upper() if k == "moneda_eje" else v.lower()
        if v not in _DOMINIOS[k]:
            raise ValueError(
                f"{k} inválido: {v!r} (válidos: {', '.join(_DOMINIOS[k])})")
        out[k] = v

    alt, aj = out.get("ajuste_alt"), out.get("ajuste")
    if alt and "ajuste" in out and not aj:
        raise ValueError("no se puede poner `ajuste_alt` sin `ajuste`: la segunda "
                         "pata de un dual necesita una primera")
    if alt and alt == aj:
        raise ValueError(
            f"`ajuste_alt` no puede ser igual a `ajuste` ({aj!r}): un dual es dual "
            "porque sus dos patas son DISTINTAS. Si el bono no es dual, dejá "
            "`ajuste_alt` vacío")
    return out


def desde_1816(nombre_curva: str | None) -> Ejes | None:
    """Nombre de curva de 1816 → `Ejes`. None si no está en el catálogo conocido
    (no se adivina: el caller lo reporta y se suma a mano a `EJES_1816`) o si está
    en `CURVAS_SIN_EJES` — ahí el motivo lo da `motivo_sin_ejes`."""
    return EJES_1816.get((nombre_curva or "").strip())


def motivo_sin_ejes(nombre_curva: str | None) -> str | None:
    """Por qué esa curva de 1816 no se puede traducir. None si sí se puede (o si
    directamente no la conocemos)."""
    return CURVAS_SIN_EJES.get((nombre_curva or "").strip())


def desconocidas(nombres) -> list[str]:
    """Las curvas del iterable que NO están en la tabla — para que el diag las
    cante en vez de dejarlas pasar. Las de `CURVAS_SIN_EJES` NO son desconocidas:
    las conocemos y sabemos por qué no alcanzan."""
    conocidas = set(EJES_1816) | set(CURVAS_SIN_EJES)
    return sorted({(n or "").strip() for n in nombres
                   if (n or "").strip() and (n or "").strip() not in conocidas})


# ── PILLS de la vista /renta-fija ────────────────────────────────────────────
# La pill NO es la curva: es un corte por AJUSTE sobre los ejes, y puede juntar
# varias curvas. Definidas acá UNA sola vez para que el backend, el front y el
# test de equivalencia no puedan contradecirse.
#
# `cer_fijado` es un ESTADO, no un eje: un bono CER cuyo CER de liquidación ya
# publicó el BCRA se comporta como tasa fija y hoy la vista lo muestra en TASA
# FIJA. Eso NO se puede perder en la migración → la pill lo contempla explícito.

# La pill DUALES fue ELIMINADA (2026-08-16). Un dual no es una familia: es un bono
# con DOS patas, y el trader lo mira en la tabla de CER *y* en la de TAMAR — no lo
# archiva en un lugar aparte. Mandarlo a una pill propia lo escondía de las dos.
PILLS = ("tasa_fija", "cer", "hard_dolar", "dolar_linked", "tamar")

DISPLAY = {
    "tasa_fija": "TASA FIJA", "cer": "CER", "hard_dolar": "HARD DOLAR",
    "dolar_linked": "DOLAR LINKED", "tamar": "TAMAR",
}

# Columna de la tab CURVAS: la MONEDA deja de ser pill y pasa a ser el layout.
LADO = {"tasa_fija": "ARS", "cer": "ARS", "tamar": "ARS",
        "hard_dolar": "USD", "dolar_linked": "USD"}


def _pill_de_ajuste(ajuste: str | None, moneda: str, cer_fijado: bool) -> str | None:
    """UNA pata → su pill. None = esa pata no entra en ninguna."""
    if not ajuste:
        return None
    # CER ya fijado → se comporta como tasa fija (regla vigente de la vista).
    if ajuste == "cer":
        return "tasa_fija" if cer_fijado else "cer"
    if ajuste == "dolar_linked":
        return "dolar_linked"
    if ajuste == "tamar":
        return "tamar"
    if ajuste == "fija":
        return "hard_dolar" if moneda in ("USD", "EUR") else "tasa_fija"
    return None      # badlar / tpm / caucion todavía no tienen pill


def pills(ejes: Ejes | None, cer_fijado: bool = False) -> tuple[str, ...]:
    """En qué pills entra un instrumento. Puede ser MÁS DE UNA.

    Ahí está todo el cambio de modelo: un dual CER+TAMAR devuelve `('cer','tamar')`
    y aparece en las dos tablas, que es donde el trader lo busca. Un bono normal
    devuelve una sola. Vacío = no entra en ninguna (badlar/tpm/caución, o sin ejes)
    y el caller tiene que mostrarlo como PENDIENTE, nunca ocultarlo.

    Sin duplicados y en orden estable: la pata principal primero.
    """
    if ejes is None:
        return ()
    out: list[str] = []
    for ajuste in (ejes.ajuste, ejes.ajuste_alt):
        p = _pill_de_ajuste(ajuste, ejes.moneda, cer_fijado)
        if p and p not in out:
            out.append(p)
    return tuple(out)
