"""Tests de la tab MONITOR (`api/services/monitor_sql.py`).

Lo que se congela acá NO son los números: es lo que **no falla cuando se rompe**.
Las tres cosas que este service puede hacer mal en silencio son elegir la tabla
equivocada, correr la hora tres horas, y dibujar un perfil con huecos — ninguna
tira una excepción, todas dibujan algo verosímil y distinto.
"""
from __future__ import annotations

import pytest

from api.services import monitor_sql as svc

# ── la fuente por (clase, ventana) ───────────────────────────────────────────
# Es EL invariante de este service. Renta variable cambia de tabla al pasar de
# HOY a multi-rueda porque su tape se vacía todas las noches; si esa regla se
# invierte, la pantalla muestra el archivo cuando quería el tape (o al revés) y
# se ve perfecta: mismos ejes, mismo formato, otros datos.

def test_rv_hoy_sale_del_tape_y_multirueda_del_archivo():
    assert svc._fuente("rv", 1).tabla == "mercado.cedears_time_sales"
    assert svc._fuente("rv", 5).tabla == "mercado.cedears_bars_1m"
    assert svc._fuente("rv", 20).tabla == "mercado.cedears_bars_1m"


def test_rf_sale_siempre_del_mismo_tape():
    for ruedas in (1, 3, 5):
        assert svc._fuente("rf", ruedas).tabla == "mercado.timesales"


def test_solo_el_archivo_se_declara_aproximado():
    """`aproximado` es lo único que le dice a la pantalla que el POC salió de
    barras y no de trades. Si se marcara al revés, la mesa creería exacto un
    número que no lo es."""
    por_tabla = {v["fuente"]: v["aproximado"] for v in svc.ventanas("rv")}
    assert por_tabla["cedears_bars_1m · barras de 1'"] is True
    assert por_tabla["cedears_time_sales · tick a tick"] is False
    assert all(v["aproximado"] is False for v in svc.ventanas("rf"))


def test_las_ventanas_ofrecidas_son_las_que_el_service_acepta():
    """La pantalla dibuja el selector con `ventanas()`. Si ofreciera una que
    `get_monitor` rechaza, el botón existiría y tiraría 400 al tocarlo."""
    for clase in svc.CLASES:
        ofrecidas = {v["ventana"] for v in svc.ventanas(clase)}
        assert ofrecidas == set(svc.VENTANAS[clase])


# ── la hora ──────────────────────────────────────────────────────────────────

def test_solo_las_tablas_aware_se_bajan_a_hora_argentina():
    """`mercado.timesales` guarda naive ART (ver sql/schema.sql) y las otras dos
    son timestamptz. Bajar la que no corresponde corre la rueda 3 horas: el
    chart sigue dibujando, pero abre a las 8 de la mañana."""
    assert "AT TIME ZONE" not in svc._expr_ts(svc._TAPE_RF)
    assert "AT TIME ZONE" in svc._expr_ts(svc._TAPE_RV)
    assert "AT TIME ZONE" in svc._expr_ts(svc._BARS_RV)


def test_el_bucket_temporal_no_pasa_por_epoch():
    """epoch obliga a pasar por timestamptz y ahí vuelve el corrimiento de zona
    que el test de arriba cuida. El bucket se arma con date_trunc."""
    for paso in (1, 5, 15):
        sql = svc._expr_bucket(paso)
        assert "epoch" not in sql.lower()
        assert "date_trunc" in sql
    assert "make_interval(mins => (EXTRACT(MINUTE FROM ts)::int / 15) * 15)" in \
        svc._expr_bucket(15)


# ── el SQL: cada fuente con su precio ────────────────────────────────────────

def test_el_perfil_de_barras_usa_el_precio_tipico_y_el_de_ticks_el_precio():
    barras = svc._sql(svc._BARS_RV, paso=15, nb=26)
    ticks = svc._sql(svc._TAPE_RV, paso=1, nb=26)
    assert "(high + low + close) / 3" in barras
    assert "(high + low + close) / 3" not in ticks
    # el tope del último bucket se empuja SIEMPRE: sin eso el precio máximo cae
    # en el bucket nb+1 y en un papel ilíquido ese fantasma sale como POC
    for sql in (barras, ticks):
        assert "r.hi + (r.hi - r.lo) / 1000" in sql


def test_el_where_entra_por_la_clave_y_la_fecha():
    """Un instrumento por vez, por índice. Si el filtro cambiara de forma, la
    query pasaría a escanear la tabla entera sin fallar — solo tardando."""
    sql = svc._sql(svc._TAPE_RF, paso=5, nb=26)
    assert "WHERE ticker = %(clave)s AND ts >= %(ini)s" in sql


# ── el perfil (lógica pura) ──────────────────────────────────────────────────

