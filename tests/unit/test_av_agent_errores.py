"""«PONER UNA LÍNEA DE CÓDIGO Y DECIR QUE NO ANDA ES INENTENDIBLE.»

El user (2026-08-21), mirando tres filas de motores:

    motor_curvas: <fecha>,<n> ERROR pg_mirror pg_mirror market_snapshot: de…

    *«Es imposible entender qué es el error, qué está pasando o qué pasó, si
    sigue pasando. Está el mensaje cortado, aparte todo súper técnico, no se
    entiende a qué está afectando de la app.»*

Y el porqué, que es lo que define el diseño:

    *«Los motores y los jobs hacen cosas LINEALES, no son a interpretación.
    Siempre tienen que estar analizados.»*

Tres preguntas, contestadas sin apretar nada: QUÉ PASÓ · A QUÉ AFECTA · SI SIGUE.
"""
from __future__ import annotations

from api.services import av_agent_errores as tr

# ── la REGLA traduce lo conocido, gratis y sin alucinar ─────────────────────

def test_los_errores_REALES_de_la_pantalla_se_traducen_por_regla():
    """Son NUESTROS motores: fallan de un conjunto finito de formas. Que la
    regla los cubra es lo que hace que esto no cueste un token."""
    casos = {
        "pg_mirror market_snapshot: deadlock detected": "guardar",
        "ModuleNotFoundError: No module named 'core.ai_resumen'": "no arranca",
        "core.websocket WS Portfolio: se cayo la conexion": "feed",
        "MotorOpciones Expiries ya vencidas": "vencidos",
        "1 tickers sin trade en 2026-08-20": "no operó",
    }
    for linea, esperado in casos.items():
        r = tr.por_regla(linea)
        assert r is not None, f"sin traducción: {linea}"
        assert esperado in r[0].lower(), f"«{linea}» → {r[0]}"


def test_lo_que_NO_es_culpa_nuestra_no_se_marca_urgente():
    """Un bono que no operó y un catálogo con contratos vencidos no son plata
    perdida. Marcarlos igual que un error de escritura enseña a ignorar los dos."""
    assert tr.por_regla("1 tickers sin trade")[2] is False
    assert tr.por_regla("Expiries ya vencidas")[2] is False
    assert tr.por_regla("pg_mirror deadlock")[2] is True


def test_una_linea_desconocida_devuelve_None_y_no_inventa():
    assert tr.por_regla("zzz algo que nunca vimos zzz") is None


def test_la_traduccion_NO_menciona_clases_de_python():
    """Es para alguien que no programa: si dice `ModuleNotFoundError` no
    tradujo nada."""
    for _pats, texto, _mas, _u in tr.FIRMAS:
        assert "Error:" not in texto and "Exception" not in texto
        assert texto == texto.lstrip(), texto


# ── A QUÉ AFECTA: un HECHO declarado, nunca del modelo ──────────────────────

def test_cada_motor_dice_que_pantalla_rompe():
    assert "RENTA FIJA" in tr.a_que_afecta("motor_curvas")
    assert "precios" in tr.a_que_afecta("motor_rofex")


def test_el_LABEL_DEL_CRON_se_resuelve_a_su_modulo():
    """⚠️ El chequeo se llama `portafolio_diario` y la ficha vive bajo
    `portafolio_backfill`. Sin resolverlo, **el job más caro de perder** (el AuM
    entero) salía sin decir a qué afecta. La resolución ya existía en
    `jobs_catalogo`: se delega, no se copia (REGLA #9)."""
    assert "AuM" in tr.a_que_afecta("portafolio_diario")


def test_un_componente_desconocido_no_inventa_un_impacto():
    assert tr.a_que_afecta("motor_que_no_existe") == ""


def test_el_modelo_NO_decide_a_que_afecta():
    """Inventar consecuencias es lo que haría desconfiar de todo el resto. Al
    LLM se le pide la traducción del error y nada más."""
    assert "NO inventes a qué afecta" in tr._SYSTEM
    import inspect
    assert "a_que_afecta" not in inspect.getsource(tr._con_ia)


# ── la IA: una vez por PATRÓN, no por fila ──────────────────────────────────

def test_la_regla_gana_y_NO_llama_al_modelo(monkeypatch):
    monkeypatch.setattr(tr, "_con_ia",
                        lambda *a: (_ for _ in ()).throw(AssertionError("gastó")))
    monkeypatch.setattr(tr, "_guardada", lambda c: "")
    r = tr.explicar("motor_curvas", "pg_mirror deadlock", "")
    assert r["fuente"] == "regla" and "guardar" in r["pasa"].lower()


def test_lo_ya_explicado_sale_de_la_MEMORIA(monkeypatch):
    """El mismo error aparece 90 veces en 6 horas: pagar una llamada por
    aparición sería absurdo."""
    monkeypatch.setattr(tr, "_guardada", lambda c: "el motor se quedó sin datos")
    monkeypatch.setattr(tr, "_con_ia",
                        lambda *a: (_ for _ in ()).throw(AssertionError("gastó")))
    r = tr.explicar("motor_x", "patron nunca visto zzz", "")
    assert r["fuente"] == "ia" and r["pasa"] == "el motor se quedó sin datos"


def test_la_clave_es_el_PATRON_no_la_fila():
    """Dos apariciones del mismo error comparten explicación; dos errores
    distintos del mismo motor, no."""
    a = tr._clave("motor_x", "falló al escribir <n> filas")
    b = tr._clave("motor_x", "falló al escribir <n> filas")
    c = tr._clave("motor_x", "otra cosa")
    assert a == b and a != c


