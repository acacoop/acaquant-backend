"""LO QUE EL AGENTE SABE EXPLICAR — `api/services/av_agent_explicar.py`.

Lo que se congela acá no son los números (esos salen de funciones que ya tenían
sus tests): es el **contrato** que hace confiable una explicación.

  · NO se reimplementa ningún cálculo — se envuelve el que ya usa la pantalla;
  · el modelo redacta, **nunca calcula**: sin modelo la explicación sale igual;
  · una discrepancia entre lo recalculado y lo que muestra la app **no la puede
    tapar la frase** — es el puente entre «me preguntaste» y «te aviso»;
  · las preguntas están escritas como las haría una persona, no como se llama
    la función. Ese era todo el problema de VALIDACIONES.
"""
from __future__ import annotations

import pytest

from api.services import av_agent_explicar as ex


@pytest.fixture(autouse=True)
def _sin_modelo(monkeypatch):
    """Ningún test llama al LLM: mediría la red, no el código."""
    monkeypatch.setattr("core.ai.disponible", lambda t: False)


# ── El catálogo ES el menú del día que se le hable ─────────────────────────

def test_las_preguntas_estan_en_castellano_y_no_dicen_debug():
    """*«Sacándole la palabra DEBUG»* — el user. Una capacidad que se llama
    `debug_curva_tea` no la va a pedir nadie que no sea el que la escribió."""
    for c in ex.catalogo():
        assert c["pregunta"].endswith("?"), c
        assert "debug" not in c["pregunta"].lower()
        assert len(c["pregunta"]) > 15          # una pregunta, no una etiqueta


def test_cada_explicador_declara_de_donde_saca_los_numeros():
    """Una explicación sin origen no se puede auditar: solo creer o no creer."""
    assert all(c["de_donde"] for c in ex.catalogo())


def test_el_catalogo_y_el_registro_no_pueden_divergir():
    assert {c["id"] for c in ex.catalogo()} == set(ex.EXPLICADORES)


def test_explicador_que_no_existe():
    assert ex.explicar("no.existe")["ok"] is False


def test_el_que_necesita_ticker_lo_pide_antes_de_calcular(monkeypatch):
    """Sin sujeto no se corre nada: arrancar el cálculo para fallar adentro
    gasta una query y devuelve un error peor."""
    corrio = []
    monkeypatch.setattr(ex.EXPLICADORES["rinde"], "explicar",
                        lambda s: corrio.append(s) or {"ok": True, "pasos": []})
    r = ex.explicar("rinde", "")
    assert r["ok"] is False and corrio == []


def test_el_que_NO_necesita_sujeto_corre_sin_el(monkeypatch):
    monkeypatch.setattr(ex.EXPLICADORES["breakeven"], "explicar",
                        lambda s: {"ok": True, "pasos": [], "numeros": {}})
    assert ex.explicar("breakeven")["ok"] is True


# ── El modelo redacta, no calcula ──────────────────────────────────────────

def test_sin_modelo_la_explicacion_SALE_IGUAL(monkeypatch):
    """Una respuesta que depende del modelo para existir es una respuesta que un
    día no está (sin credencial, sin presupuesto, proveedor caído)."""
    monkeypatch.setattr(ex.EXPLICADORES["breakeven"], "explicar", lambda s: {
        "ok": True, "pasos": [ex._paso("a", "Un paso", ex.INFO, "detalle")]})
    r = ex.explicar("breakeven")
    assert r["ok"] is True and r["frase"] == "" and len(r["pasos"]) == 1


def test_al_modelo_se_le_prohibe_calcular(monkeypatch):
    """El prompt tiene que decirlo explícito: recibe números YA calculados. Sin
    esa instrucción, un modelo servicial 'completa' el que falta."""
    capturado = {}
    monkeypatch.setattr("core.ai.disponible", lambda t: True)
    monkeypatch.setattr("core.ai.completar",
                        lambda t, **k: capturado.update(k) or "una frase")
    monkeypatch.setattr(ex.EXPLICADORES["breakeven"], "explicar", lambda s: {
        "ok": True, "pasos": [ex._paso("a", "T", ex.INFO, "d")]})
    ex.explicar("breakeven")
    sistema = capturado["system"].lower()
    assert "no calcules" in sistema and "solo los números que te paso" in sistema


