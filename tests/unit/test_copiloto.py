"""Tests del Copiloto de Mesa (api/services/copiloto.py) — sin red ni DB.

Congelan el contrato: gate por módulo RBAC de la vista, contexto TSV (headers
una vez, celdas sanitizadas), cap de filas/historial, y degradación ok=False
en cada camino de fallo (vista desconocida, sin datos, proveedor caído).
"""
from __future__ import annotations

from api.services import copiloto

_FILAS = [
    {
        "ticker_corto": "AAPL", "nombre": "Apple Inc", "underlying": "AAPL",
        "ratio_cedear": 20, "sector": "Tech", "pais": "USA",
        "last": 15234.5, "intraday_pct": 1.234, "vs_1d_pct": None,
    },
    {
        "ticker_corto": "MELI", "nombre": "Mercado\tLibre\ncon enter",
        "underlying": "MELI", "ratio_cedear": 60, "sector": "Tech", "pais": "ARG",
        "last": None, "intraday_pct": -0.5, "vs_1d_pct": 2.0,
    },
]


def _vista_fake(monkeypatch, filas=None):
    monkeypatch.setitem(
        copiloto.VISTAS, "fake",
        {
            "titulo": "Vista Fake", "modulo": "renta-variable",
            "fetch": lambda: filas if filas is not None else _FILAS,
            "columnas": ["ticker_corto", "nombre", "last", "intraday_pct", "vs_1d_pct"],
            "reglas": "reglas de la vista fake",
        },
    )


def test_tsv_headers_una_vez_y_celdas_sanitizadas():
    tsv = copiloto._tsv(_FILAS, ["ticker_corto", "nombre", "last", "vs_1d_pct"])
    lineas = tsv.split("\n")
    assert lineas[0] == "ticker_corto\tnombre\tlast\tvs_1d_pct"
    assert len(lineas) == 3
    # None → "-", floats con 2 decimales SIN separador de miles (menos tokens)
    assert lineas[1].split("\t") == ["AAPL", "Apple Inc", "15234.50", "-"]
    # un string con tab/newline no rompe el TSV
    assert lineas[2].split("\t")[1] == "Mercado Libre con enter"


def test_vista_desconocida_y_pregunta_vacia():
    assert copiloto.preguntar("no_existe", "hola") == {"ok": False, "error": "vista_desconocida"}
    _ = copiloto.VISTAS["renta_variable"]  # la vista real existe
    assert copiloto.preguntar("renta_variable", "   ")["error"] == "pregunta_vacia"


def test_sin_datos_degrada(monkeypatch):
    _vista_fake(monkeypatch, filas=[])
    assert copiloto.preguntar("fake", "¿qué pasa?") == {
        "ok": False, "error": "datos_no_disponibles",
    }


def test_fetch_que_explota_degrada(monkeypatch):
    monkeypatch.setitem(
        copiloto.VISTAS, "fake",
        {**copiloto.VISTAS["renta_variable"], "fetch": lambda: 1 / 0},
    )
    assert copiloto.preguntar("fake", "x")["error"] == "datos_no_disponibles"


def test_proveedor_caido_degrada(monkeypatch):
    _vista_fake(monkeypatch)
    monkeypatch.setattr("core.ai.completar_con_traza", lambda *a, **k: (None, None))
    assert copiloto.preguntar("fake", "¿cómo está AAPL?") == {
        "ok": False, "error": "ia_no_disponible",
    }


