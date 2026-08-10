"""Ajustes manuales de PnL (splits / eventos corporativos) — motor + merge.

Cubre el caso que motivó la feature: un CEDEAR hace split 10:1 SIN boleto →
qty_calc queda pre-split, el valor live se calcula con la cantidad vieja y el
PnL no realizado inventa una pérdida de ~90%. Con el ajuste `split` el motor
multiplica la cantidad viva sin tocar el costo y todo reconcilia.

Todo puro: `_pnl_por_cuenta_core` recibe las deps por kwarg (cero DB) y
`merge_ajustes_en_boletos` es una función pura de pnl_ajustes_sql.
"""
from __future__ import annotations

from api.services.pnl import _pnl_por_cuenta_core
from api.services.pnl_ajustes_sql import merge_ajustes_en_boletos

_UNIDAD = "[9131] YPFD - CEDEAR YPF"
_INSTR = "MERV - XMEV - YPFD - 24hs"
_CUENTA = "255"


def _deps(boletos: list[dict], *, qty_aum: float, precio_live: float) -> dict:
    """Deps mínimas del motor para UNA cuenta con UN ticker RENTA VARIABLE."""
    return {
        "id_cuenta": _CUENTA,
        "unidad_to_match": {_UNIDAD: "YPFD"},
        "match_to_display": {"YPFD": "YPFD"},
        "instrumentos_by_unidad": {_UNIDAD: _INSTR},
        "portfolio_snap_by_ticker": {_INSTR: {"last_price": precio_live,
                                              "closing_price": precio_live}},
        "snapshots_cierre_by_ticker": {},
        "boletos_by_id_cuenta": {_CUENTA: boletos},
        "aum_rows_by_id_cuenta": {_CUENTA: [{
            "id_cuenta": _CUENTA, "unidad": _UNIDAD, "cantidad": qty_aum,
            "precio": precio_live, "valuacion": qty_aum * precio_live,
            "tipoTitulo": "Cedears", "cartera": "RENTA VARIABLE",
        }]},
        "fecha_actual_aum_global": "2026-08-08",
        "mep_hoy": 1000.0,
        "mep_cache": {},
    }


def _compra(fecha: str, cantidad: float, importe_ars: float) -> dict:
    return {"fecha": fecha, "categoria": "compra", "op": "Compra 24hs",
            "ticker": "YPFD", "cantidad": cantidad, "precio": abs(importe_ars) / cantidad,
            "importe": -abs(importe_ars), "moneda": "ARS", "mep": 1000.0,
            "comprobante": "BOL 1"}


def _venta(fecha: str, cantidad: float, importe_ars: float) -> dict:
    return {"fecha": fecha, "categoria": "venta", "op": "Venta 24hs",
            "ticker": "YPFD", "cantidad": cantidad, "precio": importe_ars / cantidad,
            "importe": abs(importe_ars), "moneda": "ARS", "mep": 1000.0,
            "comprobante": "BOL 2"}


def _split(fecha: str, factor: float, ajuste_id: int = 1) -> dict:
    return {"fecha": fecha, "categoria": "ajuste_split", "op": f"SPLIT ×{factor:g}",
            "ticker": "YPFD", "cantidad": None, "precio": None, "importe": None,
            "moneda": "ARS", "mep": None, "comprobante": f"AJUSTE-{ajuste_id}",
            "factor": factor, "id_cuenta": None, "_orden": 0}


def _ajuste_cantidad(fecha: str, delta: float, costo: float | None = None,
                     ajuste_id: int = 1) -> dict:
    return {"fecha": fecha, "categoria": "ajuste_cantidad", "op": "AJUSTE CANTIDAD",
            "ticker": "YPFD", "cantidad": delta, "precio": None,
            "importe": costo, "moneda": "ARS", "mep": None,
            "comprobante": f"AJUSTE-{ajuste_id}", "factor": None,
            "id_cuenta": _CUENTA, "_orden": 0}


def _row(res: dict, ticker: str = "YPFD") -> dict:
    return next(r for r in res["rows"] if r["ticker"] == ticker)


# ── Split ────────────────────────────────────────────────────────────────────

