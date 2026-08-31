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
221 bonos** (medido en prod, 2026-08): un eje que casi nunca se
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


# ── El UNIVERSO de una curva, en UN solo lugar ───────────────────────────────
#
# Fase B: forwards, fair value, forwards_zscore e histórico partían por
# `mercado.curvas.curva`, un string que contesta tres preguntas a la vez. Acá vive
# la traducción a los ejes, escrita UNA vez para que no puedan volver a divergir.
#
# **Son DOS predicados y no uno.** Es lo que la medición del 2026-08-16 dejó
# claro, y confundirlos es el error caro:
#
#   · VISTA — qué se MUESTRA en esa tabla. Junta emisores a propósito: la mesa
#     quiere ver el corporativo al lado del soberano. Con este predicado la tabla
#     HARD DOLAR pasa de 21 a 129 bonos, que es exactamente lo que se busca.
#   · FIT   — qué entra al AJUSTE de la curva (fair value, z-score, forwards).
#     Acá juntar emisores está MAL: un corporativo tiene spread de crédito, y
#     meterlo adentro corre la curva para TODOS los demás. El modo de fallar es
#     mudo — el ajuste sale, el z-score sale, y los números son otros.
#
# VERIFICADO contra producción (2026-08): el predicado FIT reproduce
# el universo de hoy BONO POR BONO, sin una sola diferencia, en las tres curvas
# que tienen fit persistido (tasa_fija 11=11, cer 22=22, dolar_linked 7=7).
#
# El `OR ajuste_alt` es la decisión de los duales: un dual CER+TAMAR entra a las
# dos tablas. Para el FIT eso es una DECISIÓN DE MESA pendiente — un dual cotiza
# distinto que un CER puro porque tiene la opción de la otra pata.

_UNIVERSO_VISTA = {
    "tasa_fija":    "ajuste = 'fija' AND moneda_eje = 'ARS'",
    "cer":          "(ajuste = 'cer' OR ajuste_alt = 'cer')",
    "soberanos":    "ajuste = 'fija' AND moneda_eje IN ('USD','EUR')",
    "dolar_linked": "(ajuste = 'dolar_linked' OR ajuste_alt = 'dolar_linked')",
    "tamar":        "(ajuste = 'tamar' OR ajuste_alt = 'tamar')",
}


# La PILL y la CURVA son la MISMA clasificación con un nombre distinto en un solo
# caso: la tabla se llama HARD DOLAR y la curva se llama `soberanos`. Todo lo
# demás coincide. Escribirlo como una traducción de una palabra —y no como una
# segunda tabla de reglas— es lo que garantiza que no puedan divergir nunca.
_PILL_A_CURVA = {"hard_dolar": "soberanos"}


def curvas_de(ejes: Ejes | None) -> tuple[str, ...]:
    """En qué CURVAS entra un bono. Es `pills()` con un solo nombre traducido.

    Existe para que `curvas_sql.por_curva` pueda dejar de filtrar por la columna
    `curva` —una palabra escrita a mano— sin que aparezca una SEGUNDA copia del
    criterio. La clasificación del sistema vive en `pills()` y en ningún otro
    lado; esto es una vista de eso.

    Devuelve varias para un dual, igual que `pills()`: un CER+TAMAR pertenece a
    las dos curvas, y hoy `por_curva('tamar')` no lo encuentra.

    `cer_fijado` NO se aplica acá a propósito: es un ESTADO del día (cambia
    cuando el BCRA publica) y la pertenencia a una curva es una propiedad del
    bono. Mezclarlos haría que un bono cambie de curva de un día para el otro.
    """
    return tuple(_PILL_A_CURVA.get(p, p) for p in pills(ejes))


def ejes_de_doc(doc: dict) -> Ejes | None:
    """Los ejes de un doc del master (o `None` si le faltan). Vive acá para que
    los ~500 lugares que leen por `curvas_sql` no reconstruyan la tupla cada uno
    a su manera."""
    if not (doc.get("emisor_tipo") and doc.get("moneda_eje") and doc.get("ajuste")):
        return None
    return Ejes(doc["emisor_tipo"], doc["moneda_eje"], doc["ajuste"],
                doc.get("ley"), doc.get("ajuste_alt"))


def sql_universo(curva: str, *, fit: bool = False) -> str | None:
    """El `WHERE` sobre `mercado.curvas` que reproduce el universo de esa curva.

    `fit=True` agrega `emisor_tipo='soberano'`: es la diferencia entre lo que se
    MUESTRA y lo que entra al CÁLCULO. `None` si la curva no está mapeada (las
    `on_*` no van acá: son `emisor_tipo='corporativo'`, un eje y no una curva).
    """
    curva = (curva or "").strip()
    base = _UNIVERSO_VISTA.get(curva)
    if base is None:
        # Curva del CATÁLOGO: su predicado se GENERA (mismo shape que el de
        # `tamar`, que también es un ajuste puro) en vez de guardarse como texto
        # en la tabla. Un WHERE escrito a mano en una fila de configuración es un
        # SQL injection esperando y, peor, un criterio que puede contradecir a
        # `pills()` sin que nadie lo note.
        fila = _catalogo().get(curva)
        if not fila:
            return None
        aj = fila["ajuste"].replace("'", "''")
        base = f"(ajuste = '{aj}' OR ajuste_alt = '{aj}')"
    return f"{base} AND emisor_tipo = 'soberano'" if fit else base


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
    # Lo que el código no sabe, lo puede saber el CATÁLOGO (2026-08-17): una curva
    # creada desde el AV Agent vive en `mercado.curvas_catalogo` y no en un `if`.
    # Import lazy + degradación a None: si la tabla no responde, el comportamiento
    # es exactamente el de antes de que el catálogo existiera.
    return _pill_del_catalogo(ajuste)