def test_contexto_y_respuesta_ok(monkeypatch):
    _vista_fake(monkeypatch)
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None):
        capturado.update(tarea=tarea, system=system, user=user, usuario=usuario)
        return "AAPL sube 1.23%", 42

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    historial = [{"pregunta": f"p{i}", "respuesta": f"r{i}"} for i in range(6)]
    out = copiloto.preguntar("fake", "¿cómo está AAPL?", historial=historial, usuario="u@x.com")

    assert out["ok"] is True and out["respuesta"] == "AAPL sube 1.23%"
    assert out["traza_id"] == 42
    assert out["fuente"]["vista"] == "fake" and out["fuente"]["filas"] == 2
    assert capturado["tarea"] == "copiloto_vista" and capturado["usuario"] == "u@x.com"
    assert "reglas de la vista fake" in capturado["system"]
    assert "<datos>" in capturado["user"] and "PREGUNTA: ¿cómo está AAPL?" in capturado["user"]
    # historial capado a los últimos 4 pares
    assert "[pregunta previa] p1" not in capturado["user"]
    assert "[pregunta previa] p2" in capturado["user"] and "p5" in capturado["user"]


def test_cap_de_filas(monkeypatch):
    filas = [{"ticker_corto": f"T{i}", "nombre": "x", "last": 1.0,
              "intraday_pct": 0.0, "vs_1d_pct": 0.0} for i in range(500)]
    _vista_fake(monkeypatch, filas=filas)
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None):
        capturado["user"] = user
        return "ok", 1

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿cuántos hay?")
    assert out["fuente"]["filas"] == copiloto._MAX_FILAS
    assert "(recortada de 500)" in capturado["user"]
    # header + _MAX_FILAS filas dentro de <datos>
    datos = capturado["user"].split("<datos>")[1].split("</datos>")[0].strip()
    assert len(datos.split("\n")) == copiloto._MAX_FILAS + 1


def test_gate_por_modulo_de_vista(monkeypatch):
    monkeypatch.setattr("core.roles.has_access", lambda email, mod: mod == "renta-variable")
    vistas = copiloto.vistas_para("u@x.com")
    assert {"vista": "renta_variable", "titulo": "Renta Variable"} in vistas
    assert copiloto.puede_usar("u@x.com", "renta_variable") is True
    monkeypatch.setattr("core.roles.has_access", lambda email, mod: False)
    assert copiloto.vistas_para("u@x.com") == []
    assert copiloto.puede_usar("u@x.com", "renta_variable") is False


def test_detectar_tickers():
    filas = [
        {"ticker_corto": "AAPL", "underlying": "AAPL"},
        {"ticker_corto": "MELI", "underlying": "MELI"},
        {"ticker_corto": "NVDA", "underlying": "NVDA"},
        {"ticker_corto": "GOOGL", "underlying": "GOOGL"},
    ]
    # match por token, case-insensitive; palabras comunes no matchean
    out = copiloto._detectar_tickers(filas, "¿cómo viene nvda contra AAPL hoy?", [])
    assert [f["ticker_corto"] for f in out] == ["NVDA", "AAPL"]
    # follow-up: el ticker viene de una pregunta previa del historial
    out = copiloto._detectar_tickers(filas, "¿y sus pivots?", [{"pregunta": "dame MELI"}])
    assert [f["ticker_corto"] for f in out] == ["MELI"]
    # cap de 3
    out = copiloto._detectar_tickers(filas, "AAPL MELI NVDA GOOGL", [])
    assert len(out) == copiloto._MAX_TICKERS_DETALLE


def test_detectar_tickers_palabras_comunes_no_matchean():
    filas = [{"ticker_corto": "DE", "underlying": "DE"},
             {"ticker_corto": "NVDA", "underlying": "NVDA"}]
    # "de" minúscula (palabra española) NO es Deere (caso real del shadow)
    out = copiloto._detectar_tickers(filas, "recomendame acciones de IA con nvda", [])
    assert [f["ticker_corto"] for f in out] == ["NVDA"]
    # "DE" escrito en mayúsculas a propósito SÍ es Deere
    out = copiloto._detectar_tickers(filas, "¿cómo viene DE hoy?", [])
    assert [f["ticker_corto"] for f in out] == ["DE"]


