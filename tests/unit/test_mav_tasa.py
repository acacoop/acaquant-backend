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
    # TASAS NEGATIVAS — 132 boletos en prod. El signo NO se puede perder:
    # guardar 0,5 donde va -0,5 es un error que no se nota.
    ("Compra [#UMV131230011] 500.000,00@-0,5% (ARS Inm)", -0.5, "#UMV131230011", 500_000.0),
    ("Subasta [#UMV201230037] 500.000,00@-0,5% (ARS 24hs)", -0.5, "#UMV201230037", 500_000.0),
    ("Compra [#UMV311030030] 500.000,00@-0,25% (ARS 24hs)", -0.25, "#UMV311030030", 500_000.0),
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


def test_tasa_negativa_conserva_el_signo():
    """Regresión: la primera versión del regex no aceptaba '-' y estos 132

    boletos quedaban sin tasa. Peor sería haberlos tomado como positivos.
    """
    r = parse_informacion("Compra [#UMV131230011] 500.000,00@-0,5% (ARS Inm)")
    assert r["tasa_pct"] == -0.5
    assert r["tasa_raw"] == "-0,5"


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
        r = self._resolver([{"boleto": "A", "infos": ["Compra [#X] 100,00@6% (ARS)"]}])
        assert r["updates"] == [(6.0, "A")]
        assert (r["sin_texto"], r["formato_desconocido"], r["ambiguos"]) == (0, 0, 0)

    def test_varios_movimientos_misma_tasa_se_resuelve(self):
        r = self._resolver([{"boleto": "B", "infos": [
            "Compra [#X] 100,00@6% (ARS)", "Venta [#X] 100,00@6% (ARS)"]}])
        assert r["updates"] == [(6.0, "B")]
        assert r["ambiguos"] == 0

    def test_tasas_distintas_NO_se_adivina(self):
        """Con dos tasas distintas se deja NULL a propósito: escribir una

        inventada en la base es peor que dejar el dato vacío.
        """
        r = self._resolver([{"boleto": "C", "infos": [
            "Compra [#X] 100,00@6% (ARS)", "Venta [#X] 100,00@9% (ARS)"]}])
        assert r["updates"] == []
        assert r["ambiguos"] == 1

    @pytest.mark.parametrize("infos", [None, [], [None], [""]])
    def test_sin_informacion_se_cuenta_como_sin_texto(self, infos):
        """Falta el dato en origen — distinto de 'formato nuevo'."""
        r = self._resolver([{"boleto": "D", "infos": infos}])
        assert r["updates"] == []
        assert (r["sin_texto"], r["formato_desconocido"]) == (1, 0)

    def test_texto_sin_tasa_es_formato_desconocido_y_deja_muestra(self):
        """Hay texto pero no matchea: hay que extender el parseo. La muestra va

        al log del cron para poder verlo sin entrar a la base.
        """
        r = self._resolver([{"boleto": "E", "infos": ["Canje [#X] 100,00 (ARS)"]}])
        assert r["updates"] == []
        assert (r["sin_texto"], r["formato_desconocido"]) == (0, 1)
        assert r["muestras"] == ["Canje [#X] 100,00 (ARS)"]
