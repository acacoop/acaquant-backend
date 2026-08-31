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
