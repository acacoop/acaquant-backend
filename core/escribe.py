"""core/escribe.py — ¿QUIÉN ESCRIBE ESTA TABLA, Y LA DISPARA UN RELOJ O UN EVENTO?

Doc madre: **`docs/AV_AGENT.md`** §0.aq.

LO QUE ESTO ARREGLA
===================

La primera medición de cobertura (§0.ap) puso a `tabla_quieta · sin_escribir` como
**la pared más cara: 8 casos**, y los tres ejemplos que imprimió fueron
`ia.trazas`, `manager.role_audit` y `manager.salud_eventos`.

Mirando quién las escribe, ninguna tiene un job atrás:

    ia.trazas             ← `core/ai.py`, una fila por CADA LLAMADA al LLM
    manager.role_audit    ← `core/roles.py`, cuando alguien CAMBIA un rol
    manager.salud_eventos ← cuando un chequeo TRANSICIONA

**Están quietas porque no pasó nada, no porque algo esté roto.** Y no hay nada
que relanzar: no existe el job. Ponerle un botón «relanzar» a esa pared habría
sido construir una puerta a ninguna parte — que es peor que no tener puerta,
porque encima promete.

    Una tabla de EVENTOS no tiene cadencia: tiene OCASIONES.

Es la otra mitad de §0.u. Allá el problema era que una ráfaga se leía como ritmo;
acá es que un ritmo REAL (los eventos vienen seguido) se lee como una obligación.
`ia.trazas` escribe casi todos los días porque el agente usa IA casi todos los
días — hasta el día que no, y ese día no hay nada roto.

CÓMO SE SABE, SIN NINGUNA LISTA
===============================

Igual que `core/dependencias`: **la respuesta ya está escrita en el código**. El
`INSERT INTO <schema>.<tabla>` vive en un archivo, y de qué carpeta es ese archivo
dice quién lo dispara:

    jobs/ · engines/   → lo dispara un RELOJ (cron, loop de motor) → se le exige
    core/ · api/       → lo dispara un EVENTO (una request, una acción) → no

⚠️ **`no sé` NO es `evento`.** Si no se encuentra el escritor, la tabla se sigue
exigiendo como hasta hoy: dejar de mirar algo porque no lo entendimos es cómo se
pierde una señal de verdad. Ante la duda, se sigue mirando.

Y de yapa deja lo que la puerta va a necesitar el día que exista: **el nombre del
módulo que hay que relanzar**, derivado y no adivinado.
"""
from __future__ import annotations

import functools
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

RELOJ, EVENTO, NO_SE = "reloj", "evento", "no_se"

# ⚠️⚠️ **LA CARPETA NO DICE SI EL DATO ES PERIÓDICO.**
#
# La heurística de abajo (jobs/engines → reloj) acierta en casi todo y falla en
# una clase concreta: las tablas que registran **OCASIONES**. Un motor de precios
# escribe siempre porque siempre hay precios; un motor de ÓRDENES escribe cuando
# alguien opera. Los dos viven en `engines/`.
#
# User (2026-08-28), sobre `ordenes_audit` y `ordenes_live`: *«que sean en tiempo
# real no significa que todo el tiempo tenga que haber datos nuevos. Si no hay
# órdenes en todo el día va a estar sin escribir y eso no implica que se rompió
# algo»*. Y: *«que haya habido 1 sola señal puede ser suficiente — es justamente
# el significado de que está bien y no se rompió»*.
#
# Esto **no se puede derivar del código**: la diferencia no está en quién
# escribe, está en si el sistema CAUSA el dato o solo lo registra. Así que se
# declara — corto, y cada entrada dice POR QUÉ, que es lo que hace que la lista
# se pueda revisar en vez de crecer sola.
#
# (`camara_cereales_audit` cae acá por otro motivo y llega al mismo lugar: la
# escribe `api/services/camara_cereales.py` —o sea un evento— pero con
# `INSERT INTO {table}`, con el nombre en una VARIABLE, así que el regex de
# `_mapa()` no la encuentra y quedaba en `no_se`, que se sigue exigiendo.)
POR_OCASION: dict[str, str] = {
    "operaciones.ordenes_live":
        "una orden existe cuando alguien opera: un día sin órdenes es un día "
        "sin órdenes, no un motor caído",
    "operaciones.ordenes_audit":
        "el rastro de cada cambio de estado de una orden — sin órdenes, no hay "
        "cambios que registrar",
    "mercado.camara_cereales_audit":
        "auditoría: escribe cuando alguien EDITA un precio de cámara, y eso "
        "pasa cuando pasa",
    "mercado.adhoc_subscriptions":
        "los símbolos que alguien pidió a mano desde OPERAR (o desde el botón "
        "«pedir pata» del agente): si nadie pide ninguno, no hay nada que "
        "escribir — y encima vencen solas por TTL, así que vacía es un estado "
        "normal",
}

# De qué carpeta sale el escritor → quién lo dispara. `scripts/` NO cuenta: un
# one-shot que alguien corre a mano no es el escritor habitual de nada, y
# tomarlo como tal haría que una siembra vieja defina la cadencia de la tabla.
_QUIEN_DISPARA = {"jobs": RELOJ, "engines": RELOJ, "core": EVENTO, "api": EVENTO}
_RAICES = tuple(_QUIEN_DISPARA)

# `INSERT INTO schema.tabla`, con lo que se le cruce en el medio (comillas,
# saltos de línea del string SQL partido en varias líneas de Python).
_RE_INSERT = re.compile(
    r'INSERT\s+INTO\s+"?([a-z_]+)"?\s*\.\s*"?([a-z_0-9]+)"?', re.I)

