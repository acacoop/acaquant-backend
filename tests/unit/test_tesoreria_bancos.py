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
    monkeypatch.setattr(tes, "catalogo", lambda: set())
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
