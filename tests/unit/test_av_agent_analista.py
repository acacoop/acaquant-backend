"""El ANALISTA del informe masivo: qué se le manda al LLM y qué NO."""
from __future__ import annotations

import inspect

from api.services import av_agent_analista as an


def test_la_IA_nunca_es_la_fuente_de_un_numero():
    """La regla de oro del programa. Al modelo se le pide el PATRÓN sobre casos
    ya diagnosticados; los diagnósticos son deterministas y no se discuten."""
    assert "NO te pido que diagnostiques los casos" in an._INSTRUCCIONES
    assert "NO inventes números" in an._INSTRUCCIONES


def test_el_prompt_lleva_el_dominio_explicado_desde_cero():
    """«Que quede general para que si mañana paso a otra IA lo entienda»: el
    contexto tiene que alcanzar para que un modelo que no vio nunca este sistema
    lea el informe. Por eso están las reglas del negocio, no solo los datos."""
    for concepto in ("PARIDAD", "moneda_flujo", "cer_emision", "XIRR", "base 100"):
        assert concepto in an._DOMINIO, concepto


def test_no_hay_jerga_de_ningun_proveedor_en_el_prompt():
    """El módulo no sabe con quién habla: cambiar de proveedor es tocar
    core/llm.py, no esto."""
    txt = (an._DOMINIO + an._INSTRUCCIONES).lower()
    for marca in ("deepseek", "openai", "gpt", "claude", "anthropic", "gemini"):
        assert marca not in txt, marca
    src = inspect.getsource(an)
    assert "core.llm" not in src and "import llm" not in src


def test_sin_IA_el_informe_determinista_sigue_sirviendo(monkeypatch):
    """Es una capa ARRIBA, nunca un reemplazo: si el LLM no está, se dice y el
    informe queda igual de completo."""
    from core import ai
    monkeypatch.setattr(ai, "disponible", lambda _t: False)
    r = an.analizar({"id": 1, "texto": "algo"})
    assert r["ok"] is False and "determinista" in r["error"]


def test_un_informe_vacio_no_gasta_tokens():
    assert an.analizar({"id": 1, "texto": "  "})["ok"] is False


def test_las_lecciones_se_INYECTAN_como_contexto():
    """El «entrenamiento» es un contexto, no un fine-tuning: cada lección nueva
    mejora el análisis siguiente sin re-entrenar y sin atarse a un proveedor."""
    src = inspect.getsource(an.analizar)
    assert "_lecciones()" in src and "_causas()" in src


def test_el_catalogo_de_causas_dice_QUIEN_arregla_cada_una():
    """Es el dato que decide el orden de ataque: no es lo mismo una causa que el
    agente resuelve solo que una que necesita a una persona."""
    txt = an._causas()
    assert "el agente lo arregla solo" in txt and "necesita una persona" in txt
