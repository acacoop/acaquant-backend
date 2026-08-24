"""tests/unit/test_ap5_posicion.py — la transformación de PositionReport.

Congela las reglas de `core/postrade_posicion.aplanar()`, que es pura (recibe
la respuesta y devuelve filas, sin red ni base). Cada test de acá corresponde a
un modo de fallar que NO grita: el dato queda mal escrito y todo lo demás sigue
funcionando.
"""
from __future__ import annotations

from datetime import date

from core.postrade_posicion import aplanar, familia_de_contrato, lado, multiplicadores
from jobs.ap5_portfolio import deduplicar, ultimo_dia_habil

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
        "side": "short",
        "cfi_code": "FXXXSX",
        "unit_of_measure": "Tn",
        "currency": "Dólar MtR",
        "avg_px": 228.0,
        "daily_settlement": -320.0,
        "settlement_price": 228.4,
        "settlement_currency": "Dólar MtR",
        "long_qty": 0.0,
        "short_qty": 8.0,
    }]


# --------------------------------------------------------------------------- #
# El LADO — sin esto se pierde una posición entera
# --------------------------------------------------------------------------- #
def test_lado_segun_la_pata():
    assert lado(10, 0) == "long"
    assert lado(0, 8) == "short"
    assert lado(0, 0) == "flat"


def test_lado_con_las_dos_patas_no_inventa_un_lado():
    """No se vio en producción. Si aparece hay que MIRARLO, no adivinar."""
    assert lado(5, 3) == "long+short"


def test_las_dos_patas_del_mismo_futuro_son_DOS_posiciones():
    """MEDIDO en producción: la cámara manda la pata larga y la corta del mismo
    instrumento, en la misma cuenta, como registros separados y con su PROPIO
    precio promedio (SOJ.ROS/NOV26 cuenta 331000: 346,7 y 355,4).

    Si colapsaran en una clave, el UPSERT guardaría una y perdería la otra sin
    que nada falle. Este test es el que impide que eso vuelva a pasar.
    """
    corta = {**FUTURO, "AvgPX": 346.7, "PositionQty": [{"PosType": "FIN", "ShortQty": 1}]}
    larga = {**FUTURO, "AvgPX": 355.4, "PositionQty": [{"PosType": "FIN", "LongQty": 1}]}
    filas, _ = aplanar([corta, larga])

    assert len(filas) == 2
    assert {f["side"] for f in filas} == {"long", "short"}

    salida, divergencias = deduplicar(filas)
    assert len(salida) == 2, "las dos patas colapsaron en una: se pierde una posición"
    assert divergencias == []

    # Y cada pata conserva SU precio promedio, que es el dato del negocio.
    por_lado = {f["side"]: f["avg_px"] for f in filas}
    assert por_lado == {"short": 346.7, "long": 355.4}


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
# Deduplicación — el UPSERT pierde datos en silencio si esto falla
# --------------------------------------------------------------------------- #
def _fila(**kw):
    base = {
        "business_date": "2026-08-21", "account": "155235", "symbol": "MAI.ROS/SEP26",
        "position_type": "FIN", "side": "long", "cfi_code": "FXXXSX",
        "unit_of_measure": "Tn", "currency": "Dólar MtR", "avg_px": 188.0,
        "daily_settlement": 1000.0, "settlement_price": 197.0,
        "settlement_currency": "Dólar MtR", "long_qty": 10.0, "short_qty": 0.0,
    }
    return {**base, **kw}


def test_filas_identicas_se_colapsan_sin_ruido():
    """Guardar una de dos filas iguales no pierde nada: no es un problema."""
    salida, divergencias = deduplicar([_fila(), _fila()])
    assert len(salida) == 1
    assert divergencias == []


def test_filas_que_difieren_se_reportan_CON_EL_CAMPO():
    """Un conteo dice que hay un problema; el campo dice cuál es.

    Este es el caso caro: el UPSERT se queda con una y pierde la otra **sin
    fallar**, así que si esto no se canta el dato queda mal y nadie se entera.
    """
    salida, divergencias = deduplicar([_fila(long_qty=10.0), _fila(long_qty=25.0)])
    assert len(salida) == 1
    assert len(divergencias) == 1
    assert "long_qty" in divergencias[0]
    assert "10.0" in divergencias[0] and "25.0" in divergencias[0]


def test_claves_distintas_no_se_tocan():
    filas = [_fila(), _fila(symbol="TRI.ROS/ENE27"), _fila(account="999")]
    salida, divergencias = deduplicar(filas)
    assert len(salida) == 3
    assert divergencias == []


def test_deduplicar_conserva_una_fila_completa():
    """Lo que sale tiene que ser una fila escribible, no un resumen."""
    salida, _ = deduplicar([_fila(), _fila()])
    assert salida[0]["symbol"] == "MAI.ROS/SEP26"
    assert salida[0]["avg_px"] == 188.0


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


# --------------------------------------------------------------------------- #
# El MULTIPLICADOR de contrato — sin esto la posición sale 100 veces más chica
#
# Este bloque congela el modo de falla más caro de la vista: `long_qty` viene en
# CONTRATOS y el reporte habla de toneladas. Si se muestra la cantidad cruda, el
# total es plausible y está mal — las cantidades igual suman bien entre sí.
# --------------------------------------------------------------------------- #
def _pos(symbol, long_qty, short_qty, avg, px, settl, unidad="Tn"):
    return {"symbol": symbol, "unit_of_measure": unidad, "long_qty": long_qty,
            "short_qty": short_qty, "avg_px": avg, "settlement_price": px,
            "daily_settlement": settl}


