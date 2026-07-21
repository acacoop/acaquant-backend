"""Tests del asistente de negocio (QuantAI P7) — orquestador + RBAC.

Los dos tests que NO pueden fallar jamás:
1. INVARIANTE ARQUITECTÓNICO: a core.llm.chat no le llega NUNCA una identidad
   real — todo pasó por la aduana (mensaje, historial y resultados de tools).
2. RBAC congelado: módulo `asistente` existe, es admin-only por default y
   JAMÁS está en INVITADO_MODULES (REGLA #8 — habla del negocio de la mesa).
"""
from __future__ import annotations

import pytest

from api.services import asistente
from core import ai, llm
from core import pii_gateway as pg

CATALOGO_FAKE = {
    "ids": {"805"},
    "nombres": {"juan perez": "805"},
    "tokens": {"perez": "805"},
    "documentos": set(),
}

PROHIBIDAS = ("juan", "perez", "805")


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    monkeypatch.setattr(pg, "_catalogo", lambda: CATALOGO_FAKE)
    monkeypatch.setattr(pg, "cargar_mapping", lambda cid, em: pg._mapping_nuevo())
    monkeypatch.setattr(pg, "guardar_mapping", lambda cid, em, m: None)
    monkeypatch.setattr(asistente, "_cargar_historial", lambda cid: [])
    monkeypatch.setattr(asistente, "_persistir", lambda cid, em, t: None)
    monkeypatch.setattr(ai, "motivo_presupuesto", lambda u: None)
    monkeypatch.setattr(ai, "_trazar", lambda *a, **kw: 7)


def _espiar_llm(monkeypatch, respuestas=None):
    """Reemplaza core.llm.chat por un espía que acumula TODO lo que viajaría
    al proveedor y devuelve las respuestas dadas (en fichas)."""
    payloads: list[list[dict]] = []
    cola = list(respuestas or [llm.RespuestaLLM(ok=True, texto="CLIENTE_1 viene bien",
                                                mensaje={"content": "x"})])

    def chat_fake(mensajes, **kw):
        payloads.append([dict(m) for m in mensajes])
        return cola.pop(0) if len(cola) > 1 else cola[0]

    monkeypatch.setattr(llm, "chat", chat_fake)
    return payloads


def _todo_el_texto(payloads) -> str:
    partes = []
    for llamada in payloads:
        for m in llamada:
            partes.append(str(m.get("content") or ""))
    return " ".join(partes).lower()


# ── 1. el invariante ─────────────────────────────────────────────────────────

def test_invariante_ninguna_identidad_llega_a_llm(monkeypatch):
    payloads = _espiar_llm(monkeypatch)
    r = asistente.responder(mensaje="¿cómo viene la cuenta de Juan Perez?",
                            email="jefe@acavalores.com.ar")
    assert r["ok"], r
    texto_viajado = _todo_el_texto(payloads)
    for palabra in PROHIBIDAS:
        assert palabra not in texto_viajado, f"LEAK hacia el proveedor: {palabra!r}"
    assert r["respuesta"] != "CLIENTE_1 viene bien"        # se detokenizó
    assert "CLIENTE_1" not in r["respuesta"]


def test_invariante_historial_re_tokenizado(monkeypatch):
    monkeypatch.setattr(asistente, "_cargar_historial", lambda cid: [
        ("user", "dame la data de Juan Perez"),
        ("assistant", "Juan Perez viene bien"),
    ])
    payloads = _espiar_llm(monkeypatch)
    asistente.responder(mensaje="¿y este mes?", email="jefe@x.com", chat_id="abc")
    texto_viajado = _todo_el_texto(payloads)
    for palabra in PROHIBIDAS:
        assert palabra not in texto_viajado, f"LEAK vía historial: {palabra!r}"


def test_respuesta_final_detokenizada(monkeypatch):
    _espiar_llm(monkeypatch)
    r = asistente.responder(mensaje="cómo viene Juan Perez", email="jefe@x.com")
    assert "Juan Perez" in r["respuesta"]


def test_transcript_guarda_nombres_reales(monkeypatch):
    guardado = []
    monkeypatch.setattr(asistente, "_persistir",
                        lambda cid, em, turnos: guardado.extend(turnos))
    _espiar_llm(monkeypatch)
    asistente.responder(mensaje="cómo viene Juan Perez", email="jefe@x.com")
    assert guardado[0] == ("user", "cómo viene Juan Perez")
    assert "Juan Perez" in guardado[1][1]


# ── degradación / fail-closed ────────────────────────────────────────────────

