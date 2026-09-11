"""EL AV AGENT 2.0 — sus invariantes. Doc: `docs/AGENT.md` §8.

Estos tests no prueban detectores: prueban que **no haya dónde equivocarse**.
Cada uno corresponde a un invariante del doc, y a un bug real del agente viejo.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, date, timedelta
from pathlib import Path

import pytest

from agente import arreglos, catalogo, redactar, registro, rehacer, tipos
from agente.detectores import catalogo as cat_det
from agente.detectores import datos, mercado, sistema


def _codigo(fuente) -> str:
    """La fuente SIN comentarios ni docstrings.

    ⚠️ Un test que busca (o prohíbe) un string en el código NO puede leer la
    prosa que explica por qué ese string no está. En este repo los comentarios
    **nombran el bug que evitan** —es su razón de ser—, así que grepear el
    archivo entero hace fallar al test justo cuando la explicación está bien
    escrita: el test castiga documentar. Ya pasó con dos.

    `ast.unparse` devuelve solo código (los comentarios no llegan al árbol) y
    acá además se sacan los docstrings.

    ⚠️ Ojo al escribir el assert: `ast.unparse` **normaliza las comillas** (un
    `r.get("x")` vuelve como `r.get('x')`) y reescribe los f-strings. Buscá el
    identificador, no el fragmento literal tal como está en el archivo.
    """
    src = fuente if isinstance(fuente, str) else inspect.getsource(fuente)
    arbol = ast.parse(textwrap.dedent(src))
    for n in ast.walk(arbol):
        cuerpo = getattr(n, "body", None)
        if (isinstance(cuerpo, list) and cuerpo
                and isinstance(cuerpo[0], ast.Expr)
                and isinstance(getattr(cuerpo[0], "value", None), ast.Constant)
                and isinstance(cuerpo[0].value.value, str)):
            cuerpo.pop(0)
            if not cuerpo:
                cuerpo.append(ast.Pass())
    return ast.unparse(arbol)

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


def test_un_arreglo_que_pide_datos_solo_es_automatico_si_sabe_solo():
    """Lo que una habilidad declara `automatico` tiene que poder aplicarse
    SOLA: sobre una regla con arreglo declarado y, si ese arreglo `pide_datos`
    (un listado editable, no un botón), solo vale si sobreescribe `solo` —
    sabe decir QUÉ escribiría sin que nadie apriete. Un robot no tilda listas
    (`alta_cedear`, `alta_on` siguen sin poder ser automáticos), pero SÍ puede
    escribir lo que una regla determinística ya resolvió (`completar_ficha` +
    `clase_activo`, §0.ei)."""
    from agente.arreglos import Arreglo

    for h in catalogo.HABILIDADES.values():
        for regla, motivo in h.automatico.items():
            assert regla in h.arreglos, (
                f"«{h.nombre}/{regla}» es automático pero no tiene arreglo declarado")
            aid = h.arreglos[regla]
            a = arreglos.ARREGLOS[aid]
            if a.pide_datos:
                assert type(a).solo is not Arreglo.solo, (
                    f"«{h.nombre}/{regla}» es automático y su arreglo «{aid}» "
                    "pide datos, pero no sabe decir qué escribiría SOLO "
                    "(`solo` no está sobreescrito): un robot no tilda listas")
            assert str(motivo).strip(), (
                f"«{h.nombre}/{regla}» es automático sin decir por qué")


def test_el_ejecutor_solo_elige_lo_declarado():
    """`autonomo._elegir` es PURA: prueba con filas armadas a mano, sin base."""
    from agente import autonomo

    base = dict(sujeto="x", regla="no_esta_en_curvas", arreglo="alta_bono")
    reglas = {("on_faltante", "no_esta_en_curvas")}

    declarada_nuevo = {**base, "id": 1, "habilidad": "on_faltante",
                       "estado": tipos.NUEVO, "detectado_at": date(2026, 1, 1)}
    declarada_en_curso = {**base, "id": 2, "habilidad": "on_faltante",
                          "sujeto": "y", "estado": tipos.EN_CURSO,
                          "detectado_at": date(2026, 1, 1)}
    no_declarada = {**base, "id": 3, "habilidad": "otra_habilidad",
                    "sujeto": "z", "estado": tipos.NUEVO,
                    "detectado_at": date(2026, 1, 1)}
    reciente = {**base, "id": 4, "habilidad": "on_faltante", "sujeto": "w",
                "estado": tipos.NUEVO, "detectado_at": date(2026, 1, 1)}
    sin_arreglo = {**base, "id": 5, "habilidad": "on_faltante", "sujeto": "v",
                   "arreglo": "", "estado": tipos.NUEVO,
                   "detectado_at": date(2026, 1, 1)}

    abiertos = [declarada_nuevo, declarada_en_curso, no_declarada, reciente,
                sin_arreglo]
    recientes = {("on_faltante", "w", "no_esta_en_curvas")}

    elegidos = autonomo._elegir(abiertos, recientes, reglas)
    assert elegidos == [declarada_nuevo]

    # Siete declaradas válidas y tope=5: se quedan las 5 más viejas.
    siete = [
        {**base, "id": 10 + i, "habilidad": "on_faltante", "sujeto": f"s{i}",
         "estado": tipos.NUEVO, "detectado_at": date(2026, 1, 1) + timedelta(days=i)}
        for i in range(7)
    ]
    elegidos = autonomo._elegir(siete, set(), reglas, tope=5)
    assert len(elegidos) == 5
    assert [f["id"] for f in elegidos] == [10, 11, 12, 13, 14]


def test_el_ejecutor_repite_lo_repetible_y_no_lo_demas():
    """El sujeto de una regla REPETIBLE es un CAMPO —una familia—: aplicar de
    nuevo no duplica nada, así que `en_curso` sigue siendo candidato. Para lo
    NO repetible, `en_curso` significa «ya se lo apliqué a ESE título» y no
    hay nada más que hacer ahí.

    Y los dos lados de «ya lo intentué en la ventana» no pesan igual: un
    intento FALLIDO frena a cualquiera; uno que salió BIEN solo frena a lo no
    repetible — una familia puede tener títulos nuevos al minuto siguiente.
    """
    from agente import autonomo

    base = dict(sujeto="clase_activo", regla="sin_clase_activo",
                arreglo="completar_ficha", habilidad="ficha_incompleta",
                detectado_at=date(2026, 1, 1))
    reglas = {("ficha_incompleta", "sin_clase_activo")}
    repetibles = frozenset({("ficha_incompleta", "sin_clase_activo")})

    en_curso_repetible = {**base, "id": 1, "estado": tipos.EN_CURSO}
    elegidos = autonomo._elegir([en_curso_repetible], set(), reglas,
                                repetibles=repetibles)
    assert elegidos == [en_curso_repetible], (
        "una familia en_curso sigue teniendo cosas para completar")

    otra = dict(base, habilidad="on_faltante", regla="no_esta_en_curvas",
                arreglo="alta_bono")
    en_curso_no_repetible = {**otra, "id": 2, "estado": tipos.EN_CURSO}
    elegidos = autonomo._elegir(
        [en_curso_no_repetible], set(),
        {("on_faltante", "no_esta_en_curvas")}, repetibles=repetibles)
    assert elegidos == [], "lo no repetible en_curso ya no tiene nada que hacer"

    reciente_ok = {("ficha_incompleta", "clase_activo", "sin_clase_activo")}
    nuevo = {**base, "id": 3, "estado": tipos.NUEVO}
    elegidos = autonomo._elegir([nuevo], set(), reglas, repetibles=repetibles,
                                recientes_ok=reciente_ok)
    assert elegidos == [nuevo], (
        "una acción OK reciente no frena a una familia repetible")

    reciente_fallida = {("ficha_incompleta", "clase_activo", "sin_clase_activo")}
    elegidos = autonomo._elegir([nuevo], reciente_fallida, reglas,
                                repetibles=repetibles)
    assert elegidos == [], "un intento FALLIDO sí frena, incluso a lo repetible"


def test_el_actor_del_agente_es_una_constante_y_no_un_email():
    assert tipos.ACTOR_AGENTE
    assert "@" not in tipos.ACTOR_AGENTE


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

    ⚠️ La guarda se ENDURECIÓ el 2026-09-04 y por eso el SQL cambió: antes se
    pedía el más reciente **entre los cerrados por acción**, lo que se saltea lo
    que haya pasado después. Ahora se piden los dos cierres que AFIRMAN algo
    —acción y caducidad— y **gana el más nuevo**; `ausencia` sigue afuera de la
    query, como siempre. Lo que este test protege no cambió: por ausencia no se
    reincide, nunca.
    """
    src = _codigo(registro._ver)
    i = src.index("INSERT INTO agente.reincidencias")
    previo = src[:i]
    assert "cerrado_como = ANY(%s)" in previo
    assert "tipos.POR_ACCION" in previo
    # Lo cerrado por AUSENCIA no entra a la búsqueda del previo. Si entrara, un
    # bono que no operó esa noche se auto-resolvería y «volvería» cada mañana.
    assert "tipos.POR_AUSENCIA" not in previo


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
    assert "estado IN ('nuevo','en_curso','reincidio')" in v
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


def test_el_boton_de_rehacer_solo_aparece_donde_puede_funcionar():
    """El detector y el arreglo **normalizan el nombre del job igual**.

    Si difirieran, el detector ofrecería el botón y el botón contestaría «ese
    job no se puede rehacer»: la clase de contradicción que hace que la gente
    deje de creerle a la pantalla. Es el mismo defecto que tenía `salud`, que
    declaraba una acción con la puerta de solo lectura.
    """
    from agente import arreglos as arr
    from agente.detectores.sistema import _rehacible

    # ⚠️ UNA sola puerta, no dos reglas iguales. Que fueran idénticas no
    # alcanzaba: las dos hacían `split(":")` + `removeprefix("jobs.")` y las dos
    # daban mal, porque el nombre real del job no sale de transformar el string
    # —sale de `conocido_como`—. Coincidían entre ellas y no con la realidad.
    for f in (_rehacible, arr.RehacerJob._job_fecha):
        assert "cual_job" in inspect.getsource(f), (
            f"{f.__qualname__} tiene que preguntar por `rehacer.cual_job`")
    for f in (_rehacible, arr.RehacerJob._job_fecha):
        assert 'removeprefix("jobs.")' not in _codigo(f), (
            f"{f.__qualname__} volvió a normalizar por su cuenta")

    # Y el árbitro resuelve los cuatro nombres del MISMO job al mismo lugar.
    for alias in ("portafolio_diario", "jobs.portafolio_backfill", "aum",
                  "job:portafolio_diario"):
        assert rehacer.cual_job(alias) == "portafolio_diario", (
            f"«{alias}» no resuelve: la tarjeta que lo use se queda sin botón")
    assert rehacer.cual_job("motor_rofex.service") == ""


def test_salud_no_declara_un_arreglo_que_no_tiene():
    """El bug que el user detectó desde la pantalla, sin ver el código: `salud`
    caía en la lista de trabajo con un botón que solo volvía a chequear."""
    assert not catalogo.HABILIDADES["salud"].arreglos, (
        "salud es un AVISO: mirar no arregla")


def test_no_se_pegan_sujeto_y_regla_en_un_string():
    """**Cualquier separador es una apuesta a que no aparezca en los datos.**

    La primera versión unía sujeto y regla con `chr(0)` para compararlos como un
    solo texto, y Postgres rechaza el NUL en un campo `text`: las 12 habilidades
    que corrieron en la primera pasada real murieron ahí.

    Pero el byte elegido no era el bug. Un sujeto es un ticker, y también
    `mercado.market_snapshot` o `/api/x/{id}`: con cualquier separador
    "imposible" el modo de falla siguiente no es un error, es **emparejar mal en
    silencio**. Se comparan las dos columnas por separado.
    """
    # Se mira el CÓDIGO y no el archivo entero: el comentario que explica el bug
    # nombra `chr(0)` a propósito, y un test que falla por su propia
    # documentación no prueba nada.
    #
    # ⚠️ Antes esto se hacía cortando la fuente desde el primer `cur.execute(`.
    # Ese tajo mira una POSICIÓN, no el código: cuando el predicado se factorizó
    # a una constante —para que el SELECT que decide y el UPDATE que cierra no
    # tuvieran dos copias de «no vino»— quedó ARRIBA del corte y el test dejó de
    # verlo. `_codigo` saca docstrings y comentarios, así que cubre la función
    # entera sin leer la prosa: es más cobertura, no menos.
    sql = _codigo(registro._cerrar_ausentes)
    assert "chr(0)" not in sql and "||" not in sql, (
        "sujeto y regla no se concatenan: cualquier separador es una apuesta a "
        "que no aparezca en los datos")
    assert "unnest(%s::text[], %s::text[])" in sql


def test_una_escritura_fallida_no_deja_la_corrida_en_ok():
    """El sello va ANTES para que una caída dura deje rastro igual. Pero si la
    escritura falla hay que VOLVER a sellar: si no, el catálogo dice «miré y
    estaba todo bien» sobre una pasada que no guardó una fila.

    Pasó en la primera corrida real: 12 habilidades reventaron escribiendo y las
    12 quedaron marcadas `ok`.
    """
    src = inspect.getsource(registro.guardar)
    i = src.index("except Exception as e:")
    assert "sellar_corrida" in src[i:], "el fallo de escritura no se re-sella"
    assert "tipos.ERROR" in src[i:]
    assert "raise" in src[i:], "y el motor tiene que enterarse"


def test_las_rutas_del_agente_apuntan_a_la_raiz_del_repo():
    """`agente/` está UN nivel más arriba que `api/services/`, de donde vinieron
    estos módulos. Con `parents[2]` el detector de crons buscaba en
    `/root/deploy/crontab.txt` y **no fallaba**: decía «no pude leer el
    crontab», que es una respuesta legítima. Un error de ruta disfrazado de
    degradación honesta es de los que duran meses.
    """
    from agente import crontab
    src = inspect.getsource(crontab.del_repo)
    assert "parents[1]" in src
    assert (RAIZ / "deploy" / "crontab.txt").exists()


def test_el_agente_no_reimplementa_quien_cotiza():
    """«¿Este símbolo cotiza?» tiene UN lector: `core/instrumentos_validos`, el
    mismo que aplica `core/websocket.py` a TODAS las suscripciones de todos los
    motores.

    El agente escribió su propio `SELECT symbol FROM manager.pyrofex_instruments`
    y esa columna **no existe** — el catálogo guarda los símbolos adentro de un
    jsonb. Se degradó honestamente y siguió, así que no rompió nada: corrió
    CIEGO, que es peor de encontrar.

    Y el nombre de columna era el síntoma. El bug era el SEGUNDO lector: dos
    respuestas a la misma pregunta terminan siempre igual — una se queda vieja y
    nadie sabe cuál manda (REGLA #9 del repo).
    """
    from agente import fuentes
    src = _codigo(fuentes.primary)
    assert "instrumentos_validos" in src
    assert "pyrofex_instruments" not in src, (
        "el agente no lee ese catálogo directo: delega en el criterio único")


def test_no_se_adivina_el_ticker_por_sufijo():
    """REGLA #9: la identidad no es el nombre. `.rstrip("DC")` convierte `TXAD`
    en `TXA` y `PBAC` en `PBA` — dos cosas distintas emparejadas sin que falle
    nada. Y no hace falta: Primary lista `AL30`, `AL30D` y `AL30C` como símbolos
    separados, así que el base ya está en el conjunto por derecho propio."""
    from agente import fuentes
    src = inspect.getsource(fuentes.tickers_en_primary)
    codigo = "\n".join(l for l in src.splitlines()
                       if not l.strip().startswith("#") and "`" not in l)
    assert "rstrip" not in codigo and "strip(\"D" not in codigo


# ── El horario de mercado ──────────────────────────────────────────────────

def test_el_horario_de_mercado_vive_en_un_solo_lugar():
    """User (2026-08-24): *«es fundamental que todo tenga claro el horario de
    mercado para saber cuándo frenar»*.

    Es la mitad de lo que hace que el agente signifique algo: un precio sin
    actualizarse hace 282 minutos es un problema a las 11 y es lo NORMAL a las
    18. Si cada detector tuviera su propia idea de la hora, la mitad de los
    hallazgos serían falsos y la otra mitad llegaría tarde.
    """
    from agente import reloj

    for nombre in ("RUEDA_UTC", "CIERRE_UTC", "CIERRE_DURA_MIN"):
        assert hasattr(reloj, nombre)
    # Nadie define su propia ventana: el horario se pregunta, no se recalcula.
    for f in (RAIZ / "agente").rglob("*.py"):
        if f.name == "reloj.py":
            continue
        t = f.read_text()
        assert "RUEDA_UTC = " not in t, f"{f.name} redefine el horario"


def test_mirar_ahora_fuerza_el_RITMO_y_nunca_la_VENTANA():
    """⚠️ **EL BOTÓN QUE NO HACÍA NADA** (§0.dz).

    User (2026-09-06): *«¿«correr ahora» le decís a eso? porque ese botón NO HACE
    NADA»*. Y era literal: llamaba a `motor.tick()`, que corre **solo las que les
    toca** — o sea exactamente lo que el daemon iba a correr un segundo después.
    A las 22:50 no le tocaba a ninguna, así que no pasaba nada y sin explicación.

    Los dos frenos no son lo mismo:

      RITMO   («cada 2 h alcanza»)  es una decisión de FRECUENCIA. Una persona
                                    que aprieta un botón la anula a propósito.
      VENTANA («solo en rueda»)     es una condición del MUNDO. Fuera de rueda
                                    `bono_sin_precio` vería todos los precios
                                    viejos y cantaría cien problemas falsos.

    Se puede forzar el primero; el segundo no. Y lo que la ventana frena se
    INFORMA, porque «no pasó nada» y «no había nada que hacer» se veían igual.
    """
    from datetime import UTC, datetime

    from agente import motor

    # Una habilidad de rueda, recién corrida, con la rueda ABIERTA: el ritmo la
    # frena y el botón la desfrena.
    ahora = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)          # miércoles, en rueda
    recien = {"nombre": "x", "cada_segundos": 7200, "ventana": "rueda",
              "activa": True, "ultima_corrida_at": ahora}
    assert not motor._le_toca(recien, ahora)
    assert motor._le_toca(recien, ahora, forzar=True), "el botón anula el RITMO"

    # La misma, con la rueda CERRADA: ni el botón la corre, y se dice por qué.
    noche = datetime(2026, 9, 3, 1, 50, tzinfo=UTC)
    assert not motor._le_toca(recien, noche, forzar=True), (
        "forzar NUNCA puede saltear la ventana: es una condición del mundo")
    assert motor._fuera_de_ventana(recien, noche) == "la rueda está cerrada"

    # Y el endpoint que aprieta la persona fuerza; el daemon no.
    import inspect as _i

    from api.routers import agente as router_agente
    assert "motor.tick(forzar=True," in _i.getsource(router_agente.correr)
    assert "tick(forzar=True)" not in _i.getsource(motor.correr_para_siempre) \
        if hasattr(motor, "correr_para_siempre") else True


def test_la_pasada_A_PEDIDO_entra_en_el_presupuesto_del_transporte():
    """⚠️ **ARREGLAR UN BOTÓN MUDO Y DEJARLO ROTO ES PEOR** (§0.eb).

    §0.dz hizo que «mirar ahora» forzara el ritmo. Efecto no previsto: pasó de
    correr casi nada a correr las ~27 habilidades de una, se fue muy arriba de
    los 30 s del proxy de Vercel, y el botón empezó a contestar «no pude correr
    la pasada». El cambio era correcto y el resultado, peor.

    El presupuesto del daemon y el de la pantalla son DOS números distintos
    porque los limita otra cosa: al daemon, que la pasada vuelva; a la pantalla,
    un transporte que corta. Y lo que no entra **no se pierde**: vuelve en
    `faltaron`, que es lo que hace que «se cortó» y «no había nada» dejen de
    verse igual.
    """
    from agente import motor

    assert motor.PRESUPUESTO_PEDIDO_S < 30, (
        "el proxy de `src/app/api/agente/[...path]/route.ts` tiene "
        "maxDuration = 30: una pasada a pedido más larga que eso NO llega")
    assert motor.PRESUPUESTO_PEDIDO_S < motor.PRESUPUESTO_S
    # El endpoint que aprieta la persona usa el corto; el daemon, el largo.
    from api.routers import agente as router_agente
    src = inspect.getsource(router_agente.correr)
    assert "presupuesto_s=motor.PRESUPUESTO_PEDIDO_S" in src
    assert "presupuesto_s" not in inspect.getsource(motor.tick).split("def tick")[0]
    # Y `faltaron` viaja: sin él, una pasada cortada se ve igual que una entera.
    assert '"faltaron"' in inspect.getsource(motor.tick)


def test_el_cierre_es_una_ventana_y_no_un_quinto_reloj():
    """Lo que se le pide al mercado deja de pedirse a las 17, y lo que quedó sin
    resolver se completa UNA vez a las 17:30.

    Esa ventana vive en el catálogo, no en un cron: un cron aparte sería el
    quinto reloj, y salir de los cuatro relojes fue todo el punto del rediseño.
    """
    from agente import reloj, tipos

    assert "cierre" in tipos.VENTANAS
    assert reloj.CIERRE_UTC == (20, 30), "20:30 UTC = 17:30 ART"

    cierre = [h for h in catalogo.HABILIDADES.values() if h.ventana == "cierre"]
    assert cierre, "nadie barre lo que quedó sin resolver al cerrar"

    from agente import motor
    # La ventana la evalúa `_fuera_de_ventana`, que `_le_toca` consulta: es el
    # ÚNICO lugar donde vive la condición del mundo, y por eso el botón «mirar
    # ahora» puede forzar el ritmo sin poder forzarla a ella (§0.dz).
    assert "reloj.en_cierre" in inspect.getsource(motor._fuera_de_ventana)
    assert "_fuera_de_ventana" in inspect.getsource(motor._le_toca)


def test_no_se_le_pide_al_mercado_despues_de_las_17():
    """El cron intradiario para a las 17 ART.

    ⚠️ `13-20` en cron incluye la hora 20 ENTERA (20:00 a 20:59), o sea que
    seguía pidiéndole precios a 1816 hasta las 17:59 ART con el mercado cerrado
    desde las 17. Es el error de rango que no se ve leyendo.
    """
    import re
    cron = (RAIZ / "deploy" / "crontab.txt").read_text()
    for linea in cron.splitlines():
        if linea.strip().startswith("#") or "agente_tasa" not in linea:
            continue
        horas = linea.split()[1]
        m = re.match(r"^(\d+)-(\d+)$", horas)
        assert m, f"la hora del cron de agente_tasa no es un rango: {horas}"
        assert int(m.group(2)) <= 19, (
            f"«{horas}» llega hasta las {int(m.group(2))}:59 UTC = "
            f"{int(m.group(2)) - 3}:59 ART, con el mercado ya cerrado")


def test_la_tasa_de_1816_no_le_gana_al_motor():
    """El fallback se aplica SOLO si la TEA quedó vacía.

    Donde el motor calcula, su número es LIVE y el de 1816 tiene atraso:
    reemplazarlo sería empeorar la vista para ganar consistencia con un
    proveedor. Y va en la LECTURA, no escribiendo en `market_snapshot` — esa
    tabla es del motor, y dos fuentes en la misma celda dejan sin forma de
    saber cuál ganó.
    """
    from agente import tasa_1816

    src = (RAIZ / "api" / "services" / "curvas_vista.py").read_text()
    i = src.index("tasas_agente.get(tc)")
    ventana = src[max(0, i - 400):i]
    assert 'm_pill.get("TEA") is None' in ventana, (
        "el fallback tiene que estar condicionado a que no haya TEA")
    assert "market_snapshot" not in inspect.getsource(tasa_1816.refrescar), (
        "la tasa de 1816 NO se escribe en la tabla del motor")


def test_el_vocabulario_de_python_y_el_CHECK_de_la_base_dicen_lo_mismo():
    """**El mismo dato en dos lugares, y nadie manteniéndolos iguales** — la
    REGLA #9 del repo, adentro del propio agente.

    `CREATE TABLE IF NOT EXISTS` no toca la tabla que ya existe, así que agregar
    un valor a `tipos.VENTANAS` deja a la base rechazándolo, y el agente muere
    al sincronizar el catálogo: lo primero que hace al arrancar. Pasó con
    `cierre`.

    Acá el árbitro es Python; el CHECK es la copia. Este test es lo único que
    impide que se separen otra vez.
    """
    import re

    from agente import tipos

    sql = (RAIZ / "sql" / "schema.sql").read_text()

    def del_check(nombre: str) -> set[str]:
        # El ÚLTIMO, que es el que gana: el bloque de migración del final del
        # archivo redefine el del CREATE TABLE.
        # El `IS NULL OR` de los nullables entra en el patrón: se busca el
        # `IN (...)` de la columna, venga solo o detrás de la guarda de nulo.
        trozos = re.findall(
            rf"CONSTRAINT {nombre}\s*\n?\s*CHECK \([^)]*?[a-z_]+ IN \(([^)]*)\)",
            sql)
        assert trozos, f"no encontré el CHECK «{nombre}»"
        return {v.strip().strip("'") for v in trozos[-1].split(",")}

    assert del_check("habilidades_ventana_ok") == set(tipos.VENTANAS)
    assert del_check("habilidades_tipo_ok") == set(tipos.TIPOS)
    assert del_check("habilidades_resultado_ok") == {
        tipos.OK, tipos.SIN_DATOS, tipos.ERROR}
    assert del_check("hallazgos_severidad_ok") == set(tipos.SEVERIDADES)
    assert del_check("hallazgos_estado_ok") == set(tipos.ESTADOS)
    assert del_check("hallazgos_cierre_ok") == set(tipos.CIERRES)


def test_los_campos_de_1816_no_se_inventan():
    """**La API rechaza la llamada ENTERA con HTTP 400 si UN campo no existe.**

    O sea que un nombre inventado no degrada: apaga la habilidad completa. La
    primera versión pidió `tir` y `precio` —que no existen— y se llevó puesto el
    barrido del cierre entero.

    Los nombres buenos ya estaban medidos en `jobs/tamar_1816`, que corre todos
    los días. Este test ata las dos listas: si alguien agrega un campo acá, tiene
    que ser uno que el otro job ya probó contra la API.
    """
    import re

    src = (RAIZ / "jobs" / "tamar_1816.py").read_text()
    m = re.search(r"_CAMPOS = \[([^\]]*)\]", src)
    assert m, "no encontré los campos verificados de tamar_1816"
    probados = {x.strip().strip("\"'") for x in m.group(1).split(",")}

    a = (RAIZ / "agente" / "tasa_1816.py").read_text()
    m2 = re.search(r"CAMPOS = \(([^)]*)\)", a)
    usados = {x.strip().strip("\"'") for x in m2.group(1).split(",") if x.strip()}
    invalidos = usados - probados
    assert not invalidos, (
        f"{invalidos} no están en los campos que `jobs/tamar_1816` ya probó "
        f"contra la API — un campo inventado es un HTTP 400 y la habilidad "
        f"entera apagada")


def test_se_lee_la_respuesta_de_1816_como_la_lee_el_job_que_anda():
    """`{instrumentos: {TICKER: {...}}, fechaOperacion}`. La primera versión
    buscaba `data` / `indicadores`, que no existen: habría escrito cero filas en
    silencio aunque la llamada saliera bien."""
    a = (RAIZ / "agente" / "tasa_1816.py").read_text()
    cuerpo = a[a.index("def refrescar"):a.index("def purgar")]
    assert '"instrumentos"' in cuerpo and '"fechaOperacion"' in cuerpo
    assert '"data"' not in cuerpo


def test_ninguna_linea_del_libro_sale_sin_el_trio():
    """La cadena de alta anota sus propias líneas y no conoce el hallazgo que la
    disparó, así que salían con `? · ?` — justo el campo que hace que HISTORIAL
    pueda contestar «¿quedó arreglado?».

    Pasarle el trío a mano a las 14 llamadas de `alta.py` sería pedirle a cada
    una que se acuerde. Se resuelve donde SÍ se sabe: el arreglo abre el
    contexto y todo lo que se anote adentro lo hereda.
    """
    from agente import arreglos as arr
    from agente import libro

    assert "libro.contexto" in inspect.getsource(arr.aplicar)
    # Y no se anota DOS veces la misma acción: la que lleva su propio libro ya
    # dijo qué hizo, y una segunda fila es la menos informativa de las dos.
    assert "if not cuantas():" in inspect.getsource(arr.aplicar)
    assert "ctx.get(\"habilidad\")" in inspect.getsource(libro.registrar)


def test_el_alta_no_llama_a_una_tabla_de_avisos_que_ya_no_existe():
    """`agente/vista` no tiene `crear_avisos`: en 2.0 no hay tabla de avisos.

    Lo que el alta no pudo completar viaja EN EL RESULTADO y queda en el libro
    con la acción. No se pierde nada que el sistema pueda re-encontrar solo — un
    bono sin flujos lo canta `bono_sin_flujo`; sin precio, `bono_sin_precio`.
    """
    alta = (RAIZ / "agente" / "alta.py").read_text()
    assert "crear_avisos" not in alta
    assert '"pendientes": pendientes' in alta


def test_el_error_crudo_es_un_campo_y_no_queda_enterrado():
    """User (2026-08-25): *«debería verse el código del error real; con eso
    alcanza para darme cuenta de quién es el error»*.

    El error se calculaba y quedaba en `evidencia` (jsonb), que la pantalla no
    lee. Lo que se veía en su lugar era un `que_hacer` de molde: **la misma
    frase para AUNESA, 1816, BCRA e Interbanking**, escrita a mano en el
    detector y sin salir de ningún dato. Un texto que no cambia con el caso no
    informa: entrena a saltearlo.
    """
    assert "detalle" in tipos.Hallazgo.__dataclass_fields__

    sql = (RAIZ / "sql" / "schema.sql").read_text()
    i = sql.index("CREATE OR REPLACE VIEW agente.v_ahora")
    assert "f.detalle" in sql[i:i + 1200], "AHORA no publica el error crudo"

    # Y la puerta única lo escribe: sin esto el campo existiría vacío siempre.
    src = inspect.getsource(registro._ver)
    assert "h.detalle" in src


def test_que_hacer_no_es_la_misma_frase_para_todos():
    """Un `que_hacer` idéntico para cuatro proveedores distintos no es una
    instrucción: es relleno para pasar el CHECK de la base."""
    src = inspect.getsource(sistema.proveedor_caido)
    i = src.index("que_hacer=")
    frase = src[i:i + 400]
    assert "p.rompe" in frase or "rompe" in frase, (
        "el `que_hacer` tiene que decir qué rompe ESTE proveedor")
    assert "se arregla del otro lado" not in frase.lower()


def test_las_columnas_del_agente_existen_antes_que_sus_vistas():
    """`apply_schema` ejecuta el archivo EN ORDEN.

    Un `ALTER TABLE ... ADD COLUMN` puesto al final crea la columna **después**
    de que las vistas intentaron leerla, y el deploy corta con «column f.detalle
    does not exist». Pasó con `detalle` (2026-08-25): el schema quedó a medias
    —578 statements OK, 2 en error— y la API no se reinició.

    Falla ruidosamente, que es lo bueno. Este test lo corre antes.
    """
    import re

    sql = (RAIZ / "sql" / "schema.sql").read_text()
    primera_vista = min(
        sql.index(f"CREATE OR REPLACE VIEW agente.{v}")
        for v in ("v_ahora", "v_encontro", "v_habilidades"))

    tarde = [m.group(1) for m in
             re.finditer(r"ALTER TABLE agente\.(\w+) ADD COLUMN", sql[primera_vista:])]
    assert not tarde, (
        f"hay ALTER de {set(tarde)} DESPUÉS de las vistas del agente: la vista "
        f"se crea antes que la columna y `apply_schema` corta")


def test_las_vistas_del_agente_se_dropean_antes_de_recrearse():
    """`CREATE OR REPLACE VIEW` sólo sabe AGREGAR columnas AL FINAL.

    Una columna nueva metida en el MEDIO de la lista Postgres la lee como un
    RENOMBRE de la que ocupaba esa posición y rechaza el statement entero:
    «cannot change name of view column "que_hacer" to "detalle"» (2026-08-25,
    el deploy cortó dos veces seguidas por esto).

    El `DROP VIEW IF EXISTS` de arriba lo hace un no-problema — pero sólo
    mientras esté. Este test lo sostiene.
    """
    sql = (RAIZ / "sql" / "schema.sql").read_text()
    for v in ("v_ahora", "v_encontro", "v_habilidades"):
        crear = f"CREATE OR REPLACE VIEW agente.{v} AS"
        assert f"DROP VIEW IF EXISTS agente.{v};\n{crear}" in sql, (
            f"agente.{v} se recrea sin DROP previo: agregarle una columna en "
            f"el medio corta el deploy con «cannot change name of view column»")


# ── QUE NO VUELVA EL CÓDIGO MUERTO ─────────────────────────────────────────

def test_ninguna_funcion_publica_del_agente_quedo_sin_llamador():
    """**Una función pública que nadie llama es una trampa, no un sobrante.**

    Al rehacer el agente quedaron adentro del paquete cuatro detectores del
    modelo viejo —`crontab.detectar_crontab`, `latencia.detectar_latencia`,
    `seguridad.detectar_seguridad`, `tablas.detectar_tablas`— cada uno duplicado
    por su equivalente en `detectores/`. Ninguno corría. El daño no es el
    espacio: es que el próximo que abra el archivo va a creer que ESE es el
    detector, y que dos de ellos se tragaban el error devolviendo `[]`, que es
    justo lo que el invariante 6 prohíbe.

    Se busca la referencia por NOMBRE en todo el repo, no la llamada: un detector
    se declara en el catálogo sin paréntesis (`correr=mercado.bono_sin_tasa`) y
    eso cuenta como uso.
    """
    import ast
    import re

    fuente = {}
    for p in (RAIZ / "agente").rglob("*.py"):
        fuente[p] = p.read_text()
    todo = "\n".join(p.read_text() for p in RAIZ.rglob("*.py")
                     if "site-packages" not in str(p) and ".venv" not in str(p))

    huerfanas = []
    for archivo, src in fuente.items():
        for n in ast.parse(src).body:
            if not isinstance(n, ast.FunctionDef) or n.name.startswith("_"):
                continue
            # `main`/`run` son puntos de entrada: los llama la línea de comandos.
            if n.name in {"main", "run"}:
                continue
            usos = len(re.findall(r"\b" + re.escape(n.name) + r"\b", todo))
            # 1 = su propia definición. Menos de 2 = nadie más la nombra.
            if usos < 2:
                huerfanas.append(f"{archivo.relative_to(RAIZ)}::{n.name}")
    assert not huerfanas, (
        "funciones públicas del agente que nadie llama — o se usan, o se borran: "
        + ", ".join(sorted(huerfanas)))


def test_ningun_modulo_del_agente_decide_en_silencio_que_no_hay_nada():
    """Invariante 6, **en todo el paquete y no solo en `detectores/`**.

    El test viejo miraba `agente/detectores/sistema.py`. Los cuatro detectores
    huérfanos vivían un directorio más arriba y hacían exactamente lo prohibido:
    `except Exception: … return []`. Quedaban fuera del control por dónde
    estaban, no por lo que hacían.
    """
    import re

    malos = []
    for p in (RAIZ / "agente").rglob("*.py"):
        src = p.read_text()
        # `except …:` cuyo cuerpo devuelve una lista vacía = «no pude mirar» se
        # convierte en «no hay nada», callado.
        if re.search(r"except\s+[^\n]*:\s*\n(\s+#[^\n]*\n)*\s+return \[\]", src):
            malos.append(str(p.relative_to(RAIZ)))
    assert not malos, (
        f"{malos} traducen un fallo a lista vacía: eso lo decide el MOTOR, "
        f"el detector levanta `SinDatos`")


def test_ninguna_habilidad_detecta_con_ia():
    """**DETECTAR sigue siendo determinista. REDACTAR no.** La línea importa.

    Desde §0.dn el agente SÍ usa el gateway: `agente/redactar.py` le pide al
    modelo el texto de los avisos. Lo que este test congela es la mitad que no
    se negocia — **ninguna habilidad decide con IA**. `usa_ia` significa «esta
    habilidad usa un modelo para saber si hay un problema», y eso sigue en
    False en TODAS: si un modelo pudiera abrir o cerrar un hallazgo, el
    invariante #1 (una corrida que no pudo mirar no cierra nada) dejaría de ser
    verificable, porque «no pudo mirar» pasaría a ser una opinión.

    Y sigue prohibida la config de las tres tareas del agente VIEJO
    (`av_agent_*`): eran tareas que nadie podía invocar, y dejar su config hace
    creer al que lee el gateway que el agente hace algo que no hace.
    """
    import re
    src = (RAIZ / "core" / "ai.py").read_text()
    declaradas = set(re.findall(r'^\s*"(av_agent_\w+)":\s*\{', src, re.M))
    assert not declaradas, f"tareas de IA del agente viejo aún declaradas: {declaradas}"
    con_ia = [h.nombre for h in catalogo.HABILIDADES.values() if h.usa_ia]
    assert not con_ia, f"habilidades que declaran usa_ia: {con_ia}"


def test_toda_habilidad_que_depende_de_una_foto_la_mantiene_ella():
    """**El bug que no falla: leer una foto que nadie saca.**

    `tabla_quieta` lee el perfil de las ~190 tablas y `permiso_flojo` lee la foto
    de la superficie HTTP. Las dos fotos las escribía `jobs/db_tamano.py`, que se
    borró al rehacer el agente. Desde entonces las dos habilidades comparaban una
    foto de agosto contra sí misma, reportaban `ok`, y no podían ver ni una tabla
    nueva ni un endpoint que perdiera su gate.

    No hay forma de deducir esto del código: hay que declarar que la habilidad es
    dueña de su memoria. Este test lo sostiene.
    """
    sistema = (RAIZ / "agente" / "detectores" / "sistema.py").read_text()
    datos = (RAIZ / "agente" / "detectores" / "datos.py").read_text()

    i = sistema.index("def tabla_quieta")
    cuerpo = sistema[i:sistema.index("\ndef ", i + 10)]
    assert "tablas.barrer()" in cuerpo, "tabla_quieta no rebarre su propio perfil"
    assert "en_rueda()" in cuerpo, "el rebarrido tiene que evitar la rueda (REGLA #4)"

    j = datos.index("def permiso_flojo")
    fin = datos.find("\ndef ", j + 10)
    cuerpo = datos[j:] if fin < 0 else datos[j:fin]
    assert "seguridad.sacar_foto()" in cuerpo, (
        "permiso_flojo compara contra una foto que nadie saca")
    assert (cuerpo.index("seguridad.sacar_foto()")
            < cuerpo.index("seguridad.comparar()")), (
        "la foto de hoy va ANTES de comparar, o el delta es de ayer contra ayer")


# ── EL SILENCIO ────────────────────────────────────────────────────────────

