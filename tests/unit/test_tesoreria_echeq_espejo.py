"""Espejo automático: e-cheq de EGRESO de Aunesa → cheque EMITIDO de la tab CHEQUES.

Un egreso con RIEL '[E CHEQ] E CHEQ' ES un cheque emitido. El back office lo veía en
MOVIMIENTOS y lo volvía a cargar a mano, y el saldo lo restaba DOS veces (el movimiento
por un lado, el cheque manual por el otro). Estos tests congelan las dos mitades del
arreglo: que la fila nazca sola y completa, y que NO vuelva a restar del saldo.
"""

from datetime import date

import pytest

from api.services import tesoreria as tes

DIA = date(2026, 8, 10)


def _mov(**kw) -> dict:
    """El e-cheq de egreso tal como lo manda Aunesa (fila real del 10/08/2026)."""
    base = {
        "id": "20260810162000",
        "fecha": "10/08/2026",
        "estado": tes.ESTADO_EFECTIVO,
        "solicitud": "Extracción",
        "tipoDocSoli": "[E CHEQ] E CHEQ",
        "cuenta": "1632",
        "cuentaOperativa": {"denominacion": "BANCO COMAFI ACDI ARS", "id": "9"},
        "unidad": "ARS",
        "monto": 5_000_000,
        "persona": {"nombreCompleto": "DON TINCHO SRL", "cuit": "30-70937992-1"},
    }
    return base | kw


def test_el_echeq_de_egreso_se_espeja_con_todos_los_datos():
    """La fila tiene que quedar tan completa como una cargada a mano: si le falta el
    CUIT o el comitente, el back office igual tiene que ir a completarla y no le
    ahorramos nada."""
    filas = tes._filas_espejo_echeq(DIA, [_mov()])

    assert len(filas) == 1
    f = filas[0]
    assert f["mov"] == "20260810162000"
    assert f["comitente"] == "1632"
    assert f["denom"] == "DON TINCHO SRL"
    # Sin guiones, como los que carga el back office a mano.
    assert f["cuit"] == "30709379921"
    assert f["banco"] == "BANCO COMAFI ACDI ARS"
    assert f["unidad"] == "ARS"
    assert f["importe"] == 5_000_000.0
    # Un e-cheq que YA vino como movimiento se debitó ese día.
    assert f["fp"] == DIA


def test_no_se_espejan_los_ingresos_ni_los_egresos_que_no_son_echeq():
    """El corte es el MISMO que usa la fila `egresos_echeq` de BANCOS: egreso + RIEL
    e-cheq. Una transferencia común no es un cheque."""
    crudas = [
        _mov(id="1", solicitud="Depósito", tipoDocSoli="[E CHEQ] E CHEQ"),
        _mov(id="2", tipoDocSoli="[TR] Transferencia"),
    ]

    assert tes._filas_espejo_echeq(DIA, crudas) == []


def test_solo_se_espeja_lo_que_efectivamente_se_movio():
    """Un e-cheq rechazado/anulado nunca salió del banco. Espejarlo dejaría una fila
    fantasma en un tablero que NO se filtra por fecha: quedaría a la vista para siempre."""
    crudas = [_mov(id="1", estado="Rechazado"), _mov(id="2", estado="Anulado"),
              _mov(id="3", estado="Pendiente de autorizar")]

    assert tes._filas_espejo_echeq(DIA, crudas) == []


def test_sin_id_no_se_espeja_porque_se_duplicaria_en_cada_poll():
    """`mov_id` es la clave de idempotencia (índice único). El sync corre en CADA poll
    de la tab: sin clave, el mismo cheque se insertaría cada 20 segundos."""
    assert tes._filas_espejo_echeq(DIA, [_mov(id="")]) == []


def test_sin_cuenta_operativa_no_se_espeja():
    """`SIN CUENTA OPERATIVA` no es un banco (nunca entra al catálogo): un cheque
    colgado de ahí no se puede controlar contra ningún saldo."""
    assert tes._filas_espejo_echeq(DIA, [_mov(cuentaOperativa=None)]) == []


def test_el_espejo_NO_vuelve_a_restar_del_saldo(monkeypatch):
    """El corazón del arreglo: la plata del e-cheq ya entra a `egresos_echeq` por el
    movimiento de Aunesa. Si el espejo también sumara, el saldo del banco se hundiría
    exactamente el doble — que es el bug que este espejo vino a matar."""
    visto: dict[str, str] = {}
    monkeypatch.setattr(tes, "_items_sql", lambda sql, params: visto.update(sql=sql) or [])

    tes._cheques_emitidos_vencidos_rows(DIA)

    assert f"origen = '{tes.ORIGEN_MANUAL}'" in visto["sql"]


def test_el_espejo_no_se_puede_borrar(monkeypatch):
    """Borrarlo no sirve: el próximo poll lo vuelve a crear del mismo movimiento. Se
    cierra con 'completado', que es lo que lo saca de la vista sin perder la fila."""
    monkeypatch.setattr(tes, "puede_editar_saldo", lambda email: True)
    monkeypatch.setattr(tes, "_q", lambda sql, params: [{"origen": tes.ORIGEN_AUNESA}])

    with pytest.raises(ValueError, match="Aunesa"):
        tes.borrar_cheque(1, "x@y.com")
