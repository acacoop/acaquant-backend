"""`core/clase_activo.py` — PURO: sin base, sin red. Doc: `docs/AGENT.md` §0.ei."""
from __future__ import annotations

from core import clase_activo as ca


def test_de_derivado_reconoce_call_y_put_con_o_sin_otc():
    assert ca.de_derivado("DERIVADOS", "[OTC - SOJ.ROS/NOV26 380 C]", "") == ca.CALL
    assert ca.de_derivado("DERIVADOS", "[SOJ.ROS/MAY27 340 P]", "") == ca.PUT
    # Un futuro sin C/P: no hay letra que diga qué es, no se propone nada.
    assert ca.de_derivado("DERIVADOS", "[SOJ.ROS/MAY27]", "") == ""
    # Otra cartera: la regla es SOLO de DERIVADOS.
    assert ca.de_derivado("FCI", "[SOJ.ROS/MAY27 340 P]", "") == ""


def test_de_derivado_prueba_el_ticker_si_la_unidad_no_matchea():
    assert ca.de_derivado("DERIVADOS", "[1024] algo raro", "SOJ.ROS/MAY27 340 C") == ca.CALL


def test_de_fci_segun_subyacente_y_moneda():
    assert ca.de_fci("Mercado de Dinero", "USD") == "MM USD"
    assert ca.de_fci("Renta Fija", "ARS") == "ARS T1"
    assert ca.de_fci("Renta Fija", "USD") == "HD T1"
    assert ca.de_fci("Renta Variable", "ARS") == ca.RENTA_VARIABLE
    # Renta Mixta y cualquier otro subyacente: no se propone.
    assert ca.de_fci("Renta Mixta", "ARS") == ""
    assert ca.de_fci("Lo que sea", "ARS") == ""
    # Moneda distinta de ARS/USD: no se propone, aunque el subyacente sea válido.
    assert ca.de_fci("Mercado de Dinero", "EUR") == ""
    assert ca.de_fci("Renta Variable", "EUR") == ""


def test_normalizar_nombre():
    assert ca.normalizar_nombre("  Sbs   Pesos  Plus - Clase A  ") == \
        "SBS PESOS PLUS - CLASE A"
    assert ca.normalizar_nombre("Ciclo Nóva Ahórro") == "CICLO NOVA AHORRO"
