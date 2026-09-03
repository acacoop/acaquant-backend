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

import inspect
from datetime import date

from api.services import bancos
from jobs.interbanking_sync import (
    DIAS_ATRAS_DEFAULT,
    FECHAS_A_MANTENER,
    run,
    ventana,
)


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


# --------------------------------------------------------------------------- #
# LA VENTANA CUBRE EXACTAMENTE LO QUE SE CONSERVA (2026-09-03)
# --------------------------------------------------------------------------- #
# El bug: la ventana pedía 1 día hábil hacia atrás (2 fechas) mientras la purga
# conservaba 3. Ese desfasaje de UNA fecha significa que **el día más viejo de la
# base es uno que ya nunca se vuelve a pedir**: queda congelado con lo que hubiera
# ese día y, si el banco publica un movimiento con atraso o lo corrige, no entra
# nunca más.
#
# No falla nada cuando pasa. La fila existe, el job dice «ok», el saldo está — lo
# único que falta es el DETALLE que lo explica, y eso en pantalla se ve igual que
# «ese día no hubo movimientos».
#
# Caso real: BBVA 2820352686 el 02/09/2026. El saldo saltó de 10.321,77 a
# −127.566,23 (el banco confirma el salto por otro campo) y el extracto de ese día
# no traía UN SOLO movimiento. El 03/09 fue la última corrida que lo pidió.

def test_la_ventana_cubre_TODAS_las_fechas_que_se_conservan():
    """El invariante que cierra el agujero: se re-pide todo lo que se guarda.

    Si `DIAS_ATRAS_DEFAULT` vuelve a quedar por debajo de `FECHAS_A_MANTENER − 1`,
    la fecha más vieja de la base deja de re-sincronizarse y un movimiento
    publicado con atraso no entra nunca.
    """
    assert DIAS_ATRAS_DEFAULT >= FECHAS_A_MANTENER - 1, (
        f"la ventana ({DIAS_ATRAS_DEFAULT} hábiles = {DIAS_ATRAS_DEFAULT + 1} "
        f"fechas) no llega a cubrir las {FECHAS_A_MANTENER} fechas que conserva "
        "la purga: el día más viejo queda congelado sin poder corregirse")


def test_la_ventana_no_trae_de_MAS_lo_que_la_purga_borra_en_la_misma_corrida():
    """El otro lado del invariante. Traer más días de los que se conservan es
    gastar páginas de API en datos que `purgar()` borra al final de la MISMA
    corrida — y encima hace creer que hay historia donde no la hay."""
    assert DIAS_ATRAS_DEFAULT <= FECHAS_A_MANTENER - 1, (
        "la ventana trae fechas que la purga borra en la misma corrida")


def test_el_default_de_la_ventana_es_el_MISMO_en_los_tres_lugares():
    """⚠️ El default estaba escrito a mano en `ventana()`, en `run()` y en el
    `--dias` del CLI. Tres copias de la misma decisión sin árbitro (REGLA #9):
    se corrige una, el cron sigue llamando a otra y **no falla nada** — el job
    corre en verde pidiendo la ventana vieja."""
    assert inspect.signature(ventana).parameters["dias_atras"].default == \
        DIAS_ATRAS_DEFAULT
    assert inspect.signature(run).parameters["dias_atras"].default == \
        DIAS_ATRAS_DEFAULT


def test_con_la_ventana_nueva_el_dia_de_ANTEAYER_se_vuelve_a_pedir():
    """El caso del 02/09 traducido a fechas: corriendo el viernes 04/09, el
    miércoles 02/09 **tiene que seguir dentro** del rango que se le pide al banco.
    Con el default viejo (1) la ventana era 03..04 y el 02 quedaba afuera."""
    desde, hasta = ventana(hoy=date(2026, 9, 4))        # viernes
    assert (desde, hasta) == (date(2026, 9, 2), date(2026, 9, 4))


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


def test_no_se_purga_si_la_corrida_no_trajo_nada(monkeypatch):
    """La purga borra días viejos. Si Interbanking está caído y la corrida no
    guardó nada, purgar igual dejaría la base con MENOS días de los que tenía —
    un borrado silencioso causado por una caída del proveedor.

    Es el invariante que hace segura la retención de 3 fechas.
    """
    from jobs import interbanking_sync as job

    llamadas = []
    monkeypatch.setattr(job, "sincronizar_cuentas", lambda **kw: [])
    monkeypatch.setattr(job, "purgar", lambda *a, **k: llamadas.append(1) or {})

    stats = job.run()
    assert stats["cuentas_ok"] == 0
    assert llamadas == [], "purgó con una corrida que no trajo una sola cuenta"
