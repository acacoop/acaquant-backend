"""Tests del costo estimado de IA (core/llm._PRECIOS + ia_obs).

Por qué existe esto: DeepSeek expone saldo real por API, pero **OpenAI no
tiene endpoint de saldo** — ni con admin key (verificado 2026-07-21). Como
guardamos los tokens de cada llamada en ia.trazas, el gasto se CALCULA. Estos
tests congelan que la cuenta sea correcta y que un modelo sin precio no
invente un número.
"""
from __future__ import annotations

from api.services.ia_obs import _plegar_por_proveedor
from core import llm


def test_costo_simple():
    # gpt-5.6-luna: $1 por 1M in, $6 por 1M out
    c = llm.costo_estimado("gpt-5.6-luna", 1_000_000, 1_000_000)
    assert round(c, 6) == 7.0


def test_costo_descuenta_el_cache():
    """El caché se cobra ~10x/100x menos y se DESCUENTA del input (no se
    suma aparte): 1M in con 1M en caché no puede costar más que sin caché."""
    sin = llm.costo_estimado("gpt-5.6-luna", 1_000_000, 0, 0)
    con = llm.costo_estimado("gpt-5.6-luna", 1_000_000, 0, 1_000_000)
    assert con < sin and round(con, 6) == 0.10


def test_cache_mayor_que_input_no_rompe():
    c = llm.costo_estimado("gpt-5.6-luna", 100, 0, 999_999)
    assert c is not None and c >= 0


def test_modelo_sin_precio_no_inventa():
    assert llm.costo_estimado("modelo-marciano", 1_000_000, 1_000_000) is None


def test_deepseek_es_mucho_mas_barato():
    """La decisión de ruteo se apoya en esto: el copiloto (volumen grande) va
    al barato y solo el asistente de negocio al caro."""
    ds = llm.costo_estimado("deepseek-v4-flash", 1_000_000, 100_000)
    oa = llm.costo_estimado("gpt-5.6-luna", 1_000_000, 100_000)
    assert ds < oa / 5


def test_plegado_suma_costos_y_marca_lo_no_estimable():
    filas = [
        {"modelo": "gpt-5.6-luna", "llamadas": 10, "errores": 1, "tokens": 1100,
         "latencia_ms_avg": 1000, "tokens_in": 1_000_000, "tokens_out": 100_000,
         "cache_hit": 0, "tokens_in_hoy": 500_000, "tokens_out_hoy": 50_000,
         "cache_hit_hoy": 0},
        {"modelo": "deepseek-v4-flash", "llamadas": 90, "errores": 0, "tokens": 900,
         "latencia_ms_avg": 2000, "tokens_in": 1_000_000, "tokens_out": 0,
         "cache_hit": 0, "tokens_in_hoy": 0, "tokens_out_hoy": 0, "cache_hit_hoy": 0},
        {"modelo": "raro-x", "llamadas": 1, "errores": 0, "tokens": 10,
         "latencia_ms_avg": None, "tokens_in": 10, "tokens_out": 0,
         "cache_hit": 0, "tokens_in_hoy": 0, "tokens_out_hoy": 0, "cache_hit_hoy": 0},
    ]
    out = {p["proveedor"]: p for p in _plegar_por_proveedor(filas)}
    assert round(out["openai"]["costo_usd"], 4) == 1.6      # 1.00 + 0.60
    assert round(out["openai"]["costo_usd_hoy"], 4) == 0.8  # 0.50 + 0.30
    assert out["openai"]["no_entrena"] is True
    assert out["deepseek"]["no_entrena"] is False
    # el modelo desconocido cae en 'otro' y NO se estima
    assert out["otro"]["costo_estimable"] is False


def test_latencia_es_promedio_ponderado():
    filas = [
        {"modelo": "gpt-5.6-luna", "llamadas": 90, "errores": 0, "tokens": 0,
         "latencia_ms_avg": 1000, "tokens_in": 0, "tokens_out": 0, "cache_hit": 0,
         "tokens_in_hoy": 0, "tokens_out_hoy": 0, "cache_hit_hoy": 0},
        {"modelo": "gpt-5.6-terra", "llamadas": 10, "errores": 0, "tokens": 0,
         "latencia_ms_avg": 10_000, "tokens_in": 0, "tokens_out": 0, "cache_hit": 0,
         "tokens_in_hoy": 0, "tokens_out_hoy": 0, "cache_hit_hoy": 0},
    ]
    out = _plegar_por_proveedor(filas)[0]
    assert out["latencia_ms_avg"] == 1900  # (90·1000 + 10·10000) / 100


def test_todos_los_modelos_del_registro_tienen_precio():
    """Si mañana se cambia el modelo default por env y no tiene precio, el
    panel diría '—' en silencio. Este test lo caza en los defaults."""
    for prov in llm.proveedores():
        for tier in ("flash", "pro"):
            assert llm.costo_estimado(llm.modelo(tier, prov), 1000, 1000) is not None, (
                f"falta precio de {llm.modelo(tier, prov)} en core/llm._PRECIOS")
