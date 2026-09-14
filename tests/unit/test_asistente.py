"""EL ASISTENTE NO VE LA BASE ENTERA.

El asistente consulta datos del negocio —tenencias, valuaciones, cuentas— y lo
hace a pedido de un modelo, que decide solo qué herramienta usar y con qué
argumentos. Eso quiere decir que el alcance NO puede depender de que el que
escribe una herramienta se acuerde de filtrar: se acuerda las primeras veces.

Estos tests congelan las dos garantías:

  1. Toda consulta que toque datos de cuentas lleva el filtro de permiso.
  2. Sin cuentas habilitadas no se muestra NADA (y no "todo").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = (RAIZ / "asistente" / "herramientas.py").read_text(encoding="utf-8")

# Las tablas que tienen datos de una cuenta. Una consulta que las nombre y no
# filtre está mostrando cuentas que nadie habilitó.
TABLAS_DE_CUENTAS = ("portafolio.tenencia", "portafolio.tenencia_live",
                     "operaciones.operaciones", "valuaciones.portfolio_snapshot",
                     "operaciones.acreencias", "clientes.cuentas")


def _cuerpo(nombre: str) -> str:
    """El código de UNA función del archivo, hasta la siguiente. Cortar por
    caracteres se pisa con la función de al lado — y el test pasa a medir otra
    cosa sin que nadie se entere."""
    resto = FUENTE.split(f"\ndef {nombre}(", 1)[1]
    return resto.split("\ndef ", 1)[0]


def _codigo(nombre: str) -> str:
    """El cuerpo de una función SIN su docstring. Hace falta porque el docstring
    habla de lo que la función NO hace («no devuelve nominales»), y un test que
    busque esa palabra en todo el texto se dispara con su propia explicación."""
    c = _cuerpo(nombre)
    partes = c.split('"""')
    return partes[0] + "".join(partes[2:]) if len(partes) > 2 else c


def _consultas(src: str) -> list[str]:
    """Los bloques de SQL del archivo: todo lo que va entre triples comillas y
    tiene un SELECT adentro."""
    return [b for b in re.findall(r'"""(.*?)"""', src, re.S)
            if re.search(r"\bSELECT\b", b, re.I)]


def test_toda_consulta_a_datos_de_cuentas_lleva_el_permiso():
    """⚠️ **LA GARANTÍA PRINCIPAL.** Si una herramienta nueva consulta
    tenencias y se olvida del filtro, devuelve la cartera de TODA la casa — y
    no falla: contesta, con números que parecen buenos.

    Se mira el texto del SQL y no el resultado a propósito: probar el resultado
    necesitaría una base con datos de varias cuentas, y esto tiene que fallar
    en CI, antes de que la consulta llegue a correr una sola vez.
    """
    from asistente import permitido

    # Vale de las dos formas: el filtro escrito tal cual, o interpolado desde
    # la constante en un f-string. La segunda es incluso mejor —no se puede
    # desincronizar de `permitido.py`—, y es la que usa el código hoy.
    #
    # (Este test se cazó a sí mismo la primera vez: buscaba sólo el valor
    # expandido y el código usa `f"""…{permitido.FILTRO_SQL}…"""`, así que en
    # el fuente está el nombre, no el valor.)
    formas = (permitido.FILTRO_SQL, "{permitido.FILTRO_SQL}")

    for sql in _consultas(FUENTE):
        toca = [t for t in TABLAS_DE_CUENTAS if t in sql]
        if not toca:
            continue
        assert any(f in sql for f in formas), (
            f"esta consulta lee {toca} y NO lleva `permitido.FILTRO_SQL`:\n"
            f"{sql.strip()[:300]}…")


def test_sin_cuentas_habilitadas_no_se_muestra_NADA():
    """No se muestra todo: no se muestra nada, y se dice por qué.

    Es la diferencia entre un permiso y un filtro. Un filtro que no está
    configurado deja pasar todo; un permiso que falta corta.
    """
    # Con la lista vacía, `parametros()` LEVANTA — no devuelve un filtro que
    # matchee todo, que es el error que este test existe para impedir.
    import os

    from asistente import permitido
    antes = os.environ.get(permitido.CLAVE_ENV)
    try:
        os.environ[permitido.CLAVE_ENV] = ""
        assert permitido.cuentas() == []
        with pytest.raises(permitido.SinPermiso):
            permitido.parametros()
        # Y el mensaje que le llega al modelo le prohíbe estimar.
        e = permitido.como_error()
        assert e["error"] and "NO estimes" in e["que_hacer"]
    finally:
        if antes is None:
            os.environ.pop(permitido.CLAVE_ENV, None)
        else:
            os.environ[permitido.CLAVE_ENV] = antes


def test_el_permiso_se_declara_FUERA_del_codigo():
    """Las cuentas van en el `.env` del Droplet, no en un archivo del repo.

    Dos motivos: los números de cuenta no quedan en la historia de git para
    siempre, y para ampliarlos hay que entrar al servidor — el mismo nivel de
    acceso que hace falta para correr el asistente.
    """
    fuente = (RAIZ / "asistente" / "permitido.py").read_text(encoding="utf-8")
    assert 'os.getenv(CLAVE_ENV' in fuente, "el permiso tiene que salir del entorno"
    # Y ninguna cuenta escrita a mano: un id de cuenta en el código es una
    # cuenta que alguien puede ver sin haberla habilitado.
    assert not re.search(r'CUENTAS\s*=\s*[\[(]\s*["\']', fuente), (
        "hay una lista de cuentas escrita en el código")


def test_el_resultado_dice_de_QUE_cuenta_habla():
    """«Cobrás USD 612» sin decir de qué cuenta no sirve para nada, y peor: se
    lee como si fuera toda la casa. El alcance viaja con el dato, igual que la
    moneda y la fecha de la foto."""
    assert '"cuenta": {"id_cuenta": pedida, "nombre": nombre}' in FUENTE, (
        "la respuesta tiene que decir la cuenta Y su titular")


def test_la_cuenta_es_OBLIGATORIA_para_el_modelo():
    """⚠️ **ESTRUCTURAL, NO UN PEDIDO EN EL PROMPT.** Que el asistente pregunte
    por la cuenta no puede depender de que el modelo lea una instrucción y se
    acuerde: se acuerda casi siempre, y el «casi» es una respuesta segura sobre
    la cuenta equivocada.

    Con `cuenta` sin default, la ficha que ve el modelo la marca `required`, y
    su única forma de conseguir una es `cuentas_disponibles` — o preguntar.
    """
    import inspect

    from asistente import herramientas as H

    for fn in (H.cobros_futuros,):
        p = inspect.signature(fn).parameters["cuenta"]
        assert p.default is inspect.Parameter.empty, (
            f"`{fn.__name__}.cuenta` tiene default: el modelo puede omitirla")
        ficha = next(f for f in H.FICHAS if f["function"]["name"] == fn.__name__)
        assert "cuenta" in ficha["function"]["parameters"]["required"]
    # Y el modelo tiene de dónde sacarla: la lista va en el SYSTEM.
    from unittest.mock import patch

    from asistente import ciclo
    fake = {"cuentas": [{"id_cuenta": "805", "nombre": "FULANO"}], "cuantas": 1}
    with patch.object(ciclo.H, "cuentas_disponibles", return_value=fake):
        assert "805" in ciclo._instruccion()


