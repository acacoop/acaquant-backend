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


def test_lo_que_VOLVIO_es_un_voto_negativo_CON_MOTIVO():
    """Un ✖ necesita motivo, y acá lo hay y es del mejor tipo: no es una
    impresión, es que el problema reapareció."""
    src = inspect.getsource(seg._votar)
    assert "acierta=False" in src
    assert "volvió a" in src


def test_el_voto_del_tiempo_es_VERIFICADO_y_no_humano():
    """No se puede disfrazar de click: es otra clase de evidencia y se cuenta
    aparte."""
    src = inspect.getsource(seg._votar)
    assert 'origen="verificado"' in src
    assert 'origen="humano"' not in src


def test_no_se_vota_dos_veces_el_mismo_seguimiento():
    """`ref` es único: la revisión diaria vuelve a pasar y no infla el número."""
    src = inspect.getsource(seg._votar)
    assert 'ref=f"seguimiento:' in src


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
    assert "reaperturas = mercado.av_agent_centinela.reaperturas + " in cent