def test_fail_closed_sin_catalogo(monkeypatch):
    monkeypatch.setattr(pg, "catalogo_disponible", lambda: False)
    llamadas = _espiar_llm(monkeypatch)
    r = asistente.responder(mensaje="hola", email="jefe@x.com")
    assert r["ok"] is False and r["motivo"] == "aduana"
    assert not llamadas  # ni un byte viajó


def test_chat_ajeno_rechazado(monkeypatch):
    monkeypatch.setattr(pg, "cargar_mapping", lambda cid, em: None)
    r = asistente.responder(mensaje="hola", email="otro@x.com", chat_id="de-otro")
    assert r["ok"] is False and r["motivo"] == "chat_ajeno"


def test_sin_credencial_apagado(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = asistente.responder(mensaje="hola", email="jefe@x.com")
    assert r["ok"] is False and r["motivo"] == "ia_apagada"


def test_presupuesto_agotado_mensaje_claro(monkeypatch):
    monkeypatch.setattr(ai, "motivo_presupuesto", lambda u: "usuario")
    r = asistente.responder(mensaje="hola", email="jefe@x.com")
    assert r["ok"] is False and r["motivo"] == "presupuesto"
    assert "tu cupo" in r["mensaje"]


def test_llm_caido_degrada(monkeypatch):
    _espiar_llm(monkeypatch, [llm.RespuestaLLM(ok=False, error="HTTP 500")])
    r = asistente.responder(mensaje="hola", email="jefe@x.com")
    assert r["ok"] is False and r["motivo"] == "llm"
    assert "chat_id" in r  # el front puede reintentar en el mismo chat


# ── 2. RBAC congelado (REGLA #8) ─────────────────────────────────────────────

def test_rbac_modulo_asistente_existe_y_es_admin_only():
    from core.roles import DEFAULT_MATRIX, INVITADO_MODULES, MODULES
    assert "asistente" in MODULES
    assert "asistente" in DEFAULT_MATRIX["admin"]
    for rol, mods in DEFAULT_MATRIX.items():
        if rol != "admin":
            assert "asistente" not in mods, f"asistente filtrado al rol {rol}"
    assert "asistente" not in INVITADO_MODULES  # REGLA #8 — jamás el portal www


# ── 3. la vista `negocio` del copiloto (M2: un solo asistente, mismo panel) ──

def test_negocio_registrada_con_gate_y_handler():
    from api.services.copiloto import VISTAS
    cfg = VISTAS["negocio"]
    assert cfg["modulo"] == "asistente"          # gate fino: solo jefes
    assert cfg.get("solo_internos") is True      # REGLA #8: jamás el portal www
    assert callable(cfg.get("handler"))
    assert "fetch" not in cfg                    # sin tabla: el cerebro es el asistente


def test_negocio_invitado_jamas():
    from api.services.copiloto.derivacion import _acceso
    from api.services.copiloto.registro import VISTAS
    assert _acceso("guest:alguien@aca.com.ar", VISTAS["negocio"]) is False


def test_motor_despacha_al_handler(monkeypatch):
    from api.services import copiloto
    visto = {}

    def handler_fake(**kw):
        visto.update(kw)
        return {"ok": True, "respuesta": "hola", "traza_id": 1,
                "vista_sugerida": None, "numeros_sin_respaldo": []}

    monkeypatch.setitem(copiloto.VISTAS["negocio"], "handler", handler_fake)
    r = copiloto.preguntar(vista="negocio", pregunta="resumen",
                           usuario="jefe@x.com", conv_id="c1")
    assert r["ok"] and visto["conv_id"] == "c1" and visto["usuario"] == "jefe@x.com"


def test_handler_negocio_adapta_respuesta(monkeypatch):
    from api.services import asistente as asx
    from api.services.copiloto.negocio import _handler_negocio
    monkeypatch.setattr(asx, "responder",
                        lambda **kw: {"ok": True, "chat_id": kw["chat_id"],
                                      "respuesta": "todo bien", "traza_id": 9})
    monkeypatch.setattr("api.services.copiloto.motor._marcar_conversacion",
                        lambda t, c: None)
    r = _handler_negocio(pregunta="resumen", usuario="jefe@x.com",
                         conv_id="c9", historial=None, params=None)
    assert r["ok"] and r["respuesta"] == "todo bien" and r["traza_id"] == 9


def test_handler_negocio_mapea_presupuesto(monkeypatch):
    from api.services import asistente as asx
    from api.services.copiloto.negocio import _handler_negocio
    monkeypatch.setattr(asx, "responder",
                        lambda **kw: {"ok": False, "motivo": "presupuesto",
                                      "cual": "global", "mensaje": "x"})
    r = _handler_negocio(pregunta="resumen", usuario="jefe@x.com",
                         conv_id=None, historial=None, params=None)
    assert r == {"ok": False, "error": "presupuesto_global"}
