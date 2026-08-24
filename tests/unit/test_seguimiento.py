"""¿Lo que se arregló, siguió arreglado?

> *«Necesito que este agente entienda cuándo hizo algo bien, no solamente porque
> yo le puse "acertó", sino porque queda registrado y al otro día o durante unos
> días puede detectar que los cambios que se marcaron como hechos realmente
> tuvieron consistencia.»* (user, 2026-08-19)

La diferencia entre «lo apliqué» y «funcionó»: verificar releyendo la base en el
mismo segundo solo prueba que la escritura entró — **un símbolo mal puesto se
escribe igual de bien que uno bien puesto**.
"""
from __future__ import annotations

import inspect
import pathlib

from api.services import av_agent_seguimiento as seg


def test_TODAVIA_NO_VOLVIO_no_es_AGUANTO():
    """Sin la ventana de espera estaríamos premiando un arreglo de hace una hora,
    que es justo lo que no queremos medir."""
    src = inspect.getsource(seg.revisar)
    assert "ahora >= hasta" in src, "el veredicto bueno no espera la ventana"
    assert seg.DIAS_DE_PRUEBA >= 3


def test_si_no_se_puede_MIRAR_no_se_cambia_ningun_veredicto():
    """Dar todo por bueno porque no pudimos mirar sería premiar el silencio — lo
    contrario de lo que este módulo hace. Es la regla del AO29 otra vez."""
    r = seg.revisar(None)
    assert r["ok"] is False and "no se cambia" in r["error"]


def test_el_seguimiento_viejo_NO_VOTA():
    """**Devuelve 0 siempre, y es una decisión** (2026-08-24).

    Este mecanismo solo podía emitir ✔. El veredicto sale de
    `clave in claves_abiertas` y las dos mitades arman la clave distinto:

        se anota     `av_agent_acciones` →  accion:objetivo:regla
        se compara   `jobs/seguimiento`  →  tipo:sujeto:regla

    Los ids de acción están namespaceados (`mercado.*`, `assets.*`) y ninguno
    coincide jamás con un tipo de hallazgo: la intersección es vacía por
    construcción, `volvio` no puede pasar nunca y todo lo que cumple la ventana
    se sella `aguanto` → un ✔ `verificado` fabricado, que la compuerta de
    autonomía cuenta igual que un click humano.

    Y desde §0.de es el ÚNICO emisor de `verificado` que quedaba: la compuerta
    dependía entera de una fuente que no puede decir que no.
    """
    assert seg._votar([{"clave": "x"}], [{"clave": "y"}]) == 0


def test_no_queda_NINGUN_camino_al_eval_set_desde_el_seguimiento_viejo():
    """No alcanza con que `_votar` devuelva 0: mientras el módulo pueda escribir
    en `av_agent_evals`, alguien vuelve a enchufarlo sin ver el porqué."""
    src = inspect.getsource(seg)
    assert "av_agent_evals" not in src


def test_se_congela_el_MOTIVO_por_el_que_no_vota():
    """El día que las dos puntas armen la clave igual, este test FALLA — y ahí
    sí hay que volver a habilitar el voto. Es un recordatorio que se dispara
    solo, en vez de una nota en un doc que nadie relee."""
    anota = pathlib.Path(
        "api/services/av_agent_acciones.py").read_text(encoding="utf-8")
    compara = pathlib.Path("jobs/seguimiento.py").read_text(encoding="utf-8")
    assert '{accion}:{objetivo}:{regla}' in anota
    assert "tipo || ':' || ticker || ':' || regla" in compara


def test_re_arreglar_REINICIA_la_prueba():
    """Un arreglo nuevo merece su propia ventana: si heredara la anterior, un
    arreglo de hoy podría darse por bueno mañana con la prueba de la semana
    pasada."""
    src = inspect.getsource(seg.anotar)
    assert "ON CONFLICT (clave) DO UPDATE" in src
    assert "revisiones = 0" in src and "veredicto = 'mirando'" in src


def test_toda_accion_aplicada_ENTRA_en_seguimiento():
    """Si el agente arregla algo y no queda en seguimiento, ese arreglo nunca se
    verifica y su causa nunca gana evidencia real."""
    src = pathlib.Path("api/services/av_agent_acciones.py").read_text(encoding="utf-8")
    assert "av_agent_seguimiento" in src and "seg.anotar(" in src


def test_las_claves_se_arman_IGUAL_que_en_el_centinela():
    """Si cada uno armara la suya, un arreglo se daría por bueno mirando la lista
    equivocada — y el veredicto sería siempre «aguantó»."""
    job = pathlib.Path("jobs/seguimiento.py").read_text(encoding="utf-8")
    assert "tipo || ':' || ticker || ':' || regla" in job
    cent = inspect.getsource(
        __import__("api.services.av_agent_centinela", fromlist=["_clave"])._clave)
    assert "{h.get('tipo')}:" in cent


def test_se_cuentan_las_REAPERTURAS():
    """«Esto ya lo arreglamos tres veces y vuelve» es un dato distinto de «pasa
    hace tres días», y hasta hoy los dos se veían igual."""
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    assert "reaperturas" in sql
    cent = pathlib.Path("api/services/av_agent_centinela.py").read_text(encoding="utf-8")
    assert "reaperturas = agente.av_agent_centinela.reaperturas + " in cent