def test_rellenar_buckets_devuelve_el_histograma_completo_y_contiguo():
    """La query solo trae los buckets con volumen. Sin relleno, dos buckets
    vacíos seguidos se dibujan como uno y el perfil miente la forma."""
    filas = [{"b": 1, "vol": 10, "trades": 2}, {"b": 4, "vol": 30, "trades": 5}]
    bks = svc.rellenar_buckets(filas, nb=4, lo=100.0, hi=104.0)
    assert len(bks) == 4
    assert [b["vol"] for b in bks] == [10.0, 0.0, 0.0, 30.0]
    assert [b["px_lo"] for b in bks] == [100.0, 101.0, 102.0, 103.0]
    # contiguos: el techo de cada uno es el piso del siguiente
    assert all(bks[i]["px_hi"] == bks[i + 1]["px_lo"] for i in range(3))


def test_rellenar_buckets_con_un_solo_precio_no_revienta():
    """Un papel que operó todo el día al mismo precio (rango cero) es raro pero
    existe, y no puede tirar una división por cero."""
    bks = svc.rellenar_buckets([{"b": 1, "vol": 5, "trades": 1}], nb=3, lo=50.0, hi=50.0)
    assert len(bks) == 3
    assert all(b["px_lo"] == 50.0 and b["px_hi"] == 50.0 for b in bks)


def test_el_poc_es_el_bucket_de_mas_volumen():
    bks = svc.rellenar_buckets(
        [{"b": 1, "vol": 10, "trades": 1}, {"b": 2, "vol": 90, "trades": 9}],
        nb=2, lo=10.0, hi=12.0)
    poc = svc._poc(bks)
    assert poc is not None
    assert poc["vol"] == 90.0
    assert poc["px"] == 11.5  # centro del bucket 2, que va de 11 a 12


def test_sin_volumen_no_hay_poc_ni_area_de_valor():
    """Un papel que no operó no tiene POC. Devolver el primer bucket sería
    inventar un nivel que la mesa podría mirar."""
    bks = svc.rellenar_buckets([], nb=5, lo=1.0, hi=2.0)
    assert svc._poc(bks) is None
    assert svc.area_de_valor(bks) == (None, None)


def test_area_de_valor_toma_los_buckets_mas_gordos_hasta_el_70():
    # 60 + 20 = 80 % ≥ 70 % con dos buckets; los de 10 y 10 quedan afuera
    bks = svc.rellenar_buckets(
        [{"b": 1, "vol": 10, "trades": 1}, {"b": 2, "vol": 60, "trades": 6},
         {"b": 3, "vol": 20, "trades": 2}, {"b": 4, "vol": 10, "trades": 1}],
        nb=4, lo=100.0, hi=104.0)
    val, vah = svc.area_de_valor(bks)
    assert (val, vah) == (101.0, 103.0)   # buckets 2 y 3


def test_area_de_valor_es_un_rango_y_puede_contener_un_bucket_flojo():
    """Los elegidos son 1 y 4 (90 % entre los dos); la banda los abarca y adentro
    quedan 2 y 3, que no se eligieron. Es un rango, no un conjunto — así se lee
    en cualquier plataforma, y por eso el test lo fija."""
    bks = svc.rellenar_buckets(
        [{"b": 1, "vol": 45, "trades": 4}, {"b": 2, "vol": 5, "trades": 1},
         {"b": 3, "vol": 5, "trades": 1}, {"b": 4, "vol": 45, "trades": 4}],
        nb=4, lo=0.0, hi=4.0)
    assert svc.area_de_valor(bks) == (0.0, 4.0)


# ── validación de entrada ────────────────────────────────────────────────────

def test_clase_y_ventana_invalidas_se_rechazan():
    with pytest.raises(ValueError, match="clase"):
        svc.get_monitor(clase="acciones", ticker="NVDA", ventana="hoy")
    with pytest.raises(ValueError, match="ventana"):
        svc.get_monitor(clase="rv", ticker="NVDA", ventana="99r")
    with pytest.raises(ValueError, match="ticker"):
        svc.get_monitor(clase="rv", ticker="  ", ventana="hoy")


def test_una_ventana_no_se_cuela_de_una_clase_a_la_otra():
    """`20r` existe en renta variable (sale del archivo de barras) y NO en renta
    fija, cuyo tape guarda 5-6 ruedas. Aceptarla devolvería la ventana entera
    disfrazada de 20 ruedas."""
    with pytest.raises(ValueError, match="ventana"):
        svc.get_monitor(clase="rf", ticker="AL30", ventana="20r")
    with pytest.raises(ValueError, match="ventana"):
        svc.get_monitor(clase="rv", ticker="NVDA", ventana="3r")
