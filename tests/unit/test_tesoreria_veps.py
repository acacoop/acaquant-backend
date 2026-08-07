"""Tests de la tab VEPS de Tesorería.

Lo que fijan, en orden de lo que dolería si se rompe:

1. Un VEP NO puede tocar el saldo de BANCOS. El egreso ya entra por REGISTROS
   MANUALES (tipo 'VEP'); si además sumara desde acá, el mismo VEP se contaría dos
   veces y la grilla mostraría plata que no salió.
2. La marca AMARILLA (`vencido`) la decide el backend, no el navegador: vencimiento
   pasado Y sin pagar. Un VEP pagado no se marca aunque haya vencido.
3. El espejo desde un registro manual se crea SOLO para tipo VEP, y no puede
   voltear la carga del registro si falla.
"""
from __future__ import annotations

from datetime import date

from api.services import tesoreria as tes


def _row(**kw) -> dict:
    base = {"id": 1, "numero_vep": "123", "concepto": "IVA", "importe": 1000,
            "banco": "AL2", "unidad": "ARS", "vencimiento": None, "estado": "pendiente",
            "origen": "manual", "registro_id": None, "pagado_at": None,
            "creado_por": "x@y.com", "creado_at": None}
    return base | kw


HOY = date(2026, 8, 7)


# ── 1) el saldo de BANCOS no se entera de los VEPs ────────────────────────────

def test_los_veps_no_entran_al_saldo_de_bancos():
    """Ninguna fuente de la grilla puede leer la tabla de VEPs. Es la garantía de
    que no hay doble conteo con el registro manual que sí mueve el saldo."""
    import inspect

    fuentes = (tes.ingresos_egresos_dia, tes.registros_por_banco, tes.mercados_por_banco,
               tes.banco_a_banco_por_banco, tes.ingresos_echeq_dia,
               tes._cheques_emitidos_t1_rows, tes._detalle_dia)
    for fn in fuentes:
        assert tes._TABLA_VEPS not in inspect.getsource(fn), (
            f"{fn.__name__} lee la tabla de VEPs — eso duplicaría el egreso del "
            "registro manual en el saldo final")


# ── 2) la marca amarilla ──────────────────────────────────────────────────────

def test_vencido_es_vencimiento_pasado_y_sin_pagar():
    v = tes._fila_vep(_row(vencimiento=date(2026, 8, 6)), HOY)
    assert v["vencido"] is True


def test_el_vencimiento_de_HOY_todavia_no_esta_vencido():
    """Vence hoy = todavía se puede pagar. Se marca recién cuando pasó el día."""
    v = tes._fila_vep(_row(vencimiento=HOY), HOY)
    assert v["vencido"] is False


def test_un_vep_PAGADO_no_se_marca_aunque_haya_vencido():
    v = tes._fila_vep(_row(vencimiento=date(2026, 1, 1), estado="pagado"), HOY)
    assert v["vencido"] is False


def test_sin_vencimiento_cargado_no_se_marca():
    """El espejo del registro manual nace sin vencimiento: no puede salir amarillo
    solo por estar incompleto."""
    v = tes._fila_vep(_row(vencimiento=None), HOY)
    assert v["vencido"] is False


# ── 3) el espejo desde REGISTROS MANUALES ─────────────────────────────────────

def _patch_registro(monkeypatch, espejados: list):
    monkeypatch.setattr(tes, "puede_editar_saldo", lambda a: True)
    monkeypatch.setattr(tes, "_validar_registro", lambda d: {
        "fecha": HOY, "tipo": d["tipo"], "banco": "AL2", "unidad": "ARS",
        "importe": 500.0, "sentido": "egreso", "grupo": "rescate"})
    monkeypatch.setattr(tes, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(tes, "espejar_vep_de_registro",
                        lambda rid, reg, actor: espejados.append((rid, reg["tipo"])) or 77)

    class _Cur:
        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return [42]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self, *a, **k):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    monkeypatch.setattr(tes, "get_pool", lambda: _Pool())


def test_un_registro_manual_VEP_crea_el_espejo(monkeypatch):
    espejados: list = []
    _patch_registro(monkeypatch, espejados)

    out = tes.crear_registro({"tipo": "VEP"}, "x@y.com")

    assert espejados == [(42, "VEP")]
    assert out["vep_id"] == 77


def test_un_registro_manual_de_OTRO_tipo_no_crea_espejo(monkeypatch):
    espejados: list = []
    _patch_registro(monkeypatch, espejados)

    out = tes.crear_registro({"tipo": "PROVEEDORES"}, "x@y.com")

    assert espejados == []
    assert out["vep_id"] is None


def test_si_el_espejo_falla_el_registro_manual_igual_se_guarda(monkeypatch):
    """El registro manual es el que mueve el saldo: no puede caerse porque falló
    una fila de seguimiento."""
    _patch_registro(monkeypatch, [])

    def _explota(rid, reg, actor):
        raise RuntimeError("boom")

    monkeypatch.setattr(tes, "espejar_vep_de_registro", _explota)
    # `espejar_vep_de_registro` atrapa sus propias excepciones; acá se fuerza el peor
    # caso (que ni eso funcione) para probar que el alta no se pierde.
    try:
        out = tes.crear_registro({"tipo": "VEP"}, "x@y.com")
    except RuntimeError:
        raise AssertionError("el registro manual se perdió por culpa del espejo") from None
    assert out["id"] == 42