def test_la_puerta_consulta_el_silencio_antes_de_crear_un_hallazgo():
    """**«No me interesa» es del PROBLEMA, no de la fila** (2026-08-27).

    El botón viejo marcaba el hallazgo como `ignorado` y nada más. Pero `_ver`
    busca por el TRÍO entre los ABIERTOS —y un ignorado no está abierto—, así
    que no lo encontraba, lo daba por nuevo, y creaba otra fila. El índice único
    tampoco chocaba: excluye `ignorado`. El bono descartado volvía dos horas
    después como si fuera la primera vez, y la lista nunca podía llegar a cero.
    """
    src = inspect.getsource(registro.guardar)
    assert "_silenciados(conn, habilidad)" in src, (
        "`guardar` no lee la tabla de silenciados")
    # Una query por corrida, no una por hallazgo: una habilidad que ve 200
    # sujetos no puede pagar 200 consultas para preguntar lo mismo.
    assert src.index("_silenciados(conn") < src.index("for h in filas"), (
        "el silencio se lee UNA vez por corrida, antes del bucle")


def test_lo_silenciado_igual_cuenta_como_visto():
    """La guarda que impide que silenciar rompa el cierre por ausencia.

    Si el sujeto silenciado no entrara en `vistos`, `_cerrar_ausentes` lo daría
    por RESUELTO —con el problema todavía ahí— y al levantar el silencio
    volvería a nacer, perdiendo su antigüedad. Silenciar esconde una fila; no
    puede cambiar lo que el agente cree que existe.
    """
    src = inspect.getsource(registro.guardar)
    i = src.index("for h in filas")
    cuerpo = src[i:src.index("cerrados = _cerrar_ausentes")]
    assert cuerpo.index("vistos.append") < cuerpo.index("continue"), (
        "el sujeto silenciado tiene que entrar en `vistos` ANTES del salto, "
        "o el cierre por ausencia lo da por resuelto")


def test_ignorar_silencia_el_trio_y_no_solo_la_fila():
    """Las DOS escrituras, y en orden: la fila sale de la pantalla ahora, el
    trío entra en `silenciados` para que no vuelva a nacer. Sin la segunda el
    botón miente; sin la primera el aviso sigue ahí hasta la próxima corrida."""
    from agente import vista
    src = inspect.getsource(vista.ignorar)
    assert "INSERT INTO agente.silenciados" in src
    assert "UPDATE agente.hallazgos" in src
    assert "DELETE FROM agente.silenciados" in src, "`deshacer` no revive nada"


def test_el_silencio_es_permanente_por_defecto():
    """`hasta` NULL = para siempre, y **no hay nada en el código que lo llene**:
    el vencimiento es una excepción que se carga a mano en la base cuando
    alguien la elige (pedido del user 2026-08-27)."""
    sql = (RAIZ / "sql" / "schema.sql").read_text()
    i = sql.index("CREATE TABLE IF NOT EXISTS agente.silenciados")
    tabla = sql[i:sql.index(");", i)]
    linea = next(l for l in tabla.split("\n") if l.strip().startswith("hasta"))
    assert "DEFAULT" not in linea, (
        f"`hasta` no puede tener default —NULL, «para siempre», es la decisión— "
        f"y dice: {linea.strip()}")

    from agente import vista
    assert "hasta" not in _codigo(vista.ignorar).replace(
        "hasta = NULL", ""), "el código no debe poner vencimientos por su cuenta"


def test_el_silencio_no_aparece_en_ninguna_pantalla():
    """**Cero huella visual** (pedido del user 2026-08-27): los ignorados no
    ensucian el modal. El único lugar donde se enumeran es `diag_agente`, o sea
    la terminal — porque un silencio que nadie puede listar es cómo muere un
    monitoreo, pero eso se resuelve en la consola y no en la pantalla.
    """
    for v in ("v_ahora", "v_encontro", "v_habilidades"):
        sql = (RAIZ / "sql" / "schema.sql").read_text()
        i = sql.index(f"CREATE OR REPLACE VIEW agente.{v} AS")
        assert "silenciados" not in sql[i:sql.index(";", sql.index("FROM", i))], (
            f"agente.{v} no puede mencionar el silencio")
    assert "silenciados" not in (RAIZ / "agente" / "vista.py").read_text().replace(
        "agente.silenciados", ""), "la vista no expone el silencio a la pantalla"
    assert "agente.silenciados" in (RAIZ / "scripts" / "diag_agente.py").read_text(), (
        "el diag es el único lugar donde el silencio se puede enumerar")


def test_motor_caido_pregunta_si_el_dia_esta_en_la_tabla():
    """**El RESULTADO, no el proceso** — lo único que se rescató de `dia_sin_dato`.

    El detector mira FRESCURA: hace cuánto que la pieza no escribe. Eso falla por
    los dos lados —un job puede reventar al final habiendo escrito todo (nada que
    rehacer) y puede salir en verde sin dejar una fila (todo por rehacer)—, así
    que para los relanzables se pregunta si el DÍA está en la tabla.

    Era la razón de ser del control `dia_sin_dato`, dado de baja el 2026-08-27.
    Duplicaba al detector en todo menos en esto, y por eso la pregunta se mudó
    acá en vez de irse con él.
    """
    src = (RAIZ / "agente" / "detectores" / "sistema.py").read_text()
    assert "_el_dia(unidad)" in src, "motor_caido no pregunta por el día"

    # ⚠️ **TRES estados, no dos.** Antes esto devolvía `None` para «el día
    # está», «no pude mirar» y «no es relanzable» por igual, así que la tarjeta
    # no podía distinguir quedarse tranquilo de tener que actuar.
    src_r = inspect.getsource(rehacer.estado_del_dia)
    for e in ("falta", "esta", "no_pude"):
        assert f'"{e}"' in src_r, f"falta el estado «{e}»"
    # `None` de `hay_dato` es «no pude mirar» y NUNCA se publica como «falta».
    assert 'estado = "no_pude" if hay is None' in src_r

    # Y el botón cuelga SOLO de que el día falte: sobre «ya está» únicamente
    # podría contestar «no hacía falta», que es un aviso con forma de trabajo.
    assert 'regla = ("job_sin_dato" if dia and dia["estado"] == "falta"' in src
    assert 'dia["fecha"] if dia\n' in src or '"dia_faltante": (dia["fecha"] if dia' in src


# ── FICHA INCOMPLETA ───────────────────────────────────────────────────────

def test_ficha_incompleta_emite_un_hallazgo_por_campo():
    """**UN hallazgo por CAMPO, no uno por título** (2026-08-27).

    379 títulos sin clase son UN trabajo de carga, no 379 problemas. El control
    viejo emitía una anomalía por fila y por eso su lista no se leía; el agente
    la recibía aplastada en un aviso genérico cuyo sujeto era el nombre del
    control. Los dos defectos son opuestos y los dos terminan igual: nadie mira.

    El sujeto es el CAMPO, así que el trío queda `ficha_incompleta +
    clase_activo + sin_clase_activo`: uno solo, estable, que baja de número
    mientras se completa y se cierra cuando llega a cero.
    """
    from agente.detectores import catalogo as cat

    src = inspect.getsource(cat.ficha_incompleta)
    assert 'sujeto=c["campo"]' in src, "el sujeto tiene que ser el campo"
    # UN solo `Hallazgo(` en toda la función, adentro del bucle de CAMPOS: si
    # apareciera un segundo dentro del bucle de filas, volveríamos a los 379.
    assert src.count("Hallazgo(") == 1
    assert src.index("for c in CAMPOS") < src.index("Hallazgo(")


def test_la_ficha_solo_se_pide_a_lo_que_esta_en_cartera_de_cliente():
    """El alcance es la tenencia, no el catálogo (decisión del user 2026-08-27).

    Medido ese día: 2.229 assets, 1.604 vigentes, **981 en tenencia**. Pedirle la
    ficha a los 2.229 sería pedirla para papeles que nadie tiene, y una lista así
    no se vacía nunca. Con el recorte, completar lo que falta ES terminar el
    trabajo — que es lo único que hace que un contador pueda llegar a cero.
    """
    from agente.detectores import catalogo as cat

    src = (RAIZ / "agente" / "detectores" / "catalogo.py").read_text()
    assert "_EN_CARTERA_DE_CLIENTE" in inspect.getsource(cat.faltantes)
    # Una fecha, resuelta por índice: nunca un scan de la tenencia histórica.
    assert "max(fecha) FROM portafolio.tenencia" in src


def test_la_lista_de_faltantes_vive_en_UN_solo_lugar():
    """El detector cuenta y el arreglo edita **sobre la misma consulta**.

    Si cada uno tuviera la suya, el listado podría ofrecer un título que el aviso
    no cuenta —o al revés— y nadie lo notaría: las dos mitades seguirían siendo
    coherentes consigo mismas. Es exactamente el modo de falla de la REGLA #9.
    """
    from agente import arreglos
    from agente.detectores import catalogo as cat

    assert "det.faltantes(c)" in inspect.getsource(arreglos.CompletarFicha.preview)
    assert "det.faltantes(c)" in inspect.getsource(arreglos.CompletarFicha.aplicar)
    assert callable(cat.faltantes)


def test_completar_ficha_no_escribe_lo_que_manda_el_navegador():
    """**La puerta recalcula qué unidades puede tocar.**

    El front manda `unidad` y `valor`. Escribir eso directo dejaría que un
    pedido de afuera modificara CUALQUIER asset del catálogo, incluido uno que
    el detector no está mirando. Se recalcula el permitido y lo que no está
    adentro se saltea — y no se pisa: este arreglo COMPLETA, no corrige.
    """
    from agente import arreglos

    src = inspect.getsource(arreglos.CompletarFicha.aplicar)
    assert "permitidas = {" in src
    assert "if unidad not in permitidas" in src
    # Y escribe por la puerta única, con crear=False: una unidad que no existe
    # EXACTA levanta en vez de fabricar un asset fantasma trimmeado (REGLA #9).
    assert "set_campos(unidad" in src and "crear=False" in src


def test_completar_ficha_no_resuelve_el_hallazgo_de_una():
    """Completar 5 de 379 **no resuelve el problema** — pero completar el último
    SÍ, y el arreglo tiene que distinguirlo.

    `inmediato=False` deja el hallazgo `en_curso`: el detector lo va a seguir
    viendo con 374 y tiene que quedar abierto. Se cierra solo cuando la lista
    llega a cero, y entonces el cierre es POR ACCIÓN — lo único que habilita la
    reincidencia si el campo vuelve a quedar vacío.

    ⚠️ **Pero `inmediato` no habla de la escritura, habla del AVISO**, y cuando
    ya no queda nada la pantalla no puede decir «falta confirmar». User
    (2026-08-28), viendo cuatro títulos que acababa de cargar: *«¿confirmación
    de qué?? si yo ya lo apliqué»*.
    """
    from agente import arreglos

    src = _codigo(arreglos.CompletarFicha.aplicar)
    assert "inmediato=quedan <= 0" in src.replace(" ", "").replace(
        "inmediato=quedan<=0", "inmediato=quedan <= 0") or "quedan <= 0" in src, (
        "el arreglo tiene que distinguir «faltan más» de «era el último»")
    # Y sigue siendo el detector el que cierra: el arreglo NUNCA toca el estado.
    assert "estado" not in src and "RESUELTO" not in src


def test_cartera_se_propone_por_regla_master_o_1816():
    """`cartera.proponer` es PURA: la cadena regla-del-job → EJES del master →
    EJES de 1816, y la lista cerrada frena un valor que el catálogo todavía
    no usa (§0.ej)."""
    from agente import cartera

    pagare = {"unidad": "[*BIN031000050] *BIN031000050 Nro. 29805263 "
                        "Vto. 03/10/2026", "cartera": "", "ticker": ""}
    bono_master = {"unidad": "[9999] AL30 - BONO", "cartera": "", "ticker": "AL30"}
    bono_1816 = {"unidad": "[8888] XYZ26 - BONO", "cartera": "", "ticker": "XYZ26"}
    sin_nada = {"unidad": "[7777] NADA1 - BONO", "cartera": "", "ticker": "NADA1"}

    master = [{"ticker_corto": "AL30", "moneda_eje": "USD", "ajuste": "fija"}]
    # "Corporativos USD" existe en `curvas_ejes.EJES_1816` → Ejes(corporativo,
    # USD, fija) → HD.
    universo = {"instrumentos": {"XYZ26": {"_curva": "Corporativos USD"}}}
    usadas = ["FINANCIAMIENTO", "HD"]

    out = cartera.proponer([pagare, bono_master, bono_1816, sin_nada], master,
                           universo, usadas)
    por_unidad = {f["unidad"]: f for f in out}

    assert por_unidad[pagare["unidad"]]["propuesto"] == "FINANCIAMIENTO"
    assert por_unidad[pagare["unidad"]]["fuente"] == cartera.REGLA

    assert por_unidad[bono_master["unidad"]]["propuesto"] == "HD"
    assert por_unidad[bono_master["unidad"]]["fuente"] == cartera.CURVA

    assert por_unidad[bono_1816["unidad"]]["propuesto"] == "HD"
    assert por_unidad[bono_1816["unidad"]]["fuente"] == cartera.MIL816

    assert por_unidad[sin_nada["unidad"]]["propuesto"] == ""
    assert por_unidad[sin_nada["unidad"]]["fuente"] == ""

    # Lista cerrada: sin "HD" en `usadas`, el mismo bono no se escribe solo.
    out2 = cartera.proponer([bono_master], master, universo, ["FINANCIAMIENTO"])
    assert out2[0]["propuesto"] == ""
    assert out2[0]["fuente"] == ""
    assert "HD" in out2[0]["nota"]


def test_un_arreglo_que_pide_datos_lo_declara():
    """El front no puede adivinar cuáles llevan listado editable y cuáles son un
    botón: adivinar significa una lista de ids en el navegador que nadie mantiene
    igual a la de acá. Se declara con `pide_datos` y viaja en el catálogo."""
    from agente import arreglos

    piden = {a.id for a in arreglos.ARREGLOS.values() if a.pide_datos}
    # `alta_cedear` (§0.dl) pide datos por la razón contraria a `completar_ficha`:
    # el sistema SABE escribirlo todo, lo que no puede decidir es CUÁLES sumar.
    # `alta_on` (§0.dv) es el gemelo de `alta_cedear`, y por la misma razón.
    # `alta_contraparte` (§0.er): el sistema sabe escribir la fila entera y no
    # puede decidir CUÁLES son contraparte — y equivocarse saca una cuenta del
    # AuM. Además el NOMBRE no se deduce de ningún lado: lo pone una persona.
    assert piden == {"completar_ficha", "alta_cedear", "alta_on",
                     "alta_contraparte"}, (
        f"cambió qué arreglos piden datos: {piden}")
    assert all("pide_datos" in c for c in arreglos.catalogo())


def test_el_paquete_de_detectores_publica_todos_sus_modulos():
    """`agente/detectores/__init__.py` no puede quedarse corto.

    Al sumar `catalogo` quedó fuera del import y de `__all__`. No rompía nada
    —`from agente.detectores import catalogo` igual funciona—, y esa es
    justamente la razón por la que hay que congelarlo: un paquete cuyo índice no
    lista a todos sus miembros miente sobre qué hay adentro, y el próximo que lo
    lea va a creer que los detectores son tres.
    """
    from agente import detectores

    en_disco = {p.stem for p in (RAIZ / "agente" / "detectores").glob("*.py")
                if p.stem != "__init__"}
    assert set(detectores.__all__) == en_disco, (
        f"el paquete lista {sorted(detectores.__all__)} y en disco hay "
        f"{sorted(en_disco)}")


def test_si_el_agente_esta_vivo_lo_contesta_un_solo_lugar():
    """**El umbral SALE del ritmo declarado, no de un número puesto a mano.**

    `scripts/diag_agente` tenía su propio `hace < 180` y cantaba «FRÍO» todas
    las noches con el agente perfectamente vivo: fuera de rueda el ciclo es de
    300 s, así que a los 258 s el daemon está a mitad de camino. Es la REGLA #9
    en chiquito — dos mitades coherentes consigo mismas contestando distinto—, y
    el costo real no es la etiqueta: es que un tablero que dice «detenido»
    cuando todo anda enseña a ignorar el tablero.

    La respuesta vive en `motor.vivo()`, que usa tres ciclos de gracia sobre el
    `proximo_en_s` que el propio latido escribió.
    """
    import re

    from agente import motor

    src = inspect.getsource(motor.vivo)
    assert "hace < cada * 3" in src
    assert "coalesce(proximo_en_s" in src

    diag = (RAIZ / "scripts" / "diag_agente.py").read_text()
    assert "motor.vivo()" in diag, "el diag re-deriva si el agente está vivo"
    # Y no puede volver a inventarse un umbral: cualquier comparación contra el
    # tiempo del latido con un número escrito a mano es el bug de vuelta.
    cuerpo = "\n".join(l for l in diag.split("\n") if not l.lstrip().startswith("#"))
    assert not re.search(r"hace\s*[<>]=?\s*\d+", cuerpo), (
        "hay un umbral de latido escrito a mano en el diag")


def test_los_prefijos_de_agro_no_son_una_segunda_lista():
    """`_PREFIJOS_AGRO` tiene que decir lo mismo que el sistema ya usa.

    Los contratos de agro están declarados en
    `api/services/derivados_agro.DISPO_LABELS` (`TRI.ROS.P/DISPO`, etc.) y de
    ahí salen los tres prefijos. Escribirlos otra vez en `assets_autofill` es la
    REGLA #9 de manual: dos listas para la misma pregunta, cada una coherente
    consigo misma, y el día que se agregue un commodity a una y no a la otra
    nada falla — simplemente un futuro deja de recibir su cartera.
    """
    from api.services.derivados_agro import DISPO_LABELS
    from jobs.assets_autofill import _PREFIJOS_AGRO

    # `MAI.ROS.P/DISPO` → `MAI.`
    de_agro = {v.split(".", 1)[0] + "." for v in DISPO_LABELS.values()}
    assert set(_PREFIJOS_AGRO) == de_agro, (
        f"assets_autofill dice {sorted(_PREFIJOS_AGRO)} y derivados_agro "
        f"{sorted(de_agro)}")


def test_no_queda_ni_un_hilo_del_auto_control_viejo():
    """El sistema de `controles_datos` se dio de baja ENTERO (2026-08-27).

    Era **un segundo depósito de problemas**: `control_id` la habilidad,
    `item_key` el sujeto, `first_seen` la fecha de nacimiento, `resuelto_at` el
    cierre. El mismo modelo que `agente.hallazgos`, con otro reloj y otro
    criterio — y con un defecto que el agente no tiene: cuando un problema
    resuelto volvía **reseteaba `first_seen`** y se veía como nuevo, perdiendo
    justo el dato que en el agente vive en `reincidencias`.

    Se va el job, el service, el router, el cron, la tabla y la tab. Este test
    persigue los hilos sueltos: un import a un módulo borrado no falla hasta que
    alguien entra a esa pantalla, que es meses después.
    """
    import re

    vivos = []
    for f in RAIZ.rglob("*.py"):
        if "site-packages" in str(f) or f.name == "test_agente.py":
            continue
        txt = f.read_text()
        # La MENCIÓN en un comentario está bien —explica por qué algo es como
        # es—; lo que no puede quedar es el IMPORT o la QUERY.
        for patron in (r"from jobs\.controles_datos import",
                       r"from api\.services\.controles_sql import",
                       r"FROM manager\.controles_datos",
                       r"INSERT INTO manager\.controles_datos"):
            if re.search(patron, txt):
                vivos.append(f"{f.relative_to(RAIZ)}: {patron}")
    assert not vivos, f"quedan hilos del auto-control viejo: {vivos}"

    for borrado in ("jobs/controles_datos.py", "api/services/controles_sql.py",
                    "api/routers/manager/controles.py"):
        assert not (RAIZ / borrado).exists(), f"{borrado} sigue existiendo"

    # Y la tabla se DROPEA en el schema: dejarla huérfana es el resto que costó
    # encontrar con las 18 tablas del agente viejo.
    sql = (RAIZ / "sql" / "schema.sql").read_text()
    assert "DROP TABLE IF EXISTS manager.controles_datos;" in sql


def test_de_que_es_el_problema_nunca_se_escribe_a_mano():
    """El `nombre` de un hallazgo SALE DEL DATO, nunca de un literal.

    User (2026-08-28), mirando una tarjeta de `cron_desalineado`: *«en problema
    debería estar EL NOMBRE DEL CRON, no esa explicación que ya está en el
    NOMBRE y que encima también está en LA HABILIDAD — se repite 3 veces lo
    mismo»*.

    Y el texto repetido era el síntoma. Los tres campos contestan tres preguntas
    distintas —`habilidad` quién lo vio, `sujeto`/`nombre` DE QUÉ es, `problema`
    qué le pasa hoy— así que si las tres dicen lo mismo es porque **el sujeto no
    tiene el dato**: era la constante `"crontab"`, y el nombre del job había que
    redactarlo a mano.

    De ahí la regla, que es mecánica y no de estilo: **si tenés que escribir a
    mano de qué es el problema, el sujeto está mal elegido.** Un `nombre=` con
    string literal es exactamente esa firma.
    """
    import ast

    a_mano = []
    for f in sorted((RAIZ / "agente" / "detectores").glob("*.py")):
        for n in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Hallazgo"):
                continue
            for kw in n.keywords:
                if kw.arg == "nombre" and isinstance(kw.value, ast.Constant):
                    a_mano.append(f"{f.name}:{kw.value.lineno} nombre="
                                  f"{kw.value.value!r}")
    assert not a_mano, (
        "`nombre` escrito a mano: el sujeto no dice de qué es el problema — "
        + " · ".join(a_mano))


def test_cada_cron_es_un_hallazgo_con_identidad_propia():
    """El trío `habilidad+sujeto+regla` ES la identidad de un problema.

    ⚠️ **El nombre del job NO alcanza como sujeto**: medido sobre el
    `deploy/crontab.txt` real, hay nombres repetidos —cada motor aparece dos
    veces, `start` y `stop`— así que dos crons distintos compartirían el trío.
    Y no fallaría: `registro._ver` hace UPDATE sobre el hallazgo abierto, con lo
    cual el segundo **pisaría al primero en silencio** y el tablero mostraría
    uno solo.

    Por eso `crontab.sujeto()` mete el horario adentro. Este test lo verifica
    contra el archivo de verdad, no contra un ejemplo inventado: si mañana
    alguien agrega una línea que colisiona, se entera acá.
    """
    from agente import crontab

    lineas = crontab.del_repo()
    assert len(lineas) > 50, "no leí el crontab del repo"

    # El nombre pelado SÍ colisiona — es el motivo de que el sujeto lleve más.
    assert len({crontab.que_job(x) for x in lineas}) < len(lineas)
    # El sujeto, no: uno por línea.
    sujetos = {crontab.sujeto(x) for x in lineas}
    assert len(sujetos) == len(lineas), (
        f"{len(lineas) - len(sujetos)} cron(s) comparten sujeto: uno taparía "
        "al otro en el tablero")

    # Y el detector emite UNO POR CRON: sin esto, el sujeto correcto no sirve
    # de nada porque las N líneas seguirían viajando en un solo hallazgo.
    src = _codigo(sistema.cron_desalineado)
    assert "for linea in lineas" in src
    assert "crontab.sujeto(linea)" in src
    assert 'sujeto="crontab"' not in src


def test_la_tarjeta_de_un_job_contesta_lo_que_decide():
    """Las cuatro cosas que hay que saber para actuar, y ninguna estaba.

    User (2026-08-28), sobre la tarjeta de `tenencia (snapshot SQL)`: *«sigue
    siendo inentendible, no está claro qué es el error, a qué afecta… ¿qué
    política de reintentos tiene? ¿cómo sabe que no se volvió a ejecutar?»*.

    La tarjeta contaba el PROCESO (falló, hace 1h49m, tolera 36h). Lo que
    decide es otra cosa: **si el dato está**, **si algo lo va a reintentar
    solo**, **qué se rompe mientras tanto** y **si se puede arreglar desde ahí**.
    """
    src = inspect.getsource(sistema._que_hacer_pieza)

    # 1. El dato: los tres estados dan tres respuestas DISTINTAS.
    for e in ("esta", "falta", "no_pude"):
        assert f'dia["estado"] == "{e}"' in src, f"no distingue «{e}»"
    # 2. El reintento: sale de `proximo_intento`, no de la memoria del lector.
    assert "dia['proximo']" in src
    assert "no reintenta solo" in inspect.getsource(rehacer.proximo_intento), (
        "`run_job.sh` no reintenta ningún job — decirlo es lo que separa "
        "«esperá» de «hacelo vos»")
    # 3. Qué rompe: el campo existía en REHACIBLES y no llegaba a ninguna
    #    pantalla.
    assert "rompe" in inspect.getsource(sistema._renglon_del_dia)
    assert rehacer.REHACIBLES["portafolio_diario"]["rompe"]

    # 4. ⚠️ **NO se afirma «escritura» cuando el dato es una CORRIDA.** Para las
    #    piezas con `run_tipo` —casi todos los jobs— el timestamp sale de
    #    `manager.job_runs`: es cuándo corrió. La tarjeta decía «última
    #    escritura 08:00:04» de un job que había fallado al hacer login y no
    #    escribió una sola fila.
    card = inspect.getsource(sistema.motor_caido)
    i = card.index("detalle=")
    assert "p.get('tabla')" in card[i:i + 400], (
        "«escritura» solo se puede afirmar si la frescura sale de una tabla")

    # Y la frase de los MOTORES no se le dice a un job: es verdad para un motor
    # y mentira para las 35 piezas de tipo job, que es la mayoría.
    assert 'p.get("tipo") or ""' in src and '== "motor"' in src


def test_no_se_le_pregunta_al_snapshot_antes_de_que_arranque_el_motor():
    """El mercado abre ANTES que nuestro feed, y no es lo mismo.

    User (2026-08-28): *«una alerta de BONO SIN PRECIO no puede figurar antes de
    las 10:31 de los días hábiles, porque acá no es que no funciona el AGENT: el
    motor se prende antes por las dudas y queda sin precio un largo rato»*.

    Medido ese día: de 256 hallazgos abiertos, **225 eran de `bono_sin_precio`**
    (195 `precio_viejo` + 30 `sin_punta`) — el 88% del tablero, generado en una
    franja donde el sistema no puede tener precios.
    """
    from agente import reloj

    # La hora de arranque NO se inventa: es la del crontab de verdad.
    cron = (RAIZ / "deploy" / "crontab.txt").read_text()
    linea = next(x for x in cron.splitlines()
                 if "systemctl restart motor_rofex.service" in x
                 and not x.lstrip().startswith("#"))
    minuto, hora = linea.split()[0], linea.split()[1]
    assert (int(hora), int(minuto)) == reloj.FEED_ARRANCA_UTC, (
        f"el crontab arranca los motores {hora}:{minuto} UTC y `reloj` cree "
        f"{reloj.FEED_ARRANCA_UTC}: dos relojes para la misma pregunta")

    # 10:31 ART = 13:31 UTC es el número que puso el user.
    from datetime import UTC as _U
    from datetime import datetime as _dt
    def _a(h, m):
        return _dt(2026, 8, 28, h, m, tzinfo=_U)     # un viernes hábil
    assert not reloj.feed_caliente(_a(13, 0)), "abre la rueda, no el feed"
    assert not reloj.feed_caliente(_a(13, 25)), "el motor recién arrancó"
    assert reloj.feed_caliente(_a(13, 31)), "10:31 ART tiene que estar caliente"
    assert not reloj.feed_caliente(_a(2, 0)), "de madrugada no hay feed"

    # ⚠️ Y los que leen el snapshot levantan `SinDatos`, NO devuelven `[]`:
    # devolver vacío afirma «no hay nada» y el motor cierra por ausencia, así
    # que a las 13:00 se cerrarían los hallazgos de ayer y a las 13:31 volverían
    # a nacer, todos los días (invariante #1).
    guarda = inspect.getsource(mercado._feed_o_sindatos)
    assert "raise SinDatos" in guarda and "return []" not in guarda
    for fn in (mercado.bono_sin_precio, mercado.bono_sin_tasa,
               mercado.precio_moneda):
        assert "_feed_o_sindatos()" in inspect.getsource(fn), (
            f"{fn.__name__} lee el snapshot sin esperar al motor")


def test_no_se_exige_dar_de_alta_lo_que_nosotros_estamos_borrando():
    """Dos mitades nuestras peleándose, y ninguna falla.

    `jobs/cleanup_curvas` borra de `mercado.curvas` todo lo que vence a menos de
    2 días hábiles. `soberanos_faltantes` lo ve en 1816, no lo ve en el master y
    exige darlo de alta **con su cronograma** — un bono que amortiza el lunes.

    Es la primera fila que tuvo `reincidencias`: M31G6, alta el 24/08, borrado
    por el cleanup, de vuelta el 28/08 («aguantó 3.6 días»).
    """
    from core import curvas_sql

    habiles = {"2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"}
    hoy = "2026-08-28"
    # Vence el lunes: a 1 día hábil → lo estamos sacando.
    assert curvas_sql.sale_del_master("2026-08-31", habiles, hoy)
    # Y da igual si viene como `date` o como texto (1816 publica ISO).
    from datetime import date as _d
    assert curvas_sql.sale_del_master(_d(2026, 8, 31), habiles, hoy)
    # A 3 días hábiles todavía es un bono vivo.
    assert not curvas_sql.sale_del_master("2026-09-02", habiles, hoy)
    # Sin fecha o sin calendario NO se afirma que esté saliendo: ante la duda,
    # el instrumento sigue en el master.
    assert not curvas_sql.sale_del_master(None, habiles, hoy)
    assert not curvas_sql.sale_del_master("2026-08-31", set(), hoy)

    # ⚠️ **UNA sola regla, no dos iguales.** El job la consulta, no la
    # reimplementa: si volviera a tener su propio `< 2`, las dos se separarían
    # el día que alguien cambie una — y no fallaría nada.
    job = (RAIZ / "jobs" / "cleanup_curvas.py").read_text()
    assert "curvas_sql.sale_del_master" in job
    assert "def dias_habiles_entre" not in job, "el job se quedó con su copia"
    det = inspect.getsource(mercado.soberanos_faltantes)
    assert "curvas_sql.sale_del_master" in det
    assert "< 2" not in det and "DIAS_HABILES" not in det.split("sale_del_master")[0]


def test_el_dia_que_falta_no_depende_de_que_el_arbol_lo_note():
    """El botón no puede colgar de una señal que se apaga.

    El 28/08 el job del AuM falló a las 08:00, la tarjeta apareció, y para el
    mediodía `motor_caido` tenía **cero abiertos** — mientras el día seguía sin
    escribirse (medido en prod: 0 filas y 0 renglones en
    `portafolio.backfill_log` para el 27/08).

    El árbol de diagnóstico juzga la pieza por su último `run_status` y su
    frescura, y ese veredicto parpadea. **La pregunta que decide no parpadea**:
    el día está en la tabla o no está. Así que se hace igual.
    """
    src = inspect.getsource(sistema.motor_caido)
    i = src.index("for job, cfg in rehacer.rehacibles().items()")
    barrido = src[i:i + 1400]
    assert 'd["estado"] != "falta"' in barrido, (
        "solo el día que FALTA es trabajo: «ya está» y «no pude mirar» no")
    assert 'regla="job_sin_dato"' in barrido, "sin esa regla no hay botón"
    # Y NO duplica lo que el árbol ya cantó.
    assert "if job in ya_dichos" in barrido
    assert "ya_dichos.add(canon)" in src


def test_rehacer_solo_reescribe_lo_que_falta():
    """La pregunta del user (2026-08-28): *«si vamos a poner un botón tiene que
    validar que no haya realmente datos en la base, o que si ejecuta reemplace
    los datos por las dudas»*.

    Las dos cosas, y ya estaban en el job — lo que faltaba era decirlo:

      · `_write_date` hace DELETE + INSERT por (fecha, id_cuenta) en UNA
        transacción: **reemplaza**, no duplica ni acumula.
      · `_ya_hechas` saca de la lista las cuentas que ya figuran `ok`/`vacia`
        en `portafolio.backfill_log`, así que un re-run **solo reintenta las que
        fallaron**. Lo bueno no se toca. (`--force` es el que rehace todo.)

    Este test congela las dos: si el job dejara de saltear lo hecho, apretar el
    botón pasaría a reescribir 1.000 cuentas sanas para arreglar 3.
    """
    job = (RAIZ / "jobs" / "portafolio_backfill.py").read_text()

    i = job.index("def _write_date")
    cuerpo = job[i:i + 1200]
    assert "DELETE FROM portafolio.tenencia WHERE fecha = %s AND id_cuenta = ANY" in cuerpo
    assert "INSERT INTO portafolio.tenencia" in cuerpo
    assert cuerpo.index("DELETE") < cuerpo.index("INSERT INTO portafolio.tenencia")

    j = job.index("def _ya_hechas")
    assert "status IN ('ok', 'vacia')" in job[j:j + 400], (
        "sin este filtro, un re-run reescribiría también las cuentas sanas")
    assert "hechas = set() if force else _ya_hechas(iso)" in job

    # Y la guarda del agente sigue siendo la tabla, no el exit code.
    r = inspect.getsource(rehacer.rehacer)
    assert "antes = hay_dato(job, fecha)" in r and "if antes:" in r
    assert "despues = hay_dato(job, fecha)" in r


def test_lo_que_dijo_el_job_llega_a_la_pantalla():
    """User (2026-08-28), después de apretar REHACER: *«cuando lo relanzamos
    tampoco dice el motivo ni nada»*.

    Y no era que no existiera. `_correr` captura el stdout del job —que termina
    con una línea que contesta sola: `✓ 2026-08-27: OK=0 vacía=1040 TIMEOUT=0
    ERROR=0 · filas insertadas=0`— y las salidas de `rehacer()` la
    **descartaban**, y el arreglo la descartaba otra vez.

    El resultado en pantalla decía qué NO había sido el problema («no era que no
    se hubiera ejecutado») y nada de qué SÍ. Un diagnóstico por descarte deja al
    lector donde empezó.
    """
    r = inspect.getsource(rehacer.rehacer)
    # Las tres salidas que pueden pasar DESPUÉS de correr llevan la salida.
    assert r.count('"salida": r.get("salida"') >= 2, (
        "algún final de `rehacer()` sigue tirando lo que dijo el job")
    a = inspect.getsource(arreglos.RehacerJob.aplicar)
    assert 'r.get("salida")' in a, "el arreglo no la lleva a la pantalla"

    # Últimas LÍNEAS, no últimos bytes: cortar por bytes parte un renglón al
    # medio y lo que se lee arranca en la mitad de una palabra.
    # `_codigo()` y no `getsource`: el comentario de esa función NOMBRA el
    # `p.stdout` que dejó de usarse, y un grep sobre el archivo entero haría
    # fallar el test por haberlo explicado bien.
    d = _codigo(rehacer._lo_que_dijo)
    assert "splitlines()" in d and "[-3:]" in d

    # ⚠️⚠️ **Y SE LEE DEL LOG, NO DE `p.stdout`.** El primer intento de arreglar
    # esto leyó la salida del subprocess y llegaba VACÍA: `run_job.sh` redirige
    # todo con `>> "$LOG"`, así que capturar el stdout del wrapper no captura
    # nada. La explicación estaba en el archivo desde siempre.
    wrapper = (RAIZ / "deploy" / "run_job.sh").read_text()
    assert '>> "$LOG" 2>&1' in wrapper, (
        "si el wrapper dejara de redirigir, esto habría que revisarlo")
    assert "p.stdout" not in d and "seek(desde)" in d
    c = _codigo(rehacer._correr)
    # Desde el tamaño previo: leer «las últimas líneas» a secas traería las de
    # ayer el día que el job no llegue a escribir una sola.
    assert "log.stat().st_size" in c

    # SALTEAR NO ES CORRER, y las dos cosas salen 0 (`run_job.sh` escribe SKIP y
    # `exit 0` para no apilar instancias — el incidente de CPU del 2026-06-03).
    # Se ancla en la LÍNEA de código, no en la primera aparición de «SKIP»: el
    # encabezado del wrapper también la nombra al explicar qué loguea.
    i = wrapper.index('echo "[$(ts)] SKIP')
    assert "exit 0" in wrapper[i:i + 200]
    # (el texto exacto del f-string lo reescribe `ast.unparse`, así que se
    # verifica lo que no cambia: que mire el SKIP y que lo marque distinto)
    assert "SKIP" in c and "salteado" in c
    assert "salteado" in _codigo(rehacer.rehacer), (
        "`rehacer()` no distingue «no corrió» de «corrió y no escribió»")


def test_preguntar_y_no_traer_nada_no_es_un_exito():
    """La guarda del job existía y tenía un agujero del tamaño de `vacia`.

    Pedía `errores or timeouts`, pero `er = len(status_by) - ok - vac - to`: una
    cuenta que contesta «sin posiciones» no es ninguno de los dos. Un día en el
    que las ~1.040 cuentas vuelven vacías salía con **exit 0**, dejaba
    `manager.job_runs` en verde y no escribía una fila.

    Se descubrió apretando el botón del agente: corrió, salió 0, y
    `portafolio.tenencia` siguió sin el 2026-08-27. El agente no se lo creyó
    —verifica contra la tabla— pero el job seguía afirmando que había ido bien.
    """
    job = (RAIZ / "jobs" / "portafolio_backfill.py").read_text()

    # La condición no mira el TIPO de fallo, mira si se intentó y no se escribió.
    assert 'if tot["dias_pedidos"] and tot["cuentas_pedidas"] and not tot["filas"]:' in job
    assert 'tot["errores"] or tot["timeouts"]' not in job, (
        "volvió la guarda que no ve el caso «todo vacía»")
    assert 'tot["cuentas_pedidas"] += len(pend)' in job

    # Y el no-op legítimo (todo ya hecho) NO entra: ahí `cuentas_pedidas` es 0.
    # Lo que sí entra es no tener a quién preguntarle, que es otra cosa.
    i = job.index("if not universo:")
    assert "raise RuntimeError" in job[i:i + 300]


def test_un_numero_no_es_un_diagnostico():
    """User (2026-08-28), viendo el resultado del botón: *«salió con código
    -15»*.

    Un `returncode` **negativo no es un error del job**: es una señal que lo
    mató desde afuera, y eso se atiende en un lugar completamente distinto. Con
    el número pelado, el que lee se va a buscar el bug adentro de un job que
    funcionaba.
    """
    m = rehacer._por_que_murio
    assert "SIGTERM" in m(-15) and "NO falló el job" in m(-15)
    assert "api.service" in m(-15), (
        "SIGTERM acá tiene una causa concreta: el job corre como hijo del "
        "proceso de la API, así que un deploy se lo lleva puesto")
    assert "SIGKILL" in m(-9)
    assert "TIMEOUT" in m(124), "124 es `timeout(1)`, no un error del código"
    assert "adentro del job" in m(1)