# ── COBROS FUTUROS — la plata que entra (2026-09-12) ────────────────────────


def test_la_plata_nunca_se_totaliza_mezclando_monedas():
    """⚠️ **LA GARANTÍA DE `cobros_futuros`.** La respuesta a «¿cuánto cobro?»
    es un diccionario por moneda (`{"ARS": …, "USD": …}`), no un número.

    No es cosmética: si existiera UN total, el modelo podría decirlo, y ese
    número sería pesos sumados con dólares. Al no existir, decir algo mal
    requiere que el modelo invente — que es mucho menos probable que repetir un
    campo que le dieron.

    Lo mismo del lado del SQL: la moneda va en el GROUP BY, así que la base
    nunca colapsa dos monedas en una fila.
    """
    from asistente.herramientas import cobros_futuros

    doc = cobros_futuros.__doc__ or ""
    assert "POR MONEDA" in doc and "NUNCA sumes" in doc, (
        "el docstring tiene que prohibirle al modelo sumar monedas: es lo "
        "único que lee antes de contestar")

    cuerpo = _cuerpo("cobros_futuros")
    for sql in _consultas(cuerpo):
        if "operaciones.acreencias" not in sql or "sum(t.monto)" not in sql:
            continue
        group_by = sql.lower().split("group by")[1].split("order by")[0]
        assert "moneda" in group_by, (
            "una suma de plata sin la moneda en el GROUP BY mezcla ARS con USD "
            f"y el resultado parece bueno:\n{sql.strip()[:300]}…")


def test_una_cuenta_pedida_se_INTERSECA_con_el_permiso():
    """El modelo elige el argumento `cuenta`, y puede inventarlo.

    Si ese argumento REEMPLAZARA al permiso, alcanzaría con que el modelo
    escribiera un número para leer una cuenta que nadie habilitó. Y si se
    IGNORARA en silencio, el modelo pediría la cuenta A y le contestaríamos con
    la B — peor todavía, porque contesta seguro.

    Las dos cosas se evitan igual: la cuenta pedida tiene que estar en el
    permiso, y si no está se dice y se corta.
    """
    cuerpo = _cuerpo("cobros_futuros")
    assert "if pedida not in permitido.cuentas():" in cuerpo, (
        "la cuenta pedida tiene que validarse contra el permiso")
    i = cuerpo.index("if pedida not in permitido.cuentas():")
    # Y el rechazo va ANTES de pisar el parámetro de la consulta.
    assert i < cuerpo.index('params["cuentas_permitidas"] = [pedida]')


def test_el_total_no_sale_de_la_lista_que_se_puede_truncar():
    """⚠️ El detalle `pagos` tiene techo (`MAX_PAGOS`). El total NO puede salir
    de sumarlo: el día que una cartera pase el techo, el total daría de menos y
    **nada fallaría** — la respuesta seguiría siendo un número redondo y
    plausible.

    Por eso el total y el calendario salen de su propia consulta sobre el
    período entero, y el corte se declara en `truncado`.
    """
    from asistente import herramientas as H

    assert H.MAX_PAGOS > 0
    cuerpo = _codigo("cobros_futuros")
    assert '"truncado": len(filas) > MAX_PAGOS' in cuerpo, (
        "si se corta la lista hay que decirlo")
    # El detalle es lo ÚNICO que se recorta (`filas[:MAX_PAGOS]`). El total y
    # los títulos salen de sus propias consultas sobre el período entero.
    assert "filas[:MAX_PAGOS]" in cuerpo
    assert '"total": {_mon(m): round(float(v or 0), 2) for m, v in tot}' in cuerpo, (
        "el total tiene que salir de su propia consulta (`tot`), no de sumar "
        "la lista que se puede truncar")
    for var in ("tot", "tits"):
        assert f"cur.execute(sql_{'total' if var == 'tot' else 'titulos'}, params)" in cuerpo


def test_la_plata_no_se_lee_de_la_tenencia():
    """⚠️⚠️ **LA REGLA QUE COSTÓ DOS ITERACIONES, y la dijo el user:**

    > *«NO HAY QUE MIRAR LA TENENCIA, HAY QUE MIRAR LA ACREENCIA QUE SE COBRA,
    > ya está solucionado esto en la plataforma»*

    La tenencia YA se miró: la miró `jobs/acreencias.py` al escribir
    `operaciones.acreencias`, multiplicando los nominales por el cronograma del
    bono (`api/services/acreencias.py:181`). Volver a mirarla desde acá sería
    rehacer esa multiplicación: dos versiones del mismo número sin árbitro, y
    el día que difieran ninguna falla — cada una contesta segura con la suya.

    La primera versión de esto devolvía nominales y valuación, y la valuación
    salía sin moneda porque `tenencia.moneda` se ingiere cruda de Aunesa y puede
    venir vacía. La pregunta nunca fue «cuánto vale»: fue **cuánta plata cobro**.
    """
    cuerpo = _codigo("cobros_futuros")
    assert "portafolio.tenencia" not in cuerpo, (
        "`cobros_futuros` volvió a mirar la tenencia: la plata sale de "
        "`operaciones.acreencias`, que ya la cruzó")
    # Se miran las CLAVES de la respuesta, no el texto: los comentarios de
    # arriba explican justamente por qué los nominales no están, y un test que
    # busque la palabra suelta se dispara con su propia explicación.
    for prohibido in ("nominales", "valuacion", "cantidad", "precio"):
        assert f'"{prohibido}"' not in cuerpo, (
            f"`{prohibido}` es una clave de la respuesta: la pregunta es cuánta "
            "plata se cobra, no cuánto vale la posición")


def test_un_vencimiento_es_el_ultimo_cobro_de_un_bono():
    """«¿Qué bono me vence?» y «¿cuánto cobro?» parecían dos preguntas y eran
    una: el vencimiento es el último pago del cronograma.

    Tenerlas en DOS herramientas fue el error — el modelo elegía la que se
    llamaba parecido a la pregunta (`bonos_que_vencen`), que leía la tenencia y
    contestaba con nominales y una valuación sin moneda. Ahora hay una sola, y
    cada título trae su `vence` desde el CATÁLOGO de renta fija.
    """
    from asistente import herramientas as H

    assert "bonos_que_vencen" not in H.POR_NOMBRE, (
        "volvió la herramienta que contestaba la pregunta equivocada")
    assert '"vence"' in _cuerpo("cobros_futuros")
    doc = H.cobros_futuros.__doc__ or ""
    assert "vence" in doc, (
        "el modelo tiene que saber que esta herramienta contesta también "
        "«qué bono me vence», o va a decir que no puede")

    # ⚠️ Y el vencimiento sale de `mercado.curvas`, que lo guarda como `date`.
    # `portafolio.assets.vencimiento` es TEXTO libre: "2027-3-5" y "05/03/2027"
    # ordenan distinto y no falla nada.
    sql = [q for q in _consultas(_cuerpo("cobros_futuros")) if "fecha_vencimiento" in q]
    assert sql, "no encontré de dónde sale el vencimiento"
    for q in sql:
        assert "mercado.curvas" in q
        # LEFT JOIN a propósito: un bono sin la fecha cargada igual tiene que
        # aparecer con su plata, que es lo que se preguntó.
        assert "LEFT JOIN mercado.curvas" in q