def test_split_repara_cantidad_y_pnl():
    """Compra 100 @ $1.000 (costo 100.000). Split 10:1 → AuM dice 1.000 nominales
    a $100. Sin ajuste el motor valuaría 100 × $100 = 10.000 (pérdida fantasma
    de 90.000). Con el ajuste: qty_calc=1.000, valor=100.000, PnL no real = 0."""
    boletos = [_compra("2026-01-10", 100, 100_000)]

    # Sin ajuste — el bug: pérdida fantasma.
    roto = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=1000, precio_live=100))
    fila_rota = _row(roto)
    assert fila_rota["qty_calc"] == 100
    assert fila_rota["completeness"] == "parcial"
    assert fila_rota["pnl_no_realizado"] == -90_000

    # Con ajuste — reconcilia.
    con_split = _pnl_por_cuenta_core(
        **_deps(boletos + [_split("2026-06-01", 10)], qty_aum=1000, precio_live=100))
    fila = _row(con_split)
    assert fila["qty_calc"] == 1000
    assert fila["qty_efectiva"] == 1000
    assert fila["completeness"] == "completa"
    assert fila["costo_remanente"] == 100_000      # el costo NO cambia
    assert fila["valor_actual_live"] == 100_000    # 1.000 × $100
    assert fila["pnl_no_realizado"] == 0
    assert fila["pnl_realizado"] == 0              # un split no realiza nada
    assert fila["precio_promedio"] == 100          # promedio post-split


def test_split_no_toca_costo_usd():
    boletos = [_compra("2026-01-10", 100, 100_000)]  # mep 1000 → 100 USD
    res = _pnl_por_cuenta_core(
        **_deps(boletos + [_split("2026-06-01", 10)], qty_aum=1000, precio_live=100))
    fila = _row(res)
    assert fila["costo_remanente_usd"] == 100
    assert fila["fechas_sin_mep"] == []            # el split no flagea MEP


def test_reverse_split():
    """Reverse 1:10 (factor 0.1): 100 nominales → 10, costo intacto."""
    boletos = [_compra("2026-01-10", 100, 100_000)]
    res = _pnl_por_cuenta_core(
        **_deps(boletos + [_split("2026-06-01", 0.1)], qty_aum=10, precio_live=10_000))
    fila = _row(res)
    assert fila["qty_calc"] == 10
    assert fila["costo_remanente"] == 100_000
    assert fila["pnl_no_realizado"] == 0           # 10 × 10.000 − 100.000


def test_venta_post_split_usa_promedio_ajustado():
    """Tras el split el promedio es $100: vender 500 @ $100 no realiza nada y
    deja la mitad del costo."""
    boletos = [
        _compra("2026-01-10", 100, 100_000),
        _split("2026-06-01", 10),
        _venta("2026-07-01", 500, 50_000),
    ]
    res = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=500, precio_live=100))
    fila = _row(res)
    assert fila["qty_calc"] == 500
    assert fila["pnl_realizado"] == 0
    assert fila["costo_remanente"] == 50_000
    assert fila["completeness"] == "completa"


def test_split_sobre_qty_cero_es_noop():
    """Sin stock al momento del split, no hay nada que multiplicar (la compra
    posterior ya es post-split)."""
    boletos = [_split("2026-06-01", 10), _compra("2026-07-01", 1000, 100_000)]
    res = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=1000, precio_live=100))
    fila = _row(res)
    assert fila["qty_calc"] == 1000
    assert fila["costo_remanente"] == 100_000


def test_split_audit_muestra_delta_y_factor():
    boletos = [_compra("2026-01-10", 100, 100_000), _split("2026-06-01", 10)]
    res = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=1000, precio_live=100))
    aj = [b for b in _row(res)["boletos"] if b["categoria"] == "ajuste_split"]
    assert len(aj) == 1
    assert aj[0]["cantidad"] == 900     # 100 → 1.000
    assert aj[0]["factor"] == 10


# ── Ajuste de cantidad ───────────────────────────────────────────────────────