def test_ver_que_haria_muestra_lo_que_se_va_a_ejecutar():
    """User (2026-08-28): *«estaría bueno que acá también se vean los
    parámetros que usa para la consulta»*.

    Dos mitades, y cada una la dice quien la sabe:

      · **el comando y la verificación** salen de `REHACIBLES` — la MISMA
        declaración que se ejecuta, así que no puede quedar desactualizada;
      · **qué le pide a la fuente** (endpoint, `desde`, timeouts, reintentos) lo
        imprime el JOB en su log, porque `POSICION_URL` y `_PARAMS_BASE` viven
        ahí. Si el agente los reconstruyera, el día que cambie uno la pantalla
        mostraría el viejo y nadie se enteraría (REGLA #9).
    """
    from unittest.mock import patch

    with patch.object(rehacer, "hay_dato", return_value=False), \
         patch.object(rehacer, "fecha_objetivo", return_value="2026-08-27"):
        p = arreglos.RehacerJob().preview("portafolio_diario",
                                          {"job": "portafolio_diario"})
    pasos = " ".join(x["detalle"] for x in p["pasos"])
    assert "run_job.sh portafolio_diario" in pasos
    assert "jobs.portafolio_backfill --diario" in pasos
    assert "portafolio.tenencia" in pasos and "2026-08-27" in pasos

    job = (RAIZ / "jobs" / "portafolio_backfill.py").read_text()
    i = job.index('print(f"       consulta: GET')
    linea = job[i:i + 600]
    for x in ("POSICION_URL", "_PARAMS_BASE", "desde", "TIMEOUT_DEFAULT", "RETRIES"):
        assert x in linea, f"la consulta impresa no dice «{x}»"
    # Y el agente NO la reimplementa.
    a = (RAIZ / "agente").rglob("*.py")
    for f in a:
        assert "posicionValuada" not in f.read_text(), (
            f"{f.name} reconstruye el endpoint del job en vez de leer su log")


def test_un_trabajo_de_ocho_minutos_no_es_un_request_http():
    """Por qué el botón moría de una forma distinta cada vez.

    Medido el 2026-08-28 corriendo el job a mano: **502 segundos** (1.885
    cuentas, 6.311 filas). Y el proxy de Next que sirve `/api/agente` declara
    `maxDuration = 30` **segundos**. O sea que este arreglo NUNCA podía
    contestar a tiempo: el `ESPERA_S = 30 * 60` del backend era una fantasía
    porque del otro lado nadie esperaba más de medio minuto.

    Los tres intentos murieron distinto —sin explicación, con SIGTERM, «corrió y
    no escribió»— y las tres veces se buscó el bug adentro del job, que
    funcionaba perfecto.

    Un trabajo así se LARGA y se confirma después. Es para lo que existe
    `Resultado(inmediato=False)`: deja el hallazgo en `en_curso` y, cuando el
    detector no lo ve más, `registro` lo cierra **POR ACCIÓN** — que es
    justamente lo que hay que anotar.
    """
    c = _codigo(rehacer._correr)
    assert "Popen" in c and "subprocess.run" not in c, (
        "esperar a que termine es lo que no puede funcionar acá")
    assert "start_new_session=True" in c, (
        "el job tiene que quedar en su propia sesión, no colgando del que lo largó")
    assert rehacer.ESPERA_CORTA_S <= 30, (
        "más que el maxDuration del proxy y el request se corta igual")
    assert not hasattr(rehacer, "ESPERA_S"), "volvió la espera de 30 minutos"

    # Cuando quedó lanzado NO se verifica: medir antes de que termine es
    # afirmar que no escribió algo que todavía está escribiendo.
    r = _codigo(rehacer.rehacer)
    i = r.index("lanzado")
    assert "hay_dato" not in r[i:i + 400]

    # Y el arreglo lo declara `inmediato=False`, que es lo que dispara el
    # `en_curso` → cierre POR ACCIÓN.
    a = _codigo(arreglos.RehacerJob.aplicar)
    j = a.index("lanzado")
    assert "inmediato=False" in a[j:j + 400]
    assert "Resultado(True" in a[j:j + 400], (
        "«se largó y todavía no terminó» NO es un fracaso")

    # Cuánto tarda es un DATO declarado, no una impresión: es lo que se le
    # muestra al que aprieta para que sepa cuánto esperar.
    assert rehacer.REHACIBLES["portafolio_diario"]["dura_aprox_s"] > 0


def test_el_ritmo_declarado_le_gana_al_medido():
    """Medir la mediana entre filas funciona para un motor y **falla feo para un
    job que corre una vez al día y appendea un lote**: adentro del lote las filas
    están separadas por milisegundos, la mediana dice «tiempo real», y el
    detector le empieza a exigir el ritmo de un feed live.

    Medido el 2026-08-28: **7 de los 10 hallazgos abiertos de `tabla_quieta`**
    eran eso. `research.mkt_1816_series` —un append diario de las 22:00 UTC—
    figuraba como «tiempo real, cada 2 s»; `mercado.canje_cierre`, post-cierre,
    como «cada 0 s». Ninguna estaba rota.

    La guarda que ya existía (`_dias_con_escritura`) pregunta *«¿escribió en
    muchos días distintos?»* y un job diario contesta que **sí**: escribe todos
    los días… una vez. Distingue «escribe seguido» de «escribió mucho una vez»,
    pero no **«escribe todo el día»** de **«escribe todos los días»**.

    Y el dato bueno estaba al lado: `deploy/crontab.txt`.
    """
    from datetime import UTC as _U
    from datetime import datetime as _dt

    from agente import tablas
    from core import crontab

    # 1. El hueco sale del cron de verdad y es el MÁS LARGO que admite — no un
    #    promedio sobre 24 h, que para un job con ventana da un número que no
    #    existe (`*/30 12-23` no corre cada hora: corre cada media hora de 12 a
    #    23 y después no corre en doce y media).
    assert crontab.hueco_maximo("0 22 * * 1-5") == 86400
    assert crontab.hueco_maximo("*/30 12-23 * * *") == int(12.5 * 3600)
    assert crontab.hueco_maximo("0 12,14,16,18,20,22 * * 1-5") == 14 * 3600
    # Lo que no se entiende NO se inventa: quien no sabe se abstiene y el que
    # pregunta se queda con lo que medía. Adivinar un ritmo declarado es peor
    # que no declararlo, porque tapa la señal con una cifra que parece dura.
    assert crontab.hueco_maximo("raro") is None
    assert crontab.hueco_maximo("") is None

    # 2. Y le gana al medido: el caso real, con sus datos reales.
    ahora = _dt(2026, 8, 28, 15, 5, tzinfo=_U)          # viernes 12:05 ART
    perfil = {"cadencia": "tiempo_real", "intervalo_p50_s": 2,
              "ultimo_dato": _dt(2026, 8, 27, 22, 0, 22, tzinfo=_U)}
    assert tablas.frescura(perfil, ahora=ahora)["estado"] == "atrasada"
    ok = tablas.frescura(perfil, ahora=ahora, declarado={
        "hueco_s": 86400, "solo_habiles": True, "job": "jobs.mercado_1816_series"})
    assert ok["estado"] == "ok" and ok["declarado"] is True

    # 3. ⚠️ Y una tabla live DE VERDAD —sin cron que la declare— tiene que
    #    seguir gritando: esto no es subir una tolerancia, que taparía justo las
    #    que importan. Es dejar de adivinar lo que está escrito.
    live = {"cadencia": "tiempo_real", "intervalo_p50_s": 2,
            "ultimo_dato": _dt(2026, 8, 28, 12, 21, tzinfo=_U)}
    assert tablas.frescura(live, ahora=ahora)["estado"] == "atrasada"

    # 4. Las dos mitades se juntan por una sola puerta: quién escribe la tabla
    #    (`core.escribe`) y cada cuánto corre ese job (`core.crontab`). Ninguna
    #    es nueva — el agente ya usaba las dos por separado y nunca se
    #    preguntaron una a la otra.
    d = _codigo(tablas.declarados)
    assert "escribe.que_relanzar" in d and "crontab.ritmo_declarado" in d


def test_ninguna_pieza_de_diagnostico_se_queda_sin_fuente():
    """Una `Pieza` sin `tabla` ni `run_tipo` ni `coll` **no tiene de dónde leer**.

    `diagnostico._frescura` devuelve `(None, None)` → estado `sin_datos` → y el
    AV AGENT la canta como *«nunca dejó un rastro: puede no haber corrido
    jamás»* con el motor perfectamente vivo. Le pasó a `motor_cedears`: su
    comentario decía que quedaba «informativo hasta el follow-up», el follow-up
    se hizo (todas las demás leen de Postgres) y **a esa nunca le pusieron la
    tabla**. El user lo levantó desde la pantalla el 2026-08-28.

    No es un olvido que se pueda repetir sin que se note: una Pieza muda no
    falla, afirma que algo nunca corrió.
    """
    import ast as _ast

    src = (RAIZ / "api" / "services" / "diagnostico_registry.py").read_text()
    mudas = []
    for n in _ast.walk(_ast.parse(src)):
        if not (isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "Pieza"):
            continue
        if {k.arg for k in n.keywords} & {"tabla", "run_tipo", "coll"}:
            continue
        mudas.append(n.args[2].value if len(n.args) > 2 else f"línea {n.lineno}")
    assert not mudas, (
        "estas piezas no tienen de dónde leer la frescura y el agente las va a "
        f"cantar como «nunca corrió»: {mudas}")


def test_una_fecha_de_negocio_no_es_un_timestamp_de_escritura():
    """`COLS_FECHA` mezcla dos cosas que no son comparables.

    `updated_at`/`ingestado_en` dicen **cuándo se escribió la fila**;
    `fecha`/`ts_cierre` dicen **de qué día son los datos**. Medir el atraso
    contra la segunda suma hasta 24 h que no existen: el cierre del 27 se
    escribe el 27 a las 20:35, pero su `fecha` dice `2026-08-27 00:00`.

    Medido el 2026-08-28 a las 17:06 UTC: **siete tablas de cierre** salieron
    todas juntas con «hace 1,7 días» **teniendo el dato correcto** — el cierre
    del 28 todavía no había pasado.

    Y no hace falta declarar qué tabla es de negocio: **el dato se delata solo**.
    Un valor a medianoche EXACTA no es un instante de escritura —ningún job
    escribe a las 00:00:00.000000— es un día.
    """
    from datetime import UTC as _U
    from datetime import datetime as _dt

    from agente import tablas

    dec = {"hueco_s": 86400, "solo_habiles": True, "job": "jobs.snapshot_cierre"}
    cierre = {"cadencia": "diaria", "intervalo_p50_s": 86400,
              "ultimo_dato": _dt(2026, 8, 27, tzinfo=_U)}      # medianoche exacta

    # El viernes a la tarde, con el cierre del 27 cargado: está AL DÍA.
    assert tablas.frescura(cierre, ahora=_dt(2026, 8, 28, 17, 6, tzinfo=_U),
                           declarado=dec)["estado"] == "ok"
    # ⚠️ Y sigue detectando: si pasa el fin de semana sin escribirse, grita.
    assert tablas.frescura(cierre, ahora=_dt(2026, 8, 31, 15, 0, tzinfo=_U),
                           declarado=dec)["estado"] == "atrasada"

    # Un TIMESTAMP de verdad no se toca: son las 16:35:09, no medianoche.
    vivo = {"cadencia": "tiempo_real", "intervalo_p50_s": 1,
            "ultimo_dato": _dt(2026, 8, 28, 16, 35, 9, 480627, tzinfo=_U)}
    assert tablas.frescura(vivo, ahora=_dt(2026, 8, 28, 17, 6, tzinfo=_U)
                           )["estado"] == "atrasada"


def test_las_dos_formas_de_la_misma_fecha_no_se_mezclan():
    """`jobs/interbanking_sync` reventó la corrida de las 13:00 con
    `TypeError: unsupported operand type(s) for -: 'str' and 'str'`.

    Adentro de `run()` conviven las MISMAS dos fechas en dos formas —`desde`
    y `hasta` como `date`, `d1` y `d2` en ISO para la API y para el log— y el
    bloque nuevo de sellado tomó las de texto: `d2 - d1`. Se llevó puesta la
    corrida entera **después** de haber traído bien todas las cuentas.

    Es el mismo modo de falla de siempre —dos representaciones del mismo dato
    sin árbitro— pero acá SÍ falla ruidoso, y por eso se encontró en horas y no
    en días.
    """
    import ast as _ast

    src = (RAIZ / "jobs" / "interbanking_sync.py").read_text()

    # El rango es una función con firma tipada: si le pasan los strings, falla
    # en su propia línea y no a mitad de una corrida buena.
    mod = _ast.parse(src)
    fn = next(n for n in mod.body if getattr(n, "name", "") == "_dias_entre")
    ns: dict = {"date": date, "timedelta": timedelta}
    exec(compile(_ast.Module([fn], []), "<t>", "exec"), ns)
    assert ns["_dias_entre"](date(2026, 8, 27), date(2026, 8, 28)) == [
        date(2026, 8, 27), date(2026, 8, 28)]
    with pytest.raises(TypeError):
        ns["_dias_entre"]("2026-08-27", "2026-08-28")

    # Y el bloque de sellado usa la función, no las variables de texto.
    # `_codigo()` y no el archivo crudo: el comentario que dejamos ahí NOMBRA el
    # `d2 - d1` que se sacó, y grepear el texto entero haría fallar el test por
    # haberlo explicado bien. (Tercera vez que muerde esta trampa.)
    codigo = _codigo(src)
    assert "_dias_entre(desde, hasta)" in codigo
    assert "d2 - d1" not in codigo


def test_una_tabla_de_ocasiones_no_tiene_cadencia():
    """User (2026-08-28), sobre `ordenes_audit` y `ordenes_live`: *«que sean en
    tiempo real no significa que todo el tiempo tenga que haber datos nuevos. Si
    no hay órdenes en todo el día va a estar sin escribir y eso no implica que
    se rompió algo»*.

    La heurística de `core/escribe.py` (jobs/engines → reloj) acierta en casi
    todo y falla en una clase concreta: **la carpeta no dice si el dato es
    periódico**. Un motor de precios escribe siempre porque siempre hay precios;
    un motor de ÓRDENES escribe cuando alguien opera. Los dos viven en
    `engines/`, y eso no se puede derivar del código: la diferencia está en si el
    sistema CAUSA el dato o solo lo registra.
    """
    from core import escribe

    for t in ("operaciones.ordenes_live", "operaciones.ordenes_audit",
              "mercado.camara_cereales_audit"):
        assert escribe.la_dispara(t) == escribe.EVENTO, f"{t} se sigue exigiendo"
        assert escribe.por_ocasion(t), "cada entrada declara POR QUÉ"

    # Y `tabla_quieta` ya saltea los eventos — el mecanismo existía, lo que
    # faltaba era poder decir que estas lo son.
    det = _codigo(sistema.tabla_quieta)
    assert "escribe.EVENTO" in det and "continue" in det

    # ⚠️ Lo que NO es de ocasión se sigue exigiendo igual: esto no es una
    # tolerancia más alta, es una pregunta distinta.
    assert escribe.la_dispara("portafolio.tenencia") == escribe.RELOJ
    assert not escribe.por_ocasion("portafolio.tenencia")


def test_el_huso_de_la_pieza_se_aplica_en_el_sql_y_no_despues():
    """Un atraso CLAVADO en exactamente 3 h no es un motor caído.

    `mercado.timesales.ts` es naive en hora argentina (`valores.py` lo guarda
    restando 3 h a propósito) y su Pieza declara `assume="AR"`. Pero ese
    `assume` **nunca llegaba a aplicarse**: `(ts)::timestamptz` con la sesión en
    UTC etiqueta el naive como UTC, y `_parse_ts` recibe un valor YA AWARE —
    `asegurar_aware` no toca lo que tiene tzinfo.

    Medido el 2026-08-28: `motor_rofex (trades)` con `3h 0m` a las 14:11, a las
    14:21 y a las 14:23. Un motor caído acumula atraso; éste no acumulaba nada
    porque estaba escribiendo **en ese momento**. 3 h es exactamente UTC−ART.
    """
    src = (RAIZ / "api" / "services" / "diagnostico.py").read_text()
    i = src.index("if p.tabla:")
    bloque = src[i:i + 2200]
    assert "AT TIME ZONE" in bloque, (
        "el cast tiene que hacerse EN la zona que la Pieza declara")
    assert 'p.assume == "AR"' in bloque

    # Y la Pieza sigue declarando el huso: si alguien lo saca, el cast vuelve a
    # ser UTC y el atraso fantasma vuelve sin que nada falle.
    reg = (RAIZ / "api" / "services" / "diagnostico_registry.py").read_text()
    j = reg.index('tabla="timesales"')
    assert 'assume="AR"' in reg[j:j + 120]


def test_el_estado_del_hallazgo_solo_se_muestra_si_habla_de_esa_linea():
    """User (2026-08-28), viendo nueve títulos recién completados —cada uno con
    su ✔ y su `emisor: — → OTROS`— y al lado «el aviso sigue abierto»: *«¿por
    qué no pone CONFIRMADO? ¿qué tienen que ver los demás?»*.

    Y no tienen nada que ver. Cuando la acción es sobre UN título y el hallazgo
    es de TODO el campo, el estado del hallazgo habla de **los otros**, y en ese
    renglón se lee como una duda sobre la escritura que la línea ya afirma.

    Se distingue con el dato y no con una lista: si el sujeto de la acción
    difiere del sujeto del hallazgo, la acción es de un item y el hallazgo de un
    grupo. Y lo decide el BACKEND — ninguna pantalla deriva nada (invariante 11).
    """
    from agente import vista

    src = _codigo(vista.historial)
    assert "h.sujeto IS DISTINCT FROM a.sujeto" in src
    assert "THEN NULL" in src


def test_lo_de_derivados_no_tiene_emisor_y_eso_se_declara():
    """Regla del user (2026-08-28): *«derivados = otros»*.

    Un futuro o una opción no tiene emisor en el sentido de un bono —no hay una
    empresa que se haya endeudado—, así que el campo se completa con el balde
    que ya usa la mesa y deja de figurar como ficha incompleta.

    ⚠️ Va en `jobs/assets_autofill` y no en el agente: **ahí viven las reglas de
    lo que se puede derivar**, con el invariante que las hace seguras —solo
    completa vacíos, y lo cargado a mano que difiera se REPORTA como conflicto
    en vez de pisarse—.
    """
    import ast as _ast

    src = (RAIZ / "jobs" / "assets_autofill.py").read_text()
    mod = _ast.parse(src)
    fns = {n.name: n for n in mod.body if hasattr(n, "name")}
    import datetime as _dt
    import re as _re

    # `_regla_emisor_financiamiento` cae en `_regla_financiamiento` cuando la
    # cartera todavía está vacía —el mecanismo que hace que un pagaré nuevo no
    # espere a la corrida de mañana—, así que necesita el regex y `date`.
    #
    # ⚠️ El regex se EJECUTA desde el AST del job, no se copia acá. Una segunda
    # escritura de esa firma sería la REGLA #9 adentro del test que la verifica:
    # el día que el job la cambie, el test seguiría en verde contra la vieja.
    ns: dict = {"_norm": lambda x: (x or "").strip(), "re": _re, "date": _dt.date,
                "CARTERA_DERIVADOS": "DERIVADOS", "EMISOR_OTROS": "OTROS",
                "CARTERA_FINANCIAMIENTO": "FINANCIAMIENTO",
                "_PREFIJOS_AGRO": ("MAI.", "SOJ.", "TRI."),
                "_regla_ticker": lambda r: {}}
    for asig in mod.body:
        if (isinstance(asig, _ast.Assign)
                and getattr(asig.targets[0], "id", "") == "_RE_FINANCIAMIENTO"):
            exec(compile(_ast.Module([asig], []), "<t>", "exec"), ns)
    assert "_RE_FINANCIAMIENTO" in ns, "cambió el nombre del regex en el job"
    for n in ("_regla_derivados_otc", "_regla_emisor_derivados",
              "_regla_financiamiento", "_regla_emisor_financiamiento"):
        exec(compile(_ast.Module([fns[n]], []), "<t>", "exec"), ns)
    regla = ns["_regla_emisor_derivados"]

    assert regla({"unidad": "[GFGC8000OC]", "cartera": "DERIVADOS"}) == {"emisor": "OTROS"}
    # Y también para el que TODAVÍA no tiene cartera pero la va a recibir en
    # esta misma pasada: si no, un OTC nuevo esperaría a la corrida de mañana.
    assert regla({"unidad": "[OTC - MAI.ROS/JUL26]", "cartera": ""}) == {"emisor": "OTROS"}
    # Nada más se toca.
    assert regla({"unidad": "[8295] BHP", "cartera": "RENTA VARIABLE"}) == {}
    assert regla({"unidad": "[AL30]", "cartera": "HD"}) == {}

    # ── FINANCIAMIENTO usa el MISMO balde por OTRO motivo (2026-09-05) ──────
    #
    # User: *«si en cartera es FINANCIAMIENTO en emisor va OTROS, no se negocia
    # esto»*. Un pagaré SÍ tiene librador —a diferencia de un futuro—, así que
    # esto no es una propiedad del instrumento sino una decisión de la mesa: el
    # negocio se sigue por papel y vencimiento, no por quién lo libró.
    #
    # Son DOS reglas y no una con dos carteras, para que mañana una pueda
    # cambiar sin tocar la otra. Lo que sí es uno solo es el VALOR: un
    # `EMISOR_FINANCIAMIENTO = "OTROS"` al lado sería la REGLA #9 en dos
    # constantes.
    fin = ns["_regla_emisor_financiamiento"]
    unidad = "[#UBI260170001] #UBI260170001 Nro. 163214 Vto. 28/01/2027"
    assert fin({"unidad": unidad, "cartera": "FINANCIAMIENTO"}) == {"emisor": "OTROS"}
    # Y el pagaré que entra HOY, antes de que nadie le ponga la cartera: si no,
    # esperaría a la corrida de mañana para recibir su emisor.
    assert fin({"unidad": unidad, "cartera": ""}) == {"emisor": "OTROS"}
    # Nada más se toca — y en particular NO se toca lo de derivados ni al revés.
    assert fin({"unidad": "[8295] BHP", "cartera": "RENTA VARIABLE"}) == {}
    assert fin({"unidad": "[GFGC8000OC]", "cartera": "DERIVADOS"}) == {}
    assert regla({"unidad": unidad, "cartera": "FINANCIAMIENTO"}) == {}
    assert 'Regla("emisor_financiamiento"' in src

    # La regla está declarada en el catálogo y `emisor` es un campo escribible:
    # sin lo segundo el job levanta `ValueError` en vez de escribir en silencio.
    assert 'Regla("emisor_derivados"' in src
    i = src.index("_ESCRIBIBLES = frozenset(")
    assert '"emisor"' in src[i:i + 400]
    # Y el motor solo completa vacíos — lo cargado a mano se reporta, no se pisa.
    j = src.index("if _vacio(actual):")
    assert "conflictos.append" in src[j:j + 400]


def test_la_tabla_que_falta_se_avisa_siempre_y_no_un_dia():
    """User (2026-08-28): *«db_peso tiene que servir para detectar cuando se
    borra una tabla… no tiene que dejar de avisarme cuando se borra una tabla y
    cuál»*.

    Y dejaba de avisar al día. `de_hace(24)` mira la foto de hace 24 h, y esa
    referencia **se mueve**: una tabla borrada el martes 19:00 se ve el
    miércoles al mediodía (la foto del martes al mediodía la tenía) y deja de
    verse el miércoles a la noche, porque a esa altura «hace 24 h» ya es un
    mundo sin la tabla. Después, silencio para siempre.

    La otra pregunta —«¿está la que el sistema dice que tiene que estar?»— se
    contesta SIEMPRE, y la respuesta ya estaba escrita en `sql/schema.sql`.
    """
    from agente import peso

    # Lo dropeado a propósito NO es un faltante: sin esta resta el agente
    # pediría para siempre las tablas que decidimos borrar.
    d = peso.declaradas()
    assert len(d) > 200, "no leí el schema"
    assert "manager.controles_datos" not in d, "la dimos de baja a propósito"
    assert "agente.hallazgos" in d

    # Las DOS preguntas conviven: la de la foto atrapa lo no declarado, la del
    # schema no vence nunca.
    src = _codigo(sistema.db_peso)
    assert "peso.declaradas()" in src
    assert "ayer.items()" in src, "sigue mirando la foto para lo no declarado"
    # Y cada aviso dice de dónde salió: uno dura para siempre y el otro un día.
    assert "schema" in src and "foto_24h" in src


def test_el_peso_total_avisa_en_las_dos_franjas_del_dia():
    """Pedido del user (2026-08-28): *«que sea fijo dos veces por día, a las 11
    y a las 16, que avise el peso total de la base»* — hora de la mesa.

    El dato se venía midiendo y guardando cada hora desde siempre; lo que
    faltaba era **dónde verlo**. El módulo decía que el peso «viaja aparte» y
    ese aparte nunca se construyó.
    """
    from datetime import UTC as _U
    from datetime import datetime as _dt

    from agente import peso

    assert peso.FRANJAS_ART == (11, 16)
    # ART = UTC−3.
    def f(h_art):
        return peso.franja_de_hoy(_dt(2026, 8, 28, h_art + 3, 0, tzinfo=_U))
    assert f(8) == "", "antes de las 11 no hay nada que informar"
    assert f(11) == "11:00"
    assert f(13) == "11:00", "sigue vigente la de las 11 hasta que llegue la de las 16"
    assert f(16) == "16:00"
    assert f(19) == "16:00"

    # ⚠️ La franja es el SUJETO: así el de las 16 no pisa al de las 11, y cada
    # uno nace y muere como un aviso propio. Y se re-emite en cada pasada
    # mientras está vigente — `registro` cierra por ausencia lo que un detector
    # deja de ver, así que emitirlo solo a las 11:00 en punto lo borraría a las
    # 12:00.
    src = _codigo(sistema._peso_total)
    # La franja va en la REGLA y el sujeto es lo que se mide: así el aviso de
    # las 16 NACE en vez de pisar al de las 11, y no hay que escribir a mano de
    # qué es el problema (que es la firma de un sujeto mal elegido).
    assert "peso_total_" in src and "la base" in src

    # ⚠️ **LA SERIE TIENE QUE LLEGAR A LO QUE SE COMPARA.** La primera versión
    # pedía 7 días sobre una serie que se purga a los 3 (`DIAS = 3`): la
    # referencia no existía NUNCA y el aviso decía «sin referencia de hace 7
    # días todavía» para siempre. Un mensaje que no cambia nunca no informa: se
    # lee como un error del sistema.
    assert peso.DIAS * 24 > peso.COMPARAR_CONTRA_H, (
        f"se guardan {peso.DIAS} días y se compara contra "
        f"{peso.COMPARAR_CONTRA_H / 24:.0f}: la referencia no va a existir nunca")

    # Y mientras la serie no llegue, se compara contra la foto MÁS VIEJA que
    # haya diciendo de cuándo es — un «todavía no» no se distingue de un error,
    # y encima no sirve para nada.
    assert "referencia" in src
    ref = _codigo(peso.referencia)
    assert "ORDER BY at ASC" in ref and "LIMIT 1" in ref


def test_un_informe_periodico_no_es_cronico():
    """Un problema que pasa TODOS los días no es un incidente: es una
    configuración mal puesta —el peso de la base dos veces por día, la tabla
    contra 1816 cada dos horas— y contarlo como episodios lo volvía «crónico»
    a los tres días. «Pasa siempre» es su definición, no un patrón a corregir
    (`AGENT.md` §0.eg)."""
    from agente import vista

    assert catalogo.HABILIDADES["db_peso"].naturaleza == {
        "peso_total_11": tipos.INFORME, "peso_total_16": tipos.INFORME}
    assert catalogo.HABILIDADES["tasa_vs_1816"].naturaleza == {"tabla": tipos.INFORME}
    assert ("tasa_vs_1816", "tabla") in catalogo.informes()

    assert "regla='tabla'" in _codigo(mercado.tasa_vs_1816)
    assert "peso_total_" in _codigo(sistema._peso_total)

    # La exclusión vive en la QUERY (tres listas paralelas, nunca un string
    # pegado de habilidad+regla — hay un test que lo prohíbe) y no en Python.
    src_cronicos = _codigo(vista.cronicos)
    assert "unnest" in src_cronicos and "sin_episodios(" in src_cronicos
    # ⚠️ El flag de la fila se llama `sin_episodios` y no `informe`: desde
    # §0.ep también lo llevan los RECURRENTES, y un campo que dice «informe»
    # sobre una fila que no lo es es la mentira barata que este repo persigue.
    assert "sin_episodios" in _codigo(vista._con_historial)


def test_la_ficha_de_un_titulo_nuevo_es_trabajo_recurrente_no_cronico():
    """User (2026-09-08): *«clase activo, emisor, cartera no entran en lo de
    crónico: son justamente el tipo de cosas que van a pasar constantemente
    porque siempre aparecen nuevos activos. Lo de crónico va para cosas de
    sistema, monitor, salud»*. Las cuatro reglas de `ficha_incompleta` se
    declaran recurrentes y salen del conteo junto con los informes."""
    h = catalogo.HABILIDADES["ficha_incompleta"]
    assert set(h.naturaleza) == set(h.arreglos), (
        "toda regla de la ficha se declara: si aparece una nueva, clasificarla")
    assert set(h.naturaleza.values()) == {tipos.RECURRENTE}
    for r in h.naturaleza:
        assert ("ficha_incompleta", r) in catalogo.sin_episodios()
    # Lo del SISTEMA sí cuenta: un job que se cae tres veces es un patrón.
    assert catalogo.HABILIDADES["motor_caido"].naturaleza == {
        "job_sin_dato": tipos.INCIDENTE}


def test_un_sujeto_que_es_una_FAMILIA_no_puede_ser_cronico():
    """**LA DEFINICIÓN QUE FALTABA** (§0.ep). El user, mirando la tarjeta
    `ONs HARD DÓLAR · on_faltante · ⚠ crónico · 3× en 30d`: *«este aviso NO
    tiene que pasar por lo de crónico. Está bien que aparezca constantemente
    esto, pero no es algo crónico, ya que no son errores»*.

    «Crónico» cuenta cuántas veces NACIÓ el problema sobre EL MISMO sujeto, y
    ese número sólo significa algo si el sujeto es una COSA FIJA. Los tres
    sujetos de abajo son constantes de módulo —son FAMILIAS— y su fila se
    llena, se vacía y vuelve a nacer cada vez que 1816 o Primary publican algo
    nuevo: sus episodios miden cuánto creció el mercado.

    El test los nombra por la constante y no por el string, así que renombrar
    la familia no lo evade.
    """
    from agente import alta_cedear

    familias = (
        ("tasa_vs_1816", "tabla", mercado.FAMILIA_CORP_HD),
        ("on_faltante", "no_estan_en_curvas", mercado.FAMILIA_ON),
        ("cedear_faltante", "no_esta_en_master", alta_cedear.FAMILIA),
    )
    sin_ep = set(catalogo.sin_episodios())
    for hab, regla, familia in familias:
        assert familia, f"«{hab}» perdió su constante de familia"
        assert catalogo.HABILIDADES[hab].naturaleza.get(regla) in tipos.SIN_EPISODIOS, (
            f"«{hab}/{regla}» tiene por sujeto la familia «{familia}»: no puede "
            f"contar episodios. Declarar {tipos.RECURRENTE!r} o {tipos.INFORME!r}")
        assert (hab, regla) in sin_ep
        # Y la fila de familia sale del detector con esa constante: si alguien
        # la cambia por un ticker, esta línea lo dice.
        assert f"regla='{regla}'" in _codigo(catalogo.HABILIDADES[hab].correr)

    # ⚠️ La hermana de CADA una es por ÍTEM y SÍ cuenta episodios. Silenciar la
    # habilidad entera sería «arreglarlo» apagando justo lo que hay que ver:
    # el MISMO ticker apareciendo tres veces es un patrón.
    assert catalogo.HABILIDADES["on_faltante"].naturaleza["no_esta_en_curvas"] == (
        tipos.INCIDENTE)
    assert catalogo.HABILIDADES["cedear_faltante"].naturaleza["no_cotiza_en_primary"] == (
        tipos.INCIDENTE)


def test_toda_regla_con_boton_declara_su_naturaleza():
    """**LO QUE HACE QUE NO SE VUELVA A OLVIDAR** (§0.ep).

    `informes` y `recurrentes` eran dos listas OPCIONALES, y por eso el olvido
    pasó dos veces: `on_faltante` y `cedear_faltante` describían su fila de
    familia como «una oferta de catálogo, no un problema» **en un comentario**,
    y la pantalla igual las marcaba crónicas. Un comentario no es una
    declaración.

    Ahora es UNA sola declaración por regla, con vocabulario cerrado, y
    `Habilidad.__post_init__` no deja construir una habilidad cuya regla tenga
    arreglo y no diga qué es — así falla al IMPORTAR, no cuando alguien mire la
    pantalla dentro de un mes. Este test cubre el otro lado: que el catálogo de
    verdad esté completo, y que nadie declare una regla que no existe.
    """
    for h in catalogo.HABILIDADES.values():
        faltan = set(h.arreglos) - set(h.naturaleza)
        assert not faltan, (
            f"«{h.nombre}»: {sorted(faltan)} tiene(n) botón y no declara(n) "
            f"naturaleza")
        for regla, nat in h.naturaleza.items():
            assert nat in tipos.NATURALEZAS, f"«{h.nombre}/{regla}»: {nat!r}"

    # Y hay UNA sola lectura de `naturaleza` (REGLA #9): las tres listas que
    # usan las pantallas salen de la misma función.
    for fn in (catalogo.informes, catalogo.recurrentes, catalogo.sin_episodios):
        assert "_de_naturaleza(" in _codigo(fn), (
            f"«{fn.__name__}» tiene que leer por `_de_naturaleza`: dos lecturas "
            "de la naturaleza son dos definiciones de crónico")


# ── contraparte_faltante (§0.er) ───────────────────────────────────────────

