"""Tests de funciones cuantitativas en engines/curvas.py.

Cubre xirr, macaulay_duration, montos de flujo (tasa fija + CER),
búsqueda de CER con fallback, y navegación de días hábiles.
"""

from datetime import date

import pytest

from engines.curvas import (
    convexity,
    fecha_flujo,
    get_cer_en_fecha,
    get_cer_liquidacion,
    macaulay_duration,
    monto_flujo,
    monto_flujo_cer,
    siguiente_dia_habil,
    xirr,
)

# ─────────────────────────────────────────────
# xirr
# ─────────────────────────────────────────────


def test_xirr_zero_coupon_1y():
    fechas = [date(2025, 1, 1), date(2026, 1, 1)]
    flujos = [-100.0, 110.0]
    r = xirr(fechas, flujos)
    assert r is not None
    assert abs(r - 0.10) < 1e-3


def test_xirr_zero_coupon_0_5y_40pct():
    fechas = [date(2025, 1, 1), date(2025, 7, 2)]
    flujos = [-100.0, 100.0 * (1.40) ** (182 / 365)]
    r = xirr(fechas, flujos)
    assert r is not None
    assert abs(r - 0.40) < 1e-3


def test_xirr_bono_con_cupones():
    fechas = [date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)]
    flujos = [-100.0, 5.0, 5.0, 105.0]
    r = xirr(fechas, flujos)
    assert r is not None
    assert abs(r - 0.05) < 1e-3


def test_xirr_retorna_none_si_no_converge():
    fechas = [date(2025, 1, 1), date(2026, 1, 1)]
    flujos = [100.0, 100.0]  # todos positivos → no hay IRR real
    assert xirr(fechas, flujos) is None


# ─────────────────────────────────────────────
# macaulay_duration
# ─────────────────────────────────────────────


def test_duration_zero_coupon_igual_a_maturity():
    fecha_base = date(2025, 1, 1)
    vto = date(2027, 1, 1)
    d = macaulay_duration([vto], [100.0], tir=0.10, fecha_base=fecha_base)
    assert d is not None
    assert abs(d - 2.0) < 1e-3


def test_duration_bono_cupon_menor_a_maturity():
    fecha_base = date(2025, 1, 1)
    fechas = [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)]
    montos = [5.0, 5.0, 105.0]
    d = macaulay_duration(fechas, montos, tir=0.05, fecha_base=fecha_base)
    assert d is not None
    assert 2.5 < d < 3.0


def test_duration_pv_cero_retorna_none():
    fecha_base = date(2025, 1, 1)
    fechas = [date(2026, 1, 1)]
    montos = [0.0]
    assert macaulay_duration(fechas, montos, tir=0.10, fecha_base=fecha_base) is None


# ─────────────────────────────────────────────
# convexity
# ─────────────────────────────────────────────


def test_convexity_zero_coupon_5y():
    """Bono cero cupón a 5 años, tasa 10%. Convexity analítica = t(t+1)/(1+y)² = 30/1.21 = 24.79."""
    fecha_base = date(2025, 1, 1)
    vto = date(2030, 1, 1)
    c = convexity([vto], [100.0], tir=0.10, fecha_base=fecha_base)
    assert c is not None
    # Tolerancia 0.5: rounding de días → años puede dar 4.997-5.003
    assert abs(c - 24.79) < 0.5


def test_convexity_no_negativa_para_bono_con_cupones():
    """Para flujos positivos, convexity siempre > 0."""
    fecha_base = date(2025, 1, 1)
    fechas = [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)]
    montos = [5.0, 5.0, 105.0]
    c = convexity(fechas, montos, tir=0.05, fecha_base=fecha_base)
    assert c is not None
    assert c > 0


def test_convexity_aumenta_con_plazo():
    """A igual estructura de cupón y tasa, bono más largo tiene mayor convexity."""
    fecha_base = date(2025, 1, 1)
    c_corto = convexity([date(2027, 1, 1)], [100.0], tir=0.10, fecha_base=fecha_base)
    c_largo = convexity([date(2035, 1, 1)], [100.0], tir=0.10, fecha_base=fecha_base)
    assert c_corto is not None and c_largo is not None
    assert c_largo > c_corto


