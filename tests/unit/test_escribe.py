"""UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES.

La primera medición de cobertura (§0.ap) puso a `tabla_quieta · sin_escribir` como
la pared más cara —8 casos— y sus ejemplos fueron `ia.llamadas`,
`manager.role_audit` y `manager.salud_eventos`. Ninguna tiene un job atrás:

    manager.role_audit ← `core/roles.py`, cuando alguien CAMBIA un rol

(`ia.llamadas` era el otro ejemplo y se borró el 2026-08-28 con el gateway de IA.
El invariante no cambió: cambió el ejemplo.)

Están quietas porque no pasó nada. Ponerle un botón «relanzar» a esa pared habría
sido construir una puerta a ninguna parte — peor que no tenerla, porque promete.
"""
from __future__ import annotations

import pathlib
from unittest.mock import patch

from core import escribe

# La raíz del repo: estos tests se corren CONTRA EL CÓDIGO DE VERDAD, no contra
# un fixture — un mapa que anda sobre un archivo inventado no prueba nada.
RAIZ = pathlib.Path(__file__).resolve().parents[2]

# ── contra el repo REAL, que es la única prueba que vale ─────────────────────

def test_las_de_EVENTO_se_reconocen():
    """Las escribe `core/`: las dispara una request o una acción, no un reloj."""
    assert escribe.la_dispara("manager.role_audit") == escribe.EVENTO
    assert escribe.quien_escribe("manager.role_audit") == ["core.roles"]


def test_las_de_RELOJ_tambien_y_dicen_QUE_RELANZAR():
    """`portafolio.tenencia` la escribe un job diario: su silencio SÍ es un
    problema, y el nombre del módulo es lo que la puerta va a necesitar."""
    assert escribe.la_dispara("portafolio.tenencia") == escribe.RELOJ
    assert escribe.que_relanzar("portafolio.tenencia") == "jobs.portafolio_backfill"


def test_una_tabla_de_EVENTO_no_tiene_que_relanzar_nada():
    """**Es el punto entero.** Si devolviera un módulo, el botón prometería algo
    que no existe."""
    assert escribe.que_relanzar("manager.role_audit") == ""


def test_NO_SE_no_es_EVENTO():
    """⚠️ Ante la duda se SIGUE EXIGIENDO frescura. Dejar de mirar una tabla
    porque no encontramos su escritor es cómo se pierde una señal de verdad — y
    sería exactamente el bug contrario al que esto arregla."""
    assert escribe.la_dispara("no.existe_esta_tabla") == escribe.NO_SE
    assert escribe.NO_SE != escribe.EVENTO


def test_el_mapa_encuentra_las_que_pasan_por_PG_MIRROR():
    """Medido: con solo el regex de `INSERT INTO`, 6 de 8 tablas conocidas daban
    `no_se` — todas escriben por `core/pg_mirror`, que recibe la tabla como
    PARÁMETRO. Un detector que mide una sola forma de hacer la cosa ve el 12% y
    no se queja."""
    m = escribe._mapa()
    assert len(m) > 40, f"solo {len(m)} tablas mapeadas: ¿se rompió el parseo?"
    # Una que SOLO aparece vía `pg_mirror`, nunca en un INSERT literal.
    assert escribe.quien_escribe("mercado.cedears_snapshot")


# ── la regla de desempate ────────────────────────────────────────────────────

def test_si_la_escriben_LOS_DOS_gana_el_RELOJ():
    """Un job Y una request. Hay algo que debería estar corriendo, así que su
    ausencia sigue siendo un problema: exigir de más es preferible a callar."""
    with patch.object(escribe, "quien_escribe",
                      lambda t: ["api.services.x", "jobs.y"]):
        assert escribe.la_dispara("cualquiera") == escribe.RELOJ
        assert escribe.que_relanzar("cualquiera") == "jobs.y"


def test_scripts_NO_cuenta_como_escritor():
    """Un one-shot que alguien corrió a mano no es el escritor habitual de nada,
    y tomarlo como tal haría que una siembra vieja defina la cadencia."""
    assert "scripts" not in escribe._QUIEN_DISPARA


# ── y que el detector lo use ─────────────────────────────────────────────────

