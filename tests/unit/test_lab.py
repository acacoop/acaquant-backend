"""`tests/unit/test_lab.py` — lo que el INVESTIGADOR no puede romper.

El laboratorio (`lab/langgraph/`) investiga con un modelo que ELIGE qué mirar.
Eso está bien mientras lo que puede alcanzar esté acotado por algo más duro que
la buena voluntad de quien escriba la próxima herramienta. Estos tests congelan
esas cotas.

⚠️ Casi todos leen los archivos como TEXTO en vez de importar el paquete. No es
pereza: así corren aunque LangChain no esté instalado, y sobre todo prueban lo
que dice el código y no lo que hace una instancia en particular.
"""
from __future__ import annotations

import pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[2]
LAB = RAIZ / "lab" / "langgraph"
# El DDL y —sobre todo— el ALCANCE del lab sobre producción. Ver §L.3 de
# `docs/AGENT.md`: este archivo no describe qué puede leer, lo impone.
LAB_SQL = RAIZ / "sql" / "lab.sql"


def test_el_lab_no_conoce_la_conexion_del_sistema():
    """**La garantía entera.** `core.postgres` es la conexión del sistema y
    puede escribir en producción. El lab usa dos roles propios —uno que sólo
    lee y otro que sólo escribe su diario— y si importara la del sistema, esa
    separación pasaría a depender de que nadie la use para otra cosa: una
    intención, no una garantía.
    """
    import ast

    # ⚠️ Se miran los IMPORTS con el árbol sintáctico, no el texto: buscar la
    # cadena a mano daría falso positivo con cualquier comentario que la
    # nombre — y este archivo la nombra varias veces justamente para explicar
    # por qué no se usa. Un test que falla por su propia documentación enseña
    # a desactivarlo.
    for f in LAB.glob("*.py"):
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                nombres = [f"{nodo.module or ''}.{a.name}" for a in nodo.names]
            else:
                continue
            for n in nombres:
                assert not n.startswith("core.postgres"), (
                    f"{f.name} importa «{n}»: la conexión del sistema puede "
                    f"escribir en producción y el lab no la puede tener")


def test_ninguna_herramienta_escribe():
    """Las tools son de SOLO LECTURA, y no porque el modelo se porte bien: no
    existe una que pueda escribir. El día que haya una, va a tener que pasar
    por acá."""
    t = (LAB / "datos.py").read_text(encoding="utf-8").upper()
    for verbo in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER "):
        assert verbo not in t, f"datos.py tiene un {verbo.strip()}"


def test_no_hay_una_herramienta_que_corra_SQL_libre():
    """El modelo pasa un ticker, NUNCA una consulta. Una tool que reciba SQL no
    es una herramienta: es una consola remota — la misma ley que `REHACIBLES`
    en `agente/rehacer.py`."""
    t = (LAB / "datos.py").read_text(encoding="utf-8")
    assert "def consultar_sql" not in t and "sql: str" not in t


def test_el_agente_sobrevive_a_que_el_lab_se_rompa():
    """El agente es el que DETECTA. Si el laboratorio no está instalado o
    revienta, la pasada tiene que seguir: por eso el import va adentro del
    try, no arriba del archivo — un ImportError al cargar el módulo mataría el
    daemon al arrancar."""
    t = (RAIZ / "jobs" / "agente.py").read_text(encoding="utf-8")
    assert "from lab.langgraph import servicio" in t
    for linea in t.splitlines():
        if "from lab.langgraph" in linea:
            assert linea.startswith("        "), (
                "el import del lab tiene que estar adentro de un try, indentado")


def test_el_gasto_del_investigador_queda_registrado():
    """Sin traza no hay forma de saber cuánto costó ni quién lo pidió, y el
    presupuesto del gateway sería decorativo."""
    med = (LAB / "medidor.py").read_text(encoding="utf-8")
    assert "ai.registrar(" in med and "motivo_presupuesto" in med
    ai = (RAIZ / "core" / "ai.py").read_text(encoding="utf-8")
    assert '"investigador"' in ai, "la tarea no está declarada en el gateway"
    assert "def registrar(" in ai