def _armar_contrapartes(monkeypatch, filas, sin_senal=0):
    """Le pone al detector una base de mentira. Devuelve las filas tal cual las
    daría la query: (id_cuenta, denominacion, tipo_cliente)."""
    from unittest.mock import MagicMock

    cur = MagicMock()
    cur.fetchall.return_value = filas
    cur.fetchone.return_value = (sin_senal,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    import core.postgres
    monkeypatch.setattr(core.postgres, "get_pool", lambda: pool)
    return pool


def test_contraparte_faltante_solo_mira_los_tipos_INSTITUCIONALES(monkeypatch):
    """**El recorte es el diseño, y está MEDIDO** (§0.er, `diag_contrapartes` el
    2026-09-08): sin él son 615 cuentas —332 Empresa, 176 PyMES, 226
    cooperativas—, que no es una lista de pendientes, es la cartera de clientes.
    Una lista que no puede llegar a cero no la mira nadie.

    Lo que sí entra es el `tipo_cliente` institucional, y para el FCI la señal
    se midió contra las 394 que la mesa clasificó a mano: 295 de 295.
    """
    _armar_contrapartes(monkeypatch, [
        ("805", "[805] SUPERFONDO FCI RENTA", "Fondo Común de Inversión"),
        ("806", "[806] LA SEGUNDA SEGUROS", "Compañía de seguros"),
    ], sin_senal=562)
    h = datos.contraparte_faltante({})
    assert len(h) == 1, "UNA fila de familia, no una por cuenta (§0.cz)"
    assert h[0].sujeto == datos.FAMILIA_CONTRAPARTES
    assert h[0].regla == "sin_contraparte"

    # El SQL filtra por los tipos declarados: si alguien agrega «Empresa» a la
    # tupla, la lista pasa de 53 a 385 y este test lo dice.
    assert "Empresa" not in datos.TIPOS_INSTITUCIONALES
    assert "PyMES" not in datos.TIPOS_INSTITUCIONALES
    assert "Fondo Común de Inversión" in datos.TIPOS_INSTITUCIONALES

    # ⚠️ El número de las que quedan AFUERA ya NO va en el texto (§0.es: el user
    # lo pidió corto) pero NO se perdió: vive en la evidencia, que es donde se
    # mira cuando se quiere mirar. Sacarlo del todo dejaría «53» leyéndose como
    # «hay 53 sin decidir», y son 615.
    assert h[0].evidencia["sin_senal"] == 562
    assert len(h[0].problema) < 90, "el aviso volvió a ser un choclo"
    assert [f["cuenta"] for f in h[0].evidencia["_items"]] == ["805", "806"]


def test_contraparte_faltante_no_inventa_el_segmento_y_dice_de_donde_sale(monkeypatch):
    """La sugerencia sale de `contrapartes_seg.inferir_segmento` —la MISMA
    función que usa el conciliador de Manager— y no de una copia: dos criterios
    harían que la pantalla y el agente sugirieran cosas distintas sin que fallara
    nada (REGLA #9).

    Y cada fila dice DE DÓNDE sale la sugerencia. No es decorado: «lo dice
    Aunesa» y «lo dice el nombre» son dos actos distintos de confirmar, y quien
    tilda tiene que poder distinguirlos sin abrir nada.
    """
    _armar_contrapartes(monkeypatch, [
        ("805", "[805] SUPERFONDO FCI RENTA", "Fondo Común de Inversión"),
        ("807", "[807] ALGO INSTITUCIONAL", "Institucional"),
    ])
    items = datos.contraparte_faltante({})[0].evidencia["_items"]
    por = {f["cuenta"]: f for f in items}
    assert por["805"]["segmento_sugerido"] == "Fondos"
    assert por["805"]["fuente"] == "tipo_cliente", "el FCI lo afirma Aunesa"
    # El que no cae en ninguna regla queda SIN sugerencia y sin fuente: inventar
    # un segmento es peor que no proponer nada.
    assert por["807"]["segmento_sugerido"] == ""
    assert por["807"]["fuente"] == ""
    assert "inferir_segmento" in _codigo(datos.contraparte_faltante)


def test_contraparte_faltante_no_puede_leer_como_no_hay_nada_un_error(monkeypatch):
    """Invariante 1. Si la base no contesta, el detector levanta `SinDatos` — no
    devuelve `[]`. Devolver vacío cerraría el hallazgo POR AUSENCIA y borraría de
    la pantalla una lista que nadie miró."""
    from unittest.mock import MagicMock

    roto = MagicMock()
    roto.connection.side_effect = RuntimeError("Supabase caído")
    import core.postgres
    monkeypatch.setattr(core.postgres, "get_pool", lambda: roto)
    with pytest.raises(tipos.SinDatos):
        datos.contraparte_faltante({})


def test_el_alta_de_contraparte_no_escribe_lo_que_manda_el_navegador():
    """⚠️⚠️ **LA GUARDA QUE HACE QUE ESTA PUERTA NO SEA UN ABM DEL PADRÓN.**

    Dar de alta una contraparte **la saca del AuM** (`jobs/_aum_filters` reglas 3
    y 4) y le pone `nivel_3 = PJ GRANDE`. Sin verificar cada cuenta contra la
    lista VIVA, este endpoint aceptaría sacar del AuM cualquier cuenta del
    padrón — incluida una que el detector no está mirando. Y no fallaría nada:
    el AuM saldría con confianza, de menos.
    """
    from unittest.mock import patch

    import api.services.contrapartes_seg as cs

    a = arreglos.ARREGLOS["alta_contraparte"]
    vivas = {"805": {"cuenta": "805", "denominacion": "[805] SUPERFONDO FCI",
                     "tipo_cliente": "Fondo Común de Inversión",
                     "segmento_sugerido": "Fondos", "fuente": "tipo_cliente"}}
    escrito: list[dict] = []
    with patch.object(a, "_vivas", return_value=vivas), \
         patch.object(cs, "add_contraparte", lambda **kw: escrito.append(kw)):
        ajena = a.aplicar("x", {}, por="yo@x", datos=[{"cuenta": "999",
                                                      "contraparte": "LO QUE SEA"}])
        assert not ajena.ok and not escrito, "una cuenta que no es candidata NO se escribe"

        sin_nombre = a.aplicar("x", {}, por="yo@x", datos=[{"cuenta": "805",
                                                            "contraparte": "  "}])
        assert not sin_nombre.ok and not escrito, "sin nombre no se da de alta"

        ok = a.aplicar("x", {}, por="yo@x", datos=[
            {"cuenta": "805", "contraparte": "SCHRODER", "segmento": "Fondos"}])
    assert ok.ok and len(escrito) == 1
    # Escribe por la PUERTA ÚNICA y con la denominación que guardó el detector,
    # no con la que mandó el navegador.
    assert escrito[0]["cuenta"] == "805"
    assert escrito[0]["denominacion"] == "[805] SUPERFONDO FCI"
    assert escrito[0]["actor"] == "yo@x"
    assert "add_contraparte" in _codigo(a.aplicar), (
        "el alta va por `contrapartes_seg.add_contraparte`, la misma puerta que "
        "usa Manager: un INSERT propio acá sería una segunda definición de «dar "
        "de alta una contraparte» (REGLA #9)")


def test_dar_de_alta_una_contraparte_nunca_es_automatico():
    """Un robot no decide de qué cuenta deja de contarse la plata. `automatico`
    vacío es una DECLARACIÓN, no un olvido — y el test la congela porque el día
    que alguien la prenda, el efecto (una cuenta menos en el AuM) no lo va a ver
    nadie hasta el cierre de mes."""
    h = catalogo.HABILIDADES["contraparte_faltante"]
    assert h.automatico == {}, (
        "dar de alta una contraparte la SACA del AuM: no puede aplicarse solo")
    # Y es RECURRENTE (§0.ep): el sujeto es una familia que se llena y se vacía.
    assert h.naturaleza == {"sin_contraparte": tipos.RECURRENTE}
    assert ("contraparte_faltante", "sin_contraparte") in catalogo.sin_episodios()


def test_la_contraparte_se_aprende_de_las_cargadas_y_no_se_lee_del_nombre():
    """**EL CASO DEL USER, ENTERO** (§0.es).

    *«FCI Consultatio Estrategia I / II / III / IV — si acá leés uno creerías
    que es Consultatio, pero si revisás el historial que ya hay, Consultatio es
    ONE618»*.

    Ese es el motivo por el que el sugeridor mira la TABLA y no el nombre: la
    propuesta ingenua suena perfectamente razonable, y por eso el que tilda la
    acepta. Un sugeridor que se equivoca con seguridad es peor que ninguno.
    """
    from api.services.contrapartes_seg import (
        _palabras,
        indice_contrapartes,
        sugerir_contraparte,
    )

    cargadas = [
        {"den": "[2010] FCI Consultatio Estrategia I", "cp": "ONE618"},
        {"den": "[2014] FCI Consultatio Estrategia II", "cp": "ONE618"},
        {"den": "[1925] FCI Consultatio Estrategia III", "cp": "ONE618"},
        {"den": "[105] LA SEGUNDA ASEGURADORA DE RIESGO", "cp": "LA SEGUNDA"},
    ]
    idx = indice_contrapartes(cargadas)
    cp, porque = sugerir_contraparte("[2011] FCI Consultatio Estrategia IV", idx)
    assert cp == "ONE618", "tiene que salir de la historia, no del nombre"
    assert "CONSULTATIO" in porque and "3" in porque, (
        "la propuesta viaja con su evidencia: sin el porqué no se puede "
        "rechazar sin abrir otra pantalla")

    # Las genéricas NO emparejan a nadie: si «FCI» o «FONDO» contaran, todos los
    # fondos serían parientes de todos y la sugerencia sería ruido con formato
    # de dato. Devuelve una LISTA y no un set porque **el orden es el dato**:
    # la marca va adelante y el producto atrás (§0.es, v2).
    assert _palabras("[9] FCI FONDO COMUN DE INVERSION") == []
    assert _palabras("[2010] FCI Consultatio Estrategia I") == ["CONSULTATIO"]
    assert sugerir_contraparte("[999] FCI RENTA FIJA PESOS", idx) == ("", "")

    # ⚠️ Y una palabra que apunta a DOS contrapartes no se usa. No es que la
    # sugerencia sea peor: sería una moneda al aire con cara de dato.
    ambiguo = indice_contrapartes(cargadas + [
        {"den": "[77] CONSULTATIO OTRA COSA", "cp": "OTRO GESTOR"}])
    assert sugerir_contraparte("[2011] FCI Consultatio Estrategia IV",
                               ambiguo) == ("", "")


def test_el_trabajo_recurrente_vive_en_ENCONTRO_y_no_en_AHORA():
    """User, sobre `CONTRAPARTES NUEVAS`: *«esto no es para AHORA, es para
    ENCONTRÓ»* (§0.et).

    AHORA contesta «¿qué pasó hoy?». Una COLA DE TRABAJO no pasó hoy: está desde
    siempre y baja cuando alguien la trabaja. Su lugar es ENCONTRÓ.

    ⚠️ **Y la guarda que evita que esto haga desaparecer un aviso**: si una regla
    recurrente NO tuviera arreglo, sacarla de AHORA la borraría de las dos
    pantallas —ENCONTRÓ sólo muestra lo que tiene botón— y nadie la volvería a
    ver. Por eso el criterio son las RECURRENTES y no los INFORMES, que son
    avisos sin botón y viven sólo en AHORA.
    """
    from agente import vista

    # El filtro usa la declaración que ya existe (§0.ep), no una lista nueva.
    src = _codigo(vista.ahora)
    assert "catalogo.recurrentes()" in src and "not in recurrentes" in src

    for hab, regla in catalogo.recurrentes():
        assert catalogo.HABILIDADES[hab].arreglo_de(regla), (
            f"«{hab}/{regla}» es recurrente y NO tiene arreglo: sacarla de AHORA "
            "la haría desaparecer de las dos pantallas")
    # Los informes SÍ se quedan en AHORA: no tienen botón, y sacarlos de ahí los
    # dejaría sin ninguna pantalla.
    for hab, regla in catalogo.informes():
        assert not catalogo.HABILIDADES[hab].arreglo_de(regla)
        assert (hab, regla) not in set(catalogo.recurrentes())


def test_el_sugeridor_no_repite_los_27_errores_que_midio_el_diag():
    """**LOS CASOS SON REALES, NO INVENTADOS** (§0.es). La v1 del sugeridor
    acertaba 42% y CONTRADECÍA 27 veces, medido con `diag_contrapartes` §5b
    (leave-one-out) el 2026-09-08. Estos son diez de esos fallos, textuales.

    Cada uno mostró un defecto distinto y por eso están todos:

      · `FCI SBS MULTIACTIVOS` → la marca tiene TRES letras y la v1 tiraba todo
        lo de menos de cuatro. Igual `MAX`, `BM` y el `1810` de Credicoop (que
        además es un número, y los números también se tiraban).
      · `FCI ADCAP BALANCE MULTIACTIVO` → la v1 elegía la palabra con MÁS
        cuentas detrás, así que una palabra de PRODUCTO («BALANCE») le ganaba a
        la marca que estaba al lado.
      · `ALLARIA S.A. ALYC` → existir «ALLARIA - ALYC» con una sola cuenta hacía
        que «ALLARIA» quedara descartada por ambigua, y ganaba una palabra
        cualquiera.

    ⚠️ El test exige **CERO contradicciones**, no un porcentaje de acierto. No
    opinar deja la fila para escribir a mano, que es como estaba; proponer mal
    hace que alguien tilde una cuenta equivocada, y eso mueve el AuM.
    """
    from api.services.contrapartes_seg import (
        indice_contrapartes,
        sugerir_contraparte,
    )

    cargadas: list[dict] = []

    def add(cp: str, *dens: str) -> None:
        cargadas.extend({"den": d, "cp": cp} for d in dens)

    add("SCHRODER", "FCI SCHRODER PERFORMANCE", "FCI SCHRODER RETORNO TOTAL",
        "FCI SCHRODER ARGENTINA")
    add("SBS", "FCI SBS AHORRO PESOS", "FCI SBS ACCIONES ARGENTINA",
        "FCI SBS RENTA PESOS")
    add("ADCAP", "FCI ADCAP RENTA FIJA", "FCI ADCAP AHORRO PESOS",
        "FCI ADCAP WISE CAPITAL")
    add("ONE618", "FCI CONSULTATIO ESTRATEGIA I", "FCI CONSULTATIO ESTRATEGIA II",
        "FCI CONSULTATIO BALANCE", "FCI ONE618 RENTA")
    add("MAX", "FCI MAX RENTA FIJA", "FCI MAX AHORRO", "FCI MAX ACCIONES")
    add("TORONTO", "FCI TORONTO MONEY MARKET", "FCI TORONTO RENTA",
        "FCI TORONTO AHORRO")
    add("ALLARIA", "FCI ALLARIA RENTA FIJA", "FCI ALLARIA AHORRO",
        "FCI ALLARIA ACCIONES", "FCI ALLARIA GLOBAL", "FCI ALLARIA COBERTURA")
    add("ALLARIA - ALYC", "ALLARIA S.A. ALYC")
    add("BULL MARKET", "FCI BM RENTA FIJA", "FCI BM AHORRO PESOS",
        "FCI BM ACCIONES")
    add("CREDICOOP", "FCI 1810 RENTA FIJA", "FCI 1810 AHORRO", "FCI 1810 ACCIONES")
    add("ARGENFUNDS", "FCI ARGENFUNDS RENTA", "FCI ARGENFUNDS COBERTURA DINAMICA")
    add("BANCO GALICIA", "FCI GALICIA SUSTENTABLE", "FCI GALICIA RENTA")
    idx = indice_contrapartes(cargadas)

    # (denominación, quién es de verdad) — copiados del output del diag.
    casos = (
        ("FCI BM SMART CORTO PLAZO", "BULL MARKET"),
        ("FCI ADCAP BALANCE MULTIACTIVO", "ADCAP"),
        ("FCI SBS MULTIACTIVOS", "SBS"),
        ("FCI MAX MONEY MARKET", "MAX"),
        ("FCI ALLARIA DÓLAR PERFORMANCE", "ALLARIA"),
        ("FCI 1810 AHORROS ACTIVOS", "CREDICOOP"),
        ("FCI BVSA SD ALLARIA COBERTURA DINAMICA", "ALLARIA"),
        ("FCI ALLARIA SUSTENTABLE ASG FCI", "ALLARIA"),
        ("BACS ADMINISTRADORA DE ACTIVOS S.A.", "TORONTO"),
        ("ALLARIA S.A. ALYC", "ALLARIA - ALYC"),
        # El que YA andaba con la v1: no se puede romper al arreglar los otros.
        ("FCI Consultatio Estrategia IV", "ONE618"),
    )
    contradice, acierta = [], 0
    for den, real in casos:
        sug, _porque = sugerir_contraparte(den, idx)
        if not sug:
            continue
        if sug == real:
            acierta += 1
        else:
            contradice.append(f"{den} → dijo «{sug}», es «{real}»")
    assert not contradice, (
        "el sugeridor volvió a proponer mal:\n  " + "\n  ".join(contradice))
    assert acierta >= 9, (
        f"acertó {acierta} de {len(casos)}: la v1 acertaba 0 de estos. Si baja, "
        "algo del recorte por posición o de `_GENERICAS` se rompió")


def test_no_es_contraparte_no_se_anota_en_la_tabla_que_mueve_el_AuM():
    """⚠️⚠️ **EL NO VA EN SU PROPIA TABLA, Y ES LO QUE EVITA UN AGUJERO DE PLATA.**

    `jobs/_aum_filters` regla 3 excluye del AuM por la SOLA PRESENCIA del
    `id_cuenta` en `clientes.contrapartes`. Anotar ahí «esta NO es contraparte»
    —una fila con el nombre vacío— le sacaría del AuM la plata de un cliente
    real, sin que fallara nada. Por eso hay una tabla aparte que no lee nadie
    más que el detector.
    """
    from agente import vista

    src = _codigo(vista.no_interesan_contrapartes)
    assert "contrapartes_descartadas" in src
    assert "INSERT INTO clientes.contrapartes " not in src, (
        "el descarte NUNCA escribe en `contrapartes`: esa tabla saca del AuM")
    # Y el detector las resta EN LA MISMA consulta que arma la lista: dos
    # filtros serían dos números que se desincronizan sin fallar.
    det = _codigo(datos.contraparte_faltante)
    assert "contrapartes_descartadas" in det

    # La guarda de siempre: sólo se silencia lo que ESTE hallazgo ofreció.
    assert "permitidas" in src and "_items" in src


def test_el_aviso_de_contrapartes_es_corto_y_lo_que_se_saco_sigue_estando():
    """User (§0.es): *«demasiado texto, no me interesa nada acá»*. La tarjeta
    tenía el desglose por tipo, el denominador del recorte, ocho denominaciones
    truncadas y una frase sobre el AuM — y la lista entera está a un click.

    Corto NO es perder el dato: el desglose y el denominador siguen viajando en
    la evidencia, que es donde se miran cuando se quieren mirar.
    """
    src = _codigo(datos.contraparte_faltante)
    assert "cuenta(s) institucional(es) sin contraparte" in src
    assert len(_codigo(datos.contraparte_faltante).split("problema=")[1]
               .split("que_hacer=")[0]) < 90, "el `problema` volvió a crecer"
    for fuera in ("sus tenencias cuentan en el AuM", "sin señal (Empresa"):
        assert fuera not in src, f"volvió el texto largo: «{fuera}»"
    # Pero el dato no se perdió.
    assert "'sin_senal': sin_senal" in src and "'por_tipo': por_tipo" in src


def test_el_peso_se_consolida_por_vista():
    """El aviso de las 11/16 agrupa por VISTA de la página en vez de listar
    tablas sueltas sin decir a qué pantalla pertenecen (pedido del user,
    2026-09-07): qué vista concentró el crecimiento y con qué tabla adentro."""
    from agente import peso

    hoy = {
        "mercado.cedears_bars_1m": 301_200_000,
        "mercado.market_snapshot": 40_100_000,
        "portafolio.tenencia": 190_000_000,
        "auth.users": 1_000_000,
    }
    vieja = {
        "mercado.cedears_bars_1m": 276_100_000,   # +25.1 MB
        "mercado.market_snapshot": 40_000_000,    # +0.1 MB
        "portafolio.tenencia": 182_000_000,       # +8 MB
        "auth.users": 1_000_000,                  # sin cambio
    }
    grupos = peso.por_vista(hoy, vieja)

    assert grupos[0]["vista"] == peso.VISTAS_POR_SCHEMA["mercado"]
    assert grupos[0]["delta"] == (
        (hoy["mercado.cedears_bars_1m"] - vieja["mercado.cedears_bars_1m"]) +
        (hoy["mercado.market_snapshot"] - vieja["mercado.market_snapshot"]))
    assert grupos[0]["tablas"][0]["tabla"] == "mercado.cedears_bars_1m", (
        "la que más creció va primero adentro del grupo")

    otros = next(g for g in grupos if g["vista"] == peso.OTROS)
    assert "auth.users" in [t["tabla"] for t in otros["tablas"]], (
        "lo que no es nuestro (Supabase) cae en OTROS")

    # Con `vieja` vacía (todavía no hay referencia a 7 días) ningún delta se
    # afirma: no se puede decir que algo creció sin con qué comparar.
    sin_ref = peso.por_vista(hoy, {})
    assert all(g["delta"] == 0 for g in sin_ref)
    assert all(t["delta"] == 0 for g in sin_ref for t in g["tablas"])


def test_todo_schema_nuestro_tiene_vista():
    """Un schema nuestro sin vista declarada acá lo dejaría afuera del listado
    sin que nada avise — el mismo modo de falla que `ap5` con dígitos en
    `schemas_nuestros()` (2026-08-28): no falla nada, queda invisible."""
    from agente import peso

    schemas = peso.schemas_nuestros()
    if not schemas:
        pytest.skip("no se pudo leer sql/schema.sql")
    for s in schemas:
        assert s in peso.VISTAS_POR_SCHEMA


def test_el_agente_solo_mira_nuestro_territorio():
    """El inventario sale del catálogo de Postgres, así que trae también los
    schemas que crea **Supabase** para sus propios servicios.

    Medido en prod el 2026-08-28: **33 tablas** de
    `auth`, `storage`, `realtime` y `vault` que el agente venía juzgando sin
    saber de ellas nada — ni quién las escribe ni cada cuánto deberían. La
    primera que dio la cara fue `realtime.schema_migrations`, marcada como «dejó
    de escribir» estando perfecta.

    ⚠️⚠️ **Y esa misma medición encontró un bug que no fallaba.** El schema
    `ap5` —la posición de futuros de la cámara A3/ACyRSA, con su job, su router
    y sus CINCO tablas declaradas— quedaba afuera de `declaradas()` porque el
    regex pedía `[a-z_]+` para el nombre del schema y **`ap5` tiene un dígito**.
    La función devolvía 234 tablas con cara de estar completa. Sin esto, el
    filtro habría dejado ciego al agente sobre cinco tablas nuestras.
    """
    from agente import peso

    n = peso.schemas_nuestros()
    assert {"ap5", "partner", "mercado", "agente"} <= n
    assert not ({"auth", "storage", "realtime", "vault"} & n), "eso es de Supabase"

    # Los schemas salen del `CREATE SCHEMA`, NO de los nombres de tabla:
    # `partner` está declarado y sus dos tablas no figuran en el archivo, así
    # que deducirlo de las tablas lo dejaría afuera — y es nuestro.
    assert "partner" not in {t.split(".")[0] for t in peso.declaradas()}
    assert "partner" in n

    # Y el schema con dígitos entra en las dos.
    assert "ap5.portfolio" in peso.declaradas()

    src = _codigo(sistema.tabla_quieta)
    # El filtro es por SCHEMA y no por tabla: una tabla nueva en `mercado` que
    # todavía no esté en el archivo se sigue mirando. Lo que queda afuera es el
    # territorio ajeno, no lo que no llegamos a declarar.
    assert "[" + repr("schema") + "] not in nuestros" in src
    # Sin archivo NO se filtra nada: quedarse sin schema no puede convertirse en
    # dejar de mirar la base entera.
    assert "if nuestros and" in src

    # Y la tabla del botón «pedir pata» es de ocasión, como las de órdenes.
    from core import escribe
    assert escribe.la_dispara("mercado.adhoc_subscriptions") == escribe.EVENTO


def test_el_fin_de_semana_entero_no_cuenta_como_atraso():
    """Un lunes a la mañana, el cierre del VIERNES está al día.

    El 2026-08-31 —lunes— seis tablas de cierre salieron todas juntas con «no
    escribe hace 2,6 días» teniendo el dato correcto del viernes 28. Reacción
    del user: *«TOMAN EL FIN DE SEMANA COMO DIA A CONTAR!!»*. Tenía razón.

    `_segundos_de_finde` sumaba el día ANTES de mirarlo, así que el día de
    `desde` nunca se evaluaba y de cada fin de semana descontaba UNO SOLO. Con
    el cierre del viernes, `ult_efectivo` cae en sábado → el bucle arrancaba
    mirando el domingo y **el sábado quedaba contado como día hábil**.

    Y casi no se veía: con un día de descuento el tope daba 2,50 d contra un
    atraso de 2,52 d el lunes a las 09:26 ART. **Fallaba por media hora.**
    """
    from datetime import datetime

    from agente import tablas

    sab = datetime(2026, 8, 29, tzinfo=UTC)          # sábado
    lun = datetime(2026, 8, 31, 14, 26, tzinfo=UTC)  # lunes
    assert tablas._segundos_de_finde(sab, lun) == 2 * 86400, "sábado Y domingo"

    # El caso completo, con el ritmo que el cron declara para un job L-V.
    declarado = {"hueco_s": 86400, "solo_habiles": True}
    perfil = {"schema": "mercado", "tabla": "eikon_cierres", "col_fecha": "fecha",
              "ultimo_dato": datetime(2026, 8, 28, tzinfo=UTC),
              "cadencia": "diaria", "p50_s": 86400, "filas": 3908}
    # Todo el lunes, no un instante: el bug fallaba recién pasada cierta hora.
    for h in (12, 14, 17, 20, 23):
        ahora = datetime(2026, 8, 31, h, 26, tzinfo=UTC)
        r = tablas.frescura(perfil, ahora=ahora, declarado=declarado)
        assert r["estado"] == "ok", f"{h}:26 UTC → {r}"


# ── LA FOTO DE PRIMARY (§0.cy) ─────────────────────────────────────────────

def test_reincidio_es_abierto_en_el_codigo_y_en_las_vistas():
    """M31G6 quedó `reincidio` desde el 28/08: ninguna corrida lo cerraba
    (`_cerrar_ausentes` mira ABIERTOS), ninguna lo actualizaba (`_ver` también,
    así que verlo de nuevo creaba OTRA fila en `reincidencias`) y ninguna
    pantalla lo mostraba. Un problema que volvió es trabajo, no historia."""
    assert tipos.REINCIDIO in tipos.ABIERTOS
    schema = (RAIZ / "sql" / "schema.sql").read_text(encoding="utf-8")
    for vista in ("agente.v_ahora", "agente.v_encontro", "agente.v_habilidades"):
        i = schema.index(f"CREATE OR REPLACE VIEW {vista}")
        cuerpo = schema[i:i + 1500]
        assert "'reincidio'" in cuerpo, f"{vista} no cuenta `reincidio` como abierto"


def _universo_1816(*tickers: str) -> dict:
    return {"fuente": "1816", "instrumentos": {
        t: {"_curva": "Soberanos ARS tasa fija", "fechaVencimiento": "2027-01-29",
            "denominacion": f"Letra {t}"} for t in tickers}}


def test_soberanos_faltantes_descarta_lo_que_primary_no_lista_salvo_cartera(monkeypatch):
    """User (2026-09-02): *«si Primary no lo lista es porque no está, eso mata
    todo; no hay que insistir»*. Lo que 1816 publica y Primary no lista NO es
    un hallazgo — ni alta ni aviso —, salvo que esté en cartera, donde el
    problema es más grave. Lo que garantiza que «no está en Primary» sea
    verdad es la foto fresca (§0.cy), no un aviso por bono."""
    from types import SimpleNamespace

    from agente import fuentes
    from core import curvas_ejes, curvas_sql

    monkeypatch.setattr(fuentes, "universo_1816",
                        lambda: _universo_1816("S29E7", "X29E7", "T30E7"))
    monkeypatch.setattr(fuentes, "master", lambda: [{"ticker_corto": "AL30"}])
    monkeypatch.setattr(fuentes, "en_cartera", lambda: {"S29E7"})
    monkeypatch.setattr(fuentes, "tickers_en_primary", lambda: {"X29E7", "AL30"})
    monkeypatch.setattr(curvas_ejes, "desde_1816",
                        lambda c: SimpleNamespace(emisor_tipo="soberano", moneda="ARS"))
    monkeypatch.setattr(curvas_sql, "calendario_habil", lambda: set())
    monkeypatch.setattr(curvas_sql, "sale_del_master", lambda *a, **k: False)

    por = {h.sujeto: h for h in mercado.soberanos_faltantes({})}
    assert por["S29E7"].regla == "no_esta_en_curvas", "en cartera → pide el alta"
    assert por["X29E7"].regla == "no_esta_en_curvas", "cotiza → pide el alta"
    assert "T30E7" not in por, "no cotiza y no está en cartera → no existe para nosotros"


def test_la_foto_de_primary_tiene_cron_y_quien_la_vigile():
    """La foto la escribía un script manual que nadie corría. Ahora: un cron en
    el repo (antes del cleanup y de los motores), un detector que lee ESE cron
    —no una copia del horario— y canta si un día no corrió, y un script que se
    niega a pisar la foto con una respuesta parcial de Primary."""
    from agente import crontab
    cron = sistema._cron_discovery(crontab.del_repo())
    assert cron is not None, "deploy/crontab.txt no corre scripts.discovery_pyrofex"
    minuto, hora = (int(x) for x in cron.split()[:2])
    assert (hora, minuto) < (12, 30), "tiene que correr ANTES del cleanup (12:30 UTC)"
    h = catalogo.HABILIDADES["foto_primary"]
    assert h.dominio == "SISTEMA" and not h.arreglos
    disc = (RAIZ / "scripts" / "discovery_pyrofex.py").read_text(encoding="utf-8")
    assert "MINIMO_VS_ANTERIOR" in disc and "sys.exit(main())" in disc


def test_foto_primary_juzga_contra_la_ultima_corrida_esperada(monkeypatch):
    from datetime import datetime

    from agente import crontab, fuentes, reloj
    monkeypatch.setattr(crontab, "del_repo", lambda: {
        "15 12 * * 1-5 run_job.sh discovery_pyrofex 10m 'python -m scripts.discovery_pyrofex'"})
    # El horario lo evalúa el ÚNICO evaluador cron del repo (salud): un lunes
    # a las 11 UTC la última esperada es la del VIERNES, no hay cron el finde.
    from api.services import salud
    assert salud.ultima_ejecucion_esperada("15 12 * * 1-5", datetime(2026, 8, 31, 11, 0, tzinfo=UTC)) \
        == datetime(2026, 8, 28, 12, 15, tzinfo=UTC)
    assert "_ultima_esperada" not in dir(sistema), "volvió un segundo evaluador cron"

    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: datetime(2026, 9, 1, 14, 0, tzinfo=UTC))
    # Foto de hoy 12:20 → nada.
    monkeypatch.setattr(fuentes, "primary_fecha", lambda: datetime(2026, 9, 1, 12, 20, tzinfo=UTC))
    assert sistema.foto_primary({"gracia_min": 60}) == []
    # Foto del 15/08 (el caso real) → alta, con las dos fechas en la evidencia.
    monkeypatch.setattr(fuentes, "primary_fecha", lambda: datetime(2026, 8, 15, 19, 23, tzinfo=UTC))
    (h,) = sistema.foto_primary({"gracia_min": 60})
    assert h.regla == "foto_vieja" and h.severidad == "alta"
    assert h.evidencia["esperada"].startswith("2026-09-01T12:15")
    # Nunca corrió → se dice, no se calla.
    monkeypatch.setattr(fuentes, "primary_fecha", lambda: None)
    (h,) = sistema.foto_primary({"gracia_min": 60})
    assert h.regla == "nunca_corrio"
    # Sin cron declarado NO se afirma nada: SinDatos, que el motor traduce.
    monkeypatch.setattr(crontab, "del_repo", lambda: set())
    with pytest.raises(tipos.SinDatos):
        sistema.foto_primary({})


def test_el_alta_pregunta_en_vivo_antes_de_decir_que_primary_no_lo_lista():
    """El pre-flight decía «⚠ Primary NO lista este símbolo» y BLOQUEABA el alta
    de S29E7 mientras OPERAR lo encontraba: leía la FOTO y afirmaba sobre
    Primary. Antes de afirmarlo pregunta en vivo (la misma llamada que OPERAR) y
    dice de cuándo es la foto. En vivo sí / foto no → INFO, no BLOQUEA."""
    from agente import alta
    src = inspect.getsource(alta.estado_simbolo)
    assert "simbolos_live" in src and "primary_fecha" in src
    assert "foto_vieja" in src
    pre = inspect.getsource(alta)
    assert "(INFO if foto_vieja else OK) if con is True" in pre


# ── REINCIDE EL ITEM, NO EL GRUPO (§0.cz) ──────────────────────────────────

def test_reincide_el_item_no_el_grupo():
    """User (2026-09-01): *«emisores sin cargar va a haber siempre, eso no es
    reincidencia. Reincidencia sería que si yo agrego un emisor de un bono, ese
    bono vuelva a estar sin emisor»*. El sujeto de `ficha_incompleta` es el
    CAMPO, así que un título nuevo sin cartera «reincidía» sobre un arreglo
    que escribió otros títulos. La puerta cruza los `_items` de hoy contra lo
    que la acción escribió, y sin intersección no hay reincidencia.

    ⚠️ La clave lleva `_` a propósito (2026-09-05): es dato de MÁQUINA —la
    identidad para decidir la reincidencia— y no algo para leer. El `_` es lo
    que hace que la pantalla no lo dibuje, misma convención que `_fuentes` en
    `agente/explicar.py`. Sin eso, la tarjeta de EMISOR desplegaba las 379
    unidades adentro de la evidencia."""
    src = inspect.getsource(registro._ver)
    i = src.index("INSERT INTO agente.reincidencias")
    antes = src[:i]
    assert '"_items"' in antes and "agente.acciones" in antes
    assert "previo = None" in antes
    det = inspect.getsource(cat_det.ficha_incompleta)
    assert '"_items": [f["unidad"] for f in filas]' in det
    # Y la base deja UNA fila abierta por trío, `reincidio` incluido.
    schema = (RAIZ / "sql" / "schema.sql").read_text(encoding="utf-8")
    j = schema.index("CREATE UNIQUE INDEX IF NOT EXISTS hallazgos_abierto_unico")
    assert "'reincidio'" in schema[j:j + 250]
    assert "DROP INDEX IF EXISTS agente.hallazgos_abierto_unico" in schema[:j]
    assert "dedup §0.cz" in schema[:j]


# ── EL LATIDO (§0.da) ──────────────────────────────────────────────────────

def test_el_universo_de_procesos_sale_de_systemd_y_del_cron():
    """Un motor nuevo es una unit nueva: con eso el agente ya lo espera. La
    ventana sale del cron que lo prende y lo apaga, no de una lista a mano."""
    from datetime import datetime

    from agente import unidades
    d = unidades.declaradas()
    assert d["motor_rofex"]["proceso"] == "engines.valores"
    assert d["api"]["proceso"] == "api.main", "uvicorn api.main:app también late (§0.dg)"
    v = d["motor_rofex"]["ventana"]
    assert v["inicio"] == (13, 20) and v["fin"] == (20, 5) and v["dias"] == {0, 1, 2, 3, 4}
    assert d["agente"]["ventana"] is None, "sin cron que lo apague, corre siempre"
    assert unidades.en_ventana(v, datetime(2026, 9, 1, 14, 0, tzinfo=UTC))
    assert not unidades.en_ventana(v, datetime(2026, 9, 5, 14, 0, tzinfo=UTC)), "sábado"
    assert not unidades.en_ventana(v, datetime(2026, 9, 1, 21, 0, tzinfo=UTC)), "apagado"
    assert unidades.en_ventana(None, datetime(2026, 9, 5, 3, 0, tzinfo=UTC))
    # Cada unit con -m tiene proceso, y ninguno se repite: es la clave del latido.
    procesos = [x["proceso"] for x in d.values() if x["proceso"]]
    assert len(procesos) == len(set(procesos)) >= 15


def test_el_latido_arranca_solo_en_los_motores_y_sabe_quien_es():
    """`python -m engines.valores` late sin que el motor lo pida: el paquete
    `engines` lo arranca desde `sys.orig_argv`. Y `sys.argv[0]` no sirve para
    eso: mientras se importa el paquete vale `-m`."""
    from core import latido
    assert latido.proceso_de(["python", "-m", "engines.valores"]) == "engines.valores"
    assert latido.proceso_de(["python", "-m", "jobs.control_saldos"]) == "jobs.control_saldos"
    assert latido.proceso_de(["uvicorn", "api.main:app"]) is None
    src = (RAIZ / "engines" / "__init__.py").read_text(encoding="utf-8")
    assert "orig_argv" in src and 'startswith("engines.")' in src
    assert "latido.arrancar(" in src
    # El WS anota el estado del feed: es el único punto por el que pasan todos.
    ws = (RAIZ / "core" / "websocket.py").read_text(encoding="utf-8")
    for clave in ("ws_rechazados_primary", "ws_rechazados_rofex", "ws_cuarentena",
                  "ws_ultimo_mensaje_at", 'ws="conectado"', 'ws="agotado"'):
        assert clave in ws, clave
    # Y los daemons de jobs/ lo llaman explícito.
    for j in ("control_saldos", "tenencia_live", "agente"):
        assert "latido.arrancar()" in (RAIZ / "jobs" / f"{j}.py").read_text(encoding="utf-8"), j


def _latido(proceso, hace_s, ahora, **data):
    from datetime import timedelta
    return {"proceso": proceso, "pid": 1, "host": "h",
            "arrancado_at": ahora - timedelta(hours=1),
            "latido_at": ahora - timedelta(seconds=hace_s), "data": data}


def test_motor_latido_distingue_apagado_colgado_sin_feed_y_mudo(monkeypatch):
    """Cuatro veredictos porque el que_hacer es otro en cada uno. Y «vivo y
    mudo con el mercado quieto» no es «muerto»: por eso el feed mudo es media
    y solo en rueda caliente."""
    from datetime import datetime, timedelta

    from agente import fuentes, reloj, unidades

    ahora = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)          # martes, en rueda
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: ahora)
    monkeypatch.setattr(reloj, "feed_caliente", lambda a=None: True)
    v = {"inicio": (13, 20), "fin": (20, 5), "dias": {0, 1, 2, 3, 4}}
    monkeypatch.setattr(unidades, "declaradas", lambda: {
        "motor_a": {"proceso": "engines.a", "restart": "always", "ventana": v},
        "motor_b": {"proceso": "engines.b", "restart": "always", "ventana": v},
        "motor_c": {"proceso": "engines.c", "restart": "always", "ventana": v},
        "motor_d": {"proceso": "engines.d", "restart": "always", "ventana": v},
        "motor_e": {"proceso": "engines.e", "restart": "always", "ventana": v},
        "motor_f": {"proceso": "engines.f", "restart": "always", "ventana": v},
        "nocturno": {"proceso": "engines.n", "restart": "always",
                     "ventana": {"inicio": (22, 0), "fin": (23, 0), "dias": None}},
        "api": {"proceso": None, "restart": "always", "ventana": None},
    })
    monkeypatch.setattr(unidades, "activas", lambda us: {
        "motor_a": "inactive", "motor_b": "active", "motor_c": "active",
        "motor_d": "active", "motor_e": "active", "motor_f": "active", "nocturno": "inactive"})
    monkeypatch.setattr(fuentes, "latidos", lambda: {
        "engines.c": _latido("engines.c", 600, ahora),
        "engines.d": _latido("engines.d", 5, ahora, ws="reconectando", ws_reconexiones=3),
        "engines.e": _latido("engines.e", 5, ahora, ws="conectado",
                             ws_ultimo_mensaje_at=(ahora - timedelta(minutes=30)).isoformat()),
        "engines.f": _latido("engines.f", 5, ahora, ws="conectado",
                             ws_ultimo_mensaje_at=(ahora - timedelta(seconds=20)).isoformat()),
    })
    por = {h.sujeto: h for h in sistema.motor_latido(
        {"tolerancia_s": 90, "gracia_arranque_s": 120, "feed_mudo_min": 10})}
    assert por["motor_a"].regla == "apagado"
    assert por["motor_b"].regla == "sin_latido"
    assert por["motor_c"].regla == "colgado" and por["motor_c"].severidad == "alta"
    assert por["motor_d"].regla == "sin_feed"
    assert por["motor_e"].regla == "feed_mudo" and por["motor_e"].severidad == "media"
    assert "motor_f" not in por, "vivo, conectado y recibiendo"
    assert "nocturno" not in por, "fuera de su ventana, apagado es lo normal"
    assert "api" not in por
    for h in por.values():
        assert "systemctl restart" in h.que_hacer

    # Arrancando no es caído: a los 60 s del cron nadie tiene que haber latido.
    monkeypatch.setattr(reloj, "ahora_utc",
                        lambda a=None: datetime(2026, 9, 1, 13, 21, tzinfo=UTC))
    assert sistema.motor_latido({"gracia_arranque_s": 120}) == []

    # Nadie late todavía (código sin desplegar en los motores) → SinDatos, no 15 alarmas.
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: ahora)
    monkeypatch.setattr(fuentes, "latidos", lambda: {})
    with pytest.raises(tipos.SinDatos):
        sistema.motor_latido({})
    # Y sin systemd, sin_latido igual se canta con «no pude preguntarle».
    monkeypatch.setattr(fuentes, "latidos", lambda: {"engines.f": _latido("engines.f", 5, ahora)})
    monkeypatch.setattr(unidades, "activas", lambda us: None)
    por = {h.sujeto: h for h in sistema.motor_latido({})}
    assert por["motor_a"].regla == "sin_latido"


def test_motor_caido_deja_los_procesos_al_latido():
    """Dos habilidades sobre lo mismo son dos relojes: los motores los juzga
    `motor_latido` por su latido y `motor_caido` se queda con los jobs."""
    src = inspect.getsource(sistema.motor_caido)
    i = src.index('if (p.get("tipo") or "") == "motor":')
    assert "continue" in src[i:i + 80]
    h = catalogo.HABILIDADES["motor_latido"]
    assert h.dominio == "SISTEMA" and not h.arreglos and h.cada_segundos <= 300


def test_bono_sin_precio_dice_por_que_no_esta_suscripto(monkeypatch):
    """«Nadie lo suscribió» y «lo pedí y Primary no lo lista» tienen arreglos
    distintos: el primero tiene botón (pedir la pata) y el segundo no, porque
    no hay precio posible. El WS deja la lista completa en su latido."""
    from datetime import datetime

    from agente import fuentes, reloj
    monkeypatch.setattr(mercado, "_feed_o_sindatos", lambda: None)
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: datetime(2026, 9, 1, 15, 0, tzinfo=UTC))
    monkeypatch.setattr(reloj, "en_rueda", lambda a=None: True)
    monkeypatch.setattr(fuentes, "master", lambda: [
        {"ticker_corto": "AAA", "ticker": "MERV - XMEV - AAA - 24hs"},
        {"ticker_corto": "BBB", "ticker": "MERV - XMEV - BBB - 24hs"}])
    monkeypatch.setattr(fuentes, "snapshot", lambda *a, **k: {})
    monkeypatch.setattr(fuentes, "latidos", lambda: {"engines.valores": {"data": {
        "ws_rechazados_primary": ["MERV - XMEV - AAA - 24hs"]}}})
    por = {h.sujeto: h for h in mercado.bono_sin_precio({})}
    assert por["AAA"].regla == "simbolo_rechazado"
    assert por["BBB"].regla == "no_suscripto"
    assert catalogo.HABILIDADES["bono_sin_precio"].arreglo_de("simbolo_rechazado") == ""


# ── LA ILIQUIDEZ NO ES UN ERROR: ES EL SILENCIO (§0.eu) ───────────────────

