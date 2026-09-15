"""core/escribe.py — ¿QUIÉN ESCRIBE ESTA TABLA, Y LA DISPARA UN RELOJ O UN EVENTO?

Doc madre: **`docs/AGENT.md`** §0.aq.

LO QUE ESTO ARREGLA
===================

La primera medición de cobertura (§0.ap) puso a `tabla_quieta · sin_escribir` como
**la pared más cara: 8 casos**, y los tres ejemplos que imprimió fueron
`ia.llamadas` y `manager.role_audit`.

Mirando quién las escribe, ninguna tiene un job atrás:

    manager.role_audit    ← `core/roles.py`, cuando alguien CAMBIA un rol

**Están quietas porque no pasó nada, no porque algo esté roto.** Y no hay nada
que relanzar: no existe el job. Ponerle un botón «relanzar» a esa pared habría
sido construir una puerta a ninguna parte — que es peor que no tener puerta,
porque encima promete.

    Una tabla de EVENTOS no tiene cadencia: tiene OCASIONES.

Es la otra mitad de §0.u. Allá el problema era que una ráfaga se leía como ritmo;
acá es que un ritmo REAL (los eventos vienen seguido) se lee como una obligación.
`ia.llamadas` escribe casi todos los días porque el agente usa IA casi todos los
días — hasta el día que no, y ese día no hay nada roto.

CÓMO SE SABE, SIN NINGUNA LISTA
===============================

Igual que `core/dependencias`: **la respuesta ya está escrita en el código**. El
`INSERT INTO <schema>.<tabla>` vive en un archivo, y de qué carpeta es ese archivo
dice quién lo dispara:

    jobs/ · engines/   → lo dispara un RELOJ (cron, loop de motor) → se le exige
    core/ · api/       → lo dispara un EVENTO (una request, una acción) → no

⚠️ **El `INSERT` no siempre trae el nombre escrito al lado.** Las tres formas que
este repo usa y que hay que resolver antes de buscar (§0.fc): el nombre **sin
schema** (lo resuelve el `search_path` en runtime), el que viaja como argumento
de una puerta de `core/pg_mirror`, y el que vive en una **constante del módulo**
y se interpola — `_TABLA_CHEQUES = "operaciones.tesoreria_cheques"` arriba,
`f"INSERT INTO {_TABLA_CHEQUES} (…)"` abajo. Las tres fallaban en silencio, que
es el modo de falla que este módulo existe para no tener.

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
    # ⚠️ **LAS DEL PROPIO AGENTE, Y ES LA IRONÍA QUE HAY QUE MIRAR DE FRENTE**
    # (2026-09-08). `tabla_quieta` abría todos los días «agente.acciones no
    # escribe hace 6,1 h de rueda · es tiempo real» — el agente avisando de que
    # el agente no arregló nada. Y no arregló nada porque **nadie apretó un
    # botón**, que es un día normal, no una falla.
    #
    # Las cuatro comparten la forma: las escribe `agente/` cuando PASA algo, no
    # cuando corre un reloj. Y ninguna se detecta sola —`agente/` no está entre
    # las carpetas que escanea `_mapa()`, y su clase sería mixta si lo estuviera
    # (`hallazgos` y `latido` los escribe el daemon en cada pasada; estas
    # cuatro, una persona o un evento)—, así que se declaran.
    "agente.acciones":
        "el LIBRO: escribe cuando se aplica un arreglo o caduca un hallazgo. "
        "Un día sin arreglos aplicados es un día tranquilo, no una tabla rota",
    "agente.reincidencias":
        "**la que DEBE estar vacía**: escribe cuando algo que dimos por "
        "arreglado vuelve. Exigirle frescura es exigir que algo se rompa",
    "agente.silenciados":
        "escribe cuando una persona aprieta «no me interesa». Que nadie "
        "silencie nada en una semana es lo esperable",
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
# ⚠️ **EL SCHEMA PUEDE TENER UN DÍGITO Y `[a-z_]+` NO LO VE.** Acá decía
# `([a-z_]+)`, y con eso `INSERT INTO ap5.contratos` no matcheaba: el patrón
# leía `ap`, pedía un punto, encontraba un `5` y se rendía — **sin error**. Las
# dos tablas de `ap5` quedaban sin escritor, o sea en `no_se`, o sea exigidas
# con un `que_hacer` que mandaba a «relanzar el job que la escribe» sin poder
# nombrarlo, teniendo el `INSERT` literal a la vista en `jobs/ap5_portfolio.py`.
# `ap5` es el único de los 16 schemas declarados con un dígito, así que el bug
# afectaba exactamente a dos tablas — y las dos son de RELOJ, o sea que lo que
# se perdía no era ruido: era el nombre del job que hay que relanzar.
_RE_INSERT = re.compile(
    r'INSERT\s+INTO\s+"?([a-z_][a-z_0-9]*)"?\s*\.\s*"?([a-z_0-9]+)"?', re.I)

# ⚠️⚠️ **Y EL `INSERT INTO tabla` SIN SCHEMA, QUE EL DE ARRIBA NO VE.**
#
# Postgres lo resuelve por `search_path` en runtime, así que es SQL válido y
# corriente en este repo. El regex de arriba exige el punto, así que esas tablas
# quedaban sin escritor **en silencio** — y sin escritor no hay ritmo declarado,
# se cae al MEDIDO, y un job que appendea un lote se lee como «tiempo real cada
# 8 s»: `tabla_quieta` le exige el ritmo de un feed live y canta todos los días.
#
# Medido el 2026-09-08 sobre las tarjetas abiertas de AHORA: `operaciones.
# negocio_movimientos` (`jobs/negocio_movimientos.py`, `INSERT INTO
# negocio_movimientos`) salía «no escribe hace 6,1 h de rueda · es tiempo real
# (cada 8 s)» todos los días, con el job perfecto.
#
# El nombre se resuelve contra `sql/schema.sql` (`core.schema_sql.donde_vive`) y
# **lo ambiguo no se resuelve**: ver el docstring de `_por_nombre_corto`.
_RE_INSERT_CORTO = re.compile(
    r'INSERT\s+INTO\s+"?([a-z_][a-z_0-9]*)"?(?![a-z_0-9.])', re.I)

# ⚠️ **LA MAYORÍA NO ESCRIBE UN `INSERT` LITERAL.** Medido: con solo el regex de
# arriba, 6 de 8 tablas conocidas daban `no_se` — `market_snapshot`, `timesales`,
# `job_runs`… Todas pasan por `core/pg_mirror`, que recibe la tabla **como
# parámetro**, así que el nombre está en la llamada y no en el SQL.
#
# Es la misma trampa de siempre: el primer detector medía una sola forma de hacer
# la cosa y por eso veía el 12%.
#
# ⚠️⚠️ **VAN LAS PUERTAS PÚBLICAS DE `pg_mirror`, TODAS.** Faltaban tres, y una
# de ellas tenía una tarjeta abierta: `engines/portfolio_snapshot.py` escribe con
# `write_snapshot(...)` y `valuaciones.portfolio_snapshot` quedaba sin escritor,
# con el mismo final que el bloque de arriba. Una lista parcial de puertas es el
# mismo bug que un regex parcial: mide una forma de hacer la cosa y no se queja.
# Si mañana `pg_mirror` suma una puerta, esta lista hay que sumarla acá — lo
# congela `test_estan_todas_las_puertas_de_pg_mirror`.
# ⚠️ **LAS PUERTAS SE DECLARAN UNA VEZ Y LOS DOS PATRONES SALEN DE ACÁ.** Hay
# dos formas de llamarlas —con el nombre entre comillas y con una constante— y
# si cada patrón llevara su propia lista, sumar una puerta a una y olvidarla en
# la otra dejaría tablas sin escritor en silencio. Es el mismo bug que este
# módulo persigue, una capa más adentro.
_PUERTAS = ("write_native", "append_native", "write_snapshot",
            "merge_jsonb_native", "replace_native")
_P = "|".join(_PUERTAS)

_RE_MIRROR = re.compile(
    rf'(?:{_P})\s*\(\s*["\']([a-z_][a-z_0-9]*(?:\.[a-z_0-9]+)?)["\']', re.I)

# ⚠️⚠️ **Y LA MISMA PUERTA LLAMADA CON UNA CONSTANTE.** `core/eikon_live.py`
# hace `write_native(TABLE, …)` con `TABLE = "mercado.eikon_snapshot"` arriba, y
# `jobs/aranceles.py` hace `write_native(_UI_JOBS_TABLE, …)`. El de arriba exige
# comillas, así que no veía ninguna de las dos. Lo que captura acá es el
# IDENTIFICADOR; el valor lo resuelve `_constantes()` contra el archivo, y si
# ese identificador no es una constante con una tabla REAL adentro, se descarta.
_RE_MIRROR_VAR = re.compile(
    rf'(?:{_P})\s*\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*[,)]')

# ⚠️⚠️ **EL NOMBRE DE LA TABLA VIVE EN UNA CONSTANTE DEL MÓDULO.** Es la
# convención más común de este repo para las tablas que se escriben desde `api/`:
#
#     _TABLA_CHEQUES = "operaciones.tesoreria_cheques"
#     …
#     f"INSERT INTO {_TABLA_CHEQUES} (lado, tipo, …)"
#
# Ninguno de los tres patrones de arriba ve eso —el literal que buscan está
# partido en dos lugares del archivo—, y el resultado medido el 2026-09-11 eran
# OCHO tablas sin escritor: las seis de tesorería, `mercado.eikon_snapshot` y
# `manager.aranceles_job_runs`. Las siete primeras se escriben desde `api/` o
# `core/`, o sea que son de EVENTO: **`tabla_quieta` les venía exigiendo el ritmo
# de un feed live a tablas de carga manual**, y las dos que más cantaban
# (`tesoreria_cheques`, `tesoreria_banco_a_banco`) eran de ahí.
#
# Sólo se toma la asignación de MÓDULO (sin sangría): una constante adentro de
# una función es del caso de esa función, no del archivo.
_RE_CONST = re.compile(
    r'^([A-Za-z_][A-Za-z_0-9]*)\s*=\s*["\']'
    r'([a-z_][a-z_0-9]*(?:\.[a-z_0-9]+)?)["\']\s*(?:#.*)?$', re.M)

# ⚠️ **`write_hist` NO RECIBE UNA TABLA: RECIBE UNA COLECCIÓN.** Su primer
# argumento es `'FuturosDLR'`, `'Breakevens'`… y siempre escribe en la MISMA
# tabla (`core/pg_mirror.py::write_hist` → `_mirror("mercado_hist", …)`).
# Estaba en el regex de arriba, así que indexaba la colección como si fuera una
# tabla —claves fantasma que nadie consulta— y dejaba a `mercado.mercado_hist`
# sin escritor. El destino es fijo, así que se declara fijo.
_RE_HIST = re.compile(r'write_hist\s*\(', re.I)
_TABLA_HIST = "mercado.mercado_hist"

# Las citas en prosa: `lo que va entre backticks`. Se borran ANTES de buscar
# escrituras — ver el comentario de `_mapa`. El tope de largo evita que un
# backtick suelto se coma medio archivo.
_SIN_CITAS = re.compile(r"`[^`]{1,300}`", re.S)


def _constantes(texto: str) -> dict[str, str]:
    """`NOMBRE → schema.tabla` de las constantes de módulo que guardan una tabla.

    ⚠️⚠️ **LA GUARDA ES QUE LA TABLA EXISTA DE VERDAD**, y no es opcional: este
    archivo está lleno de constantes de módulo que guardan cualquier otra cosa
    (`_COLS_CHEQUE`, una lista de columnas; `_FUENTE`, un literal de negocio). Si
    se aceptara cualquier string, un `f"INSERT INTO … {_COLS_CHEQUE}"` le
    adjudicaría el escritor a una tabla inventada — y como el módulo es
    fail-open hacia «seguir mirando», un escritor FALSO es peor que ninguno:
    manda a relanzar, con seguridad, algo que no existe.

    `schema_sql.donde_vive` es exactamente ese filtro y ya lo teníamos: devuelve
    vacío si el nombre no está declarado en `sql/schema.sql`, y también si es un
    nombre corto AMBIGUO (dos schemas con la misma tabla) — el mismo default
    seguro que `_resolver`.
    """
    from core import schema_sql
    out = {}
    for nombre, valor in _RE_CONST.findall(texto):
        if (completo := schema_sql.donde_vive(valor)):
            out[nombre] = completo
    return out


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
            # ⚠️⚠️ **LA PROSA NO ESCRIBE NADA.** Este repo cita el código en
            # los comentarios con backticks (`INSERT INTO x`, `write_native(…)`),
            # y el regex no distingue una cita de una ejecución: al ampliar el
            # patrón al INSERT sin schema, la propia docstring que EXPLICA el
            # arreglo quedó anotada como escritora de la tabla que nombra.
            #
            # No es cosmético: `quien_escribe()` viaja en la evidencia del
            # hallazgo, y un módulo que sólo la menciona ahí adentro manda a
            # mirar donde no está. La regla que los separa es del repo y no del
            # lenguaje: **el SQL de verdad nunca va entre backticks.**
            texto = _SIN_CITAS.sub(" ", texto)
            # ⚠️ **LAS CONSTANTES SE RESUELVEN ANTES DE BUSCAR NADA.** Reemplazar
            # `{_TABLA_CHEQUES}` por su valor convierte el f-string en el mismo
            # `INSERT INTO operaciones.tesoreria_cheques` que los patrones ya
            # sabían leer: no hace falta un patrón nuevo para el INSERT, sólo
            # para la puerta de `pg_mirror` llamada con el identificador pelado.
            #
            # Se reemplaza SÓLO la forma `{NOMBRE}` —la interpolación—, no el
            # identificador suelto: pisar `_TABLA_CHEQUES` en todo el archivo
            # rompería su propia definición y cualquier comparación que lo use.
            constantes = _constantes(texto)
            for nombre, tabla in constantes.items():
                texto = texto.replace("{" + nombre + "}", tabla)
            arriba = texto.upper()
            if not any(x in arriba for x in
                       ("INSERT", "_NATIVE(", "WRITE_HIST(", "WRITE_SNAPSHOT(")):
                continue
            mod = ".".join(f.relative_to(base).with_suffix("").parts)
            for schema, tabla in _RE_INSERT.findall(texto):
                out.setdefault(f"{schema.lower()}.{tabla.lower()}", []).append(mod)
            # Los que llegan SIN schema —un `INSERT INTO tabla` que resuelve el
            # `search_path`, o un `append_native("cedears_time_sales", …)`— se
            # resuelven contra `sql/schema.sql`. Lo que no se puede resolver se
            # guarda igual con la clave corta: `quien_escribe` busca por las dos,
            # así que una tabla que el schema no declara no se pierde.
            # Las puertas de `pg_mirror` llamadas con una constante
            # (`write_native(TABLE, …)`): el identificador se traduce acá, y lo
            # que no sea una constante con una tabla real adentro no entra.
            por_variable = [constantes[v] for v in _RE_MIRROR_VAR.findall(texto)
                            if v in constantes]
            for nombre in (_RE_INSERT_CORTO.findall(texto) + _RE_MIRROR.findall(texto)
                           + por_variable):
                if (clave := _resolver(nombre)):
                    out.setdefault(clave, []).append(mod)
            if _RE_HIST.search(texto):
                out.setdefault(_TABLA_HIST, []).append(mod)
    return out


def _resolver(nombre: str) -> str:
    """El nombre COMPLETO de una tabla escrita sin schema. `""` = descartar.

    Tres salidas, y la tercera es la que importa:

        ya trae schema      → tal cual
        corto y ÚNICO       → resuelto contra `sql/schema.sql`
        corto y AMBIGUO     → **se descarta** (ver abajo)
        corto y desconocido → se deja corto (no lo reclama nadie más)

    ⚠️ **UN NOMBRE AMBIGUO NO SE GUARDA NI SIQUIERA CORTO.** `quien_escribe()`
    busca por el nombre completo Y por el corto, así que dejar `cuentas` en el
    mapa le adjudicaría a `bancos.cuentas` el escritor de `clientes.cuentas`
    (`jobs.sync_comitentes`) — y eso no falla: manda a relanzar, con seguridad,
    el job equivocado. Descartarlo lo deja en `no_se`, que **sigue exigiendo**
    frescura: el default seguro de este módulo. Son tres tablas medidas sobre
    `sql/schema.sql` (`cuentas`, `movimientos`, `presencia`) y las tres tienen
    además un `INSERT` con schema en algún lado, así que no se pierde cobertura.
    """
    t = (nombre or "").strip().lower()
    if "." in t:
        return t
    from core import schema_sql
    if (completo := schema_sql.donde_vive(t)):
        return completo
    return "" if schema_sql.ambiguo(t) else t


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

    ⚠️⚠️ **UN MOTOR LE GANA A UN JOB, Y NO ES UN DESEMPATE COSMÉTICO.** Cuando
    los dos escriben la misma tabla, el motor es el que la escribe TODO EL
    TIEMPO y el job es el que le parchea unas filas. Si la tabla está quieta, el
    que dejó de escribir es el motor.

    Medido el 2026-09-08 sobre `mercado.market_snapshot` —la tabla de precios,
    la más importante del sistema—: la escriben `engines.valores` (cada tick del
    WS), `engines.curvas`, `jobs.tamar_1816` y `jobs.backfill_tasas` (un puñado
    de filas cada una). Sin esta preferencia se elegía **por el orden en que se
    recorren las carpetas**, o sea `jobs.tamar_1816`: la tarjeta habría dicho
    «relanzar tamar_1816» sobre un feed de precios caído. Es exactamente el
    defecto que `agente/tablas.py` describe para elegir la columna de fecha —
    dejar que decida el orden en que alguien escribió el archivo.
    """
    quienes = quien_escribe(tabla)
    # Las carpetas de RELOJ, con `engines` adelante. Salen de `_QUIEN_DISPARA` y
    # no de una lista escrita acá: una segunda copia de «quién es de reloj» se
    # desincronizaría el día que se sume una carpeta, y no fallaría nada —
    # `que_relanzar` devolvería vacío sobre una tabla que sí tiene job (REGLA #9).
    de_reloj = sorted((r for r, cl in _QUIEN_DISPARA.items() if cl == RELOJ),
                      key=lambda r: (r != "engines", r))
    for carpeta in de_reloj:
        for m in quienes:
            if m.split(".")[0] == carpeta:
                return m
    return ""


def tablas_con_escritor() -> list[str]:
    """Las `schema.tabla` para las que se encontró quién les escribe."""
    return sorted(_mapa())
