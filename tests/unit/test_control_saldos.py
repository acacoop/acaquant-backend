"""Reglas del control de saldos: signo, suma de filas repetidas y whitelist.

Las tres cosas que decide `parsear` son las que pueden dar vuelta un descubierto:
si el signo se aplica al revés, una cuenta a favor aparece en rojo; si las filas
repetidas de la misma moneda no se suman, el saldo queda a medias; y si el filtro
de monedas se afloja, entran títulos a un tablero que es de efectivo.
"""
from __future__ import annotations

from jobs.control_saldos import MONEDAS, SIGNO, parsear


def _fila(especie: str, liq: float, pen: float = 0.0, tipo: str = "Moneda") -> dict:
    return {"especie": especie, "tipoTitulo": tipo, "cantidadLiquidada": liq,
            "cantidadPendienteLiquidar": pen, "estado": "DIS", "subCuenta": "GRAL"}


def _por_ticker(registros: list[dict]) -> dict[str, dict]:
    return {r["ticker"]: r for r in registros}


def test_el_signo_se_da_vuelta():
    """Aunesa manda -56.095,90 para una cuenta que tiene pesos A FAVOR."""
    regs, _ = parsear([_fila("ARS", -56_095.90)], "805", "TEST")
    assert _por_ticker(regs)["ARS"]["cantidad"] == 56_095.90


def test_un_descubierto_real_queda_negativo():
    regs, _ = parsear([_fila("ARS", 1_000.0)], "805", "TEST")
    assert _por_ticker(regs)["ARS"]["cantidad"] == -1_000.0


def test_filas_repetidas_de_la_misma_moneda_se_suman():
    """Caso real de la 805: dos filas de ARS con todos los campos de corte iguales.

    Sin saber qué las separa, la única lectura correcta de un saldo es la suma.
    `filas_origen` deja el rastro de cuántas entraron.
    """
    regs, _ = parsear(
        [_fila("ARS", 0.0, 345_082.90), _fila("ARS", -56_095.90, -5_159.78)],
        "805", "TEST")
    ars = _por_ticker(regs)["ARS"]
    assert ars["cantidad"] == 56_095.90
    assert ars["cantidad_pendiente"] == round(SIGNO * (345_082.90 - 5_159.78), 4)
    assert ars["filas_origen"] == 2


def test_solo_se_persisten_las_monedas_de_la_whitelist():
    """USDC apareció en la 805 y NO está en MONEDAS: no se persiste, pero se CUENTA
    — de ahí sale el dato para decidir si suma o no."""
    assert "USDC" not in MONEDAS
    regs, descartadas = parsear(
        [_fila("ARS", -100.0), _fila("USDC", -0.52)], "805", "TEST")
    assert set(_por_ticker(regs)) == {"ARS"}
    assert descartadas == {"USDC": 1}


def test_los_titulos_no_entran_ni_se_cuentan_como_moneda_descartada():
    """Un bono no es candidato a este tablero: si se contara, taparía la señal de
    qué monedas nos estamos perdiendo."""
    regs, descartadas = parsear(
        [_fila("9422", -6_128.0, tipo="Títulos Públicos"), _fila("ARS", -100.0)],
        "805", "TEST")
    assert set(_por_ticker(regs)) == {"ARS"}
    assert descartadas == {}


def test_el_saldo_en_cero_no_se_persiste():
    """La ausencia de fila ES el cero. Y como cada cuenta se reescribe entera, una
    que pasa de negativa a cero pierde su fila y sale del control."""
    regs, _ = parsear([_fila("ARS", 0.0), _fila("USD", 0.0, 500.0)], "805", "TEST")
    assert regs == []


def test_la_cuenta_se_arma_con_el_formato_de_siempre():
    regs, _ = parsear([_fila("ARS", -1.0)], "805", "MOLLO NICOLAS")
    assert regs[0]["cuenta"] == "[805] MOLLO NICOLAS"
    assert parsear([_fila("ARS", -1.0)], "805", "")[0][0]["cuenta"] == "[805]"