def test_una_sola_traduccion_de_los_pasos():
    """La terminal y el modal muestran lo MISMO porque leen la misma función.
    Dos traducciones del mismo evento terminan mostrando cosas distintas del
    mismo hecho, y no hay forma de saber cuál miente."""
    assert "def pasos_de(" in (LAB / "servicio.py").read_text(encoding="utf-8")
    assert "servicio.pasos_de(" in (LAB / "correr.py").read_text(encoding="utf-8")


def test_el_diario_no_acepta_un_veredicto_sin_fuentes():
    """El CHECK con `cardinality`, NO con `array_length`: `array_length('{}',1)`
    devuelve NULL y un CHECK que da NULL **pasa**. La primera versión era
    decorativa justo para el caso que venía a atajar.

    ⚠️ Se lee de `sql/lab.sql` porque el DDL se mudó ahí: vivía en una constante
    de `diario.py` que se aplicaba copiando el texto a Supabase, así que el
    deploy no lo tocaba nunca."""
    t = LAB_SQL.read_text(encoding="utf-8")
    assert "cardinality(de_donde) > 0" in t
    assert "array_length(de_donde" not in t


def test_el_ddl_del_lab_tiene_UNA_sola_fuente():
    """El esquema del lab vive en `sql/lab.sql` y en ningún otro lado.

    Estuvo duplicado en dos constantes `SQL_ESQUEMA` (una en `diario.py`, otra
    en `cola.py`) que se aplicaban a mano. Dos copias del mismo DDL sin árbitro
    es la REGLA #9, y encima ninguna de las dos la aplicaba el deploy: un
    `ALTER` nuevo andaba local y en producción no existía."""
    assert LAB_SQL.exists(), "falta sql/lab.sql"
    for f in LAB.glob("*.py"):
        assert "SQL_ESQUEMA" not in f.read_text(encoding="utf-8"), (
            f"{f.name} volvió a embeber el DDL: la fuente es sql/lab.sql")


def test_el_deploy_aplica_el_esquema_del_lab():
    """Sin esto, media DDL del lab se autocura en cada deploy (los GRANT de las
    vistas, que viven en `sql/schema.sql`) y la otra media depende de que
    alguien se acuerde de pegar el texto."""
    t = (RAIZ / "scripts" / "apply_schema.py").read_text(encoding="utf-8")
    assert "lab.sql" in t, "apply_schema tiene que aplicar sql/lab.sql"


def test_el_alcance_del_lab_esta_declarado_en_el_repo():
    """**Qué puede leer el investigador se audita leyendo el proyecto.**

    Los GRANT sobre producción se habían dado a mano en el editor de Supabase:
    no estaban en el repo, no se reproducían en una base nueva, y no había forma
    de contestar «¿a qué tiene acceso esto?» sin entrar a la base.

    Y REVOCA antes de otorgar: si sólo otorgara, un permiso de más puesto a mano
    sobreviviría para siempre y ningún chequeo lo vería."""
    t = LAB_SQL.read_text(encoding="utf-8")
    for tabla in ("mercado.curvas", "mercado.market_snapshot", "agente.hallazgos",
                  "agente.reincidencias", "agente.acciones", "manager.job_runs"):
        assert f"'{tabla}'" in t, f"falta declarar el acceso a {tabla}"
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA" in t, (
        "sin el REVOKE previo, este archivo describe el alcance en vez de imponerlo")
    # La lista del SQL y la que espera `probar_lector` son la misma o el chequeo
    # del techo daría falsos positivos para siempre.
    from lab.langgraph import probar_lector as pl
    for tabla in pl.TABLAS_QUE_LEE:
        assert f"'{tabla}'" in t, (
            f"probar_lector espera {tabla} y sql/lab.sql no lo otorga: "
            f"dos listas que no se hablan es la REGLA #9")


