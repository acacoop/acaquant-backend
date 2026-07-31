"""Candado del caso LEDE (2026-07-24): las letras a descuento (tipoTitulo
'LEDE' de Aunesa, ej. [9416] S13N6) cotizan en paridad → ÷100 en las TRES
copias de la regla. Sin esto el AuM las guardaba ×100 (incidente real:
$45.4MM de millones en vez de $454M en la cuenta 100).
"""
from __future__ import annotations

from api.services.pnl import _TIPOS_DIVISOR_100 as _lista_pnl
from api.services.pnl import _aplicar_normalizer
from jobs.aum import TIPOS_DIVISOR_100 as _lista_writer
from jobs.aum import _calcular_valuacion


def test_lede_en_las_listas_activas():
    assert "LEDE" in _lista_writer
    assert "LEDE" in _lista_pnl


def test_writer_divide_lede_por_100():
    # 445M nominales a paridad 102.145 → $454.5M (no $45.454MM de millones)
    val = _calcular_valuacion({"precio": 102.145, "cantidad": 445_000_000,
                               "tipoTitulo": "LEDE"})
    assert val == round(102.145 * 445_000_000 / 100, 6)


def test_normalizer_fallback_divide_lede():
    # Sin cartera clasificada, el fallback legacy por tipoTitulo debe dividir.
    assert _aplicar_normalizer(102.145, 1000, cartera=None, tipoTitulo="LEDE") == \
        102.145 * 1000 / 100
