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
    # Y la herramienta para conseguirla existe.
    assert "cuentas_disponibles" in H.POR_NOMBRE


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
    cobrar X» sobre una foto de hace cuatro días es otra respuesta.

    Son DOS fechas y no una: cuándo se sacó la foto de cartera
    (`tenencia_del`) y cuándo corrió el cálculo (`calculado_el`). Una compra de
    ayer no está en ninguna de las dos, y el modelo tiene que poder decirlo.
    """
    from asistente.herramientas import cobros_futuros

    assert '"tenencia_del"' in FUENTE and '"calculado_el"' in FUENTE
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
