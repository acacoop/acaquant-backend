"""EL AGENTE CONOCE EL CALENDARIO (§0.co).

El user, un sábado con 7 «volvió», 11 «apareció» y 35 «se arregló»:

    *«Hoy es SÁBADO. El mercado no abre. No puede pasar que un día no hábil se
    rompa algo, ni que se cuente para los días de si volvió o no algo. El
    agente tiene que saber que es un día no hábil: hay un universo de cosas
    que NO pueden pasar — y si pasan, es porque hay algo mal configurado.»*

Tres piezas, congeladas acá:

    1. `en_rueda` sabe de FERIADOS (miraba solo weekday < 5)
    2. en día no hábil, la ACTIVIDAD de mercado es el hallazgo
    3. el reloj de la prueba (hitos) corre en días HÁBILES — el finde no
       prueba nada, porque nada corre que pueda contradecir al arreglo
"""
from __future__ import annotations

from datetime import UTC, datetime

from core import ciclo

# ── 1. en_rueda / dia_habil, con feriados ───────────────────────────────────

def test_en_rueda_dice_NO_un_sabado_aunque_sea_horario_de_rueda():
    from api.services import av_agent
    sabado = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)     # sábado, 12:00 ART
    assert not av_agent.en_rueda(sabado)
    assert not av_agent.dia_habil(sabado)


def test_en_rueda_dice_NO_un_feriado_entre_semana():
    """Miraba solo `weekday < 5`: el 9 de Julio (2026 cae jueves) contaba como
    rueda y los detectores corrían sobre una foto vieja — el churn del sábado
    con disfraz de miércoles."""
    from api.services import av_agent
    independencia = datetime(2026, 7, 9, 15, 0, tzinfo=UTC)
    assert not av_agent.en_rueda(independencia)
    assert not av_agent.dia_habil(independencia)


def test_en_rueda_dice_SI_un_miercoles_comun_en_horario():
    from api.services import av_agent
    miercoles = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    assert av_agent.en_rueda(miercoles)
    assert av_agent.dia_habil(miercoles)


# ── 2. la actividad indebida ────────────────────────────────────────────────

def test_en_dia_HABIL_no_hay_universo_prohibido_y_no_se_toca_la_base(monkeypatch):
    """En hábil devuelve [] SIN una query: el chequeo no aplica. Si tocara la
    base, el fake de abajo explotaría."""
    from api.services import av_agent

    def _boom():
        raise AssertionError("no tenía que tocar la base")
    monkeypatch.setattr("core.postgres.get_pool", _boom)
    miercoles = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    assert av_agent.detectar_actividad_no_habil(miercoles) == []


def test_la_actividad_del_detector_es_ALTA_y_dice_el_dia():
    """El motivo tiene que nombrar el día (sábado/domingo/feriado) y la causa
    probable: sin eso la fila es un misterio en vez de un diagnóstico."""
    import inspect

    from api.services import av_agent
    src = inspect.getsource(av_agent.detectar_actividad_no_habil)
    assert '"severidad": "alta"' in src
    assert "quedó prendido" in src and "sábado" in src
    # Y si la base no contesta LEVANTA (no hay try adentro): tragarse el error
    # marcaría la pasada como evaluada y cerraría hallazgos sin mirar (§0.be).
    assert "except" not in src.split('"""')[2]


# ── 3. el reloj de la prueba corre en días hábiles ──────────────────────────

def test_el_finde_NO_suma_dias_de_prueba():
    """Resuelto viernes al mediodía: el domingo lleva 0.5 días de prueba (la
    tarde del viernes), no 2 — sábado y domingo no corren detectores que
    puedan contradecir al arreglo."""
    viernes_mediodia = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)  # 12:00 ART
    domingo = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    d = ciclo.dias_de_prueba(viernes_mediodia, domingo)
    assert 0.4 < d < 0.6
    assert ciclo.hitos_cumplidos(d) == 0     # el finde no regala el hito 1


def test_el_lunes_al_mediodia_recien_lleva_UN_dia_habil():
    viernes_mediodia = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)
    lunes_mediodia = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)
    d = ciclo.dias_de_prueba(viernes_mediodia, lunes_mediodia)
    assert 0.9 < d < 1.1
    assert ciclo.hitos_cumplidos(d) >= 1


def test_una_semana_con_FERIADO_suma_cuatro_y_medio():
    """La semana al lunes 17/08/2026 tiene un feriado adentro (San Martín cae
    ese lunes): lunes-a-lunes al mediodía son 4.5 hábiles, no 5. El primer
    borrador de este test asumió 5 y el reloj lo corrigió — que es exactamente
    el bug de calendario que las copias con `weekday < 5` no ven."""
    lunes = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    lunes_feriado = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
    d = ciclo.dias_de_prueba(lunes, lunes_feriado)
    assert 4.4 < d < 4.6
    # Y la quincena entera (al lunes 24) suma 9 hábiles: 10 menos el feriado.
    lunes_limpio = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)
    assert 8.9 < ciclo.dias_de_prueba(lunes, lunes_limpio) < 9.1


def test_dias_resuelto_usa_el_reloj_HABIL_y_dias_abierto_el_CALENDARIO():
    """La asimetría es a propósito: un problema abierto molesta también el
    sábado; un arreglo solo se prueba cuando algo corre que pueda romperlo."""
    it = ciclo.Item(clave="x", tipo="hallazgo",
                    abierto_at="2026-08-21T15:00:00+00:00",
                    resuelto_at="2026-08-21T15:00:00+00:00",
                    estado=ciclo.RESUELTO)
    domingo = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    assert it.dias_abierto(domingo) > 1.9        # calendario: 2 días
    assert it.dias_resuelto(domingo) < 0.6       # hábil: media tarde de viernes


def test_una_fecha_rota_devuelve_cero_tambien_en_el_reloj_habil():
    assert ciclo.dias_de_prueba("no-es-una-fecha") == 0.0
    assert ciclo.dias_de_prueba(None) == 0.0
