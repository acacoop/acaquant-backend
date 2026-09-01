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

from agente import arreglos, catalogo, registro, rehacer, tipos
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
    src = inspect.getsource(registro._cerrar_ausentes)
    # Se mira el SQL, no el archivo entero: el comentario que explica el bug
    # nombra `chr(0)` a propósito, y un test que falla por su propia
    # documentación no prueba nada.
    sql = src[src.index("cur.execute("):]
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
    assert "reloj.en_cierre" in inspect.getsource(motor._le_toca)


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


def test_no_quedan_tareas_de_ia_del_agente_viejo():
    """AGENT 2.0 no usa IA en ninguna habilidad. Dejar la CONFIG de tres tareas
    que nadie puede invocar hace creer lo contrario al que lee el gateway.

    ⚠️ El gateway (`core/ai.py` + `core/llm.py`) EXISTE pero **no tiene ninguna
    tarea productiva** desde el 2026-08-28: se conservó el núcleo a propósito
    —transporte, ruteo fail-closed, presupuesto y traza— y se borró todo lo que
    lo usaba. Este test cubre las dos mitades: que el agente no declare `usa_ia`,
    y que no reaparezca la config de las tareas del agente viejo.
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


def test_un_arreglo_que_pide_datos_lo_declara():
    """El front no puede adivinar cuáles llevan listado editable y cuáles son un
    botón: adivinar significa una lista de ids en el navegador que nadie mantiene
    igual a la de acá. Se declara con `pide_datos` y viaja en el catálogo."""
    from agente import arreglos

    piden = {a.id for a in arreglos.ARREGLOS.values() if a.pide_datos}
    assert piden == {"completar_ficha"}, (
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
    i = src.index("for job in rehacer.REHACIBLES")
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
    ns: dict = {"_norm": lambda x: (x or "").strip(),
                "CARTERA_DERIVADOS": "DERIVADOS", "EMISOR_DERIVADOS": "OTROS",
                "_PREFIJOS_AGRO": ("MAI.", "SOJ.", "TRI."),
                "_regla_ticker": lambda r: {}}
    for n in ("_regla_derivados_otc", "_regla_emisor_derivados"):
        exec(compile(_ast.Module([fns[n]], []), "<t>", "exec"), ns)
    regla = ns["_regla_emisor_derivados"]

    assert regla({"unidad": "[GFGC8000OC]", "cartera": "DERIVADOS"}) == {"emisor": "OTROS"}
    # Y también para el que TODAVÍA no tiene cartera pero la va a recibir en
    # esta misma pasada: si no, un OTC nuevo esperaría a la corrida de mañana.
    assert regla({"unidad": "[OTC - MAI.ROS/JUL26]", "cartera": ""}) == {"emisor": "OTROS"}
    # Nada más se toca.
    assert regla({"unidad": "[8295] BHP", "cartera": "RENTA VARIABLE"}) == {}
    assert regla({"unidad": "[AL30]", "cartera": "HD"}) == {}

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


def test_soberanos_faltantes_no_descarta_en_silencio(monkeypatch):
    """S29E7 (2026-09-01): la habilidad corrió 4 veces, guardó 1 hallazgo y el
    resto de los licitados nuevos cayó en un `continue` con log.info porque la
    FOTO de Primary tenía 17 días. «Miré y guardé lo que vi» sobre un descarte
    invisible es un detector que dejó de mirar. El descarte se canta como AVISO,
    con la fecha de la foto — y el que está en cartera sigue pidiendo el alta."""
    from datetime import datetime
    from types import SimpleNamespace

    from agente import fuentes
    from core import curvas_ejes, curvas_sql

    monkeypatch.setattr(fuentes, "universo_1816",
                        lambda: _universo_1816("S29E7", "X29E7", "T30E7"))
    monkeypatch.setattr(fuentes, "master", lambda: [{"ticker_corto": "AL30"}])
    monkeypatch.setattr(fuentes, "en_cartera", lambda: {"S29E7"})
    monkeypatch.setattr(fuentes, "tickers_en_primary", lambda: {"X29E7", "AL30"})
    monkeypatch.setattr(fuentes, "primary_fecha",
                        lambda: datetime(2026, 8, 15, 19, 23, tzinfo=UTC))
    monkeypatch.setattr(curvas_ejes, "desde_1816",
                        lambda c: SimpleNamespace(emisor_tipo="soberano", moneda="ARS"))
    monkeypatch.setattr(curvas_sql, "calendario_habil", lambda: set())
    monkeypatch.setattr(curvas_sql, "sale_del_master", lambda *a, **k: False)

    por = {h.sujeto: h for h in mercado.soberanos_faltantes({})}
    assert por["S29E7"].regla == "no_esta_en_curvas", "en cartera → pide el alta"
    assert por["X29E7"].regla == "no_esta_en_curvas", "cotiza → pide el alta"
    assert por["T30E7"].regla == "no_cotiza_en_primary", "no cotiza → AVISO, no silencio"
    assert por["T30E7"].severidad == "baja"
    assert "15/08" in por["T30E7"].problema, "la fecha de la foto va en el texto"
    assert por["T30E7"].evidencia["foto_primary_de"].startswith("2026-08-15")
    # Sin arreglo declarado → aviso: vive en AHORA, nunca en ENCONTRÓ.
    assert catalogo.HABILIDADES["soberanos_faltantes"].arreglo_de("no_cotiza_en_primary") == ""


def test_la_foto_de_primary_tiene_cron_y_quien_la_vigile():
    """La foto la escribía un script manual que nadie corría. Ahora: un cron en
    el repo (antes del cleanup y de los motores), un detector que lee ESE cron
    —no una copia del horario— y canta si un día no corrió, y un script que se
    niega a pisar la foto con una respuesta parcial de Primary."""
    from agente import crontab
    cron = sistema._cron_discovery(crontab.del_repo())
    assert cron is not None, "deploy/crontab.txt no corre scripts.discovery_pyrofex"
    hora, minuto = cron
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
    # Lunes 11:00 UTC: la última esperada es la del VIERNES (no hay cron el finde).
    lunes = datetime(2026, 8, 31, 11, 0, tzinfo=UTC)
    assert sistema._ultima_esperada(lunes, 12, 15, 60) == datetime(2026, 8, 28, 12, 15, tzinfo=UTC)
    # Martes 13:00 UTC: la de hoy todavía está en gracia → sigue valiendo la de ayer.
    assert sistema._ultima_esperada(datetime(2026, 9, 1, 13, 0, tzinfo=UTC), 12, 15, 60) \
        == datetime(2026, 8, 31, 12, 15, tzinfo=UTC)
    assert sistema._ultima_esperada(datetime(2026, 9, 1, 13, 30, tzinfo=UTC), 12, 15, 60) \
        == datetime(2026, 9, 1, 12, 15, tzinfo=UTC)

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
    src = inspect.getsource(alta._estado_simbolo)
    assert "simbolos_live" in src and "primary_fecha" in src
    assert "foto_vieja" in src
    pre = inspect.getsource(alta)
    assert "(INFO if foto_vieja else OK) if con is True" in pre
