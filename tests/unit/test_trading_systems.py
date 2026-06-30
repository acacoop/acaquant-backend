"""Tests de la lógica pura del panel de Trading (api/services/trading_systems)."""
from __future__ import annotations

from api.services.trading_systems import derivar_campos, evaluar_sistemas


def _scanner_row(**kw) -> dict:
    base = {
        "ticker_corto": "TEST", "last": None, "vwap": None,
        "open": None, "high": None, "low": None, "close": None,
        "intraday_pct": None, "vs_1d_pct": None, "vs_1d_usd_pct": None,
        "adr_vs_1d_pct": None, "adr_ret_mtd_pct": None, "spread_pct": None,
        "updated_at": None,
    }
    base.update(kw)
    return base


def _campos_base(**kw) -> dict:
    """campos con todo None salvo lo que el test setea (para probar el semáforo)."""
    base = {
        "pct_vs_vwap": None, "pos_rango": None, "gap_adr_pct": None,
        "rango_dia_pct": None, "retroceso_high_pct": None, "retroceso_low_pct": None,
        "zscore": None, "vol_diaria_pct": None, "dia_volatil": False,
        "dist_R_pct": None, "dist_S_pct": None, "toca_R2_R3": None, "toca_S2_S3": None,
        "vwap_crosses": None, "estado_OR": None, "climax": None,
        "minutos_rueda": None, "spread_pct": None, "adr_vs_1d_pct": None,
        "adr_ret_mtd_pct": None,
    }
    base.update(kw)
    return base


# ── campos derivados ──────────────────────────────────────────────────────────

def test_pos_rango_y_pct_vs_vwap():
    sr = _scanner_row(last=104, vwap=100, high=110, low=100, close=100)
    c = derivar_campos(sr, None, None, [])
    assert c["pos_rango"] == 40.0          # (104-100)/(110-100)*100
    assert c["pct_vs_vwap"] == 4.0         # (104-100)/100*100


def test_pos_rango_high_igual_low():
    sr = _scanner_row(last=100, high=100, low=100, close=100)
    assert derivar_campos(sr, None, None, [])["pos_rango"] == 50.0


def test_gap_adr_es_resta_de_los_dos_vs_1d():
    sr = _scanner_row(adr_vs_1d_pct=2.0, vs_1d_usd_pct=1.0)
    assert derivar_campos(sr, None, None, [])["gap_adr_pct"] == 1.0


def test_dia_volatil_usa_vol30d_como_fraccion():
    # vol30d=0.30 (fracción) → vol_diaria ≈ 1.889% ; rango 5% > 1.5×1.889 → volátil
    sr = _scanner_row(last=105, high=105, low=100, close=100)
    stats = {"vol": {"d30": 0.30, "d60": 0.30}, "zscore": {"d30": 1.0, "d60": 1.0}}
    c = derivar_campos(sr, None, stats, [])
    assert c["dia_volatil"] is True
    # rango chico (0.5%) → no volátil
    sr2 = _scanner_row(last=100.5, high=100.5, low=100, close=100)
    assert derivar_campos(sr2, None, stats, [])["dia_volatil"] is False


def test_pivotes_distancia_y_toque():
    sr = _scanner_row(last=119)
    pivots = {"frames": {"diario": {"levels": {
        "pp": 110, "r1": 115, "r2": 118, "r3": 125,
        "s1": 105, "s2": 100, "s3": 95,
    }}}}
    c = derivar_campos(sr, pivots, None, [])
    assert c["toca_R2_R3"] is True          # 119 >= 118*0.998
    assert c["toca_S2_S3"] is False
    assert round(c["dist_R_pct"], 3) == round((125 - 119) / 119 * 100, 3)  # R3 inmediato


def test_velas_opening_range_y_cruces():
    # OR = primeras 15' → solo las velas en minutos 0,1,2 (high=102.2, low=99.8);
    # las de minuto 16+ quedan fuera del opening range.
    candles = []
    serie = [(0, 100), (1, 101), (2, 102), (16, 103), (17, 99), (18, 98),
             (19, 101), (20, 97), (21, 102)]  # zigzag → varios cruces de VWAP
    for m, p in serie:
        candles.append({
            "t": f"2026-06-30T14:{m:02d}:00Z",
            "o": p, "h": p + 0.2, "l": p - 0.2, "c": p, "vol": 1000,
        })
    sr = _scanner_row(last=103)             # arriba del OR_high (102.2) → BREAK_UP
    c = derivar_campos(sr, None, None, candles)
    assert c["estado_OR"] == "BREAK_UP"
    assert c["vwap_crosses"] >= 2
    assert c["minutos_rueda"] == 21.0


