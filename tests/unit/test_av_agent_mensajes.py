"""Mandar un mensaje: la capacidad, y el bug que la tenía rota.

EL BUG (2026-08-19, lo encontró el user probando el aviso de
`comitentes_sin_nivel1`): el índice único de `av_agent_avisos` era
`(ticker, clave) WHERE NOT resuelto` — **sin el destinatario**. Así que el primer
ping sobre un tema lo bloqueaba para todos los demás: avisarle a otra persona
hacía `ON CONFLICT DO NOTHING`, devolvía 0, y como nadie miraba ese 0 la acción
decía «listo» y al destinatario no le llegaba nada.

El modo de falla de siempre: **no falla nada**. Se reportó bien, se verificó mal
y el mensaje no existió.
"""
from __future__ import annotations

import pathlib

from api.services import av_agent_mensajes as msg


def test_el_indice_incluye_al_DESTINATARIO():
    """Sin esto, dos personas no pueden tener el mismo tema abierto — y la
    segunda pierde el mensaje en silencio."""
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    assert "ux_av_agent_avisos_abierto_para" in sql
    assert "(ticker, clave, coalesce(lower(para), ''))" in sql
    # Y el viejo tiene que estar explícitamente dado de baja: si conviven, el
    # de antes sigue bloqueando y el arreglo no surte efecto.
    assert "DROP INDEX IF EXISTS mercado.ux_av_agent_avisos_abierto;" in sql


def test_la_accion_MIRA_si_el_mensaje_se_creo():
    """`avisar_a` devuelve 0 cuando no creó nada y eso se estaba tirando."""
    import inspect

    from api.services.av_agent_hacer import AccionAvisar
    src = inspect.getsource(AccionAvisar.aplicar)
    assert "if not n" in src and "raise" in src


def test_un_destinatario_invalido_no_levanta():
    """Un mensaje que no se puede mandar no puede tumbar al job que lo mandaba."""
    r = msg.enviar(para="no-es-un-mail", asunto="hola")
    assert r["ok"] is False and "destinatario" in r["error"]


def test_un_mensaje_sin_asunto_se_rechaza():
    """Un pendiente sin texto es una fila que nadie sabe qué hacer con ella."""
    r = msg.enviar(para="alguien@acavalores.com.ar", asunto="   ")
    assert r["ok"] is False and "asunto" in r["error"]


def test_hay_TOPE_de_destinatarios():
    """Un mensaje a 200 personas no es un mensaje, es spam interno — y el primero
    que lo reciba sin esperarlo deja de mirar la campanita."""
    muchos = [{"para": f"u{i}@acavalores.com.ar", "asunto": "x"}
              for i in range(msg.MAX_DESTINATARIOS + 5)]
    r = msg.enviar_muchos(muchos)
    assert r["ok"] is False and "spam" in r["error"]
    assert r["enviados"] == 0


def test_el_envio_masivo_DEVUELVE_los_que_fallaron():
    """Un envío que solo dice «mandé 12» esconde a los 3 que no llegaron, que son
    justo los que hay que mirar."""
    r = msg.enviar_muchos([{"para": "roto", "asunto": "x"}])
    assert r["ok"] is True
    assert r["enviados"] == 0 and len(r["fallaron"]) == 1
    assert r["fallaron"][0]["error"]


def test_no_se_crea_un_buzon_nuevo():
    """Es la MISMA tabla que la persona ya ve en su barra. Un segundo lugar donde
    mirar lo que hay para hacer es el problema que SALUD vino a resolver."""
    import inspect
    src = inspect.getsource(msg.enviar)
    assert "av_agent_vista.avisar_a" in src


def test_el_job_de_saldos_NO_reinvierte_el_signo():
    """`control_saldos` ya trae el signo corregido (Aunesa manda las tenencias al
    revés y el daemon lo arregla): negativo = DESCUBIERTO real. Invertirlo acá
    convertiría cada descubierto en un sobrante y al revés."""
    src = pathlib.Path("jobs/saldos_a_operadores.py").read_text(encoding="utf-8")
    assert "-s.cantidad" not in src and "* -1" not in src
    assert "no re-invertir" in src.lower()


def test_las_cuentas_SIN_operador_se_cuentan():
    """No le llegan a nadie por definición. Un envío que solo cuenta lo que mandó
    esconde justo lo que quedó sin dueño."""
    src = pathlib.Path("jobs/saldos_a_operadores.py").read_text(encoding="utf-8")
    assert "_sin_operador" in src and 'set_stat("sin_operador"' in src


