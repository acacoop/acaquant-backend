"""Tests de los filtros del universo del fit (sin Mongo)."""
from __future__ import annotations

from datetime import date

from jobs.fair_value import _en_universo_fit

HOY = date(2026, 4, 27)
VOL_MIN = 50_000_000


def _bono(**overrides):
    """Bono base que pasa todos los filtros para tasa_fija."""
    base = {
        "tea": 0.27,
        "duration": 0.5,
        "fecha_vencimiento": "2027-01-15",      # 263 días al vto
        "fecha_emision": "2026-01-01",          # 116 días desde emisión
        "total_nominals_dia": 5_000_000_000,
        "is_zero_coupon": True,
    }
    base.update(overrides)
    return base


def test_universo_pasa_caso_normal():
    assert _en_universo_fit(_bono(), "tasa_fija", HOY, VOL_MIN) is True
    assert _en_universo_fit(_bono(), "cer", HOY, VOL_MIN) is True


def test_universo_excluye_dias_al_vto_corto():
    """Bono que vence en <15 días no entra al fit."""
    bono = _bono(fecha_vencimiento="2026-05-05")  # 8 días
    assert _en_universo_fit(bono, "tasa_fija", HOY, VOL_MIN) is False


def test_universo_excluye_volumen_bajo():
    bono = _bono(total_nominals_dia=10_000_000)  # 10M < 50M
    assert _en_universo_fit(bono, "tasa_fija", HOY, VOL_MIN) is False


def test_universo_excluye_tea_o_duration_null():
    assert _en_universo_fit(_bono(tea=None), "tasa_fija", HOY, VOL_MIN) is False
    assert _en_universo_fit(_bono(duration=None), "tasa_fija", HOY, VOL_MIN) is False
    assert _en_universo_fit(_bono(duration=0), "tasa_fija", HOY, VOL_MIN) is False


def test_universo_excluye_recien_emitido():
    """Bono emitido hace <5 días tiene microestructura ruidosa, no entra."""
    bono = _bono(fecha_emision="2026-04-25")  # 2 días desde emisión
    assert _en_universo_fit(bono, "tasa_fija", HOY, VOL_MIN) is False


def test_universo_cer_excluye_con_cupon():
    """En CER: bonos con cupón (TX26, TX28, DICP, PARP) NO entran al fit."""
    bono = _bono(is_zero_coupon=False)
    assert _en_universo_fit(bono, "cer", HOY, VOL_MIN) is False
    # En tasa_fija el flag is_zero_coupon no aplica → sigue pasando.
    assert _en_universo_fit(bono, "tasa_fija", HOY, VOL_MIN) is True


def test_universo_fecha_emision_null_no_filtra():
    """Si fecha_emision es None (lecaps sin emisión registrada), no se aplica
    el filtro — asume OK."""
    bono = _bono(fecha_emision=None)
    assert _en_universo_fit(bono, "tasa_fija", HOY, VOL_MIN) is True