def test_la_respuesta_dice_de_cuando_son_los_datos():
    """`operaciones.acreencias` la recalcula un cron una vez por día. «Vas a
    cobrar X» sobre una foto de hace cuatro días es otra respuesta, y el modelo
    tiene que poder decirlo.

    ⚠️ UNA fecha, no dos. Viajaba también `calculado_el` (cuándo corrió el job) y
    se sacó: el usuario pregunta AHORA, así que eso no le dice nada que no sepa
    — y el modelo lo repetía en cada respuesta. Si el job quedó viejo, la foto
    también, así que `tenencia_del` ya lo delata.
    """
    from asistente.herramientas import cobros_futuros

    assert '"tenencia_del"' in FUENTE
    doc = cobros_futuros.__doc__ or ""
    assert "tenencia_del" in doc


def test_la_ficha_que_ve_el_modelo_sale_de_la_funcion_y_no_de_una_lista_aparte():
    """Sumar una herramienta es agregarla a `DISPONIBLES` y nada más. Si la
    descripción viviera en una lista paralela, el día que alguien cambie el
    docstring y no la lista, el modelo elegiría con una descripción vieja — y
    no fallaría nada, elegiría mal.
    """
    from asistente import herramientas as H

    assert len(H.FICHAS) == len(H.DISPONIBLES) == len(H.POR_NOMBRE)
    import inspect
    for fn, f in zip(H.DISPONIBLES, H.FICHAS, strict=True):
        assert f["function"]["name"] == fn.__name__
        # La descripción ES el docstring, carácter por carácter. No un resumen,
        # no un texto paralelo: lo mismo que se lee abriendo la función.
        assert f["function"]["description"] == inspect.getdoc(fn), (
            f"la ficha de `{fn.__name__}` no es su docstring")
        assert f["function"]["description"], f"`{fn.__name__}` sin docstring"
        # Y los argumentos salen de la firma, con los obligatorios marcados.
        params = f["function"]["parameters"]
        firma = inspect.signature(fn).parameters
        assert set(params["properties"]) == set(firma)
        assert set(params["required"]) == {
            n for n, p in firma.items() if p.default is inspect.Parameter.empty}


# ── ACHICAR LA CONVERSACIÓN VIEJA (2026-09-12) ─────────────────────────────
#
# El modelo no recuerda nada, así que en cada vuelta se le reenvía todo. Un
# resultado de herramienta no se paga una vez: se paga en cada vuelta de esa
# pregunta Y en cada pregunta que venga después.

import json


def _charla() -> list[dict]:
    """Dos preguntas ya contestadas, con el shape EXACTO del proveedor."""
    def pide(cid, nombre, args):
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": nombre, "arguments": json.dumps(args)}}]}

    grande = json.dumps({"pagos": [{"fecha": f"2026-10-{d:02d}", "monto": 100.0}
                                   for d in range(1, 29)]})
    return [
        {"role": "user", "content": "cuánta plata cobro"},
        pide("c1", "cobros_futuros", {"cuenta": "805", "dias": 60}),
        {"role": "tool", "tool_call_id": "c1", "content": grande},
        {"role": "assistant", "content": "Cobrás USD 611,83."},
        {"role": "user", "content": "y de la 1346"},
        pide("c2", "cobros_futuros", {"cuenta": "1346", "dias": 60}),
        {"role": "tool", "tool_call_id": "c2", "content": grande},
        {"role": "assistant", "content": "Cobrás USD 3.926,80."},
    ]


def test_el_id_del_pedido_sobrevive_al_achicado():
    """⚠️ **LO QUE ROMPE LA CONVERSACIÓN ENTERA SI SE TOCA.** El proveedor exige
    que cada mensaje `tool` conteste a un pedido suyo, por `tool_call_id`. Si un
    id no calza, rechaza la llamada COMPLETA — y el error habla del formato, no
    del contenido, así que buscarlo lleva horas.

    El contenido se puede reemplazar por cualquier cosa. El id, no.
    """
    from asistente import ciclo

    hist = _charla()
    nuevo, _ = ciclo._achicar(hist)
    assert [m.get("tool_call_id") for m in nuevo] == [m.get("tool_call_id") for m in hist]
    # Y el orden tampoco: una conversación desordenada no la entiende nadie.
    assert [m["role"] for m in nuevo] == [m["role"] for m in hist]


def test_se_achica_ENTRE_preguntas_y_nunca_dentro_de_una():
    """Adentro del turno el modelo NECESITA el resultado completo para contestar.
    Achicarlo ahí sería sacarle el dato justo antes de usarlo.

    Por eso `_achicar` se llama UNA vez, sobre el historial que llega de afuera,
    y ANTES del bucle de vueltas. Es estructural: no depende de acordarse.
    """
    cuerpo = (RAIZ / "asistente" / "ciclo.py").read_text(encoding="utf-8")
    cuerpo = cuerpo.split("def preguntar(", 1)[1]
    assert cuerpo.count("_achicar(") == 1, "se achica en un solo lugar"
    assert cuerpo.index("_achicar(") < cuerpo.index("for vuelta in range"), (
        "el achicado quedó DENTRO del bucle: le estaría sacando al modelo el "
        "resultado que necesita para contestar esta misma pregunta")


def test_el_resumen_le_dice_al_modelo_que_puede_volver_a_pedir():
    """Si el stub solo dijera «acá había algo», el modelo contestaría «no tengo
    ese dato» — que es peor que el gasto que vinimos a evitar.

    Tiene que decir dos cosas: qué herramienta fue (con sus argumentos, para que
    sepa cómo repetirla) y que puede volver a llamarla.
    """
    from asistente import ciclo

    nuevo, ahorro = ciclo._achicar(_charla())
    stub = next(m["content"] for m in nuevo
                if m["role"] == "tool" and m["content"].startswith("["))
    assert "cobros_futuros" in stub and "805" in stub, (
        "el stub tiene que nombrar la herramienta y sus argumentos")
    assert "volvé a llamar" in stub.lower(), (
        "sin esto el modelo dice «no tengo ese dato» en vez de volver a pedirlo")
    assert ahorro > 0


def test_el_resultado_mas_reciente_queda_entero():
    """La próxima pregunta suele ser sobre lo último que se miró («¿y el emisor
    de AO28?»). Achicar ESO obligaría a una vuelta más casi siempre.

    Cuántos se dejan enteros es una perilla declarada, no un número enterrado.
    """
    from asistente import ciclo

    assert ciclo.RESULTADOS_ENTEROS >= 1
    nuevo, _ = ciclo._achicar(_charla())
    tools = [m["content"] for m in nuevo if m["role"] == "tool"]
    assert tools[-1].startswith("{"), "el último resultado tiene que quedar crudo"
    assert tools[0].startswith("["), "el viejo tenía que achicarse"


