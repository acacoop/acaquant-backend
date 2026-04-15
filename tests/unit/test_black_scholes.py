"""Tests de quant/black_scholes.py — pricing, Greeks, IV."""
import pytest

from quant.black_scholes import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vega,
    calc_extrinseco,
    calc_intrinseco,
    find_iv,
)


S, K, T, R, SIGMA = 100.0, 100.0, 1.0, 0.05, 0.30


# ─── Pricing ──────────────────────────────────────────────────────────

def test_put_call_parity_atm():
    """C − P = S − K·e^(−rT) para la misma S, K, T, r, σ."""
    import math
    c = bs_price(S, K, T, R, SIGMA, "CALL")
    p = bs_price(S, K, T, R, SIGMA, "PUT")
    expected = S - K * math.exp(-R * T)
    assert c - p == pytest.approx(expected, abs=1e-6)


def test_call_price_positivo_para_parametros_estandar():
    assert bs_price(S, K, T, R, SIGMA, "CALL") > 0
    assert bs_price(S, K, T, R, SIGMA, "PUT") > 0


@pytest.mark.parametrize("T_edge", [0.0, -0.1])
def test_precio_en_vencimiento_devuelve_intrinseco(T_edge):
    """T <= 0 → intrinsic value."""
    assert bs_price(110, 100, T_edge, R, SIGMA, "CALL") == pytest.approx(10.0)
    assert bs_price(90, 100, T_edge, R, SIGMA, "PUT") == pytest.approx(10.0)
    assert bs_price(90, 100, T_edge, R, SIGMA, "CALL") == pytest.approx(0.0)


def test_call_deep_itm_vale_cerca_del_intrinseco():
    """S >> K → call ≈ S − K·e^(−rT). Tolerancia ancha porque el forward a 1y importa."""
    c = bs_price(200, 100, T, R, SIGMA, "CALL")
    assert c > 100  # al menos el intrínseco


# ─── Greeks ────────────────────────────────────────────────────────────

def test_delta_call_itm_cerca_de_1():
    assert bs_delta(200, 100, T, R, SIGMA, "CALL") == pytest.approx(1.0, abs=0.01)


def test_delta_call_otm_cerca_de_0():
    assert bs_delta(50, 100, T, R, SIGMA, "CALL") == pytest.approx(0.0, abs=0.05)


def test_delta_put_itm_cerca_de_minus_1():
    assert bs_delta(50, 100, T, R, SIGMA, "PUT") == pytest.approx(-1.0, abs=0.05)


def test_delta_atm_aprox_0_5():
    """Call ATM delta ≈ 0.5 (en realidad un poco > 0.5 por el drift)."""
    d = bs_delta(S, K, T, R, SIGMA, "CALL")
    assert 0.5 < d < 0.7


def test_gamma_siempre_positivo():
    for S_ in [50, 100, 150]:
        assert bs_gamma(S_, K, T, R, SIGMA) > 0


def test_vega_siempre_positivo():
    for S_ in [50, 100, 150]:
        assert bs_vega(S_, K, T, R, SIGMA) > 0


def test_theta_call_tipicamente_negativo():
    """El call pierde valor con el paso del tiempo (carry aparte)."""
    assert bs_theta(S, K, T, R, SIGMA, "CALL") < 0


# ─── IV (round-trip) ────────────────────────────────────────────────────

@pytest.mark.parametrize("sigma_verdadero", [0.20, 0.30, 0.45, 0.60])
def test_find_iv_recupera_sigma_original(sigma_verdadero):
    """Precio sintético con σ conocida → find_iv debe recuperarla."""
    market_price = bs_price(S, K, T, R, sigma_verdadero, "CALL")
    iv = find_iv(market_price, S, K, T, R, "CALL")
    assert iv == pytest.approx(sigma_verdadero, abs=1e-3)


def test_find_iv_sobre_precio_cercano_al_intrinseco_devuelve_0():
    """Caso borde: precio < intrínseco + 0.01 → IV = 0 (no converge)."""
    # ITM: intrínseco = 10. Pasamos un precio ridículamente bajo.
    iv = find_iv(10.005, 110, 100, T, R, "CALL")
    assert iv == 0.0


# ─── Intrínseco / extrínseco ────────────────────────────────────────────

def test_calc_intrinseco_call():
    assert calc_intrinseco(110, 100, "CALL") == 10
    assert calc_intrinseco(90, 100, "CALL") == 0


def test_calc_intrinseco_put():
    assert calc_intrinseco(90, 100, "PUT") == 10
    assert calc_intrinseco(110, 100, "PUT") == 0


def test_calc_extrinseco_no_negativo():
    """Si precio < intrínseco (desarbitraje), extrínseco = 0."""
    assert calc_extrinseco(precio_opcion=5, intrinseco=10) == 0
    assert calc_extrinseco(precio_opcion=12, intrinseco=10) == 2