def test_la_iliquidez_no_genera_hallazgo_y_las_tres_causas_nuestras_si(monkeypatch):
    """User (2026-09-08), sobre `BYZ2O · sin_punta · ×26`: *«si es por iliquidez
    no lo quiero ver. Ver solamente algo que ES un error. Iliquidez no es un
    error... SIN AVISO DOY POR SENTADO LA ILIQUIDEZ»*.

    ⚠️ **Y ESE «doy por sentado» es lo que este test protege.** El silencio sólo
    significa iliquidez si las TRES causas nuestras siguen cantando: sin símbolo
    cargado, símbolo que el mercado rechaza, y símbolo que nadie suscribió. El
    día que se caiga una de las tres, callar el cuarto caso pasa a esconder un
    error — por eso las cuatro ramas se verifican juntas, en un solo test.
    """
    from datetime import datetime

    from agente import fuentes, reloj
    ahora = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(mercado, "_feed_o_sindatos", lambda: None)
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: ahora)
    monkeypatch.setattr(reloj, "en_rueda", lambda a=None: True)
    monkeypatch.setattr(fuentes, "master", lambda: [
        {"ticker_corto": "RECH", "ticker": "MERV - XMEV - RECH - 24hs"},
        {"ticker_corto": "NADIE", "ticker": "MERV - XMEV - NADIE - 24hs"},
        {"ticker_corto": "SINSIM", "ticker": ""},
        {"ticker_corto": "BYZ2O", "ticker": "MERV - XMEV - BYZ2O - 24hs"},
        {"ticker_corto": "OPERA", "ticker": "MERV - XMEV - OPERA - 24hs"}])
    monkeypatch.setattr(fuentes, "snapshot", lambda *a, **k: {
        # Lo estamos escuchando y el mercado no dio punta: ILIQUIDEZ.
        "MERV - XMEV - BYZ2O - 24hs": {"last_price": 0, "updated_at": ahora},
        "MERV - XMEV - OPERA - 24hs": {"last_price": 74.19, "updated_at": ahora}})
    monkeypatch.setattr(fuentes, "latidos", lambda: {"engines.valores": {"data": {
        "ws_rechazados_primary": ["MERV - XMEV - RECH - 24hs"]}}})

    por = {h.sujeto: h.regla for h in mercado.bono_sin_precio({})}
    assert por == {"RECH": "simbolo_rechazado", "NADIE": "no_suscripto",
                   "SINSIM": "sin_simbolo"}, (
        "el que no tiene punta y el que opera no son hallazgos; las tres causas "
        "NUESTRAS sí, y las tres en alta")
    assert "BYZ2O" not in por, (
        "lo estamos escuchando y el mercado no dio punta: eso es el papel, no "
        "el sistema — y sin aviso la iliquidez se da por sentada")
    # Lo que impide que la regla vuelva no es este test: es que su `que_hacer`
    # tendría que nombrar una acción, y no hay ninguna (`tipos.NADA_QUE_HACER`).


def test_un_que_hacer_que_dice_QUE_NO_HAY_NADA_QUE_HACER_no_es_un_hallazgo():
    """El invariante #2 se cumplía **de forma nominal**: `sin_punta` escribía
    «Nada que apretar» y pasaba el CHECK. Un CHECK que se contesta «no hay nada
    que hacer» no es un CHECK — ahora lo rechaza el constructor, que es donde no
    se puede olvidar.

    Al ponerlo aparecieron TRES casos más, y ninguno se había notado: dos tenían
    la acción escondida después de la frase (se reordenaron) y el tercero
    —`cleanup_curvas·borrados`— declaraba por escrito que no era un problema y
    ocupaba un renglón igual.
    """
    for frase in ("Nada que apretar: el papel no operó.",
                  "Nada que hacer: es lo esperado.",
                  "No hay nada que hacer acá.",
                  "  ninguna ACCIÓN necesaria  "):
        with pytest.raises(ValueError, match="no hay nada que hacer"):
            tipos.Hallazgo(sujeto="X", regla="r", severidad="baja",
                           problema="p", que_hacer=frase)
    # Y un `que_hacer` que NOMBRA una acción entra, aunque contenga la palabra.
    tipos.Hallazgo(sujeto="X", regla="r", severidad="baja", problema="p",
                   que_hacer="Relanzar el job; si no cambia nada, no hacer nada más.")

    # ⚠️ Los avisos de jobs no pasan por `Hallazgo` hasta que el detector corre,
    # así que la tabla se barre acá: un `Reporte` mal escrito hoy reventaría en
    # producción el día que su contador diera > 0, y no antes.
    from agente.reportes import REPORTES
    for r in REPORTES:
        assert not " ".join(r.que_hacer.split()).lower().startswith(
            tipos.NADA_QUE_HACER), (
            f"{r.job}·{r.stat}: su `que_hacer` empieza diciendo que no hay nada "
            "que hacer. O nombra la acción, o no es un aviso")


# ── LOS RELANZABLES SE DERIVAN (§0.db) ────────────────────────────────────

def test_los_relanzables_salen_del_crontab_y_de_los_contratos():
    """`REHACIBLES` tuvo UNA entrada diez días y el botón de rehacer aparecía
    en un solo job. Lo que hace relanzable a un job ya está escrito: el
    comando en el crontab, la prueba en los contratos de SALUD unidos por
    `core.escribe`. Lo declarado a mano gana; el resto se deriva."""
    r = rehacer.rehacibles()
    assert len(r) >= 40
    assert r["portafolio_diario"]["prueba"] == rehacer.PRUEBA_DIA
    assert r["portafolio_diario"]["schedule"] == "0 11 * * 1-5", "el horario sale del cron"
    assert r["cierre_chain"]["prueba"] == rehacer.PRUEBA_TABLA
    assert r["cierre_chain"]["tabla"] == "mercado.snapshots_cierre"
    # ⚠️ `negocio_chain` pasó de PRUEBA_CORRIDA a PRUEBA_TABLA el 2026-09-08, y
    # el cambio no fue acá: fue que `core/escribe` **empezó a ver** quién escribe
    # `operaciones.operaciones` (§0.ew). La cadena corre `jobs.fci_bilateral`,
    # que la escribe, y esa tabla YA tenía contrato en SALUD — así que ahora se
    # juzga por el DATO y no por la corrida. Es la lección de `motor_caido`:
    # **mirar el proceso no es mirar el resultado**; un job que revienta después
    # de escribir figuraba en rojo con el día completo, y uno que corre y no
    # escribe figuraba en verde.
    #
    # No inventa un juicio nuevo: `_al_dia_por_contrato` llama a
    # `salud._chequeo_dato` con el MISMO contrato que salud ya evalúa, con su
    # tolerancia de hábiles adentro. Lo que se gana es el BOTÓN sobre un
    # veredicto que hasta hoy no tenía dónde apretarse.
    assert r["negocio_chain"]["prueba"] == rehacer.PRUEBA_TABLA
    assert r["negocio_chain"]["tabla"] == "operaciones.operaciones"
    assert "run_job" not in r["negocio_chain"]["comando"]
    assert "jobs.aranceles" in r["negocio_chain"]["comando"]
    # Un job con tres líneas de cron tiene los tres horarios, no el primero.
    assert len(r["sync_comitentes"]["schedules"]) == 3
    # Y el árbitro de nombres resuelve el módulo, el label y el tipo de job_runs.
    assert rehacer.cual_job("jobs.aranceles") == "negocio_chain"
    assert rehacer.cual_job("job:cierre_chain") == "cierre_chain"
    assert rehacer.cual_job("jobs.snapshot_cierre") == "cierre_chain"
    assert rehacer.cual_job("motor_rofex.service") == ""


def test_todavia_no_le_toco_lee_el_crontab_y_no_la_prosa():
    """La hora de arranque salía de una regex sobre `cadencia` (texto libre).
    Ahora sale del crontab, por el único evaluador cron. Un job diario de las
    12 UTC no está atrasado a las 11; a las 13 con umbral de 10 min, sí."""
    from datetime import datetime

    from core.tz import AR_TZ
    p = {"unidad": "jobs.argentina_datos", "umbral_s": 600, "cadencia": "prosa"}
    a_las_8_ar = datetime(2026, 9, 1, 8, 0, tzinfo=AR_TZ)      # 11:00 UTC
    a_las_10_ar = datetime(2026, 9, 1, 10, 0, tzinfo=AR_TZ)    # 13:00 UTC
    assert sistema._todavia_no_le_toco(p, a_las_8_ar)
    assert not sistema._todavia_no_le_toco(p, a_las_10_ar)
    # Sin cron conocido no se afirma nada: avisar de más antes que callar.
    assert not sistema._todavia_no_le_toco({"unidad": "motor_rofex", "cadencia": "13-21 UTC"},
                                           a_las_8_ar)
    assert "re.search" not in inspect.getsource(sistema._todavia_no_le_toco)


# ── ARBITRAR DOS COPIAS (§0.dc) ────────────────────────────────────────────

def test_el_duplicado_con_sql_tiene_boton_y_el_manual_es_aviso(monkeypatch):
    """`core/duplicados` declaraba `arreglo_sql` en dos de cinco y ningún
    arreglo lo usaba: «dato partido» salía sin botón. Ahora el que tiene SQL
    se arbitra desde ENCONTRÓ y el manual es un aviso con la instrucción."""
    from core import duplicados
    assert catalogo.HABILIDADES["dato_partido"].arreglo_de("copias_que_no_coinciden") \
        == "arbitrar_copia"
    assert catalogo.HABILIDADES["dato_partido"].arreglo_de("copias_a_mano") == ""
    con_sql = [d for d in duplicados.DUPLICADOS if d.arreglo_sql.strip()]
    assert con_sql and all(d.gana in ("a", "b") for d in con_sql), (
        "todo duplicado con SQL declara quién gana: es lo que se anota en el libro")

    monkeypatch.setattr(duplicados, "divergencias", lambda: {
        "partidos": [
            {"id": "simbolo_columna_vs_blob", "que": "el símbolo", "a": "col", "b": "blob",
             "arbitro": "la COLUMNA", "rompe": "sin precio", "tiene_sql": True,
             "arreglo_manual": "", "n": 2,
             "ejemplos": [{"sujeto": "AO29", "valor_a": "x", "valor_b": "y"}]},
            {"id": "emisor_curvas_vs_assets", "que": "el emisor", "a": "curvas", "b": "assets",
             "arbitro": "1816", "rompe": "agrupa mal", "tiene_sql": False,
             "arreglo_manual": "correr jobs.ficha_1816", "n": 1, "ejemplos": []}],
        "sin_mirar": [], "revisados": 5})
    por = {h.sujeto: h for h in datos.dato_partido({})}
    assert por["simbolo_columna_vs_blob"].regla == "copias_que_no_coinciden"
    assert por["emisor_curvas_vs_assets"].regla == "copias_a_mano"
    assert "ficha_1816" in por["emisor_curvas_vs_assets"].que_hacer


def test_arbitrar_copia_relee_escribe_y_anota_una_linea_por_fila(monkeypatch):
    from core import duplicados
    a = arreglos.ARREGLOS["arbitrar_copia"]
    filas = [("AO29", "MERV - XMEV - AO29D - 24hs", "MERV - XMEV - AO29 - 24hs"),
             ("CO32", "MERV - XMEV - CO32D - 24hs", "MERV - XMEV - CO32 - 24hs")]
    monkeypatch.setattr(duplicados, "una", lambda i: {"ok": True, "id": i, "filas": filas})
    pv = a.preview("simbolo_columna_vs_blob", {})
    assert pv["puede_aplicar"] and pv["donde"] == "mercado.curvas"
    assert len(pv["pasos"]) == 2 and "AO29D" in pv["pasos"][0]["detalle"]
    # El manual no se puede aplicar, y lo dice sin error.
    pv2 = a.preview("emisor_curvas_vs_assets", {})
    assert pv2["ok"] and not pv2["puede_aplicar"]

    escritas, libro_lineas = [], []
    monkeypatch.setattr(duplicados, "arbitrar",
                        lambda i: (escritas.append(i) or {"ok": True, "filas": 2}))
    from agente import libro as _libro
    monkeypatch.setattr(_libro, "registrar", lambda **kw: libro_lineas.append(kw))
    r = a.aplicar("simbolo_columna_vs_blob", {}, por="test")
    assert r.ok and escritas == ["simbolo_columna_vs_blob"]
    assert [x["objetivo"] for x in libro_lineas] == ["AO29", "CO32"]
    assert libro_lineas[0]["antes"].endswith("AO29 - 24hs")
    assert libro_lineas[0]["despues"].endswith("AO29D - 24hs")
    # Sin filas no se ejecuta nada: ya coinciden.
    monkeypatch.setattr(duplicados, "una", lambda i: {"ok": True, "id": i, "filas": []})
    r = a.aplicar("simbolo_columna_vs_blob", {}, por="test")
    assert r.ok and "coinciden" in r.detalle and escritas == ["simbolo_columna_vs_blob"]


# ── LO QUE LOS JOBS REPORTAN (§0.dd) ───────────────────────────────────────

def test_cada_reporte_declarado_existe_en_su_job():
    """Una fila en `agente/reportes.py` promete que ese job guarda ese stat y
    su lista. Si el job no lo escribe, el aviso jamás aparece y nadie lo nota:
    por eso se cruza contra el código del job."""
    from agente.reportes import REPORTES
    for r in REPORTES:
        src = (RAIZ / "jobs" / f"{r.job}.py").read_text(encoding="utf-8")
        if r.stat.endswith("_conflictos"):
            # Stat por regla: `f"{r.id}_conflictos"` con el id declarado en REGLAS.
            assert '_conflictos"' in src and f'"{r.stat[:-11]}"' in src, (
                f"{r.job}: la regla {r.stat[:-11]!r} no existe o no guarda conflictos")
        else:
            assert r.stat in src, f"{r.job} no guarda el stat {r.stat!r}"
        if r.con_lista:
            assert f'{r.stat}_lista' in src or '_conflictos_lista' in src, (
                f"{r.job} no guarda la lista de {r.stat!r}")
        assert len(r.que_hacer) > 40 and r.severidad in tipos.SEVERIDADES
    assert len({(r.job, r.stat) for r in REPORTES}) == len(REPORTES), "un stat repetido"
    assert not catalogo.HABILIDADES["job_reporto"].arreglos
    # Lo mismo para los VOLÚMENES (§0.dk): el contador declarado tiene que
    # existir en el job, o `trajo_poco` vigila un número que nadie escribe.
    from agente.reportes import VOLUMENES
    for v in VOLUMENES:
        archivo = "portafolio_backfill" if v.job == "aum" else v.job
        src = (RAIZ / "jobs" / f"{archivo}.py").read_text(encoding="utf-8")
        assert v.stat in src, f"{v.job} no guarda el stat {v.stat!r}"
        assert v.modo in ("diario", "acumulado")
    assert len({v.job for v in VOLUMENES}) == len(VOLUMENES), "un job declarado dos veces"


def test_job_reporto_convierte_el_contador_en_aviso_con_su_lista(monkeypatch):
    from datetime import datetime

    from agente import reloj
    monkeypatch.setattr(reloj, "hhmm", lambda a=None: "10:00")
    corridas = {
        "ficha_1816": {"finished_at": datetime(2026, 9, 1, 22, 31, tzinfo=UTC),
                       "status": "ok",
                       "stats": {"moneda_divergente": 2,
                                 "moneda_divergente_lista": ["AO29: nuestro=ARS 1816=USD",
                                                             "CO32: nuestro=ARS 1816=USD"]}},
        "tamar_1816": {"finished_at": datetime(2026, 9, 1, 20, 0, tzinfo=UTC),
                       "status": "ok", "stats": {"sin_dato": 0}},
        # Corre código viejo: tiene el número y no la lista → igual se canta.
        "snapshot_cierre": {"finished_at": datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
                            "status": "ok", "stats": {"curvas_salteadas": 3}},
    }
    monkeypatch.setattr(datos, "_ultima_corrida", lambda job: corridas.get(job))
    por = {h.sujeto: h for h in datos.job_reporto({})}
    h = por["ficha_1816·moneda_divergente"]
    assert h.regla == "moneda_divergente" and h.severidad == "alta"
    assert "2 bono(s)" in h.problema and "01/09 22:31" in h.problema
    assert "AO29" in h.detalle and h.evidencia["lista"][1].startswith("CO32")
    assert "tamar_1816·sin_dato" not in por, "cero no es un aviso"
    assert "próxima corrida" in por["snapshot_cierre·curvas_salteadas"].detalle
    # Sin poder leer job_runs, no se afirma nada.
    def _rompe(job):
        raise RuntimeError("db caída")
    monkeypatch.setattr(datos, "_ultima_corrida", _rompe)
    with pytest.raises(tipos.SinDatos):
        datos.job_reporto({})


# ── EL CATÁLOGO DE 1816 NO ES ESTÁTICO (§0.df) ─────────────────────────────

def test_el_catalogo_de_1816_tiene_cron_vigia_y_rastro():
    """User (2026-09-02): *«jamás algo así puede ser estático»*. La misma
    receta que la foto de Primary: un cron en el repo, una habilidad que lee
    ESE cron y canta si no corrió, y el job deja rastro en job_runs."""
    from agente import crontab
    cron = sistema._cron_de(crontab.del_repo(), "jobs.mercado_1816_discovery")
    assert cron is not None, "deploy/crontab.txt no corre mercado_1816_discovery"
    minuto, hora = (int(x) for x in cron.split()[:2])
    assert (hora, minuto) < (13, 0), "antes de tamar_1816 (13:00 UTC), que lee la grafía de ahí"
    h = catalogo.HABILIDADES["foto_1816"]
    assert h.dominio == "SISTEMA" and not h.arreglos
    job = (RAIZ / "jobs" / "mercado_1816_discovery.py").read_text(encoding="utf-8")
    assert "JobRunLogger" in job and "sys.exit(main())" in job
    # Las dos fotos comparten el detector: dos copias de «¿la foto está vieja?»
    # se separarían el día que se corrija una.
    assert "_foto(" in inspect.getsource(sistema.foto_primary)
    assert "_foto(" in inspect.getsource(sistema.foto_1816)


def test_foto_1816_canta_cuando_el_catalogo_quedo_viejo(monkeypatch):
    from datetime import datetime

    from agente import crontab, fuentes, reloj
    monkeypatch.setattr(crontab, "del_repo", lambda: {
        "0 12 * * 1-5 run_job.sh mercado_1816_discovery 15m 'python -m jobs.mercado_1816_discovery --apply --catalogo'"})
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setattr(fuentes, "catalogo_1816_fecha", lambda: datetime(2026, 8, 15, 12, 5, tzinfo=UTC))
    (h,) = sistema.foto_1816({"gracia_min": 60})
    assert h.regla == "foto_vieja" and h.sujeto == "research.mkt_1816_instrumentos"
    assert h.severidad == "alta" and "mercado_1816_discovery" in h.que_hacer
    monkeypatch.setattr(fuentes, "catalogo_1816_fecha", lambda: datetime(2026, 9, 2, 12, 4, tzinfo=UTC))
    assert sistema.foto_1816({"gracia_min": 60}) == []


# ── EL PULSO DEL CLIENTE (§0.dg) ───────────────────────────────────────────

def test_vista_ciega_agrupa_por_vista_y_cruza_con_el_reinicio_de_la_api(monkeypatch):
    """De 37 pantallas que se refrescan solas, 3 le muestran el fallo a la
    persona y el servidor no se entera de ninguna. El pulso es lo único que
    le cuenta al agente lo que la mesa tiene enfrente, y el latido de la API
    es lo que convierte «ciega 4 min» en «coincide con el deploy de las 11:20»."""
    from datetime import datetime, timedelta

    from agente import fuentes, latencia, reloj
    ahora = datetime(2026, 9, 2, 11, 25, tzinfo=UTC)
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: ahora)
    monkeypatch.setattr(latencia, "comparar", lambda: [])
    t0 = ahora - timedelta(minutes=4)
    monkeypatch.setattr(fuentes, "pulsos", lambda m=10: [
        {"at": t0 + timedelta(minutes=1), "email": "a@x", "vista": "/agro",
         "endpoint": "/api/derivados-agro", "motivo": "HTTP 502", "desde_at": t0},
        {"at": t0 + timedelta(minutes=2), "email": "b@x", "vista": "/agro",
         "endpoint": "/api/derivados-agro", "motivo": "HTTP 502", "desde_at": t0},
        {"at": t0 + timedelta(minutes=3), "email": "a@x", "vista": "/renta-fija",
         "endpoint": "/api/cotizaciones/snapshot-live", "motivo": "error de red",
         "desde_at": t0 + timedelta(minutes=2)}])
    monkeypatch.setattr(fuentes, "latidos", lambda: {"api.main": {
        "arrancado_at": ahora - timedelta(minutes=5)}})
    por = {h.sujeto: h for h in sistema.vista_ciega({"pulso_ventana_min": 10})}
    assert por["/agro"].regla == "vista_ciega"
    assert "2 pantalla" in por["/agro"].problema and "reinicio de la API" in por["/agro"].problema
    assert por["/agro"].evidencia["personas"] == ["a@x", "b@x"]
    assert "/api/cotizaciones/snapshot-live" in por["/renta-fija"].detalle
    # Sin pulsos no hay nada; sin poder leer, no se afirma.
    monkeypatch.setattr(fuentes, "pulsos", lambda m=10: [])
    assert sistema.vista_ciega({}) == []
    monkeypatch.setattr(fuentes, "pulsos", lambda m=10: None)
    with pytest.raises(tipos.SinDatos):
        sistema.vista_ciega({})


def test_no_poder_mirar_una_fuente_no_apaga_las_otras(monkeypatch):
    """**«No pude mirar» no puede ser CONTAGIOSO** (§0.ex).

    Es el invariante #1 puesto al revés: si una corrida ciega no puede cerrar
    nada, tampoco puede apagar lo que SÍ se pudo ver. `latencia` leía dos
    tablas —`manager.latencia_endpoints` para los 5xx y la degradación, y
    `agente.pulso_cliente` para las pantallas ciegas— y el `SinDatos` de la
    segunda se llevaba puesta a la primera: los casos ya estaban calculados y
    se tiraban, y el motivo que quedaba en pantalla mandaba a mirar la tabla
    del navegador mientras lo que se caía era un endpoint.

    Es el MISMO error por el que `pantalla_tildada` ya se había separado de
    acá (§0.dm) — se corrigió una mitad y quedó la otra. Este test congela la
    forma, no el caso: **una habilidad, una fuente que puede faltar.**
    """
    from agente import fuentes
    from agente import latencia as maq

    # La fuente del pulso, MUERTA. Antes esto dejaba a `latencia` en sin_datos.
    monkeypatch.setattr(fuentes, "pulsos", lambda m=10: None)
    monkeypatch.setattr(maq, "comparar", lambda: [
        {"endpoint": "/api/back-office/tesoreria/dia", "n": 120, "avg_ms": 900,
         "base_ms": 700, "veces": 1.3, "max_ms": 4000, "errores": 12,
         "degradado": False, "roto": True}])
    hallazgos = sistema.latencia({})
    assert [h.regla for h in hallazgos] == ["errores"], (
        "los 5xx se apagaron porque no se pudo leer OTRA tabla")
    assert hallazgos[0].severidad == "alta"

    # Y la forma, que es lo que evita que vuelva: cada una lee UNA tabla.
    # ⚠️ Sobre el CÓDIGO (`_codigo`), no sobre el archivo: los dos docstrings
    # se nombran entre sí a propósito —explican el bug que evitan— y un test
    # que grepea la prosa castiga justo la documentación que hace falta.
    assert "pulsos" not in _codigo(sistema.latencia), (
        "`latencia` volvió a leer el pulso del cliente: son dos habilidades")
    assert "comparar" not in _codigo(sistema.vista_ciega), (
        "`vista_ciega` volvió a depender de la telemetría de endpoints")

    # Las dos están declaradas, y ninguna promete un botón que no tiene.
    for nombre in ("latencia", "vista_ciega"):
        assert not catalogo.HABILIDADES[nombre].arreglos, (
            f"«{nombre}» es un aviso: el agente no reinicia la API")


def test_la_api_late_y_el_pulso_tiene_puerta():
    """La API era el único proceso sin vigía: `uvicorn api.main:app` → proceso
    `api.main`, y `api/main.py` arranca su latido. El pulso entra por
    `/api/pulso`, sin módulo y sin invitado."""
    from agente import unidades
    from api.routers import pulso
    assert unidades.declaradas()["api"]["proceso"] == "api.main"
    main = (RAIZ / "api" / "main.py").read_text(encoding="utf-8")
    assert 'latido.arrancar("api.main")' in main
    rutas = {r.path for r in pulso.router.routes}
    assert "/api/pulso" in rutas
    assert "require_no_invitado" in inspect.getsource(pulso)
    from jobs import cleanup_retencion
    assert any(r.tabla == "agente.pulso_cliente" for r in cleanup_retencion.TABLAS)


# ── EXPLICÁMELO (§0.dh) ────────────────────────────────────────────────────

def test_explicar_manda_el_repo_y_no_solo_el_error(monkeypatch):
    """La IA sabe porque se le da el código, las fuentes que lee, el diario que
    ese código cita y el traceback entero — y cada respuesta viaja con esa
    lista. Sin contexto, «KeyError: 'x'» se explica adivinando."""
    from agente import explicar
    from core import ai, llm
    ctx = {"habilidad": "soberanos_faltantes", "que_mira": "…", "dominio": "MERCADO",
           "resultado": "error", "corrida_at": None,
           "error": "KeyError: 'fechaVencimiento'",
           "traceback": "Traceback…\n  File agente/detectores/mercado.py, line 90",
           "fuentes_que_lee": ["universo_1816", "master"], "codigo": "def soberanos…",
           "diario": "### 0.cy …", "corrida_job": None,
           "_fuentes": ["código: mercado.py::soberanos_faltantes", "diario: §0.cy"]}
    monkeypatch.setattr(explicar, "contexto", lambda n: ctx)
    monkeypatch.setattr(explicar, "_cache", lambda h: None)
    guardadas = []
    monkeypatch.setattr(explicar, "_guardar", lambda *a: guardadas.append(a))
    monkeypatch.setattr(llm, "configurado", lambda p=None: True)
    monkeypatch.setattr(llm, "modelo", lambda tier, nombre=None: "flash-x")
    pedidos = []
    def _completar(tarea, *, system, user, usuario=None, detalle=None):
        pedidos.append((tarea, user))
        return ('```json\n{"de_quien": "dato", "explicacion": "1816 mandó un instrumento '
                'sin vencimiento.", "afecta": "solo esa habilidad", "que_hacer": "saltearlo", '
                '"test": "", "tarea": {"titulo": "Tolerar instrumento sin vencimiento", '
                '"prompt": "en agente/detectores/mercado.py …"}}\n```')
    monkeypatch.setattr(ai, "completar", _completar)
    r = explicar.explicar("soberanos_faltantes", por="nico@x")
    assert r["ok"] and r["respuesta"]["de_quien"] == "dato"
    assert r["fuentes"] == ctx["_fuentes"]
    tarea, user = pedidos[0]
    assert tarea == "explicar_error"
    assert "fechaVencimiento" in user and "### 0.cy" in user and "_fuentes" not in user
    assert guardadas and guardadas[0][0] == explicar._hash(
        "soberanos_faltantes", ctx["error"], ctx["traceback"])
    # Cacheada: el mismo error no se paga dos veces.
    monkeypatch.setattr(explicar, "_cache", lambda h: {"respuesta": {"explicacion": "ya"},
                                                        "fuentes": [], "modelo": "m",
                                                        "por": "a", "at": "t"})
    pedidos.clear()
    r = explicar.explicar("soberanos_faltantes", por="otro")
    assert r["ok"] and r["cacheada"] and not pedidos
    # Sin IA configurada, se dice; sin error que explicar, también.
    monkeypatch.setattr(explicar, "_cache", lambda h: None)
    monkeypatch.setattr(llm, "configurado", lambda p=None: False)
    assert not explicar.explicar("soberanos_faltantes")["ok"]
    monkeypatch.setattr(explicar, "contexto", lambda n: {**ctx, "error": "", "traceback": ""})
    assert "ningún error" in explicar.explicar("soberanos_faltantes")["error"]
    # La tarea está registrada en el gateway y el motor guarda el traceback.
    assert "explicar_error" in ai._TAREAS
    assert "traceback=" in inspect.getsource(registro.guardar)
    assert "ultimo_traceback" in inspect.getsource(registro.sellar_corrida)


def test_parsear_tolera_el_cerco_y_rechaza_lo_que_no_es_la_forma():
    from agente import explicar
    assert explicar._parsear('```json\n{"explicacion": "x", "de_quien": "raro"}\n```')["de_quien"] == "no_se"
    assert explicar._parsear("hola") is None
    assert explicar._parsear('{"otra": 1}') is None


# ── TRAJO POCO (§0.dk) ───────────────────────────────────────────────────────

def test_trajo_poco_helpers_con_los_numeros_reales_del_diag():
    """Los números son los del diag del 2026-09-02, no inventados."""
    from agente.detectores.datos import _encogido_acumulado, _encogido_diario

    k = {"corte": 0.5, "min_corridas": 5, "minimo_referencia": 20}
    aum = [1062, 1062, 1057, 1057, 1051, 1049, 1044, 1046, 1046, 1051]
    assert _encogido_diario(1061, aum, **k)["encogido"] is False
    assert _encogido_diario(400, aum, **k)["encogido"] is True
    assert _encogido_diario(1061, aum[:3], **k) is None, "poca historia: no opina"
    assert _encogido_diario(1, [3, 3, 2, 2, 3, 3], **k) is None, "referencia chica: no opina"
    # interbanking 01/09: 382 a las 15:01 y 0 a las 17:00, con estado ok
    k2 = {"corte": 0.5, "minimo_referencia": 20}
    assert _encogido_acumulado(0, 382, **k2)["encogido"] is True
    assert _encogido_acumulado(360, 382, **k2)["encogido"] is False
    assert _encogido_acumulado(2536, None, **k2) is None, "primera del día: no opina"


def test_trajo_poco_compara_por_modo_y_saltea_las_corridas_en_seco(monkeypatch):
    from datetime import datetime, timedelta

    from agente import fuentes, reloj
    from agente.detectores import datos
    from agente.reportes import VOLUMENES

    monkeypatch.setattr(reloj, "hhmm", lambda a=None: "12:00")
    t0 = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)   # 11:00 ART

    def corrida(minutos_atras, status, **stats):
        return {"finished_at": t0 - timedelta(minutes=minutos_atras), "status": status,
                "stats": stats}

    series = {
        # diario: hoy 400 contra una mediana de ~1050 → encogido
        "aum": [corrida(0, "ok", cuentas_ok=400)]
               + [corrida(1440 * (i + 1), "ok", cuentas_ok=1050 + i) for i in range(10)],
        # acumulado: 0 después de 382 el mismo día → encogido; la corrida en seco
        # del medio se saltea y no tapa la comparación
        # 245 → 7 → 0 el mismo día (28/08 real): el 0 se compara contra el 245,
        # la última SANA, y no contra el 7 — si no, cerraba el aviso por ausencia
        "interbanking_sync": [corrida(0, "ok", movimientos=0),
                              corrida(30, "ok", movimientos=0, modo="dry"),
                              corrida(90, "ok", movimientos=7),
                              corrida(120, "ok", movimientos=245),
                              corrida(1500, "ok", movimientos=263)],
        # acumulado: primera corrida del día (la anterior es de ayer) → no opina
        "negocio_movimientos": [corrida(0, "ok", boletos=1200),
                                corrida(1000, "ok", boletos=2811)],
        # el contador declarado no está → volumen_sin_dato
        "snapshot_cierre": [corrida(0, "ok", otra_cosa=5)],
    }
    monkeypatch.setattr(fuentes, "corridas", lambda job, n=15: series.get(job, []))

    por = {(h.sujeto, h.regla): h for h in datos.trajo_poco(
        {"corte": 0.5, "min_corridas": 5, "minimo_referencia": 20, "ventana": 10})}
    assert por[("aum", "volumen_encogido")].evidencia["modo"] == "diario"
    assert por[("interbanking_sync", "volumen_encogido")].evidencia["referencia"] == 245.0
    assert ("negocio_movimientos", "volumen_encogido") not in por
    assert por[("snapshot_cierre", "volumen_sin_dato")].severidad == "baja"
    assert all(k[0] in {v.job for v in VOLUMENES} for k in por)
    for h in por.values():
        assert h.que_hacer and "12:00" in h.problema

    monkeypatch.setattr(fuentes, "corridas", lambda job, n=15: None)
    with pytest.raises(tipos.SinDatos):
        datos.trajo_poco({})

    h = catalogo.HABILIDADES["trajo_poco"]
    assert h.dominio == "DATOS" and h.ventana == "habil" and not h.arreglos
    assert set(h.umbrales) == {"corte", "min_corridas", "minimo_referencia", "ventana"}



# ── LA CADUCIDAD (2026-09-04) ──────────────────────────────────────────────

def test_no_encontrar_el_sujeto_no_es_prueba_de_que_no_exista():
    """**La guarda que hace que caducar no sea peligroso** (invariante 1, un
    nivel más abajo).

    Un hallazgo se puede cerrar por CADUCIDAD sólo si una fuente **afirma** que
    el sujeto murió, con fecha. Que el ticker no aparezca en ninguna tabla es
    «no sé» — y no sé no cierra nada. Sin esto, el día que una fuente devuelva
    vacío el agente caducaría todo lo abierto de golpe y dejaría el tablero en
    verde justo cuando está ciego: el bug más caro que puede tener una
    herramienta de integridad, con cara de feature nueva.

    Por eso `existe` es de TRES valores y la pregunta se hace con `.muerto`:
    `not v.existe` daría `True` para «no sé» y convertiría la guarda en su
    contrario.
    """
    from agente import vigencia

    assert vigencia.Veredicto(None).muerto is False, (
        "«no sé» no puede habilitar una caducidad")
    assert vigencia.Veredicto(True).muerto is False
    assert vigencia.Veredicto(False, "venció", "x").muerto is True
    assert vigencia.NO_SE.existe is None

    # Y la pregunta se hace por `.muerto`, no negando `existe`.
    src = _codigo(registro._caducados) + _codigo(vigencia.muertos)
    assert "not v.existe" not in src and "not veredicto.existe" not in src


def test_dos_fuentes_que_se_contradicen_no_caducan_nada():
    """**«Uno dice muerto y otro dice vivo» tampoco es muerte** (REGLA #9).

    Lo encontró la PRIMERA corrida real, y es el bug que este módulo existía para
    evitar, un nivel más arriba: la versión original se cuidaba de que «no lo
    encontré» no fuera muerte, y tomaba el primer «está muerto» sin mirar si otra
    fuente afirmaba lo contrario.

    GMCGO, medido en producción el 2026-09-04: `mercado.curvas` dice que vence el
    2028-01-28 —una afirmación de que está VIVO— y `portafolio.assets` lo tiene de
    baja con fecha 2026-06-28. Diecinueve meses de diferencia, las dos copias
    cargadas, ninguna arbitrando. El código saltaba la primera rama (2028 no es
    pasado), entraba por la segunda, y **daba por muerto un título que el master
    declara vivo**.

    Una fecha de vencimiento FUTURA es una afirmación tan válida como la que dice
    que murió. Dos copias sin árbitro no habilitan a cerrar nada.
    """
    from datetime import date, timedelta
    from unittest.mock import MagicMock

    from agente import vigencia

    ayer = date.today() - timedelta(1)
    manana = date.today() + timedelta(1)
    filas = [
        # (ticker, vto_master, vto_1816, todos_de_baja, motivo)
        ("GMCGO",   date(2028, 1, 28), None,   True,  "vencido"),  # el caso real
        ("CRUZADO", ayer,              manana, None,  None),       # al revés
        ("ACUERDO", ayer,              ayer,   True,  "vencido"),  # coinciden
        ("VIVO",    manana,            None,   None,  None),
    ]
    cur = MagicMock()
    cur.fetchall.return_value = filas
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    r = vigencia._bonos(conn, [f[0] for f in filas])
    assert r["GMCGO"].existe is None and not r["GMCGO"].muerto, (
        "el master lo declara vivo hasta 2028: contradecir a `assets` es «no sé»")
    assert r["CRUZADO"].existe is None, "la contradicción vale en las dos direcciones"
    assert r["ACUERDO"].muerto, "si las fuentes coinciden en que murió, sí caduca"
    assert r["VIVO"].existe is True


def test_una_fuente_que_no_contesta_no_caduca_nada():
    """Si `vigencia` no puede leer, la respuesta es NINGUNO — ni «todos vivos»
    ni «todos muertos». Y no puede tirar abajo la escritura de la corrida: va en
    un SAVEPOINT porque en psycopg un error deja la transacción entera abortada,
    y una función que sólo agrega información no puede romper la que ya andaba.
    """
    from agente import vigencia

    src = _codigo(vigencia.muertos)
    assert "with conn.transaction():" in src, "la verificación va en un SAVEPOINT"
    assert "return {}" in src[src.index("except"):], (
        "si no pude verificar, no caduca ninguno")


def test_lo_caducado_no_puede_reincidir():
    """**Un bono vencido que «vuelve» no significa nada** (invariante 4).

    `_ver` sólo busca un cierre POR ACCIÓN para decidir si hay reincidencia, así
    que la caducidad queda afuera por construcción — igual que la ausencia. Este
    test congela esa construcción: el día que alguien amplíe ese `WHERE` a
    cualquier cierre, la tabla que debe estar vacía se llena de sujetos muertos.

    Es exactamente cómo nació su primera fila (M31G6, 28/08): se dio de alta un
    bono que vencía, `cleanup_curvas` lo borró por vencer, el detector lo vio
    faltar de nuevo — y el cierre por ACCIÓN lo habilitó a reincidir.
    """
    # ⚠️ Se mira el ÚLTIMO cierre, no «alguno por acción». Pedir el más reciente
    # ENTRE los cerrados por acción se saltea lo que pasó después: un bono
    # arreglado en marzo, caducado en agosto y visto de nuevo en septiembre
    # encontraba el cierre de marzo y fabricaba una reincidencia sobre un sujeto
    # que el propio agente había declarado muerto. Una garantía que un `ORDER BY`
    # puede resucitar no es una garantía.
    src = _codigo(registro._ver)
    assert "cerrado_como = ANY(%s)" in src, (
        "se piden los dos cierres que afirman algo y gana el más nuevo")
    assert "previo[3] != tipos.POR_ACCION" in src, (
        "si el último cierre NO fue por acción, no hay reincidencia")
    assert "tipos.POR_CADUCIDAD" in src and "tipos.POR_ACCION" in src

    # Y la caducidad le gana a la acción al CERRAR: si no, un sujeto muerto que
    # tenía arreglo aplicado quedaría cerrado por acción y habilitado a reincidir.
    cierre = _codigo(registro._cerrar_ausentes)
    assert cierre.index("POR_CADUCIDAD") < cierre.index("POR_ACCION"), (
        "el UPDATE de caducidad va ANTES del de acción/ausencia")


def test_una_corrida_no_puede_caducar_medio_tablero():
    """El tope. Caducar 40 hallazgos de una es más probable que sea una fuente
    rota que 40 bonos venciendo el mismo día.

    Y cuando se pasa **no se corta la corrida**: los que sobran se cierran por
    la vía de siempre (ausencia, que dice menos pero no miente). Frenar del todo
    dejaría hallazgos abiertos sobre sujetos muertos para siempre.
    """
    from agente import vigencia

    assert 0 < vigencia.TOPE_POR_CORRIDA <= 25
    src = _codigo(registro._caducados)
    assert "TOPE_POR_CORRIDA" in src and "break" in src
    assert "logger.warning" in src, "pasarse del tope tiene que quedar escrito"


