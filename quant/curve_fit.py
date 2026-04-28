"""curve_fit.py — Ajuste de curva por mínimos cuadrados.

Cuadrática OLS sobre TEA vs duration:

    TEA(d) = β₀ + β₁·d + β₂·d²

3 parámetros, lineal en β. Solución cerrada vía pseudo-inversa de la matriz
de diseño X = [1, d, d²]. Sin scipy. Estable con 7-15 puntos.

Por qué cuadrática y no log o Nelson-Siegel:
  - Log fit (a·ln(d) + b) no captura humps (la curva tasa fija al 27-abr-26
    tiene hump entre 12-14m — un fit estrictamente monótono lo aplana).
  - Nelson-Siegel sobre 7-15 bonos sobre-ajusta y τ se vuelve inestable día
    a día (ruido del optimizador domina los residuos).
  - Cuadrática es el sweet spot: 3 grados de libertad, captura curvatura,
    OLS cerrado, robusto a pocos puntos.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuadraticFit:
    """Resultado del fit cuadrático.

    `r2` es coeficient of determination en [0, 1]. Si todos los puntos caen
    sobre la curva → r2 = 1; si la curva no explica nada de la variabilidad
    de y → r2 = 0. Negativo solo si el modelo es peor que la media (no debería
    pasar con OLS + intercepto, sirve como canario).
    """

    beta0: float
    beta1: float
    beta2: float
    r2: float
    n: int

    def predict(self, duration: float) -> float:
        return self.beta0 + self.beta1 * duration + self.beta2 * duration * duration


def fit_quadratic(durations: list[float], teas: list[float]) -> QuadraticFit | None:
    """Ajusta TEA = β₀ + β₁·d + β₂·d² por OLS.

    Devuelve None si:
      - len < 3 (cuadrática necesita ≥3 puntos para estar bien definida)
      - matriz XᵀX singular (todos los puntos en la misma duration, etc.)

    Las listas deben tener la misma longitud. Se asume sin nulos (filtra el
    caller).
    """
    n = len(durations)
    if n != len(teas):
        raise ValueError("durations y teas deben tener la misma longitud")
    if n < 3:
        return None

    # X = [[1, d_i, d_i²]], y = [tea_i]
    # β = (XᵀX)⁻¹ Xᵀy resuelto manualmente sobre la matriz 3×3.
    s0 = float(n)
    s1 = 0.0  # Σ d
    s2 = 0.0  # Σ d²
    s3 = 0.0  # Σ d³
    s4 = 0.0  # Σ d⁴
    sy = 0.0  # Σ y
    sdy = 0.0  # Σ d·y
    sd2y = 0.0  # Σ d²·y

    for d, y in zip(durations, teas, strict=True):
        d2 = d * d
        d3 = d2 * d
        d4 = d2 * d2
        s1 += d
        s2 += d2
        s3 += d3
        s4 += d4
        sy += y
        sdy += d * y
        sd2y += d2 * y

    # Matriz XᵀX:
    #   [s0  s1  s2]
    #   [s1  s2  s3]
    #   [s2  s3  s4]
    # Resolver A·β = b con Cramer (3×3 — barato y exacto).
    a00, a01, a02 = s0, s1, s2
    a10, a11, a12 = s1, s2, s3
    a20, a21, a22 = s2, s3, s4
    b0, b1, b2 = sy, sdy, sd2y

    def det3(
        m00: float, m01: float, m02: float,
        m10: float, m11: float, m12: float,
        m20: float, m21: float, m22: float,
    ) -> float:
        return (
            m00 * (m11 * m22 - m12 * m21)
            - m01 * (m10 * m22 - m12 * m20)
            + m02 * (m10 * m21 - m11 * m20)
        )

    det_a = det3(a00, a01, a02, a10, a11, a12, a20, a21, a22)
    if abs(det_a) < 1e-18:
        return None

    det_b0 = det3(b0, a01, a02, b1, a11, a12, b2, a21, a22)
    det_b1 = det3(a00, b0, a02, a10, b1, a12, a20, b2, a22)
    det_b2 = det3(a00, a01, b0, a10, a11, b1, a20, a21, b2)

    beta0 = det_b0 / det_a
    beta1 = det_b1 / det_a
    beta2 = det_b2 / det_a

    # R² = 1 − SS_res / SS_tot
    y_mean = sy / s0
    ss_tot = 0.0
    ss_res = 0.0
    for d, y in zip(durations, teas, strict=True):
        y_hat = beta0 + beta1 * d + beta2 * d * d
        ss_res += (y - y_hat) ** 2
        ss_tot += (y - y_mean) ** 2

    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-18 else 0.0

    return QuadraticFit(
        beta0=beta0, beta1=beta1, beta2=beta2, r2=r2, n=n,
    )