def test_el_detector_SALTEA_las_de_evento():
    """⚠️ El detector se llama `tabla_quieta` y vive en `agente/detectores/
    sistema.py` desde AGENT 2.0. Este test apuntaba a `agente.tablas.
    detectar_tablas`, que no existe desde entonces: reventaba con
    `AttributeError` — o sea que el invariante llevaba semanas SIN mirarse."""
    import inspect

    from agente.detectores import sistema as det

    src = inspect.getsource(det.tabla_quieta)
    assert "escribe.EVENTO" in src and "continue" in src
    # Y deja lo que la puerta va a necesitar.
    assert "que_relanzar" in src


# ── LO QUE EL DETECTOR NO VEÍA (2026-09-08, AGENT.md §0.ew) ─────────────────
#
# Tres agujeros distintos, todos con el mismo final: sin escritor no hay ritmo
# declarado, `agente/tablas.py::frescura` cae al MEDIDO, y un job que appendea un
# lote se lee como «tiempo real cada 8 s». `tabla_quieta` le exige entonces el
# ritmo de un feed live y canta TODOS LOS DÍAS sobre un job sano.

def test_ve_el_INSERT_sin_schema():
    """`jobs/negocio_movimientos.py` hace `INSERT INTO negocio_movimientos` —sin
    schema, lo resuelve el `search_path`— y el regex exigía el punto. La tabla
    salía en AHORA todos los días con «es tiempo real (cada 8 s)»."""
    assert escribe.que_relanzar("operaciones.negocio_movimientos") == \
        "jobs.negocio_movimientos"


def test_ve_las_puertas_de_pg_mirror_que_faltaban():
    """`engines/portfolio_snapshot.py` escribe con `write_snapshot(...)`, que no
    estaba en el patrón. Misma tarjeta diaria, mismo motivo."""
    assert escribe.que_relanzar("valuaciones.portfolio_snapshot") == \
        "engines.portfolio_snapshot"


def test_estan_TODAS_las_puertas_de_pg_mirror():
    """⚠️ **UNA LISTA PARCIAL DE PUERTAS ES UN REGEX PARCIAL.** Si `pg_mirror`
    suma una forma de escribir y nadie la agrega al patrón, las tablas que la
    usen quedan sin escritor **en silencio** — que es exactamente cómo
    `write_snapshot` estuvo afuera. Este test obliga a decidir: o entra al
    patrón, o se declara que no escribe."""
    import inspect

    from core import pg_mirror

    # Las públicas que NO son escrituras de datos nuevos, cada una con su motivo.
    NO_ESCRIBEN = {
        "doc_iso": "serializa un dict, no toca la base",
        "read_native_doc": "lee",
        "prune_native": "BORRA filas viejas: no hace que la tabla esté fresca",
        "write_hist": "escribe siempre en `mercado_hist` y su primer argumento "
                      "es una COLECCIÓN, no una tabla — va por `_RE_HIST`",
    }
    for nombre, fn in vars(pg_mirror).items():
        if nombre.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != pg_mirror.__name__ or nombre in NO_ESCRIBEN:
            continue
        assert nombre in escribe._RE_MIRROR.pattern, (
            f"`pg_mirror.{nombre}` no está en `_RE_MIRROR` ni declarada en "
            f"NO_ESCRIBEN: las tablas que la usen quedan sin escritor, callado")


def test_write_hist_apunta_a_SU_tabla_y_no_a_la_coleccion():
    """Su primer argumento es `'FuturosDLR'`, `'Breakevens'`… y siempre escribe
    en `mercado_hist`. Estaba en el patrón general, así que indexaba la
    colección como si fuera una tabla y dejaba a la tabla real sin escritor."""
    assert escribe.la_dispara("mercado.mercado_hist") == escribe.RELOJ
    assert "engines.futuros_dlr" in escribe.quien_escribe("mercado.mercado_hist")
    # Y las claves fantasma no están.
    assert "breakevenshistorico" not in escribe._mapa()
    assert "forwardshistorico" not in escribe._mapa()


