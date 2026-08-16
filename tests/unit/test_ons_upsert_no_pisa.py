"""Editar un bono desde Manager NO puede borrar lo que escribió otro proceso.

Incidente encontrado el 2026-08-16 leyendo el camino de escritura, antes de que
nadie lo reportara — y ese es el punto: **no había nada que se viera mal**.

La cadena: `jobs/ficha_1816.py` estandariza el emisor con un `UPDATE` a la
COLUMNA. El editor de Manager mergea sobre `curvas_sql.find_one`, que lee el BLOB
`data`. Como el blob nunca se enteró, el merge lo pisaba — y de dos maneras según
el bono, las dos silenciosas:

  · bono NO-ON  → el blob no tiene `emisor` → se escribía NULL
  · ON          → el blob tiene el emisor VIEJO → se revertía la estandarización

En los dos casos la fila sigue existiendo, la pantalla se ve igual de prolija, y
cualquier cosa que AGRUPE por emisor empieza a contar mal.
"""
from __future__ import annotations

from api.services.ons import fila_a_escribir

# El blob de un bono NO-ON: nunca tuvo `emisor` (el form de alta no lo pide).
_BLOB_BONO = {"ticker_corto": "TX26", "ticker": "MERV - XMEV - TX26 - 24hs",
              "curva": "cer", "tipo": "cer", "valor_nominal": 100}

# El de una ON: tiene el emisor VIEJO, con la grafía que 1816 vino a arreglar.
_BLOB_ON = {**_BLOB_BONO, "ticker_corto": "CO32", "curva": "on_finanzas",
            "emisor": "BCO.COMAFI", "sector": "finanzas"}


def test_editar_un_bono_no_toca_la_columna_emisor():
    """El caso que borraba el dato: el payload no habla de emisor, así que la
    columna NO se escribe. Sacar la clave del dict es exactamente 'no tocar esa
    columna' — `_mirror` solo escribe las columnas presentes."""
    fila = fila_a_escribir(_BLOB_BONO, {"ticker_corto": "TX26", "cupon_anual": 2})
    assert "emisor" not in fila
    assert "sector" not in fila
    assert fila["cupon_anual"] == 2          # lo que SÍ se pidió, se escribe


def test_editar_una_ON_no_revierte_el_emisor_estandarizado():
    """Más traicionero que el NULL: acá el blob tiene un valor y es el VIEJO, así
    que el pisón no deja rastro de que hubo un pisón."""
    fila = fila_a_escribir(_BLOB_ON, {"ticker_corto": "CO32", "curva": "on_energia"})
    assert "emisor" not in fila
    assert fila["curva"] == "on_energia"


def test_si_el_payload_TRAE_el_emisor_se_escribe():
    """No es un candado: el alta de ONs manda `emisor` y esa escritura es
    intencional. Lo que se bloquea es el ARRASTRE del blob viejo, no la edición."""
    fila = fila_a_escribir(_BLOB_ON, {"ticker_corto": "CO32", "emisor": "Banco Comafi"})
    assert fila["emisor"] == "Banco Comafi"


def test_set_sector_sigue_pudiendo_escribir_el_sector():
    fila = fila_a_escribir(_BLOB_ON, {"ticker_corto": "CO32", "sector": "energia"})
    assert fila["sector"] == "energia"
    assert "emisor" not in fila              # el otro sigue protegido


def test_un_emisor_vacio_explicito_igual_se_respeta():
    """Vaciarlo A PROPÓSITO tiene que seguir siendo posible: la clave está en el
    payload, así que es una decisión y no un arrastre."""
    fila = fila_a_escribir(_BLOB_ON, {"ticker_corto": "CO32", "emisor": ""})
    assert "emisor" in fila and fila["emisor"] is None


def test_el_resto_de_las_columnas_se_sigue_escribiendo_igual():
    """La protección es SOLO para las dos columnas ajenas: si se extendiera de más,
    el editor dejaría de poder editar."""
    fila = fila_a_escribir(_BLOB_BONO, {"ticker_corto": "TX26", "curva": "tasa_fija"})
    for col in ("ticker", "instrumento", "curva", "tipo", "valor_nominal",
                "fecha_emision", "fecha_vencimiento", "flujos", "data"):
        assert col in fila, col
    assert fila["ticker"] == "TX26"                          # PK = ticker del bono
    assert fila["instrumento"] == "MERV - XMEV - TX26 - 24hs"  # símbolo de mercado
