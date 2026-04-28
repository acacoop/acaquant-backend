"""Tests del fit cuadrático puro (sin Mongo)."""
from __future__ import annotations

from quant.curve_fit import fit_quadratic


def test_fit_quadratic_perfecto():
    """Si los puntos están exactamente sobre y = 1 + 2x + 3x², R² = 1."""
    durations = [0.0, 0.5, 1.0, 1.5, 2.0]
    teas = [1.0 + 2 * d + 3 * d * d for d in durations]
    fit = fit_quadratic(durations, teas)
    assert fit is not None
    assert abs(fit.beta0 - 1.0) < 1e-9
    assert abs(fit.beta1 - 2.0) < 1e-9
    assert abs(fit.beta2 - 3.0) < 1e-9
    assert abs(fit.r2 - 1.0) < 1e-9
    assert fit.n == 5


def test_fit_quadratic_predict():
    fit = fit_quadratic(
        [0.0, 1.0, 2.0],
        [1.0, 6.0, 17.0],  # 1 + 2x + 3x² evaluado
    )
    assert fit is not None
    assert abs(fit.predict(0.5) - (1.0 + 2 * 0.5 + 3 * 0.25)) < 1e-9


def test_fit_quadratic_menos_de_3_puntos():
    assert fit_quadratic([0.5, 1.0], [0.27, 0.28]) is None
    assert fit_quadratic([], []) is None


def test_fit_quadratic_r2_en_rango():
    """R² debe estar siempre en [0, 1] (no negativo) para datos no patológicos."""
    durations = [0.05, 0.1, 0.3, 0.5, 1.0, 1.5]
    teas = [0.25, 0.26, 0.27, 0.28, 0.29, 0.28]
    fit = fit_quadratic(durations, teas)
    assert fit is not None
    assert 0.0 <= fit.r2 <= 1.0


def test_fit_quadratic_datos_reales_tasa_fija():
    """Sanity con datos reales tasa fija 2026-04-27 (12 bonos del MCP)."""
    durations = [0.0493, 0.0877, 0.1753, 0.2219, 0.2603, 0.3452, 0.5096, 0.5945, 0.7205, 1.0082, 1.0932, 1.1753]
    teas = [0.249252, 0.248448, 0.275169, 0.269713, 0.272182, 0.27792, 0.269272, 0.272721, 0.284647, 0.285441, 0.284742, 0.282242]
    fit = fit_quadratic(durations, teas)
    assert fit is not None
    # OLS sanity: media de residuos del universo ≈ 0 (con intercepto, por construcción).
    residuos_bps = [(t - fit.predict(d)) * 10000 for d, t in zip(durations, teas, strict=True)]
    media = sum(residuos_bps) / len(residuos_bps)
    assert abs(media) < 2.0, f"media residuos = {media:.4f} bps (esperado <2)"
    # R² razonable: la tasa fija real tiene escalón 24-27%, R²≈0.72.
    assert fit.r2 > 0.5


def test_fit_quadratic_singular():
    """3 puntos colineales con misma duration → matriz singular."""
    fit = fit_quadratic([1.0, 1.0, 1.0], [0.5, 0.5, 0.5])
    assert fit is None
