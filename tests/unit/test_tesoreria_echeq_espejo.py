"""Espejo automático: e-cheq de EGRESO de Aunesa → cheque EMITIDO de la tab CHEQUES.

Un egreso con RIEL '[E CHEQ] E CHEQ' ES un cheque emitido. El back office lo veía en
MOVIMIENTOS y lo volvía a cargar a mano en la tab CHEQUES, y el saldo lo restaba DOS
veces: el movimiento por un lado, el cheque manual por el otro.

El espejo lo arregla creando esa fila SOLO COMO REGISTRO: aparece en la tab para que el
equipo la vea y no la cargue, pero NO suma al saldo — esa plata ya la puso el movimiento
e-cheq, que cuenta normal. Estos tests congelan las dos mitades.
"""

from datetime import date

import pytest

from api.services import tesoreria as tes

DIA = date(2026, 8, 10)
BANCO = "BANCO COMAFI ACDI ARS"
MOV_ID = "20260810162000"


def _mov(**kw) -> dict:
    """El e-cheq de egreso tal como lo manda Aunesa (fila real del 10/08/2026)."""
    base = {
        "id": MOV_ID,
        "fecha": "10/08/2026",
        "estado": tes.ESTADO_EFECTIVO,
        "solicitud": "Extracción",
        "tipoDocSoli": "[E CHEQ] E CHEQ",
        "cuenta": "1632",
        "cuentaOperativa": {"denominacion": BANCO, "id": "9"},
        "unidad": "ARS",
        "monto": 5_000_000,
        "persona": {"nombreCompleto": "DON TINCHO SRL", "cuit": "30-70937992-1"},
    }
    return base | kw


def _patch_grilla(monkeypatch):
    """Grilla BANCOS con todas las fuentes en cero: lo único que se mueve en estos
    tests son los e-cheq y sus cheques."""
    monkeypatch.setattr(tes, "marcar_presencia", lambda email: None)
    monkeypatch.setattr(tes, "registrar_cuentas", lambda vistas, dia: None)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_saldos_dia", lambda dia: {})
    monkeypatch.setattr(tes, "ingresos_echeq_dia", lambda dia: {})
    monkeypatch.setattr(tes, "mercados_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "banco_a_banco_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "registros_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "catalogo", lambda: {(BANCO, "ARS")})
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [
        {"cuenta_operativa": BANCO, "unidad": "ARS", "activa": True},
    ])
    monkeypatch.setattr(tes, "_exclusiones_dia", lambda dia: {})
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [])
    monkeypatch.setattr(tes, "_items_sql", lambda sql, params: [])


def _echeq(cuentas: list[dict]) -> float:
    return next(c for c in cuentas if c["cuenta_operativa"] == BANCO)["egresos_echeq"]


# ── 1) el espejo: qué fila nace ──────────────────────────────────────────────

def test_el_echeq_de_egreso_se_espeja_con_todos_los_datos():
    """La fila tiene que quedar tan completa como una cargada a mano: si le falta el
    CUIT o el comitente, el back office igual tiene que ir a completarla y no le
    ahorramos nada."""
    filas = tes._filas_espejo_echeq(DIA, [_mov()])

    assert len(filas) == 1
    f = filas[0]
    assert f["mov"] == MOV_ID
    assert f["comitente"] == "1632"
    assert f["denom"] == "DON TINCHO SRL"
    # Sin guiones, como los que carga el back office a mano.
    assert f["cuit"] == "30709379921"
    assert f["banco"] == BANCO
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


# ── 2) la plata resta UNA vez ────────────────────────────────────────────────

def test_el_cheque_espejo_es_SOLO_REGISTRO_y_no_suma_al_saldo(monkeypatch):
    """El corazón del arreglo: el e-cheq YA restó por su movimiento, que estaba bien
    contado. El cheque espejo existe para que quede el REGISTRO en la tab CHEQUES y
    nadie lo cargue a mano — si además sumara, el banco restaría el doble."""
    _patch_grilla(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [_mov()])

    out = tes.ingresos_egresos_dia(fecha=DIA.isoformat(), email="")

    assert _echeq(out["cuentas"]) == 5_000_000.0


def test_el_renglon_de_cheques_de_la_celda_suma_solo_los_manuales(monkeypatch):
    """Lo único que el renglón de cheques agrega al saldo es lo que el equipo cargó a
    mano (más los vencidos que arrastra). El SQL filtra `origen='manual'`: si dejara
    entrar los espejo, cada e-cheq del día se contaría dos veces."""
    visto: dict[str, str] = {}
    monkeypatch.setattr(tes, "_items_sql", lambda sql, params: visto.update(sql=sql) or [])

    tes._cheques_emitidos_vencidos_rows(DIA)

    assert f"origen = '{tes.ORIGEN_MANUAL}'" in visto["sql"]


# ── 3) el detalle partido en dos ─────────────────────────────────────────────

def test_el_detalle_separa_los_cheques_vencidos_de_los_del_dia(monkeypatch):
    """Un solo renglón "cheques emitidos vencidos" mezclaba el arrastre con la carga del
    día: el back office no podía ver de dónde salía cada peso ni tildarlos aparte."""
    _patch_grilla(monkeypatch)
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [
        {"banco": BANCO, "unidad": "ARS", "grupo": "vencido", "cantidad": 1, "total": 30},
        {"banco": BANCO, "unidad": "ARS", "grupo": "hoy", "cantidad": 2, "total": 50},
    ])

    celda = tes._detalle_dia(DIA)[tes.clave_celda(BANCO, "ARS", "egresos_echeq")]

    assert [(i["ref"], i["detalle"], i["importe"]) for i in celda["items"]] == [
        (f"emitidos_t1|{BANCO}|ARS", "cheques emitidos vencidos", 30.0),
        (f"emitidos_hoy|{BANCO}|ARS", "cheques emitidos del día", 50.0),
    ]
    assert celda["total"] == 80.0


def test_el_espejo_no_se_puede_borrar(monkeypatch):
    """Borrarlo no sirve: el próximo poll lo vuelve a crear del mismo movimiento. Se
    cierra con 'completado', que es lo que lo saca de la vista sin perder la fila."""
    monkeypatch.setattr(tes, "puede_editar_saldo", lambda email: True)
    monkeypatch.setattr(tes, "_q", lambda sql, params: [{"origen": tes.ORIGEN_AUNESA}])

    with pytest.raises(ValueError, match="Aunesa"):
        tes.borrar_cheque(1, "x@y.com")
