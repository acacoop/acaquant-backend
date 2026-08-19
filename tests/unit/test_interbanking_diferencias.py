"""DIFERENCIAS: ¿la variación del saldo está explicada por sus movimientos?

La cuenta que tiene que dar:

    cierre(hoy) − cierre(día anterior)  ==  Σ movimientos de hoy

Lo que sobra es la **diferencia sin explicar**, y tiene una causa concreta que el
back office ya conocía: **el banco a veces registra un movimiento con fecha de
ANTEAYER que recién impacta en el saldo de AYER**. El movimiento queda en un día
que ya cerramos y el salto aparece en el otro.

⚠️ Esto falla en silencio si se hace mal: una diferencia inventada manda al back
office a buscar un movimiento que no existe, y una que no se detecta deja pasar
plata sin justificar. Por eso los casos límite —falta un cierre, falta el día
anterior, hay ajustes manuales— tienen test propio.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import bancos as svc

HOY = date(2026, 8, 18)
AYER = date(2026, 8, 14)


def _cuenta(**kw):
    return {"id": 1, "bank_number": "034", "bank_name": "Patagonia",
            "account_number": "300", "account_type": "CC", "currency": "ARS",
            "account_label": "ACA", "activa": True, "origen": "interbanking", **kw}


def _mock(monkeypatch, *, extractos=(), saldos=(), movs=(), manuales=(), previa=AYER):
    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "max(fecha)" in t:
            return [{"f": previa}]
        if "FROM bancos.cuentas" in t:
            return [_cuenta()]
        if "FROM bancos.extracto_dia" in t:
            return list(extractos)
        if "FROM bancos.saldos" in t:
            return list(saldos)
        if "movimientos_manuales" in t:
            return list(manuales)
        if "FROM bancos.movimientos" in t:
            return list(movs)
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    return svc


def _fila(out):
    return out["filas"][0]


def test_un_dia_que_cierra_no_tiene_diferencia(monkeypatch):
    """El caso normal: el saldo se movió exactamente lo que dicen sus
    movimientos. Si esto diera distinto de cero, TODA la pantalla sería ruido."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": 1000.0,
                          "saldo_cierre": 1300.0, "cierra": True},
                         {"cuenta_id": 1, "fecha": AYER, "saldo_apertura": 0.0,
                          "saldo_cierre": 1000.0, "cierra": True}],
              movs=[{"cuenta_id": 1, "neto": 300.0, "n": 2}])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["variacion"] == 300.0
    assert f["sin_explicar"] == 0.0
    assert f["salto_apertura"] == 0.0


def test_el_asiento_RETROACTIVO_aparece_como_diferencia(monkeypatch):
    """El caso que motivó la pantalla. El banco cerró AYER en 0 y abrió HOY en
    100.000 sin un movimiento de hoy que lo explique: el asiento lo registró con
    fecha de ayer, un día que nosotros ya habíamos cerrado."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": 100_000.0,
                          "saldo_cierre": 100_000.0, "cierra": True},
                         {"cuenta_id": 1, "fecha": AYER, "saldo_apertura": 0.0,
                          "saldo_cierre": 0.0, "cierra": True}],
              movs=[])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["variacion"] == 100_000.0
    assert f["movimientos"] == 0.0
    assert f["sin_explicar"] == 100_000.0


def test_la_diferencia_ES_el_salto_entre_cierre_y_apertura(monkeypatch):
    """La propiedad que hace útil a esta pantalla: cuando el día cierra bien
    contra sus propios movimientos, la diferencia sin explicar es EXACTAMENTE el
    salto entre el cierre de un día y la apertura del siguiente. O sea que la
    pantalla no solo dice cuánto falta: dice dónde mirar."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": 750.0,
                          "saldo_cierre": 900.0, "cierra": True},
                         {"cuenta_id": 1, "fecha": AYER, "saldo_apertura": 0.0,
                          "saldo_cierre": 500.0, "cierra": True}],
              movs=[{"cuenta_id": 1, "neto": 150.0, "n": 1}])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["sin_explicar"] == 250.0
    assert f["salto_apertura"] == 250.0


def test_sin_cierre_de_alguno_de_los_dos_dias_NO_se_inventa_una_diferencia(monkeypatch):
    """«No sabemos» no es «no se movió». Si se asumiera cero, la diferencia daría
    del tamaño del saldo entero y mandaría al back office a buscar un movimiento
    que no existe."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": None,
                          "saldo_cierre": 900.0, "cierra": None}],
              movs=[])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["cierre_previo"] is None
    assert f["variacion"] is None
    assert f["sin_explicar"] is None


def test_el_cierre_cae_a_bancos_saldos_igual_que_el_consolidado(monkeypatch):
    """Una cuenta QUIETA no tiene extracto pero el banco igual informa su saldo.
    Si acá eligiera otra fuente que el consolidado, dos pantallas dirían dos
    saldos para el mismo día."""
    s = _mock(monkeypatch,
              saldos=[{"cuenta_id": 1, "fecha": HOY, "saldo": 500.0},
                      {"cuenta_id": 1, "fecha": AYER, "saldo": 500.0}],
              movs=[])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["cierre"] == 500.0
    assert f["sin_explicar"] == 0.0


def test_los_manuales_NO_ensucian_la_conciliacion(monkeypatch):
    """Se reconcilia contra el BANCO. Un ajuste nuestro mueve el saldo que
    mostramos pero no existe para el banco: si entrara, cada movimiento manual
    aparecería como una diferencia del banco."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": 0.0,
                          "saldo_cierre": 0.0, "cierra": True},
                         {"cuenta_id": 1, "fecha": AYER, "saldo_apertura": 0.0,
                          "saldo_cierre": 0.0, "cierra": True}],
              movs=[],
              manuales=[{"cuenta_id": 1, "ajuste": 999.0, "n": 1}])
    f = _fila(s.diferencias("x@y", HOY))
    assert f["sin_explicar"] == 0.0, "el ajuste manual no es una diferencia del banco"
    assert f["ajuste_manual"] == 999.0, "pero se muestra, para que nadie se confunda"


def test_sin_dia_anterior_lo_dice_en_vez_de_comparar_contra_nada(monkeypatch):
    """La base retiene 3 fechas: esto pasa el primer día o si la ingesta viene
    fallando. Es un dato, no un error."""
    s = _mock(monkeypatch, previa=None)
    out = s.diferencias("x@y", HOY)
    assert out["sin_previa"] is True
    assert out["filas"] == []


@pytest.mark.parametrize("neto,cierre,esperado", [
    (-2_000_000_000.0 + 2_000_000_000.0, 0.0, 0.0),   # el caso de la pantalla
    (500.0, 500.0, 0.0),
])
def test_los_importes_grandes_no_arrastran_error_de_redondeo(monkeypatch, neto,
                                                             cierre, esperado):
    """Los movimientos de 2.000 millones que se cancelan entre sí tienen que dar
    CERO pelado. Un centavo de resto acá se lee como una diferencia real."""
    s = _mock(monkeypatch,
              extractos=[{"cuenta_id": 1, "fecha": HOY, "saldo_apertura": 0.0,
                          "saldo_cierre": cierre, "cierra": True},
                         {"cuenta_id": 1, "fecha": AYER, "saldo_apertura": 0.0,
                          "saldo_cierre": 0.0, "cierra": True}],
              movs=[{"cuenta_id": 1, "neto": neto, "n": 2}])
    assert _fila(s.diferencias("x@y", HOY))["sin_explicar"] == esperado