def test_toda_caducidad_deja_su_fundamento_en_el_libro():
    """**Caducar es el agente escribiendo una decisión que nadie le pidió.**

    Sin la línea del libro sería un `except: pass` con mejor prensa. Y no
    alcanza con «caducó»: van el MOTIVO y la FUENTE, o no es trazabilidad, es un
    log. Se anota con el MISMO `conn` que el cierre — por `anotar_accion`, que
    abre el suyo, una transacción caída dejaría una línea diciendo que caducó
    algo que sigue abierto.
    """
    # `_codigo` y no `getsource`: el docstring de la función NOMBRA
    # `anotar_accion` para explicar por qué no la usa, y un test que castiga
    # documentar no prueba nada (es la misma trampa de `_codigo`, arriba).
    src = _codigo(registro._anotar_caducidad)
    assert "INSERT INTO agente.acciones" in src
    assert "veredicto.motivo" in src and "veredicto.fuente" in src
    assert "anotar_accion" not in src, "va con el conn de la corrida"
    assert "_anotar_caducidad" in _codigo(registro._caducados)


def test_una_habilidad_no_caduca_si_no_declaro_de_que_habla():
    """Sin `sujeto_es` no hay caducidad: es el default seguro, y es una
    declaración explícita como el `arreglos` vacío.

    Y un tipo inventado NO puede fallar callado: `vigencia` no lo encontraría en
    su registro, no verificaría nada, y la habilidad no caducaría nunca — sin un
    error, sin un log, sin nada que mirar. El dataclass lo rechaza al construir.
    """
    from agente import tipos, vigencia

    assert set(tipos.SUJETOS) == set(vigencia.VERIFICADORES), (
        "el vocabulario y los verificadores tienen que decir lo mismo: un tipo "
        "declarado sin verificador no caduca nada, callado")

    for h in catalogo.HABILIDADES.values():
        assert h.sujeto_es in ("", *tipos.SUJETOS)

    with pytest.raises(ValueError):
        tipos.Habilidad(nombre="x", tipo="detector", dominio="MERCADO",
                        que_mira="x", cada_segundos=60, correr=lambda u: [],
                        sujeto_es="pantalla")


def test_la_reincidencia_se_apaga_con_su_hallazgo():
    """**La tabla que DEBE estar vacía tenía que poder vaciarse.**

    `agente.reincidencias` sólo recibe INSERT: no existe una línea que cierre
    una fila. Leerla entera dejaba el cartel rojo prendido para siempre — M31G6
    volvió el 28/08, el detector se corrigió ese mismo día, el hallazgo se
    cerró, y el cartel siguió arriba de la pantalla una semana describiendo un
    bono que ya venció.

    Es el mismo defecto que el propio agente evita en el latido: «un círculo que
    está en rojo cuando todo está bien enseña a ignorar el círculo». La fila
    número 16 —la que importaba— no la mira nadie.

    ⚠️ Y el criterio es UNO. Se cuenta en TRES lugares (el modal, el panel de
    HABILIDADES y los casos del lab) y los tres tienen que decir lo mismo, o es
    la REGLA #9 adentro del agente: nada falla, cada pantalla muestra otro
    número.
    """
    from agente import vista

    src = _codigo(vista.reincidencias)
    assert "JOIN agente.hallazgos" in src and "tipos.ABIERTOS" in src

    sql = (RAIZ / "sql" / "schema.sql").read_text()
    i = sql.index("CREATE OR REPLACE VIEW agente.v_habilidades")
    vista_sql = sql[i:sql.index(";", sql.index("ORDER BY h.dominio", i))]
    j = vista_sql.index("FROM agente.reincidencias")
    assert "JOIN agente.hallazgos" in vista_sql[j:j + 400], (
        "el ⚠ del panel de HABILIDADES cuenta las reincidencias ACTIVAS")

    lab = (RAIZ / "lab" / "langgraph" / "cola.py").read_text()
    assert "JOIN agente.hallazgos h ON h.id = r.hallazgo_id" in lab


# ── EL INVESTIGADOR: las horas (2026-09-04) ────────────────────────────────

def test_ninguna_herramienta_del_lab_devuelve_una_hora_sin_huso():
    """**Una hora sin huso declarado es una hora que se va a mezclar con otra.**

    Postgres guarda `timestamptz` y `to_char` lo devuelve en el huso de la
    sesión (UTC en el Droplet). Las pantallas muestran ART. El investigador leía
    UTC de las tablas y ART del modal, y armaba un solo relato con las dos.

    Pasó investigando la caída de AUNESA: «15:35 se anotó una nueva falla» (UTC)
    y «12:41 el detector emitió el aviso» (ART) son **el mismo evento**, contado
    dos veces y en un orden imposible. La conclusión igual salió bien — por
    casualidad, que es la peor forma de acertar.

    Dos cosas se congelan acá: que la conversión viva en UN solo lugar
    (`_hora`), y que el NOMBRE de cada columna diga el huso — el modelo lee los
    nombres, y `desde` no le dice nada mientras que `desde_art` sí.
    """
    # ⚠️ Se lee el ARCHIVO, no se importa el módulo: `datos.py` importa
    # `langchain_core`, y un test de la forma del SQL no puede depender de que
    # el laboratorio esté instalado. Si mañana el lab no se instala, este test
    # tiene que seguir diciendo la verdad sobre el código que hay en el repo.
    codigo = _codigo((RAIZ / "lab" / "langgraph" / "datos.py").read_text())
    assert codigo.count("to_char(") == 1, (
        "la conversión de hora vive en `_hora()` y en ningún otro lado: seis "
        "consultas con su propio `to_char` son seis oportunidades de que una "
        "quede en UTC, y el día que pase no falla nada — cambia el relato")
    assert "AT TIME ZONE" in codigo

    # Toda columna de hora se llama `_art`. Se mira el SQL, no el archivo.
    import re
    # ⚠️ `ast.unparse` deja el f-string como `{_hora('x')} AS alias`: el cierre
    # es la llave del f-string, no un paréntesis. Buscar `)` acá daba cero
    # matches y el test pasaba... hasta el assert que lo cazó.
    alias_de_hora = re.findall(r"_hora\('(\w+)'\)\}\s*AS\s+(\w+)", codigo)
    assert alias_de_hora, "no encontré los alias — ¿cambió la forma de `_hora`?"
    sin_huso = [a for _, a in alias_de_hora if not a.endswith("_art")]
    assert not sin_huso, f"columnas de hora sin el huso en el nombre: {sin_huso}"

    # Y el prompt se lo dice al modelo, que es quien las junta.
    grafo = (RAIZ / "lab" / "langgraph" / "grafo.py").read_text()
    assert "_art" in grafo and "ART" in grafo, (
        "el prompt tiene que declarar el huso: el que arma la cronología es el "
        "modelo, no la consulta")

    # El error crudo tiene que estar: es lo único que dice DE QUIÉN es el problema.
    i = codigo.index("def hallazgos_del_sujeto")
    assert "detalle" in codigo[i:i + 900], (
        "`hallazgos_del_sujeto` sin `detalle` deja al investigador escribiendo "
        "«no pude leer el error crudo» con el error en la fila de al lado")


# ── EL TRIAGE — a qué vale la pena ir a investigar (2026-09-04) ────────────

def test_no_se_investiga_lo_que_se_acaba_de_caer():
    """**La regla del triage, y la dijo el user:**

    > *«el agente tranquilamente puede ver 5 minutos después si eso ya funciona
    > y listo — esto tiene que ser cuando se termina de caer del todo»*

    `proveedor_caido` corre cada 5' y le alcanza UN fallo para cantar. Aunesa se
    cayó 12:35, el hallazgo nació 12:41, y a la tarde ya no existía: se había
    recuperado solo. Disparar en el momento del hallazgo habría pagado una
    investigación entera (8 a 18 llamadas al modelo) de algo que se arregló sin
    que nadie hiciera nada.

    No hace falta un reloj para medirlo: el detector ya vuelve a mirar, y si el
    problema se fue el hallazgo se cierra. **Lo que sobrevive es lo real.**
    """
    from agente import triage

    # Toda regla declarada tiene una espera, y el dataclass la exige.
    declaradas = triage._declaradas()
    assert declaradas, "el triage no tiene ninguna regla declarada"
    for hab, regla, seg in declaradas:
        assert seg >= 300, f"«{hab}/{regla}» dispara a los {seg}s"

    # Y el piso no depende de que alguien se acuerde: no se puede construir.
    with pytest.raises(ValueError):
        tipos.Habilidad(nombre="x", tipo="detector", dominio="SISTEMA",
                        que_mira="x", cada_segundos=60, correr=lambda u: [],
                        investigar={"r": 0})
    with pytest.raises(ValueError):
        # El error de tipeo obvio: minutos donde van segundos.
        tipos.Habilidad(nombre="x", tipo="detector", dominio="SISTEMA",
                        que_mira="x", cada_segundos=60, correr=lambda u: [],
                        investigar={"r": 20})

    # La consulta exige que el hallazgo haya sobrevivido su espera.
    src = _codigo(triage.candidatos)
    assert "make_interval" in src and "detectado_at <=" in src


def test_el_triage_elige_pero_no_conoce_al_investigador():
    """`agente/` no puede depender del laboratorio: si el lab no está instalado,
    el agente tiene que detectar exactamente igual.

    Por eso son dos piezas: `triage.candidatos()` ELIGE y no sabe que existe un
    investigador; el lab INVESTIGA y no sabe que existe un agente. Las junta
    `jobs/agente.py`, que es el único que conoce las dos mitades — y ahí el
    import va adentro del `try`, como el de `_atender_investigaciones`.
    """
    for f in (RAIZ / "agente").rglob("*.py"):
        t = _codigo(f.read_text())
        assert "lab.langgraph" not in t and "from lab" not in t, (
            f"{f.name} importa el laboratorio: el agente tiene que funcionar "
            "sin él")

    daemon = _codigo((RAIZ / "jobs" / "agente.py").read_text())
    i = daemon.index("def _disparar_investigaciones")
    cuerpo = daemon[i:i + 2000]
    assert "try:" in cuerpo[:cuerpo.index("from lab")], (
        "el import del lab va DENTRO del try: un ImportError al arrancar "
        "mataría el daemon entero")


def test_el_triage_tiene_un_techo_de_plata_que_se_puede_contar():
    """Investigar cuesta. El tope va DECLARADO, y si no se puede contar lo
    gastado **no se gasta**: un tope que no se puede contar no es un tope.

    Y el «quién lo pidió» es una constante y no un literal suelto — el tope se
    cuenta filtrando por ese mismo string, así que dos grafías distintas harían
    que el tope no cuente lo que gastó.
    """
    from agente import triage

    assert 0 < triage.TOPE_DIARIO <= 20
    assert triage.NO_REPETIR_H >= 1

    daemon = _codigo((RAIZ / "jobs" / "agente.py").read_text())
    assert "POR_EL_TRIAGE" in daemon
    assert daemon.count("'agente/triage'") <= 1, (
        "el autor del pedido va por la constante, no repetido a mano")
    i = daemon.index("def _disparar_investigaciones")
    cuerpo = daemon[i:i + 2000]
    assert "gastadas is None" in cuerpo and "return" in cuerpo, (
        "si no puedo contar lo gastado, no disparo")
    assert "TOPE_DIARIO" in cuerpo and "no_repetir_h" in cuerpo


def test_el_tipo_de_investigacion_no_se_declara_dos_veces():
    """**REGLA #9 adentro del triage.** Qué investigación le corresponde a cada
    habilidad ya vive en `lab.langgraph.investigaciones.DE_LA_HABILIDAD`, que es
    quien sabe qué sabe investigar.

    Si el catálogo del agente también lo dijera, serían dos mapas del mismo
    hecho sin árbitro — y el que se desincronice no falla: manda a investigar
    con el método equivocado. Acá se declara QUÉ reglas y CUÁNTO aguantan, y
    nada más.
    """
    lab = (RAIZ / "lab" / "langgraph" / "investigaciones.py").read_text()
    assert "DE_LA_HABILIDAD" in lab

    for h in catalogo.HABILIDADES.values():
        for regla, valor in (h.investigar or {}).items():
            assert isinstance(valor, int), (
                f"«{h.nombre}/{regla}» declara {valor!r}: el valor es la ESPERA "
                "en segundos. El tipo de investigación lo dice el lab")

    daemon = _codigo((RAIZ / "jobs" / "agente.py").read_text())
    assert "tipo_de(" in daemon, "el tipo sale del lab, no del catálogo"


# ── AGUDO vs CRÓNICO (2026-09-04) ──────────────────────────────────────────

def test_un_problema_cronico_no_se_ve_igual_que_uno_nuevo():
    """**La pregunta que decide QUÉ hacer con un hallazgo**, y que el agente no
    se hacía.

    Miraba cada hallazgo AISLADO, así que un job que no escribió HOY y uno que
    no escribe TODOS LOS DÍAS se veían idénticos — y los dos terminaban en
    «relanzá el job». Para el primero está bien; para el segundo, relanzar ES el
    parche: lo que hay que revisar es el umbral, el cron, o si el job sigue
    haciendo falta. El user (2026-09-04): *«el agente debe poder buscar mejoras
    y potenciar lo que puede llegar a haber mal, no dejar todo como está y
    parchear»*.

    Un EPISODIO es una vez que el problema NACIÓ, no una vez que se lo VIO
    (eso es `veces`, y sube sin crear fila). Confundirlos daría «crónico» a
    cualquier problema que lleve un rato abierto.
    """
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from agente import tipos, vista

    assert tipos.EPISODIOS_CRONICO >= 3, (
        "dos veces en un mes puede ser casualidad; el piso es tres")
    assert tipos.VENTANA_CRONICO_D >= 7

    filas = [{"habilidad": "tabla_quieta", "sujeto": s, "regla": "sin_escribir"}
             for s in ("cronica", "repetida", "nueva")]
    cur = MagicMock()
    cur.fetchall.return_value = [
        ("tabla_quieta", "cronica", "sin_escribir", 27, datetime(2026, 8, 6, tzinfo=UTC)),
        ("tabla_quieta", "repetida", "sin_escribir", 2, datetime(2026, 8, 6, tzinfo=UTC)),
        ("tabla_quieta", "nueva", "sin_escribir", 1, datetime(2026, 9, 4, tzinfo=UTC)),
    ]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn

    with patch.object(vista, "get_pool", return_value=pool):
        r = {f["sujeto"]: f for f in vista._con_historial([dict(f) for f in filas])}
    assert r["cronica"]["cronico"] and r["cronica"]["episodios"] == 27
    assert not r["repetida"]["cronico"], "dos no alcanza"
    assert not r["nueva"]["cronico"]
    assert r["nueva"]["episodios_desde"], "sin la fecha no se sabe desde cuándo viene"

    # Y se cuenta con UNA query para toda la lista, no una por fila: AHORA puede
    # traer cuarenta, y cuarenta consultas para lo mismo es cómo una pantalla se
    # vuelve lenta sin que nadie sepa por qué.
    assert cur.execute.call_count == 1


def test_no_poder_contar_los_episodios_no_es_decir_que_es_la_primera_vez():
    """**El invariante 1, en la pantalla.**

    Si la consulta del historial falla, cada fila queda con `episodios = None` y
    `cronico = False` — y el front NO dibuja nada. Poner «1ª vez» porque no se
    pudo contar sería afirmar lo contrario de la verdad justo cuando el sistema
    está más ciego, que es la mentira más cara que puede decir una herramienta
    de integridad.

    Y no puede tirar abajo la pantalla: el resto de la fila ya estaba.
    """
    from unittest.mock import MagicMock, patch

    from agente import vista

    roto = MagicMock()
    roto.connection.side_effect = RuntimeError("Supabase caído")
    filas = [{"habilidad": "x", "sujeto": "y", "regla": "z", "problema": "algo"}]
    with patch.object(vista, "get_pool", return_value=roto):
        r = vista._con_historial(filas)
    assert r[0]["episodios"] is None, "«no sé» no puede ser un número"
    assert r[0]["cronico"] is False, "ante la duda, la rama que no afirma nada"
    assert r[0]["problema"] == "algo", "la fila sigue entera"

    # Y el front lo respeta: sin dato, no dibuja el indicador.
    front = (RAIZ.parent / "acaquant-frontend" / "src" / "components" / "agente"
             / "evidencia.tsx")
    if front.exists():
        assert "episodios == null" in front.read_text(), (
            "el componente tiene que saltear el caso «no pude contar»")


def test_hay_UNA_definicion_de_cronico_y_la_leen_todos():
    """**REGLA #9, y la lección ya la pagamos tres veces hoy** — con el conteo
    de reincidencias, que se calculaba en tres lugares y decía cosas distintas.

    «Crónico» son tres cosas: el umbral de episodios, la ventana, y la CONSULTA
    que los cuenta. Si la terminal tuviera su propia copia de cualquiera de las
    tres, el día que se toque una el diag y la pantalla darían números distintos
    sobre el mismo problema — y ninguno fallaría.

    Por eso `scripts/diag_agente` no consulta: **llama a `vista.cronicos()` y
    sólo dibuja**. El umbral tampoco se copia al schema.
    """
    from agente import vista

    sql = (RAIZ / "sql" / "schema.sql").read_text()
    assert "EPISODIOS_CRONICO" not in sql and "make_interval(days => 30)" not in sql, (
        "el umbral no se copia al schema: lo calculan `vista._con_historial` y "
        "`vista.cronicos`")

    diag = _codigo((RAIZ / "scripts" / "diag_agente.py").read_text())
    assert "vista.cronicos(" in diag, "el diag LEE la lista, no la arma"
    assert "percentile_cont" not in diag and "HAVING count(*)" not in diag, (
        "el diag no puede tener su propia consulta de crónicos: dos consultas "
        "son dos definiciones, y la que se desincronice no falla — miente")

    # Y la función devuelve los umbrales con los que contó, para que la pantalla
    # pueda decir «3+ en 30 días» sin repetir los números.
    for k in ("ventana_dias", "dias_activo", "desde_episodios"):
        assert k in _codigo(vista.cronicos), (
            f"`cronicos()` tiene que devolver «{k}»: si el front lo hardcodea, "
            "el día que cambie el umbral la leyenda va a mentir")


# ── EL REDACTOR (§0.dn) ────────────────────────────────────────────────────
#
# Lo que estos tests sostienen no es que el texto salga lindo —eso no lo puede
# afirmar un test— sino las cuatro cosas que hacen que un texto feo no cueste
# nada: que el modelo no decida, que el piso no se pise, que el alcance se
# derive y que la salida se valide antes de mostrarse.

def test_solo_los_avisos_se_redactan():
    """**El alcance se DERIVA, no se lista** (REGLA #10).

    Un hallazgo con arreglo ya dice qué hacer: apretar el botón. Ahí el modelo
    no puede agregar nada y sí puede contradecirlo, que es peor que no estar.
    Una lista de nombres se desactualiza el día que alguien suma una habilidad;
    esta regla no puede.
    """
    con, sin = [], []
    for h in catalogo.HABILIDADES.values():
        for regla, arreglo in (h.arreglos or {}).items():
            assert not redactar.alcanza(h.nombre, regla), (
                f"{h.nombre}/{regla} tiene el arreglo «{arreglo}» y aun así "
                f"entraría al redactor")
            con.append(regla)
        if not h.arreglos:
            sin.append(h.nombre)
    # Y al revés: una habilidad sin ningún arreglo tiene que entrar, o el
    # mecanismo no sirve para las 16 que son puro aviso.
    assert sin, "el catálogo no tiene habilidades sin arreglo: el test no mide nada"
    for nombre in sin:
        assert redactar.alcanza(nombre, "cualquier_regla"), (
            f"{nombre} no tiene arreglo y no se redactaría")
    assert con, "ninguna habilidad declara arreglo: el test no mide nada"


def test_el_piso_nunca_se_pisa():
    """`que_hacer` es el texto determinista y **se conserva entero**.

    Es lo que hace que meter un modelo acá no pueda agregar un modo de falla:
    si el gateway no contesta, si no hay presupuesto o si la validación
    rechaza, el aviso muestra lo de siempre. Un diseño donde el modelo escribe
    ENCIMA convierte cada caída del proveedor en una fila muda.
    """
    src = inspect.getsource(registro.guardar_texto_ia)
    update = src[src.index("UPDATE agente.hallazgos"):]
    for prohibida in ("que_hacer", "severidad", "estado", "arreglo",
                      "cerrado", "evidencia", "problema"):
        assert prohibida not in update, (
            f"el redactor escribe `{prohibida}`: sólo puede tocar columnas ia_*")
    assert "ia_intentos = ia_intentos + 1" in update, (
        "sin contar los intentos, un hallazgo que siempre se rechaza se paga "
        "en cada pasada para siempre")


def test_el_redactor_no_escribe_ni_conoce_detectores():
    """Un solo prompt para todas las habilidades de hoy, y para la que se sume.

    Un prompt por habilidad sería la misma frase de molde de vuelta, escrita en
    otro archivo y encima pagándola. Lo que varía lo aportan el hallazgo y el
    `que_mira` del catálogo, que ya está declarado en castellano.
    """
    # ⚠️ El CÓDIGO, no la prosa: el docstring del módulo cita
    # `api/services/salud.py`, y «salud» es el nombre de una habilidad. Un test
    # que grepea el archivo entero castiga documentar (ver `_codigo`).
    src = _codigo((RAIZ / "agente" / "redactar.py").read_text())
    for nombre in catalogo.HABILIDADES:
        assert nombre not in src, (
            f"el redactor nombra la habilidad «{nombre}»: eso lo vuelve un "
            f"parche por detector en vez de un mecanismo")
    assert "detectores" not in src, "el redactor importa detectores"
    # La escritura entra por la puerta única (invariante #5); acá sólo se deja
    # explícito que este módulo es PURO.
    assert "get_pool" not in src and "cur.execute" not in src, (
        "el redactor toca la base: eso es de registro.py")


def test_la_validacion_rechaza_lo_berreta():
    """**La guarda que hace la diferencia**: se valida contra los MISMOS hechos
    que vio el modelo.

    Un texto lindo con un número inventado es peor que la frase de molde: la
    frase de molde no informa, el número inventado desinforma con la autoridad
    de un dato.
    """
    fila = {"habilidad": "db_peso", "sujeto": "la base", "regla": "peso_total_11",
            "problema": "la base pesa 12 GB en 190 tablas",
            "que_hacer": "Nada: es el número del día.",
            "evidencia": {"top": [{"tabla": "mercado.market_snapshot", "bytes": 4400}]}}
    h = redactar.hechos(fila)

    bueno = "El salto lo puso mercado.market_snapshot, que sola pesa 4400 de los 12 GB."
    assert redactar.revisar(bueno, h, fila) == "", "rechaza un texto correcto"

    malos = {
        "número inventado": "Creció 37 por ciento contra la semana pasada.",
        "muletilla": "Se recomienda monitorear el crecimiento de las tablas.",
        # ⚠️ Los backticks sueltos YA NO se rechazan: los borra `limpiar()`.
        # Tirar un texto correcto por una comilla es pagar la llamada para
        # mostrar el piso. Lo que sí se rechaza es lo que cambia el sentido.
        "bloque de código": "El salto lo puso ```mercado.market_snapshot```.",
        "muy corto": "Creció.",
        "calco del problema": "La base pesa 12 GB en 190 tablas.",
        "el piso de vuelta": "Nada: es el numero del dia.",
    }
    for caso, texto in malos.items():
        assert redactar.revisar(texto, h, fila), f"dejó pasar: {caso} → {texto!r}"

    largo = "mercado.market_snapshot " * 20      # una sola «oración» de 480
    assert "entra" in redactar.revisar(largo, h, fila)


def test_no_manda_a_destruir_datos_ni_escala_un_aviso():
    """**El primer texto que salió a producción, congelado como test.**

    El modelo convirtió un «Nada: es el número del día» en *«purgá o archivá
    las cinco tablas»* — que eran las cinco más GRANDES del sistema, no basura.
    Los otros cuatro validadores lo dejaron pasar porque miran la FORMA (largo,
    markdown, muletillas, números) y esto está mal por lo que DICE.

    Es estructural, no prudencia: acá sólo llegan hallazgos SIN arreglo. Si
    hubiera algo que ejecutar sería un botón, escrito por alguien que sabe qué
    depende de esa tabla.
    """
    fila = {"habilidad": "db_peso", "sujeto": "la base", "regla": "peso_total_16",
            "problema": "la base pesa 1.4 GB en 247 tablas",
            "que_hacer": "Nada: es el número del día.",
            "evidencia": {"crecio": [{"tabla": "mercado.market_snapshot"}]}}
    h = redactar.hechos(fila)
    real = ("Purgá o archivá las cinco tablas que figuran abajo; si el peso no "
            "baja, compará contra el corte de mañana.")
    assert "destruir" in redactar.revisar(real, h, fila)
    for verbo in ("Borrá la tabla vieja de a poco.",
                  "Conviene truncar mercado.market_snapshot esta noche.",
                  "Eliminá lo que sobra en mercado.market_snapshot.",
                  "Archivá mercado.market_snapshot fuera de la base."):
        assert redactar.revisar(verbo, h, fila), f"dejó pasar: {verbo!r}"
    # Y lo que SÍ puede decir: nombrar la que se movió y qué mirar.
    ok = ("Lo que se movió es mercado.market_snapshot; si mañana vuelve a "
          "encabezar, mirar qué la escribe fuera de rueda.")
    assert redactar.revisar(ok, h, fila) == ""


def test_lo_cosmetico_se_limpia_en_vez_de_tirar_la_respuesta():
    """Dos de las primeras cuatro corridas reales se perdieron por FORMA:
    backticks y 269 caracteres contra un tope de 260. Pagar la llamada y
    quedarse con el piso por una comilla es tirar plata; cortar a cuchillo deja
    media oración, que es peor que la frase de molde."""
    assert redactar.limpiar("Creció `mercado.market_snapshot` **fuerte**.") == (
        "Creció mercado.market_snapshot fuerte.")
    # ⚠️ el `_` NO se toca: sin él el nombre de la tabla deja de existir, y es
    # lo único que sirve del texto.
    assert "market_snapshot" in redactar.limpiar("`mercado.market_snapshot`")
    largo = "Primera oración, corta y entera. " + "palabra " * 80
    corto = redactar.limpiar(largo)
    assert corto == "Primera oración, corta y entera."
    assert len(corto) <= redactar.MAX_CHARS


def test_lo_que_no_contesta_deja_el_piso_y_dice_por_que():
    """«No contestó» y «contestó una macana que tiré» no se pueden ver iguales.

    Es el invariante #1 aplicado al propio redactor: sin `rechazo`, una
    habilidad cuyo texto se descarta siempre se vería idéntica a una que el
    proveedor nunca atendió, y nadie sabría cuál de las dos arreglar.
    """
    fila = {"habilidad": "db_peso", "regla": "peso_total_11", "sujeto": "la base",
            "problema": "x", "que_hacer": "y", "evidencia": {}}
    import agente.redactar as r
    from core import ai as _ai
    orig = _ai.completar_con_traza
    try:
        _ai.completar_con_traza = lambda *a, **k: (None, None)
        out = r.redactar_uno(fila)
        assert out["texto"] == "" and out["rechazo"], "no dijo por qué no hay texto"

        _ai.completar_con_traza = lambda *a, **k: (
            '{"que_hacer": "Se recomienda revisar el crecimiento.", "no_se": ""}', 7)
        out = r.redactar_uno(fila)
        assert out["texto"] == "", "dejó pasar una muletilla"
        assert "muletilla" in out["rechazo"] and out["traza"] == 7
    finally:
        _ai.completar_con_traza = orig


def test_el_redactor_se_apaga_sin_deploy(monkeypatch):
    """`AGENTE_REDACTA=0` y no se hace una sola llamada. Apagarlo no deja un
    aviso mudo: deja el texto determinista, que nunca se borró."""
    monkeypatch.setenv("AGENTE_REDACTA", "0")
    assert not redactar.encendido()
    out = redactar.redactar_uno({"habilidad": "db_peso", "regla": "peso_total_11",
                                 "sujeto": "la base", "problema": "x",
                                 "que_hacer": "y", "evidencia": {}})
    assert out["texto"] == "" and "apagado" in out["rechazo"]


def test_el_alcance_del_redactor_lo_pone_la_query():
    """Que sólo se redacten avisos no puede depender de que el llamador se
    acuerde: el filtro vive en la query de pendientes."""
    src = inspect.getsource(registro.pendientes_de_texto)
    assert "arreglo = ''" in src, "la query no limita a los avisos"
    assert "ia_intentos < %s" in src, "la query no respeta el tope de intentos"
    assert "estado = ANY(%s)" in src, "redactaría hallazgos ya cerrados"


# ── tasa_vs_1816 y on_faltante (§0.do, §0.dp) ──────────────────────────────

def _corp_hd(tk: str, tea: float, precio: float = 80.0) -> tuple[dict, dict]:
    """Un corporativo hard dólar del master + su fila de snapshot."""
    simbolo = f"MERV - XMEV - {tk}D - 24hs"
    doc = {"ticker_corto": tk, "ticker": simbolo, "emisor_tipo": "corporativo",
           "moneda_eje": "USD", "ajuste": "fija", "moneda_flujo": "USD"}
    return doc, {simbolo: {"last_price": precio, "tea": tea}}


def _armar_tasa_vs_1816(monkeypatch, docs, snap, su: dict):
    """Inyecta las fuentes y la respuesta de 1816 (`ticker → tea`)."""
    from agente import fuentes
    from api.services import curvas_vista
    from core import mercado_1816

    monkeypatch.setattr(mercado, "_feed_o_sindatos", lambda: None)
    monkeypatch.setattr(mercado_1816, "disponible", lambda: True)
    monkeypatch.setattr(fuentes, "master", lambda: docs)
    monkeypatch.setattr(fuentes, "snapshot", lambda cols=None: snap)
    monkeypatch.setattr(curvas_vista, "pills_del_master",
                        lambda: {d["ticker_corto"]: ("hard_dolar",) for d in docs})
    monkeypatch.setattr(mercado_1816, "grafias_de", lambda tks, ajuste="fija": {t: t for t in tks})
    llamadas = []

    def _vigentes(tickers, campos, **kw):
        llamadas.append((list(tickers), kw.get("moneda")))
        return {"fechaOperacion": "2026-09-04",
                "instrumentos": {t: {"tea": su[t], "tna": su[t] * 0.97}
                                 for t in tickers if t in su}}
    monkeypatch.setattr(mercado_1816, "indicadores_vigentes", _vigentes)
    return llamadas


def test_tasa_vs_1816_es_la_tabla_del_diag_sin_umbral_ni_filtro(monkeypatch):
    """User (2026-09-05): *«no quiero NADA de análisis, solamente comparar, uno
    vs otro y listo»*. UN hallazgo, la tabla adentro, TODOS los bonos: el que
    coincide, el que no, el que no tiene TEA nuestra y el que 1816 no publica."""
    d1, s1 = _corp_hd("IGUAL", 0.0800)
    d2, s2 = _corp_hd("LEJOS", 0.0800)
    d3, s3 = _corp_hd("SINTEA", 0.0800); s3[d3["ticker"]]["tea"] = None
    d4, s4 = _corp_hd("SIN1816", 0.0800)
    ll = _armar_tasa_vs_1816(monkeypatch, [d1, d2, d3, d4], {**s1, **s2, **s3, **s4},
                             su={"IGUAL": 0.0805, "LEJOS": 0.1100, "SINTEA": 0.0900})
    out = mercado.tasa_vs_1816({})
    assert len(out) == 1
    h = out[0]
    assert h.sujeto == mercado.FAMILIA_CORP_HD and h.regla == "tabla"
    assert [b["ticker"] for b in h.evidencia["bonos"]] == ["IGUAL", "LEJOS", "SIN1816", "SINTEA"]
    por = {b["ticker"]: b for b in h.evidencia["bonos"]}
    assert por["LEJOS"]["tea_mia"] == 0.08 and por["LEJOS"]["tea_1816"] == 0.11
    assert por["SINTEA"]["tea_mia"] is None and por["SINTEA"]["tea_1816"] == 0.09
    assert por["SIN1816"]["tea_1816"] is None and por["SIN1816"]["tna_1816"] is None
    from quant.tasas import tna_desde_tea
    assert por["IGUAL"]["tna_mia"] == tna_desde_tea(0.08), "la TNA de la pantalla: TEM×12"
    # La tabla, en el detalle, con las columnas del script y una fila por bono.
    lineas = h.detalle.split("\n")
    assert lineas[0].split() == ["TICKER", "TNA", "MIA", "TNA", "1816", "TEA", "MIA", "TEA", "1816"]
    assert len(lineas) == 5 and lineas[3].startswith("SIN1816") and "--" in lineas[3]
    assert {m for _, m in ll} == {"mep"}, "1816 divide por CCL: se pide en mep"
    # Ninguna conclusión: ni «se aparta», ni bps, ni umbral.
    assert "bps" not in h.problema and "bps" not in h.detalle
    assert "umbral" not in h.evidencia


def test_tasa_vs_1816_si_1816_no_trae_ninguna_tea_no_afirma_nada(monkeypatch):
    """Una respuesta vacía NO es una tabla (invariante 1): SinDatos."""
    d, s = _corp_hd("X", 0.08)
    _armar_tasa_vs_1816(monkeypatch, [d], s, su={})
    with pytest.raises(tipos.SinDatos):
        mercado.tasa_vs_1816({})


def test_tasa_vs_1816_troza_de_a_50_porque_la_api_trunca(monkeypatch):
    """`indicadores` hace `list(tickers)[:50]` sin avisar: del 51 en adelante
    1816 «no tendría» el bono. Se pide en lotes."""
    docs, snap, su = [], {}, {}
    for i in range(120):
        d, s = _corp_hd(f"ON{i:03d}", 0.08)
        docs.append(d); snap.update(s); su[d["ticker_corto"]] = 0.0805
    ll = _armar_tasa_vs_1816(monkeypatch, docs, snap, su=su)
    assert len(mercado.tasa_vs_1816({})[0].evidencia["bonos"]) == 120
    assert [len(t) for t, _ in ll] == [50, 50, 20]
    assert mercado.LOTE_1816 == 50


def test_tasa_vs_1816_es_un_aviso_por_familia_y_pide_lo_que_tamar_1816_ya_probo():
    """Sin arreglo, sin umbral, sin sujeto que pueda caducar (es una tabla), y
    los campos del lote son los que `jobs/tamar_1816` ya probó contra la API."""
    h = catalogo.HABILIDADES["tasa_vs_1816"]
    assert h.dominio == "MERCADO" and not h.arreglos and not h.umbrales and h.sujeto_es == ""
    assert '["tea", "tna"]' in inspect.getsource(mercado.tasa_vs_1816)


def _universo_on(*tickers: str, curva: str = "Corporativos USD") -> dict:
    return {"fuente": "1816", "instrumentos": {
        t: {"_curva": curva, "fechaVencimiento": "2028-06-30",
            "denominacion": f"ON {t}", "emisorNombre": "YPF"} for t in tickers}}


def _armar_on_faltante(monkeypatch, univ, primary, cartera=frozenset(),
                       descartadas=frozenset()):
    from agente import fuentes
    from core import curvas_sql
    monkeypatch.setattr(fuentes, "universo_1816", lambda: univ)
    monkeypatch.setattr(fuentes, "master", lambda: [{"ticker_corto": "YMCXO"}])
    monkeypatch.setattr(fuentes, "en_cartera", lambda: set(cartera))
    monkeypatch.setattr(fuentes, "tickers_en_primary", lambda: primary)
    monkeypatch.setattr(fuentes, "primary_fecha", lambda: None)
    monkeypatch.setattr(fuentes, "ons_no_interesan", lambda: set(descartadas))
    monkeypatch.setattr(curvas_sql, "calendario_habil", lambda: set())
    monkeypatch.setattr(curvas_sql, "sale_del_master", lambda *a, **k: False)


def test_on_faltante_solo_ofrece_lo_que_primary_cotiza(monkeypatch):
    """User: *«antes de ofrecer agregarlas, validar que se encuentra en
    Primary»*. La que 1816 lista y Primary no → no existe para nosotros. La
    que ya tenemos → no falta. La que tenemos en CARTERA y no valúa → se canta
    aunque Primary no la liste, y lo dice."""
    _armar_on_faltante(monkeypatch, _universo_on("YMCXO", "COTIZA", "NOCOTZ", "ENCART"),
                       primary={"COTIZA", "YMCXO"}, cartera={"ENCART"})
    por = {h.sujeto: h for h in mercado.on_faltante({})}
    # La que NO tenemos entra a la fila de familia, no a una propia.
    assert set(por) == {mercado.FAMILIA_ON, "ENCART"}
    fam = por[mercado.FAMILIA_ON]
    assert fam.regla == "no_estan_en_curvas" and fam.severidad == "baja"
    assert [x["ticker"] for x in fam.evidencia["_items"]] == ["COTIZA"]
    assert por["ENCART"].severidad == "alta" and "CARTERA" in por["ENCART"].problema
    assert "Primary NO la lista" in por["ENCART"].problema
    assert "ENCONTRÓ" in por["ENCART"].que_hacer


def test_on_faltante_sin_foto_de_primary_no_ofrece_nada(monkeypatch):
    """Primary es CONDICIÓN, no filtro (al revés que en los soberanos): sin foto
    no se puede afirmar que cotice, y sin eso no se manda a nadie a cargar un
    cronograma. SinDatos, no []."""
    _armar_on_faltante(monkeypatch, _universo_on("COTIZA"), primary=None)
    with pytest.raises(tipos.SinDatos):
        mercado.on_faltante({})


def test_on_faltante_es_solo_hard_dolar_por_los_EJES_de_la_curva(monkeypatch):
    """El corte es estructural: corporativo + USD + fija. «Corporativos USD
    Linked» es corporativo y USD y NO entra; un soberano tampoco (eso es de
    `soberanos_faltantes`, que a su vez excluye corporativos: sin solape)."""
    univ = {"fuente": "1816", "instrumentos": {
        "ONHD": {"_curva": "Corporativos USD", "fechaVencimiento": "2028-01-01"},
        "ONDL": {"_curva": "Corporativos USD Linked", "fechaVencimiento": "2028-01-01"},
        "ONARS": {"_curva": "Corporativos ARS Fijo", "fechaVencimiento": "2028-01-01"},
        "GD30": {"_curva": "Soberanos USD", "fechaVencimiento": "2030-07-09"},
        "RARO": {"_curva": "Curva que no existe", "fechaVencimiento": "2028-01-01"},
    }}
    _armar_on_faltante(monkeypatch, univ, primary={"ONHD", "ONDL", "ONARS", "GD30", "RARO"})
    h = mercado.on_faltante({})
    assert [x["ticker"] for x in h[0].evidencia["_items"]] == ["ONHD"]
    assert "corporativo" not in mercado.ALCANCE, "los dos censos no se solapan"


