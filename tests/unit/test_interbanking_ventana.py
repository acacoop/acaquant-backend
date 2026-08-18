"""La ventana de sincronización de Interbanking cuenta días HÁBILES.

Por qué esto tiene tests propios y no es un comentario: la ventana es la
decisión del job que se rompe **en silencio**. Si apunta al día equivocado no
falla nada — devuelve cero movimientos, que es exactamente lo mismo que se ve
cuando de verdad no hubo movimientos. No hay excepción, no hay log rojo, no hay
chequeo de SALUD que salte. La pantalla queda vacía y se lee como un dato.

Fue el bug del 2026-08-18: el lunes 17 fue feriado (Paso a la Inmortalidad del
Gral. San Martín), la ventana `ayer + hoy` pidió 17..18 y la tab del back office
mostró cero movimientos. El "ayer" que correspondía era el **viernes 14**.
Lo mismo pasaba todos los lunes, con el domingo.
"""
from __future__ import annotations

from datetime import date

from api.services import bancos
from jobs.interbanking_sync import ventana


def test_martes_post_feriado_retrocede_hasta_el_viernes():
    """EL caso que reportó el back office. Lunes 17/08/2026 = feriado nacional."""
    desde, hasta = ventana(1, hoy=date(2026, 8, 18))   # martes
    assert hasta == date(2026, 8, 18)
    assert desde == date(2026, 8, 14), (
        "el hábil anterior a un martes post-feriado es el VIERNES, no el lunes feriado"
    )


def test_lunes_retrocede_hasta_el_viernes():
    """Sin feriados de por medio: el lunes tiene que mirar el viernes, no el domingo."""
    desde, hasta = ventana(1, hoy=date(2026, 8, 24))   # lunes
    assert (desde, hasta) == (date(2026, 8, 21), date(2026, 8, 24))


def test_dia_normal_es_el_dia_anterior():
    """En el medio de la semana, hábil anterior y calendario anterior coinciden."""
    desde, hasta = ventana(1, hoy=date(2026, 8, 20))   # jueves
    assert (desde, hasta) == (date(2026, 8, 19), date(2026, 8, 20))


def test_correr_un_domingo_igual_llega_hasta_hoy():
    """`hasta` es la fecha de corrida aunque no sea hábil: el rango no puede
    terminar antes del día en que se está corriendo el job."""
    desde, hasta = ventana(1, hoy=date(2026, 8, 23))   # domingo
    assert hasta == date(2026, 8, 23)
    assert desde == date(2026, 8, 21)                  # viernes


def test_cero_dias_atras_es_solo_hoy():
    assert ventana(0, hoy=date(2026, 8, 18)) == (date(2026, 8, 18), date(2026, 8, 18))


def test_el_tope_de_60_dias_es_de_CALENDARIO():
    """Interbanking rechaza consultas de más de 60 días por llamada, y ese tope
    es de calendario. 60 días HÁBILES son ~84 corridos: sin este clamp, un
    `--dias 60` armaría un rango que la API no acepta."""
    desde, hasta = ventana(60, hoy=date(2026, 8, 18))
    assert (hasta - desde).days == 60


def test_el_dia_que_muestra_la_vista_esta_DENTRO_de_lo_que_trae_el_job():
    """La vista es de UN día y el job ingesta una VENTANA. El invariante que
    importa es que el día por defecto de la pantalla caiga adentro de esa
    ventana: si no, mostraría un hueco que no existe en el banco — pediría un día
    que el job nunca trajo."""
    desde, hasta = ventana(1)
    assert desde <= bancos.fecha_default() <= hasta