def _pill_del_catalogo(ajuste: str) -> str | None:
    try:
        from core import curvas_catalogo
        fila = curvas_catalogo.de_ajuste(ajuste)
        return fila.get("pill") if fila else None
    except Exception:
        return None


def _catalogo() -> dict[str, dict]:
    """Las curvas del catálogo, o `{}` si no se puede leer. Nunca levanta."""
    try:
        from core import curvas_catalogo
        return curvas_catalogo.todas()
    except Exception:
        return {}


def pills_disponibles() -> tuple[str, ...]:
    """`PILLS` (las de código) + las que agregó el catálogo, sin duplicar.

    La vista arma su barra de pills con esto: una curva nueva aparece en la
    pantalla sin tocar el front."""
    extra = tuple(f["pill"] for f in _catalogo().values() if f.get("pill") not in PILLS)
    return PILLS + tuple(dict.fromkeys(extra))


def display_de(pill: str) -> str:
    """Label de la pill. Cae al catálogo y, si tampoco está, al código en
    mayúsculas — **nunca un KeyError**: una pill sin label rompería la vista
    entera por una fila de configuración que falta."""
    if pill in DISPLAY:
        return DISPLAY[pill]
    for f in _catalogo().values():
        if f.get("pill") == pill:
            return f.get("display") or pill.upper()
    return pill.upper()


def lado_de(pill: str) -> str:
    """Columna (ARS/USD) de la pill. Mismo criterio anti-KeyError que
    `display_de`; el default es ARS porque las curvas en dólares son las menos y
    están todas en código."""
    if pill in LADO:
        return LADO[pill]
    for f in _catalogo().values():
        if f.get("pill") == pill:
            return f.get("lado") or "ARS"
    return "ARS"


def ajuste_sin_curva(ajuste: str | None) -> bool:
    """¿Este ajuste NO cae en ninguna pill, o sea en ninguna curva? (hoy: `badlar`,
    `tpm`, `caucion`).

    **Un bono con un ajuste así es INVISIBLE**: `pills()` le devuelve vacío,
    `curvas_de()` también, y entonces `por_curva()` / `sql_universo()` no lo
    encuentran. No sale en la tabla, ni en los forwards, ni en el fair value —
    **y no da ningún error**. Es el mismo modo de falla del paso 14 de
    `RENTA_FIJA.md`, que es el que lo hace peligroso: nada se rompe, el bono
    simplemente no está.

    Se DERIVA de `_pill_de_ajuste` en vez de escribir la lista a mano: el día que
    `badlar` tenga su pill, esta función deja de reportarlo sola. Una lista
    paralela seguiría diciendo que falta cuando ya no falta, que es la clase de
    aviso que enseña a ignorar los avisos.

    Se prueban las dos monedas porque `fija` cae en pills distintas según ARS/USD:
    un ajuste solo está realmente sin curva si no cae en NINGUNA."""
    if not ajuste:
        return False          # sin ajuste es "sin ejes", que ya se reporta aparte
    return all(_pill_de_ajuste(ajuste, m, False) is None for m in ("ARS", "USD"))


def pata_de_pill(ejes: Ejes | None, pill: str, cer_fijado: bool = False) -> str | None:
    """Qué PATA (ajuste) de este bono produjo esa pill. `None` si ninguna.

    Es la inversa de `pills()` y existe por los DUALES. Un CER+TAMAR sale en las
    dos tablas, y hasta el 2026-08-16 llevaba la MISMA tasa en ambas porque
    `mercado.market_snapshot` tiene una fila por símbolo y por lo tanto UNA sola
    TEA. Medido ese día: entre la pata CER y la TAMAR de TXMD9 hay ~2.900 bps
    (6,82% real contra 38,62% nominal). No son dos formas de decir lo mismo.

    Sabiendo la pata, cada tabla puede buscar la tasa que le corresponde. Vive
    ACÁ y reusa `_pill_de_ajuste` —la misma función que decidió las pills— para
    que no pueda existir un segundo criterio que diverja: con dos, el bono
    quedaría mostrando la tasa de la otra pata y nada fallaría.

    Devuelve la PRIMERA pata que cae en esa pill. Que las dos caigan en la misma
    es imposible por construcción: `normalizar_ejes` rechaza `ajuste_alt == ajuste`.
    """
    if ejes is None:
        return None
    for ajuste in (ejes.ajuste, ejes.ajuste_alt):
        if ajuste and _pill_de_ajuste(ajuste, ejes.moneda, cer_fijado) == pill:
            return ajuste
    return None


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
