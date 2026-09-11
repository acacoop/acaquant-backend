"""Tests del estado comercial (api/services/comercial.py).

Congela el semáforo de actividad: NUEVA / ACTIVA / ENFRIANDOSE / DORMIDA
según días sin operar (umbrales 30/90 por default).
"""
from __future__ import annotations

from api.services.comercial import estado_comercial
from jobs.negocio_movimientos import _extract_id_cuenta


def test_nunca_opero_es_nueva():
    assert estado_comercial(None, opero_alguna_vez=False, dias_activa=30, dias_dormida=90) == "NUEVA"


def test_opero_pero_fuera_de_ventana_es_dormida():
    # dias=None (no está en la ventana reciente) + operó alguna vez → DORMIDA.
    assert estado_comercial(None, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "DORMIDA"


def test_opero_reciente_es_activa():
    assert estado_comercial(10, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ACTIVA"


def test_borde_activa_inclusive():
    assert estado_comercial(30, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ACTIVA"


def test_entre_umbrales_es_enfriandose():
    assert estado_comercial(60, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ENFRIANDOSE"


# ── id_cuenta denormalizado (ingesta) ──────────────────────────────────────

def test_extract_id_cuenta():
    assert _extract_id_cuenta("[805] MOLLO NICOLAS") == "805"
    assert _extract_id_cuenta("[1114] OTRA CUENTA SA") == "1114"
    assert _extract_id_cuenta("sin corchete") is None
    assert _extract_id_cuenta("") is None
    assert _extract_id_cuenta(None) is None


# ── CTAS OPS del INFORME: una sola definición de "cuenta operativa" ──────────
#
# El informe se contradecía consigo mismo: la misma fila mostraba arancel cobrado
# (que sale de `operaciones.operaciones`) y 0 cuentas operativas (que salía de
# `negocio_movimientos`, donde las cuentas OTC no existen porque la ingesta las
# tira por el nombre). Si le cobramos arancel, operó.
#
# Estos tests son estructurales —miran el código, no la base— porque el bug no
# era un cálculo mal hecho sino de QUÉ TABLA se leía, y eso no se ve en un número.

def test_ctas_ops_sale_de_operaciones_y_no_de_negocio_movimientos():
    """La query que decide quién operó tiene que ir contra `operaciones` con el
    MISMO predicado que DÍAS SIN OPERAR. Si alguien la vuelve a apoyar en
    negocio_movimientos, las cuentas OTC desaparecen de nuevo y nada falla."""
    import inspect

    from api.services import comercial_sql as cs

    # Solo las líneas de CÓDIGO desde que se arma el predicado: los comentarios del
    # bloque nombran negocio_movimientos a propósito (explican de dónde NO sale).
    src = inspect.getsource(cs._rollup_por_cuenta)
    _, ancla, resto = src.partition("    w_act = ")
    assert ancla, "desapareció el predicado que calcula opero_mes"
    codigo = "\n".join(ln for ln in (ancla + resto).splitlines()
                       if not ln.lstrip().startswith("#"))
    assert "FROM operaciones WHERE" in codigo
    assert "_ULT_OP_WHERE" in codigo
    assert "negocio_movimientos" not in codigo
    # Scopeada al mes: sin esto es un scan histórico de la tabla de boletos.
    assert "concertacion >= %(mes_ini)s" in codigo


def test_el_contador_de_ctas_ops_lee_el_flag_y_no_el_volumen():
    """`n_ops_mes` (negocio_movimientos) era el que alimentaba CTAS OPS. Se sacó
    entero para que no quede un campo con nombre de 'cuentas que operaron' al que
    alguien pueda volver a engancharse."""
    import inspect

    from api.services import comercial_sql as cs

    cuerpo = inspect.getsource(cs.informe_comercial)
    assert 'agg.get("opero_mes")' in cuerpo
    assert "n_ops_mes" not in inspect.getsource(cs._rollup_por_cuenta)


def test_operativas_por_segmento_cuenta_lo_mismo_que_el_ranking():
    """El gráfico por segmento (modo Operativas) y la columna CTAS OPS del ranking
    son la MISMA pregunta en dos lugares de la misma pantalla. Si uno lee
    `operaciones` y el otro `negocio_movimientos`, muestran totales distintos y
    ninguno de los dos falla — que es exactamente cómo empezó esto."""
    import inspect

    from api.services import comercial_sql as cs

    seg = inspect.getsource(cs.informe_cuentas_por_segmento)
    _, ancla, resto = seg.partition("    w_op = ")
    assert ancla, "desapareció el predicado de operativas por segmento"
    codigo = "\n".join(ln for ln in (ancla + resto).splitlines()
                       if not ln.lstrip().startswith("#"))
    assert "_act_where('o')" in codigo
    assert "FROM operaciones o" in codigo
    assert "negocio_movimientos" not in codigo


# ── SEMÁFORO (ACTIVAS + ENFRIÁNDOSE) en el INFORME: una sola definición ─────
#
# El Informe pasó a mostrar ACTIVAS + ENFRIÁNDOSE (las dos juntas: el padrón que
# sigue vivo), que hasta ahora solo vivía en ANÁLISIS COMERCIAL. Es el caso exacto
# de la REGLA #9: el mismo dato en dos pantallas. Si cada una trae su propio umbral
# o reimplementa la clasificación, las dos siguen andando y cuentan cuentas
# distintas — nada falla, y el que mira no tiene cómo saber cuál creer. Estos tests
# congelan que haya UN solo umbral y UNA sola función que clasifica.

def test_los_umbrales_del_semaforo_viven_en_un_solo_lugar():
    """`analisis_comercial` y `detalle_ultima_op` no pueden traer su propio 45/90:
    tienen que tomar las constantes del módulo, las mismas que usa el Informe."""
    import inspect

    from api.services import comercial_sql as cs

    assert (cs.DIAS_ACTIVA, cs.DIAS_DORMIDA) == (45, 90)
    for fn in (cs.analisis_comercial, cs.detalle_ultima_op):
        firma = inspect.signature(fn)
        assert firma.parameters["dias_activa"].default is cs.DIAS_ACTIVA, fn.__name__
        assert firma.parameters["dias_dormida"].default is cs.DIAS_DORMIDA, fn.__name__


def test_semaforo_por_segmento_usa_su_ventana_y_el_mismo_predicado_de_boleto():
    """El conteo por segmento de Q1 cuenta CUENTAS DISTINTAS con al menos un boleto
    dentro de la ventana del semáforo, con el MISMO predicado que DÍAS SIN OPERAR.
    Sin ese piso sería un scan histórico de la tabla de boletos."""
    import inspect

    from api.services import comercial_sql as cs

    seg = inspect.getsource(cs.informe_cuentas_por_segmento)
    _, ancla, resto = seg.partition("    w_est = ")
    assert ancla, "desapareció el predicado del semáforo por segmento"
    codigo = "\n".join(ln for ln in (ancla + resto).splitlines()
                       if not ln.lstrip().startswith("#"))
    assert "_act_where('o')" in codigo
    assert "count(DISTINCT o.id_cuenta)" in codigo  # la cuenta, no el boleto
    assert "%(dorm_ini)s" in codigo                 # piso = corte − DIAS_DORMIDA
    assert "negocio_movimientos" not in codigo
    # El umbral entra por parámetro desde la constante, no escrito en el SQL.
    assert "dorm_ini = corte - timedelta(days=DIAS_DORMIDA)" in seg


def test_el_detalle_clasifica_con_estado_comercial_y_no_reimplementa_el_umbral():
    """Q4 tiene que llamar a `estado_comercial` (la función PURA que ya usa la tabla
    ESTADO COMERCIAL). Un `if dias <= 45` escrito acá sería la segunda definición."""
    import inspect

    from api.services import comercial_sql as cs

    src = inspect.getsource(cs.informe_segmento_detalle)
    assert 'estado_comercial((corte - ult).days, True, DIAS_ACTIVA,' in src
    assert 'in ("ACTIVA", "ENFRIANDOSE")' in src
    # Fuera de la ventana no inventa DORMIDA/NUEVA: el Informe no scanea el histórico.
    assert "(corte - ult).days > DIAS_DORMIDA" in src
    assert '"DORMIDA"' not in src and '"NUEVA"' not in src


def test_el_detalle_scanea_hasta_cubrir_la_ventana_del_semaforo():
    """El piso del scan es min(1 de enero, corte − DIAS_DORMIDA). Con el piso viejo
    (1 de enero a secas) y un corte de enero, una cuenta cuya última op fue en
    diciembre queda ENFRIÁNDOSE, el filtro la cuenta y la tabla no la muestra."""
    import inspect

    from api.services import comercial_sql as cs

    src = inspect.getsource(cs.informe_segmento_detalle)
    assert 'pa["scan_ini"] = min(pa["ano_ini"], corte - timedelta(days=DIAS_DORMIDA))' in src
    assert "concertacion >= %(scan_ini)s" in src