def test_una_fila_vieja_vacia_no_impide_crear_el_CHECK():
    """**Medido contra un Postgres de verdad, no razonado.**

    La migración `text → text[]` convierte un `''` en arreglo VACÍO, y entonces
    `ADD CONSTRAINT ... cardinality(...) > 0` falla con «is violated by some
    row». Como los errores del lab avisan sin cortar, el deploy seguiría en
    verde y **la restricción simplemente no existiría** — la misma falla
    decorativa del `array_length`, entrando por otra puerta.

    El arreglo no es saltear el CHECK ni borrar la fila: es rellenar lo vacío
    con una frase que dice por qué está vacío, ANTES de crear la restricción.
    """
    t = LAB_SQL.read_text(encoding="utf-8")
    i_relleno = t.find("la versión vieja no lo guardaba")
    i_check = t.find("ADD CONSTRAINT inv_que_paso")
    assert i_relleno != -1, "falta rellenar las filas vacías antes de los CHECK"
    assert i_check != -1
    assert i_relleno < i_check, (
        "el relleno tiene que ir ANTES del ADD CONSTRAINT, o el CHECK no se crea")


def test_un_objeto_que_falta_no_deja_al_lector_sin_nada():
    """El REVOKE corre primero. Si después un GRANT aborta el bloque entero, el
    lector queda **sin ningún permiso** y el investigador deja de funcionar — con
    el motivo en una línea del deploy que nadie lee. Cada objeto lleva su guarda
    y lo que falta se canta por nombre."""
    t = LAB_SQL.read_text(encoding="utf-8")
    assert "to_regclass" in t, "los GRANT por tabla necesitan guarda de existencia"
    assert t.count("RAISE WARNING 'lab:") >= 2, "lo que falta se nombra, no se calla"


def test_la_prueba_de_permisos_mide_el_techo_y_no_solo_el_piso():
    """Comprobar que PUEDE leer sus tablas no dice nada sobre si puede leer
    otras. Un `GRANT ... ON ALL TABLES` otorgado un martes apurado pasaba todos
    los chequeos en verde."""
    t = (LAB / "probar_lector.py").read_text(encoding="utf-8")
    assert "information_schema.table_privileges" in t, (
        "el techo se pregunta al catálogo: probar tabla por tabla exige saber "
        "de antemano qué buscar, que es justo lo que no se sabe")
    assert "NEGOCIO_PROHIBIDO" in t
    # Las cuatro familias del negocio (REGLA #8), no una sola.
    for tabla in ("clientes.comitentes", "portafolio.tenencia",
                  "operaciones.operaciones", "manager.manager_users"):
        assert tabla in t, f"{tabla} no se prueba: el negocio se cierra entero"


def test_el_eval_no_usa_un_modelo_para_juzgar_al_modelo():
    """Invariante #12: el agente no se autoevalúa. Un juez que es el mismo
    modelo que contestó mide su propio estilo, no su acierto. La verdad de campo
    es `agente.hallazgos.arreglo_aplicado`, que escribió el agente
    determinista."""
    t = (RAIZ / "scripts" / "eval_investigador.py").read_text(encoding="utf-8")
    assert "arreglo_aplicado" in t, "sin la verdad de campo no hay eval"
    for prohibido in ("core.ai", "core.llm", "langchain", "ChatOpenAI"):
        assert prohibido not in t, f"el eval no puede llamar a un modelo ({prohibido})"
    # Tres resultados, no dos: lo que no se pudo comparar no cuenta como error.
    assert "INDECIDIBLE" in t


def test_el_piso_de_cada_investigacion_nombra_herramientas_que_existen():
    """Un piso que pide una herramienta inexistente hace que el motor la exija
    para siempre: el síntoma sería un agente que da vueltas sin concluir, con
    la causa muy lejos del efecto."""
    inv = (LAB / "investigaciones.py").read_text(encoding="utf-8")
    assert "_validar()" in inv, "el catálogo tiene que validarse al importarse"
