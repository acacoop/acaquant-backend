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
            "fetch": lambda params=None: filas if filas is not None else _FILAS,
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
        {**copiloto.VISTAS["renta_variable"], "fetch": lambda params=None: 1 / 0},
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
    copiloto.VISTAS["fake"]["extras"] = (
        lambda filas, pregunta, historial, params=None: ["[CCL live] 1.234"]
    )
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


def test_numeros_pegados_a_letras():
    # bug real (batería ronda 3): "156bps"/"143d" en el CONTEXTO eran
    # invisibles por el \b del regex → bloqueaba respuestas correctas
    ctx = ("AL30D−GD30D: hoy +156bps vs promedio 90d +111bps (35 ruedas); "
           "AL35D−GD35D: hoy +64bps vs +60bps; a 143d: mercado 1.53%/mes")
    malos, _ = copiloto._numeros_sin_respaldo(
        "El spread está en 156 bps contra 111 de promedio; a 143 días paga 1.53%", ctx)
    assert malos == []
    malos, _ = copiloto._numeros_sin_respaldo("hoy 156bps vs 60 y 64", ctx)
    assert malos == []
    # y lo inventado se sigue cazando
    malos, _ = copiloto._numeros_sin_respaldo("me invento 999 bps", ctx)
    assert malos == ["999b"] or malos == ["999"]


def test_numeros_redondeados_medio_decimal():
    # batería home 2026-07-12: el modelo redondea a 1 decimal ("5,9" para
    # 5.85) y la respuesta moría — media unidad del último decimal se acepta
    ctx = "brent\t5.85\nwti\t4.11\nytd\t24.54"
    malos, _ = copiloto._numeros_sin_respaldo(
        "el brent sube 5,9% en la semana y acumula 24.5% en el año", ctx)
    assert malos == []
    # un redondeo MAL hecho (4.11 no es 4.2) sigue cayendo
    malos, _ = copiloto._numeros_sin_respaldo("el wti sube 4.2%", ctx)
    assert malos == ["4.2"]


def test_numeros_formato_argentino():
    # contexto en formato del TSV (punto decimal); precios en miles
    ctx = "ticker\tlast\tPP\tR1\nRKLB\t10580.00\t10587.00\t10793.00"
    # el modelo escribe a la argentina: "10.793" ES 10793 → respaldado
    malos, total = copiloto._numeros_sin_respaldo(
        "está pegado a R1 en 10.793; el last es 10.580", ctx
    )
    assert (malos, total) == ([], 2)
    # coma decimal también: "6,65%" con 6.65 en contexto
    malos, _ = copiloto._numeros_sin_respaldo("subió 6,65%", "var\t6.65")
    assert malos == []
    # y un número inventado sigue cayendo
    malos, _ = copiloto._numeros_sin_respaldo("R2 está en 99.999", ctx)
    assert malos == ["99.999"]


def test_derivacion_a_otra_vista(monkeypatch):
    # "¿qué bono rinde más?" en RV: el prompt lista las otras vistas del
    # usuario y el marcador [[VISTA:x]] se valida por código y sale del texto
    _vista_fake(monkeypatch)
    monkeypatch.setattr("core.roles.has_access", lambda email, mod: True)
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        capturado["system"] = system
        return "Eso es de bonos: consultalo desde la vista Renta Fija.\n[[VISTA:renta_fija]]", 7

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿qué bono rinde más este mes?", usuario="u@x.com")
    assert out["ok"] is True
    assert "OTRAS VISTAS CON COPILOTO" in capturado["system"]
    assert "renta_fija" in capturado["system"]
    assert "[[VISTA" not in out["respuesta"]  # el marcador jamás llega al usuario
    assert out["vista_sugerida"] == {"vista": "renta_fija", "titulo": "Renta Fija"}


def test_derivacion_sin_acceso_se_descarta(monkeypatch):
    # sin acceso RBAC a la vista sugerida: ni bloque en el prompt ni botón
    _vista_fake(monkeypatch)
    monkeypatch.setattr("core.roles.has_access", lambda email, mod: False)
    capturado = {}

    def fake_completar(tarea, *, system, user, usuario=None, detalle=None):
        capturado["system"] = system
        return "Andá a Renta Fija.\n[[VISTA:renta_fija]]", 7

    monkeypatch.setattr("core.ai.completar_con_traza", fake_completar)
    out = copiloto.preguntar("fake", "¿qué bono rinde más?", usuario="u@x.com")
    assert "OTRAS VISTAS CON COPILOTO" not in capturado["system"]
    assert out["vista_sugerida"] is None
    assert "[[VISTA" not in out["respuesta"]


