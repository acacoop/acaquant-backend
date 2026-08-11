"""Tests de la vista FINANCIAMIENTO (tab de /operaciones).

Lo que se protege acá es la decisión que da sentido a la pantalla: QUÉ TASA le
toca a cada posición. La tasa vive en los boletos MAV y la posición en la
tenencia — son dos tablas distintas que se cruzan por (id_cuenta, código del
instrumento). Si ese cruce se rompe, la columna TASA no se vacía: se llena con
la tasa de OTRO, que es mucho peor porque no se nota.
"""
from datetime import date

from api.services.financiamiento import armar_filas

HOY = "2026-08-11"


def _row(id_cuenta="534", cuenta="[534] EGUREN, NE", unidad="[*ACI250300289] *ACI250300289 Nro. 3027",
         ticker="*ACI250300289", emisor="OTROS", clase="DL", vto=date(2026, 9, 10),
         cantidad=100_000, moneda="ARS"):
    return (id_cuenta, cuenta, unidad, ticker, emisor, clase, vto, cantidad, moneda)


def _tasa(tasa=6.0, tasa_min=6.0, tasa_max=6.0, n_boletos=1):
    return {"tasa": tasa, "tasa_min": tasa_min, "tasa_max": tasa_max, "n_boletos": n_boletos}


def test_la_tasa_se_asigna_por_cuenta_Y_codigo_no_solo_por_codigo():
    """El mismo papel comprado por dos clientes a tasas distintas NO se mezcla.

    Es el invariante central: la tasa es de la OPERACIÓN de ese cliente, no una
    propiedad del instrumento. Matchear solo por código le pondría a un cliente
    la tasa que consiguió el otro.
    """
    rows = [_row(id_cuenta="534"), _row(id_cuenta="700", cuenta="[700] OTRO")]
    tasas = {
        ("534", "*ACI250300289"): _tasa(6.0),
        ("700", "*ACI250300289"): _tasa(39.5),
    }
    filas, con_tasa = armar_filas(rows, tasas, HOY)
    assert con_tasa == 2
    assert [f["tasa"] for f in filas] == [6.0, 39.5]


def test_sin_match_la_tasa_queda_None_y_no_se_inventa():
    """Una posición sin boleto MAV parseado NO recibe tasa. Vale '—', no un 0."""
    filas, con_tasa = armar_filas([_row()], {}, HOY)
    assert con_tasa == 0
    assert filas[0]["tasa"] is None
    assert filas[0]["n_boletos"] == 0


def test_la_tasa_de_otra_cuenta_no_se_filtra():
    """Hay tasa para ESE código pero de OTRO cliente → la fila queda sin tasa."""
    filas, con_tasa = armar_filas(
        [_row(id_cuenta="534")], {("999", "*ACI250300289"): _tasa(6.0)}, HOY)
    assert con_tasa == 0
    assert filas[0]["tasa"] is None


def test_dias_al_vencimiento_cuenta_desde_hoy():
    filas, _ = armar_filas([_row(vto=date(2026, 8, 11))], {}, HOY)
    assert filas[0]["dias"] == 0          # vence HOY → entra, con 0 días
    filas, _ = armar_filas([_row(vto=date(2026, 9, 10))], {}, HOY)
    assert filas[0]["dias"] == 30


def test_dispersion_viaja_para_poder_marcar_el_promedio():
    """Comprar a 6% y a 39% promedia en algo que no pasó nunca: la vista necesita
    min/max para marcarlo en vez de mostrar el promedio a secas."""
    filas, _ = armar_filas(
        [_row()], {("534", "*ACI250300289"): _tasa(22.75, 6.0, 39.5, 2)}, HOY)
    f = filas[0]
    assert (f["tasa"], f["tasa_min"], f["tasa_max"], f["n_boletos"]) == (22.75, 6.0, 39.5, 2)


def test_clase_y_moneda_se_normalizan_a_mayuscula():
    filas, _ = armar_filas([_row(clase="hd", moneda="usd")], {}, HOY)
    assert (filas[0]["clase"], filas[0]["moneda"]) == ("HD", "USD")


def test_sin_clase_la_fila_no_se_pierde():
    """Un asset que el job todavía no clasificó va con `clase=''` y la vista lo
    agrupa en SIN CLASIFICAR. Descartarlo escondería posiciones reales."""
    filas, _ = armar_filas([_row(clase=None)], {}, HOY)
    assert filas[0]["clase"] == ""
    assert filas[0]["cantidad"] == 100_000


def test_cuenta_vacia_cae_al_id_en_vez_de_dejar_la_celda_en_blanco():
    filas, _ = armar_filas([_row(cuenta="  ")], {}, HOY)
    assert filas[0]["cuenta"] == "534"


def test_sin_ticker_no_se_intenta_matchear_tasa():
    """Sin código no hay con qué cruzar: la fila igual se muestra (la posición
    existe) pero se cae al `unidad` como etiqueta y no busca tasa."""
    filas, con_tasa = armar_filas(
        [_row(ticker="")], {("534", ""): _tasa(6.0)}, HOY)
    assert con_tasa == 0
    assert filas[0]["tasa"] is None
    assert filas[0]["ticker"].startswith("[*ACI250300289]")