# ⚠️ **LA MAYORÍA NO ESCRIBE UN `INSERT` LITERAL.** Medido: con solo el regex de
# arriba, 6 de 8 tablas conocidas daban `no_se` — `market_snapshot`, `timesales`,
# `job_runs`… Todas pasan por `core/pg_mirror`, que recibe la tabla **como
# parámetro**, así que el nombre está en la llamada y no en el SQL.
#
# Es la misma trampa de siempre: el primer detector medía una sola forma de hacer
# la cosa y por eso veía el 12%. Se agregan las tres puertas de `pg_mirror`, que
# son las que usan los motores y los jobs — justo los de RELOJ, que son los que
# importa clasificar bien.
_RE_MIRROR = re.compile(
    r'(?:write_native|append_native|write_hist)\s*\(\s*["\']'
    r'([a-z_]+(?:\.[a-z_0-9]+)?)["\']', re.I)


@functools.lru_cache(maxsize=1)
def _mapa() -> dict[str, list[str]]:
    """`schema.tabla → [módulos que le hacen INSERT]`. Se lee una vez por proceso."""
    out: dict[str, list[str]] = {}
    base = pathlib.Path(__file__).resolve().parent.parent
    for raiz in _RAICES:
        d = base / raiz
        if not d.is_dir():
            continue
        for f in d.rglob("*.py"):
            try:
                texto = f.read_text(encoding="utf-8")
            except OSError:
                continue
            # ⚠️ **EL ATAJO QUE SALTEABA LA MITAD.** Esto decía solo
            # `if "INSERT" not in texto`, y `engines/motor_cedears.py` —que
            # escribe únicamente por `pg_mirror`— no tiene la palabra INSERT en
            # ninguna parte: quedaba fuera del mapa **en silencio**. Lo cazó su
            # test, no la lectura. Un filtro de performance que achica lo medido
            # sin avisar es el mismo bug que el `for r in app.routes` que veía 5
            # de 428 rutas.
            arriba = texto.upper()
            if "INSERT" not in arriba and "_NATIVE(" not in arriba \
                    and "WRITE_HIST(" not in arriba:
                continue
            mod = ".".join(f.relative_to(base).with_suffix("").parts)
            for schema, tabla in _RE_INSERT.findall(texto):
                out.setdefault(f"{schema.lower()}.{tabla.lower()}", []).append(mod)
            for nombre in _RE_MIRROR.findall(texto):
                # Algunas llamadas pasan la tabla SIN schema (`append_native(
                # "cedears_time_sales", …)`). Se guarda igual con la clave corta:
                # el que pregunta trae el nombre completo, así que se busca por
                # las dos y una tabla sin schema no se pierde.
                out.setdefault(nombre.lower(), []).append(mod)
    return out


def quien_escribe(tabla: str) -> list[str]:
    """Los módulos que la escriben. Vacío = no lo pude encontrar.

    Se prueba con el nombre completo y con el corto: hay llamadas a `pg_mirror`
    que pasan la tabla sin schema, y perderlas dejaría en `no_se` a una tabla
    que sí tiene dueño.
    """
    t = (tabla or "").strip().lower()
    if not t:
        return []
    m = _mapa()
    corto = t.split(".")[-1]
    # `dict.fromkeys` y no `set`: el orden importa, el primero es el principal.
    return list(dict.fromkeys(m.get(t, []) + m.get(corto, [])))


def por_ocasion(tabla: str) -> str:
    """Por qué esta tabla puede estar quieta sin que nada esté roto. `""` si no
    es una de ellas — y entonces se le sigue exigiendo frescura."""
    return POR_OCASION.get((tabla or "").strip(), "")


def la_dispara(tabla: str) -> str:
    """`reloj` · `evento` · `no_se`.

    ⚠️ **La declaración explícita gana sobre la heurística de carpeta.** Ver
    `POR_OCASION`: hay tablas de `engines/` cuyo dato lo causa una persona, no
    un loop, y ahí la carpeta contesta mal. **`no_se` NO es `evento`**: ante la duda se
    sigue exigiendo frescura, porque dejar de mirar algo que no entendimos es
    cómo se pierde una señal de verdad.

    Si la escriben los DOS (un job y una request), gana el RELOJ: hay algo que
    debería estar corriendo y su ausencia sí es un problema.
    """
    # Primero la declaración: `camara_cereales_audit` ni siquiera tiene
    # escritor detectado (su INSERT usa el nombre en una variable), así que sin
    # esto caería en `no_se` y se le seguiría exigiendo.
    if por_ocasion(tabla):
        return EVENTO
    quienes = quien_escribe(tabla)
    if not quienes:
        return NO_SE
    clases = {_QUIEN_DISPARA.get(m.split(".")[0], NO_SE) for m in quienes}
    return RELOJ if RELOJ in clases else (EVENTO if clases == {EVENTO} else NO_SE)


def que_relanzar(tabla: str) -> str:
    """El módulo que habría que relanzar para que esa tabla vuelva a escribir.

    Vacío si no hay uno de reloj — que es justo el caso donde un botón
    «relanzar» sería una promesa vacía.
    """
    for m in quien_escribe(tabla):
        if _QUIEN_DISPARA.get(m.split(".")[0]) == RELOJ:
            return m
    return ""


def tablas_con_escritor() -> list[str]:
    """Las `schema.tabla` para las que se encontró quién les escribe."""
    return sorted(_mapa())
