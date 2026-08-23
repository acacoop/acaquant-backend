"""LA REFERENCIA ROTA NO JUZGA, Y EL JUEZ LOCAL TOMA LA POSTA (§0.db).

El caso VSCYO (2026-08-23): 1816 publicaba paridad 0,06% y TEA 499.839% para
un bono cuya duration coincidía con la nuestra al 0,00%. La cadena concluía
«se contradicen → otro cronograma» — **aritméticamente imposible**: la
duration depende SOLO del cronograma y las fechas; si coincide, el cronograma
es el mismo y lo roto es la referencia. Y como el cotejo era el único juez,
el bono quedaba BLOQUEADO para siempre — contra la ley del user de que nada
puede quedar sin salida.

Este archivo congela: (1) referencia inverosímil o contradicha por la
duration → NO_SE + `ref_inutil`, nunca «otro cronograma»; (2) con
`ref_inutil` aparece el JUEZ LOCAL (el mismo de `_arreglo_local`); (3) las
otras tres contradicciones de esa misma pantalla.
"""
from __future__ import annotations

import inspect

from api.services import av_agent_alta as alta


def codigo(fn) -> str:
    return inspect.getsource(fn)


# ── 1. la referencia rota no condena ────────────────────────────────────────

def test_duration_clavada_no_puede_ser_otro_cronograma():
    """El caso VSCYO literal: duration 0,00% de diferencia y la paridad de
    1816 en 0,06%. Antes: BLOQUEA «otro cronograma». Ahora: la referencia no
    sirve de juez, y lo dice."""
    ref = {"paridad": 0.0006, "tea": 4998.39, "duration": 1.3724}
    p = alta._cotejo_tea(0.0401, ref, paridad=100.10, duration=1.3724,
                         cota_ic=None, precio=100.10)
    assert p["estado"] == alta.NO_SE
    assert p.get("ref_inutil") is True


def test_paridad_inverosimil_de_1816_tampoco_condena():
    """Sin duration para desempatar, una paridad de 1816 fuera del rango sano
    del propio sistema no puede ser el juez."""
    ref = {"paridad": 0.0006}
    p = alta._cotejo_tea(0.04, ref, paridad=100.0, duration=None,
                         cota_ic=None, precio=100.0)
    assert p["estado"] == alta.NO_SE
    assert p.get("ref_inutil") is True


def test_una_contradiccion_de_verdad_SIGUE_bloqueando():
    """La guarda no abre la puerta a escribir cualquier cosa: referencia sana
    (60% está en rango) y sin duration que la contradiga → BLOQUEA igual que
    siempre."""
    ref = {"paridad": 0.60}
    p = alta._cotejo_tea(0.04, ref, paridad=100.0, duration=None,
                         cota_ic=None, precio=100.0)
    assert p["estado"] == alta.BLOQUEA
    assert not p.get("ref_inutil")


# ── 2. el juez local toma la posta ──────────────────────────────────────────

def test_con_ref_inutil_aparece_el_juez_local():
    src = codigo(alta._chequeos_arreglo)
    assert 'if cot_prop.get("ref_inutil"):' in src
    assert '"juez_local"' in src
    # El mismo criterio que `_arreglo_local`: estaba mal → vuelve al rango.
    assert "en_rango and estaba_mal" in src


def test_el_desenlace_cuenta_que_juzgo_el_juez_local():
    """Con el cotejo imposible (referencia rota o papel sin precio) y un juez
    local en verde, el encabezado no puede decir «reintentá» — reintentar
    contra 1816 no cambia nada. Se cubren los DOS jueces."""
    src = codigo(alta._desenlace)
    assert '("juez_local", "juez_falla")' in src
    assert "juzgó el JUEZ" in src


def test_sin_precio_juzga_la_falla_medida():
    """«No se resuelven nada» (user, 2026-08-23): casi todos los bloqueados
    eran papeles SIN precio y todos los jueces juzgaban por precio. Sus fallas
    se miden sin precio: la Σ del cuadro vuelve a base 100 y `moneda_flujo`
    pasa a coincidir con la regla del motor. Si la falla medida desaparece,
    el arreglo está verificado — sin precio no hay TEA que pueda salir mal."""
    src = codigo(alta._chequeos_arreglo)
    assert '"juez_falla"' in src
    assert '_residual_vivo({"flujos": conv["flujos"]}, rama)' in src
    # Y el arreglo simple (moneda sola, sin precio) también tiene su salida.
    assert '"juez_falla"' in codigo(alta._arreglo_local)


def test_el_informe_masivo_habla_corto():
    """«El informe es un tablero, no un testamento»: por fila queda el HECHO
    en una frase — la explicación vive en el modal del bono."""
    from api.services import av_agent_masivo as m
    assert m._titulo_corto(
        "La moneda: ¿en qué unidad entra el precio al motor?") == "La moneda"
    assert m._frase_corta(
        "`moneda_flujo`=**ARS** pero los ejes piden **DL** → el motor usa el "
        "precio tal cual") == "`moneda_flujo`=ARS pero los ejes piden DL"
    src = codigo(m._diagnosticar_uno)
    # La conclusión y las dos cuentas espejo no se repiten en las trabas.
    for clave in ('"causa_local"', '"escala_local"', '"division_local"'):
        assert clave in src


# ── 3. las otras contradicciones de la misma pantalla ───────────────────────

def test_un_BLOQUEA_gana_al_ambar_en_la_traba():
    """«Nada probado mal» sobre una cadena con un ✘ era la pantalla
    contradiciéndose. Cubierto también en test_av_agent_desenlace."""
    src = codigo(alta._desenlace)
    assert "(bloqueadas or fallan)[0]" in src


def test_sigma_cero_no_es_base_100():
    """El paso decía «Σ = 0.00 → está en base 100, como corresponde». Un campo
    vacío no es una escala sana — y dos renglones abajo otra lente decía que
    el cuadro estaba en el campo de la OTRA rama."""
    src = codigo(alta._diagnostico_local)
    assert "el campo que lee esta rama está **vacío**" in src


def test_cotejo_hoy_no_culpa_a_la_red_si_1816_contesto():
    """Decía «no se pudo consultar a 1816» con la respuesta de 1816 usada dos
    renglones arriba — lo que faltaba era NUESTRA paridad (el síntoma)."""
    src = codigo(alta._chequeos_arreglo)
    assert "NUESTRA paridad de HOY" in src
    assert 'sin_red = bool(ref.get("error")) or not ref' in src


def test_en_el_arreglo_las_lentes_son_contexto():
    """Las 8 lentes son el PORQUÉ de la propuesta, no pruebas del arreglo: un
    síntoma en ámbar es lo esperado (por eso hay propuesta) y no puede ser
    «LA TRABA» destacada. En los DOS caminos del arreglo."""
    for fn in (alta._chequeos_arreglo, alta._arreglo_local):
        src = codigo(fn)
        assert 'p["capa"] = CONTEXTO' in src, fn.__name__