def test_LA_PROSA_NO_ESCRIBE_NADA():
    """Este repo cita el código entre backticks en sus comentarios, y el regex no
    distingue una cita de una ejecución: al ampliar el patrón, la docstring que
    EXPLICA el arreglo quedó anotada como escritora de la tabla que nombra.

    No es cosmético — `quien_escribe()` viaja en la evidencia del hallazgo y
    manda a mirar el archivo equivocado."""
    assert "core.escribe" not in escribe.quien_escribe(
        "operaciones.negocio_movimientos")
    assert "schema.tabla" not in escribe._mapa(), \
        "el `INSERT INTO schema.tabla` del propio comentario entró al mapa"


def test_un_nombre_AMBIGUO_no_se_le_adjudica_a_nadie():
    """`cuentas` vive en `clientes` y en `bancos`. `quien_escribe` busca por el
    nombre completo Y por el corto, así que guardar la clave corta le daría a
    `clientes.cuentas` el escritor de `bancos.cuentas`. Eso no falla: manda a
    relanzar, con seguridad, el job equivocado. Ante la duda, `no_se` — que
    SIGUE exigiendo frescura."""
    from core import schema_sql

    assert schema_sql.ambiguo("cuentas") and not schema_sql.donde_vive("cuentas")
    assert not [k for k in escribe._mapa() if "." not in k], \
        "quedaron claves sin schema en el mapa: pueden cruzarse entre schemas"
    # `bancos.cuentas` sí tiene un INSERT propio, con schema; `clientes.cuentas`
    # no, y por eso queda sin escritor en vez de heredar el ajeno.
    assert escribe.que_relanzar("bancos.cuentas") == "jobs.interbanking_sync"
    assert escribe.que_relanzar("clientes.cuentas") == ""


def test_un_MOTOR_le_gana_a_un_JOB():
    """⚠️ `mercado.market_snapshot` —la tabla de precios— la escriben el motor
    (cada tick) y tres jobs que le parchean unas filas. Si está quieta, el que
    dejó de escribir es el motor. Antes decidía el ORDEN en que se recorren las
    carpetas y contestaba `jobs.tamar_1816`: «relanzar tamar_1816» sobre un feed
    de precios caído."""
    quienes = escribe.quien_escribe("mercado.market_snapshot")
    assert "engines.valores" in quienes and "jobs.tamar_1816" in quienes
    assert escribe.que_relanzar("mercado.market_snapshot") == "engines.valores"


def test_las_tablas_del_AGENTE_son_POR_OCASION():
    """El agente avisaba de que el agente no arregló nada. `agente.acciones`
    escribe cuando alguien aprieta un botón: un día sin arreglos aplicados es un
    día tranquilo. Y `agente.reincidencias` **debe** estar vacía — exigirle
    frescura es exigir que algo se rompa."""
    for tabla in ("agente.acciones", "agente.reincidencias",
                  "agente.silenciados", "agente.explicaciones"):
        assert escribe.la_dispara(tabla) == escribe.EVENTO, tabla
        assert escribe.que_relanzar(tabla) == "", tabla
        assert escribe.por_ocasion(tabla), f"{tabla} sin motivo declarado"


# ── LAS DOS FORMAS QUE EL PATRÓN NO VEÍA (2026-09-11) ───────────────────────
#
# Medido con `scripts/diag_quien_escribe`: DOCE tablas del universo de
# `tabla_quieta` caían en `no_se`, o sea que se les exigía frescura sin saber si
# correspondía. Diez eran estas dos formas, y las dos fallaban en silencio.


def test_el_schema_con_DIGITO_se_ve():
    """`[a-z_]+` no matchea el `5` de `ap5`: el patrón leía `ap`, pedía un punto,
    encontraba un dígito y se rendía. Las cuatro tablas de `ap5` que escribe el
    job quedaban sin escritor con el `INSERT` literal a la vista.

    ⚠️ Es la mitad que NO silencia nada: son de RELOJ y se las sigue mirando.
    Lo que estaba roto era el `que_hacer`, que mandaba a «relanzar el job que la
    escribe» sin poder nombrarlo."""
    assert escribe.la_dispara("ap5.contratos") == escribe.RELOJ
    assert escribe.que_relanzar("ap5.contratos") == "jobs.ap5_portfolio"
    assert escribe.que_relanzar("ap5.margenes") == "jobs.ap5_portfolio"


