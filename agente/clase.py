"""`agente/clase.py` — LA CLASE DE ACTIVO, PROPUESTA Y CON SU FUENTE.

Doc: `docs/AGENT.md` §0.ei → habilidad `ficha_incompleta`, regla `sin_clase_activo`.

Mismo espíritu que `agente/emisor.py` (el patrón «proponer con fuente»), pero
SIN modelo por ahora: CINCO reglas DETERMINÍSTICAS (`core.clase_activo`)
proponen un valor y de dónde salió; la escritura sigue siendo la de siempre
(`agente.arreglos.CompletarFicha`) y una persona confirma — o, si la regla es
determinística y el valor ya existe en el catálogo, `CompletarFicha.solo` la
aplica sin que nadie apriete.

    REGLA    — cartera DERIVADOS con C/P al final → CALL/PUT OPCIONES
    REGLA    — DERIVADOS sin C/P: futuros/OTC de agro y dólar por el prefijo
    REGLA    — copia de la cartera (RENTA VARIABLE/HD/DL) → esa misma clase
    FCI      — cartera FCI, por el LINK `mercado.fci.unidad` que confirmó la mesa
    PRIMARY  — cartera FCI sin link, ficha del fondo en Primary por NOMBRE
    CURVA    — cartera ARS, por los EJES del bono en el master de curvas

⚠️⚠️ **Y SI NINGUNA APLICA, LA FILA DICE POR QUÉ** (`nota`, §0.fg). Un guion sin
motivo hace que «no hay nada que proponer» y «la regla no encontró su fuente» se
vean iguales, y son trabajos opuestos. Dos de esos motivos ni siquiera son de
este campo: un bono en `mercado.curvas` sin ejes y una cartera ARS con curva en
USD son datos rotos que nadie más estaba reportando.

⚠️ **LISTA CERRADA, mismo invariante que `emisor.py`.** Una regla no inventa
grafías: si el valor que la regla derivaría (p. ej. `PUT OPCIONES`) todavía no
existe en `clase_activo`, la fila NO se escribe sola — viaja con
`propuesto=""` y una nota que dice qué hay que cargar una vez a mano. La
comparación es tolerante a grafía (`core.clase_activo.en_lista_cerrada`): si
`MAIZ`/`MAÍZ` ya normalizan igual, se escribe la grafía que YA está en la
base, nunca la de la regla.
"""
from __future__ import annotations

# ⚠️⚠️ **EL NORMALIZADOR DE NOMBRES DE FONDO ES EL DEL DOMINIO FCI, NO OTRO.**
#
# `normalizar_nombre` (upper + sin acentos + espacios) sigue valiendo para la
# LISTA CERRADA, que compara valores de un vocabulario corto. Para emparejar
# FONDOS no alcanza, y no es una cuestión de grado: `core/fci_match.py` existe
# porque **un fondo llega con TRES grafías del mismo nombre** y su `normalizar`
# saca «FCI»/«F.C.I.», el `[1047]` de Aunesa, el código `CAFCI643-1024` y la
# puntuación. Medido el 2026-09-11: de 84 FCI sin clase, **73 «no matchearon»**
# con el normalizador corto — `FCI IAM Ahorro Pesos - Clase B` contra
# ` IAM Ahorro Pesos - Clase B`. Dos normalizadores para la misma pregunta es la
# REGLA #9, y el que se desincroniza no falla: deja la columna vacía.
from core.cartera import FCI as CARTERAS_FCI_CORE
from core.clase_activo import (
    CARTERA_ARS,
    CARTERA_DERIVADOS,
    POR_AJUSTE,
    PRODUCTO,
    contrato_de,
    de_cartera,
    de_curva,
    de_derivado,
    de_fci,
    de_futuro,
    en_lista_cerrada,
    es_opcion,
)
from core.fci_match import normalizar as normalizar_fondo

REGLA, PRIMARY, CURVA, FCI = "regla", "primary", "curva", "fci"

# Las dos grafías con las que el FCI está cargado. Se importan: son las mismas
# que lee el asistente para contestar «qué fondos tengo» (REGLA #9).
CARTERAS_FCI = CARTERAS_FCI_CORE


