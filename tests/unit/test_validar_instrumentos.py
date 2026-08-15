"""Vigencia de títulos + marca de validación (jobs/validar_instrumentos.py).

Lo que se congela: cuándo un título deja de estar vigente y cuándo NO. Apagar de
más borra papeles vivos de la vista; apagar de menos deja basura. Las dos cosas
se deciden acá y no en una query, para que se puedan probar sin base.
"""
from __future__ import annotations

from datetime import date

from jobs.validar_instrumentos import (
    MOTIVO_JOB,
    decidir_validacion,
    decidir_vigencia,
)

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


# ── Marca de validación ──────────────────────────────────────────────────────

def test_marca_todos_los_simbolos_no_solo_los_rotos():
    """La marca lleva `validado_at`: sin re-confirmar los buenos, un `true` de
    hace tres meses no se distingue de uno de hoy."""
    marcas = decidir_validacion(["A", "B"], {"A"})
    assert marcas == [{"simbolo": "A", "validado": True},
                      {"simbolo": "B", "validado": False}]


def test_no_marca_dos_veces_el_mismo_simbolo():
    assert len(decidir_validacion(["A", "A", "A"], {"A"})) == 1
