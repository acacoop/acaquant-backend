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


def test_el_boton_de_rehacer_solo_aparece_donde_puede_funcionar():
    """El detector y el arreglo **normalizan el nombre del job igual**.

    Si difirieran, el detector ofrecería el botón y el botón contestaría «ese
    job no se puede rehacer»: la clase de contradicción que hace que la gente
    deje de creerle a la pantalla. Es el mismo defecto que tenía `salud`, que
    declaraba una acción con la puerta de solo lectura.
    """
    from agente import arreglos as arr
    from agente.detectores.sistema import _rehacible

    det = inspect.getsource(_rehacible)
    apl = inspect.getsource(arr.RehacerJob._job_fecha)
    for frag in ('split(":")[-1]', 'removeprefix("jobs.")'):
        assert frag in det and frag in apl, (
            f"«{frag}» tiene que estar en los dos lados o van a discrepar")


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
    src = inspect.getsource(fuentes.primary)
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
