"""Paso 9 — sacarle la especie al TICKER del master.

Lo que se congela es qué se considera "sufijo de especie" y qué no. Pasarse de
listo acá renombra ONs cuyo nombre TERMINA en O/D como parte del ticker, y eso
rompe el join con la tenencia.
"""
from __future__ import annotations

from scripts.limpiar_ticker_curvas import limpiar, planificar


def test_saca_la_especie_de_soberanos_y_letras():
    assert limpiar("AL30D") == "AL30"
    assert limpiar("GD29C") == "GD29"
    assert limpiar("AO27D") == "AO27"


def test_el_ticker_limpio_no_se_toca():
    assert limpiar("AL30") is None
    assert limpiar("TX26") is None


def test_no_toca_las_ONs():
    """`AERBO` es el bono, no la pata de `AERB`. La regla pide un DÍGITO antes de
    la letra final — es lo único que separa las dos convenciones del mercado."""
    assert limpiar("AERBO") is None
    assert limpiar("BACGO") is None
    assert limpiar("VSCWD") is None      # sin dígito antes de la D


def test_bopreales_y_similares_quedan_igual():
    assert limpiar("BPOA7") is None
    assert limpiar("BPOC7") is None


def test_una_colision_no_se_migra():
    """Si `AL30` ya existe como fila propia, renombrar `AL30D` choca con la PK.
    Son dos filas del mismo bono y elegir cuál sobrevive no es de un script."""
    migrar, choques = planificar(["AL30", "AL30D", "GD29D"])
    assert [m["de"] for m in migrar] == ["GD29D"]
    assert [c["de"] for c in choques] == ["AL30D"]


def test_dos_especies_del_mismo_bono_chocan_entre_si():
    """`AL30D` y `AL30C` van LOS DOS a `AL30`. Aunque `AL30` no exista todavía,
    migrar los dos revienta la PK en el segundo UPDATE. Este choque sólo se ve
    mirando el conjunto entero — fila por fila los dos parecen seguros."""
    migrar, choques = planificar(["AL30D", "AL30C"])
    assert migrar == []
    assert sorted(c["de"] for c in choques) == ["AL30C", "AL30D"]


def test_un_choque_no_frena_a_los_demas():
    migrar, choques = planificar(["AL30", "AL30D", "GD29D", "TX26"])
    assert [m["de"] for m in migrar] == ["GD29D"]
    assert [c["de"] for c in choques] == ["AL30D"]


def test_ningun_destino_se_repite_entre_los_migrables():
    """El invariante que hace segura la escritura: si dos filas fueran al mismo
    ticker, el UPDATE fallaría a mitad de la transacción."""
    migrar, _ = planificar(["AL30D", "AL30C", "GD29D", "GD30D", "AO27D"])
    destinos = [m["a"] for m in migrar]
    assert len(destinos) == len(set(destinos))


def test_lista_vacia_no_rompe():
    assert planificar([]) == ([], [])