def test_la_tabla_guardada_en_una_CONSTANTE_del_modulo_se_ve():
    """`_TABLA_CHEQUES = "operaciones.tesoreria_cheques"` arriba del archivo y
    `f"INSERT INTO {_TABLA_CHEQUES} (…)"` abajo: el literal que el patrón busca
    está partido en dos lugares y no existe en ninguno.

    Las seis de tesorería se escriben desde `api/`, o sea que son de EVENTO: a
    tablas de CARGA MANUAL se les venía exigiendo el ritmo de un feed live."""
    assert escribe.la_dispara("operaciones.tesoreria_cheques") == escribe.EVENTO
    assert escribe.quien_escribe("operaciones.tesoreria_cheques") == ["api.services.tesoreria"]
    # Y por lo tanto no hay botón que prometer.
    assert escribe.que_relanzar("operaciones.tesoreria_cheques") == ""


def test_la_puerta_de_pg_mirror_llamada_con_una_CONSTANTE():
    """`write_native(TABLE, …)` con `TABLE = "mercado.eikon_snapshot"` arriba.
    `_RE_MIRROR` exige comillas en el argumento, así que no la veía."""
    assert escribe.quien_escribe("mercado.eikon_snapshot") == ["core.eikon_live"]
    assert escribe.la_dispara("mercado.eikon_snapshot") == escribe.EVENTO


def test_la_constante_SIN_schema_tambien():
    """`_UI_JOBS_TABLE = "aranceles_job_runs"` — sin schema, como el `INSERT INTO
    tabla` que resuelve el `search_path`. Se resuelve contra `sql/schema.sql` por
    la misma puerta (`_resolver`), así que no hay una segunda regla que mantener.

    La escriben un job Y la API: gana el RELOJ, que es el invariante de siempre."""
    assert escribe.la_dispara("manager.aranceles_job_runs") == escribe.RELOJ
    assert escribe.que_relanzar("manager.aranceles_job_runs") == "jobs.aranceles"


def test_una_constante_que_NO_es_una_tabla_no_inventa_un_escritor():
    """⚠️ **LA GUARDA, Y ES LA QUE HACE SEGURO TODO ESTO.** Un módulo tiene
    muchas constantes de módulo que guardan strings que no son tablas
    (`_COLS_CHEQUE`, una lista de columnas). Si se aceptara cualquiera, un
    `f"… {_COLS_CHEQUE}"` le adjudicaría un escritor a una tabla inventada — y un
    escritor FALSO es peor que ninguno: manda a relanzar algo que no existe.

    El filtro es que el valor exista en `sql/schema.sql`, y ya lo teníamos.

    Se prueba sobre `api/services/tesoreria.py`, que tiene las dos clases de
    constante al lado: `_TABLA_CHEQUES` (una tabla) y `_COLS_CHEQUE` (una lista
    de columnas). La primera tiene que entrar; la segunda, nunca.

    ⚠️ NO se prueba «toda tabla del mapa está en `schema.sql`»: sería más
    fuerte y FALSO — `mercado.agro_pizarra_audit` la crea en runtime
    `api/services/derivados_agro.py` con un `CREATE TABLE IF NOT EXISTS` y su
    `INSERT` literal es perfectamente real. Eso es anterior a esto y no es lo
    que este test vigila."""
    fuente = (RAIZ / "api" / "services" / "tesoreria.py").read_text(encoding="utf-8")
    consts = escribe._constantes(fuente)
    assert consts.get("_TABLA_CHEQUES") == "operaciones.tesoreria_cheques"
    assert "_COLS_CHEQUE" not in consts, (
        "una constante que no es una tabla entró al mapa: le adjudicaría un "
        "escritor a algo que no existe, y eso manda a relanzar lo que no hay")
    # Y ninguna de las que pasaron el filtro puede ser un invento.
    from core import schema_sql
    assert all(schema_sql.donde_vive(v) for v in consts.values())


def test_las_DOS_formas_de_llamar_una_puerta_salen_de_LA_MISMA_lista():
    """Hay dos patrones para `pg_mirror` —con el nombre entre comillas y con una
    constante— y si cada uno llevara su propia lista de puertas, sumar una a uno
    y olvidarla en el otro dejaría tablas sin escritor en silencio: el mismo bug
    que este módulo persigue, una capa más adentro."""
    for puerta in escribe._PUERTAS:
        assert puerta in escribe._RE_MIRROR.pattern
        assert puerta in escribe._RE_MIRROR_VAR.pattern