def test_convexity_pv_cero_retorna_none():
    fecha_base = date(2025, 1, 1)
    assert convexity([date(2026, 1, 1)], [0.0], tir=0.10, fecha_base=fecha_base) is None


# ─────────────────────────────────────────────
# monto_flujo (tasa fija)
# ─────────────────────────────────────────────


def test_monto_flujo_con_campo_monto():
    assert monto_flujo({"monto": 123.45}) == 123.45


def test_monto_flujo_suma_amort_mas_interes():
    assert monto_flujo({"amortizacion": 100.0, "interes": 5.5}) == 105.5


def test_monto_flujo_campos_faltantes_retornan_cero():
    assert monto_flujo({}) == 0.0


# ─────────────────────────────────────────────
# monto_flujo_cer (porcentual)
# ─────────────────────────────────────────────


def test_monto_flujo_cer_solo_amort():
    # 50% de amortización sobre VN=100 = 50
    f = {"amortizacion_pct": 50.0, "cupon_sobre_residual": 0.0, "residual_previo_pct": 100.0}
    assert monto_flujo_cer(f, valor_nominal=100) == 50.0


def test_monto_flujo_cer_amort_mas_cupon_sobre_residual():
    # amort 10% + cupón 4% sobre 100% residual = 10 + 4 = 14
    f = {"amortizacion_pct": 10.0, "cupon_sobre_residual": 0.04, "residual_previo_pct": 100.0}
    assert monto_flujo_cer(f, valor_nominal=100) == pytest.approx(14.0)


def test_monto_flujo_cer_zero_coupon():
    # sin cupon_sobre_residual, usa cupon_anual × VN
    f = {"amortizacion_pct": 100.0, "cupon_anual": 0.0}
    assert monto_flujo_cer(f, valor_nominal=100) == 100.0


# ─────────────────────────────────────────────
# fecha_flujo
# ─────────────────────────────────────────────


def test_fecha_flujo_string_iso():
    assert fecha_flujo({"fecha": "2026-05-15"}) == date(2026, 5, 15)


def test_fecha_flujo_string_con_hora():
    assert fecha_flujo({"fecha": "2026-05-15T12:00:00"}) == date(2026, 5, 15)


def test_fecha_flujo_vacio():
    assert fecha_flujo({}) is None


# ─────────────────────────────────────────────
# get_cer_en_fecha (fallback hasta 7 días atrás)
# ─────────────────────────────────────────────


def test_get_cer_en_fecha_exacto():
    cer = {"2026-04-10": 100.5}
    assert get_cer_en_fecha(cer, date(2026, 4, 10)) == 100.5


def test_get_cer_en_fecha_fallback_3_dias():
    cer = {"2026-04-07": 98.0}
    assert get_cer_en_fecha(cer, date(2026, 4, 10)) == 98.0


def test_get_cer_en_fecha_no_encuentra_pasa_7_dias():
    cer = {"2026-04-01": 90.0}  # 10 días atrás
    assert get_cer_en_fecha(cer, date(2026, 4, 11)) is None


# ─────────────────────────────────────────────
# siguiente_dia_habil
# ─────────────────────────────────────────────


def test_siguiente_dia_habil_salto_weekend():
    dh = ["2026-04-10", "2026-04-13", "2026-04-14"]  # vie, lun, mar
    assert siguiente_dia_habil(dh, date(2026, 4, 10)) == "2026-04-13"


def test_siguiente_dia_habil_sin_futuros():
    dh = ["2026-04-10"]
    assert siguiente_dia_habil(dh, date(2026, 4, 15)) is None


# ─────────────────────────────────────────────
# get_cer_liquidacion (T − 10 días hábiles)
# ─────────────────────────────────────────────


def test_get_cer_liquidacion_retrocede_10_habiles():
    # 11 días hábiles consecutivos: settlement día 11 → CER día 1
    dh = [f"2026-04-{d:02d}" for d in range(1, 12)]
    cer = {"2026-04-01": 555.5}
    assert get_cer_liquidacion(cer, dh, "2026-04-11") == 555.5


def test_get_cer_liquidacion_no_alcanzan_10_habiles():
    dh = [f"2026-04-{d:02d}" for d in range(1, 6)]
    cer = {"2026-04-01": 100.0}
    assert get_cer_liquidacion(cer, dh, "2026-04-05") is None