def test_marcador_invalido_se_borra(monkeypatch):
    # vista inexistente alucinada por el modelo: se limpia, sin sugerencia
    texto, sugerida = copiloto._extraer_vista_sugerida(
        "Consultalo allá.\n[[VISTA:no_existe]]", "renta_variable", "u@x.com")
    assert sugerida is None and "[[VISTA" not in texto


def test_presupuesto_agotado_durante_la_llamada(monkeypatch):
    # el pre-chequeo pasa (había margen) pero el gasto cruza el tope durante
    # la llamada → el error nombra el presupuesto, no el genérico
    _vista_fake(monkeypatch)
    monkeypatch.setattr("core.ai.completar_con_traza", lambda *a, **k: (None, None))
    llamadas = {"n": 0}

    def fake_motivo(usuario):
        llamadas["n"] += 1
        return None if llamadas["n"] == 1 else "usuario"

    monkeypatch.setattr("core.ai.motivo_presupuesto", fake_motivo)
    out = copiloto.preguntar("fake", "¿cómo viene AAPL?", usuario="u@x.com")
    assert out == {"ok": False, "error": "presupuesto_usuario"}


def test_vista_home_registrada():
    cfg = copiloto.VISTAS["home"]
    assert cfg["modulo"] == "home" and cfg["dominio"]
    # "Narrame el briefing" se eliminó a propósito (commit 22421de: la narración
    # del briefing no funcionaba). El chip que queda es "¿Cómo viene el mercado?".
    assert {c["label"] for c in cfg["chips"]} >= {"¿Cómo viene el mercado?"}
    assert all(c["pregunta"] for c in cfg["chips"])


def test_fetch_home_mezcla_argy_y_quotes(monkeypatch):
    monkeypatch.setattr(
        "api.services.argy.get_argy_with_returns",
        lambda: [{"label": "DOLAR MEP", "value": 1234.5, "unit": "$",
                  "ret_day": 0.5, "ret_7d": 1.2, "ret_mtd": 3.0, "ret_ytd": 20.0}],
    )
    monkeypatch.setattr(
        "api.services.market_sql.quotes",
        lambda: [{"symbol": "ES=F", "grupo": "Índices", "last": 6100.0,
                  "pct_day": -0.3, "ret_7d": 0.8, "ret_mtd": 2.0, "ret_ytd": 15.0}],
    )
    filas = copiloto._fetch_home()
    assert [f["symbol"] for f in filas] == ["DOLAR MEP", "ES=F"]
    assert filas[0]["grupo"] == "ARGENTINA" and filas[0]["unit"] == "$"
    assert filas[1]["pct_day"] == -0.3


def test_fetch_home_una_fuente_caida_no_rompe(monkeypatch):
    monkeypatch.setattr("api.services.argy.get_argy_with_returns", lambda: 1 / 0)
    monkeypatch.setattr(
        "api.services.market_sql.quotes",
        lambda: [{"symbol": "GC=F", "grupo": "Metales", "last": 3300.0}],
    )
    filas = copiloto._fetch_home()
    assert len(filas) == 1 and filas[0]["symbol"] == "GC=F"


def test_renta_fija_pulso_por_curva_y_tramo(monkeypatch):
    monkeypatch.setattr(copiloto, "_fetch_renta_fija", lambda params=None: [
        {"ticker_corto": "S31L6", "curva_label": "tasa_fija", "tea": 39.0, "meses_al_vto": 1},
        {"ticker_corto": "T15E7", "curva_label": "tasa_fija", "tea": 33.0, "meses_al_vto": 24},
        {"ticker_corto": "TX26", "curva_label": "cer", "tea": 10.0, "meses_al_vto": 8},
    ])
    monkeypatch.setattr(copiloto, "_teas_cierre_anterior",
                        lambda: {"S31L6": 40.0, "T15E7": 32.5, "_fecha": "2026-07-11"})
    bloque = "\n".join(copiloto._renta_fija_pulso())
    # tasa_fija: corto comprime 100bps, largo descomprime 50bps — separado por tramo
    assert "tasa_fija: corto 39.0% (-100bps hoy) · largo 33.0% (+50bps hoy)" in bloque
    # cer sin cierre previo → TEA sin delta
    assert "cer: medio 10.0%" in bloque


