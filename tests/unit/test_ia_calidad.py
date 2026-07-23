"""Tests de jobs/ia_calidad.py — el loop de calidad de conversaciones.

Lo crítico: el PRE-FILTRO barato (qué turnos merecen mirada del crítico) y el
SANEO del veredicto (el crítico propone, el código encierra). El LLM no se
llama en los tests.
"""
from __future__ import annotations

from jobs import ia_calidad as qa

# ── pre-filtro: separa lo obviamente sano de lo que merece mirada ────────────

def test_prefiltro_marca_tabla_ordenada():
    """Una tabla que dice 'ordenado' es el caso exacto de la traza 2026-07-23."""
    resp = ("| Papel | Año |\n|---|---|\n| VIST | +37% |\n\n"
            "ordenado por retorno del año")
    assert qa._senal_barata("rankeá energía", resp) is True


def test_prefiltro_marca_deflexion():
    assert qa._senal_barata("vist", "VIST viene bien. ¿La mirás para comprar?") is True
    assert qa._senal_barata("x", "¿Querés A? ¿O preferís B?") is True


def test_prefiltro_marca_tool_muda_y_compromiso():
    assert qa._senal_barata("dame X", "eso no lo tengo acá") is True
    assert qa._senal_barata("agregá Y", "lo dejo planteado como requerimiento") is True


def test_prefiltro_marca_causalidad():
    assert qa._senal_barata("vist", "subió la semana, así que el año se estira") is True


def test_prefiltro_deja_pasar_lo_sano():
    """Una respuesta breve, correcta, sin señales, NO va al crítico (ahorra tokens)."""
    assert qa._senal_barata("precio de AL30", "AL30 cotiza 72.5 en pesos.") is False
    assert qa._senal_barata("cuánto rinde", "Rinde 8.2% de TEA.") is False


# ── saneo del veredicto: el crítico propone, el código valida ────────────────

def test_saneo_encierra_modo_y_severidad(monkeypatch):
    monkeypatch.setattr(qa.ai, "completar", lambda *a, **k:
                        '{"sospechoso": true, "modo": "INVENTADO", '
                        '"severidad": "critiquísimo", "nota": " x "}')
    v = qa._criticar({"id": 1, "detalle": "q", "respuesta": "r"})
    assert v["modo"] == "otro" and v["severidad"] == "medio"   # fuera del enum → default
    assert v["nota"] == "x"


def test_saneo_sano_no_es_sospechoso(monkeypatch):
    monkeypatch.setattr(qa.ai, "completar", lambda *a, **k: '{"sospechoso": false}')
    assert qa._criticar({"id": 1, "detalle": "q", "respuesta": "r"}) == {"sospechoso": False}


def test_saneo_json_roto_no_marca(monkeypatch):
    """Sin JSON usable, el turno NO se marca (mejor no marcar que marcar mal)."""
    monkeypatch.setattr(qa.ai, "completar", lambda *a, **k: "no sé qué decir")
    assert qa._criticar({"id": 1, "detalle": "q", "respuesta": "r"}) is None


def test_gateway_caido_no_marca(monkeypatch):
    monkeypatch.setattr(qa.ai, "completar", lambda *a, **k: None)
    assert qa._criticar({"id": 1, "detalle": "q", "respuesta": "r"}) is None


def test_parsear_acepta_json_envuelto():
    assert qa._parsear('```json\n{"sospechoso": false}\n```')["sospechoso"] is False


def test_digest_no_lleva_markdown_que_pueda_romperse():
    """1ª corrida real: una nota del crítico con '*'/'[' rompió el parser
    Markdown de Telegram (HTTP 400). El digest va en texto plano, y aunque la
    nota traiga caracteres raros, no debe explotar."""
    flags = [{"traza_id": 644, "modo": "ranking_a_mano", "severidad": "alto",
              "nota": "dice que GD35 lidera con 8.25%, pero GD41 tiene 8.35% "
                      "[mayor] *contradiciendo* el orden"}]
    d = qa._digest(flags)
    assert "traza #644" in d and "GD41" in d
    # sin marcado propio que el contenido del modelo pueda dejar sin cerrar
    assert "*Calidad" not in d and "_Revisá" not in d


# ── gen_evals_desde_flags: el harness que cierra detectar -> prevenir ─────────

def test_caso_generado_tiene_pregunta_real_y_hint_por_modo():
    """Un flag se convierte en un borrador de caso con la pregunta REAL y el
    hint del modo — el punto de partida que el humano afina."""
    import datetime

    from scripts import gen_evals_desde_flags as gen
    flag = {"traza_id": 656, "tarea": "copiloto_vista_pro", "modo": "ranking_a_mano",
            "severidad": "medio", "nota": "tabla desordenada",
            "ts": datetime.date(2026, 7, 23),
            "pregunta": "rankeá el sector energía", "respuesta": "VIST +37% ... USO +90%"}
    c = gen._caso(flag)
    assert c["pregunta"] == "rankeá el sector energía"
    assert "ranking_a_mano" in c["id"] and c["_REVISAR"]        # trae el hint
    assert "USO +90%" in c["_respuesta_original"]


def test_hay_hint_para_cada_modo_del_loop():
    """Todo modo que el loop puede emitir tiene un hint — un flag nunca queda
    sin punto de partida."""
    from jobs.ia_calidad import _MODOS
    from scripts.gen_evals_desde_flags import _HINT
    for m in (*_MODOS, "voto_negativo"):
        assert m in _HINT
