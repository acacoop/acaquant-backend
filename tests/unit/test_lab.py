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
    decorativa justo para el caso que venía a atajar."""
    t = (LAB / "diario.py").read_text(encoding="utf-8")
    assert "cardinality(de_donde) > 0" in t
    assert "array_length(de_donde" not in t


def test_el_piso_de_cada_investigacion_nombra_herramientas_que_existen():
    """Un piso que pide una herramienta inexistente hace que el motor la exija
    para siempre: el síntoma sería un agente que da vueltas sin concluir, con
    la causa muy lejos del efecto."""
    inv = (LAB / "investigaciones.py").read_text(encoding="utf-8")
    assert "_validar()" in inv, "el catálogo tiene que validarse al importarse"