def test_lo_que_traduce_el_modelo_se_GUARDA(monkeypatch):
    guardado = {}
    monkeypatch.setattr(tr, "_guardada", lambda c: "")
    monkeypatch.setattr(tr, "_con_ia", lambda u, m: "se quedó sin memoria")
    monkeypatch.setattr(tr, "_guardar",
                        lambda c, u, p, e: guardado.update({"e": e}))
    r = tr.explicar("motor_x", "zzz raro", "zzz raro")
    assert r["fuente"] == "ia" and guardado["e"] == "se quedó sin memoria"


def test_sin_IA_se_muestra_la_linea_CRUDA_y_se_dice(monkeypatch):
    """Un texto feo es mejor que ningún texto — y `fuente='crudo'` deja ver
    cuántos patrones todavía no sabemos explicar."""
    monkeypatch.setattr(tr, "_guardada", lambda c: "")
    monkeypatch.setattr(tr, "_con_ia", lambda u, m: "")
    r = tr.explicar("motor_x", "zzz raro", "")
    assert r["fuente"] == "crudo" and r["pasa"] == "zzz raro"


def test_si_la_IA_EXPLOTA_el_aviso_igual_sale(monkeypatch):
    """El traductor no puede ser la causa de que el aviso no aparezca."""
    monkeypatch.setattr(tr, "_guardada", lambda c: "")
    monkeypatch.setattr(tr, "_con_ia",
                        lambda u, m: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tr.explicar("motor_x", "zzz", "")["fuente"] == "crudo"


def test_la_tarea_de_IA_es_la_BARATA_y_no_piensa():
    """No decide nada: traduce una oración. `pro` + thinking sería pagar por
    razonar sobre una línea de log."""
    from core.ai import _TAREAS
    cfg = _TAREAS["av_agent_error"]
    assert cfg["tier"] == "flash" and cfg["thinking"] == "disabled"
    assert cfg["max_tokens"] <= 500


# ── la fila del motor, ya analizada ─────────────────────────────────────────

def _grupo(**kw):
    base = {"unidad": "motor_curvas", "patron": "pg_mirror deadlock detected",
            "veces": 3, "primera": 1000.0, "ultima": 1060.0, "peor": 3,
            "nivel": "error",
            "muestra": "2026-08-21 16:49 ERROR pg_mirror deadlock detected"}
    return {**base, **kw}


def test_el_titulo_dice_QUE_PASO_y_no_la_linea_del_log():
    from api.services.av_agent_motores import _hallazgo_log
    h = _hallazgo_log(_grupo())
    assert h is not None
    assert "<fecha>" not in h["motivo"] and "pg_mirror" not in h["motivo"]
    assert "guardar" in h["motivo"].lower()
    assert "…" not in h["motivo"], (
        "el título se cortó: el `pasa` de la firma tiene que entrar en los 88 "
        "caracteres, si no volvemos al mensaje truncado que originó todo esto")


def test_la_evidencia_contesta_las_TRES_preguntas():
    from api.services.av_agent_motores import _hallazgo_log
    ev = _hallazgo_log(_grupo())["evidencia"]
    assert ev["pasa"] and ev["afecta"] and ev["sigue"]
    assert "RENTA FIJA" in ev["afecta"]
    # Y la línea original sigue estando: traducir no es esconder.
    assert "Log:" in ev["texto"]


def test_un_WARNING_de_catalogo_no_queda_en_severidad_ALTA():
    """90 repeticiones de «contratos vencidos» disparaban `machaca` en alta. Lo
    que decide es la CONSECUENCIA, no cuántas veces se repite."""
    from api.services.av_agent_motores import _hallazgo_log
    h = _hallazgo_log(_grupo(unidad="motor_options", patron="Expiries ya vencidas",
                             veces=90, peor=4, nivel="warning",
                             muestra="WARNING Expiries ya vencidas"))
    assert h["severidad"] == "media"


# ── SALUD también viene analizado ───────────────────────────────────────────

def test_el_chequeo_dice_QUE_fallo_sin_apretar_ANALIZAR():
    """El motivo decía «la corrida de 20/08 16:30 UTC falló» y el error real
    vivía tres clics adentro, atrás del botón ANALIZAR."""
    from api.services.av_agent import _motivo_salud
    m = _motivo_salud({
        "id": "job:controles_datos", "titulo": "controles_datos",
        "motivo": "la corrida de 20/08 16:30 UTC falló",
        "corridas": [{"errors": ["ModuleNotFoundError: No module named 'x'"]}]})
    assert "no arranca" in m and "ModuleNotFoundError" not in m
    assert "afecta" in m


def test_sin_error_registrado_se_deja_el_motivo_original():
    """No se inventa un diagnóstico donde no hay evidencia."""
    from api.services.av_agent import _motivo_salud
    c = {"id": "job:x", "titulo": "x", "motivo": "no registra corridas",
         "corridas": []}
    assert _motivo_salud(c) == "x: no registra corridas"


def test_a_un_JOB_QUE_FALLO_no_se_le_pregunta_si_ACERTO():
    """*«Los motores y los jobs hacen cosas lineales, no son a interpretación.»*
    Que la corrida falló lo dice `job_runs`: el agente acertó por construcción."""
    from api.services import av_agent
    assert av_agent.pregunta_de("salud") == av_agent.OBSERVACION
    assert av_agent.pregunta_de("motor_ruidoso") == av_agent.OBSERVACION
