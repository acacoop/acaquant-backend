"""Tests de quant/xirr — validado contra TIR.NO.PER de Excel."""
from __future__ import annotations

from datetime import date

import pytest

from quant.xirr import xirr


# ─── Caso canónico Excel ─────────────────────────────────────────────
#
# =TIR.NO.PER({-10000, 2750, 4250, 3250, 2750}, {01/01/08, 01/03/08,
#              30/10/08, 15/02/09, 01/04/09})
#   → 0.373362535 (37.34%)
#
# Fuente: ejemplo oficial de Microsoft.

def test_caso_canonico_excel():
    cf = [
        (date(2008, 1,  1), -10000),
        (date(2008, 3,  1),   2750),
        (date(2008, 10, 30),  4250),
        (date(2009, 2, 15),   3250),
        (date(2009, 4,  1),   2750),
    ]
    r = xirr(cf)
    assert r is not None
    assert r == pytest.approx(0.373362535, rel=1e-4)


# ─── Caso simple: 1 año exacto, sin flujos intermedios ─────────────────

def test_un_anio_exacto_sin_flujos():
    """Si invierto 100 y al año retiro 110, TEA = 10%."""
    cf = [
        (date(2025, 1, 1), -100),
        (date(2026, 1, 1),  110),
    ]
    r = xirr(cf)
    assert r is not None
    assert r == pytest.approx(0.10, abs=1e-6)


# ─── Caso "valuación de portfolio sin flujos" ──────────────────────────
#
# Convención del proyecto: valor inicio +, valor cierre -.
# Si arranco el mes con $100 y termino con $105, la TEA del mes es:
#   (105/100)^(365/días_mes) - 1
# En un mes de 31 días: (1.05)^(365/31) - 1 ≈ 0.7917 (79.17%)

def test_portfolio_sin_flujos_un_mes():
    cf = [
        (date(2026, 1,  1), +100.0),   # cierre mes anterior (valor inicio)
        (date(2026, 1, 31), -105.0),   # cierre mes (valor final, signo negativo)
    ]
    r = xirr(cf)
    assert r is not None
    # (1.05)^(365/30) - 1 ≈ 0.7917  (30 días entre 01/01 y 31/01)
    esperado = (105 / 100) ** (365 / 30) - 1
    assert r == pytest.approx(esperado, rel=1e-4)


# ─── Caso con flujos intermedios ──────────────────────────────────────
#
# Convención del proyecto: valor inicio +, flujos externos con signo
# natural (deposito +, extracción -), valor cierre -.

def test_portfolio_con_flujos_intermedios():
    cf = [
        (date(2026, 4, 30), +27_810_000),   # cierre Abr
        (date(2026, 5,  4),     +45_000),   # depósito
        (date(2026, 5,  4),    +166_300),   # depósito
        (date(2026, 5,  7),    -225_000),   # extracción
        (date(2026, 5, 11),    -163_500),   # extracción
        (date(2026, 5, 13),     -44_300),   # extracción
        (date(2026, 5, 13),    -100_000),   # extracción
        (date(2026, 5, 31), -28_345_800),   # cierre May (con +535.8K vs valor sin rendim.)
    ]
    r = xirr(cf)
    # Solo chequeamos que converge a algo razonable. La TEA reportada
    # por el frontend para este mes era ~+26%, XIRR debería dar
    # parecido aunque no idéntico (XIRR es geom., Modified Dietz lineal).
    assert r is not None
    assert -1.0 < r < 5.0   # entre -100% y +500% TEA


# ─── Edge cases ────────────────────────────────────────────────────────

def test_menos_de_dos_flujos_no_nulos():
    assert xirr([]) is None
    assert xirr([(date(2026, 1, 1), 100)]) is None
    assert xirr([(date(2026, 1, 1), 0), (date(2026, 2, 1), 0)]) is None


def test_todos_mismo_signo():
    """Sin cambio de signo no hay raíz del NPV."""
    cf = [
        (date(2026, 1, 1), 100),
        (date(2026, 6, 1), 200),
        (date(2026, 12, 1), 50),
    ]
    assert xirr(cf) is None


def test_invariante_swap_global_de_signos():
    """xirr([+a, -b]) == xirr([-a, +b]) — cambiar todos los signos a la
    vez no cambia la tasa."""
    cf_a = [
        (date(2025, 1, 1), -1000),
        (date(2025, 7, 1),    300),
        (date(2026, 1, 1),    800),
    ]
    cf_b = [(d, -m) for d, m in cf_a]
    r_a = xirr(cf_a)
    r_b = xirr(cf_b)
    assert r_a is not None and r_b is not None
    assert r_a == pytest.approx(r_b, abs=1e-6)


def test_orden_de_input_no_importa():
    """xirr es invariante al orden de los cashflows."""
    cf = [
        (date(2025, 1,  1), -1000),
        (date(2025, 7,  1),   300),
        (date(2026, 1,  1),   800),
    ]
    r1 = xirr(cf)
    r2 = xirr(list(reversed(cf)))
    assert r1 == pytest.approx(r2, abs=1e-9)


# ─── Cuenta argentina con TEA muy alta (regression test) ──────────────
#
# Cuenta arranca el mes con $100, termina con $150. Sin flujos, eso es
# (1.5)^12 - 1 ≈ 12676% TEA. Este caso ANTES no convergía porque el
# rango original era TEA ≤ 1000%. Ahora con _RATE_MAX = 10000 y guess
# decente debe converger sí o sí.

def test_tea_extrema_un_mes():
    cf = [
        (date(2025, 1,  1), +100.0),    # V_inicio
        (date(2025, 1, 31), -150.0),    # V_cierre +50% en 30 días
    ]
    guess = (150 / 100) ** (365 / 30) - 1   # ~126.7
    r = xirr(cf, guess=guess)
    assert r is not None
    # TEA real ≈ 12676% — confirmamos que está en el ballpark
    esperado = (150 / 100) ** (365 / 30) - 1
    assert r == pytest.approx(esperado, rel=1e-3)


def test_tea_extrema_con_flujos_netos_cero():
    """Cuenta con flujos que se cancelan entre sí: misma TEA que el caso
    sin flujos. Valida que XIRR maneja flujos grandes mid-month."""
    cf = [
        (date(2025, 1,  1), +100.0),     # V_inicio
        (date(2025, 1, 10),  +50.0),     # depósito
        (date(2025, 1, 20),  -50.0),     # extracción que cancela
        (date(2025, 1, 31), -150.0),     # V_cierre
    ]
    guess = (150 / 100) ** (365 / 30) - 1
    r = xirr(cf, guess=guess)
    assert r is not None
    # Con flujos netos cero la TEA debería ser similar al caso sin flujos.
    assert 50 < r < 200    # ballpark amplio — basta con que converja


def test_tea_negativa_extrema():
    """Cuenta que pierde 70% en un mes: TEA muy negativa pero finita.
    Antes el bisect cortaba en -99.99% lo cual igual cubre, pero
    Newton con damping debería llegar antes."""
    cf = [
        (date(2025, 1,  1), +100.0),
        (date(2025, 1, 31),  -30.0),    # perdió 70% en 30 días
    ]
    guess = (30 / 100) ** (365 / 30) - 1
    r = xirr(cf, guess=guess)
    assert r is not None
    # TEA = (0.3)^(365/30) - 1 ≈ -0.99999998
    assert -1.0 < r < -0.99