def test_futuros_dlr_fallback_al_cierre(monkeypatch):
    # el snapshot live viene vacío fuera de rueda (verificado con diag en el
    # Droplet) → cae al último cierre persistido, declarando la fecha
    monkeypatch.setattr("api.services.mercado_hist_sql.get_futuros_dlr", lambda: [])
    monkeypatch.setattr(
        "api.services.derivados.get_historico_futuros_dlr",
        lambda **kw: [
            {"fecha": "2026-07-10", "ticker": "DLR/AGO26", "vencimiento": "20260831",
             "dias_a_vto": 49, "precio_cierre": 1560.5, "tasa_implicita_tna_cierre": 35.2},
            {"fecha": "2026-07-09", "ticker": "DLR/AGO26", "vencimiento": "20260831",
             "dias_a_vto": 50, "precio_cierre": 1555.0, "tasa_implicita_tna_cierre": 34.0},
        ],
    )
    bloque = "\n".join(copiloto._futuros_dlr_bloque())
    assert "cierre del 2026-07-10" in bloque
    assert "DLR/AGO26 (49d): 1560.5 · TNA implícita 35.2%" in bloque
    assert "1555.0" not in bloque  # solo la última fecha persistida


def test_briefing_bloque_serializa(monkeypatch):
    monkeypatch.setattr(
        "api.services.briefing.briefing_hoy",
        lambda: {
            "fecha": "2026-07-13",
            "futuros": [{"label": "S&P 500", "grupo": "Índices US", "hoy": 6100.0,
                         "ret_1d": 0.5, "ret_wtd": 1.0, "ret_mtd": None}],
            "oficial": [{"label": "Mayorista MAE", "hoy": None, "ret_1d": None,
                         "ret_wtd": None, "ret_mtd": None}],
            "financieros": [],
            "pagan_hoy": [{"ticker": "TX26", "emisor": None}],
        },
    )
    bloque = "\n".join(copiloto._briefing_bloque())
    assert "briefing de apertura" in bloque and "2026-07-13" in bloque
    assert "futuros Índices US" in bloque
    assert "S&P 500: hoy 6100.00 · 1d +0.50% · sem +1.00% · mes -" in bloque
    assert "pagan hoy: TX26" in bloque


def test_estado_mercado_mapa_horario():
    from datetime import datetime

    def art(h, m=0, dia=4):  # dia 4 = viernes
        return datetime(2026, 7, 6 + dia, h, m)  # 2026-07-06 es lunes

    assert "PRE-APERTURA" in copiloto._estado_mercado(art(9, 45), True)[0]
    assert "RUEDA VIVA" in copiloto._estado_mercado(art(11, 30), True)[0]
    assert "ZONA MUERTA" in copiloto._estado_mercado(art(14, 0), True)[0]
    assert "NO operar" in copiloto._estado_mercado(art(14, 0), True)[1]
    assert "ÚLTIMO TRAMO" in copiloto._estado_mercado(art(16, 30), True)[0]
    assert "CERRADO" in copiloto._estado_mercado(art(18, 0), True)[0]
    # feriado/finde: cerrado aunque sea horario de rueda
    assert "no es día hábil" in copiloto._estado_mercado(art(11, 0), False)[0]


def test_sanear_params_trading():
    tickers, sel, ov = copiloto._sanear_params_trading({
        "tickers": ["rklb", "SNDK", "rklb", "../x", ""],
        "seleccionado": "sndk",
        "overrides": {"RKLB": {"high": "10800", "low": 10400, "close": "no"},
                      "OTRO": {"high": 1}},
    })
    assert tickers == ["RKLB", "SNDK"]  # upper, dedup, basura afuera
    assert sel == "SNDK"
    # override solo de tickers presentes, solo números válidos
    assert ov == {"RKLB": {"high": 10800.0, "low": 10400.0}}
    # sin params → vacío, sin explotar
    assert copiloto._sanear_params_trading(None) == ([], None, {})