def _indice_primary(fichas: list[dict]) -> dict[str, dict]:
    """`{normalizar_fondo(simbolo): ficha}` para emparejar FONDOS por nombre.

    ⚠️ **UNA CLAVE AMBIGUA SE DESCARTA, no se resuelve por orden.** El criterio
    viejo era «el primero gana», que es estable pero arbitrario: acá el
    resultado se ESCRIBE en la ficha de un título, así que con dos fichas de
    distinta clase bajo la misma clave la propuesta sería la de otro fondo. Un
    campo vacío se ve; una clase de otro fondo se suma. Dos fichas idénticas en
    (subyacente, moneda) no son ambiguas para esta pregunta: proponen lo mismo.
    """
    out: dict[str, dict] = {}
    ambiguas: set[str] = set()
    for f in fichas or []:
        clave = normalizar_fondo(f.get("simbolo") or "")
        if not clave:
            continue
        previa = out.get(clave)
        if previa is None:
            out[clave] = f
        elif (previa.get("subyacente"), previa.get("moneda")) != (
                f.get("subyacente"), f.get("moneda")):
            ambiguas.add(clave)
    for clave in ambiguas:
        out.pop(clave, None)
    return out


def _indice_master(master: list[dict] | None) -> dict[str, dict]:
    """`{ticker_corto.upper(): doc}` del master de `mercado.curvas`
    (`agente.fuentes.master()`). **El PRIMERO gana**, mismo criterio que
    `_indice_primary`. ⚠️ En el blob del master `ticker_corto` es el corto
    (`AL30`) — los nombres están cruzados, ver `agente/fuentes.py::master`."""
    out: dict[str, dict] = {}
    for d in master or []:
        clave = (d.get("ticker_corto") or "").strip().upper()
        if clave and clave not in out:
            out[clave] = d
    return out


def proponer(filas: list[dict], fichas_primary: list[dict] | None,
             usadas: list[str], master: list[dict] | None = None,
             fci: dict[str, dict] | None = None) -> list[dict]:
    """Cada fila con `propuesto`, `fuente` y `nota`. **PURA**: no toca base ni
    red — recibe todo lo que necesita, igual que `emisor.proponer`.

    `master` es la lista de docs de `mercado.curvas` (`agente.fuentes.master()`)
    para la regla CURVA; `fci` es `{unidad: fila de mercado.fci}`
    (`agente.fuentes.fci_por_unidad()`) para la regla FCI.

    ⚠️⚠️ **UNA FILA SIN PROPUESTA SIEMPRE DICE POR QUÉ** (§0.fg). Hasta el
    2026-09-11 la `nota` se llenaba en UN solo caso —la regla derivó un valor y
    la lista cerrada lo rechazó— y en todos los demás la fila viajaba con los
    tres campos vacíos. En pantalla eso es un guion, y abajo del mismo guion
    convivían **«no hay nada que proponer» (correcto, por diseño) y «la regla no
    encontró su fuente» (un bug)**: medido, 101 de 170 filas. Es el modo de
    falla que este subsistema persigue en los datos, adentro del subsistema.
    """
    indice = _indice_primary(fichas_primary or [])
    indice_master = _indice_master(master)
    indice_fci = fci or {}
    out = []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": "", "nota": ""}
        cartera = (f.get("cartera") or "").strip().upper()
        unidad, ticker = f.get("unidad", ""), f.get("ticker", "")

        if (v := de_derivado(cartera, unidad, ticker)
                or de_futuro(cartera, unidad, ticker)
                or de_cartera(cartera)):
            fila["propuesto"], fila["fuente"] = v, REGLA

        elif not cartera or cartera == "NO APLICA":
            # Las CINCO reglas arrancan mirando la cartera, así que sin ella no
            # hay ninguna que se pueda ni evaluar. El caso que lo demuestra:
            # `[CRN.CME/ABR27]` es un futuro de maíz que la regla SABE leer, y
            # no lo mira porque su cartera está vacía. Son dos hallazgos en
            # cadena (`sin_cartera` primero) y nada lo decía.
            fila["nota"] = ("sin CARTERA no hay regla posible: las cinco "
                            "arrancan por ahí — completá primero la cartera")

        elif cartera == CARTERA_DERIVADOS:
            fila["nota"] = _nota_derivado(unidad, ticker)

        elif cartera in CARTERAS_FCI:
            fila["propuesto"], fila["fuente"], fila["nota"] = _fci(
                f, indice, indice_fci, fichas_primary is not None)

        elif cartera == CARTERA_ARS:
            fila["propuesto"], fila["fuente"], fila["nota"] = _ars(
                ticker, indice_master)

        else:
            fila["nota"] = f"ninguna regla mira la cartera «{cartera}»"

        # ⚠️ **LA LISTA CERRADA**, tolerante a grafía (`en_lista_cerrada`). Un
        # valor propuesto por regla solo se escribe si YA existe en
        # `clase_activo` —una regla no inventa grafías, mismo invariante que
        # el modelo en `emisor.py`— y se escribe con la grafía que YA está en
        # la base, no con la de la regla.
        if fila["propuesto"]:
            grafia = en_lista_cerrada(fila["propuesto"], usadas or [])
            if grafia:
                fila["propuesto"] = grafia
            else:
                valor = fila["propuesto"]
                fila["propuesto"], fila["fuente"] = "", ""
                fila["nota"] = (f"la regla dice «{valor}», pero ese valor todavía "
                                "no existe en clase_activo: cargalo una vez a mano")
        out.append(fila)
    return out


