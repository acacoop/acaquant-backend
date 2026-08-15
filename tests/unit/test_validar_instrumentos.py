"""Validación de símbolos guardados contra el universo real de Primary.

Lo que se congela acá es la parte que decide: qué se considera inexistente y a
qué se parece. La sugerencia importa tanto como el hallazgo — saber que un
símbolo no existe no arregla nada; saber cuál es el bueno sí.
"""
from __future__ import annotations

from datetime import date

from jobs.validar_instrumentos import (
    MOTIVO_JOB,
    decidir_vigencia,
    partir,
    revisar,
    sugerencias,
)

_UNIVERSO = {
    "MERV - XMEV - AL30 - 24hs",
    "MERV - XMEV - AL30 - CI",
    "MERV - XMEV - AL30D - 24hs",
    "MERV - XMEV - AL30C - 24hs",
    "MERV - XMEV - VSCWD - 24hs",
    "MERV - XMEV - GD30 - CI",
    "CICAOD/24hs",
}


def test_partir_reconoce_las_dos_formas_que_publica_primary():
    assert partir("MERV - XMEV - AL30D - 24hs") == ("AL30D", "24hs")
    assert partir("CICAOD/24hs") == ("CICAOD", "24hs")
    assert partir("cualquier cosa") is None


def test_la_pata_que_falta_es_la_primera_sugerencia():
    """El caso real del 2026-08-15: el master usaba VSCWO, que no existe."""
    assert sugerencias("MERV - XMEV - VSCWO - 24hs", _UNIVERSO)[0] \
        == "MERV - XMEV - VSCWD - 24hs"


def test_el_mismo_papel_en_el_otro_plazo_gana():
    """Caso benigno: el símbolo está bien escrito y sólo cotiza en CI."""
    assert sugerencias("MERV - XMEV - GD30 - 24hs", _UNIVERSO)[0] \
        == "MERV - XMEV - GD30 - CI"


def test_sugerir_no_devuelve_el_simbolo_consultado():
    assert "MERV - XMEV - AL30 - 24hs" not in sugerencias(
        "MERV - XMEV - AL30 - 24hs", _UNIVERSO)


def test_un_simbolo_sin_forma_conocida_no_inventa_sugerencias():
    assert sugerencias("ARS", _UNIVERSO) == []


def test_sin_parecido_no_se_fuerza_una_sugerencia():
    """Peor que no sugerir nada es sugerir cualquier cosa: la mesa la aplicaría."""
    assert sugerencias("MERV - XMEV - ZZZZ9 - 24hs", _UNIVERSO) == []


def test_revisar_solo_devuelve_los_que_no_existen():
    guardados = ["MERV - XMEV - AL30 - 24hs", "MERV - XMEV - VSCWO - 24hs"]
    out = revisar(guardados, _UNIVERSO)
    assert [h["simbolo"] for h in out] == ["MERV - XMEV - VSCWO - 24hs"]
    assert out[0]["sugerencias"]


def test_revisar_deduplica_el_mismo_simbolo_repetido():
    """El mismo símbolo en 40 assets es UN problema para arreglar, no 40."""
    assert len(revisar(["MERV - XMEV - VSCWO - 24hs"] * 40, _UNIVERSO)) == 1


# ── Vigencia — un título que amortizó no es un símbolo mal escrito ────────────

_HOY = date(2026, 8, 15)


def _asset(unidad, **kw):
    base = {"unidad": unidad, "vencimiento": None, "vigente": True,
            "vigencia_motivo": None, "fecha_vencimiento": None}
    return {**base, **kw}


def test_un_titulo_vencido_se_da_de_baja():
    cambios = decidir_vigencia([_asset("[1] AL30", vencimiento="2024-07-09")], _HOY)
    assert cambios == [{"unidad": "[1] AL30", "vigente": False, "motivo": MOTIVO_JOB}]


def test_el_vencimiento_del_master_sirve_si_el_catalogo_no_lo_tiene():
    cambios = decidir_vigencia(
        [_asset("[1] AL30", fecha_vencimiento=date(2024, 7, 9))], _HOY)
    assert cambios and cambios[0]["vigente"] is False


def test_el_que_todavia_no_vencio_no_se_toca():
    assert decidir_vigencia([_asset("[1] AL30", vencimiento="2030-07-09")], _HOY) == []


def test_sin_fecha_cargada_no_se_apaga_nada():
    """'No sé cuándo vence' no es 'venció'. Apagar por un dato faltante borraría
    de la vista títulos vivos."""
    assert decidir_vigencia([_asset("[1] ARS", vencimiento="")], _HOY) == []
    assert decidir_vigencia([_asset("[2] ARS", vencimiento="no aplica")], _HOY) == []


def test_no_pisa_la_marca_que_puso_un_humano():
    """Un rescate anticipado lo sabe la mesa y la fecha de vencimiento no."""
    fila = _asset("[1] AL30", vencimiento="2030-07-09", vigente=False,
                  vigencia_motivo="rescatado")
    assert decidir_vigencia([fila], _HOY) == []


def test_una_fecha_corregida_reactiva_el_titulo():
    """Reversible en las dos direcciones: si no lo fuera, un dato mal cargado
    dejaría el papel apagado para siempre."""
    fila = _asset("[1] AL30", vencimiento="2030-07-09", vigente=False,
                  vigencia_motivo=MOTIVO_JOB)
    assert decidir_vigencia([fila], _HOY) == [
        {"unidad": "[1] AL30", "vigente": True, "motivo": None}]


def test_no_se_reactiva_lo_que_apago_un_humano():
    fila = _asset("[1] AL30", vencimiento="2030-07-09", vigente=False,
                  vigencia_motivo="rescatado")
    assert decidir_vigencia([fila], _HOY) == []


def test_el_dia_del_vencimiento_todavia_esta_vigente():
    """Amortiza ESE día: el papel existe hasta que el día pasa."""
    assert decidir_vigencia([_asset("[1] AL30", vencimiento="2026-08-15")], _HOY) == []
