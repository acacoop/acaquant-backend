"""Tests del parseo de flujos por tipo de bono (api/services/bonos_admin.py).

Congela el mapeo de shapes que alimenta el "pegar Excel" del form de bonos
(Manager → TÍTULOS → Bonos), agregado 2026-07-17. La regla es data-crítica: una
shape mal mapeada crea un bono roto en Curvas. Ver docs raíz "mercado.curvas —
shape de flujos".
"""
from __future__ import annotations

from api.services import bonos_admin

# formato SIMPLE: fecha \t amortización \t interés \t residual
_TXT = "2026-01-15\t50\t2.5\t100\n2026-07-15\t50\t1.25\t50"


def test_soberano_mapea_a_pct():
    """Soberano usa shape percentual: en 'c/100 vn' la amortización YA es el % y
    el interés YA es el cupón sobre residual resuelto → sólo se renombra."""
    r = bonos_admin.parse_flujos_bono(_TXT, "soberano")
    assert r["vencimiento"] == "2026-07-15"
    assert r["flujos"] == [
        {"fecha": "2026-01-15", "amortizacion_pct": 50.0, "cupon_sobre_residual": 2.5},
        {"fecha": "2026-07-15", "amortizacion_pct": 50.0, "cupon_sobre_residual": 1.25},
    ]


def test_tasa_fija_mapea_a_absoluto():
    """Tasa fija c/cupón (tipo 'bono') usa shape absoluto → directo del parser."""
    r = bonos_admin.parse_flujos_bono(_TXT, "bono")
    assert r["flujos"] == [
        {"fecha": "2026-01-15", "amortizacion": 50.0, "interes": 2.5},
        {"fecha": "2026-07-15", "amortizacion": 50.0, "interes": 1.25},
    ]


def test_dual_solo_amortizacion_pct():
    r = bonos_admin.parse_flujos_bono(_TXT, "dual")
    assert all(set(f) == {"fecha", "amortizacion_pct"} for f in r["flujos"])


def test_texto_vacio_no_rompe():
    r = bonos_admin.parse_flujos_bono("", "soberano")
    assert r["flujos"] == [] and r["formato"] == "vacio"
