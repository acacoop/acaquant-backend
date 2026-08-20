"""CONTROL TÍTULOS NEGATIVOS — la vista no puede mentir en las dos direcciones.

Un control de descubiertos falla de dos formas, y las dos son caras:

  · FALSO NEGATIVO — no muestra un título que sí está negativo. El back office
    cree que está todo bien y el descubierto aparece en la conciliación.
  · FALSO POSITIVO — muestra como problema algo que es normal (efectivo negativo
    = descubierto bancario; derivado negativo = posición vendida). Es ruido que
    entrena a ignorar la pantalla, y una pantalla que se ignora no controla.

Estos tests fijan el predicado que separa una cosa de la otra, que se pidan los
DOS horizontes, y que la respuesta diga SIEMPRE cuán viejo es el dato — una lista
vacía con el daemon parado no es "no hay negativos", es "no sabemos".
"""
from __future__ import annotations

import re
from datetime import date, datetime

from api.services import titulos_negativos as svc


def _fake_q(por_horizonte: dict[str, list], capturadas: list):
    """_q falso: guarda (sql, params) y responde según el horizonte pedido.

    La query de metadata se reconoce por el `count(`.
    """
    def _q(sql, params=None):
        p = params or {}
        capturadas.append((sql, p))
        if "count(" in sql:
            # `latido` lo agregó la query del meta como subconsulta escalar: si
            # el falso no lo devuelve, el service revienta con KeyError y el test
            # falla por el andamio, no por la lógica que quiere fijar.
            return [{"fecha": date(2026, 8, 12),
                     "ult": datetime(2026, 8, 12, 15, 30),
                     "cuentas": 97,
                     "latido": datetime(2026, 8, 12, 15, 30)}]
        return por_horizonte.get(p.get("h"), [])
    return _q


def _fila(**kw) -> dict:
    base = {
        "id_cuenta": "805", "cuenta": "[805] CLIENTE", "unidad": "[9422] AO29",
        "ticker": "AO29", "cartera": "HD", "cantidad": -1000.0,
        "actualizado_at": datetime(2026, 8, 12, 15, 30),
    }
    return {**base, **kw}


def _correr(monkeypatch, por_horizonte=None, **kw) -> tuple[dict, list]:
    cap: list = []
    monkeypatch.setattr(svc, "_q", _fake_q(por_horizonte or {}, cap))
    from api.cache import invalidate
    invalidate("titulos_negativos")
    return svc.titulos_negativos(**kw), cap


def _sql_de(cap: list, horizonte: str) -> tuple[str, dict]:
    return next((s, p) for s, p in cap if p.get("h") == horizonte)


# ── qué se excluye ───────────────────────────────────────────────────────────
def test_por_default_quedan_afuera_monedas_Y_derivados(monkeypatch):
    """Efectivo negativo = descubierto bancario. Derivado negativo = posición
    vendida, que es la forma NORMAL de representar un short. Ninguno de los dos
    es un título en descubierto; si entran, la pantalla es ruido."""
    _, cap = _correr(monkeypatch)
    sql, params = _sql_de(cap, "t0")
    assert "a.cartera" in sql, "no filtra por cartera"
    assert params["excl"] == ["MONEDAS", "MONEDA", "DERIVADOS", "DERIVADO"]


def test_el_patron_del_like_va_como_parametro(monkeypatch):
    """Un `%` LITERAL en el SQL lo toma psycopg como placeholder y la query
    revienta en runtime. Se sacan los placeholders bien formados (`%(x)s`) y no
    puede quedar ningún `%` suelto."""
    _, cap = _correr(monkeypatch)
    sql, params = _sql_de(cap, "t0")
    assert params["pfx"] == "[%"
    assert "%" not in re.sub(r"%\(\w+\)s", "", sql)


def test_sin_cartera_pero_con_corchete_igual_cuenta_como_titulo(monkeypatch):
    """La red del predicado: si el asset no tiene `cartera` cargada, el corchete
    de la unidad (`[9422] AO29`) lo salva. Sin eso, un título mal clasificado
    desaparecería del control — falso negativo, el peor de los dos."""
    _, cap = _correr(monkeypatch)
    sql, _ = _sql_de(cap, "t0")
    # NOT (es_excluida_por_cartera OR no_tiene_corchete) → hacen falta las DOS
    # para que la fila entre. Un título sin cartera pasa por el corchete.
    assert "NOT (" in sql and "NOT LIKE" in sql


