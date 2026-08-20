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


def _arbol(*piezas, en_rueda=True, ahora="2026-08-19 14:00:00"):
    # `ahora_ar` no es decoración: es EL reloj con el que se juzga esta foto.
    # Antes el detector leía el del proceso y esta hora era ignorada — por eso
    # tres tests fallaban entre las 10:00 y las 10:30 ART y nadie sabía por qué.
    return {"en_rueda": en_rueda, "ahora_ar": ahora,
            "vistas": [{"vista": "MERCADOS", "resumen": {},
                        "grupos": [{"grupo": None, "piezas": list(piezas)}]}]}


def _p(label, estado, tipo="motor", **kw):
    # `ventana` va en el default: es lo que decide DESDE y HASTA qué hora el
    # problema es real, y una pieza de prueba sin ventana no representa a
    # ninguna real — todas la declaran en `diagnostico_registry`.
    return {"label": label, "tipo": tipo, "estado": estado, "cadencia": "live",
            "hace": "hace 40 min", "umbral_s": 120, "ultima": None,
            "ventana": "rueda", **kw}


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
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "critico"),
                                       ahora="2026-08-20 10:05:00"))
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


def test_la_GRACIA_se_mide_con_el_reloj_DEL_ARBOL_y_no_con_el_del_proceso(monkeypatch):
    """Dos relojes para juzgar UNA foto es cómo se cuela un veredicto que no
    corresponde al momento en que la foto se sacó. El docstring de `_recien_abrio`
    ya decía que la hora sale del árbol y el código llamaba a `ahora_ar()`; se
    descubrió porque tres tests fallaban SOLO entre las 10:00 y las 10:30 ART.
    Mismo bug que tenía `salud._chequeo_job`, mismo motivo."""
    # Se mira el IMPORT y no el texto: el docstring de `_recien_abrio` explica el
    # bug nombrando `ahora_ar()`, así que buscar la palabra hace fallar al test
    # por su propio comentario — la misma trampa que ya documentó el test de
    # contexto con la palabra «XIRR».
    import inspect
    src = inspect.getsource(mot)
    assert "from core.tz import ahora_ar" not in src, (
        "volvió a leer el reloj del proceso en vez del del árbol")
    # Y con un árbol a media rueda, la gracia NO aplica: la hora manda.
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "critico"),
                                       ahora="2026-08-20 14:00:00"))
    assert len(mot.detectar_motores()) == 1


def test_un_arbol_SIN_HORA_no_se_da_por_recien_abierto(monkeypatch):
    """Caer al reloj del proceso cuando falta la hora sería volver a tener dos
    relojes, solo que a veces — que es peor, porque a veces anda."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_rofex", "critico"), ahora=""))
    assert len(mot.detectar_motores()) == 1


# ── DESDE Y HASTA QUÉ HORA el problema es real (pedido del user, 2026-08-20) ──

def test_un_motor_que_NUNCA_escribio_no_es_un_problema_de_madrugada(monkeypatch):
    """**El hueco que quedaba.** `diagnostico._estado` devuelve `sin_datos` ANTES
    de mirar la ventana, así que un motor de mercado que nunca escribió salía en
    ALTA a las 3 de la mañana y los sábados. `fuera_rueda` ya estaba cubierto;
    éste no, porque nunca llegaba a compararse contra un umbral."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_cedears", "sin_datos",
                                          ventana="rueda"),
                                       ahora="2026-08-20 03:00:00"))
    assert mot.detectar_motores() == []


def test_el_mismo_motor_EN_RUEDA_si_es_un_problema(monkeypatch):
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_cedears", "sin_datos",
                                          ventana="rueda"),
                                       ahora="2026-08-20 14:00:00"))
    assert len(mot.detectar_motores()) == 1


def test_un_job_de_TODO_EL_DIA_no_se_suprime_por_la_hora(monkeypatch):
    """`always` y `diario` no tienen horario de mercado: suprimirlos de noche
    escondería justamente a los que corren de noche."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("bcra", "sin_datos", tipo="job",
                                          ventana="always"),
                                       ahora="2026-08-20 03:00:00"))
    assert len(mot.detectar_motores()) == 1


def test_el_hallazgo_DICE_desde_y_hasta_que_hora_es_real(monkeypatch):
    """*«Es fundamental entender desde qué hora hasta qué hora el error es real
    para cada motor»* (user). Sin eso, el que lee no puede decidir si tiene que
    actuar ahora o si la pieza ni siquiera debería estar corriendo."""
    monkeypatch.setattr("api.services.diagnostico.arbol",
                        lambda: _arbol(_p("motor_curvas", "critico"),
                                       ahora="2026-08-20 14:32:00"))
    ev = mot.detectar_motores()[0]["evidencia"]
    assert "10:00" in ev["texto"] and "17:05" in ev["texto"]
    assert "14:32" in ev["texto"]
    assert ev["ventana"] == "rueda" and ev["ahora_ar"] == "14:32"


def test_la_ventana_se_LEE_del_diagnostico_y_no_se_escribe_a_mano():
    """Escribir «10 a 17:05» en el texto del aviso sería una segunda verdad que
    se desactualiza sola el día que muevan el horario del mercado (REGLA #9)."""
    import inspect
    src = inspect.getsource(mot.ventana_en_palabras)
    assert "_APERTURA" in src and "_CIERRE" in src
    assert "17:05" not in src, "el horario está hardcodeado en el texto"
