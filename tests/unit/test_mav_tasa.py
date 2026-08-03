"""Parseo de la tasa MAV (core/mav_tasa.py) + resolución por boleto del job.

El precio de un boleto MAV es la TASA, y viaja embebida en el texto de
`negocio_movimientos.informacion`. Las cadenas de abajo son REALES (salida del
diagnóstico en prod, 2026-08-03) — si el parseo se rompe, la columna
`operaciones.tasa` se llena mal o se queda vacía en silencio.
"""
from __future__ import annotations

import pytest

from core.mav_tasa import num_ar, parse_informacion

# (texto, tasa, código, nominal) — cadenas reales de prod.
REALES = [
    ("Compra [#UCO310770001] 30.000,00@7% (ARS Inm)", 7.0, "#UCO310770001", 30_000.0),
    ("Subasta [#UCO310770001] 30.000,00@7% (ARS Inm)", 7.0, "#UCO310770001", 30_000.0),
    ("Compra [#UAC140770002] 100.000,00@6% (ARS 24hs)", 6.0, "#UAC140770002", 100_000.0),
    ("Compra [*ACI120300125] 27.000.000,00@39,5% (ARS 24hs)", 39.5, "*ACI120300125", 27_000_000.0),
    ("Compra [*ARP110400156] 5.000.000,00@29,5% (ARS 24hs)", 29.5, "*ARP110400156", 5_000_000.0),
    ("Compra [*ARP121200166] 10.000.000,00@36% (ARS 24hs)", 36.0, "*ARP121200166", 10_000_000.0),
    ("Compra [*ACI250300289] 35.545.924,29@28% (ARS 24hs)", 28.0, "*ACI250300289", 35_545_924.29),
]


@pytest.mark.parametrize(("texto", "tasa", "cod", "nominal"), REALES)
def test_parse_cadenas_reales(texto, tasa, cod, nominal):
    r = parse_informacion(texto)
    assert r["tasa_pct"] == tasa
    assert r["cod_instrumento"] == cod
    assert r["nominal_info"] == nominal


@pytest.mark.parametrize("vacio", [None, "", "   ", "texto sin el formato conocido"])
def test_sin_formato_devuelve_none(vacio):
    """None, NO una excepción ni un 0: el caller distingue 'no hay tasa' de 'tasa 0'."""
    assert parse_informacion(vacio)["tasa_pct"] is None


def test_tasa_en_porcentaje_no_en_tanto_por_uno():
    """'@6%' → 6.0, no 0.06. La columna `operaciones.tasa` guarda el % tal cual."""
    assert parse_informacion("Compra [#X] 100,00@6% (ARS)")["tasa_pct"] == 6.0


def test_tasa_raw_conserva_el_token():
    """Para poder auditar el parseo desde el CSV sin volver a la base."""
    assert parse_informacion("Compra [#X] 100,00@39,5% (ARS)")["tasa_raw"] == "39,5"


class TestNumeroArgentino:
    def test_coma_decimal(self):
        assert num_ar("39,5") == 39.5

    def test_punto_de_miles_con_coma_decimal(self):
        assert num_ar("27.000.000,00") == 27_000_000.0

    def test_entero_pelado(self):
        assert num_ar("6") == 6.0

    def test_punto_decimal_sin_coma(self):
        """'39.5' (1 punto, 1 decimal) se lee como 39,5 — no como 395."""
        assert num_ar("39.5") == 39.5

    def test_puntos_de_miles_sin_coma(self):
        """'1.000' son mil, no uno coma cero."""
        assert num_ar("1.000") == 1000.0

    @pytest.mark.parametrize("basura", [None, "", "abc", "--"])
    def test_basura_devuelve_none(self, basura):
        assert num_ar(basura) is None


class TestResolucionPorBoleto:
    """`jobs.ops_tasa_mav._resolver`: una tasa por boleto, o ninguna."""

    def _resolver(self, rows):
        from jobs.ops_tasa_mav import _resolver
        return _resolver(rows)

    def test_un_movimiento_una_tasa(self):
        u, sin, amb = self._resolver([{"boleto": "A", "infos": ["Compra [#X] 100,00@6% (ARS)"]}])
        assert u == [(6.0, "A")]
        assert (sin, amb) == (0, 0)

    def test_varios_movimientos_misma_tasa_se_resuelve(self):
        u, sin, amb = self._resolver([{"boleto": "B", "infos": [
            "Compra [#X] 100,00@6% (ARS)", "Venta [#X] 100,00@6% (ARS)"]}])
        assert u == [(6.0, "B")]
        assert (sin, amb) == (0, 0)

    def test_tasas_distintas_NO_se_adivina(self):
        """Con dos tasas distintas se deja NULL a propósito: escribir una

        inventada en la base es peor que dejar el dato vacío.
        """
        u, _sin, amb = self._resolver([{"boleto": "C", "infos": [
            "Compra [#X] 100,00@6% (ARS)", "Venta [#X] 100,00@9% (ARS)"]}])
        assert u == []
        assert amb == 1

    @pytest.mark.parametrize("infos", [None, [], ["sin formato"]])
    def test_sin_tasa_parseable_se_cuenta_aparte(self, infos):
        u, sin, amb = self._resolver([{"boleto": "D", "infos": infos}])
        assert u == []
        assert (sin, amb) == (1, 0)