def _nota_derivado(unidad: str, ticker: str) -> str:
    """Por qué un DERIVADO no recibió propuesta. Lee el contrato con las MISMAS
    funciones que decidieron (`core.clase_activo`), no con un parseo propio."""
    if es_opcion(unidad, ticker):
        # No debería pasar: si es opción, `de_derivado` ya propuso CALL/PUT.
        return "tiene forma de opción y aun así ninguna regla la reconoció"
    prefijo, _otc = contrato_de(unidad, ticker)
    if not prefijo:
        return ("el contrato no tiene la forma XXX.YYY/MMMAA: ninguna regla de "
                "derivados lo reconoce (¿es una opción de acción local?)")
    if prefijo not in PRODUCTO:
        return (f"el prefijo «{prefijo}» del contrato no tiene producto "
                f"declarado (hoy: {'/'.join(sorted(set(PRODUCTO)))})")
    return (f"«{prefijo}» solo se propone bajo OTC: sin OTC no hay clase "
            "declarada para ese producto")


def _fci(f: dict, indice: dict, indice_fci: dict,
         primary_leido: bool) -> tuple[str, str, str]:
    """`(propuesto, fuente, nota)` de un FCI.

    ⚠️⚠️ **PRIMERO EL LINK, DESPUÉS EL NOMBRE.** `mercado.fci.unidad` es el
    link que la MESA confirmó en Manager, y es la misma clave con la que
    `CompletarFicha` escribe. El match por nombre es lo que el propio
    `core/fci_match.py` pide no usar como identidad (*«el link es por símbolo,
    no por texto»*) — queda como respaldo para los fondos que todavía no están
    linkeados (medido 2026-09-11: 77 de 84 sí lo estaban).

    **Se usa `tipo_renta` + `moneda` y NO `categoria`.** `categoria` es el
    estante del INFORME y la mesa manda ahí: puede decir «T+0 MONEY MARKET»,
    que no es vocabulario de `clase_activo`. Tomarla prestada haría que la
    lista cerrada la rechazara y la nota mandara a «cargar ese valor a mano» —
    un consejo equivocado sobre un campo que no es este. Con `tipo_renta` se
    pasa por `de_fci`, o sea la MISMA función con la que el job calculó su
    categoría: un vocabulario, una fuente.
    """
    fondo = indice_fci.get(f.get("unidad", "")) or {}
    if (v := de_fci(fondo.get("tipo_renta") or "", fondo.get("moneda") or "")):
        return v, FCI, ""

    ficha = indice.get(normalizar_fondo(f.get("ticker") or ""))
    if ficha and (v := de_fci(ficha.get("subyacente") or "",
                              ficha.get("moneda") or "")):
        return v, PRIMARY, ""

    # Sin propuesta: hay que decir CUÁL de las tres razones es, porque se
    # atienden distinto — una es carga de datos, otra es vocabulario, otra es
    # que la fuente no se pudo leer.
    if fondo and (fondo.get("tipo_renta") or "").strip():
        return "", "", (f"mercado.fci dice tipo_renta «{fondo['tipo_renta']}» / "
                        f"moneda «{fondo.get('moneda') or '—'}»: no hay clase "
                        "declarada para eso — lo decide la mesa")
    if ficha:
        return "", "", (f"la ficha de Primary dice «{ficha.get('subyacente') or '—'}» / "
                        f"«{ficha.get('moneda') or '—'}»: no hay clase declarada "
                        "para eso — lo decide la mesa")
    if not primary_leido:
        return "", "", "no pude leer el catálogo de Primary en esta pasada"
    if fondo:
        # ⚠️ Las dos razones de un `tipo_renta` vacío se atienden distinto, y
        # «está linkeado pero sin tipo_renta» se leía como un bug del link.
        if not (fondo.get("simbolo_primary") or "").strip():
            return "", "", ("no está emparejado con Primary: `fci_universo` le "
                            "creó una fila propia desde el asset, así que no hay "
                            "tipo de renta de dónde derivar la clase — cargá "
                            "`instrumento` en Manager, o la clase a mano si el "
                            "fondo es bilateral")
        return "", "", (f"está emparejado con Primary («{fondo['simbolo_primary']}») "
                        "pero esa ficha no trae tipo de renta")
    return "", "", ("no está linkeado en mercado.fci (falta el link en Manager) y "
                    "su nombre no matchea ninguna ficha de Primary")


