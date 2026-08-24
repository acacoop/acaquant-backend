"""tests/unit/test_ap5_posicion.py — la transformación de PositionReport.

Congela las reglas de `core/postrade_posicion.aplanar()`, que es pura (recibe
la respuesta y devuelve filas, sin red ni base). Cada test de acá corresponde a
un modo de fallar que NO grita: el dato queda mal escrito y todo lo demás sigue
funcionando.
"""
from __future__ import annotations

from datetime import date

from core.postrade_posicion import aplanar
from jobs.ap5_portfolio import ultimo_dia_habil

# La respuesta real de producción, tal cual la devuelve la cámara.
FUTURO = {
    "PosReqType": 0,
    "ClearingBusinessDate": "2026-08-21",
    "SettlSessID": "EOD",
    "Account": "156947",
    "ClearingMember": "ACA VALORES S.A.",
    "ClearingMemberCode": "172",
    "Instrument": {
        "Symbol": "TRI.ROS/ENE27",
        "CFICode": "FXXXSX",
        "SecurityType": "Futuro",
        "UnitOfMeasure": "Tn",
    },
    "Currency": "Dólar MtR",
    "AvgPX": 228.0,
    "SecurityExchange": "XMTB",
    "DailySettlement": -320.00,
    "SettlPrice": 228.40,
    "SettlPriceType": 1,
    "SettlCurrency": "Dólar MtR",
    "PositionQty": [{"PosType": "FIN", "ShortQty": 8.0}],
}


def test_mapea_los_campos_pedidos():
    filas, _ = aplanar([FUTURO])
    assert filas == [{
        "business_date": "2026-08-21",
        "account": "156947",
        "symbol": "TRI.ROS/ENE27",
        "position_type": "FIN",
        "cfi_code": "FXXXSX",
        "unit_of_measure": "Tn",
        "daily_settlement": -320.0,
        "settlement_price": 228.4,
        "settlement_currency": "Dólar MtR",
        "long_qty": 0.0,
        "short_qty": 8.0,
    }]


def test_la_cantidad_ausente_es_CERO_y_no_null():
    """La API OMITE el campo cuando vale cero: no manda `LongQty: 0`.

    Si eso se guardara como NULL, 'no tengo posición larga' y 'no sé si tengo
    posición larga' quedarían escritos igual — y solo uno es cierto.
    """
    filas, _ = aplanar([FUTURO])
    assert filas[0]["long_qty"] == 0.0
    assert filas[0]["long_qty"] is not None


def test_position_qty_multiple_se_expande_en_filas():
    """Un blob no se puede sumar ni filtrar sin re-parsearlo en cada consulta."""
    p = {**FUTURO, "PositionQty": [
        {"PosType": "FIN", "ShortQty": 8},
        {"PosType": "TOT", "LongQty": 3},
    ]}
    filas, _ = aplanar([p])
    assert len(filas) == 2
    assert {f["position_type"] for f in filas} == {"FIN", "TOT"}
    assert [f["short_qty"] for f in filas if f["position_type"] == "FIN"] == [8.0]
    assert [f["long_qty"] for f in filas if f["position_type"] == "TOT"] == [3.0]


def test_solo_entran_los_futuros():
    opcion = {**FUTURO, "Instrument": {**FUTURO["Instrument"], "SecurityType": "Opcion"}}
    filas, stats = aplanar([FUTURO, opcion])
    assert len(filas) == 1
    assert stats["futuros"] == 1
    assert stats["descartadas_no_futuro"] == 1


def test_el_descarte_se_cuenta_no_se_esconde():
    """Un filtro silencioso es indistinguible de un bug que se come los datos."""
    otros = [
        {**FUTURO, "Instrument": {**FUTURO["Instrument"], "SecurityType": t}}
        for t in ("Opcion", "PAFG", "CS", "")
    ]
    _, stats = aplanar(otros)
    assert stats["recibidas"] == 4
    assert stats["descartadas_no_futuro"] == 4
    assert stats["futuros"] == 0


def test_futuro_sin_position_qty_se_cuenta_aparte():
    """No es una posición, pero tampoco puede desaparecer sin dejar rastro."""
    sin = {**FUTURO, "PositionQty": []}
    filas, stats = aplanar([sin])
    assert filas == []
    assert stats["sin_position_qty"] == 1
    assert stats["futuros"] == 1


def test_la_fecha_se_corta_a_10_aunque_venga_con_hora():
    """La API alterna 'AAAA-MM-DD' y 'AAAA-MM-DDT00:00:00' según el método."""
    p = {**FUTURO, "ClearingBusinessDate": "2026-08-21T00:00:00"}
    filas, _ = aplanar([p])
    assert filas[0]["business_date"] == "2026-08-21"


def test_precio_ausente_queda_NULL_y_no_cero():
    """Al revés que las cantidades: en un precio, 'no vino' y 'vale cero' son
    cosas distintas y confundirlas inventa un dato que nadie informó."""
    p = {**FUTURO}
    p.pop("SettlPrice")
    filas, _ = aplanar([p])
    assert filas[0]["settlement_price"] is None


def test_settlement_cero_se_preserva():
    """Un DailySettlement de 0 ES un dato: no se puede confundir con ausencia."""
    filas, _ = aplanar([{**FUTURO, "DailySettlement": 0}])
    assert filas[0]["daily_settlement"] == 0.0


def test_respuesta_vacia_o_rara_no_revienta():
    for entrada in ([], None, {}, "texto", [None, 1, "x"]):
        filas, _ = aplanar(entrada)
        assert filas == []


# --------------------------------------------------------------------------- #
# El último día hábil
# --------------------------------------------------------------------------- #
def test_lunes_devuelve_el_viernes():
    """Ayer de un lunes es domingo — y la cámara no publica los domingos."""
    assert ultimo_dia_habil(date(2026, 8, 24)) == "20260821"  # lunes → viernes


def test_dia_normal_devuelve_ayer():
    assert ultimo_dia_habil(date(2026, 8, 21)) == "20260820"  # viernes → jueves


def test_saltea_feriados_no_solo_findes():
    """25 de mayo (feriado, lunes). El martes 26 tiene que devolver el viernes 22.

    El repo ya se comió una vez el bug de contar solo `weekday < 5`.
    """
    assert ultimo_dia_habil(date(2026, 5, 26)) == "20260522"
