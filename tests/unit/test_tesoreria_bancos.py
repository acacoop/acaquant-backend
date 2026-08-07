from datetime import date

from api.services import tesoreria as tes


def _patch_base(monkeypatch):
    monkeypatch.setattr(tes, "marcar_presencia", lambda email: None)
    monkeypatch.setattr(tes, "registrar_cuentas", lambda vistas, dia: None)
    monkeypatch.setattr(tes, "_saldos_dia", lambda dia: {})
    monkeypatch.setattr(tes, "ingresos_echeq_dia", lambda dia: {})
    monkeypatch.setattr(tes, "mercados_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "banco_a_banco_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "registros_por_banco", lambda dia: {})
    monkeypatch.setattr(tes, "catalogo", lambda: {("BANCO A", "ARS")})
    # La grilla saca las claves del catálogo de `listar_cuentas()` (una sola lectura de
    # tesoreria_cuentas por request). Sin este mock la llamada iba a la base real y el
    # test pasaba de casualidad, por el try/except que devuelve [] cuando falla.
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [
        {"cuenta_operativa": "BANCO A", "unidad": "ARS", "activa": True},
    ])
    monkeypatch.setattr(tes, "_exclusiones_dia", lambda dia: {})
    monkeypatch.setattr(tes, "_items_sql", lambda sql, params: [])


def test_bancos_suma_cheques_emitidos_t1_en_egresos_echeq(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [{
        "id": "20260806103000",
        "estado": "Procesado",
        "solicitud": "Extracción",
        "tipoDocSoli": "[E CHEQ] E CHEQ",
        "monto": 20,
        "unidad": "ARS",
        "cuentaOperativa": {"denominacion": "BANCO A", "id": "1"},
    }])
    monkeypatch.setattr(tes, "_cheques_emitidos_t1_rows", lambda dia: [{
        "banco": "BANCO A",
        "unidad": "ARS",
        "cantidad": 2,
        "total": 80,
    }])

    out = tes.ingresos_egresos_dia(fecha="2026-08-06", email="")

    assert out["cuentas"] == [{
        "cuenta_operativa": "BANCO A",
        "unidad": "ARS",
        "ingresos": 0.0,
        "ingresos_echeq": 0.0,
        "egresos": 0.0,
        "egresos_echeq": 100.0,
        "mercados": 0.0,
        "fci": 0.0,
        "bb_mas": 0.0,
        "bb_menos": 0.0,
        "neto": 0.0,
        "n": 1,
        "saldo_cargado": False,
        "saldo_inicial": 0.0,
        "saldo_final": -100.0,
        "saldo_por": None,
        "saldo_at": None,
    }]


def test_detalle_egresos_echeq_compacta_cheques_emitidos_t1(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_t1_rows", lambda dia: [{
        "banco": "BANCO A",
        "unidad": "ARS",
        "cantidad": 2,
        "total": 80,
    }])

    out = tes.detalle_celda(
        fecha="2026-08-06",
        banco="BANCO A",
        unidad="ARS",
        fila="egresos_echeq",
        email="",
    )

    assert out["total"] == 80.0
    assert out["excluidos"] == 0
    assert out["items"] == [{
        "fuente": "cheque",
        "ref": "emitidos_t1|BANCO A|ARS",
        "detalle": "cheques emitidos T-1",
        "referencia": "2 cheques · fecha de pago anterior al día",
        "estado": "emitido",
        "importe": 80.0,
        "excluido": False,
        "observacion": "",
    }]


def test_bancos_muestra_banco_con_solo_cheques_emitidos_t1(monkeypatch):
    _patch_base(monkeypatch)
    # Catálogo VACÍO: el banco tiene que aparecer por los cheques, no por el catálogo
    # (si no, el test pasaría igual sin que la fuente nueva sume la clave).
    monkeypatch.setattr(tes, "catalogo", lambda: set())
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [])
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_t1_rows", lambda dia: [{
        "banco": "BANCO A",
        "unidad": "ARS",
        "cantidad": 1,
        "total": 80,
    }])

    out = tes.ingresos_egresos_dia(fecha="2026-08-06", email="")

    assert [c["cuenta_operativa"] for c in out["cuentas"]] == ["BANCO A"]
    assert out["cuentas"][0]["egresos_echeq"] == 80.0


# ── El detalle de celda usa el MISMO pedido a Aunesa que la grilla ────────────
#
# `_detalle_dia` pedía a Aunesa solo el estado efectivo: otra llamada HTTP (~1.6s
# medidos en el Droplet) con distinta cache key, así que abrir el detalle volvía a
# pagarla aunque el poll acabara de traer esos mismos movimientos. Ahora pide TODOS
# los estados —igual que la grilla— y descarta el resto en Python. Estos tests fijan
# las dos mitades de ese cambio: que pida lo mismo, y que siga contando solo lo
# efectivo (si se colara un Rechazado, el detalle mostraría plata que no se movió).

def _cruda(id_, estado, monto):
    return {"id": id_, "estado": estado, "solicitud": "Depósito", "tipoDocSoli": "[TR] Transferencia",
            "monto": monto, "unidad": "ARS", "cuentaOperativa": {"denominacion": "BANCO A", "id": "1"}}


def test_detalle_pide_a_aunesa_todos_los_estados(monkeypatch):
    _patch_base(monkeypatch)
    pedidos = []

    def _spy(dia, estado):
        pedidos.append(estado)
        return []

    monkeypatch.setattr(tes, "traer_crudas", _spy)
    tes._detalle_dia(date(2026, 8, 6))

    assert pedidos == [tes.TODOS_ESTADOS], "el detalle tiene que reusar el pedido de la grilla"


def test_detalle_solo_cuenta_los_movimientos_procesados(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [
        _cruda("20260806103000", tes.ESTADO_EFECTIVO, 100),
        _cruda("20260806104000", "Rechazado", 500),
        _cruda("20260806105000", "Anulado", 700),
    ])

    celda = tes._detalle_dia(date(2026, 8, 6))[tes.clave_celda("BANCO A", "ARS", "ingresos")]

    assert celda["total"] == 100.0
    assert [i["ref"] for i in celda["items"]] == ["20260806103000"]
