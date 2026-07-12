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

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
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

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
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
    rv = next(v for v in vistas if v["vista"] == "renta_variable")
    assert rv["titulo"] == "Renta Variable"
    # los chips (consultas de mesa curadas) viajan en el payload de /vistas
    assert {c["label"] for c in rv["chips"]} >= {"Papeles de IA", "Argentina"}
    assert all(c["pregunta"] for c in rv["chips"])
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

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
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


def test_tsv_headers_renombrados():
    tsv = copiloto._tsv(
        [{"adr_ret_mtd_pct": 5.0, "adr_ret_ytd_pct": -23.37}],
        [("adr_ret_mtd_pct", "ret_mes%"), ("adr_ret_ytd_pct", "ret_año%")],
    )
    lineas = tsv.split("\n")
    assert lineas[0] == "ret_mes%\tret_año%"
    assert lineas[1] == "5.00\t-23.37"


def test_numeros_sin_respaldo():
    ctx = "ticker\tret_mes%\tret_año%\tmonto\nMU\t-5.13\t243.12\t325432132.00\nTGT\t3.72\t38.25\t8842000.00"
    # copiados bien (redondeo a entero incluido) → sin sospechosos
    malos, total = copiloto._numeros_sin_respaldo("MU +243.12% en el año, TGT +38%", ctx)
    assert (malos, total) == ([], 2)
    # abreviaciones con sufijo (325M ≈ 325432132, 8.8M ≈ 8842000) → respaldadas
    malos, total = copiloto._numeros_sin_respaldo("movieron 325M y 8.8M", ctx)
    assert malos == []
    # un número inventado se detecta y se DEVUELVE cuál es
    malos, total = copiloto._numeros_sin_respaldo("MU subió 99.99% este mes", ctx)
    assert malos == ["99.99"] and total == 1
    # posiciones de ranking y años no cuentan
    malos, total = copiloto._numeros_sin_respaldo("1. MU 2. TGT (desde 2025)", ctx)
    assert total == 0


def test_jerga_en_respuesta():
    cfg = copiloto.VISTAS["renta_variable"]
    # headers internos colados → detectados
    jerga = copiloto._jerga_en_respuesta(
        "el rubro pierde 4.39% de ret_7d con monto_usd_ny fuerte", cfg, "como vienen?"
    )
    assert "ret_7d" in jerga and "monto_usd_ny" in jerga
    # bug real del shadow: el "%" pegado al término NO lo esconde
    jerga = copiloto._jerga_en_respuesta(
        "T (ret_año% -14.94, ret_semana% 2.67) y la columna ia", cfg, "rezagados?"
    )
    assert "ret_año" in jerga and "ret_semana" in jerga and "columna" in jerga
    # lenguaje de mesa limpio → nada
    assert copiloto._jerga_en_respuesta(
        "Vienen bien en el año: +11% en dólares, aunque esta semana caen.", cfg, "como vienen?"
    ) == []
    # si el USUARIO usa el término, se le puede contestar con él
    assert copiloto._jerga_en_respuesta(
        "zona_piv_año te dice dónde está parado", cfg, "que es zona_piv_año?"
    ) == []


def test_autocorreccion_reintenta_con_numeros_malos(monkeypatch):
    _vista_fake(monkeypatch)
    llamadas = []

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        llamadas.append({"user": user, "detalle": detalle})
        if len(llamadas) == 1:
            return "AAPL subió 99.99%", 1  # número inventado → dispara reflexion
        return "AAPL subió 1.23%", 2       # corregido con el dato real

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿cómo viene AAPL?")
    assert len(llamadas) == 2
    assert "99.99" in llamadas[1]["user"] and "autocorrección" in llamadas[1]["detalle"]
    assert out["respuesta"] == "AAPL subió 1.23%" and out["traza_id"] == 2
    assert out["numeros_sin_respaldo"] == []


def test_jerga_nomenclatura_pivots():
    cfg = copiloto.VISTAS["renta_variable"]
    # ">R3 anual" sin que el usuario hable de pivots → jerga
    jerga = copiloto._jerga_en_respuesta(
        "SNDK está en zona >R3 anual, sin techos", cfg, "cuáles rompieron techos?"
    )
    assert any("pivots" in j for j in jerga)
    # si el usuario nombra PP/pivots, se le contesta con esos términos
    assert copiloto._jerga_en_respuesta(
        "V y CAT están en PP-R1 anual", cfg, "qué papeles están en zona de PP anual?"
    ) == []