def _ars(ticker: str, indice_master: dict) -> tuple[str, str, str]:
    """`(propuesto, fuente, nota)` de un bono en cartera ARS.

    Las tres razones de un "" son tres trabajos distintos, y **dos de ellas no
    son «falta la clase»: son un dato roto que nadie más está reportando** —un
    bono en `mercado.curvas` SIN EJES no cae en ninguna curva (`core/curvas_sql`:
    «desaparece de todo lo que llame acá»), y una cartera ARS contra una curva
    en USD son dos fuentes afirmando cosas distintas del mismo bono (REGLA #9).
    """
    doc = indice_master.get((ticker or "").strip().upper())
    if doc is None:
        return "", "", ("el bono no está en mercado.curvas: sin ejes no hay "
                        "clase por curva (¿hay que darlo de alta?)")
    moneda = (doc.get("moneda_eje") or "").strip().upper()
    if not moneda:
        return "", "", ("está en mercado.curvas SIN EJES (moneda_eje vacío): "
                        "así tampoco aparece en ninguna vista de curvas")
    if moneda != "ARS":
        return "", "", (f"la ficha dice cartera ARS y su curva dice moneda_eje "
                        f"«{moneda}»: una de las dos está mal")
    if (v := de_curva(CARTERA_ARS, moneda, doc.get("ajuste") or "",
                      doc.get("ajuste_alt"))):
        return v, CURVA, ""
    return "", "", (f"ajuste «{(doc.get('ajuste') or '').strip() or 'sin ajuste'}» "
                    f"no tiene clase declarada (solo {'/'.join(POR_AJUSTE)})")


def deterministas(filas_propuestas: list[dict]) -> list[dict]:
    """`[{"unidad", "valor"}]` de lo que una regla determinística propuso Y
    superó la lista cerrada. Es lo único que `CompletarFicha.solo` puede
    escribir SIN que nadie apriete: lo del modelo nunca va por acá (§0.ei).

    `FCI` entra porque **no es una inferencia**: es el link que la mesa confirmó
    en Manager (`mercado.fci.unidad`) leído por la MISMA función con la que el
    job calcula la categoría del fondo. Si eso no fuera determinístico, tampoco
    lo sería `CURVA`."""
    return [{"unidad": f["unidad"], "valor": f["propuesto"]}
            for f in filas_propuestas
            if f["propuesto"] and f["fuente"] in (REGLA, PRIMARY, CURVA, FCI)]