def test_fetch_trading_aplica_overrides(monkeypatch):
    import api.services.scanner_sql as ssql
    import api.services.trading_pivots as tp

    monkeypatch.setattr(tp, "get_pivots", lambda *, tickers: [{
        "ticker": "RKLB", "high": 10800.0, "low": 10400.0, "close": 10560.0,
        "last": 10580.0, "vwap": 10533.0,
        "pivots": {"pp": 10586.7, "r1": 10773.3, "r2": 10986.7, "r3": 11173.3,
                   "s1": 10373.3, "s2": 10186.7, "s3": 9973.3},
    }])
    monkeypatch.setattr(ssql, "get_cedears_scanner",
                        lambda: [{"ticker_corto": "RKLB", "vs_1d_pct": 3.1,
                                  "rubro": "Espacial"}])
    filas = copiloto._fetch_trading({"tickers": ["RKLB"], "seleccionado": "RKLB"})
    f = filas[0]
    assert f["foco"] is True and f["dia_pct"] == 3.1 and f["rubro"] == "Espacial"
    # niveles como "precio (dif%)" — la distancia YA calculada (toggle DIF%)
    assert f["pp"].startswith("10586.70 (") and "%" in f["pp"]
    # nivel más cercano: PP está a +0.06% del last 10580
    assert f["nivel_cercano"].startswith("PP a +0.0")
    assert f["zona"] == "S1-PP"
    # con override del usuario: los pivots se RECALCULAN con su base editada
    filas = copiloto._fetch_trading({
        "tickers": ["RKLB"],
        "overrides": {"RKLB": {"high": 11000, "low": 10000, "close": 10500}},
    })
    assert filas[0]["pp"].startswith(f"{(11000 + 10000 + 10500) / 3:.2f} (")
    assert filas[0]["high"] == 11000


def test_sanear_posiciones():
    out = copiloto._sanear_posiciones({"posiciones": [
        {"especie": "rklb", "estado": "long", "qty": -350, "precio": "10700.5"},
        {"especie": "SNDK", "estado": "CERRADA", "qty": 1, "precio": 1},  # cerrada afuera
        {"especie": "X", "estado": "SHORT", "qty": "nada", "precio": 5},  # qty inválida
    ]})
    assert out == [{"especie": "RKLB", "estado": "LONG", "qty": 350.0, "precio": 10700.5}]
    assert copiloto._sanear_posiciones(None) == []


def test_fetch_renta_fija_merge(monkeypatch):
    import api.services.fair_value as fv
    import api.services.renta_fija as rf

    # OJO: kwargs-only, como el wrapper @cached real — una llamada posicional
    # acá adentro tiene que EXPLOTAR el test (bug real 2026-07-12: el fair
    # value no llegaba al copiloto por llamarlo posicional)
    monkeypatch.setattr(fv, "get_fair_value_live", lambda *, curva: {
        "r2": 0.97,
        "bonos": [{"ticker_corto": "TX26", "tea_teorica": 0.41, "residuo_bps": 85.0}],
    } if curva == "cer" else {"bonos": []})
    monkeypatch.setattr(rf, "get_renta_fija", lambda: [
        {"instrumento": "MERV - XMEV - S31O5 - 24hs",
         "metrics": {"tc_breakeven": 1450.5}},
    ])

    def fake_listar(*, curva):
        # los services devuelven TEA en FRACCIÓN (0.4185 = 41.85%)
        if curva == "cer":
            return [{"ticker_corto": "TX26", "tipo": "cer", "tea": 0.4185,
                     "meses_al_vto": 14.0, "ultimo_precio": 1520.0}]
        if curva == "tasa_fija":
            return [{"ticker_corto": "S31O5", "tipo": "tasa_fija", "tea": 0.39,
                     "cer_fijado": True, "meses_al_vto": 3.0, "ultimo_precio": 132.0}]
        return []

    monkeypatch.setattr(rf, "listar_curva", fake_listar)
    filas = copiloto._fetch_renta_fija()
    tx = next(f for f in filas if f["ticker_corto"] == "TX26")
    assert tx["residuo_bps"] == 85.0
    # normalización a %: fracción 0.4185 → 41.85 en la tabla del copiloto
    assert round(tx["tea"], 2) == 41.85
    s31 = next(f for f in filas if f["ticker_corto"] == "S31O5")
    assert s31["curva_label"] == "tasa_fija (CER fijado)"
    assert s31["tc_breakeven"] == 1450.5  # mapeado del instrumento ROFEX largo