def test_multiplicador_se_despeja_de_los_propios_datos():
    """MEDIDO en producción: MAI.ROS/SEP26, 1889 contratos comprados a 188,028321
    con ajuste 197 dieron un settlement de 1.694.750 → 100 Tn por contrato."""
    m, sin = multiplicadores([_pos("MAI.ROS/SEP26", 1889, 0, 188.028321, 197.0, 1694750)])
    assert m["MAI.ROS/SEP26"]["multiplicador"] == 100.0
    assert m["MAI.ROS/SEP26"]["fuente"] == "derivado"
    assert sin == []


def test_la_unidad_de_medida_NO_alcanza_para_adivinarlo():
    """La razón por la que esto se deriva y no se escribe a mano.

    Los tres son `Tn` y valen distinto: el `.ROS` 100, el `.MIN` (mini) 10 y el
    `.CME` 5. Una regla por unidad erraría 10× en los minis — y el total saldría
    igual, sin fallar nada.
    """
    m, _ = multiplicadores([
        _pos("SOJ.ROS/NOV26", 572, 0, 345.702097, 359.3, 777800),
        _pos("SOJ.MIN/NOV26", 100, 0, 345.0, 359.3, 14300),
        _pos("SOY.CME/OCT26", 100, 0, 345.0, 359.3, 7150),
    ])
    assert m["SOJ.ROS/NOV26"]["multiplicador"] == 100.0
    assert m["SOJ.MIN/NOV26"]["multiplicador"] == 10.0
    assert m["SOY.CME/OCT26"]["multiplicador"] == 5.0


def test_el_short_tambien_despeja_bien():
    """La pata corta tiene settlement negativo: el signo se cancela y el
    multiplicador sale positivo igual."""
    m, _ = multiplicadores([_pos("MAI.ROS/DIC26", 0, 599, 190.475292, 203.2, -762210)])
    assert m["MAI.ROS/DIC26"]["multiplicador"] == 100.0


def test_vencimiento_sin_settlement_HEREDA_del_hermano():
    """El caso real: `MAI.ROS/MAR27` entró con settlement 0 y sin él su posición
    no se podía expresar en toneladas — quedaba fuera del total sin que nada
    falle. El tamaño es del CONTRATO, no del mes."""
    m, sin = multiplicadores([
        _pos("MAI.ROS/SEP26", 1889, 0, 188.028321, 197.0, 1694750),
        _pos("MAI.ROS/MAR27", 5, 0, 200.0, 200.0, 0),
    ])
    assert m["MAI.ROS/MAR27"]["multiplicador"] == 100.0
    assert m["MAI.ROS/MAR27"]["fuente"] == "hermano"
    assert sin == []


def test_hermanos_que_NO_coinciden_no_heredan():
    """Dos tamaños bajo el mismo contrato no es un dato, es una pregunta: el
    contrato cambió de tamaño y eso lo tiene que mirar una persona."""
    m, sin = multiplicadores([
        _pos("XX.ROS/SEP26", 10, 0, 100.0, 110.0, 10000),    # mult 100
        _pos("XX.ROS/DIC26", 10, 0, 100.0, 110.0, 1000),     # mult 10
        _pos("XX.ROS/MAR27", 5, 0, 100.0, 100.0, 0),         # sin settlement
    ])
    assert "XX.ROS/MAR27" not in m
    assert sin == ["XX.ROS/MAR27"]


def test_lo_que_no_se_resuelve_NO_recibe_un_1_por_default():
    """Inventar un 1 sería peor que no tenerlo: el total saldría igual de
    plausible y nadie podría notar que falta."""
    m, sin = multiplicadores([_pos("ZZZ012027", 5, 0, 100.0, 100.0, 0, unidad="USD")])
    assert m == {}
    assert sin == ["ZZZ012027"]


def test_la_dispersion_delata_un_contrato_que_cambio_de_tamano():
    """Hoy da < 0,002 en producción. Si un día se dispara, es lo único que avisa."""
    m, _ = multiplicadores([
        _pos("MAI.ROS/SEP26", 1889, 0, 188.028321, 197.0, 1694750),
        _pos("MAI.ROS/SEP26", 100, 0, 188.0, 197.0, 90000),
    ])
    assert m["MAI.ROS/SEP26"]["dispersion"] > 0


def test_familia_de_contrato_saca_el_vencimiento():
    assert familia_de_contrato("MAI.ROS/MAR27") == "MAI.ROS"
    assert familia_de_contrato("SOJ.MIN/NOV26") == "SOJ.MIN"
    assert familia_de_contrato("DLR102026") == "DLR"
    assert familia_de_contrato("WTI092026") == "WTI"


def test_multiplicadores_con_entrada_vacia_o_incompleta_no_revienta():
    m, sin = multiplicadores([])
    assert (m, sin) == ({}, [])
    m, sin = multiplicadores([{"symbol": "X", "long_qty": 1, "short_qty": 0}])
    assert m == {} and sin == ["X"]
