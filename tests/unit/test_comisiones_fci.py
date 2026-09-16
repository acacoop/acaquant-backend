"""tests/unit/test_comisiones_fci.py — el cálculo del arancel de FCI.

Lo que se congela acá es plata que se factura. Tres fallas que esto previene:

  · que alguien "simplifique" el ÷2 (el fee guardado es el ENTERO de la gerente;
    la mitad es del mercado),
  · que los fines de semana dejen de devengar y el mes quede ~25 % corto **sin que
    nada avise** — la tenencia solo tiene foto los días hábiles,
  · que un fondo sin `fee_admin` pase a valer 0 en vez de declararse desconocido.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import comisiones_fci as s


# ── La fórmula ───────────────────────────────────────────────────────────────
def test_la_mitad_es_del_mercado():
    """`fee_admin` es el honorario ANUAL COMPLETO de la gerente. Medido contra el
    export contable de agosto 2026: la relación es exactamente 2,0. Si esto
    cambiara a 1, todo lo que se factura se duplica."""
    assert s.PARTE_ACA == 0.5


def test_el_devengamiento_es_por_dia_corrido():
    """365, no 252: el honorario corre sábados, domingos y feriados."""
    assert s.DIAS_ANIO == 365


def test_la_formula_del_sql_es_la_declarada():
    """La expresión que se manda a Postgres tiene que ser la del docstring. Un
    `/2` que se caiga del SQL no rompe nada: factura el doble, en silencio."""
    assert "t.valuacion" in s._ARANCEL_DIA
    assert "a.fee_admin" in s._ARANCEL_DIA
    assert f"{s.PARTE_ACA}" in s._ARANCEL_DIA
    assert f"{s.DIAS_ANIO}" in s._ARANCEL_DIA


def test_un_caso_con_numeros_de_verdad():
    """SBS Pesos Plus: fee 2,5 % anual. Con $1.000.000 el contable devengó 0,0125
    (su fee ya es la mitad) → nuestro cálculo con el fee entero tiene que dar lo
    mismo."""
    valuacion, fee_entero, fee_contable = 1_000_000.0, 0.025, 0.0125
    nuestro = valuacion * fee_entero * s.PARTE_ACA / s.DIAS_ANIO
    del_contable = valuacion * fee_contable / s.DIAS_ANIO
    assert nuestro == pytest.approx(del_contable)
    assert nuestro == pytest.approx(34.2465753, abs=1e-6)


# ── Ventana del mes ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(("mes", "ini", "fin"), [
    ("2026-08", date(2026, 8, 1), date(2026, 8, 31)),
    ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),   # no bisiesto
    ("2024-02", date(2024, 2, 1), date(2024, 2, 29)),   # bisiesto
    ("2026-12", date(2026, 12, 1), date(2026, 12, 31)),  # cambio de año
    ("2026-04", date(2026, 4, 1), date(2026, 4, 30)),
])
def test_el_mes_se_cierra_en_su_ultimo_dia(mes, ini, fin):
    """Diciembre y febrero son donde un `+1 mes` casero se rompe."""
    assert s._mes_rango(mes) == (ini, fin)


# ── Tramos: el fin de semana no es un agujero ────────────────────────────────
def _cubren(fechas: list[date], ini: date, fin: date) -> dict[date, int]:
    """Replica la aritmética de `_TRAMOS` sin ir a la base: cada foto cubre hasta
    el día anterior a la siguiente, recortada a [ini, fin]."""
    out = {}
    for i, f in enumerate(fechas):
        sig = fechas[i + 1] if i + 1 < len(fechas) else None
        desde = max(f, ini)
        hasta = min(sig - __import__("datetime").timedelta(days=1), fin) if sig else fin
        if hasta >= desde:
            out[f] = (hasta - desde).days + 1
    return out


def test_el_viernes_cubre_sabado_y_domingo():
    """Si cada foto devengara un solo día, un mes daría ~22 de 31 — un 29 % menos
    de comisión, sin error ni log."""
    # Ago 2026: 7=vie, 10=lun.
    cubre = _cubren([date(2026, 8, 7), date(2026, 8, 10)],
                    date(2026, 8, 1), date(2026, 8, 31))
    assert cubre[date(2026, 8, 7)] == 3


def test_la_foto_del_mes_anterior_cubre_el_arranque():
    """Ago 2026 arranca sábado. Sin el ancla del 31/07 los días 1 y 2 no devengan
    y el mes nace con dos días menos."""
    cubre = _cubren([date(2026, 7, 31), date(2026, 8, 3)],
                    date(2026, 8, 1), date(2026, 8, 31))
    assert cubre[date(2026, 7, 31)] == 2      # recortado a agosto: 1 y 2
    assert date(2026, 7, 31) in cubre


def test_la_ultima_foto_cubre_hasta_el_fin_del_recorte():
    """La última foto no se queda en su día: devenga hasta el corte."""
    cubre = _cubren([date(2026, 8, 28)], date(2026, 8, 1), date(2026, 8, 31))
    assert cubre[date(2026, 8, 28)] == 4      # 28, 29, 30, 31


def test_los_tramos_cubren_el_mes_entero_sin_huecos_ni_solapes():
    """La invariante que hace que el total sea el del contable: Σ días = días del
    mes. Ni uno de menos (falta plata) ni uno de más (se cobra dos veces)."""
    ini, fin = s._mes_rango("2026-08")
    habiles = [d for d in (date(2026, 8, i) for i in range(1, 32)) if d.weekday() < 5]
    anclas = [date(2026, 7, 31), *habiles]
    assert sum(_cubren(anclas, ini, fin).values()) == 31


# ── Sin fee: desconocido, no cero ────────────────────────────────────────────
def test_el_sql_no_convierte_el_fee_faltante_en_cero():
    """Un `coalesce(fee_admin, 0)` haría que un fondo sin tarifa devengue 0 y se
    lea como "no genera". Tiene que quedar NULL para caer en `sin_fee`."""
    assert "coalesce(a.fee_admin" not in s._ARANCEL_DIA.lower()


def test_el_join_a_assets_es_left():
    """Un fondo sin asset NO puede desaparecer de la tabla: sale con el fee en
    NULL. Un INNER JOIN lo borraría junto con su valuación."""
    sql = s._sql_agregado("t.unidad")
    assert "LEFT JOIN portafolio.assets" in sql


def test_la_serie_historica_si_exige_fee():
    """El gráfico histórico es de PLATA DEVENGADA: una barra no puede mezclar
    "no generó" con "no sabemos". Ahí el join sí filtra por fee cargado."""
    assert "a.fee_admin IS NOT NULL" in s._SQL_SERIE


def test_el_mes_vacio_se_declara_y_no_da_cero():
    """Un mes sin foto no es un mes sin comisiones."""
    v = s._vacio("2019-01")
    assert v["sin_datos"] is True
    assert v["corte"] is None


# ── Las dos grafías de FCI ───────────────────────────────────────────────────
def test_entran_las_dos_grafias_de_cartera_fci():
    """'FCI' y 'CARTERA FCI' conviven en la base. Filtrar por una sola pierde
    fondos enteros sin avisar."""
    assert set(s._FCI_PARAMS) == {"FCI", "CARTERA FCI"}
    assert "upper(btrim" in s._W_FCI