def test_rvol_dispara_dia_volatil():
    # rango chico (0.5%) → NO volátil por rango; pero RVOL=2.0 → SÍ volátil.
    sr = _scanner_row(last=100.5, high=100.5, low=100, close=100)
    stats = {"vol": {"d30": 0.30, "d60": 0.30}, "zscore": {"d30": 1.0, "d60": 1.0}}
    candles = [
        {"t": "2026-06-30T14:00:00Z", "o": 100, "h": 100, "l": 100, "c": 100, "vol": 1000},
        {"t": "2026-06-30T14:01:00Z", "o": 100, "h": 100, "l": 100, "c": 100, "vol": 1000},
        {"t": "2026-06-30T14:02:00Z", "o": 100, "h": 100, "l": 100, "c": 100, "vol": 1000},
    ]
    baseline = {"14:00": 500.0, "14:01": 1000.0, "14:02": 1500.0}  # cum hoy 3000 / 1500 = 2.0
    c = derivar_campos(sr, None, stats, candles, baseline)
    assert c["rvol"] == 2.0
    assert c["dia_volatil"] is True
    # sin baseline → cae al fallback por rango (que acá da False)
    assert derivar_campos(sr, None, stats, candles)["dia_volatil"] is False


def test_baseline_at_toma_el_minuto_anterior_mas_cercano():
    from api.services.trading_systems import _baseline_at
    bl = {"14:00": 100.0, "14:05": 500.0, "14:10": 900.0}
    assert _baseline_at(bl, "14:07") == 500.0   # el <= más cercano
    assert _baseline_at(bl, "13:00") is None     # nada antes


# ── semáforo de sistemas ────────────────────────────────────────────────────────

def test_s4_dislocacion():
    assert evaluar_sistemas(_campos_base(gap_adr_pct=1.0))["S4"]["estado"] == "ACTIVO"
    assert evaluar_sistemas(_campos_base(gap_adr_pct=1.0))["S4"]["lado"] == "long"
    assert evaluar_sistemas(_campos_base(gap_adr_pct=-1.0))["S4"]["lado"] == "short"
    assert evaluar_sistemas(_campos_base(gap_adr_pct=0.6))["S4"]["estado"] == "VIGILAR"
    assert evaluar_sistemas(_campos_base(gap_adr_pct=0.1))["S4"]["estado"] == "INACTIVO"
    assert evaluar_sistemas(_campos_base(gap_adr_pct=None))["S4"]["estado"] == "INACTIVO"


def test_s2_fade_requiere_dia_volatil_y_gatillo():
    # extremo alto + retroceso en día volátil → ACTIVO short
    activo = _campos_base(
        dia_volatil=True, pos_rango=95, toca_R2_R3=True, zscore=3.0,
        retroceso_high_pct=0.8,
    )
    assert evaluar_sistemas(activo)["S2"] == {
        "estado": "ACTIVO", "lado": "short", "nota": "estirado arriba y empezó a retroceder",
    }
    # mismo extremo pero sin retroceso → VIGILAR
    vigilar = dict(activo, retroceso_high_pct=0.1)
    assert evaluar_sistemas(vigilar)["S2"]["estado"] == "VIGILAR"
    # sin día volátil → INACTIVO aunque esté estirado
    no_vol = dict(activo, dia_volatil=False)
    assert evaluar_sistemas(no_vol)["S2"]["estado"] == "INACTIVO"


def test_s3_scalp_rango():
    lateral = _campos_base(vwap_crosses=5, spread_pct=0.1)
    assert evaluar_sistemas(dict(lateral, pos_rango=10))["S3"]["lado"] == "long"
    assert evaluar_sistemas(dict(lateral, pos_rango=90))["S3"]["lado"] == "short"
    assert evaluar_sistemas(dict(lateral, pos_rango=50))["S3"]["estado"] == "VIGILAR"
    # spread ancho → descarta
    assert evaluar_sistemas(_campos_base(vwap_crosses=5, spread_pct=0.5))["S3"]["estado"] == "INACTIVO"
    # pocos cruces → no es lateral
    assert evaluar_sistemas(_campos_base(vwap_crosses=1, spread_pct=0.1))["S3"]["estado"] == "INACTIVO"


def test_s1_apertura_solo_en_ventana():
    activo = _campos_base(
        minutos_rueda=30, adr_vs_1d_pct=2.0, estado_OR="BREAK_UP", pct_vs_vwap=1.5,
    )
    assert evaluar_sistemas(activo)["S1"] == {
        "estado": "ACTIVO", "lado": "long",
        "nota": "rompió el opening range a favor del ADR y del VWAP",
    }
    # fuera de la ventana de 90' → INACTIVO
    assert evaluar_sistemas(dict(activo, minutos_rueda=120))["S1"]["estado"] == "INACTIVO"
    # dentro del OR → VIGILAR
    assert evaluar_sistemas(dict(activo, estado_OR="INSIDE"))["S1"]["estado"] == "VIGILAR"


def test_s5_holdeo_y_alerta_salida():
    sostener = _campos_base(pct_vs_vwap=1.0, vwap_crosses=1, adr_ret_mtd_pct=3.0)
    assert evaluar_sistemas(sostener)["S5"]["estado"] == "ACTIVO"
    # perdió el VWAP → alerta de salida
    alerta = _campos_base(pct_vs_vwap=-0.5, vwap_crosses=1, adr_ret_mtd_pct=3.0)
    s5 = evaluar_sistemas(alerta)["S5"]
    assert s5["estado"] == "VIGILAR" and "ALERTA" in s5["nota"]