def test_rem_promedio_hasta():
    serie = [
        {"fin_mes": "2026-07-31", "promedio_mensual_acum": 0.020},
        {"fin_mes": "2026-08-31", "promedio_mensual_acum": 0.018},
        {"fin_mes": "2026-10-31", "promedio_mensual_acum": 0.017},
    ]
    # vencimiento en septiembre → toma el primer fin_mes que lo cubre (octubre)
    assert round(copiloto._rem_promedio_hasta(serie, "2026-09-15"), 6) == 1.7
    # vencimiento pasado el REM disponible → usa el último acumulado
    assert round(copiloto._rem_promedio_hasta(serie, "2027-06-30"), 6) == 1.7
    assert copiloto._rem_promedio_hasta(serie, None) is None
    assert copiloto._rem_promedio_hasta([], "2026-09-15") is None


def test_resumen_curvas_por_tramo():
    filas = [
        {"curva_label": "cer", "tea": 40.0, "meses_al_vto": 3.0},
        {"curva_label": "cer", "tea": 44.0, "meses_al_vto": 24.0},
        {"curva_label": "tasa_fija (CER fijado)", "tea": 38.0, "meses_al_vto": 2.0},
    ]
    lineas = copiloto._resumen_curvas_rf(filas)
    cer = next(li for li in lineas if li.startswith("cer"))
    assert "corto 40.0%" in cer and "largo 44.0%" in cer and "empinamiento +4.0pp" in cer
    # el "(CER fijado)" agrupa con tasa_fija base
    assert any(li.startswith("tasa_fija (1)") for li in lineas)


def test_estrategia_rf_bajo_demanda(monkeypatch):
    import api.services.comparar_inversion as ci
    import api.services.descomposicion_retorno as dr
    import api.services.sensibilidad as se

    llamadas = []
    # kwargs-only como el wrapper @cached real (batería: posicional explotaba)
    monkeypatch.setattr(ci, "comparar", lambda *, a_id, b_id, monto, moneda_input="ARS": (
        llamadas.append(("comparar", a_id, b_id)) or {
            "a": {"flujos": [{"monto": 600000.0}, {"monto": 550000.0}],
                  "moneda": "ARS", "vencimiento": "2027-06-30"},
            "b": {"flujos": [{"monto": 1180000.0}], "moneda": "ARS"},
            "meta": {"warnings": []},
        }))
    monkeypatch.setattr(se, "sensibilidad_retorno_total",
                        lambda *, curva, modo="absoluta", tirs=(): [])
    monkeypatch.setattr(dr, "descomposicion_realizada",
                        lambda *, desde, hasta, curva: {"bonos": [
                            {"ticker_corto": "TX26", "r_total": 0.031,
                             "carry": 0.028, "rolldown": 0.002, "cambio_tasa": 0.001},
                        ]})

    filas = [{"ticker_corto": "TX26", "curva_label": "cer", "tipo": "cer"},
             {"ticker_corto": "S31O5", "curva_label": "tasa_fija", "tipo": "tasa_fija"}]
    partes = copiloto._estrategia_rf(filas, "¿TX26 o S31O5 para 6 meses?", [])
    texto = "\n".join(partes)
    # nombró dos bonos → comparación completa con ids curvas:<ticker>
    assert llamadas and llamadas[0] == ("comparar", "curvas:TX26", "curvas:S31O5")
    assert "cobra 1150000 ARS en 2 pagos" in texto
    # TX26 es CER → descomposición de 30 días
    assert "descomposición TX26" in texto and "carry 2.80%" in texto
    # sin bonos nombrados → nada (no engorda el contexto base)
    assert copiloto._estrategia_rf(filas, "¿cómo está la curva?", []) == []


def test_jerga_permitida_por_vista():
    cfg_rf = copiloto.VISTAS["renta_fija"]
    # en renta fija, TEA/bps/duration SON el idioma → no disparan
    assert copiloto._jerga_en_respuesta(
        "TX26 rinde 41.85% de tea con residuo de +85 bps y duration 1.2",
        cfg_rf, "¿cómo está TX26?",
    ) == []
    # pero la jerga fija del sistema sigue prohibida acá también
    jerga = copiloto._jerga_en_respuesta("los que tienen es_ia prendido", cfg_rf, "¿y?")
    assert "es_ia" in jerga