def test_el_job_ARRANCA():
    """Se rompió en la primera corrida real: `JobRunLogger` toma UN argumento
    (`tipo`) y se lo llamó con dos. Lo que no se puede volver a pasar es que un
    job entre a producción sin que nada haya intentado construirlo — la firma es
    lo primero que se rompe y lo único que no cuesta nada chequear."""
    import inspect

    from core.job_runs import JobRunLogger
    firma = inspect.signature(JobRunLogger.__init__)
    # Sin `self`: exactamente un parámetro obligatorio.
    params = [p for n, p in firma.parameters.items() if n != "self"]
    assert len(params) == 1, "cambió la firma de JobRunLogger: revisá los jobs"

    src = pathlib.Path("jobs/saldos_a_operadores.py").read_text(encoding="utf-8")
    assert 'JobRunLogger("saldos_a_operadores")' in src
    # Y el `run_tipo` del árbol tiene que ser EL MISMO string, o el diagnóstico
    # busca corridas de un tipo que nadie escribe y el job se ve como caído.
    reg = pathlib.Path("api/services/diagnostico_registry.py").read_text(encoding="utf-8")
    assert 'run_tipo="saldos_a_operadores"' in reg


# ── (2026-08-19) EL MENSAJE QUE INTERRUMPE Y TRAE TABLA ─────────────────────

def test_re_mandar_la_tabla_NO_pisa_las_tildes():
    """El operador que ya marcó tres no las pierde porque el job volvió a
    correr. Si se perdieran, marcar dejaría de tener sentido."""
    import inspect
    src = inspect.getsource(msg.enviar_tabla)
    assert "ON CONFLICT (aviso_id, clave) DO UPDATE" in src
    # `hecho` NO puede estar en el SET del upsert.
    setclause = src.split("DO UPDATE SET")[1].split('",')[0]
    assert "hecho" not in setclause


def test_el_aviso_de_saldos_VENCE():
    """Vale HOY. Mañana el mercado abre con otros números y pedir acción sobre la
    foto de ayer es peor que no avisar."""
    import inspect
    assert "vence_at" in inspect.getsource(msg.enviar_tabla)
    from api.services import av_agent_vista
    src = inspect.getsource(av_agent_vista.avisos_de)
    assert "vence_at IS NULL OR vence_at > now()" in src


def test_tildar_la_fila_de_OTRO_es_imposible():
    """El «es mío» vive en el WHERE del UPDATE y no en un `if` previo: así no es
    un permiso que alguien pueda olvidarse de chequear en el próximo endpoint."""
    import inspect
    src = inspect.getsource(msg.marcar_item)
    assert "lower(a.para) = %s" in src


def test_al_marcar_la_ultima_el_aviso_se_CIERRA_solo():
    """Pedir además que apriete «listo» sería un paso que no agrega nada."""
    import inspect
    src = inspect.getsource(msg.marcar_item)
    assert "SET resuelto = true" in src and "NOT hecho" in src


def test_el_job_de_saldos_manda_TABLA_y_no_un_parrafo():
    src = pathlib.Path("jobs/saldos_a_operadores.py").read_text(encoding="utf-8")
    assert "enviar_tabla" in src and "interrumpe=True" in src
    # Y la clave de cada fila la identifica entre corridas.
    assert '"clave": f"{f[\'id_cuenta\']}:{f[\'moneda\']}"' in src


def test_se_puede_probar_sin_molestar_a_nadie():
    """Sin un modo prueba, ver si el modal se ve bien significa escribirle a la
    mesa entera."""
    src = pathlib.Path("jobs/saldos_a_operadores.py").read_text(encoding="utf-8")
    assert '"--a"' in src and "[PRUEBA]" in src


def test_no_se_CREA_y_se_DROPEA_el_mismo_indice_en_el_mismo_schema():
    """**Frenó un deploy el 2026-08-19.** Al cambiar el índice de avisos dejé el
    `CREATE` viejo arriba del `DROP` nuevo: cada `apply_schema` recreaba el que
    acabábamos de borrar, y en cuanto dos operadores recibieron el mismo tema esa
    recreación falló por duplicado y **cortó el deploy entero**.

    Un `CREATE` y un `DROP` del mismo índice en el mismo archivo no es
    redundancia: es una bomba de tiempo que explota el día que los datos usan la
    libertad que el índice nuevo les dio."""
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    import re
    dropeados = set(re.findall(
        r"DROP INDEX IF EXISTS\s+(?:\w+\.)?(\w+)\s*;", sql))
    creados = set(re.findall(
        r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS\s+(\w+)", sql))
    chocan = dropeados & creados
    assert not chocan, (
        f"estos índices se crean Y se dropean en el mismo schema: {sorted(chocan)}. "
        f"El próximo `apply_schema` va a recrear lo que acaba de borrar.")
