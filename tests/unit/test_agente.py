"""EL AV AGENT 2.0 — sus invariantes. Doc: `docs/AGENT_2.0.md` §8.

Estos tests no prueban detectores: prueban que **no haya dónde equivocarse**.
Cada uno corresponde a un invariante del doc, y a un bug real del agente viejo.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agente import arreglos, catalogo, registro, tipos
from agente.detectores import datos, mercado, sistema

RAIZ = Path(__file__).resolve().parents[2]


# ── El catálogo se sostiene solo ───────────────────────────────────────────

def test_toda_habilidad_declara_lo_que_hay_que_saber():
    """Sumar una habilidad es UNA fila. La validación vive en el dataclass, así
    que una fila incompleta no llega a existir — no hace falta un test que se lo
    recuerde a nadie.

    **Un test que existe para recordarte algo es la señal de que el diseño no lo
    garantiza solo.** Este solo confirma que la validación está puesta.
    """
    assert catalogo.HABILIDADES, "el catálogo está vacío"
    for h in catalogo.HABILIDADES.values():
        assert h.que_mira.strip()
        assert h.dominio in tipos.DOMINIOS
        assert h.ventana in tipos.VENTANAS
        assert callable(h.correr)

    with pytest.raises(ValueError):
        tipos.Habilidad(nombre="x", tipo="detector", dominio="MERCADO",
                        que_mira="", cada_segundos=60, correr=lambda u: [])
    with pytest.raises(ValueError):
        tipos.Habilidad(nombre="x", tipo="detector", dominio="INVENTADO",
                        que_mira="algo", cada_segundos=60, correr=lambda u: [])


def test_todo_arreglo_declarado_existe():
    """Una habilidad no puede apuntar a un arreglo que no está. En el agente
    viejo `salud` y `falta_en_base` declaraban acción y no la tenían: sus
    hallazgos caían en la lista de trabajo con un botón que no arreglaba nada."""
    for h in catalogo.HABILIDADES.values():
        for regla, aid in h.arreglos.items():
            assert aid in arreglos.ARREGLOS, (
                f"«{h.nombre}/{regla}» apunta al arreglo «{aid}», que no existe")


def test_un_arreglo_escribe():
    """**Un arreglo ESCRIBE en algún lado** (invariante 10). Si después de
    apretarlo el sistema quedó igual, no era un arreglo: era un botón de mirar."""
    for a in arreglos.ARREGLOS.values():
        assert a.donde.strip(), f"«{a.id}» no declara dónde escribe"
        src = inspect.getsource(type(a).aplicar)
        assert any(x in src for x in ("UPDATE", "INSERT", "subscribe", "pedir",
                                      "aplicar", "rehacer")), (
            f"«{a.id}».aplicar no parece escribir nada")


# ── La puerta única ────────────────────────────────────────────────────────

def test_solo_registro_escribe_hallazgos():
    """UNA función escribe, y esto prohíbe el resto.

    El agente viejo tenía SEIS puertas escribiendo estado, cada una con su
    criterio sobre qué significaba «resuelto». El costo no se paga en los bugs
    que ya salieron sino en que **nada obligaba a una funcionalidad nueva a usar
    el pipeline que ya existía**, así que cada feature inauguraba la séptima.
    """
    permitidos = {"agente/registro.py", "agente/vista.py",
                  "agente/arreglos.py", "agente/items_compat.py"}
    malos = []
    for f in (RAIZ / "agente").rglob("*.py"):
        rel = str(f.relative_to(RAIZ))
        if rel in permitidos:
            continue
        t = f.read_text()
        # Se busca la ESCRITURA, no la mención: leer `agente.hallazgos` lo hace
        # cualquiera (la lista de prioridad sale de ahí). Escribirla, no.
        if any(x in t for x in ("INSERT INTO agente.hallazgos",
                                "UPDATE agente.hallazgos")):
            malos.append(rel)
    assert not malos, f"escriben hallazgos fuera de la puerta única: {malos}"


def test_una_corrida_que_no_pudo_mirar_no_cierra_nada():
    """**La guarda más importante del subsistema** (invariante 1).

    Una corrida que miró menos de lo habitual no puede convertir su lista más
    corta en «se arreglaron 40 problemas»: es la mentira más cara que puede
    decir una herramienta de integridad, porque deja el tablero en verde justo
    el día que está más ciega.
    """
    src = inspect.getsource(registro.guardar)
    assert "if resultado != tipos.OK:" in src
    i = src.index("if resultado != tipos.OK:")
    assert "return" in src[i:i + 300]
    # Y el corte va DESPUÉS de sellar la corrida: que no se pudo mirar tiene que
    # quedar escrito igual, o «no corrí» se vuelve indistinguible de «corrí».
    assert src.index("sellar_corrida") < i


def test_solo_lo_cerrado_por_accion_reincide():
    """La regla que mantiene VACÍA a la tabla que debe estar vacía.

    Si lo cerrado por AUSENCIA pudiera reincidir, un bono que no operó esa noche
    se auto-resolvería, volvería mañana, y la alarma se llenaría de ruido hasta
    que nadie la mire. La base no puede expresar esto sin un trigger, así que la
    guarda vive en la única función que inserta — y este test la sostiene.
    """
    src = inspect.getsource(registro._ver)
    i = src.index("INSERT INTO agente.reincidencias")
    previo = src[:i]
    assert "cerrado_como = %s" in previo
    assert "tipos.POR_ACCION" in previo


def test_toda_accion_guarda_la_regla_que_la_motivo():
    """Es lo que le permite a HISTORIAL contestar «¿quedó arreglado?».

    En el agente viejo la mayoría de las acciones no guardaban qué las motivó,
    así que la única columna que respondía esa pregunta no podía responderla y
    mostraba todos los problemas del sujeto «por las dudas».
    """
    firma = inspect.signature(registro.anotar_accion).parameters
    for campo in ("habilidad", "sujeto", "regla"):
        assert campo in firma
        assert firma[campo].default is inspect.Parameter.empty or campo == "sujeto"


# ── Los detectores ─────────────────────────────────────────────────────────

def test_ningun_detector_escribe():
    """Un detector mira y devuelve. **Escribir es de los arreglos.**"""
    for mod in (mercado, sistema, datos):
        t = Path(inspect.getfile(mod)).read_text()
        for verbo in ("UPDATE ", "DELETE FROM"):
            assert verbo not in t, f"{mod.__name__} contiene «{verbo}»"


def test_ningun_detector_decide_que_significa_no_poder_mirar():
    """**La pregunta se contesta UNA vez, en el motor** (invariante 6).

    En el agente viejo la contestaban los 19 por su cuenta y no igual: unos
    devolvían vacío en silencio, otros emitían un hallazgo que lo decía, y uno
    reventaba a propósito. Los tres son defendibles; el problema es que era la
    misma decisión tomada 19 veces, y la vigésima se iba a tomar mal.
    """
    src = inspect.getsource(sistema)
    # `except` que devuelve `[]` = decidir en silencio que no hay nada.
    assert "return []\n    except" not in src
    # El único lugar donde se traduce un fallo a estado es el motor.
    from agente import motor
    m = inspect.getsource(motor.correr_una)
    assert "tipos.SinDatos" in m and "tipos.SIN_DATOS" in m and "tipos.ERROR" in m


def test_un_hallazgo_sin_que_hacer_no_existe():
    """Invariante 2. Si no se puede decir qué hacer, la regla está mal pensada:
    una fila que solo dice «esto está mal» le pasa el problema entero al que la
    lee. La base lo exige con un CHECK y el dataclass antes."""
    with pytest.raises(ValueError):
        tipos.Hallazgo(sujeto="AL30", regla="x", severidad="alta",
                       problema="algo", que_hacer="")
    with pytest.raises(ValueError):
        tipos.Hallazgo(sujeto="AL30", regla="", severidad="alta",
                       problema="algo", que_hacer="algo")


# ── Las pantallas ──────────────────────────────────────────────────────────

def test_ahora_es_de_hoy_sin_leer_y_sin_resolver():
    """La regla entera de AHORA, en la vista SQL — no en el navegador."""
    sql = (RAIZ / "sql" / "schema.sql").read_text()
    i = sql.index("CREATE OR REPLACE VIEW agente.v_ahora")
    v = sql[i:i + 1200]
    assert "leido_at IS NULL" in v
    assert "estado IN ('nuevo','en_curso')" in v
    # El día es ART: el día UTC arranca a las 21:00 de acá y mezclaría dos días.
    assert "America/Argentina/Buenos_Aires" in v


def test_a_encontro_solo_entra_lo_que_tiene_arreglo():
    """§6.2. Un AVISO no entra: `salud` declaraba una acción y su puerta era de
    solo lectura — un aviso con forma de trabajo."""
    sql = (RAIZ / "sql" / "schema.sql").read_text()
    i = sql.index("CREATE OR REPLACE VIEW agente.v_encontro")
    assert "arreglo <> ''" in sql[i:i + 1200]


def test_leido_no_es_resuelto():
    """Dos ejes independientes, y por eso dos columnas y no un estado más.
    Marcar leído baja el ruido del día; no cierra nada."""
    from agente import vista
    src = inspect.getsource(vista.marcar_leidos)
    assert "leido_at" in src
    assert "estado" not in src, "marcar leído no puede tocar el estado"
    # Idempotente: marcar dos veces no pisa quién fue el primero.
    assert "leido_at IS NULL" in src


def test_el_contador_no_se_suma_en_el_navegador():
    """Invariante 11. El «AHORA 92» del agente viejo lo sumaba el browser
    juntando cuatro cosas de dos endpoints con frescuras distintas."""
    from agente import vista
    src = inspect.getsource(vista.ahora)
    assert "v_ahora" in src
    assert "len(filas)" in src, "el total sale de la misma query que la lista"


# ── Nada del agente vive fuera del agente ──────────────────────────────────

def test_ningun_detector_vive_en_un_job_ajeno():
    """Invariante 8. `permiso_flojo` vivía adentro de `jobs/db_tamano`, que no
    era del agente: alguien que tocara ese job por otro motivo apagaba un
    chequeo de seguridad sin enterarse."""
    for f in (RAIZ / "jobs").glob("*.py"):
        if f.name.startswith("agente"):
            continue
        t = f.read_text()
        assert "registro.guardar" not in t, f"{f.name} escribe hallazgos"


def test_el_agente_no_se_autoevalua():
    """Invariante 12 (§9.1). *«No entra nada de votos y eso: todo eso generó
    demasiada complejidad en algo que no funcionaba»* — user, 2026-08-24."""
    prohibido = ("av_agent_evals", "eval_set", "hitos_cumplidos",
                 "confianza_del_arreglo", "ya_votados")
    for f in (RAIZ / "agente").rglob("*.py"):
        t = f.read_text()
        for p in prohibido:
            assert p not in t, f"{f.name} todavía habla de «{p}»"


def test_el_agente_nunca_es_alcanzable_por_el_invitado():
    """REGLA #8 del repo, congelada. El agente habla del estado interno del
    sistema, que es exactamente lo que el portal invitado no puede ver."""
    from api.auth import GUEST_PATH_PREFIXES
    assert "/api/agente" not in GUEST_PATH_PREFIXES