def test_vigia_disparadores(monkeypatch):
    import api.services.scanner_sql as ssql
    import api.services.trading_pivots as tp

    # T1: RKLB (tarjeta) pegado a PP (dist +0.06% < 0.20)
    monkeypatch.setattr(tp, "get_pivots", lambda *, tickers: [{
        "ticker": "RKLB", "high": 10800.0, "low": 10400.0, "close": 10560.0,
        "last": 10580.0, "vwap": 10533.0,
        "pivots": {"pp": 10586.7, "r1": 10773.3, "r2": 10986.7, "r3": 11173.3,
                   "s1": 10373.3, "s2": 10186.7, "s3": 9973.3},
    }])
    # T2: GGAL top volumen, +5.2% hoy, fuera de tarjetas, a 0.1% de R1
    monkeypatch.setattr(ssql, "get_cedears_scanner", lambda: [
        {"ticker_corto": "GGAL", "vs_1d_pct": 5.2, "total_money": 9e9, "rubro": None},
        {"ticker_corto": "RKLB", "vs_1d_pct": -1.0, "total_money": 5e9, "rubro": "Espacial"},
        {"ticker_corto": "KO", "vs_1d_pct": 0.5, "total_money": 8e9, "rubro": None},
    ])
    monkeypatch.setattr(tp, "pivot_radar", lambda: [
        {"ticker": "GGAL", "last": 8350.0, "nivel": "R1", "nivel_precio": 8358.0,
         "dist_pct": 0.10},
        {"ticker": "KO", "last": 100.0, "nivel": "PP", "nivel_precio": 100.05,
         "dist_pct": 0.05},  # cerca de nivel pero NO es mover → no dispara
    ])
    # rueda viva forzada (el vigía no dispara fuera de rueda)
    monkeypatch.setattr(copiloto, "_estado_mercado",
                        lambda ahora, habil: ("RUEDA VIVA — tramo de la mañana", "x"))

    out = copiloto.vigia({"tickers": ["RKLB"]})
    tipos = {a["tipo"]: a for a in out["alertas"]}
    assert "nivel_card" in tipos and tipos["nivel_card"]["ticker"] == "RKLB"
    assert "PP" in tipos["nivel_card"]["mensaje"]
    assert "radar" in tipos and tipos["radar"]["ticker"] == "GGAL"
    assert tipos["radar"]["accion_agregar"] == "GGAL"
    assert all(a["ticker"] != "KO" for a in out["alertas"])  # no-mover no dispara

    # fuera de rueda: silencio total
    monkeypatch.setattr(copiloto, "_estado_mercado",
                        lambda ahora, habil: ("CERRADO (cerró 17:00)", "x"))
    assert copiloto.vigia({"tickers": ["RKLB"]})["alertas"] == []


def test_feedback_valor_invalido():
    assert copiloto.registrar_feedback(1, 5, "u@x.com") == {
        "ok": False, "error": "valor_invalido",
    }


# ── Vistas nuevas 2026-07-14: AGRO · OPCIONES (derivados) · ONs ──────────────


def test_vistas_nuevas_registradas():
    """Congela el contrato de las 3 vistas sumadas el 2026-07-14."""
    agro = copiloto.VISTAS["agro"]
    assert agro["modulo"] == "agro" and agro["dominio"] and agro["chips"]
    opc = copiloto.VISTAS["derivados"]
    assert opc["modulo"] == "derivados" and opc["dominio"] and opc["chips"]
    ons = copiloto.VISTAS["ons"]
    # la página /ons vive bajo renta-fija en la nav — mismo gate
    assert ons["modulo"] == "renta-fija" and ons["dominio"] and ons["chips"]


def test_fetch_agro_aplana_y_normaliza(monkeypatch):
    from api.services import agro_sql

    monkeypatch.setattr(agro_sql, "get_pase_agro", lambda: {
        "bloques": [{
            "commodity": "SOJA",
            "rows": [
                {"tipo": "pizarra", "posicion": "SOJA PIZARRA", "vencimiento": "2026-07-14",
                 "us": 220.0, "ars": 325000.0, "pase": None, "tnav_us": None},
                {"tipo": "dispo", "posicion": "SOJ.ROS.P/DISPO"},  # placeholder → afuera
                {"tipo": "futuro", "posicion": "SOJ.ROS/SEP26", "vencimiento": "20260930",
                 "dias_a_vto": 78, "us": 222.0, "ars": 328000.0, "pase": -2.0,
                 "tnav_us": -0.0421},
            ],
        }],
    })
    filas = copiloto._fetch_agro()
    assert [f["tipo"] for f in filas] == ["pizarra", "futuro"]  # dispo salteada
    # tnav fracción → % (lección v1.34)
    assert round(filas[1]["tnav"], 2) == -4.21
    assert filas[1]["pase"] == -2.0 and filas[1]["commodity"] == "SOJA"


