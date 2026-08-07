"""Tests del AGREGADO de fundamentals (panel superior derecho del screener RV
internacional): `core.eikon_live._agregar`.

Fija las tres decisiones que hacen que la curva no mienta:
  1. Empresas con cierre fiscal distinto se alinean por CALENDARIO.
  2. La canasta CONSTANTE deja afuera a la que no cubre todos los períodos —
     un salto en la curva tiene que ser negocio, no una empresa que entró.
  3. Los márgenes del agregado se DERIVAN de los montos sumados
     (Σ utilidad ÷ Σ ingresos), no se promedian.
"""
from __future__ import annotations

from core.eikon_live import _agregar, _periodo_calendario


def test_periodo_calendario():
    """El cierre fiscal se mapea al período de CALENDARIO en que cae."""
    assert _periodo_calendario("2025-09-30", "anual") == "2025"
    assert _periodo_calendario("2025-09-30", "trimestral") == "2025Q3"
    assert _periodo_calendario("2026-01-26", "trimestral") == "2026Q1"   # NVDA
    assert _periodo_calendario("2025-12-31", "trimestral") == "2025Q4"
    assert _periodo_calendario("", "anual") is None
    assert _periodo_calendario(None, "anual") is None


def _doc(*filas):
    return {"serie_anual": list(filas)}


def test_alinea_cierres_fiscales_distintos():
    """AAPL cierra en septiembre y KO en diciembre: ambos caen en 2025 y se
    suman en el mismo punto (sin esto no habría con qué comparar)."""
    out = _agregar({
        "AAPL": _doc({"fecha": "2025-09-30", "revenue": 400.0},
                     {"fecha": "2024-09-28", "revenue": 380.0}),
        "KO":   _doc({"fecha": "2025-12-31", "revenue": 100.0},
                     {"fecha": "2024-12-31", "revenue": 90.0}),
    }, modo="anual", canasta="constante")

    assert [p["periodo"] for p in out["puntos"]] == ["2024", "2025"]
    assert [p["revenue"] for p in out["puntos"]] == [470.0, 500.0]
    assert [p["n"] for p in out["puntos"]] == [2, 2]
    assert out["empresas"] == ["AAPL", "KO"]
    assert out["excluidas"] == []


def test_canasta_constante_excluye_cobertura_parcial():
    """La empresa que solo tiene el último año NO entra a la canasta constante
    (si entrara, el salto 2024→2025 sería puro artefacto de cobertura)."""
    series = {
        "AAPL": _doc({"fecha": "2025-09-30", "revenue": 400.0},
                     {"fecha": "2024-09-28", "revenue": 380.0}),
        "NUEVA": _doc({"fecha": "2025-12-31", "revenue": 50.0}),
        "SINSERIE": {},
    }
    out = _agregar(series, modo="anual", canasta="constante")
    assert out["empresas"] == ["AAPL"]
    assert [p["revenue"] for p in out["puntos"]] == [380.0, 400.0]
    motivos = {e["ticker"]: e["motivo"] for e in out["excluidas"]}
    assert motivos == {"NUEVA": "no cubre todos los períodos",
                       "SINSERIE": "sin serie histórica"}

    # Con canasta='todas' la nueva SÍ suma, y el punto avisa con cuántas
    # empresas se armó (2 en 2025 vs 1 en 2024).
    todas = _agregar(series, modo="anual", canasta="todas")
    assert sorted(todas["empresas"]) == ["AAPL", "NUEVA"]
    assert [p["revenue"] for p in todas["puntos"]] == [380.0, 450.0]
    assert [p["n"] for p in todas["puntos"]] == [1, 2]


def test_margenes_del_agregado_se_derivan_de_los_montos():
    """Margen de la canasta = Σ utilidad ÷ Σ ingresos. Un promedio simple daría
    15% (media de 20% y 10%); el margen REAL de la canasta es 12,5%."""
    out = _agregar({
        "GRANDE": _doc({"fecha": "2025-12-31", "revenue": 1000.0, "net_income": 100.0}),
        "CHICA":  _doc({"fecha": "2025-12-31", "revenue": 200.0, "net_income": 40.0}),
    }, modo="anual", canasta="constante")

    punto = out["puntos"][-1]
    assert punto["revenue"] == 1200.0
    assert punto["net_income"] == 140.0
    assert round(punto["margen_neto"], 4) == round(140 / 1200 * 100, 4)


