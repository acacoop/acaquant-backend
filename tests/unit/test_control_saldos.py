"""Reglas del control de saldos: signo, suma de filas repetidas y whitelist.

Las tres cosas que decide `parsear` son las que pueden dar vuelta un descubierto:
si el signo se aplica al revés, una cuenta a favor aparece en rojo; si las filas
repetidas de la misma moneda no se suman, el saldo queda a medias; y si el filtro
de monedas se afloja, entran títulos a un tablero que es de efectivo.
"""
from __future__ import annotations

from jobs.control_saldos import MONEDAS, SIGNO, UMBRALES, _nombre_cuenta, parsear


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
    regs, _ = parsear([_fila("ARS", 1_000_000.0)], "805", "TEST")
    assert _por_ticker(regs)["ARS"]["cantidad"] == -1_000_000.0


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


def test_las_cuatro_monedas_del_negocio_se_persisten():
    """ARS/USD/USDL/USDC. USDC (dólar cable) se sumó el 2026-08-14 por pedido del
    back office: antes se veía y se descartaba, y el saldo cable no se controlaba."""
    assert set(MONEDAS) == {"ARS", "USD", "USDL", "USDC"}
    regs, descartadas = parsear(
        [_fila("ARS", -100_000.0), _fila("USDC", -0.52)], "805", "TEST")
    assert set(_por_ticker(regs)) == {"ARS", "USDC"}
    assert _por_ticker(regs)["USDC"]["cantidad"] == 0.52
    assert descartadas == {}


def test_una_moneda_fuera_de_la_whitelist_no_se_persiste_pero_se_cuenta():
    """El filtro sigue siendo una whitelist: una moneda nueva NO entra sola al
    tablero, pero queda contada — de ahí sale el dato para decidir si sumarla."""
    regs, descartadas = parsear(
        [_fila("ARS", -100_000.0), _fila("EUR", -0.52)], "805", "TEST")
    assert set(_por_ticker(regs)) == {"ARS"}
    assert descartadas == {"EUR": 1}


def test_los_titulos_no_entran_ni_se_cuentan_como_moneda_descartada():
    """Un bono no es candidato a este tablero: si se contara, taparía la señal de
    qué monedas nos estamos perdiendo."""
    regs, descartadas = parsear(
        [_fila("9422", -6_128.0, tipo="Títulos Públicos"), _fila("ARS", -100_000.0)],
        "805", "TEST")
    assert set(_por_ticker(regs)) == {"ARS"}
    assert descartadas == {}


def test_el_saldo_en_cero_no_se_persiste():
    """La ausencia de fila ES el cero. Y como cada cuenta se reescribe entera, una
    que pasa de negativa a cero pierde su fila y sale del control."""
    regs, _ = parsear([_fila("ARS", 0.0), _fila("USD", 0.0, 500.0)], "805", "TEST")
    assert regs == []


def test_el_umbral_de_ruido_corta_las_DOS_puntas():
    """|saldo| por debajo del umbral no se persiste, sea a favor o en rojo.

    El caso que lo motivó: un descubierto de -10.000 pesos no es un problema que
    haya que perseguir, y cien filas así tapan las tres que sí importan.
    """
    assert UMBRALES["ARS"] == 15_000.0
    # Aunesa manda al revés, así que estos crudos son -10.000 / +12.000 / -80.000
    # una vez corregido el signo.
    regs, _ = parsear(
        [_fila("ARS", 10_000.0), _fila("USD", -3.0)], "805", "TEST")
    assert _por_ticker(regs).get("ARS") is None, "un -10.000 ARS no entra"
    assert _por_ticker(regs)["USD"]["cantidad"] == 3.0, "USD todavía no tiene umbral"

    regs, _ = parsear([_fila("ARS", -12_000.0)], "805", "TEST")
    assert regs == [], "un +12.000 ARS tampoco: el corte es por valor absoluto"

    regs, _ = parsear([_fila("ARS", 80_000.0)], "805", "TEST")
    assert _por_ticker(regs)["ARS"]["cantidad"] == -80_000.0


def test_el_umbral_se_aplica_al_saldo_SUMADO_y_no_a_cada_fila():
    """Dos filas chicas del mismo signo que juntas pasan el umbral SÍ entran —
    y dos grandes que se cancelan entre sí, no. El umbral mira el saldo real."""
    regs, _ = parsear(
        [_fila("ARS", 9_000.0), _fila("ARS", 9_000.0)], "805", "TEST")
    assert _por_ticker(regs)["ARS"]["cantidad"] == -18_000.0

    regs, _ = parsear(
        [_fila("ARS", 500_000.0), _fila("ARS", -499_000.0)], "805", "TEST")
    assert regs == [], "el neto es -1.000: ruido, aunque las filas sean grandes"


def test_la_cuenta_se_arma_con_el_formato_de_siempre():
    regs, _ = parsear([_fila("ARS", -100_000.0)], "805", "MOLLO NICOLAS")
    assert regs[0]["cuenta"] == "[805] MOLLO NICOLAS"
    assert parsear([_fila("ARS", -100_000.0)], "805", "")[0][0]["cuenta"] == "[805]"


def test_el_nombre_de_cuenta_es_IDEMPOTENTE():
    """Volver a formatear un nombre ya formateado NO agrega otro prefijo.

    Incidente 2026-08-19: el ciclo de revisión leía la denominación de la tabla
    —donde ya estaba armada como "[105] LA SEGUNDA"— y se la pasaba al parser,
    que le ponía el prefijo otra vez. Cada pasada sumaba uno: en pantalla se
    llegó a ver "[21] [21] [21] …". El llamador ya se corrigió (la denominación
    sale del universo de Aunesa); esto es la red para que no vuelva a pasar.
    """
    assert _nombre_cuenta("805", "MOLLO NICOLAS") == "[805] MOLLO NICOLAS"
    assert _nombre_cuenta("105", "[105] LA SEGUNDA") == "[105] LA SEGUNDA"
    assert _nombre_cuenta("21", "[21] [21] [21] COOP") == "[21] COOP"
    assert _nombre_cuenta("805", "") == "[805]"
    # Y el invariante que lo define: aplicarlo N veces da lo mismo que una.
    una = _nombre_cuenta("1243", "BERDIÑAS, MARIANA")
    assert _nombre_cuenta("1243", una) == una