def test_las_perillas_del_achicado_estan_declaradas_juntas():
    """El user va a querer mover esto. Tres constantes con nombre, arriba y
    juntas — no tres números adentro de la función."""
    from asistente import ciclo

    for perilla in ("RESULTADOS_ENTEROS", "ACHICAR_DESDE_CHARS", "PLANTILLA_ACHICADO"):
        assert hasattr(ciclo, perilla), f"falta la perilla {perilla}"


def test_no_quedo_nada_del_presupuesto_diario():
    """Se sacó entero por decisión del user (2026-09-12): era un mecanismo que
    nadie miraba y le pedía una consulta a la base a CADA llamada.

    Lo que queda acotando un gasto en bucle es el techo de vueltas del ciclo.
    Este test existe para que la mitad vieja no vuelva de a pedazos.
    """
    from core import ai

    for muerto in ("motivo_presupuesto", "presupuesto_dia_global",
                   "presupuesto_dia_usuario"):
        assert not hasattr(ai, muerto), f"volvió `{muerto}`"
    fuente = (RAIZ / "core" / "ai.py").read_text(encoding="utf-8")
    assert "budget" not in fuente.lower()
    # Y el techo que SÍ queda.
    from asistente import ciclo
    assert 0 < ciclo.MAX_VUELTAS <= 10