def test_la_frase_del_modelo_no_puede_tapar_la_discrepancia(monkeypatch):
    """**El invariante que convierte esto en un detector.** Si el número no
    cierra, eso viaja aparte y sobrevive a lo que diga el modelo."""
    monkeypatch.setattr("core.ai.disponible", lambda t: True)
    monkeypatch.setattr("core.ai.completar", lambda t, **k: "todo perfecto")
    monkeypatch.setattr(ex.EXPLICADORES["rinde"], "explicar", lambda s: {
        "ok": True, "pasos": [], "discrepancia": "la TEA no coincide"})
    r = ex.explicar("rinde", "AL30")
    assert r["discrepancia"] == "la TEA no coincide"
    assert r["frase"] == "todo perfecto"      # conviven, no se pisan


def test_una_excepcion_del_calculo_no_tumba_la_respuesta(monkeypatch):
    def revienta(_s):
        raise RuntimeError("la base no responde")
    monkeypatch.setattr(ex.EXPLICADORES["rinde"], "explicar", revienta)
    r = ex.explicar("rinde", "AL30")
    assert r["ok"] is False and "RuntimeError" in r["error"]


# ── No se reimplementa ningún cálculo ──────────────────────────────────────

def test_los_explicadores_ENVUELVEN_las_funciones_que_ya_existen():
    """Dos implementaciones del mismo cálculo terminan dando dos respuestas a la
    misma pregunta — y la pantalla y el agente diciendo cosas distintas."""
    import inspect
    src = inspect.getsource(ex)
    for fn in ("debug_calculo_tea", "debug_soberano", "debug_tna_futuros",
               "breakevens_debug", "debug_4_timeframes"):
        assert fn in src, f"{fn} debería reusarse, no reescribirse"
    # Y la prueba NEGATIVA: acá no se resuelve nada. La palabra "XIRR" puede
    # aparecer —se le explica al usuario qué se hizo— pero un solver, no.
    for solver in ("import numpy", "import scipy", "from quant.curve_fit",
                   "newton(", "brentq(", "def _xirr", "def _tir"):
        assert solver not in src, f"{solver}: acá no se calcula, se envuelve"


# ── El puente a detectar ───────────────────────────────────────────────────

def test_una_diferencia_CHICA_de_TEA_no_es_una_discrepancia():
    """Recalcular nunca da idéntico (redondeos, el precio se movió un tick).
    Marcar cada diferencia sería ruido que enseña a ignorar el aviso."""
    assert ex._discrepancia_tea(
        {"diff": {"TEA": 0.001}, "calculado": {}, "persistido": {}}) == ""


def test_una_diferencia_GRANDE_de_TEA_si_lo_es():
    d = {"diff": {"TEA": 0.08}, "calculado": {"TEA": 0.40},
         "persistido": {"TEA": 0.32}}
    txt = ex._discrepancia_tea(d)
    assert txt and "40,00%" in txt and "32,00%" in txt


def test_sin_diff_no_se_inventa_una_discrepancia():
    assert ex._discrepancia_tea({}) == ""
    assert ex._discrepancia_tea({"diff": {"TEA": None}}) == ""


# ── Los números para leer ──────────────────────────────────────────────────

def test_un_valor_ausente_es_raya_y_NO_cero():
    """No saber cuánto vale algo y que valga cero son cosas distintas, y en una
    explicación el que lee se lleva una conclusión."""
    assert ex._n(None) == "—" and ex._n("") == "—"
    assert ex._n(0) == "0,00"


def test_los_porcentajes_salen_en_escala_humana():
    """`market_snapshot.tea` viaja en FRACCIONES (0.0973 = 9,73%)."""
    assert ex._n(0.0973, 2, pct=True) == "9,73%"


def test_los_miles_se_leen_a_la_argentina():
    assert ex._n(1234567.5) == "1.234.567,50"


# ── Los pasos hablan el idioma del resto del agente ────────────────────────

def test_un_paso_tiene_la_MISMA_forma_que_los_del_alta_de_un_bono():
    """Para que el modal los dibuje igual. Si fueran dos formas, habría dos
    componentes y una de las dos se quedaría vieja."""
    p = ex._paso("k", "T", ex.INFO, "d")
    for campo in ("clave", "titulo", "estado", "detalle", "tabla", "accion",
                  "aviso", "pide", "frena", "frena_auto"):
        assert campo in p


def test_los_pasos_se_numeran_al_final(monkeypatch):
    monkeypatch.setattr(ex.EXPLICADORES["breakeven"], "explicar", lambda s: {
        "ok": True, "pasos": [ex._paso(str(i), "T", ex.INFO, "d") for i in range(3)]})
    assert [p["n"] for p in ex.explicar("breakeven")["pasos"]] == [1, 2, 3]


def test_sugerencias_solo_para_el_que_pide_sujeto():
    assert ex.sugerencias("breakeven") == []
    assert ex.sugerencias("no.existe") == []
