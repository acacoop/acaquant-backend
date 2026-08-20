"""LA RESPUESTA QUE LLEGA DESPUÉS — el pasito que faltaba.

El user, mirando la pantalla (2026-08-20):

    *«eso es SUPER INMEDIATO… no es ni 1 seg, ya sabe si da o no da punta. Me
    resulta raro. Acá le falta un pasito más: está bien, sí, pero no terminás de
    entender ni te quedás tranquilo.»*

Tenía razón: `verificar()` corría cero segundos después de `aplicar()` y el
motor levanta la suscripción a los 5 s. «Todavía sin precio» era la ÚNICA frase
que esa función podía devolver.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from api.services import av_agent_respuesta as resp

# ── lo que la acción controla ≠ lo que contesta el mercado ───────────────────

def test_verificar_NO_habla_del_precio():
    """La frase de la verificación inmediata no puede mencionar el precio: en ese
    instante no hay forma de saberlo, y decir algo igual es el cartel que el user
    detectó."""
    import inspect

    from api.services.av_agent_hacer import AccionPataDolar, AccionPedirPata
    for a in (AccionPedirPata(), AccionPataDolar()):
        src = inspect.getsource(a.verificar)
        # Lo único que puede afirmar es que quedó pedida.
        assert "sin precio" not in src, f"{type(a).__name__} sigue opinando del precio"
        assert "YA llegó precio" not in src


def test_las_dos_acciones_de_pata_declaran_su_espera():
    """Sin `espera_s` el seguimiento las cierra en la primera pasada, o sea a los
    5 minutos — que es casi tan inmediato como el bug original."""
    from api.services.av_agent_hacer import ACCIONES, Seguible
    for id_ in ("mercado.pedir_pata", "mercado.pata_dolar"):
        a = ACCIONES[id_]
        assert isinstance(a, Seguible), id_
        assert a.espera_s >= 30 * 60, f"{id_}: {a.espera_s} s es muy poco para un «no»"


# ── el cierre ────────────────────────────────────────────────────────────────

_APLICADO = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)     # en rueda


def _fila(**kw):
    base = {"id": 7, "accion": "mercado.pata_dolar", "sujeto": "BPOB7",
            "campo": "suscripción", "antes": "nadie la pide", "propuesto": "pedir",
            "porque": "", "fuente": "regla", "extra": {},
            "aplicado_at": _APLICADO, "por": "x@y.com"}
    return {**base, **kw}


def _correr(veredicto, *, ahora, fila=None):
    """Corre una pasada con el veredicto que devuelva la acción, sin base."""
    from api.services import av_agent_hacer as h

    cerrados: list[tuple] = []
    with patch.object(resp, "pendientes", lambda: [fila or _fila()]), \
         patch.object(resp, "_cerrar", lambda *a: cerrados.append(a)), \
         patch.object(h.ACCIONES["mercado.pata_dolar"], "veredicto",
                      lambda p: veredicto):
        return resp.revisar(ahora=ahora), cerrados


def test_mientras_no_haya_respuesta_NO_se_cierra():
    """15 minutos de rueda no alcanzan para decir «no cotiza»: es impaciencia,
    no una medición."""
    hallazgos, cerrados = _correr((None, "todavía sin punta"),
                                  ahora=_APLICADO + timedelta(minutes=15))
    assert hallazgos == [] and cerrados == []


def test_si_LLEGA_el_precio_se_cierra_y_se_canta():
    hallazgos, cerrados = _correr((True, "llegó precio 90,76"),
                                  ahora=_APLICADO + timedelta(minutes=6))
    assert len(cerrados) == 1 and cerrados[0][1] is True
    h = hallazgos[0]
    assert h["tipo"] == "respuesta" and h["regla"] == "cotiza"
    assert "SÍ cotiza" in h["motivo"] and "90,76" in h["motivo"]
    # El paso siguiente REAL, que no se automatiza: apuntar el master.
    assert "reiniciar" in h["evidencia"]["texto"]


def test_el_NO_tambien_es_una_respuesta_y_tambien_se_canta():
    """**Es la mitad del pedido.** Un «no» medido cierra el tema; el silencio lo
    deja abierto para siempre — que es el «no te quedás tranquilo»."""
    hallazgos, cerrados = _correr((None, "todavía sin punta"),
                                  ahora=_APLICADO + timedelta(hours=2))
    assert len(cerrados) == 1
    # Se cierra en OK: la acción anduvo. Lo que se confirmó es que del otro lado
    # no hay nada — eso no es un fallo de la acción.
    assert cerrados[0][1] is True
    h = hallazgos[0]
    assert h["regla"] == "no_cotiza" and "NO cotiza" in h["motivo"]
    assert "min de rueda" in h["motivo"] or "h " in h["motivo"]


def test_la_espera_se_mide_en_MERCADO_ABIERTO_y_no_en_reloj():
    """Una pata pedida a las 16:50 ART no estuvo «3 horas sin punta» a las 20:00:
    estuvo 40 minutos y después cerró. Misma regla que §0.u."""
    tarde = datetime(2026, 8, 20, 19, 50, tzinfo=UTC)      # ~16:50 ART, casi cierre
    hallazgos, cerrados = _correr((None, "todavía sin punta"),
                                  ahora=tarde + timedelta(hours=14),
                                  fila=_fila(aplicado_at=tarde))
    # De reloj pasaron 14 h; de RUEDA, apenas los minutos hasta el cierre.
    assert hallazgos == [] and cerrados == [], "cerró contando horas de mercado cerrado"


def test_una_accion_que_ya_no_sigue_no_queda_esperando_para_siempre():
    """Si mañana la acción deja de ser `Seguible`, sus propuestas colgadas se
    cierran: una fila esperando a alguien que no va a venir es basura que crece."""
    cerrados: list[tuple] = []
    with patch.object(resp, "pendientes",
                      lambda: [_fila(accion="mercado.esta_no_existe")]), \
         patch.object(resp, "_cerrar", lambda *a: cerrados.append(a)):
        assert resp.revisar(ahora=_APLICADO) == []
    assert len(cerrados) == 1


def test_nunca_levanta():
    """Corre adentro del monitor de rueda: una excepción acá apagaría los
    detectores del mismo ciclo."""
    with patch.object(resp, "pendientes", side_effect=RuntimeError("base caída")):
        assert resp.revisar() == []


# ── el tipo nuevo está declarado donde tiene que estar ───────────────────────

@pytest.mark.parametrize("reg", ("cotiza", "no_cotiza"))
def test_la_respuesta_VENCE(reg):
    """Es una novedad, no un problema: a las tres horas «BPOB7 no cotiza» ya no
    le sirve a nadie y ocupa el lugar de lo que sí está pasando."""
    from api.services import av_agent
    assert reg in av_agent.VENCE_RAPIDO


def test_el_tipo_respuesta_esta_declarado():
    from api.services import av_agent
    assert "respuesta" in av_agent.ACCION_POR_TIPO
    assert av_agent.dominio_eval("respuesta") == "bono"


# ── el display de las patas ──────────────────────────────────────────────────

def test_dos_patas_en_dos_plazos_no_se_muestran_como_CUATRO():
    """La pantalla decía «4 pata(s) en dólares: BPB7C, BPB7C, BPB7D, BPB7D» — son
    DOS patas, cada una en CI y 24hs, y `_corto()` borra justo el plazo que las
    distingue. Un nombre repetido sin explicación hace dudar de todo lo demás."""
    from api.services.av_agent_pata import _por_pata
    r = _por_pata(["MERV - XMEV - BPB7C - CI", "MERV - XMEV - BPB7C - 24hs",
                   "MERV - XMEV - BPB7D - CI", "MERV - XMEV - BPB7D - 24hs"])
    assert set(r) == {"BPB7C", "BPB7D"}
    assert r["BPB7D"] == ["24hs", "CI"]