def test_screenings_filtra_por_codigo():
    filas = [
        {"ticker_corto": "T", "adr_ret_ytd_pct": -14.9, "adr_ret_15r_pct": 5.2,
         "adr_ret_wtd_pct": 2.7, "total_money": 100, "piv_anual": "S1-PP"},
        {"ticker_corto": "BIDU", "adr_ret_ytd_pct": -10.0, "adr_ret_15r_pct": -9.5,
         "adr_ret_wtd_pct": 3.7, "total_money": 50, "piv_anual": "S2-S1"},
        {"ticker_corto": "SNDK", "adr_ret_ytd_pct": 707.1, "adr_ret_15r_pct": 20.0,
         "adr_ret_wtd_pct": 9.8, "total_money": 900, "piv_anual": ">R3",
         "es_ia": True, "adr_vs_1d_pct": 12.2},
        # GGAL sube más que nadie hoy pero NO es IA → no puede colarse
        {"ticker_corto": "GGAL", "adr_ret_ytd_pct": -0.1, "adr_ret_15r_pct": 4.0,
         "adr_ret_wtd_pct": 6.7, "total_money": 800, "piv_anual": "PP-R1",
         "es_ia": None, "adr_vs_1d_pct": 15.0},
    ]
    lineas = copiloto._screenings(filas)
    texto = "\n".join(lineas)
    # T repunta de verdad; BIDU es solo rebote de corto; SNDK rompió techos
    assert "rezagados repuntando de verdad" in texto and "T año -14.9%" in texto
    assert "BIDU" in next(li for li in lineas if "rebotes de corto" in li)
    assert "SNDK" in next(li for li in lineas if "rompieron todos los techos" in li)
    # T también está en zona de decisión (S1-PP)
    assert "T" in next(li for li in lineas if "zona de decisión" in li)
    # destacados IA del día: SOLO es_ia — GGAL (líder del día) queda afuera
    linea_ia = next(li for li in lineas if "papeles de IA destacados" in li)
    assert "SNDK" in linea_ia and "GGAL" not in linea_ia


def test_rankings_ordena_por_codigo():
    filas = [
        {"ticker_corto": "SNDK", "adr_ret_ytd_pct": 707.11, "adr_ret_mtd_pct": -5.72,
         "adr_ret_wtd_pct": 9.83, "adr_vs_1d_pct": 1.0},
        {"ticker_corto": "MU", "adr_ret_ytd_pct": 243.12, "adr_ret_mtd_pct": -5.13,
         "adr_ret_wtd_pct": -0.55, "adr_vs_1d_pct": 2.0},
        {"ticker_corto": "BABA", "adr_ret_ytd_pct": -23.37, "adr_ret_mtd_pct": 14.63,
         "adr_ret_wtd_pct": 14.73, "adr_vs_1d_pct": 3.0},
    ]
    lineas = copiloto._rankings(filas)
    # el caso real: SNDK lidera el año y el modelo lo salteaba — el código no
    assert lineas[1].startswith("top año: SNDK +707.11%, MU +243.12%")
    assert lineas[2].startswith("peores año: BABA -23.37%")
    assert lineas[3].startswith("top mes: BABA +14.63%")


def test_derrame_dispara_autocorreccion(monkeypatch):
    _vista_fake(monkeypatch)
    llamadas = []

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        llamadas.append(user)
        if len(llamadas) == 1:
            return "V sube 1.23% — no, V pierde. Corrijo: solo AAPL.", 1
        return "Solo AAPL sube 1.23%.", 2

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿quién sube?")
    assert len(llamadas) == 2 and "razonamiento intermedio" in llamadas[1]
    assert out["respuesta"] == "Solo AAPL sube 1.23%."


def test_tono_por_rol_entra_al_system(monkeypatch):
    _vista_fake(monkeypatch)
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        capturado["system"] = system
        return "ok", 1

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    monkeypatch.setattr("core.roles.get_user_role", lambda email: "sales")
    copiloto.preguntar("fake", "¿cómo viene AAPL?", usuario="v@x.com")
    assert "COMERCIAL" in capturado["system"]
    monkeypatch.setattr("core.roles.get_user_role", lambda email: "trader")
    copiloto.preguntar("fake", "¿cómo viene AAPL?", usuario="v@x.com")
    assert "TRADER" in capturado["system"]
    # rol sin tono especial (admin) → system neutro
    monkeypatch.setattr("core.roles.get_user_role", lambda email: "admin")
    copiloto.preguntar("fake", "¿cómo viene AAPL?", usuario="v@x.com")
    assert "COMERCIAL" not in capturado["system"] and "TRADER" not in capturado["system"]


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
    monkeypatch.setattr(copiloto, "_retornos_ruedas", lambda: {"AAPL": {"r30": 4.2, "r45": 9.9}})
    monkeypatch.setattr(copiloto, "_extremos_serie", lambda: {"AAPL": {"max": 250.0, "min": 50.0}})
    original = [{"ticker_corto": "AAPL", "underlying": "AAPL", "adr_last": 125.0}]
    out = copiloto._enriquecer_cedears(original)
    assert out[0]["piv_anual"] == "R2-R3"
    assert out[0]["piv_mensual"] is None  # sin vela mensual → sin zona
    assert out[0]["ret_30r"] == 4.2 and out[0]["ret_45r"] == 9.9
    assert out[0]["max_serie"] == 250.0 and out[0]["min_serie"] == 50.0
    assert out[0]["dist_max"] == -50.0  # 125 está 50% abajo del máximo de la serie
    assert "piv_anual" not in original[0]  # las filas cacheadas del scanner no se tocan


def test_verificacion_estricta_bloquea(monkeypatch):
    _vista_fake(monkeypatch)

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        return "AAPL subió 99.99%", 1  # inventa el número SIEMPRE (también en el retry)

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿cómo viene AAPL?")
    # política estricta: verificada o no se muestra
    assert out == {"ok": False, "error": "verificacion"}


def test_feedback_valor_invalido():
    assert copiloto.registrar_feedback(1, 5, "u@x.com") == {
        "ok": False, "error": "valor_invalido",
    }
