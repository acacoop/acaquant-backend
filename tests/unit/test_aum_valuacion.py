"""Golden tests de la fórmula de valuación AuM (api/services/portfolio.py).

Congela `_valuacion_api` (la regla documentada en CLAUDE.md):
  - Renta fija (Títulos, Letras, ONs, Fideicomisos, CPD) → cant × px / 100
  - FCI / clase OTROS → cant × px directo
Si alguien rompe esto, el AuM de carteras enteras queda mal.
(La regla de Futuros — (px+1)×cant — vive en jobs/aum.py, no acá.)
"""
from __future__ import annotations

from api.services.portfolio import _valuacion_api


def test_renta_fija_divide_por_cien():
    # Bono a paridad 98.5, 1000 nominales → 985.0
    assert _valuacion_api(1000, 98.5, "Cartera Pesos", "TITULOS") == 985.0


def test_clase_otros_es_precio_por_cantidad_directo():
    assert _valuacion_api(10, 250.0, "Cartera Pesos", "OTROS") == 2500.0


def test_fci_por_nombre_de_cartera_es_directo():
    # Si "FCI" está en el nombre de la cartera → P×Q directo (no /100).
    assert _valuacion_api(100, 1.23, "Cartera FCI", "TITULOS") == 123.0


def test_renta_fija_no_se_confunde_con_fci_en_otra_cartera():
    assert _valuacion_api(100, 100.0, "Cartera Dólares", "TITULOS") == 100.0