def test_cobertura_por_metrica_y_metricas_faltantes():
    """El capex recién llega con la serie ampliada del feed: mientras falte, la
    métrica viaja en None (no en 0) y `capex_n` dice sobre cuántas empresas se
    sumó — un 0 mentiría diciendo 'no invirtieron nada'."""
    out = _agregar({
        "A": _doc({"fecha": "2025-12-31", "revenue": 100.0, "capex": -10.0}),
        "B": _doc({"fecha": "2025-12-31", "revenue": 200.0}),
    }, modo="anual", canasta="constante")

    punto = out["puntos"][-1]
    assert punto["revenue"] == 300.0 and punto["revenue_n"] == 2
    assert punto["capex"] == -10.0 and punto["capex_n"] == 1
    assert punto["ebitda"] is None and punto["ebitda_n"] == 0
    assert punto["margen_neto"] is None       # sin resultado no se inventa margen


def test_la_ventana_se_corre_a_donde_hay_datos():
    """BUG REAL (2026-08-07, universo de 184 empresas): tomando SIEMPRE los
    últimos N períodos, la canasta constante daba 0 empresas en trimestral —
    el período más reciente lo tiene solo la minoría que ya reportó.

    Acá: 3 empresas que cierran en diciembre (2020-2024) y UNA que cierra en
    enero, así que sus ejercicios caen en 2021-2025. Con 'los últimos 5' la
    ventana sería 2021-2025 y la canasta tendría UNA sola empresa. Corriendo la
    ventana se elige 2020-2024, donde entran TRES — el tramo con datos de
    verdad. La minoría adelantada queda afuera, dicho con su motivo.
    """
    series = {
        "A": _doc(*[{"fecha": f"{a}-12-31", "revenue": 10.0} for a in range(2020, 2025)]),
        "B": _doc(*[{"fecha": f"{a}-12-31", "revenue": 20.0} for a in range(2020, 2025)]),
        "C": _doc(*[{"fecha": f"{a}-12-31", "revenue": 30.0} for a in range(2020, 2025)]),
        "ADELANTADA": _doc(*[{"fecha": f"{a}-01-31", "revenue": 1.0}
                             for a in range(2021, 2026)]),
    }
    out = _agregar(series, modo="anual", canasta="constante")
    assert out["ventana"] == {"desde": "2020", "hasta": "2024"}
    assert [p["periodo"] for p in out["puntos"]] == ["2020", "2021", "2022", "2023", "2024"]
    assert sorted(out["empresas"]) == ["A", "B", "C"]
    assert [p["revenue"] for p in out["puntos"]] == [60.0] * 5
    assert [e["ticker"] for e in out["excluidas"]] == ["ADELANTADA"]


def test_ante_igual_cobertura_gana_la_ventana_mas_reciente():
    """Si dos ventanas cubren lo mismo, se muestra la más nueva — el research
    mira para adelante."""
    series = {"A": _doc(*[{"fecha": f"{a}-12-31", "revenue": 1.0}
                          for a in range(2019, 2026)])}
    out = _agregar(series, modo="anual", canasta="constante")
    assert out["ventana"] == {"desde": "2021", "hasta": "2025"}


def test_ventana_recorta_a_los_ultimos_periodos():
    """Anual muestra 5 puntos como máximo (lo que trae el feed)."""
    filas = [{"fecha": f"{a}-12-31", "revenue": float(a)} for a in range(2018, 2026)]
    out = _agregar({"A": _doc(*filas)}, modo="anual", canasta="constante")
    assert [p["periodo"] for p in out["puntos"]] == ["2021", "2022", "2023", "2024", "2025"]


def test_trimestral_ventana_y_etiquetas():
    """Trimestral: 8 puntos, etiquetados por trimestre de calendario."""
    filas = [{"fecha": f"{a}-{m:02d}-28", "revenue": 1.0}
             for a in (2024, 2025) for m in (3, 6, 9, 12)]
    out = _agregar({"A": {"serie_trimestral": filas}},
                   modo="trimestral", canasta="constante")
    assert [p["periodo"] for p in out["puntos"]] == [
        "2024Q1", "2024Q2", "2024Q3", "2024Q4",
        "2025Q1", "2025Q2", "2025Q3", "2025Q4"]