def test_incluir_todo_saca_el_filtro(monkeypatch):
    _, cap = _correr(monkeypatch, incluir_todo=True)
    sql, params = _sql_de(cap, "t0")
    assert "cartera" not in sql.split("WHERE")[1], "siguió filtrando"
    assert "excl" not in params


# ── los DOS horizontes ───────────────────────────────────────────────────────
def test_pide_t0_Y_t1(monkeypatch):
    """T0 responde "¿puedo entregar HOY?" y T1 "¿voy a poder MAÑANA?". Son dos
    preguntas distintas y la vista muestra las dos, una por lado."""
    resp, cap = _correr(monkeypatch, {
        "t0": [_fila()],
        "t1": [_fila(), _fila(unidad="[8032] MSFT", ticker="MSFT", cantidad=-5.0)],
    })
    assert {p.get("h") for _, p in cap if "h" in p} == {"t0", "t1"}
    assert resp["t0"]["n"] == 1
    assert resp["t1"]["n"] == 2
    assert resp["t0"]["filas"][0]["ticker"] == "AO29"


def test_cada_lado_ordena_del_peor_al_menos(monkeypatch):
    _, cap = _correr(monkeypatch)
    for h in ("t0", "t1"):
        assert "ORDER BY tl.cantidad ASC" in _sql_de(cap, h)[0]


def test_un_horizonte_vacio_no_afecta_al_otro(monkeypatch):
    """Caso real: el descubierto se resolvió con una compra de hoy → sigue en T0
    (todavía no liquidó) pero ya no está en T1."""
    resp, _ = _correr(monkeypatch, {"t0": [_fila()], "t1": []})
    assert resp["t0"]["n"] == 1
    assert resp["t1"]["n"] == 0
    assert resp["t1"]["filas"] == []


# ── la frescura viaja SIEMPRE ────────────────────────────────────────────────
def test_sin_negativos_igual_informa_la_frescura(monkeypatch):
    """El caso bueno (todo vacío) es indistinguible del caso malo (daemon
    parado) si no viaja el timestamp. Por eso la metadata se pide aparte y no se
    deriva de las filas."""
    resp, _ = _correr(monkeypatch)
    assert resp["t0"]["n"] == 0 and resp["t1"]["n"] == 0
    assert resp["actualizado_at"] == "2026-08-12T15:30:00"
    assert resp["fecha"] == "2026-08-12"
    assert resp["cuentas_en_posicion"] == 97


def test_cuenta_vacia_no_deja_la_fila_sin_etiqueta(monkeypatch):
    """Sin `cuenta` la fila sería un número sin dueño — el back office necesita
    saber a quién llamar."""
    resp, _ = _correr(monkeypatch, {"t0": [_fila(cuenta=None)]})
    assert resp["t0"]["filas"][0]["cuenta"] == "[805]"


# ── la CLASIFICACIÓN se lee en vivo, no la copia congelada ───────────────────
def test_la_cartera_sale_de_assets_en_vivo_no_de_la_copia_de_tenencia(monkeypatch):
    """Incidente 2026-08-12: unos OTC clasificados como DERIVADOS en Manager
    seguían apareciendo en el control.

    Causa: el daemon carga `portafolio.assets` UNA vez al arrancar y lo reusa las
    ~10 horas que corre, así que la `cartera` que copia a `tenencia_live` es la
    que existía a las 11:00. Reclasificar a mediodía no tenía efecto hasta el día
    siguiente, y sin ningún síntoma visible.

    La cartera es una CLASIFICACIÓN (metadato mutable), no un hecho del día: se
    resuelve con JOIN a `assets` al momento de mirar. `tenencia_live.cartera`
    queda solo de respaldo para unidades que no estén en el catálogo.
    """
    _, cap = _correr(monkeypatch)
    sql, _ = _sql_de(cap, "t0")
    assert "LEFT JOIN portafolio.assets a ON a.unidad = tl.unidad" in sql, \
        "no joinea assets — vuelve a depender de la copia congelada"
    # El respaldo tiene que estar: una unidad fuera del catálogo no puede quedar
    # sin cartera y colarse como si fuera un título.
    assert "tl.cartera" in sql, "se perdió el fallback a la copia de tenencia_live"