def test_ajuste_cantidad_establece_posicion_pre_data():
    """Cuenta sin boletos del ticker: el ajuste +200 con costo 20.000 establece
    posición y cost-basis (caso posiciones anteriores al feed)."""
    boletos = [_ajuste_cantidad("2026-01-02", 200, costo=20_000)]
    res = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=200, precio_live=100))
    fila = _row(res)
    assert fila["qty_calc"] == 200
    assert fila["costo_remanente"] == 20_000
    assert fila["completeness"] == "completa"
    assert fila["pnl_no_realizado"] == 0           # 200 × 100 = 20.000


def test_ajuste_cantidad_negativo_libera_costo_sin_realizar():
    """Canje saliente: −40 nominales liberan 40% del costo, cero realizado."""
    boletos = [_compra("2026-01-10", 100, 100_000),
               _ajuste_cantidad("2026-06-01", -40)]
    res = _pnl_por_cuenta_core(**_deps(boletos, qty_aum=60, precio_live=1000))
    fila = _row(res)
    assert fila["qty_calc"] == 60
    assert fila["costo_remanente"] == 60_000
    assert fila["pnl_realizado"] == 0


# ── Merge de ajustes al stream de boletos ────────────────────────────────────

def test_merge_global_solo_en_cuentas_con_boletos_del_ticker():
    boletos = {
        "1": [_compra("2026-01-10", 100, 100_000)],                       # tiene YPFD
        "2": [{**_compra("2026-01-10", 5, 5_000), "ticker": "GGAL"}],     # no tiene
    }
    out = merge_ajustes_en_boletos(boletos, [_split("2026-06-01", 10)])
    assert [b["categoria"] for b in out["1"]] == ["compra", "ajuste_split"]
    assert [b["categoria"] for b in out["2"]] == ["compra"]


def test_merge_por_cuenta_crea_lista_si_no_hay_boletos():
    """Un ajuste dirigido a UNA cuenta entra aunque la cuenta no tenga boletos
    (así se establece una posición pre-data)."""
    aj = _ajuste_cantidad("2026-01-02", 200, costo=20_000)
    out = merge_ajustes_en_boletos({}, [aj])
    assert out[_CUENTA][0]["categoria"] == "ajuste_cantidad"


def test_merge_ordena_ajuste_antes_de_boletos_del_mismo_dia():
    """El evento es efectivo a la apertura: lo operado ese día ya es post-split."""
    boletos = {"1": [_compra("2026-06-01", 100, 100_000)]}
    out = merge_ajustes_en_boletos(boletos, [_split("2026-06-01", 10)])
    assert [b["categoria"] for b in out["1"]] == ["ajuste_split", "compra"]


def test_merge_sin_ajustes_no_toca_nada():
    boletos = {"1": [_compra("2026-01-10", 100, 100_000)]}
    assert merge_ajustes_en_boletos(boletos, []) is boletos


# ── Alcance de escritura: admin todo, operador SUS cuentas ───────────────────

def _patch_permisos(monkeypatch, *, admin: bool, cuentas: tuple[str, ...]):
    import api.services.pnl_ajustes_sql as svc
    monkeypatch.setattr(svc, "_es_admin", lambda email: admin)
    monkeypatch.setattr(svc, "_cuentas_del_operador", lambda email: cuentas)
    return svc


def test_admin_puede_global_y_cualquier_cuenta(monkeypatch):
    svc = _patch_permisos(monkeypatch, admin=True, cuentas=())
    svc._verificar_alcance("admin@x.com", None)      # global OK
    svc._verificar_alcance("admin@x.com", "999")     # cualquier cuenta OK


def test_operador_solo_sus_cuentas(monkeypatch):
    import pytest
    svc = _patch_permisos(monkeypatch, admin=False, cuentas=("255", "300"))
    svc._verificar_alcance("op@x.com", "255")        # la suya OK
    with pytest.raises(PermissionError):
        svc._verificar_alcance("op@x.com", "999")    # ajena NO
    with pytest.raises(PermissionError):
        svc._verificar_alcance("op@x.com", None)     # global NO (solo admin)


def test_sin_rol_ni_cuentas_no_escribe(monkeypatch):
    svc = _patch_permisos(monkeypatch, admin=False, cuentas=())
    assert svc.puede_escribir("nadie@x.com") is False
