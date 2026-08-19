"""LA RELEVADA a pedido: el botón y el cron, por el mismo camino."""
from __future__ import annotations

import inspect

from api.services import av_agent_relevar as r


def test_dos_clicks_no_gastan_el_doble(monkeypatch):
    """29 créditos por corrida. Dos clicks seguidos —o el botón y el cron a la
    vez— gastarían 58 para escribir lo mismo dos veces."""
    from core import mercado_1816
    # El entorno de test no tiene la key: sin esto el rechazo vendría por «1816
    # no configurado» y el test pasaría sin probar el lock.
    monkeypatch.setattr(mercado_1816, "disponible", lambda: True)
    r._corriendo.update({"si": True, "desde": "x", "por": "nico@aca"})
    try:
        out = r.arrancar(por="otro@aca")
        assert out["ok"] is False and out.get("ya_corriendo") is True
        # Y DICE quién la pidió: «no pasa nada» se lee como que el botón está
        # roto; «ya la pidió nico» se entiende sin preguntar.
        assert "nico@aca" in out["error"]
    finally:
        r._corriendo.update({"si": False, "desde": None, "por": ""})


def test_sin_1816_no_arranca_y_lo_dice(monkeypatch):
    from core import mercado_1816
    monkeypatch.setattr(mercado_1816, "disponible", lambda: False)
    out = r.arrancar()
    assert out["ok"] is False and "1816" in out["error"]


def test_es_EL_MISMO_camino_que_el_job():
    """Dos implementaciones de la misma relevada terminan discrepando sobre qué
    es un hallazgo — y ahí la pantalla y el cron dirían cosas distintas."""
    src = inspect.getsource(r._correr)
    assert "from jobs.av_agent import persistir" in inspect.getsource(r._correr) \
        or "persistir" in src
    assert "av_agent.relevar(" in src


def test_queda_en_job_runs_igual_que_la_corrida_por_cron():
    """Una relevada pedida a mano tiene que verse en SALUD como cualquier otra,
    o el historial de «¿cuándo se miró?» tendría agujeros sin explicación."""
    src = inspect.getsource(r._correr)
    assert "JobRunLogger(\"av_agent\")" in src
    assert 'set_stat("origen", "vista")' in src


def test_el_flag_se_baja_SIEMPRE():
    """Si la corrida explota y el flag queda arriba, el botón queda muerto hasta
    el próximo reinicio de la API."""
    src = inspect.getsource(r._correr)
    i_finally = src.index("finally:")
    assert '_corriendo.update({"si": False' in src[i_finally:]


def test_avisa_cuanto_va_a_tardar():
    """Son ~29 llamadas a 1 por segundo. Sin el aviso, el que aprieta cree que
    se colgó y vuelve a apretar."""
    src = inspect.getsource(r.arrancar)
    assert "segundos_estimados" in src and "créditos" in src


def test_el_cron_de_la_relevada_EXISTE():
    """El hallazgo que originó todo esto: `jobs.av_agent` no estaba en el
    crontab, así que no había ninguna frecuencia — la relevada dependía de que
    alguien se acordara."""
    from pathlib import Path
    cron = Path(__file__).resolve().parents[2] / "deploy" / "crontab.txt"
    txt = cron.read_text(encoding="utf-8")
    assert "python -m jobs.av_agent'" in txt, "la relevada volvió a quedarse sin cron"