def test_extras_entran_al_contexto_y_su_fallo_no_rompe(monkeypatch):
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None):
        capturado["user"] = user
        return "ok", 1

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)

    _vista_fake(monkeypatch)
    copiloto.VISTAS["fake"]["extras"] = lambda filas, pregunta, historial: ["[CCL live] 1.234"]
    assert copiloto.preguntar("fake", "¿cuánto está el ccl?")["ok"] is True
    assert "[CCL live] 1.234" in capturado["user"]

    copiloto.VISTAS["fake"]["extras"] = lambda *a: 1 / 0
    out = copiloto.preguntar("fake", "¿algo?")
    assert out["ok"] is True  # extras rotos → contexto sin detalle, pregunta sigue


def test_pct_convierte_fraccion():
    assert copiloto._pct(0.0123) == "1.23%"
    assert copiloto._pct(None) == "-"
    assert copiloto._pct(0.4567, 1) == "45.7%"


def test_celda_bool_legible():
    assert copiloto._celda(True) == "si"
    assert copiloto._celda(False) == "no"


def test_zona_pivots():
    lv = {"pp": 100.0, "r1": 110.0, "r2": 120.0, "r3": 130.0,
          "s1": 90.0, "s2": 80.0, "s3": 70.0}
    assert copiloto._zona(135, lv) == ">R3"
    assert copiloto._zona(125, lv) == "R2-R3"
    assert copiloto._zona(115, lv) == "R1-R2"
    assert copiloto._zona(105, lv) == "PP-R1"
    assert copiloto._zona(95, lv) == "S1-PP"
    assert copiloto._zona(85, lv) == "S2-S1"
    assert copiloto._zona(75, lv) == "S3-S2"
    assert copiloto._zona(65, lv) == "<S3"


def test_pulso_por_rubro_pondera_por_volumen():
    filas = [
        {"rubro": "SEMIS", "adr_vs_1d_pct": 10.0, "adr_dollar_vol": 300.0,
         "adr_ret_wtd_pct": None, "adr_ret_mtd_pct": None, "adr_ret_ytd_pct": None},
        {"rubro": "SEMIS", "adr_vs_1d_pct": -2.0, "adr_dollar_vol": 100.0,
         "adr_ret_wtd_pct": None, "adr_ret_mtd_pct": None, "adr_ret_ytd_pct": None},
        {"rubro": "BANCOS", "adr_vs_1d_pct": 1.0, "adr_dollar_vol": 50.0,
         "adr_ret_wtd_pct": 2.0, "adr_ret_mtd_pct": 3.0, "adr_ret_ytd_pct": 4.0},
    ]
    lineas = copiloto._pulso_por_rubro(filas)
    assert lineas[1].startswith("rubro\t")
    # SEMIS primero (más volumen) y ponderado: (10·300 − 2·100) / 400 = 7.00
    assert lineas[2].split("\t")[:3] == ["SEMIS", "2", "7.00"]
    assert lineas[3].split("\t")[:3] == ["BANCOS", "1", "1.00"]


def test_enriquecer_no_muta_las_filas_originales(monkeypatch):
    monkeypatch.setattr(
        copiloto, "_velas_periodo_previo",
        lambda: {"AAPL": {"anual": {"pp": 100.0, "r1": 110.0, "r2": 120.0, "r3": 130.0,
                                    "s1": 90.0, "s2": 80.0, "s3": 70.0}}},
    )
    original = [{"ticker_corto": "AAPL", "underlying": "AAPL", "adr_last": 125.0}]
    out = copiloto._enriquecer_cedears(original)
    assert out[0]["piv_anual"] == "R2-R3"
    assert out[0]["piv_mensual"] is None  # sin vela mensual → sin zona
    assert "piv_anual" not in original[0]  # las filas cacheadas del scanner no se tocan


def test_feedback_valor_invalido():
    assert copiloto.registrar_feedback(1, 5, "u@x.com") == {
        "ok": False, "error": "valor_invalido",
    }
