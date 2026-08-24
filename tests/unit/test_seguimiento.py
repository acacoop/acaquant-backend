"""El SEGUIMIENTO: un solo medidor de «¿el arreglo aguantó?».

Hasta el 2026-08-24 había DOS —`av_agent_seguimiento` (tabla propia, ventana de
5 días corridos) y los HITOS de `av_agent_items`— y el viejo **no podía
funcionar**: armaba la clave `accion:objetivo:regla` y la comparaba contra
`tipo:sujeto:regla`, así que la intersección era vacía por construcción y el
veredicto solo podía ser «aguantó» → un ✔ `verificado` fabricado en la única
señal que la compuerta de autonomía cuenta como humana.

Lo que se prueba acá es que **no vuelva a haber dos**.
"""
from __future__ import annotations

import inspect
import pathlib


def test_el_medidor_VIEJO_no_existe():
    """El service se borró entero. Mientras exista, alguien lo re-enchufa."""
    raiz = pathlib.Path(__file__).resolve().parents[2]
    assert not (raiz / "api/services/av_agent_seguimiento.py").exists()


def test_NADIE_escribe_en_la_tabla_vieja():
    """La tabla queda en la base (borrar datos no se revierte) pero el código no
    la toca: si alguien vuelve a escribirla, vuelven los dos medidores."""
    raiz = pathlib.Path(__file__).resolve().parents[2]
    culpables = []
    for d in ("api", "jobs", "core"):
        for f in (raiz / d).rglob("*.py"):
            txt = f.read_text(encoding="utf-8")
            for verbo in ("INSERT INTO agente.av_agent_seguimiento",
                          "UPDATE agente.av_agent_seguimiento"):
                if verbo in txt:
                    culpables.append(f"{f.name}: {verbo}")
    assert not culpables, culpables


def test_QUEDA_UN_solo_medidor_y_es_el_de_los_hitos():
    from api.services import av_agent_items
    from core import ciclo
    assert callable(av_agent_items.cerrar_hitos)
    # El escalonado, no un plazo único: la señal rápida (día 1) y la confianza
    # que se acumula son dos necesidades distintas.
    assert ciclo.HITOS_DIAS == (1, 2, 3, 7, 14, 30)


def test_el_reloj_de_los_hitos_corre_en_dias_HABILES():
    """El finde no prueba nada: un arreglo del viernes llegaba al hito 1 el
    sábado, con los motores apagados y ningún detector corriendo. Evidencia que
    no pudo contradecirse no es evidencia."""
    from core import ciclo
    src = inspect.getsource(ciclo.dias_de_prueba)
    assert "es_habil" in src and "America/Argentina" in src


def test_el_job_llama_al_medidor_que_quedo():
    src = pathlib.Path(__file__).resolve().parents[2].joinpath(
        "jobs/seguimiento.py").read_text(encoding="utf-8")
    assert "cerrar_hitos()" in src
    assert "av_agent_seguimiento as seg" not in src


def test_la_ACCION_no_cierra_su_propio_arreglo():
    """Quien declara resuelto es el DETECTOR cuando deja de ver el problema. Si
    lo cerrara la acción, el agente estaría calificando su propio trabajo."""
    from api.services import av_agent_hacer
    from core import ciclo
    src = inspect.getsource(av_agent_hacer._mover_item)
    assert f"ciclo.{ciclo.EN_CURSO.upper()}" in src or "EN_CURSO" in src
    assert "RESUELTO" not in src
