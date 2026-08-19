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