def test_las_ONs_que_no_tenemos_son_UNA_fila_y_no_doscientas(monkeypatch):
    """⚠️ **212 avisos no se leen, y peor: no se pueden callar de a uno.**

    User (2026-09-06): *«esto ensucia el AHORA, muchas ON no son relevantes, ya
    de por sí hay muchas ON y genera muchos avisos»*. Medido ese día: 212
    hallazgos abiertos de `on_faltante`, uno por ON.

    El corte no es «cuáles importan» —eso el sistema no lo sabe— sino uno que sí
    se puede afirmar: **si la tenemos en cartera, hoy no valúa**, y eso es un
    problema concreto que merece su fila. Lo que no tenemos es una OFERTA de
    catálogo, y una oferta es UNA fila con la lista adentro, que el ✕ silencia de
    un click. Mismo patrón que `cedear_faltante` (§0.dl) y por la misma razón.
    """
    _armar_on_faltante(
        monkeypatch, _universo_on("A1", "A2", "A3", "A4", "TENGO"),
        primary={"A1", "A2", "A3", "A4", "TENGO"}, cartera={"TENGO"})
    h = mercado.on_faltante({})
    fam = [x for x in h if x.sujeto == mercado.FAMILIA_ON]
    assert len(h) == 2 and len(fam) == 1, (
        f"cuatro ONs sueltas tienen que dar UNA fila, no cuatro: {[x.sujeto for x in h]}")
    assert fam[0].evidencia["cantidad"] == 4
    assert [x["ticker"] for x in fam[0].evidencia["_items"]] == ["A1", "A2", "A3", "A4"]
    # La lista viaja en `_items` (guión bajo = dato de máquina): la pantalla NO
    # la dibuja como evidencia suelta. El mismo criterio que `ficha_incompleta`.
    assert "items" not in fam[0].evidencia
    # Y el que_hacer manda a tildar (dar de alta o descartar), no a tipear en
    # Manager. Descartar es por TICKER (§0.eh): no silencia el aviso entero.
    assert "tildar" in fam[0].que_hacer.lower()
    assert "no me interesan" in fam[0].que_hacer


def test_on_faltante_descarta_por_ticker_y_vuelve_solo_con_las_nuevas(monkeypatch):
    """Descartar por ticker (§0.eh) NO es «no me interesa» el aviso entero: las
    descartadas dejan de contarse y ofrecerse, y el aviso renace SOLO con las
    que 1816 publique de acá en más. La cartera nunca se filtra: si la casa la
    tiene y no valúa, es un problema aunque alguien la haya descartado."""
    univ = _universo_on("COTIZA", "NUEVA", "ENCART")
    primary = {"COTIZA", "NUEVA", "ENCART"}

    _armar_on_faltante(monkeypatch, univ, primary=primary, cartera={"ENCART"},
                       descartadas={"COTIZA"})
    por = {h.sujeto: h for h in mercado.on_faltante({})}
    fam = por[mercado.FAMILIA_ON]
    assert [x["ticker"] for x in fam.evidencia["_items"]] == ["NUEVA"]
    assert fam.evidencia["ya_descartadas"] == 1
    assert "1 ya descartada" in fam.problema

    _armar_on_faltante(monkeypatch, univ, primary=primary, cartera={"ENCART"},
                       descartadas={"COTIZA", "NUEVA"})
    sujetos = {h.sujeto for h in mercado.on_faltante({})}
    assert mercado.FAMILIA_ON not in sujetos, "sin ninguna suelta, no hay fila de familia"

    _armar_on_faltante(monkeypatch, univ, primary=primary, cartera={"ENCART"},
                       descartadas={"ENCART"})
    sujetos = {h.sujeto for h in mercado.on_faltante({})}
    assert "ENCART" in sujetos, "la cartera NUNCA se filtra por descartadas"


def test_no_interesan_ons_solo_descarta_lo_que_el_detector_ofrecio(monkeypatch):
    """No se escribe lo que manda el navegador: solo lo que el propio detector
    ofreció en `_items` de la fila de familia (§0.eh)."""
    from agente import fuentes, libro, motor, vista
    from api.services import ons as ons_service

    hallazgo = {"id": 1, "habilidad": "on_faltante", "sujeto": mercado.FAMILIA_ON,
                "regla": "no_estan_en_curvas", "estado": tipos.NUEVO,
                "evidencia": {"_items": [{"ticker": "AAA1O"}, {"ticker": "BBB2O"}]}}
    monkeypatch.setattr(vista, "_hallazgo_on", lambda hid: dict(hallazgo))
    llamadas = []
    monkeypatch.setattr(ons_service, "ignorar_concil",
                        lambda tk, actor="": llamadas.append(tk))
    monkeypatch.setattr(libro, "registrar", lambda **kw: None)
    monkeypatch.setattr(fuentes, "refrescar", lambda: None)
    monkeypatch.setattr(motor, "correr_una", lambda h: None)

    r = vista.no_interesan_ons(1, ["aaa1o", "ZZZ9O"])
    assert r["ok"] and r["descartadas"] == ["AAA1O"], "ZZZ9O no la ofreció el detector"
    assert llamadas == ["AAA1O"]

    llamadas.clear()
    r2 = vista.no_interesan_ons(1, [], todas=True)
    assert r2["ok"] and set(r2["descartadas"]) == {"AAA1O", "BBB2O"}
    assert set(llamadas) == {"AAA1O", "BBB2O"}

    otra_regla = {**hallazgo, "regla": "otra_regla"}
    monkeypatch.setattr(vista, "_hallazgo_on", lambda hid: dict(otra_regla))
    r3 = vista.no_interesan_ons(1, ["AAA1O"])
    assert r3["ok"] is False


def test_no_interesan_cedears_solo_descarta_lo_que_primary_ofrece(monkeypatch):
    """Gemelo de las ONs (§0.eh): no se escribe lo que manda el navegador,
    solo lo que `alta_cedear.candidatos` ofrece EN VIVO ahora — no la
    `muestra` congelada del hallazgo."""
    from agente import alta_cedear, fuentes, libro, motor, vista
    from core import cedears_sql

    hallazgo = {"id": 1, "habilidad": "cedear_faltante", "sujeto": alta_cedear.FAMILIA,
                "regla": "no_esta_en_master", "estado": tipos.NUEVO,
                "evidencia": {"cantidad": 2, "muestra": ["META", "GOOGL"]}}
    monkeypatch.setattr(vista, "_hallazgo_on", lambda hid: dict(hallazgo))
    monkeypatch.setattr(fuentes, "cedears_master", lambda: [{"ticker": "x"}])
    monkeypatch.setattr(fuentes, "fichas_primary", lambda: [{"simbolo": "x"}])
    filas = [{"unidad": "META", "simbolo": "MERV - XMEV - META - 24hs",
              "subyacente_primary": "META"},
             {"unidad": "GOOGL", "simbolo": "MERV - XMEV - GOOGL - 24hs",
              "subyacente_primary": "GOOGL"}]
    monkeypatch.setattr(alta_cedear, "candidatos",
                        lambda master, fichas, min_propios=3: {"filas": filas})
    llamadas = []
    monkeypatch.setattr(cedears_sql, "descartar",
                        lambda tk, **kw: llamadas.append(tk))
    monkeypatch.setattr(libro, "registrar", lambda **kw: None)
    monkeypatch.setattr(fuentes, "refrescar", lambda: None)
    monkeypatch.setattr(motor, "correr_una", lambda h: None)

    r = vista.no_interesan_cedears(1, ["meta", "ZZZZ"])
    assert r["ok"] and r["descartados"] == ["META"], "ZZZZ no la ofreció candidatos"
    assert llamadas == ["META"]

    llamadas.clear()
    r2 = vista.no_interesan_cedears(1, [], todas=True)
    assert r2["ok"] and set(r2["descartados"]) == {"META", "GOOGL"}
    assert set(llamadas) == {"META", "GOOGL"}

    otra_regla = {**hallazgo, "regla": "otra_regla"}
    monkeypatch.setattr(vista, "_hallazgo_on", lambda hid: dict(otra_regla))
    r3 = vista.no_interesan_cedears(1, ["META"])
    assert r3["ok"] is False


def test_el_preflight_pregunta_al_MOTOR_si_va_a_haber_TEA():
    """⚠️ **DOS PREGUNTAS DISTINTAS CON UNA SOLA RESPUESTA** (REGLA #9(B)).

    El paso `tea_motor` («¿el motor va a calcular la TEA?») miraba
    `RAMAS_AUTOMATICAS`, que contesta otra cosa («¿el cuadro de 1816 se convierte
    sin ambigüedad?»). Coincidían para cuatro ramas y por eso nadie lo notó,
    hasta las ONs: `calcular_campos` tiene un `elif curva == "on"` con la
    matemática hard-dólar completa desde siempre, y el pre-flight igual afirmaba
    «cae en el `else` del motor: solo computa duration» y BLOQUEABA.

    Un paso que dice algo falso sobre el motor es peor que uno que no existe.
    """
    from agente import alta
    from engines.curvas import RAMAS_CON_FORMULA

    # Las dos listas se PARECEN y contestan cosas distintas. `tamar`/`dual` son
    # el caso que las separa hoy: la mesa los tiene cargados y el motor NO les
    # calcula la tasa a propósito (cae en el `else`), así que ninguna de las dos
    # los incluye — pero la razón es otra en cada una, y por eso siguen separadas.
    assert "on" in RAMAS_CON_FORMULA
    assert alta._ramas_con_formula() == RAMAS_CON_FORMULA
    assert alta._ramas_con_formula() is not alta.RAMAS_AUTOMATICAS, (
        "son dos preguntas distintas: si una pasa a ser un alias de la otra, "
        "vuelve el bug de las ONs")
    # Y cada rama de la lista tiene su `elif` de verdad en el motor: si alguien
    # suma una a mano sin escribir la fórmula, esto falla.
    src = inspect.getsource(__import__("engines.curvas", fromlist=["x"]).calcular_campos)
    for rama in RAMAS_CON_FORMULA:
        assert f'curva == "{rama}"' in src, (
            f"«{rama}» está declarada con fórmula y no tiene rama en calcular_campos")


def test_la_ON_que_la_mesa_TIENE_y_la_de_CATALOGO_llevan_botones_distintos():
    """⚠️ **El botón existe, y solo donde hay algo roto.**

    Hasta el 2026-09-06 `on_faltante` no declaraba arreglo porque la rama `on`
    bloqueaba a TODAS por adelantado (§0.dt) — y un botón que siempre bloquea
    enseña a no apretar. Con la conversión medida (§0.du) el paso `rama` deja de
    bloquear en bloque y **cada bono lo juzga su propio cotejo contra 1816**: de
    8 medidas, 5 con el cronograma idéntico y 3 que siguen bloqueadas.

    El arreglo va sobre la regla de las que la mesa TIENE. La fila de familia
    —las que 1816 publica y nosotros no seguimos— **no lleva botón a propósito**:
    ahí no hay nada roto, y a ENCONTRÓ solo entra lo que tiene arreglo
    (invariante 9).
    """
    from agente import alta
    h = catalogo.HABILIDADES["on_faltante"]
    assert h.sujeto_es == "bono" and h.ventana == "rueda"
    # DOS reglas, DOS botones, y no son el mismo: la que la mesa tiene se da de
    # alta sola (el sujeto ES el bono); la de catálogo despliega la lista para
    # tildar, porque el sistema sabe darlas de alta a todas y no cuáles quiere
    # la mesa (§0.dv).
    assert h.arreglos == {"no_esta_en_curvas": "alta_bono",
                          "no_estan_en_curvas": "alta_on"}
    assert "on" in alta.RAMAS_AUTOMATICAS


def test_un_arreglo_cuenta_lo_que_HIZO_paso_por_paso(monkeypatch):
    """⚠️ **SIETE COSAS Y UNA FRASE AL FINAL** (§0.dx).

    Un alta simula, verifica, escribe, completa la ficha, siembra la tasa, siembra
    las especies y anota en el libro. Devolvía una sola línea, así que un paso
    que salía mal sin tumbar a los demás —el bono escrito pero la especie no— iba
    metido en una subordinada y se leía como éxito.

    El rastro sale del BACKEND: la pantalla no inventa ninguno, y los que fallan
    también viajan. Es la contracara del pre-flight —uno dice lo que VA a hacer,
    el otro lo que HIZO— y los dos salen del mismo lugar.
    """
    from agente import arreglos

    a = arreglos.ARREGLOS["alta_bono"]
    monkeypatch.setattr("agente.alta.aplicar", lambda tk, **k: {
        "ok": True, "aplicado": True,
        "pasos": [{"titulo": "Escribir el bono", "estado": "ok", "detalle": "8 flujos"},
                  {"titulo": "Sembrar las patas", "estado": "falló",
                   "detalle": "Primary no listó ninguna"}]})
    r = a.aplicar("YM43O", {"curva_1816": "Corporativos USD"}, por="yo")
    assert r.ok, "un paso que falla sin tumbar la escritura NO invalida el alta"
    assert [p["estado"] for p in r.pasos] == ["ok", "falló"], (
        "el paso que salió mal tiene que VERSE, no quedar en una subordinada")

    # Y el que NO cierra también cuenta por qué, en vez de devolver un string pelado.
    monkeypatch.setattr("agente.alta.aplicar", lambda tk, **k: {
        "ok": True, "aplicado": False, "error": "el pre-flight no pasa",
        "pasos": [{"titulo": "Verificar antes de escribir", "estado": "falló"}]})
    r2 = a.aplicar("YM43O", {"curva_1816": "Corporativos USD"}, por="yo")
    assert not r2.ok and len(r2.pasos) == 1


def test_la_tasa_viaja_con_su_TNA_y_con_su_DOLAR():
    """⚠️ **UNA TASA EN DÓLARES SIN DECIR CUÁL NO ES UN NÚMERO** (§0.ec).

    Medido el 2026-09-07 sobre cuatro ONs: el tipo de cambio implícito nuestro es
    1.525,4 en las cuatro y el de 1816 es 1.589,5 — **4,20% de diferencia**, que
    entra entera en el precio en dólares y de ahí en la tasa. No es un misterio:
    el motor divide por MEP (`precio_soberano_a_usd`) y 1816 por CCL, y eso está
    escrito en `core/mercado_1816.py` desde el episodio de GD46.

    De ahí salen los 136 / 158 / 215 bps del cotejo. Y el caso que lo grita es
    LMS8O: nosotros −13,44% contra −0,01% de ellos — un bono que rinde cero
    mostrado como si perdiera 13% al año, **sin que nada falle**.

    Este test NO congela qué dólar se usa (esa es una decisión de la mesa y mueve
    todo el hard dólar de la casa). Congela que la pantalla lo DIGA, y que la TNA
    —el número que la mesa mira— salga de la misma función que el resto de la app
    en vez de que alguien la derive a ojo.
    """
    import inspect as _i

    from agente import alta, arreglos
    from quant.tasas import tna_desde_tea

    src = _i.getsource(alta._simular_tasa)
    assert '"tna": tna_desde_tea(' in src, (
        "la TNA sale de `quant/tasas`, no de una cuenta local: dos TNAs para el "
        "mismo bono según la pantalla es el bug que ese módulo vino a cerrar")
    assert '"dolar":' in src and '"dolar_valor":' in src
    # Y llega hasta la pantalla: si el preview no lo mapea, el backend lo calcula
    # para nadie.
    prev = _i.getsource(arreglos._preview_de_simulacion)
    for k in ('"tna"', '"dolar"', '"dolar_valor"'):
        assert k in prev, f"{k} no viaja a la pantalla"
    # La conversión es la de la casa, no una nueva.
    assert abs(tna_desde_tea(0.085876) - 0.0826713) < 1e-6


def test_el_cuadro_de_una_ON_se_VE_y_no_sale_en_guiones():
    """⚠️ **TRES SHAPES DE CUADRO, y la pantalla conocía una.**

    `convertir_flujos` devuelve formas distintas a propósito, porque el motor
    consume cosas distintas: `cer`/`soberanos`/`dolar_linked` traen
    `amortizacion_pct` + `cupon_sobre_residual`; `tasa_fija` y **`on`** traen
    `amortizacion` + `interes`, montos absolutos.

    El mapeo de la pantalla conocía solo la primera, así que el cronograma de una
    ON —y el de una LECAP— salía con las tres columnas en «—». Y **no fallaba
    nada**: `.get("amortizacion_pct")` sobre un dict que no la tiene devuelve
    None, y None se dibuja como un guión. La fila estaba, la fecha estaba, los
    números no.
    """
    from agente.arreglos import _flujos_para_ver

    # Shape de una ON: montos absolutos, y el residual NO viene — se deriva con
    # la misma cuenta que el motor (suma de las amortizaciones que faltan).
    on = _flujos_para_ver([
        {"fecha": "2028-01-14", "amortizacion": 40.0, "interes": 4.25},
        {"fecha": "2030-04-12", "amortizacion": 60.0, "interes": 2.55}])
    assert [f["amortizacion"] for f in on] == [40.0, 60.0]
    assert [f["cupon"] for f in on] == [4.25, 2.55]
    assert [f["residual"] for f in on] == [100.0, 60.0]

    # Shape de un soberano: sigue leyéndose igual que siempre.
    sob = _flujos_para_ver([{"fecha": "2030-01-01", "amortizacion_pct": 50.0,
                             "cupon_sobre_residual": 1.5,
                             "residual_previo_pct": 100.0}])
    assert sob == [{"fecha": "2030-01-01", "amortizacion": 50.0,
                    "cupon": 1.5, "residual": 100.0}]


def test_la_fila_de_catalogo_despliega_la_lista_para_TILDAR(monkeypatch):
    """⚠️ **UNA fila, pero con botón: el sistema sabe cuáles PUEDE, no cuáles
    QUIERE la mesa.**

    Es el mismo problema que `alta_cedear` (§0.dl) y por eso la misma forma. Y es
    lo que faltaba después de §0.ds: juntar las 193 en una fila sin botón dejaba
    al user sin ninguna manera de dar de alta las que sí le interesan — que era
    justo lo que venía pidiendo.

    El arreglo NO escribe lo que manda el navegador: cada ticker tildado vuelve a
    pasar por `alta.aplicar`, o sea por el pre-flight entero, y la curva de 1816
    sale de lo que guardó el DETECTOR, no del cliente.
    """
    from agente import arreglos

    a = arreglos.ARREGLOS["alta_on"]
    assert a.pide_datos and not a.inmediato
    ev = {"cantidad": 2, "_items": [
        {"ticker": "AAA1O", "emisor": "YPF", "curva_1816": "Corporativos USD"},
        {"ticker": "BBB2O", "emisor": "Capex", "curva_1816": "Corporativos USD"}]}
    pv = a.preview("ONs HARD DÓLAR", ev)
    assert pv["ok"] and pv["listado"] == "ons" and len(pv["ons"]) == 2

    vistos = []

    def _falso(tk, *, curva_1816, actor=""):
        vistos.append((tk, curva_1816, actor))
        return {"ok": tk == "AAA1O", "aplicado": tk == "AAA1O",
                "error": "" if tk == "AAA1O" else "el cotejo no cierra"}

    monkeypatch.setattr("agente.alta.aplicar", _falso)
    # El navegador manda una curva MENTIROSA: se ignora, manda la del detector.
    r = a.aplicar("ONs HARD DÓLAR", ev, por="yo",
                  datos=[{"unidad": "AAA1O", "valor": "Soberanos USD"},
                         {"unidad": "BBB2O", "valor": ""}])
    assert vistos == [("AAA1O", "Corporativos USD", "yo"),
                      ("BBB2O", "Corporativos USD", "yo")]
    # Una que no cierra NO frena a las demás, y las dos cosas se dicen.
    assert r.ok and "AAA1O" in r.detalle and "BBB2O" in r.detalle
    assert "1 dada(s) de alta" in r.detalle


def test_una_ON_se_escribe_por_la_puerta_de_las_ONs_y_no_por_la_de_bonos():
    """⚠️ **CADA TIPO POR SU PUERTA, y confundirlas bloqueaba todo.**

    Las ONs no viven bajo la curva «on» pelada: viven en `on_<sector>` y se
    escriben por `api/services/ons.upsert_on`, con su propio panel en Manager.
    Por eso «on» no está en `bonos_admin.CURVAS_BONO` — y `curva_destino`, que le
    preguntaba a esa lista, devolvía "" y dejaba el paso «la escritura va a ser
    aceptada» en BLOQUEA para CUALQUIER ON, con el cuadro perfecto.

    Usar la puerta de la mesa y no una escritura propia es lo que garantiza que
    un alta del agente no pueda tener otra shape que un alta humana.
    """
    from types import SimpleNamespace

    from agente import alta
    from api.services.bonos_admin import CURVAS_BONO

    assert "on" not in CURVAS_BONO, "si «on» entró acá, revisar por qué"
    ejes = SimpleNamespace(ajuste="fija", moneda="USD", emisor_tipo="corporativo")
    assert alta.curva_destino("on", ejes) == "on_otros", (
        "1816 no publica el sector: la ON nace en on_otros y se reclasifica "
        "desde el panel, en vivo")
    # Y `aplicar` rutea de verdad: el traductor existe y el `if` lo usa.
    src = inspect.getsource(alta.aplicar)
    assert 'sim["rama"] == "on"' in src and "_upsert_on_desde_simulacion" in src
    tr = inspect.getsource(alta._upsert_on_desde_simulacion)
    assert "ons.upsert_on" in tr and "upsert_bono" not in tr


# ═══════════════════════════════════════════════════════════════════════════
# EL EMISOR PROPUESTO — `agente/emisor.py` (2026-09-05)
# ═══════════════════════════════════════════════════════════════════════════


def test_el_modelo_elige_de_la_lista_y_lo_de_afuera_se_descarta(monkeypatch):
    """**LA GUARDA QUE HACE QUE ESTO NO ENSUCIE EL CATÁLOGO.**

    El modelo puede contestar un emisor que no existe —inventado, o una variante
    de grafía— y escribirlo sería fabricar el duplicado que este campo no puede
    tener: `CREDICUOTAS` y `Credicuotas Consumo` son el mismo emisor partido en
    dos, y por eso cualquier cosa que agrupe por emisor cuenta mal.

    Y lo que se devuelve es **la grafía del CATÁLOGO**, no la del modelo: aceptar
    `iam` y escribirlo así dejaría `iam` al lado de `IAM` sin que nada falle.
    """
    from agente import emisor

    monkeypatch.setattr(
        "core.ai.completar",
        lambda *a, **k: '{"[1] Ciclo Nova": "iam", "[2] X": "Banco Inventado", '
                        '"[3] Y": "", "[4] Z": "OTROS"}')
    r = emisor.por_modelo(
        [{"unidad": f"[{i}] X"} for i in (1, 2, 3, 4)], ["IAM", "OTROS"])

    # `iam` se acepta (la comparación no mira mayúsculas) pero entra la del catálogo.
    assert r["[1] Ciclo Nova"] == "IAM"
    assert r["[4] Z"] == "OTROS"
    # Un emisor que NO está en la lista no entra, aunque suene razonable.
    assert "[2] X" not in r
    # Y «no sé» tampoco escribe nada: es una respuesta válida, no un hueco.
    assert "[3] Y" not in r


def test_si_el_gateway_no_contesta_las_filas_quedan_como_estaban(monkeypatch):
    """**Meter IA acá no puede agregar un modo de falla nuevo.**

    Sin key, sin presupuesto o con el proveedor caído, el listado tiene que
    quedar exactamente como estaba antes de que este módulo existiera: vacío y
    tipeable. Es el mismo criterio que el PISO de `agente/redactar.py`.
    """
    from agente import emisor

    filas = [{"unidad": "[15154] Ciclo Nova Ahorro Plus", "ticker": "x"}]
    monkeypatch.setattr("core.ai.completar", lambda *a, **k: None)
    assert emisor.proponer(filas, ["IEB"]) == [
        {**filas[0], "propuesto": "", "fuente": ""}]

    # Y si el gateway LEVANTA, tampoco se cae la pantalla.
    def _revienta(*a, **k):
        raise RuntimeError("proveedor caído")
    monkeypatch.setattr("core.ai.completar", _revienta)
    assert emisor.proponer(filas, ["IEB"])[0]["propuesto"] == ""


def test_la_cadena_respeta_el_orden_de_confianza(monkeypatch):
    """El primero que contesta gana, y **el orden ES el de la confianza**.

    Lo de arriba se verifica sin abrir nada (el nombre está a la vista); lo de
    abajo hay que mirarlo. Por eso cada fila se lleva su `fuente` hasta la
    pantalla: confirmar «Finnhub dice Chevron Corp» no es el mismo acto que
    confirmar «el modelo eligió IEB».
    """
    from agente import emisor

    # Finnhub contesta por la acción y NO por el ETF — que es lo que se midió
    # de verdad el 2026-09-05: `profile2` es un perfil de EMPRESA.
    monkeypatch.setattr(emisor, "por_finnhub",
                        lambda u: "Chevron Corp" if u == "CVX" else "")
    monkeypatch.setattr(emisor, "por_modelo",
                        lambda f, e: {x["unidad"]: "OTROS" for x in f})
    filas = [
        {"unidad": "[903] CAFCI462-903 - Allaria Ahorro Plus", "ticker": "AAP"},
        {"unidad": "[8013] CVX", "ticker": "CVX"},
        {"unidad": "[8671] XLK", "ticker": "XLK"},
    ]
    r = emisor.proponer(filas, ["ALLARIA", "Chevron Corp", "OTROS"],
                        subyacentes={"CVX": "CVX", "XLK": "XLK"})
    # 1. el nombre gana aunque el ticker tuviera subyacente
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("ALLARIA", emisor.NOMBRE)
    # 2. Finnhub, cuando hay subyacente y contesta
    assert (r[1]["propuesto"], r[1]["fuente"]) == ("Chevron Corp", emisor.FINNHUB)
    # 3. el modelo, para lo que quedó (el ETF: Finnhub contestaría vacío)
    assert (r[2]["propuesto"], r[2]["fuente"]) == ("OTROS", emisor.MODELO)


def test_finnhub_vacio_no_se_lee_como_es_un_etf(monkeypatch):
    """⚠️ **«No contestó» NO es un veredicto** — el invariante 1, un nivel abajo.

    `profile2` devuelve vacío para un ETF, pero también para un símbolo mal
    escrito, para la red caída y para `AGRO`, que es una empresa de verdad. Si
    ese vacío se leyera como «es un ETF → OTROS», `AGRO` terminaría en el mismo
    balde que `XLK` y nadie lo notaría.

    Por eso la fila cae al siguiente eslabón en vez de resolverse acá.
    """
    from agente import emisor

    monkeypatch.setattr(emisor, "por_finnhub", lambda u: "")
    visto: list = []
    monkeypatch.setattr(emisor, "por_modelo",
                        lambda f, e: visto.extend(x["unidad"] for x in f) or {})
    r = emisor.proponer([{"unidad": "[19] AGRO", "ticker": "AGRO"}],
                        ["OTROS"], subyacentes={"AGRO": "AGRO"})
    assert visto == ["[19] AGRO"], "un Finnhub vacío tiene que caer al modelo"
    assert r[0]["propuesto"] == "" and r[0]["fuente"] == ""


def test_una_accion_sin_ficha_no_va_al_modelo_y_un_etf_con_ficha_si(monkeypatch):
    """⚠️ **Dónde el modelo NO opina, y por qué es ahí y no en otro lado.**

    Medido el 2026-09-05: en RENTA VARIABLE **sin** subyacente cargado el modelo
    acertó 2 de 6, y las cuatro que erró son el mismo caso —una acción argentina
    cuyo emisor no está en la lista cerrada, así que eligió el más parecido:
    `TXAR → YPF`, `TRAN → Transportadora de Gas del Norte`, `OEST → BBVA`,
    `AGRO → Banco de Valores`—. Plausibles todas, y falsas todas; un emisor
    equivocado se suma a los totales **sin que nada falle** (REGLA #9).

    El corte NO es «renta variable»: es **«sin ficha»**. Un ETF tiene subyacente
    cargado y Finnhub simplemente no lo cubre — ese sí va al modelo, que contestó
    `OTROS` 8 de 8. Si el corte fuera por cartera a secas, los ETFs se apagarían
    con él.
    """
    from agente import emisor

    monkeypatch.setattr(emisor, "por_finnhub", lambda u: "")
    visto: list = []
    monkeypatch.setattr(emisor, "por_modelo",
                        lambda f, e: visto.extend(x["ticker"] for x in f) or {})
    filas = [
        {"unidad": "[19] TXAR", "ticker": "TXAR", "cartera": "RENTA VARIABLE"},
        {"unidad": "[8671] XLK", "ticker": "XLK", "cartera": "RENTA VARIABLE"},
        {"unidad": "[903] Ciclo Nova", "ticker": "CN", "cartera": "FCI"},
    ]
    r = emisor.proponer(filas, ["OTROS", "IEB"], subyacentes={"XLK": "XLK"})
    assert visto == ["XLK", "CN"], (
        "una acción sin ficha no va al modelo; el ETF con ficha y el FCI sí")
    # Y la que no fue al modelo vuelve VACÍA, no «lo más parecido».
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("", "")


def test_el_emisor_propuesto_no_escribe_nada_por_su_cuenta():
    """**Propone, no aplica.** La escritura sigue donde estaba.

    `emisor.py` no puede tocar la base: quien escribe es
    `arreglos.CompletarFicha.aplicar` → `set_campos(crear=False)`, contra la
    lista VIVA de faltantes y con una línea de libro por título. Una propuesta
    que nadie confirma no llega a ninguna tabla.
    """
    # ⚠️ El CÓDIGO, no la prosa: el docstring del módulo NOMBRA `set_campos`
    # para decir dónde vive la escritura. Grepear el archivo entero haría fallar
    # este test justo cuando la explicación está bien escrita (ver `_codigo`).
    from agente import emisor
    src = _codigo(emisor)
    for prohibido in ("set_campos", "UPDATE ", "INSERT ", "DELETE "):
        assert prohibido not in src, (
            f"`agente/emisor.py` escribe ({prohibido!r}): tiene que PROPONER. "
            "La escritura vive en el arreglo, que verifica contra la lista viva.")
    # Y la única query que sí hace es de LECTURA del master de CEDEARs.
    assert src.count("cur.execute") == 1 and "SELECT" in src

    # El preview sólo propone para el EMISOR: cartera y clase_activo son
    # criterio de la mesa y no hay de dónde derivarlas — proponerlas sería
    # inventar, que es justo lo que este diseño no hace.
    arr = (RAIZ / "agente" / "arreglos.py").read_text()
    i = arr.index("propuestas = 0")
    assert 'if c["campo"] == "emisor"' in arr[i:i + 200]


def test_la_tarea_de_emisor_esta_declarada_en_el_gateway():
    """Una tarea sin fila en `core/ai.py` corre igual, con la config default y
    sin presupuesto propio — o sea, gasta sin techo y su traza no se puede
    rastrear. Declararla es lo que la hace auditable."""
    from agente import emisor
    from core.ai import _TAREAS

    cfg = _TAREAS.get(emisor.TAREA)
    assert cfg, f"«{emisor.TAREA}» no está declarada en core/ai.py::_TAREAS"
    # `pro`: acá no se redacta, se RECONOCE, y equivocarse escribe un dato en un
    # campo por el que se agrupa plata.
    assert cfg["tier"] == "pro" and cfg["timeout_s"] <= 45


def test_la_pantalla_resuelve_lo_MISMO_que_el_cron():
    """⚠️⚠️ **LO QUE EL SISTEMA YA SABE DERIVAR NO PUEDE ESPERAR A LA NOCHE.**

    El bug (2026-09-05): las reglas deterministas —FINANCIAMIENTO → OTROS,
    DERIVADOS → OTROS— vivían SOLO en el cron nocturno. La pantalla mostraba
    nueve pagarés con el emisor vacío mientras el sistema sabía perfectamente
    qué iba ahí, y para verlo resuelto había que esperar a que corriera el job.
    El user: *«no entiendo por qué justo el más fácil no lo hace»*.

    La pantalla es donde se trabaja, así que **la pantalla no puede saber menos
    que el cron**.

    ⚠️ Y la corrección NO fue copiar las reglas: se IMPORTAN las del job. Dos
    definiciones de «qué emisor le toca a un pagaré» serían dos verdades sin
    árbitro (REGLA #9) — el día que una cambie, la otra sigue contestando lo de
    antes y ninguna falla.
    """
    from agente import emisor

    # La regla gana sobre todo lo demás: es gratis, determinista, y si hay regla
    # no hay nada que proponer ni que confirmar.
    fin = {"unidad": "[#UBI260170001] #UBI260170001 Nro. 163214 Vto. 28/01/2027",
           "cartera": "FINANCIAMIENTO", "ticker": "#UBI260170001"}
    der = {"unidad": "[GFGC8000OC]", "cartera": "DERIVADOS", "ticker": "GFGC8000OC"}
    r = emisor.proponer([fin, der], ["OTROS", "ALLARIA"], usar_modelo=False)
    assert [(x["propuesto"], x["fuente"]) for x in r] == [
        ("OTROS", emisor.REGLA), ("OTROS", emisor.REGLA)]

    # Y lo que NO tiene regla sigue cayendo a los eslabones de abajo — la regla
    # no puede convertirse en un balde que se traga todo.
    fci = {"unidad": "[903] CAFCI462-903 - Allaria Ahorro Plus - Clase B",
           "cartera": "FCI", "ticker": "Allaria Ahorro Plus - Clase B"}
    otro = emisor.proponer([fci], ["OTROS", "ALLARIA"], usar_modelo=False)[0]
    assert (otro["propuesto"], otro["fuente"]) == ("ALLARIA", emisor.NOMBRE)

    # ⚠️ Las reglas se LLAMAN, no se copian: el módulo no puede tener su propia
    # versión de «FINANCIAMIENTO va a OTROS».
    src = _codigo(emisor)
    assert "assets_autofill" in src, (
        "`por_regla` tiene que importar las reglas del job, no reimplementarlas")
    assert "FINANCIAMIENTO" not in src and "DERIVADOS" not in src, (
        "el nombre de una cartera escrito acá es una SEGUNDA definición de la "
        "regla (REGLA #9): el día que el job la cambie, esto sigue con la vieja")


def test_lo_que_ya_TIENE_emisor_no_se_toca_nunca():
    """⚠️⚠️ **SOLO SE COMPLETAN VACÍOS. NI EL JOB NI EL BOTÓN NI EL MODELO
    PUEDEN PISAR UN EMISOR CARGADO.**

    Regla del user (2026-09-05), en mayúsculas: *«esto tiene que funcionar con
    los que están VACÍOS, ahora y de acá en adelante, no modificar lo que hay»*.

    No es una preferencia: un emisor cargado a mano es la ÚNICA información que
    el sistema no puede reconstruir. Y pisar acá no fallaría —quedaría un
    catálogo internamente coherente diciendo otra cosa—, que es el modo de falla
    que este repo persigue en todos lados.

    La garantía es de TRES capas independientes, y este test las congela a las
    tres. Que sean tres no es redundancia: cada una protege una vía de entrada
    distinta (el cron, el botón, y de qué universo salen las propuestas).
    """
    import inspect as _i

    from agente import arreglos, emisor
    from agente.detectores.catalogo import CAMPOS

    # ── 1. EL CRON. `assets_autofill` escribe SOLO si el valor está vacío; si
    #       la regla propone algo distinto de lo cargado, lo REPORTA.
    job = (RAIZ / "jobs" / "assets_autofill.py").read_text()
    i = job.index("if _vacio(actual):")
    rama = job[i:i + 500]
    assert "cambios[unidad][col] = val" in rama, "el job dejó de mirar si está vacío"
    assert "conflictos.append" in rama, (
        "lo que difiere de lo cargado tiene que REPORTARSE, no escribirse")

    # ── 2. EL BOTÓN. `aplicar` recalcula el permitido contra la lista VIVA de
    #       faltantes, así que una unidad que se completó entre que se abrió la
    #       pantalla y se apretó GUARDAR se saltea en vez de pisarse.
    ap = _i.getsource(arreglos.CompletarFicha.aplicar)
    assert "permitidas = {" in ap and "if unidad not in permitidas" in ap
    assert "saltados.append(unidad)" in ap, (
        "lo que ya no está vacío tiene que saltearse, nunca escribirse")

    # ── 3. DE DÓNDE SALEN LAS PROPUESTAS. El modelo nunca ve un asset que ya
    #       tenga emisor: `preview` le pasa `det.faltantes(c)`, y «faltante» lo
    #       define el SQL del detector — que es literalmente «está vacío».
    #       Sin esto, el modelo podría proponer sobre algo cargado y la pantalla
    #       lo ofrecería para escribir.
    pv = _i.getsource(arreglos.CompletarFicha.preview)
    assert "filas = det.faltantes(c)" in pv
    j = pv.index("em.proponer")
    assert "filas" in pv[j:j + 80], (
        "el modelo tiene que recibir SOLO los faltantes, no el catálogo")
    falta = next(c["falta"] for c in CAMPOS if c["campo"] == "emisor")
    assert "IS NULL" in falta and "= ''" in falta, (
        f"«sin emisor» dejó de significar «vacío»: {falta}")

    # ── Y el módulo que propone no tiene forma de escribir, ni siquiera por
    #    accidente: no importa la puerta de escritura.
    assert "assets_sql" not in _codigo(emisor)


def test_el_arreglo_que_puede_probarse_solo_refresca_su_tarjeta():
    """⚠️⚠️ **DOS NÚMEROS SOBRE LO MISMO, DE INSTANTES DISTINTOS.**

    El bug (2026-09-05). `ficha_incompleta` corre cada SEIS HORAS, y el
    `problema`/`detalle`/`evidencia` de un hallazgo son texto ESCRITO en la
    fila que sólo reescribe el detector cuando vuelve a correr. Entonces:
    completabas 28 títulos, el listado de abajo pasaba a 10 —se recalcula al
    mirar— y la tarjeta de arriba seguía diciendo 44 hasta la noche.

    El user: *«dice 44 pero son 6, está 100% desactualizado»*. Y es justo lo que
    este subsistema existe para no hacer: el badge y la lista salen de la misma
    query para que no puedan decir cosas distintas (invariante 11) — pero nadie
    había mirado el par tarjeta/listado.

    **El orden importa y es lo que este test congela**: el detector se corre
    DESPUÉS de dejar el hallazgo en `en_curso`. Si corriera antes, el cierre
    caería en AUSENCIA en vez de ACCIÓN y se perdería que fue el botón el que
    lo resolvió — con eso se pierde también la única señal que después habilita
    una reincidencia (invariante 4).
    """
    import inspect as _i

    from agente import arreglos

    src = _i.getsource(arreglos.aplicar)
    assert "if a.confirma_ya:" in src and "motor.correr_una" in src
    # El UPDATE a `en_curso` va ANTES del re-run. Sin esto, el cierre no es
    # POR ACCIÓN y la reincidencia deja de poder existir.
    assert src.index("tipos.EN_CURSO, a.id, hallazgo_id") < src.index("correr_una"), (
        "el detector se corre DESPUÉS de dejar el hallazgo en_curso, o el "
        "cierre cae en ausencia y se pierde que lo arregló el botón")
    # Y no puede tirar abajo una escritura que ya pasó.
    j = src.index("if a.confirma_ya:")
    assert "try:" in src[j:j + 200] and "except Exception" in src[j:j + 500]

    # ⚠️ Es una DECLARACIÓN por arreglo, no un default. Lo que lo impide de
    # verdad es que **la respuesta todavía no exista**: el job de `rehacer_job`
    # tarda ocho minutos, así que preguntar apenas se lanza sería medir antes de
    # tiempo y el «no» sería falso.
    #
    # El COSTO casi nunca alcanza para decir que no (2026-09-06). Las tres altas
    # se sumaron ese día: el censo de 1816 cuesta créditos, sí, pero **el alta ya
    # gastó uno POR CUPÓN** (7 a 39) bajando el cuadro. La alternativa era lo que
    # el user vio: «APLICADO · ESPERANDO QUE EL DETECTOR CONFIRME» durante dos
    # horas, con el bono ya escrito.
    assert arreglos.Arreglo.confirma_ya is False, "el default tiene que ser NO"
    ya = {a.id for a in arreglos.ARREGLOS.values() if a.confirma_ya}
    # `alta_contraparte` se auto-confirma porque su detector son DOS consultas
    # por índice sobre 1.900 filas: no toca la red, no cuesta créditos y contesta
    # en el acto. Sin esto, la tarjeta seguiría diciendo 53 media hora después de
    # haber dado de alta 20 — los dos números sobre lo mismo que este subsistema
    # existe para no mostrar.
    assert ya == {"alta_contraparte", "completar_ficha", "arbitrar_copia",
                  "alta_bono", "alta_on", "alta_cedear"}, (
        f"cambió quién se auto-confirma: {sorted(ya)}. Sumá uno sólo si su "
        "detector puede contestar YA — y decí por qué en el código.")
    # `rehacer_job` es el que NO puede: su respuesta tarda ocho minutos.
    assert not arreglos.ARREGLOS["rehacer_job"].confirma_ya


