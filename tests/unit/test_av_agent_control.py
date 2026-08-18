"""El TABLERO DE CONTROL del AV Agent: la parada y las fuentes.

Lo que se prueba acá no es el happy path (que la parada frene): es que **no se
pueda olvidar de aplicarla**. Una puerta de escritura nueva que no llame al
guardia no rompe ningún test obvio — simplemente escribe con el agente frenado,
en silencio, que es exactamente el modo de falla que la parada vino a eliminar.
"""
from __future__ import annotations

import inspect
import re

from api.services import av_agent_alta, av_agent_control

# ── LO IMPORTANTE: ninguna puerta se saltea el guardia ──────────────────────

def test_toda_puerta_de_escritura_llama_al_guardia():
    """Cada `aplicar*` público de `av_agent_alta` tiene que consultar la parada.

    Es la misma idea que `core/instrumentos_validos` aplicado en el ÚNICO punto
    por el que pasan todas las suscripciones: si el chequeo vive en un solo lado
    y hay un test que lo exige, una puerta nueva lo hereda o falla el build.
    """
    puertas = [n for n in dir(av_agent_alta)
               if n.startswith("aplicar") and callable(getattr(av_agent_alta, n))]
    assert puertas, "no se encontró ninguna puerta de escritura — ¿se renombraron?"
    sin_guardia = []
    for n in puertas:
        src = inspect.getsource(getattr(av_agent_alta, n))
        if "guardia(" not in src:
            sin_guardia.append(n)
    assert not sin_guardia, (
        f"estas puertas escriben sin consultar la PARADA: {sin_guardia}. "
        f"Agregá `if (frenado := av_agent_control.guardia('<accion>')): return frenado` "
        f"al principio, ANTES de simular (simular gasta créditos de 1816).")


def test_el_guardia_se_llama_antes_de_simular():
    """El orden importa: chequear la parada DESPUÉS de simular gastaría créditos
    de 1816 para después rechazar la escritura."""
    for n in ("aplicar", "aplicar_flujos", "aplicar_arreglo"):
        src = inspect.getsource(getattr(av_agent_alta, n))
        i_guardia = src.index("guardia(")
        m = re.search(r"\bsim = simular", src)
        assert m and i_guardia < m.start(), (
            f"{n}: la parada se chequea después de simular — se gastan créditos "
            f"de 1816 para nada")


# ── El guardia, en sí ───────────────────────────────────────────────────────

def test_sin_parada_deja_pasar(monkeypatch):
    monkeypatch.setattr(av_agent_control, "_leer_parada",
                        lambda: {"parada": False, "motivo": "", "por": "", "leido": True})
    assert av_agent_control.guardia("alta_bono") is None


def test_con_parada_devuelve_el_rechazo_con_forma_de_respuesta(monkeypatch):
    """El rechazo viaja con la MISMA forma que un `aplicar` fallido — así el
    modal lo muestra sin una rama nueva."""
    monkeypatch.setattr(av_agent_control, "_leer_parada",
                        lambda: {"parada": True, "motivo": "estoy revisando la escala",
                                 "por": "nico@aca", "leido": True})
    r = av_agent_control.guardia("arreglar_bono")
    assert r is not None
    assert r["ok"] is False and r["parada"] is True
    # El motivo y el autor van EN el mensaje: el que se lo encuentra frenado
    # tiene que poder decidir si lo reanuda sin ir a preguntar.
    assert "nico@aca" in r["error"] and "estoy revisando la escala" in r["error"]


def test_frenar_exige_motivo():
    r = av_agent_control.set_parada(activa=True, motivo="   ", por="x@y")
    assert r["ok"] is False and "motivo" in r["error"]


# ── La degradación, que es una decisión y no un descuido ────────────────────

def test_sin_haber_leido_nunca_se_permite_escribir(monkeypatch):
    """Proceso recién arrancado o schema sin aplicar → se deja pasar. La parada
    no es un control de seguridad (eso lo dan `require_admin` y el humano que
    aprueba), y fallar cerrado rompería el agente ante un problema de la MISMA
    base donde escribe."""
    monkeypatch.setattr(av_agent_control, "_cache", None)
    monkeypatch.setattr(av_agent_control, "get_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("sin base")))
    assert av_agent_control.guardia("alta_bono") is None


def test_una_parada_activa_sobrevive_a_un_blip_de_la_base(monkeypatch):
    """Lo contrario del anterior, y es el caso que importa: si ya se sabía que
    estaba frenado, un error de lectura NO lo levanta."""
    import time
    monkeypatch.setattr(av_agent_control, "_cache",
                        (time.time() - 999, {"parada": True, "motivo": "m",
                                             "por": "p", "leido": True}))
    monkeypatch.setattr(av_agent_control, "get_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("blip")))
    assert av_agent_control.guardia("alta_bono") is not None


# ── Las fuentes ─────────────────────────────────────────────────────────────

def test_las_fuentes_usan_el_vocabulario_del_preflight():
    """Mismo vocabulario de estados que la cadena, para que el modal las pinte
    con los colores que ya tiene y nadie aprenda una segunda convención."""
    src = inspect.getsource(av_agent_control)
    for estado in ('"ok"', '"revisar"', '"bloquea"'):
        assert estado in src


def test_ninguna_fuente_pega_a_la_red():
    """Un tablero que gasta un crédito cada vez que se mira consume justo el
    recurso que vino a cuidar."""
    src = inspect.getsource(av_agent_control)
    for prohibido in ("mercado_1816.censar", "mercado_1816.balance",
                      "mercado_1816.cashflow", "mercado_1816.indicadores"):
        assert prohibido not in src, f"{prohibido} pega a 1816 y cuesta créditos"