def test_fetch_opciones_filtra_sin_precio_y_normaliza_iv(monkeypatch):
    from api.services import opciones_sql

    monkeypatch.setattr(opciones_sql, "get_opciones", lambda: [
        {"instrumento": "GFGC47346J", "tipo": "CALL", "strike": 47346.0,
         "vence": "2026-08-21", "last": 850.0, "bid": 840.0, "offer": 860.0,
         "iv": 0.412, "spot": 47500.0, "ev": 1200},
        {"instrumento": "GFGC99999J", "tipo": "CALL", "strike": 99999.0,
         "vence": "2026-08-21", "last": 0, "bid": 0, "offer": 0, "iv": None},
    ])
    filas = copiloto._fetch_opciones()
    assert len(filas) == 1  # el strike sin cotizar no viaja
    assert round(filas[0]["iv"], 1) == 41.2  # fracción → %


def test_fetch_ons_normaliza_tea_y_sector(monkeypatch):
    from api.services import renta_fija_sql

    def fake_listar(**kwargs):  # @cached → kwargs SIEMPRE (el clásico del repo)
        assert kwargs["curva"] == "on"
        return [
            {"ticker_corto": "YMCXO", "emisor": "YPF", "sector": "on_energia",
             "moneda": "USD", "fecha_vencimiento": "2031-06-30", "meses_al_vto": 59.4,
             "ultimo_precio": 102.5, "tea": 0.0745, "duration": 3.9,
             "paridad": 98.2, "total_nominals_dia": 15000},
            {"ticker_corto": "XXXX", "emisor": None, "sector": None, "moneda": "ARS",
             "fecha_vencimiento": "2027-01-01", "meses_al_vto": 5.5,
             "ultimo_precio": None, "tea": None, "duration": None,
             "paridad": None, "total_nominals_dia": None},
        ]

    monkeypatch.setattr(renta_fija_sql, "listar_curva", fake_listar)
    filas = copiloto._fetch_ons()
    assert round(filas[0]["tea"], 2) == 7.45      # fracción → %
    assert filas[0]["sector_label"] == "energia"
    assert filas[1]["sector_label"] == "otros"          # sin sector → otros
    assert filas[1]["tea"] is None                      # None no revienta


def test_extras_ons_promedio_por_sector_y_moneda():
    filas = [
        {"sector_label": "energia", "moneda": "USD", "tea": 8.0},
        {"sector_label": "energia", "moneda": "USD", "tea": 6.0},
        {"sector_label": "energia", "moneda": "ARS", "tea": 40.0},
        {"sector_label": "otros", "moneda": "USD", "tea": None},  # sin TEA no cuenta
    ]
    bloque = "\n".join(copiloto._extras_ons(filas, "panorama", []))
    assert "energia (USD): 2 ONs · TEA promedio 7.00%" in bloque
    assert "energia (ARS): 1 ONs · TEA promedio 40.00%" in bloque


def test_verificador_puntuacion_final_pegada():
    """Batería ONs 2026-07-14: 'operó 634.100, y…' tokenizaba '634.100,' (con la
    coma de la FRASE pegada) → todos los parseos fallaban → bloqueo de respuestas
    correctas. La puntuación final se descarta antes de interpretar."""
    assert 634100.0 in copiloto._candidatos_numericos("634.100,")
    assert 651209.0 in copiloto._candidatos_numericos("651.209.")
    contexto = "YMCXO\t634100\nYM42O\t651209"
    malos, _ = copiloto._numeros_sin_respaldo(
        "La más líquida operó 634.100, y le siguió otra con 651.209.", contexto)
    assert malos == []


def test_sanear_params_trading_cap_12():
    """Fallo real 2026-07-17: el frontend (trading-view.tsx SLOTS=12) manda hasta
    12 cards pero el backend topeaba en 8 → las de la 9ª en adelante se truncaban
    en silencio y el copiloto negaba cards que el trader tenía. El cap DEBE ser 12
    (matchea SLOTS). Este test congela el contrato para que CI cace el drift."""
    doce = [f"TK{i}" for i in range(12)]
    tickers, _sel, _ov = copiloto._sanear_params_trading({"tickers": doce})
    assert len(tickers) == 12
    assert "TK11" in tickers  # la 12ª ya no se pierde