# ═══════════════════════════════════════════════════════════════════════════
# LA CLASE PROPUESTA — `agente/clase.py` (§0.ei)
# ═══════════════════════════════════════════════════════════════════════════


def test_clase_activo_se_propone_con_fuente_y_de_lista_cerrada():
    """`clase.proponer` es PURA: la regla del derivado gana si su valor ya
    está en la lista cerrada, y se apaga sola —con una nota— si no está. La
    de Primary hace lo mismo con la ficha del fondo."""
    from agente import clase

    call = {"unidad": "[OTC - SOJ.ROS/NOV26 380 C]", "cartera": "DERIVADOS",
            "ticker": "", "clase_activo": "", "emisor": ""}
    put = {"unidad": "[SOJ.ROS/MAY27 340 P]", "cartera": "DERIVADOS",
           "ticker": "", "clase_activo": "", "emisor": ""}

    r = clase.proponer([call, put], None, usadas=["CALL OPCIONES"])
    # CALL está en la lista cerrada: se propone con fuente REGLA.
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("CALL OPCIONES", clase.REGLA)
    assert r[0]["nota"] == ""
    # PUT no está en la lista: la fila viaja vacía, con la nota que dice qué
    # falta cargar a mano — nunca se inventa la grafía.
    assert (r[1]["propuesto"], r[1]["fuente"]) == ("", "")
    assert "PUT OPCIONES" in r[1]["nota"] and "clase_activo" in r[1]["nota"]

    fondo = {"unidad": "[1024] CAFCI643-1024 - SBS Pesos Plus - Clase A",
             "cartera": "FCI", "ticker": "SBS Pesos Plus - Clase A",
             "clase_activo": "", "emisor": ""}
    fichas = [{"simbolo": "sbs pesos plus - clase a", "subyacente": "Mercado de Dinero",
              "moneda": "ARS", "cficode": "1"}]
    r = clase.proponer([fondo], fichas, usadas=["MM ARS"])
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("MM ARS", clase.PRIMARY)

    # Sin ficha en Primary, no se propone nada.
    r = clase.proponer([fondo], [], usadas=["MM ARS"])
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("", "")

    # Cartera HD: copia directa, fuente REGLA (misma regla que DERIVADOS pero
    # sin cotejar unidad/ticker — la cartera ES la clase).
    hd = {"unidad": "u_hd", "cartera": "HD", "ticker": "", "clase_activo": "",
          "emisor": ""}
    r = clase.proponer([hd], None, usadas=["HD"])
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("HD", clase.REGLA)

    # Un futuro (DERIVADOS sin C/P): la regla dice "FUTUROS DE MAIZ" sin
    # acento, pero la base ya usa la grafía con acento — se escribe ESA, no la
    # de la regla (lista cerrada tolerante a grafía, `en_lista_cerrada`).
    futuro = {"unidad": "[MAI.ROS/JUL27]", "cartera": "DERIVADOS", "ticker": "",
              "clase_activo": "", "emisor": ""}
    r = clase.proponer([futuro], None, usadas=["FUTUROS DE MAÍZ"])
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("FUTUROS DE MAÍZ", clase.REGLA)


def test_clase_activo_ars_se_propone_por_la_curva_del_master():
    """La regla CURVA: cartera ARS, ejes del bono en `mercado.curvas`
    (`ticker_corto` == `ticker` del asset, upper/strip)."""
    from agente import clase

    bono = {"unidad": "u_al30", "cartera": "ARS", "ticker": "al30",
            "clase_activo": "", "emisor": ""}
    master = [{"ticker_corto": "AL30", "ajuste": "cer", "moneda_eje": "ARS"}]

    r = clase.proponer([bono], None, usadas=["CER"], master=master)
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("CER", clase.CURVA)

    master_dual = [{"ticker_corto": "AL30", "ajuste": "cer",
                    "ajuste_alt": "tamar", "moneda_eje": "ARS"}]
    r = clase.proponer([bono], None, usadas=["DUAL"], master=master_dual)
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("DUAL", clase.CURVA)

    # El ticker del asset no está en el master: no se propone nada.
    r = clase.proponer([bono], None, usadas=["CER"], master=[])
    assert (r[0]["propuesto"], r[0]["fuente"]) == ("", "")


def test_deterministas_solo_lleva_regla_primary_y_curva():
    """`deterministas` es lo único que `CompletarFicha.solo` puede escribir
    sin que nadie apriete: filas con `propuesto` vacío no aportan nada."""
    from agente import clase

    filas = [
        {"unidad": "u1", "propuesto": "CALL OPCIONES", "fuente": clase.REGLA},
        {"unidad": "u2", "propuesto": "MM ARS", "fuente": clase.PRIMARY},
        {"unidad": "u3", "propuesto": "CER", "fuente": clase.CURVA},
        {"unidad": "u4", "propuesto": "", "fuente": ""},
    ]
    assert clase.deterministas(filas) == [
        {"unidad": "u1", "valor": "CALL OPCIONES"},
        {"unidad": "u2", "valor": "MM ARS"},
        {"unidad": "u3", "valor": "CER"},
    ]


# ═══ EL AVISO FALSO DE `bancos.mayor_movimientos` (AGENT.md §0.em) ═════════
#
# El 08/09 el agente cantó `bancos.mayor_movimientos · tabla_quieta` — *«no
# escribe hace 3,5 días y su cron dice cada 21,0 h como mucho»* — sobre una
# tabla que se estaba reescribiendo ENTERA cada quince minutos. Dos bugs
# independientes, y cada uno solo no alcanzaba para el aviso: los tres tests de
# abajo los separan y congelan el arreglo de cada uno.

def test_un_job_en_varias_lineas_del_crontab_es_un_solo_reloj():
    """El «21 h» del aviso no lo tipeó nadie: lo dedujo mal el parser.

    `jobs.mayor_sync` está declarado en TRES líneas del crontab —13:00-15:45
    cada 15', 16:00-17:30 cada 30', 18:00-21:00 cada hora— que juntas son una
    corrida continua de 13:00 a 21:00 UTC. El parser resolvía cada línea por
    separado, como si fueran horarios alternativos, y se quedaba con el hueco
    más chico de las tres: el de `0 18-21 * * 1-5`, que aislada admite 21 h.

    **No son alternativas: se COMPONEN.** El hueco real es 21:00 → 13:00 del
    día siguiente = 16 h. Medido: 7 de los 58 jobs del crontab tienen más de una
    línea y en los 7 el número viejo era falso.
    """
    from core import crontab

    # La aritmética, sin depender del archivo.
    assert crontab.hueco_maximo_union(
        ["*/15 13-15 * * 1-5", "0,30 16-17 * * 1-5", "0 18-21 * * 1-5"]
    ) == (16 * 3600, True)
    # Una sola línea no cambia de respuesta: `hueco_maximo` sigue contestando lo
    # de siempre (esto es lo que hace que el cambio no mueva las otras ~50).
    assert crontab.hueco_maximo("0 22 * * 1-5") == 86400
    assert crontab.hueco_maximo("*/30 12-23 * * *") == int(12.5 * 3600)
    assert crontab.hueco_maximo("0 12,14,16,18,20,22 * * 1-5") == 14 * 3600
    assert crontab.hueco_maximo("raro") is None

    # ⚠️ **EL FINDE NO PUEDE ESTAR ADENTRO DE ESTE NÚMERO.** Se descuenta acá y
    # lo vuelve a sumar `agente/tablas.py::_segundos_de_finde` según cuánto
    # finde hubo DE VERDAD entre la última escritura y ahora. Si el hueco
    # viernes→lunes se colara acá, todo job de días hábiles toleraría el triple
    # TODOS los días de la semana (REGLA #9: dos mitades, una definición).
    assert crontab.hueco_maximo_union(["0 22 * * 1-5"]) == (86400, True)

    # Y sobre el crontab de verdad: el ritmo que el agente le va a exigir.
    r = crontab.ritmo_declarado()
    assert r["jobs.mayor_sync"] == {"hueco_s": 16 * 3600, "solo_habiles": True}


def test_una_columna_date_nunca_es_un_sello_de_escritura():
    """El otro bug, y el que de verdad disparó el aviso.

    `tabla_quieta` pregunta *«¿hace cuánto que no escribe?»* y lo medía contra
    `fecha_conciliacion`, que dice **de qué día son los datos**. `mayor_sync`
    trae SIEMPRE el día hábil anterior, así que esa columna **nunca puede estar
    más fresca que T-1 hábil**: un martes a la mañana su valor correcto es el
    viernes, y contra el reloj eso son 3,5 días.

    La causa era la elección de columna: `COLS_FECHA` estaba escrita sólo en
    inglés (`actualizado_at` aparece 38 veces en `sql/schema.sql`, más que
    `updated_at`, y no estaba), así que la tabla caía al fallback «primera
    columna temporal del DDL» — que no es una elección, es el orden en que
    alguien tipeó el `CREATE TABLE`.

    **La regla nueva es de TIPO, no una lista de tablas**: un `date` no tiene
    hora, así que por construcción no puede ser un instante de escritura.
    """
    from agente.tablas import _elegir_col

    # El caso: el sello está al lado y ahora gana.
    assert _elegir_col(["fecha_conciliacion", "fecha_alta", "actualizado_at"],
                       ["date", "date", "timestamptz"]) == ("actualizado_at", False)
    # Sin ningún sello no se puede inventar uno: se usa la fecha de negocio y se
    # DICE (`es_fecha_negocio`), que es lo que permite tratarlas distinto.
    assert _elegir_col(["fecha"], ["date"]) == ("fecha", True)
    assert _elegir_col([], []) == (None, False)
    # ⚠️ Un sello de ALTA no sirve para medir frescura: no se mueve. Le gana
    # hasta un sello que no está en ninguna lista.
    assert _elegir_col(["creada_at", "ultima_corrida_at"],
                       ["timestamptz", "timestamptz"])[0] == "ultima_corrida_at"
    assert _elegir_col(["creado_at", "actualizado_at"],
                       ["timestamptz", "timestamptz"])[0] == "actualizado_at"
    assert _elegir_col(["creado_at"], ["timestamptz"])[0] == "creado_at"
    # Si el agregado paralelo del SQL viniera desalineado, lo que no se puede
    # clasificar se trata como SELLO: ante la duda se sigue midiendo.
    assert _elegir_col(["actualizado_at"], [])[0] == "actualizado_at"


def test_el_aviso_falso_del_mayor_no_vuelve_a_salir():
    """Los dos bugs juntos, con los números REALES del aviso del 08/09.

    Y la parte que importa entender: **arreglar el cron solo no alcanzaba.**
    Con el hueco corregido a 16 h pero midiendo todavía contra
    `fecha_conciliacion`, el aviso salía IGUAL. El que lo apaga es el cambio de
    columna; el del cron es lo que además le devuelve sensibilidad.
    """
    from datetime import UTC as _U
    from datetime import datetime as _dt

    from agente import tablas

    ahora = _dt(2026, 9, 8, 8, 54, tzinfo=_U)          # martes, antes de las 13
    viejo = {"hueco_s": 21 * 3600, "solo_habiles": True, "job": "jobs.mayor_sync"}
    nuevo = {"hueco_s": 16 * 3600, "solo_habiles": True, "job": "jobs.mayor_sync"}

    # ANTES: la fecha de NEGOCIO del último día cargado (viernes 04/09).
    negocio = {"cadencia": "intradiaria", "intervalo_p50_s": 900,
               "ultimo_dato": _dt(2026, 9, 4, tzinfo=_U)}
    assert tablas.frescura(negocio, ahora=ahora, declarado=viejo)["estado"] == "atrasada"
    # El cron arreglado NO lo salva: la columna sigue contestando otra pregunta.
    assert tablas.frescura(negocio, ahora=ahora, declarado=nuevo)["estado"] == "atrasada"

    # ⚠️ Y el veredicto DICE que midió contra una fecha de negocio: para las
    # tablas que no tienen ningún sello eso es lo mejor que se puede hacer, pero
    # el número es una COTA y quien lee la tarjeta tiene que saberlo (§0.em).
    assert tablas.frescura(negocio, ahora=ahora,
                           declarado=nuevo)["fecha_de_negocio"] is True

    # DESPUÉS: el sello de escritura — la última corrida fue el lunes 21:00 UTC,
    # que es exactamente lo que el cron manda. Está al día.
    sello = {"cadencia": "intradiaria", "intervalo_p50_s": 900,
             "ultimo_dato": _dt(2026, 9, 7, 21, 0, 12, tzinfo=_U)}
    f = tablas.frescura(sello, ahora=ahora, declarado=nuevo)
    assert f["estado"] == "ok" and f["fecha_de_negocio"] is False

    # ⚠️ Y NO se volvió ciego: si el job de verdad no corre desde el viernes,
    # con el sello bueno sigue gritando. Un detector que deja de avisar no está
    # arreglado, está apagado.
    muerto = {"cadencia": "intradiaria", "intervalo_p50_s": 900,
              "ultimo_dato": _dt(2026, 9, 4, 21, 0, 12, tzinfo=_U)}
    assert tablas.frescura(muerto, ahora=ahora, declarado=nuevo)["estado"] == "atrasada"
    # El lunes a la mañana, en cambio, el finde NO cuenta como atraso.
    assert tablas.frescura(
        muerto, ahora=_dt(2026, 9, 7, 12, 0, tzinfo=_U),
        declarado=nuevo)["estado"] == "ok"


def test_el_agente_no_se_queda_ciego_eligiendo_mal_el_sello():
    """Los tres modos de elegir un `timestamptz` que NO mide la escritura.

    Arreglar `date` vs `timestamptz` dejaba el MISMO defecto un nivel más abajo:
    entre dos sellos que no están en ninguna lista, volvía a decidir el orden del
    `CREATE TABLE`. Medido sobre `sql/schema.sql`, y los tres fallan distinto:

    · **Uno que no se mueve** — `operaciones.latidos` elegía `arrancado_at` (una
      vez, al arrancar el proceso) en vez de `latido_at`, que se escribe cada
      15 s. Falla ruidoso: canta la tabla como caída.
    · **Uno que sólo tienen algunas filas** — `anulado_en` describe la última
      anulación, no la última escritura.
    · **Uno FUTURO** — `manager.tokens_externos` elegía `expira_at`. Ese falla
      CALLADO y es el peor: `max()` da un instante que todavía no llegó, el
      atraso sale negativo y la tabla queda en verde **para siempre**. Un aviso
      falso se ve y se vota; una tabla que nunca avisa no se ve nunca.
    """
    from agente.tablas import _elegir_col

    ts = ["timestamptz", "timestamptz"]
    # El que se mueve gana, aunque nadie lo haya anotado en una lista.
    assert _elegir_col(["arrancado_at", "latido_at"], ts)[0] == "latido_at"
    assert _elegir_col(["first_seen", "last_seen"], ts)[0] == "last_seen"
    # `ultimo_dato` DICE «último» pero es un dato copiado de otra tabla; la
    # convención declarada (`medido_at`) le gana a la regla de morfemas.
    assert _elegir_col(["ultimo_dato", "medido_at"], ts)[0] == "medido_at"
    # Un sello de alta le gana a uno que sólo tienen algunas filas: en una tabla
    # que sólo appendea, `ingestado_en` ES el instante de escritura.
    assert _elegir_col(["ingestado_en", "anulado_en"], ts)[0] == "ingestado_en"
    # Un vencimiento no se elige nunca…
    assert _elegir_col(["expira_at", "obtenido_at", "dia", "llamadas_at"],
                       ["timestamptz", "timestamptz", "date",
                        "timestamptz"])[0] == "llamadas_at"
    # …y si es lo ÚNICO que hay, la tabla queda SIN columna —fuera del detector—
    # antes que en verde por un atraso negativo.
    assert _elegir_col(["expira_at"], ["timestamptz"]) == (None, False)


def test_un_cron_de_fin_de_semana_no_se_marca_como_de_dias_habiles():
    """`solo_habiles` sale del campo día-de-semana != `*`, y eso lo cumple
    también un cron que corra SÓLO sábado y domingo. Marcarlo así sería mentira
    dos veces: `hueco_maximo_union` le descontaría el finde —que es justo cuando
    corre— y `tablas.frescura` le sumaría `_segundos_de_finde` encima.

    La grilla contesta lo que el campo no puede: ¿dispara algún lunes-a-viernes?
    """
    from core import crontab

    assert crontab.hueco_maximo_union(["0 6 * * 6,0"])[1] is False
    assert crontab.hueco_maximo_union(["0 22 * * 1-5"])[1] is True
    # Y el caso real que mezcla los dos: `market_quotes` corre L-V de día y
    # Ma-Sá de madrugada (el corrimiento ART→UTC). Tiene corridas hábiles, así
    # que SÍ es de días hábiles, y el hueco es el de la mañana (~8 h), no el del
    # fin de semana entero.
    hueco, habiles = crontab.hueco_maximo_union(["* 10-23 * * 1-5", "* 0-1 * * 2-6"])
    assert habiles is True and 8 * 3600 <= hueco < 9 * 3600


def test_una_columna_que_mira_al_futuro_no_da_verde_eterno():
    """Lo destapó el diag en prod (§0.em), y era invisible por diseño.

    `mercado.dias_habiles` es un CALENDARIO: su `fecha` máxima es el 31/12. Con
    el último valor adelante del reloj, el atraso sale NEGATIVO y la tabla pasa
    todas las tolerancias **para siempre**. No estaba al día: la pregunta no se
    podía hacer contra esa columna, y nadie lo sabía.

    Mismo veneno que `expira_at`, que se resuelve no eligiendo la columna. Acá
    es la única que hay, así que se contesta lo único cierto: **no se puede
    saber**. Un «no sé» declarado se ve en el tablero; un verde falso, nunca.
    """
    from datetime import UTC as _U
    from datetime import datetime as _dt

    from agente import tablas

    ahora = _dt(2026, 9, 8, 12, 0, tzinfo=_U)
    calendario = {"cadencia": "diaria", "col_fecha": "fecha",
                  "ultimo_dato": _dt(2026, 12, 31, tzinfo=_U)}
    f = tablas.frescura(calendario, ahora=ahora)
    assert f["estado"] == "no_se_puede_saber" and f["atraso_s"] is None
    assert "FUTURO" in f["motivo"]
    # Y no se rompe lo de siempre: una fecha pasada sigue midiéndose igual.
    assert tablas.frescura({"cadencia": "diaria", "col_fecha": "fecha",
                            "ultimo_dato": _dt(2026, 9, 7, tzinfo=_U)},
                           ahora=ahora)["estado"] == "ok"


def test_hd_1816_al_ccl_canta_el_pipeline_y_no_el_bono(monkeypatch):
    """El caso real (2026-09-09, §0.ez): RESEARCH → RENTA FIJA ARGENTINA mostraba
    la TEA de los Bonares y Globales al CCL de 1816 y no al MEP.

    Lo difícil no era el bug, era que **no falla nada**: pedirle a 1816 sin decir
    la moneda devuelve un número plausible, calculado con el dólar de ellos.
    Medido en prod, BPOB7 daba 7,26% en `ars` contra 2,44% en `mep`.

    Tres cosas congela este test:
      · el sujeto es la TABLA (el pipeline), no el bono — se rompen los 21
        juntos o ninguno, y 21 filas en AHORA diciendo lo mismo es ruido;
      · **`monedaPago`, no `monedaDenom`**: un dólar-linked está denominado en
        USD y paga en pesos, así que `ars` es lo CORRECTO para él y no puede
        salir como hallazgo (1816 ni siquiera publica `mep` para esos);
      · un ticker sin ficha en el catálogo no se da por bueno: se pide con el
        default `ars` y, si paga en dólares, queda al CCL sin que se note.
    """
    from datetime import datetime as _dt

    from agente import fuentes, reloj
    from agente.detectores import datos as det

    filas = [
        # Hard dollar guardado en `ars` = al CCL. Tiene que cantar.
        {"tabla": "research.mkt_1816_series", "ticker": "GD30",
         "moneda_pago": "USD", "moneda": "ars", "filas": 1364, "desde": "2025-07-21"},
        {"tabla": "research.mkt_1816_series", "ticker": "AL30",
         "moneda_pago": "USD", "moneda": "ars", "filas": 1364, "desde": "2025-07-21"},
        # ⚠️ EL CASO QUE ROMPÍA LA PRIMERA VERSIÓN: un bono YA rebajado tiene
        # LAS DOS series. La `ars` se conserva a propósito (es el tramo que la
        # API ya no deja rebajar) y la lectura la ignora. Juzgando fila por
        # fila, esa `ars` intencional era un hallazgo eterno.
        {"tabla": "research.mkt_1816_series", "ticker": "AE38",
         "moneda_pago": "USD", "moneda": "ars", "filas": 1364, "desde": "2025-07-21"},
        {"tabla": "research.mkt_1816_series", "ticker": "AE38",
         "moneda_pago": "USD", "moneda": "mep", "filas": 980, "desde": "2025-09-10"},
        # Otro pipeline, otro sujeto.
        {"tabla": "agente.tasa_1816", "ticker": "BPOB7",
         "moneda_pago": "USD", "moneda": "ars", "filas": 1, "desde": "2026-09-09"},
        # Dólar-linked: denominado en USD, PAGA en pesos → `ars` está bien.
        {"tabla": "research.mkt_1816_series", "ticker": "TZV27",
         "moneda_pago": "ARS", "moneda": "ars", "filas": 520, "desde": "2026-01-05"},
        # Bono en pesos de toda la vida.
        {"tabla": "research.mkt_1816_series", "ticker": "TX26",
         "moneda_pago": "ARS", "moneda": "ars", "filas": 672, "desde": "2026-01-05"},
    ]
    monkeypatch.setattr(fuentes, "monedas_1816", lambda: filas)
    monkeypatch.setattr(reloj, "ahora_utc",
                        lambda a=None: _dt(2026, 9, 9, 15, 0, tzinfo=UTC))

    por = {h.sujeto: h for h in det.hd_1816_al_ccl({"sin_ficha_max": 0})}
    assert set(por) == {"research.mkt_1816_series", "agente.tasa_1816"}

    h = por["research.mkt_1816_series"]
    assert h.regla == "al_ccl" and h.severidad == "alta"
    # Los bonos van en `evidencia["items"]`, no cada uno en su fila.
    assert h.evidencia["items"] == ["AL30", "GD30"]
    assert h.evidencia["filas"] == 1364 * 2
    assert "TZV27" not in h.evidencia["items"], "un dólar-linked paga en pesos"
    assert "AE38" not in h.evidencia["items"], (
        "AE38 tiene las dos series: lo que importa es que EXISTA la de `mep`, "
        "no que sobre la vieja en `ars` — si no, el hallazgo sería eterno")
    assert h.que_hacer.strip()

    # Sin ficha en el catálogo: no se afirma que esté mal, se canta el agujero.
    sin_ficha = [{"tabla": "agente.tasa_1816", "ticker": "XXXX",
                  "moneda_pago": None, "moneda": "ars", "filas": 1, "desde": None}]
    monkeypatch.setattr(fuentes, "monedas_1816", lambda: sin_ficha)
    hs = det.hd_1816_al_ccl({"sin_ficha_max": 0})
    assert [x.regla for x in hs] == ["sin_ficha_1816"]
    assert hs[0].evidencia["items"] == ["XXXX"]
    # Y el umbral se respeta: con tolerancia 1, ese mismo caso no canta.
    assert det.hd_1816_al_ccl({"sin_ficha_max": 1}) == []

    # Nada limpio → nada que decir.
    monkeypatch.setattr(fuentes, "monedas_1816",
                        lambda: [f for f in filas if f["moneda_pago"] != "USD"])
    assert det.hd_1816_al_ccl({"sin_ficha_max": 0}) == []

    # No pude leer ≠ está todo bien (invariante #6).
    monkeypatch.setattr(fuentes, "monedas_1816", lambda: None)
    with pytest.raises(tipos.SinDatos):
        det.hd_1816_al_ccl({})


def test_hd_1816_al_ccl_esta_conectada_y_es_un_aviso():
    """La estructura (REGLA #10). Y una en particular: **no tiene arreglo, a
    propósito**.

    Rehacer una serie de 1816 cuesta créditos y es un backfill — REGLA #4, o sea
    que no puede salir de un botón del tablero. Un `arreglos` vacío por olvido y
    uno vacío por decisión se ven igual en el código; lo que los distingue es que
    este test lo exija.
    """
    from agente.catalogo import HABILIDADES
    from agente.detectores import datos as det

    h = HABILIDADES["hd_1816_al_ccl"]
    assert h.dominio == "DATOS" and h.tipo == "detector"
    assert h.correr is det.hd_1816_al_ccl
    assert h.arreglos == {}, "es un AVISO: el arreglo es un backfill (REGLA #4)"
    assert "sin_ficha_max" in h.umbrales, "el umbral se declara acá, no en el detector"
    assert h.ventana == "siempre", "no depende de rueda ni de precio"

    # El detector no escribe: solo `agente/registro.py` escribe hallazgos.
    fuente = inspect.getsource(det.hd_1816_al_ccl)
    for prohibido in ("INSERT", "UPDATE ", "DELETE", "registro."):
        assert prohibido not in fuente, f"el detector no puede {prohibido}"

    # La regla de qué moneda corresponde NO se reimplementa acá: se deriva del
    # cliente, que es de donde la toman también los jobs (REGLA #9).
    assert "moneda_series" in fuente


# ── tabla_quieta: sin dato vivo no se juzga (§0.fa) ────────────────────────

def _pool_de_maximos(monkeypatch, *, muertas: set[str], fecha):
    """Una base de mentira para `_ultimo_dato_vivo`: cualquier query que nombre
    una tabla muerta falla —el viaje único con todas, y de a una sólo esa—; el
    resto contesta `fecha`."""
    from unittest.mock import MagicMock

    def execute(sql, *_a):
        for m in muertas:
            if f'"{m}"' in sql:
                raise RuntimeError(f'relation "{m}" does not exist')
    cur = MagicMock()
    cur.execute.side_effect = execute
    cur.fetchone.return_value = (fecha,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    # `tablas` liga `get_pool` al importar: se parchea el nombre que usa.
    from agente import tablas
    monkeypatch.setattr(tablas, "get_pool", lambda: pool)
    return cur


def test_una_tabla_muerta_no_deja_sin_dato_vivo_a_las_demas(monkeypatch):
    """§0.fa. `estrategia.resultados` ya no existía, el perfil la recordaba, y el
    `UNION ALL` de las 72 fallaba entero en cada pasada: 0 de 72 leídas, y las
    72 juzgadas con la foto de ayer. Ahora el viaje único que falla se rehace de
    a una: vuelven las que sí están, y la muerta vuelve con su motivo."""
    from datetime import datetime

    from agente import tablas
    hoy = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    _pool_de_maximos(monkeypatch, muertas={"resultados"}, fecha=hoy)
    perfiles = [{"schema": "mercado", "tabla": "cedears_ohlc_daily", "col_fecha": "fecha"},
                {"schema": "estrategia", "tabla": "resultados", "col_fecha": "resuelto_at"},
                {"schema": "mercado", "tabla": "eikon_cierres", "col_fecha": "fecha"}]
    leidas, fallidas = tablas._ultimo_dato_vivo(perfiles)
    assert leidas == {0: hoy, 2: hoy}
    assert set(fallidas) == {1} and "does not exist" in fallidas[1]


def _tabla_quieta_con(monkeypatch, perfiles, vivo, fallidas):
    """Le pone al detector un perfil y una lectura viva de mentira, y apaga el
    barrido, el contrato y el filtro de schemas."""
    from datetime import datetime

    from agente import peso, tablas
    ahora = datetime(2026, 9, 11, 12, 19, tzinfo=UTC)
    for p in perfiles:
        p.setdefault("cadencia", "diaria_habil")
        p.setdefault("col_fecha", "fecha")
        p.setdefault("intervalo_p50_s", 86400)
        p.setdefault("filas", 10)
        p.setdefault("medido_at", ahora)
    monkeypatch.setattr(sistema, "_perfil_vencido", lambda *a, **k: False)
    monkeypatch.setattr(tablas, "perfiles", lambda solo_con_ritmo=False: perfiles)
    monkeypatch.setattr(tablas, "_ya_tienen_contrato", lambda: set())
    monkeypatch.setattr(tablas, "_ultimo_dato_vivo", lambda _p: (vivo, fallidas))
    monkeypatch.setattr(tablas, "declarados", lambda: {})
    monkeypatch.setattr(peso, "schemas_nuestros", lambda: set())
    return sistema.tabla_quieta({})


def test_sin_dato_vivo_no_se_juzga_con_la_foto(monkeypatch):
    """§0.fa. La foto dice que `x` escribió hace cinco días. Si la lectura viva
    de `x` falló, eso NO es un hallazgo: es un `NoMirado`. Juzgar con la foto es
    exactamente lo que puso diez tablas sanas en rojo a las 09:19."""
    from datetime import datetime, timedelta
    ahora = datetime(2026, 9, 11, 12, 19, tzinfo=UTC)
    foto_vieja = ahora - timedelta(days=5)
    perfiles = [{"schema": "mercado", "tabla": "sana", "ultimo_dato": foto_vieja},
                {"schema": "mercado", "tabla": "muerta", "ultimo_dato": foto_vieja}]
    out = _tabla_quieta_con(monkeypatch, perfiles,
                            vivo={0: ahora}, fallidas={1: "relation does not exist"})
    assert [type(o).__name__ for o in out] == ["NoMirado"]
    assert out[0].sujeto == "mercado.muerta" and out[0].regla == "sin_escribir"
    assert "does not exist" in out[0].motivo


def test_si_no_se_pudo_leer_ninguna_tabla_la_habilidad_no_miro(monkeypatch):
    """§0.fa. Cero leídas de N no es «N están al día» ni «N están quietas»: es
    `SinDatos`, y el motor no cierra nada."""
    from datetime import datetime
    ahora = datetime(2026, 9, 11, 12, 19, tzinfo=UTC)
    perfiles = [{"schema": "mercado", "tabla": "a", "ultimo_dato": ahora},
                {"schema": "mercado", "tabla": "b", "ultimo_dato": ahora}]
    with pytest.raises(tipos.SinDatos):
        _tabla_quieta_con(monkeypatch, perfiles, vivo={}, fallidas={0: "caído", 1: "caído"})


def test_no_mirado_no_crea_ni_cierra_y_queda_dicho(monkeypatch):
    """§0.fa. `registro.guardar` separa los `NoMirado`: el sujeto entra a
    `vistos` (lo que tuviera abierto NO se cierra por ausencia), no pasa por
    `_ver` (no nace un hallazgo), y la corrida queda `ok` con «no pude mirar»
    en `ultimo_error`."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(registro, "get_pool", lambda: MagicMock())
    sellos, vistos_cerrar, creados = [], [], []
    monkeypatch.setattr(registro, "sellar_corrida",
                        lambda h, **k: sellos.append(k))
    monkeypatch.setattr(registro, "_silenciados", lambda conn, h: set())
    monkeypatch.setattr(registro, "_ver",
                        lambda conn, h, x: (creados.append(x), {"nacio": True, "reincidio": False})[1])
    monkeypatch.setattr(registro, "_cerrar_ausentes",
                        lambda conn, h, vistos: (vistos_cerrar.extend(vistos), 0)[1])
    real = tipos.Hallazgo(sujeto="mercado.b", regla="sin_escribir", severidad="media",
                          problema="quieta", que_hacer="Relanzar jobs.b.")
    r = registro.guardar("tabla_quieta",
                         [real, tipos.NoMirado("mercado.a", "sin_escribir", "does not exist")])
    assert creados == [real]
    assert ("mercado.a", "sin_escribir") in vistos_cerrar
    assert r["no_mirados"] == 1 and r["resultado"] == tipos.OK
    # UNA sola vez: sellar dos veces suma `corridas_hoy` dos veces.
    assert len(sellos) == 1
    assert "no pude mirar 1: mercado.a" in sellos[0]["error"]


def test_el_barrido_olvida_las_tablas_que_murieron():
    """§0.fa. El upsert del perfil agregaba y actualizaba, y una tabla borrada
    de la base quedaba en la memoria para siempre — y tumbaba la lectura viva
    de las otras 71. Lo que no está en el catálogo se borra del perfil."""
    from agente import tablas
    assert "DELETE FROM manager.tabla_perfil" in _codigo(tablas.barrer)
    # Y el detector ya no lleva `ultimo_vivo`: no hay veredicto sin dato vivo.
    assert "ultimo_vivo" not in _codigo(sistema.tabla_quieta)
    assert "NoMirado(" in _codigo(sistema.tabla_quieta)


# ── tabla_quieta: la planilla del job antes que la tarjeta (§0.fb) ─────────

def _corrida(started, status="ok"):
    from datetime import timedelta
    return {"started_at": started, "finished_at": started + timedelta(seconds=40),
            "status": status, "stats": {}}


def test_la_corrida_que_escribio_el_dato_no_cuenta_como_posterior(monkeypatch):
    """§0.fb. Tabla de fecha de NEGOCIO: el dato del jueves 10 lo escribe la
    corrida del jueves 17:15. Esa corrida arrancó ANTES de que el día 10
    cerrara (viernes 00:00), así que no es «posterior al último dato». Con
    `finished_at` en vez de `started_at` una tabla de sello contaría siempre
    una corrida de más."""
    from datetime import datetime

    from agente import fuentes
    jue = datetime(2026, 9, 10, 20, 15, tzinfo=UTC)
    vie = datetime(2026, 9, 11, 20, 15, tzinfo=UTC)
    monkeypatch.setattr(fuentes, "corridas", lambda tipo, n: [_corrida(vie), _corrida(jue)])
    cierre_del_10 = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)
    v, pl = sistema._planilla("jobs.cedears_ohlc_daily", cierre_del_10, umbral=1)
    assert v == sistema.ESCALAR and pl["corridas_ok"] == 1


def test_un_motor_no_tiene_planilla():
    """Un motor (`engines.x`) no anota corridas: se canta como siempre."""
    from datetime import datetime
    v, _ = sistema._planilla("engines.valores", datetime(2026, 9, 11, tzinfo=UTC), 4)
    assert v == sistema.SIN_PLANILLA


def _quieta_con_planilla(monkeypatch, corridas, umbral=4):
    """Una tabla diaria atrasada de verdad (último dato hace 5 días, leído en
    VIVO) con la planilla que se le dé al job que la escribe."""
    from datetime import datetime, timedelta

    from agente import fuentes
    from core import escribe
    ahora = datetime(2026, 9, 11, 12, 19, tzinfo=UTC)
    monkeypatch.setattr(fuentes, "corridas", lambda tipo, n: corridas)
    monkeypatch.setattr(escribe, "que_relanzar", lambda t: "jobs.fred_research")
    monkeypatch.setattr(escribe, "la_dispara", lambda t: "reloj")
    monkeypatch.setattr(catalogo, "umbrales_de", lambda n: {"corridas_ok_sin_avanzar": umbral})
    viejo = ahora - timedelta(days=5)
    perfiles = [{"schema": "research", "tabla": "fred_observations", "ultimo_dato": viejo,
                 "col_fecha": "ingestado_en"}]
    out = _tabla_quieta_con(monkeypatch, perfiles, vivo={0: viejo}, fallidas={})
    return out


def test_sin_planilla_se_canta_como_siempre(monkeypatch):
    out = _quieta_con_planilla(monkeypatch, corridas=[])
    assert [h.regla for h in out] == ["sin_escribir"]


def test_si_el_job_fallo_lo_canta_salud_y_tabla_quieta_calla(monkeypatch):
    """§0.fb / REGLA #9: «el job falló» es UN hecho y lo canta `salud` para todo
    el crontab. Dos tarjetas por la misma caída es cómo se deja de leer una."""
    from datetime import datetime
    hoy = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    out = _quieta_con_planilla(monkeypatch, corridas=[_corrida(hoy, "error")])
    assert out == []


def test_si_el_job_corrio_ok_pocas_veces_la_fuente_no_publico_y_se_calla(monkeypatch):
    """§0.fb. El BCRA no publicó un día nuevo: el job corrió ok, trajo cero, la
    tabla no avanzó. No hay nada roto y «relanzar el job» no cambia nada."""
    from datetime import datetime, timedelta
    hoy = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    out = _quieta_con_planilla(monkeypatch, corridas=[
        _corrida(hoy - timedelta(days=i)) for i in range(3)])
    assert out == []


def test_si_el_job_corrio_ok_muchas_veces_y_la_tabla_no_avanza_se_escala(monkeypatch):
    """§0.fb. Cuatro corridas ok seguidas sin que la tabla avance ya no es «la
    fuente no publicó hoy»: o la fuente lleva días muda, o el job escribe y la
    columna no se mueve. Regla propia, y el `que_hacer` nombra cómo separarlas."""
    from datetime import datetime, timedelta
    hoy = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    out = _quieta_con_planilla(monkeypatch, corridas=[
        _corrida(hoy - timedelta(days=i)) for i in range(4)])
    assert [h.regla for h in out] == ["corre_ok_sin_avanzar"]
    h = out[0]
    assert h.evidencia["corridas_ok"] == 4 and "jobs.fred_research" in h.que_hacer
    assert "python -m jobs.fred_research" in h.que_hacer


def test_el_umbral_de_la_planilla_esta_declarado_en_el_catalogo():
    assert catalogo.HABILIDADES["tabla_quieta"].umbrales["corridas_ok_sin_avanzar"] >= 2
