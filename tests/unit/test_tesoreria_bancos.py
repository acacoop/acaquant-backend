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


def test_los_cheques_emitidos_con_fecha_de_pago_de_HOY_entran(monkeypatch):
    """El corte es `<=`, no `<`: un cheque que se paga HOY se debita HOY.

    Con `<` el banco no lo veía hasta el día siguiente y su saldo quedaba inflado
    (BANCO PATAGONIA COMÚN, 2026-08-10), además de contradecir al total "impacta hoy"
    del tablero EMITIDOS, que sí incluye los del día.
    """
    visto: dict[str, str] = {}
    monkeypatch.setattr(tes, "_items_sql",
                        lambda sql, params: visto.update(sql=sql) or [])

    tes._cheques_emitidos_vencidos_rows(date(2026, 8, 10))

    assert "fecha_pago <= %(d)s" in visto["sql"]


def test_bancos_suma_cheques_emitidos_vencidos_en_egresos_echeq(monkeypatch):
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
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [{
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


def test_detalle_egresos_echeq_compacta_cheques_emitidos_vencidos(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [{
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
        "detalle": "cheques emitidos vencidos",
        "referencia": "2 cheques · fecha de pago anterior al día",
        "estado": "emitido",
        "importe": 80.0,
        "excluido": False,
        "observacion": "",
    }]


def test_bancos_muestra_banco_con_solo_cheques_emitidos_vencidos(monkeypatch):
    _patch_base(monkeypatch)
    # Catálogo VACÍO: el banco tiene que aparecer por los cheques, no por el catálogo
    # (si no, el test pasaría igual sin que la fuente nueva sume la clave).
    monkeypatch.setattr(tes, "catalogo", lambda: set())
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [])
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [{
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


# ── Aunesa caído: la vista degrada, NO devuelve 502 ──────────────────────────

def test_si_aunesa_se_cae_la_vista_igual_responde(monkeypatch):
    """Incidente 2026-08-07/09: Aunesa devolvió HTTP 500 durante dos días y la
    Tesorería entera tiraba 502.

    Aunesa es una dependencia EXTERNA y se va a volver a caer. Lo que no puede pasar
    es que se lleve puesta la vista completa: los saldos, cheques, mercados, banco a
    banco, registros manuales y VEPs viven en Postgres y están disponibles. La grilla
    se arma con lo que hay y DICE que le falta Aunesa.
    """
    _patch_base(monkeypatch)

    def _explota(dia, estado):
        raise RuntimeError("500 Server Error: aca.aunesa.com/Irmo/api/login")

    monkeypatch.setattr(tes, "traer_crudas", _explota)
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [
        {"banco": "BANCO A", "unidad": "ARS", "cantidad": 1, "total": 80}])

    out = tes.ingresos_egresos_dia(fecha="2026-08-06", email="")

    assert out["aunesa_ok"] is False, "la vista tiene que declarar que le falta Aunesa"
    assert "500" in (out["aunesa_error"] or ""), "y decir por qué"
    # Lo que NO depende de Aunesa sigue estando: sin esto el back office se queda sin
    # nada de lo que cargó a mano, que es lo que más duele.
    assert out["cuentas"][0]["egresos_echeq"] == 80.0


def test_con_aunesa_sano_la_marca_dice_que_esta_todo(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [])

    out = tes.ingresos_egresos_dia(fecha="2026-08-06", email="")

    assert out["aunesa_ok"] is True
    assert out["aunesa_error"] is None


# ── Bancos que están en la GRILLA y no en el ABM (incidente 2026-08-10) ───────
#
# La grilla es la UNIÓN de (catálogo activo) ∪ (lo que aparece hoy en alguna fuente);
# el ABM lista solo `activa = true`. En el medio quedaban dos estados que ninguna
# pantalla mostraba, y por eso un banco de alta MANUAL desapareció del catálogo sin
# que nadie se enterara: seguía ocupando su columna en la grilla.

def test_un_banco_de_una_fuente_sin_fila_en_el_catalogo_se_declara(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "catalogo", lambda: set())
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [])
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [])
    # Su único rastro es un registro manual: no lo trae ninguna fuente automática.
    monkeypatch.setattr(tes, "registros_por_banco", lambda dia: {
        ("BANCO MANUAL", "ARS"): {"ingresos": 0.0, "egresos": 50.0, "n": 1}})

    out = tes.ingresos_egresos_dia(fecha="2026-08-10", email="")

    assert [c["cuenta_operativa"] for c in out["cuentas"]] == ["BANCO MANUAL"]
    assert out["fuera_catalogo"] == [
        {"cuenta_operativa": "BANCO MANUAL", "unidad": "ARS", "motivo": "sin_catalogo"}]


def test_un_banco_dado_de_baja_que_sigue_operando_se_declara(monkeypatch):
    """`registrar_cuentas` NO revive la fila (su UPDATE no toca `activa`), así que sin
    esto el banco se quedaba fuera del ABM para siempre."""
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "catalogo", lambda: set())
    monkeypatch.setattr(tes, "listar_cuentas", lambda: [
        {"cuenta_operativa": "BANCO A", "unidad": "ARS", "activa": False}])
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [
        {"banco": "BANCO A", "unidad": "ARS", "cantidad": 1, "total": 80}])

    out = tes.ingresos_egresos_dia(fecha="2026-08-10", email="")

    assert out["fuera_catalogo"] == [
        {"cuenta_operativa": "BANCO A", "unidad": "ARS", "motivo": "dado_de_baja"}]


def test_un_banco_activo_y_en_el_catalogo_no_se_declara(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tes, "traer_crudas", lambda dia, estado: [])
    monkeypatch.setattr(tes, "_cheques_emitidos_vencidos_rows", lambda dia: [
        {"banco": "BANCO A", "unidad": "ARS", "cantidad": 1, "total": 80}])

    out = tes.ingresos_egresos_dia(fecha="2026-08-10", email="")

    assert out["fuera_catalogo"] == []


def test_borrar_un_banco_mira_TODAS_las_tablas_que_lo_referencian():
    """Faltaban `tesoreria_registros` y `tesoreria_veps`: un banco de alta manual con
    solo registros contaba 0 referencias, así que «borrar» lo eliminaba FÍSICAMENTE y,
    como no lo trae Aunesa, no volvía nunca — mientras sus registros lo seguían
    metiendo en la grilla."""
    tablas = {t for t, _ in tes._REFS_CUENTA}

    assert "operaciones.tesoreria_registros" in tablas
    assert "operaciones.tesoreria_veps" in tablas
