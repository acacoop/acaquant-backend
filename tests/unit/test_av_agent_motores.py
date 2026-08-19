"""SI UN MOTOR SE CAYÓ, EL AGENTE SE ENTERA — `av_agent_motores`.

*«Con los logs de los motores lo mismo: quiero que si hay alguno caído enterarme
rápido»* (user, 2026-08-19).

Lo que se congela es la decisión que hace que esto NO mienta: **fuera de su
ventana un motor no está caído, está apagado**. Sin eso, este detector cantaría
quince motores muertos todos los sábados y en dos fines de semana nadie volvería
a leerlo.
"""
from __future__ import annotations

from api.services import av_agent_motores as mot


def _arbol(*piezas, en_rueda=True):
    return {"en_rueda": en_rueda, "ahora_ar": "2026-08-19 14:00",
            "vistas": [{"vista": "MERCADOS", "resumen": {},
                        "grupos": [{"grupo": None, "piezas": list(piezas)}]}]}


def _p(label, estado, tipo="motor", **kw):
    return {"label": label, "tipo": tipo, "estado": estado, "cadencia": "live",
            "hace": "hace 40 min", "umbral_s": 120, "ultima": None, **kw}


def test_un_motor_APAGADO_no_es_un_motor_caido(monkeypatch):
    """**El invariante central.** Los motores de mercado los prende y los apaga el
    cron de lunes a viernes: fuera de rueda están apagados a propósito."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "fuera_rueda"),
                                       en_rueda=False))
    assert mot.detectar_motores() == []


def test_un_motor_que_no_produce_EN_RUEDA_si(monkeypatch):
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_curvas", "critico")))
    h = mot.detectar_motores()
    assert len(h) == 1 and h[0]["severidad"] == "alta"
    assert "no es que esté apagado" in h[0]["evidencia"]["texto"]


def test_LENTO_no_es_lo_mismo_que_CAIDO(monkeypatch):
    """Un motor que tarda el doble sigue produciendo. Mezclarlo con uno muerto es
    cómo se pierde la diferencia entre las dos cosas."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_agro", "lento")))
    assert mot.detectar_motores() == []


def test_un_motor_pesa_MAS_que_un_job(monkeypatch):
    """Un motor caído deja a la mesa sin precios AHORA; un job se recupera en la
    corrida siguiente."""
    monkeypatch.setattr("api.services.diagnostico.arbol", lambda: _arbol(
        _p("un_job", "error", tipo="job"),
        _p("motor_rofex", "critico")))
    h = mot.detectar_motores()
    assert h[0]["ticker"] == "motor_rofex"


def test_no_se_reimplementa_el_arbol():
    """50 piezas con su cadencia, ventana y umbral YA estaban en
    `diagnostico_registry`. Reescribir eso daría dos verdades sobre si un motor
    anda — que es el problema que este proyecto viene resolviendo hace tres días."""
    import inspect
    src = inspect.getsource(mot)
    assert "diagnostico.arbol()" in src
    assert "systemctl" not in src and "PIEZAS" not in src


def test_si_el_arbol_falla_no_se_inventa_nada(monkeypatch):
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert mot.detectar_motores() == []
    assert mot.resumen()["ok"] is False


def test_el_resumen_CUENTA_no_lista(monkeypatch):
    """*«No quiero que me muestre todos los endpoints; quiero saber que la
    aplicación funciona bien»*. El resumen devuelve el conteo, no las 50 piezas —
    y solo detalla las rotas."""
    monkeypatch.setattr("api.services.diagnostico.arbol", lambda: _arbol(
        _p("a", "ok"), _p("b", "ok"), _p("c", "critico"),
        _p("d", "fuera_rueda"), _p("e", "lento")))
    r = mot.resumen()
    assert (r["total"], r["bien"], r["rotas"], r["apagadas"], r["lentas"]) == (5, 2, 1, 1, 1)
    assert [d["label"] for d in r["detalle_rotas"]] == ["c"]


def test_la_evidencia_es_un_dict(monkeypatch):
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("m", "sin_datos")))
    ev = mot.detectar_motores()[0]["evidencia"]
    assert isinstance(ev, dict) and ev["tipo"] == "motor" and ev["texto"]


def test_las_dos_capacidades_entraron_a_SKILLS():
    from api.services.av_agent_skills import SIN_IA, catalogo
    ids = {s.id: s for s in catalogo()}
    for k in ("detectar.motor_caido", "detectar.tabla_quieta"):
        assert k in ids, f"la ley de §0.o: {k} tiene que estar mapeada"
        assert ids[k].usa_ia == SIN_IA


# ── La gracia del arranque (2026-08-20) ────────────────────────────────────

def test_en_los_primeros_minutos_de_rueda_NO_canta_nada(monkeypatch):
    """**La ventana del árbol abre 20 minutos ANTES de que los motores
    arranquen** (`_APERTURA["rueda"]` = 10:00 ART; el cron los prende 13:20 UTC).
    Con umbrales de 60-120 s, entre las 10:03 y las 10:20 los doce motores dan
    CRÍTICO sin estar caídos — un aviso en ALTA cada mañana a la misma hora, que
    es la forma más rápida de que se deje de leer."""
    from datetime import datetime

    from core.tz import AR_TZ

    monkeypatch.setattr("core.tz.ahora_ar",
                        lambda: datetime(2026, 8, 20, 10, 5, tzinfo=AR_TZ))
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "critico")))
    assert mot.detectar_motores() == []


def test_pasada_la_gracia_un_motor_muerto_SI_se_canta(monkeypatch):
    """No es tolerancia: pasado ese rato, no producir sí es estar caído."""
    from datetime import datetime

    from core.tz import AR_TZ

    monkeypatch.setattr("core.tz.ahora_ar",
                        lambda: datetime(2026, 8, 20, 11, 30, tzinfo=AR_TZ))
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "critico")))
    assert len(mot.detectar_motores()) == 1


def test_la_gracia_solo_aplica_EN_rueda(monkeypatch):
    """Fuera de rueda ya no se reporta nada por la ventana; la gracia no puede
    ser una segunda razón para callar algo `always` que sí está roto."""
    assert mot._recien_abrio({"en_rueda": False}) is False


def test_la_hora_sale_de_core_tz_y_no_de_datetime_now():
    """El Droplet corre en UTC. Restar tres horas a mano es el bug que `core/tz`
    existe para no repetir."""
    import inspect
    src = inspect.getsource(mot._recien_abrio)
    assert "ahora_ar()" in src and "utcnow" not in src
