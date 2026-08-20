"""CUANDO ALGO VUELVE, TAMBIÉN SE AVISA.

*«Quiero que el agente sepa avisar cuando un motor que estaba caído vuelve a
funcionar»* (user, 2026-08-20).

Hasta acá se arreglaba y **desaparecía en silencio**: los hallazgos de rueda se
reemplazan en cada corrida, así que una recuperación es una fila que deja de
escribirse. El que estaba esperando entra a la pantalla cada diez minutos a ver
si sigue el problema — y si el aviso salió por mensaje al back office, se quedan
con la mala noticia y sin la buena.
"""
from __future__ import annotations

from api.services import av_agent_recuperados as rec


def _h(tipo, ticker, motivo="algo roto"):
    return {"tipo": tipo, "ticker": ticker, "motivo": motivo,
            "severidad": "alta", "regla": "x"}


def _antes(*hs):
    return {rec.clave(h): {**h, "desde": None} for h in hs}


def test_lo_que_ESTABA_y_ya_NO_esta_se_anuncia(monkeypatch):
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("motor_caido", "motor_rofex")))
    out = rec.detectar_recuperados([])
    assert len(out) == 1
    assert out[0]["motivo"] == "MOTOR_ROFEX VOLVIÓ · ya funciona"
    assert out[0]["regla"] == "volvio"


def test_lo_que_SIGUE_roto_no_se_anuncia(monkeypatch):
    roto = _h("motor_caido", "motor_rofex")
    monkeypatch.setattr(rec, "_lo_de_antes", lambda a: _antes(roto))
    assert rec.detectar_recuperados([roto]) == []


def test_la_identidad_NO_incluye_la_regla(monkeypatch):
    """Si un motor pasa de «no produce» a «error» sigue siendo el mismo motor
    roto. Contarlo como uno que se arregló y otro nuevo sería mentir dos veces."""
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("motor_caido", "motor_rofex")))
    ahora = {**_h("motor_caido", "motor_rofex"), "regla": "otra_cosa"}
    assert rec.detectar_recuperados([ahora]) == []


def test_un_BONO_que_consiguio_punta_NO_es_una_noticia(monkeypatch):
    """Pasa cien veces por día. Anunciarlo llenaría la pantalla de confeti hasta
    que nadie mire ninguna — y ahí se pierden también las que importan."""
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("sin_precio", "AL30")))
    assert rec.detectar_recuperados([]) == []


def test_la_buena_noticia_va_en_BAJA(monkeypatch):
    """Que aparezca arriba de un problema real sería justo al revés de lo que la
    pantalla tiene que hacer."""
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("proveedor_caido", "aunesa")))
    assert rec.detectar_recuperados([])[0]["severidad"] == "baja"


def test_el_aviso_dice_QUE_ESTABA_pasando(monkeypatch):
    """«Volvió» sin decir de qué es una frase sin información: el que lo lee
    tiene que poder atarlo al aviso que vio ayer."""
    monkeypatch.setattr(rec, "_lo_de_antes", lambda a: _antes(
        _h("proveedor_caido", "aunesa", "AUNESA CAÍDO · 500 Server Error")))
    ev = rec.detectar_recuperados([])[0]["evidencia"]
    assert "500 Server Error" in ev["texto"]
    assert ev["era_tipo"] == "proveedor_caido"


def test_si_no_se_puede_leer_lo_anterior_NO_se_rompe_la_corrida(monkeypatch):
    """Esto corre en el medio del monitor: un fallo acá no puede impedir que se
    guarde lo que la corrida encontró."""
    def _boom(a):
        raise RuntimeError("sin base")
    monkeypatch.setattr(rec, "_lo_de_antes", _boom)
    assert rec.detectar_recuperados([_h("motor_caido", "x")]) == []


def test_se_compara_ANTES_de_pisar_los_viejos():
    """La corrida anterior está en la tabla **hasta** que `reemplazar_hallazgos`
    hace su DELETE. Si se llamara después, no habría con qué comparar y el
    detector diría siempre «no volvió nada» — sin dar ningún error."""
    import inspect

    from jobs import av_agent_live
    src = inspect.getsource(av_agent_live.main)
    assert src.index("detectar_recuperados") < src.index("_guardar(r[")


def test_la_buena_noticia_VENCE_mas_rapido_que_la_mala():
    """«Volvió hace tres horas» no le sirve a nadie y ocupa el lugar de lo que sí
    está pasando ahora."""
    from api.services import av_agent
    assert "volvio" in av_agent.VENCE_RAPIDO
    assert av_agent.VENCE_RAPIDO_S < av_agent.VENCE_OBSERVACION_S * 10
    import inspect
    src = inspect.getsource(av_agent_vista_modulo := __import__(
        "api.services.av_agent_vista", fromlist=["x"]))
    assert "VENCE_RAPIDO" in src, "la vista no aplica el vencimiento corto"
    assert av_agent_vista_modulo is not None


def test_si_la_MALA_salio_por_mensaje_la_BUENA_tambien(monkeypatch):
    """Dejar al back office con la mala noticia y sin la buena es peor que no
    haber avisado: siguen operando a mano y pensando que falta media jornada."""
    mandados = []
    import api.services.av_agent_mensajes as msg
    from api.services import av_agent_proveedores as prov
    monkeypatch.setattr(prov, "_a_quien", lambda: ["bo@aca.com"])
    monkeypatch.setattr(msg, "enviar_muchos",
                        lambda m, **k: mandados.append(m) or {"enviados": len(m)})
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("proveedor_caido", "aunesa")))
    h = rec.detectar_recuperados([])[0]
    assert h["evidencia"]["aviso"]["enviados"] == 1
    assert mandados[0][0]["asunto"] == "AUNESA VOLVIÓ · ya funciona"


def test_la_vuelta_de_un_MOTOR_no_le_escribe_al_back_office(monkeypatch):
    """Un motor que vuelve es del sistema, no del back office. Mandarles lo que
    no les toca es cómo se logra que dejen de leer los mensajes."""
    import api.services.av_agent_mensajes as msg
    monkeypatch.setattr(msg, "enviar_muchos",
                        lambda m, **k: (_ for _ in ()).throw(
                            AssertionError("no se le escribe a nadie")))
    monkeypatch.setattr(rec, "_lo_de_antes",
                        lambda a: _antes(_h("motor_caido", "motor_rofex")))
    assert rec.detectar_recuperados([])[0]["evidencia"].get("aviso") is None


def test_un_aviso_de_vuelta_por_DIA(monkeypatch):
    import inspect
    assert "date.today().isoformat()" in inspect.getsource(rec._avisar_vuelta)
