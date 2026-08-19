"""EL BLOB Y LA COLUMNA TIENEN QUE DECIR EL MISMO SÍMBOLO — `core/curvas_sql`.

El renombre del 2026-08-15 migró las columnas y dejó el blob intacto, con el
significado **invertido**. Nadie los mantenía iguales, y el día que divergieron
el sistema quedó partido en dos mitades que no se hablan:

    el MOTOR escribe el precio leyendo el BLOB
    la VISTA lo busca por la COLUMNA

Medido en prod: 2 de 229 (AO29, CO32). La fila salía entera en «--» con el
precio existiendo, y ningún detector lo veía — porque el agente lee por el blob,
igual que el motor.
"""
from __future__ import annotations

from core import curvas_sql


def _cargar(monkeypatch, doc: dict, columnas: dict) -> dict:
    """Simula UNA fila de `mercado.curvas`: su blob + sus columnas."""
    nombres = list(curvas_sql._COLS_FUERA_DEL_BLOB) + list(curvas_sql._ALIAS_DEL_BLOB)

    class _Cur:
        description = [("data",)] + [(n,) for n in nombres]

        def execute(self, *a, **k): pass
        def fetchall(self): return [[doc] + [columnas.get(n) for n in nombres]]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(curvas_sql, "get_pool",
                        lambda: type("P", (), {"connection": lambda s: _Conn()})())
    return curvas_sql._load_all()[0]


def test_la_COLUMNA_gana_sobre_el_blob_para_el_SIMBOLO(monkeypatch):
    """**El caso AO29.** La columna ya tenía la pata correcta (la en dólares) y
    el blob la vieja en pesos: el motor suscribía una y la pantalla buscaba la
    otra. La regla es la misma que ya regía para el emisor y los ejes."""
    doc = _cargar(monkeypatch,
                  {"ticker": "MERV - XMEV - AO29 - 24hs", "ticker_corto": "AO29"},
                  {"instrumento": "MERV - XMEV - AO29D - 24hs", "ticker": "AO29"})
    assert doc["ticker"] == "MERV - XMEV - AO29D - 24hs"


def test_el_ALIAS_es_lo_que_evita_romper_500_lugares(monkeypatch):
    """Los nombres están CRUZADOS entre el blob y la columna. Sin alias,
    `doc["ticker"]` pasaría a valer «AO29» y todo lo que lo usa como símbolo de
    mercado se rompería junto — que es peor que el bug que se viene a arreglar."""
    doc = _cargar(monkeypatch,
                  {"ticker": "MERV - XMEV - AO29 - 24hs", "ticker_corto": "AO29"},
                  {"instrumento": "MERV - XMEV - AO29D - 24hs", "ticker": "AO29"})
    assert " - " in doc["ticker"], "el símbolo de mercado dejó de serlo"
    assert doc["ticker_corto"] == "AO29"


def test_una_columna_VACIA_no_borra_lo_que_el_blob_tiene(monkeypatch):
    """Completar sí, pisar con nada no. Si la migración no llegó a esa fila, el
    blob sigue siendo lo único que hay — y dejarla sin símbolo la sacaría del
    universo del motor."""
    doc = _cargar(monkeypatch,
                  {"ticker": "MERV - XMEV - AL30 - 24hs", "ticker_corto": "AL30"},
                  {"instrumento": None, "ticker": None})
    assert doc["ticker"] == "MERV - XMEV - AL30 - 24hs"
    assert doc["ticker_corto"] == "AL30"


def test_cuando_COINCIDEN_no_cambia_nada(monkeypatch):
    """Los otros 227 bonos. El arreglo no puede mover un símbolo que ya estaba
    bien: eso dejaría a la mesa sin precios por un bug de dos filas."""
    doc = _cargar(monkeypatch,
                  {"ticker": "MERV - XMEV - AL30 - 24hs", "ticker_corto": "AL30"},
                  {"instrumento": "MERV - XMEV - AL30 - 24hs", "ticker": "AL30"})
    assert doc["ticker"] == "MERV - XMEV - AL30 - 24hs"


def test_el_alias_cubre_LOS_DOS_campos_del_renombre():
    """Si mañana alguien suma una columna al merge sin alias, el que la lea por
    el blob va a recibir otra cosa. Están declarados, no adivinados."""
    assert curvas_sql._ALIAS_DEL_BLOB == {"instrumento": "ticker",
                                         "ticker": "ticker_corto"}
