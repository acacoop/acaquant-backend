"""CONTROL TÍTULOS NEGATIVOS — la vista no puede mentir en las dos direcciones.

Un control de descubiertos falla de dos formas, y las dos son caras:

  · FALSO NEGATIVO — no muestra un título que sí está negativo. El back office
    cree que está todo bien y el descubierto aparece en la conciliación.
  · FALSO POSITIVO — muestra efectivo negativo como si fuera un título. Es ruido
    que entrena a ignorar la pantalla, y una pantalla que se ignora no controla.

Estos tests fijan el predicado que separa una cosa de la otra, y que la respuesta
diga SIEMPRE cuán viejo es el dato — una lista vacía con el daemon parado no es
"no hay negativos", es "no sabemos", y eso tiene que poder distinguirse.
"""
from __future__ import annotations

from datetime import date, datetime

from api.services import titulos_negativos as svc


def _fake_q(filas: list[dict], capturadas: list):
    """_q falso: guarda (sql, params) y devuelve `filas` para la query de datos.

    La segunda query del service es la de metadata (fecha + actualizado_at), que
    se reconoce por el `count(`.
    """
    def _q(sql, params=None):
        capturadas.append((sql, params or {}))
        if "count(" in sql:
            return [{"fecha": date(2026, 8, 12),
                     "ult": datetime(2026, 8, 12, 15, 30),
                     "cuentas": 97, "filas": 799}]
        return filas
    return _q


def _fila(**kw) -> dict:
    base = {
        "id_cuenta": "805", "cuenta": "[805] CLIENTE", "unidad": "[9422] AO29",
        "ticker": "AO29", "cartera": "HD", "cantidad": -1000.0, "precio": 143.0,
        "valuacion": -1430.0, "moneda": "ARS", "aum": "si",
        "actualizado_at": datetime(2026, 8, 12, 15, 30), "desde_consultado": date(2026, 8, 13),
        "origen": "boleto",
    }
    return {**base, **kw}


def _correr(monkeypatch, filas, **kw) -> tuple[dict, list]:
    cap: list = []
    monkeypatch.setattr(svc, "_q", _fake_q(filas, cap))
    from api.cache import invalidate
    invalidate("titulos_negativos")
    return svc.titulos_negativos(**kw), cap


# ── el predicado de efectivo ─────────────────────────────────────────────────
def test_por_default_el_efectivo_queda_afuera(monkeypatch):
    """Un ARS en descubierto es un problema del banco, no de custodia. Si entra
    acá, la pantalla se llena de ruido y se deja de mirar."""
    _, cap = _correr(monkeypatch, [])
    sql, params = cap[0]
    assert "upper(coalesce(cartera, ''))" in sql, "no filtra por cartera"
    assert params.get("cash") == ["MONEDAS", "MONEDA"]
    # El patrón del LIKE va como PARÁMETRO: un `%` LITERAL en el SQL lo toma
    # psycopg como placeholder y la query revienta en runtime. Se sacan los
    # placeholders bien formados (`%(x)s`) y no puede quedar ningún `%` suelto.
    assert params.get("pfx") == "[%"
    import re
    sin_ph = re.sub(r"%\(\w+\)s", "", sql)
    assert "%" not in sin_ph, "quedó un % literal en el SQL — psycopg lo lee como placeholder"


def test_el_toggle_de_monedas_saca_el_filtro(monkeypatch):
    _, cap = _correr(monkeypatch, [], incluir_monedas=True)
    sql, params = cap[0]
    assert "cartera" not in sql.split("WHERE")[1], "siguió filtrando efectivo"
    assert params == {}


def test_sin_cartera_pero_con_corchete_igual_cuenta_como_titulo(monkeypatch):
    """La red del predicado: si el asset no tiene `cartera` cargada, el corchete
    de la unidad (`[9422] AO29`) lo salva. Sin eso, un título mal clasificado
    desaparecería del control — falso negativo, el peor de los dos."""
    sql, _ = _correr(monkeypatch, [])[1][0]
    # NOT (es_cash_por_cartera OR no_tiene_corchete) → hace falta que FALLEN las
    # dos para que la fila entre. Un título sin cartera pasa por el corchete.
    assert "NOT (" in sql and "NOT LIKE" in sql


# ── solo_aum ─────────────────────────────────────────────────────────────────
def test_por_default_NO_filtra_por_aum(monkeypatch):
    """El flag `aum` excluye contrapartes, FCI, OTC y cash de cuentas propias.
    Un nominal negativo ahí sigue siendo un descubierto: filtrarlo por default
    escondería justo las cuentas operativas."""
    _, cap = _correr(monkeypatch, [])
    assert "aum = 'si'" not in cap[0][0]


def test_solo_aum_prendido_si_filtra(monkeypatch):
    _, cap = _correr(monkeypatch, [], solo_aum=True)
    assert "aum = 'si'" in cap[0][0]


# ── siempre T0, nunca T1 ─────────────────────────────────────────────────────
def test_siempre_pide_t0(monkeypatch):
    """T1 mete lo concertado hoy que todavía no liquidó: una venta de hoy
    aparecería restando aunque el título siga en custodia → falso positivo
    sistemático. El control es sobre lo que se puede ENTREGAR."""
    resp, cap = _correr(monkeypatch, [])
    assert "horizonte = 't0'" in cap[0][0]
    assert resp["horizonte"] == "t0"


# ── la frescura viaja SIEMPRE ────────────────────────────────────────────────
def test_sin_negativos_igual_informa_la_frescura(monkeypatch):
    """El caso bueno (lista vacía) es indistinguible del caso malo (daemon
    parado) si no viaja el timestamp. Por eso la metadata se pide aparte y no
    se deriva de las filas."""
    resp, _ = _correr(monkeypatch, [])
    assert resp["n"] == 0
    assert resp["negativos"] == []
    assert resp["actualizado_at"] == "2026-08-12T15:30:00"
    assert resp["fecha"] == "2026-08-12"
    assert resp["cuentas_en_posicion"] == 97


def test_la_fila_sale_completa_y_ordenada_por_query(monkeypatch):
    resp, cap = _correr(monkeypatch, [_fila(), _fila(unidad="[8032] MSFT", ticker="MSFT",
                                            cantidad=-5.0)])
    assert "ORDER BY cantidad ASC" in cap[0][0], "el más negativo tiene que ir primero"
    f = resp["negativos"][0]
    assert f["ticker"] == "AO29"
    assert f["cantidad"] == -1000.0
    assert f["actualizado_at"] == "2026-08-12T15:30:00"
    assert f["desde_consultado"] == "2026-08-13"
    assert resp["n"] == 2


def test_cuenta_vacia_no_deja_la_fila_sin_etiqueta(monkeypatch):
    """Sin `cuenta` la fila sería una tabla de números sin dueño — el back
    office necesita saber a quién llamar."""
    resp, _ = _correr(monkeypatch, [_fila(cuenta=None)])
    assert resp["negativos"][0]["cuenta"] == "[805]"