def test_el_libro_de_llamadas_se_llama_llamadas():
    """`ia.trazas` → `ia.llamadas`. «Traza» es el término técnico de
    observabilidad, pero esta tabla la mira una PERSONA para saber qué gastó: un
    nombre que hay que explicar está mal puesto.

    Y la mitad que se olvida: la columna `agente.hallazgos.ia_traza` apunta a esa
    tabla. Media renombrada es peor que ninguna.
    """
    esquema = (RAIZ / "sql" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS ia.llamadas" in esquema
    assert "ALTER TABLE ia.trazas RENAME TO llamadas" in esquema, (
        "sin el RENAME, el deploy crea una tabla nueva vacía y deja la vieja "
        "con todos los datos al lado")
    assert "RENAME COLUMN ia_traza TO ia_llamada" in esquema

    for py in (RAIZ / "core" / "ai.py", RAIZ / "agente" / "registro.py"):
        assert "ia.trazas" not in py.read_text(encoding="utf-8")
        assert "ia_traza " not in py.read_text(encoding="utf-8")

    # Las dos columnas que no leía nadie.
    assert "DROP COLUMN IF EXISTS conv_id" in esquema
    assert "DROP COLUMN IF EXISTS feedback" in esquema


# ── EL PANEL DEL LAB (2026-09-12) ──────────────────────────────────────────


def test_lo_que_se_elige_es_UNA_TAREA_y_no_un_rol():
    """⚠️⚠️ **LA PANTALLA MOSTRABA UN DESPLEGABLE QUE NO HACÍA NADA** (visto en
    producción, 2026-09-13). La elección se guardaba por `proveedor × rol`, así
    que el user configuró «deepseek · pro» y el asistente siguió andando con
    openai — porque a esa tarea nunca le tocaba esa combinación. No falló nada:
    simplemente el cambio no tenía efecto, y no había forma de saberlo.

    **Una fila de la pantalla tiene que ser una cosa que corre**, y lo que corre
    es una TAREA: el chat del LAB, el texto de los avisos, el botón explicámelo.
    «El pro de openai» es una abstracción interna que no le sirve a nadie.
    """
    from asistente import panel
    from core import ai

    assert ai.CLAVE_TAREA == "tarea:{tarea}"
    # La pantalla usa LA MISMA clave que el gateway lee, no una copia: con dos
    # formatos distintos la pantalla guardaría donde nadie mira — no falla nada,
    # simplemente el cambio no tiene efecto.
    assert panel.CLAVE_TAREA is ai.CLAVE_TAREA
    # Y las tareas salen de `_TAREAS`, no de una lista aparte: una tarea nueva
    # aparece sola en la pantalla.
    assert set(ai.tareas()) == set(ai._TAREAS)
    assert "asistente" in ai.tareas()


def test_proveedor_y_modelo_van_en_UN_valor():
    """Un nombre de modelo sólo existe para su proveedor. Con dos claves
    separadas se puede guardar `deepseek` + `gpt-5.6-terra` — un pedido que
    ningún proveedor entiende, y que no falla hasta la próxima llamada.

    Guardados juntos (`proveedor/modelo`), esa combinación **no se puede ni
    escribir**. Es la REGLA #9: dos copias de un hecho apareado necesitan un
    árbitro, y lo más barato es que sean una sola.
    """
    from asistente import panel

    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    i = cuerpo.index("def elegir_modelo(")
    cuerpo = cuerpo[i:cuerpo.index("\ndef ", i + 10)]
    assert 'f"{proveedor}/{modelo}"' in cuerpo, (
        "el valor tiene que llevar las dos cosas juntas")
    assert hasattr(panel, "volver_al_default"), (
        "«elegí mal y quiero deshacerlo» no se resuelve eligiendo otra cosa: "
        "tiene que poder borrarse la elección")


def test_cada_tarea_dice_PARA_QUE_es():
    """«asistente» y «agente_emisor» no le dicen nada a nadie que no haya
    escrito `core/ai.py`. La pantalla muestra una línea en criollo, y sale de la
    misma fila que declara la tarea — no de una tabla de nombres en el front,
    que quedaría vieja sin que nada falle.
    """
    from core import ai

    for t in ai.tareas():
        f = ai.ficha_de(t)
        assert f["para_que"], f"la tarea «{t}» no dice para qué es"
        assert f["proveedor"] and f["modelo"]


def test_solo_se_le_exige_tool_calling_a_la_tarea_que_usa_herramientas():
    """Exigirle tool calling a `agente_texto`, que sólo redacta, dejaría afuera
    modelos perfectamente buenos para eso. Y NO exigírselo al asistente dejaría
    entrar uno que lo deja contestando de memoria.

    Quién usa herramientas lo declara la tarea, no lo adivina la pantalla.
    """
    from core import ai

    assert ai.ficha_de("asistente")["usa_herramientas"] is True
    assert ai.ficha_de("agente_texto")["usa_herramientas"] is False
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    assert 'exigir_herramienta=ficha["usa_herramientas"]' in cuerpo


def test_el_ruteo_por_proveedor_lo_decide_UNA_constante():
    """⚠️ El portazo a un proveedor que entrena con lo que se le manda está
    **aflojado por decisión del user** (`config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA`,
    2026-09-13). Este test NO congela que esté abierto ni cerrado: congela que
    la decisión viva en UN solo lugar.

    El gateway (`core/ai.py::_ruteo_seguro`) y la pantalla (`panel.elegir_modelo`)
    tienen que leer la MISMA constante. Con dos reglas para lo mismo, el día que
    se desincronicen la pantalla dejaría elegir un modelo que después el gateway
    rechaza en cada pregunta — y nadie entendería por qué.

    Y el mecanismo tiene que seguir entero: volver atrás es poner la constante
    en False, no reconstruirlo.
    """
    from core import llm

    ai_src = (RAIZ / "core" / "ai.py").read_text(encoding="utf-8")
    panel_src = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")

    for fuente, donde in ((ai_src, "core/ai.py"), (panel_src, "asistente/panel.py")):
        assert "IA_PERMITE_PROVEEDOR_QUE_ENTRENA" in fuente, (
            f"{donde} decide por su cuenta si un proveedor que entrena puede "
            "correr tareas de negocio, en vez de leer la constante")

    # El mecanismo sigue en pie: la ficha de cada proveedor lo sigue declarando.
    assert not llm.no_entrena("deepseek") and llm.no_entrena("openai")

    # Y el chequeo de la pantalla sigue ANTES de guardar.
    i = panel_src.index("def elegir_modelo(")
    cuerpo = panel_src[i:panel_src.index("\ndef ", i + 10)]
    assert cuerpo.index("no_entrena") < cuerpo.index("INSERT INTO ia.config")


def test_un_proveedor_que_entrena_avisa_aunque_se_pueda_elegir():
    """Un permiso que no se explica se vuelve un default que nadie recuerda
    haber decidido. DeepSeek es elegible, y aun así viaja el aviso de qué
    implica elegirlo."""
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    i = cuerpo.index("def modelos(")
    cuerpo = cuerpo[i:cuerpo.index("\ndef ", i + 10)]
    assert '"aviso"' in cuerpo and "entrena con lo que se le manda" in cuerpo
    # Y en el log queda rastro de cada llamada así, no sólo en la pantalla.
    ai_src = (RAIZ / "core" / "ai.py").read_text(encoding="utf-8")
    i = ai_src.index("def _ruteo_seguro(")
    assert "logger.warning" in ai_src[i:ai_src.index("\ndef ", i + 10)]


def test_probar_el_modelo_es_parte_de_guardar_y_no_un_boton():
    """⚠️ Un modelo que ignora `tools` deja al asistente **contestando de
    memoria**: sin consultar la base, inventando números, con el mismo tono de
    siempre y sin un solo error. Es el peor modo de falla de esta app.

    Por eso la prueba no es un botón que se pueda saltear: vive adentro de
    `elegir_modelo`, antes del INSERT. Si no pasa, no se guarda y queda el
    modelo anterior. Misma decisión que `cuenta` en las herramientas: lo que se
    puede mover de «acordate» a «no podés no hacerlo», se mueve.
    """
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    i = cuerpo.index("def elegir_modelo(")
    cuerpo = cuerpo[i:cuerpo.index("\ndef ", i + 10)]
    assert "probar(" in cuerpo, "guardar tiene que probar el modelo"
    assert cuerpo.index("probar(") < cuerpo.index("INSERT INTO ia.config"), (
        "la prueba quedó DESPUÉS de guardar: entonces no protege de nada")
    assert 'return {"ok": False' in cuerpo.split("probar(")[1].split("INSERT")[0], (
        "si la prueba falla tiene que cortar, no seguir y guardar igual")

    # Y la prueba tiene que EXIGIR una herramienta: un modelo que conteste texto
    # pasaría una prueba que sólo mire que no hubo error.
    from asistente import panel
    assert panel._PRUEBA_HERRAMIENTA and "r.pedidos" in (
        (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8"))


def test_la_plata_no_se_estima_cuando_falta_el_precio():
    """Una tarifa vieja hardcodeada no falla: miente, y encima se usa para
    decidir. Por eso los precios van en `ia.config` (editables sin deploy) y un
    modelo sin precio devuelve `usd: null` MÁS su nombre en `sin_precio`.

    Sin esa lista, un `null` se lee como «no gastó».
    """
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    assert '"sin_precio"' in cuerpo
    assert "usd = None" in cuerpo
    # Ningún precio escrito en el código.
    import re
    assert not re.search(r"_PRECIOS\s*=|precio.*=\s*\d+\.\d+\s*#", cuerpo), (
        "hay una tarifa hardcodeada: las tarifas cambian y esto no avisa")


def test_lo_que_pega_en_CACHE_se_cobra_a_precio_de_cache():
    """⚠️⚠️ **EL ERROR QUE ESTO CORRIGE, CON LOS NÚMEROS DEL PROVEEDOR.** En
    `gpt-5.6-luna` la entrada cuesta US$0,20 por millón y la entrada CACHEADA
    US$0,02: diez veces menos.

    Cobrando todo a precio de entrada, la factura se infla justo en la parte que
    veníamos optimizando — y el hit rate del caché, que es la palanca más barata
    que tenemos, no se vería en el único número que mira una persona.

    Los tres pedazos ya estaban guardados por llamada en `ia.llamadas`. Sólo
    faltaba usarlos.
    """
    from asistente import panel

    luna = (0.20, 0.02, 1.20)
    sin_cache = panel.costo(luna, cache_hit=0, cache_miss=2100,
                            tokens_in=2100, tokens_out=60)
    con_cache = panel.costo(luna, cache_hit=1800, cache_miss=300,
                            tokens_in=2100, tokens_out=60)
    assert con_cache < sin_cache, "el caché tiene que salir más barato"
    assert con_cache * 2 < sin_cache, (
        "la diferencia tiene que notarse: si no, el número no sirve para decidir")


def test_sin_telemetria_de_cache_se_cobra_TODO_a_precio_lleno():
    """`cache_hit` y `cache_miss` vienen en cero cuando el proveedor no los
    informó. Cero hit NO es «no hubo entrada»: sin esta rama, una llamada sin
    telemetría de caché costaría cero pesos de entrada — el número quedaría más
    lindo y más falso.

    Es la misma regla que el resto: el silencio no es un dato.
    """
    from asistente import panel

    luna = (0.20, 0.02, 1.20)
    mudo = panel.costo(luna, cache_hit=0, cache_miss=0,
                       tokens_in=2100, tokens_out=60)
    lleno = panel.costo(luna, cache_hit=0, cache_miss=2100,
                        tokens_in=2100, tokens_out=60)
    assert mudo == lleno > 0


def test_una_tarifa_no_se_puede_cargar_a_MEDIAS():
    """Entrada cargada y caché en blanco calcularía un costo equivocado sin
    fallar, y encima al revés de lo que uno espera: sobrecobraría la parte más
    barata. Por eso los tres van en UN valor — igual que `proveedor/modelo`."""
    from asistente import panel

    assert panel.CLAVE_PRECIO == "precio:{modelo}"
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    i = cuerpo.index("def poner_precio(")
    cuerpo = cuerpo[i:cuerpo.index("\ndef ", i + 10)] if "\ndef " in cuerpo[i:] else cuerpo[i:]
    assert '"/".join(' in cuerpo, "los tres precios van en un solo valor"
    # Y una tarifa ilegible se ignora entera: media tarifa es peor que ninguna.
    assert panel._tarifa({"precio:x": "0.2/0.02"}, "x") is None
    assert panel._tarifa({"precio:x": "0.2/0.02/1.2"}, "x") == (0.2, 0.02, 1.2)


def test_el_panel_no_replica_la_precedencia_del_gateway():
    """Con qué modelo corre una tarea lo decide `core/ai.py` (elección > env >
    default). El panel lo PREGUNTA, no lo recalcula: dos versiones de la misma
    regla se desincronizan, y cuando pasa la pantalla muestra un modelo y el
    sistema usa otro — sin que nada falle."""
    from asistente import panel
    from core import ai

    assert hasattr(ai, "modelo_de") and hasattr(ai, "proveedor_de")
    cuerpo = (RAIZ / "asistente" / "panel.py").read_text(encoding="utf-8")
    assert "ai.ficha_de(" in cuerpo, (
        "la pantalla tiene que PREGUNTAR con qué corre cada tarea, no "
        "recalcular la precedencia (elegido > declarado > default) por su cuenta")
    assert panel.modelos


def test_la_lista_de_modelos_sale_del_proveedor_y_no_del_codigo():
    """Los nombres de modelo cambian cada pocos meses. Si estuvieran escritos en
    el repo, probar uno nuevo sería un deploy — y la lista quedaría vieja sin
    que nada lo marque."""
    from core import llm

    assert hasattr(llm, "disponibles") and hasattr(llm, "proveedores")
    fuente = (RAIZ / "core" / "llm.py").read_text(encoding="utf-8")
    i = fuente.index("def disponibles(")
    cuerpo = fuente[i:fuente.index("\ndef ", i + 10)]
    assert "/models" in cuerpo, "se le pregunta al proveedor"
    # Y no puede levantar: que no se pueda listar no rompe una pantalla.
    assert "return []" in cuerpo and "except Exception" in cuerpo


def test_el_panel_entra_en_el_techo_del_proxy():
    """⚠️ **UNA CUENTA QUE HAY QUE VOLVER A HACER SI SE SUMA UN PROVEEDOR.**

    El panel le pregunta la lista de modelos a CADA proveedor, en serie, adentro
    de un request que tiene el proxy de Next cortando a los 30 s y el cliente
    abortando a los 25 (`lib/fetch-json.ts::conTecho`).

    Con esperas largas y dos proveedores caídos, el panel se ve exactamente
    igual que un backend muerto — por una lista que es OPCIONAL. Este test hace
    la multiplicación para que nadie tenga que acordarse.
    """
    import inspect

    from core import llm

    espera = inspect.signature(llm.disponibles).parameters["timeout_s"].default
    peor = espera * len(llm.proveedores())
    assert peor <= 20, (
        f"el panel puede tardar {peor}s en el peor caso y el cliente aborta a "
        f"los 25: bajá el timeout de `llm.disponibles` o pedí las listas en "
        f"paralelo")

    # Y la prueba del modelo viaja adentro del POST de guardar, con el mismo techo.
    prueba = inspect.getsource((__import__("asistente.panel", fromlist=["x"])).probar)
    assert "timeout_s=20" in prueba, (
        "la prueba del modelo tiene que entrar en el techo del cliente")


# ── EL CONTROL DE NÚMEROS (2026-09-13) ─────────────────────────────────────
#
# En esta app un número puesto por el modelo es plata que no existe. El control
# es código: cero tokens, milisegundos, y sin opinión.

_CTX = """{"total":{"USD":611.83},
"titulos":[{"ticker":"YFCOO","total":354.2},{"ticker":"AO28","total":89.86}],
"por_mes":[{"mes":"2026-09","USD":431.23},{"mes":"2026-10","USD":180.6}],
"pagos":[{"fecha":"2026-09-15","monto":354.2},{"fecha":"2026-09-30","monto":44.93}]}"""


def test_un_numero_inventado_se_detecta():
    from asistente import control

    r = control.revisar("Cobrás USD 700 en total.", contexto=_CTX, pregunta="cuánto cobro")
    assert not r["ok"]
    assert "700" in r["hallazgos"][0]["detalle"]


def test_la_coma_decimal_y_el_redondeo_NO_son_inventos():
    """⚠️ **LA FALSA ALARMA ES EL MODO DE FALLA DE ESTE CONTROL.** Una alarma
    que suena por nada se deja de mirar en una semana, y ahí deja de servir para
    lo que vino a servir.

    La herramienta devuelve `611.83` y el modelo escribe `USD 611,83`, o `612`,
    o `unos 600`. Los tres son el mismo número.
    """
    from asistente import control

    for texto in ("Cobrás USD 611,83.", "Cobrás unos USD 612.",
                  "Cobrás alrededor de USD 600.", "Son 6 pagos de 4 títulos."):
        r = control.revisar(texto, contexto=_CTX, pregunta="cuánto cobro en 60 días")
        assert r["ok"], f"falsa alarma con: {texto} → {r['hallazgos']}"


def test_los_numeros_de_la_pregunta_cuentan_como_fuente():
    """«En los próximos 60 días» — el 60 lo puso el usuario. Marcarlo sería
    acusar al modelo de inventar lo que le dijeron."""
    from asistente import control

    r = control.revisar("En los próximos 90 días cobrás USD 611.83.",
                        contexto=_CTX, pregunta="cuánto cobro de acá a 90 días")
    assert r["ok"], r["hallazgos"]


def test_la_respuesta_NO_se_valida_contra_si_misma():
    """⚠️⚠️ **EL BUG QUE HABRÍA DEJADO ESTO EN VERDE PARA SIEMPRE.**

    El ciclo agrega la respuesta a `mensajes` antes de devolverla. Si el
    contexto se armara con `mensajes` entero, cada número se buscaría a sí
    mismo y el control no encontraría nada JAMÁS — sin fallar, sin avisar, y
    con la tranquilidad de un tilde verde.

    Se cazó corriéndolo con datos reales, no leyéndolo.
    """
    from asistente import ciclo, control

    resp = "Cobrás USD 700."
    msgs = [{"role": "user", "content": "cuánto cobro"},
            {"role": "tool", "tool_call_id": "c1", "content": _CTX},
            {"role": "assistant", "content": resp}]

    assert resp not in ciclo._contexto(msgs, resp), "la respuesta tiene que quedar afuera"
    assert not control.revisar(resp, contexto=ciclo._contexto(msgs, resp),
                               pregunta="cuánto cobro")["ok"]
    # Y la prueba de que el filtro es lo que lo hace funcionar: sin él, pasa.
    assert control.revisar(resp, contexto=ciclo._contexto(msgs, None),
                           pregunta="cuánto cobro")["ok"]


def test_el_control_no_sabe_NADA_de_las_herramientas():
    """Es lo que lo hace servir para las herramientas que todavía no existen.

    Trabaja sobre tres textos —la respuesta, todo lo que se le mandó, y la
    pregunta— y nada más. Una herramienta nueva queda cubierta sin escribir una
    línea, igual que la ficha del modelo y el achicado del historial.
    """
    # Se miran los IMPORTS, no la prosa: el docstring habla de las herramientas
    # justamente para explicar que no las conoce, y un test que busque la
    # palabra suelta se dispara con su propia explicación.
    import ast

    arbol = ast.parse((RAIZ / "asistente" / "control.py").read_text(encoding="utf-8"))
    importa = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importa |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            importa.add(n.module.split(".")[0])
    assert importa <= {"re", "__future__"}, (
        f"`control.py` importa {sorted(importa - {'re', '__future__'})}: se acopló "
        "a algo del proyecto, así que dejó de servir para las herramientas que "
        "todavía no existen y hay que tocarlo con cada una que se agregue")

    # Y su firma son tres textos: nada de objetos del dominio.
    import inspect

    from asistente import control
    for fn in control.CONTROLES:
        assert list(inspect.signature(fn).parameters) == [
            "respuesta", "contexto", "pregunta"], (
            f"`{fn.__name__}` no respeta la firma del registro")


def test_avisa_pero_no_bloquea():
    """El veredicto viaja AL LADO de la respuesta, no en vez de ella.

    Todavía no sabemos cuántas falsas alarmas da. Si bloqueara, la primera te
    deja sin una respuesta que estaba bien — peor que el problema. Endurecerlo
    pide un dato que hoy no existe.
    """
    cuerpo = (RAIZ / "asistente" / "ciclo.py").read_text(encoding="utf-8")
    i = cuerpo.index("def _salida(")
    cuerpo = cuerpo[i:]
    assert '"control": CTL.revisar(' in cuerpo, "el veredicto tiene que ir en la salida"
    assert '"respuesta": texto' in cuerpo, (
        "la respuesta se devuelve igual: el control avisa, no reemplaza")
    # Y un control que se rompe no puede tirar abajo una respuesta ya escrita.
    ctl = (RAIZ / "asistente" / "control.py").read_text(encoding="utf-8")
    assert "except Exception" in ctl.split("def revisar(")[1]


def test_sumar_es_trabajo_de_la_HERRAMIENTA_no_del_modelo():
    """⚠️ **ESTO LO ENCONTRÓ EL CONTROL EN SU PRIMERA CORRIDA**, con la respuesta
    real de la cuenta 805: marcó `431.23` y `180.6`.

    No eran inventos: el modelo había sumado los pagos de cada mes, y bien. Pero
    el SYSTEM le dice «no sumes números por tu cuenta», y lo hizo igual sin que
    nada lo notara. Con seis pagos acierta; con sesenta no hay motivo para
    creerle, y el error no se vería.

    La respuesta correcta a «el modelo está calculando» no es pedirle mejor que
    no calcule: es darle el número hecho. Por eso `cobros_futuros` devuelve
    `por_mes` agregado en SQL.
    """
    from asistente import herramientas as H

    cuerpo = _cuerpo("cobros_futuros")
    assert '"por_mes"' in cuerpo
    assert "GROUP BY 1, t.moneda" in cuerpo, "el agregado lo hace Postgres"
    doc = H.cobros_futuros.__doc__ or ""
    assert "por_mes" in doc and "NO lo calcules" in doc, (
        "el modelo tiene que saber que existe, o lo suma igual")


# ── LAS CUENTAS VAN EN EL SYSTEM (2026-09-13) ──────────────────────────────


def test_las_cuentas_van_en_el_system_y_al_FINAL():
    """⚠️ **LO VARIABLE AL FINAL, Y ESE ES TODO EL PUNTO.** El proveedor cachea
    el PREFIJO del prompt: lo que cambia rompe el caché de todo lo que viene
    después. Las cuentas cambian (se editan en el `.env`), el bloque de reglas
    no. Al final, el día que cambien se pierde el caché de tres renglones en vez
    del de la instrucción entera.
    """
    from unittest.mock import patch

    from asistente import ciclo

    fake = {"cuentas": [{"id_cuenta": "805", "nombre": "FULANO"},
                        {"id_cuenta": "1346", "nombre": "MENGANO"}], "cuantas": 2}
    with patch.object(ciclo.H, "cuentas_disponibles", return_value=fake):
        txt = ciclo._instruccion()
    assert txt.startswith(ciclo.SYSTEM), (
        "el bloque fijo tiene que quedar intacto y primero, o el caché no pega")
    assert "805" in txt and "FULANO" in txt


def test_si_no_se_pueden_leer_las_cuentas_el_asistente_sigue():
    """Una lista que no se pudo traer no puede dejar al asistente sin contestar:
    cae al bloque fijo y el modelo pregunta, que es el comportamiento de antes."""
    from unittest.mock import patch

    from asistente import ciclo

    with patch.object(ciclo.H, "cuentas_disponibles", side_effect=RuntimeError("caída")):
        assert ciclo._instruccion() == ciclo.SYSTEM


def test_la_regla_de_la_cuenta_esta_escrita_en_UN_solo_lugar():
    """⚠️⚠️ **VISTO EN PRODUCCIÓN (2026-09-13).** «Preguntá de qué cuenta» estaba
    en TRES lugares —el SYSTEM y dos docstrings— y el modelo pidió confirmación
    de una cuenta que el usuario YA había nombrado: un turno entero de más,
    2.110 tokens de entrada, para no enterarse de nada.

    Repetir una instrucción no la refuerza: la vuelve más pesada de lo que
    querías, y el modelo la sobre-aplica. Va en el SYSTEM y en ningún otro lado.
    """
    from asistente import ciclo

    assert "preguntale cuál quiere" in ciclo.SYSTEM
    fuente = (RAIZ / "asistente" / "herramientas.py").read_text(encoding="utf-8")
    assert "PREGUNTALE" not in fuente, (
        "volvió la orden de preguntar a un docstring: con la regla en dos lados "
        "el modelo pide confirmación de lo que ya le dijiste")


def test_cuentas_disponibles_no_se_le_ofrece_al_modelo():
    """La lista ya está en el SYSTEM. Ofrecerla además sería pagar su ficha en
    cada llamada y darle una opción más para elegir mal, por un dato que tiene
    delante. La función queda: es de donde el SYSTEM la saca."""
    from asistente import herramientas as H

    assert "cuentas_disponibles" not in H.POR_NOMBRE
    assert hasattr(H, "cuentas_disponibles"), "la función tiene que seguir existiendo"
    assert len(H.FICHAS) == len(H.DISPONIBLES)


def test_no_se_reporta_cuando_corrio_el_job():
    """El usuario pregunta AHORA: «calculado el …» no le dice nada que no sepa, y
    el modelo lo repetía en cada respuesta. Lo que importa es de cuándo es la
    FOTO — y si el job quedó viejo, la foto también, así que `tenencia_del` ya
    lo delata."""
    cuerpo = _cuerpo("cobros_futuros")
    assert '"tenencia_del"' in cuerpo
    assert "calculado_el" not in cuerpo and "generado_at" not in cuerpo.split('"""')[-1]


# ── EL ESQUEMA: LA FORMA QUE TIENE QUE TENER LA RESPUESTA ───────────────────
#
# `asistente/esquema.py`. Dos campos: la prosa y qué no pudo contestar.


def test_el_esquema_tiene_DOS_campos_y_NINGUNO_lleva_datos():
    """⚠️⚠️ **HUBO UN TERCER CAMPO, `mostrar`, Y SE BORRÓ.**

    El modelo nombraba qué partes del resultado dibujar como tabla. El campo era
    una lista sin tope y sin criterio, así que a «¿cuánto cobro?» contestaba con
    TRES tablas del mismo total. El arreglo no fue ponerle tope: una tabla no
    era la forma de contestar.

    Este test es la pared para las dos cosas: que no vuelva `mostrar`, y que no
    aparezca un campo nuevo que lleve filas, montos o tickers — el modelo copia
    números sólo en la prosa, donde el control los revisa.
    """
    from asistente import esquema as ESQ

    esquema = ESQ.FORMATO["json_schema"]["schema"]
    assert set(esquema["properties"]) == {"respuesta", "falta"}
    for campo in esquema["properties"].values():
        assert campo["type"] in ("string", ["string", "null"]), (
            "un campo que no es texto es un lugar donde el modelo puede "
            "escribir datos estructurados; los datos los trae la herramienta")


def test_el_esquema_es_una_PARED_y_no_un_pedido():
    """`strict` + `additionalProperties: false` es lo que hace que el proveedor
    RECHACE una respuesta con otra forma. Sin eso vuelve a ser una sugerencia, y
    una sugerencia se cumple casi siempre — que es la peor frecuencia."""
    from asistente import esquema as ESQ

    js = ESQ.FORMATO["json_schema"]
    assert js["strict"] is True
    assert js["schema"]["additionalProperties"] is False
    assert set(js["schema"]["required"]) == {"respuesta", "falta"}


def test_la_PROSA_es_la_respuesta_cuando_el_proveedor_no_soporta_esquema():
    """⚠️ DeepSeek no acepta `json_schema` (medido). `leer()` corre igual, y ahí
    lo que llega es prosa: tratarla como un JSON roto sería tirar una respuesta
    buena por una capacidad que ese proveedor no tiene."""
    from asistente import esquema as ESQ

    prosa = "Cobrás USD 611,83 en los próximos 60 días."
    assert ESQ.leer(prosa) == {"respuesta": prosa, "falta": None}


def test_leer_NUNCA_levanta():
    """El contrato del ciclo es no romperse por lo que conteste el modelo. Un
    JSON cortado a la mitad, un `null`, una lista: ninguno puede dejar al
    usuario sin respuesta ni tirar un 500."""
    from asistente import esquema as ESQ

    for crudo in (None, "", "   ", "{", "[]", "null", '"texto"', "{}",
                  '{"respuesta": null}', '{"otra_cosa": 1}'):
        assert set(ESQ.leer(crudo)) == {"respuesta", "falta"}


def test_CON_ESQUEMA_la_respuesta_sigue_sin_validarse_contra_si_misma():
    """⚠️⚠️ **EL MISMO BUG, POR LA PUERTA DE ATRÁS.**

    Al historial va el JSON del modelo TAL CUAL —es lo que el proveedor espera
    recibir de vuelta—, así que con structured output la prosa viaja ADENTRO de
    ese JSON. Si el ciclo excluyera del contexto el texto PARSEADO, ese string
    no coincidiría con ningún mensaje: el JSON quedaría en el contexto, cada
    número se validaría contra sí mismo y el control volvería a pasar en verde
    para siempre. Por eso `_salida` excluye el CRUDO.

    Se encontró releyendo el diff, no corriéndolo: el síntoma es que todo anda.
    """
    import json as _json

    from asistente import ciclo, control

    resp = "Cobrás USD 700."
    crudo = _json.dumps({"respuesta": resp, "mostrar": [], "falta": None},
                        ensure_ascii=False)
    msgs = [{"role": "user", "content": "cuánto cobro"},
            {"role": "tool", "tool_call_id": "c1", "content": _CTX},
            {"role": "assistant", "content": crudo}]

    # Lo que hace el ciclo: excluye el crudo, revisa el texto parseado.
    ctx = ciclo._contexto(msgs, crudo)
    assert "700" not in ctx, "el JSON con la respuesta adentro quedó en el contexto"
    assert not control.revisar(resp, contexto=ctx, pregunta="cuánto cobro")["ok"]
    # Y la prueba de que excluir el PARSEADO no alcanza: el invento pasa.
    assert control.revisar(resp, contexto=ciclo._contexto(msgs, resp),
                           pregunta="cuánto cobro")["ok"], (
        "si esto falla, el crudo ya no contiene la respuesta y el test perdió "
        "sentido — revisar qué se escribe en `mensajes`")


def test_el_ciclo_le_pasa_al_control_lo_que_de_verdad_escribio_en_el_HISTORIAL():
    """El contrato de arriba, pero sobre el ciclo entero y no sobre `_contexto`:
    `_salida` tiene que recibir el crudo del modelo. Sin ese argumento la
    función sigue andando —el default es el texto— y el control se apaga solo."""
    from asistente import ciclo

    fuente = (RAIZ / "asistente" / "ciclo.py").read_text(encoding="utf-8")
    assert "crudo=r.texto" in fuente, (
        "el camino feliz dejó de pasarle el crudo a `_salida`: el control "
        "vuelve a validar cada número contra sí mismo")
    assert "crudo" in ciclo._salida.__code__.co_varnames


def test_a_un_proveedor_SIN_esquema_se_le_manda_SIN_esquema():
    """⚠️ **UNA CAPACIDAD DE MENOS, NO UN CAMINO CORTADO.** DeepSeek contesta
    HTTP 400 a `json_schema`. Si el esquema viajara igual, elegirlo desde la
    pantalla dejaría al asistente contestando «el proveedor no contestó» en
    CADA pregunta, con el motivo enterrado en un error de formato."""
    from unittest.mock import patch

    from core import ai, llm

    visto: dict = {}

    def fake_chat(mensajes, **kw):
        visto.clear()
        visto.update(kw)
        return llm.RespuestaLLM(ok=True, texto="hola")

    from asistente import esquema as ESQ

    with patch.object(llm, "chat", fake_chat), \
         patch.object(llm, "configurado", return_value=True), \
         patch.object(ai, "_ruteo_seguro", return_value=True), \
         patch.object(ai, "_trazar", return_value=1):
        with patch.object(llm, "soporta_esquema", return_value=True):
            ai.conversar("asistente", mensajes=[], formato=ESQ.FORMATO)
            assert visto.get("formato") == ESQ.FORMATO
        with patch.object(llm, "soporta_esquema", return_value=False):
            ai.conversar("asistente", mensajes=[], formato=ESQ.FORMATO)
            assert visto.get("formato") is None, (
                "el esquema viajó a un proveedor que lo rechaza: la pregunta "
                "vuelve como error de formato en vez de como respuesta")
