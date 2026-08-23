"""EL CONTADOR QUE PROMETÍA UN TRABAJO Y ADENTRO HABÍA OTRO.

El user, 2026-08-23, mirando el modal:

    tab ENCONTRÓ .............. 95
    sub-tab LA LISTA .......... 60      ← un centímetro más abajo

Los dos afirman lo mismo —*cuánto tenés para hacer*— y el criterio estaba
escrito DOS VECES en el front:

    tab padre   !atendido && !es_ruido
    sub-tab     !atendido && !es_ruido && !ignorado && !noticia

Al sumar `ignorado` (§0.cv) y `noticia` (§0.cx) se actualizó una copia y no la
otra. **Es el mismo bug que ya se arregló dos veces en este modal** (el badge
que decía 7 con 10 adentro; el tab que decía 94 con 67): volvió porque las dos
veces se arregló la COPIA y no la causa.

> Un contador de tab promete trabajo. Si dos contadores de la misma lista
> prometen distinto, no hay forma de saber cuál creerle — y se dejan de mirar
> los dos.

El arreglo NO es sincronizar los filtros: es que el front deje de tener
filtros. El número se calcula una sola vez en `av_agent_vista.pide_trabajo` y
las dos pantallas lo LEEN (§0.dd). Estos tests congelan eso — el próximo corte que
alguien agregue entra en un solo lugar o falla acá.
"""
from __future__ import annotations

import inspect

from api.services import av_agent_vista as v

# ── el criterio, entero y en un solo lugar ──────────────────────────────────


def test_lo_que_no_tiene_ninguna_marca_PIDE_trabajo():
    assert v.pide_trabajo({"tipo": "sin_flujo", "atendido": ""}) is True


def test_los_CUATRO_cortes_sacan_la_fila_del_numero():
    """Cada uno es una decisión distinta que ya tomó alguien. Omitir cualquiera
    cambia lo que el número significa — que es exactamente lo que pasó."""
    for marca, valor in (("atendido", "aplicado"), ("atendido", "votado"),
                         ("es_ruido", True), ("ignorado", True),
                         ("noticia", True)):
        assert v.pide_trabajo({"tipo": "x", marca: valor}) is False, marca


def test_atendido_VACIO_no_es_atendido():
    """`atendido` es un string ('' | 'aplicado' | 'votado'), no un bool: si se
    lo evaluara con `is not None` la lista entera desaparecería."""
    assert v.pide_trabajo({"tipo": "x", "atendido": ""}) is True


# ── y NADIE lo vuelve a calcular por su cuenta ──────────────────────────────


def test_la_vista_PUBLICA_el_numero_para_que_el_front_no_lo_derive():
    """Sin este campo, las dos pantallas vuelven a contar cada una por su lado
    — que es el estado del que venimos."""
    src = inspect.getsource(v.vista)
    assert '"por_resolver"' in src
    assert '"por_resolver_tipo"' in src


def test_el_total_y_el_desglose_por_tipo_usan_EL_MISMO_predicado():
    """El desplegable de LA LISTA era la TERCERA copia. Un desglose que no suma
    el total del menú es la misma contradicción, nada más que en chico."""
    src = inspect.getsource(v.vista)
    assert "por_resolver = [h for h in hallazgos if pide_trabajo(h)]" in src
    # El desglose se arma recorriendo ESA lista, no filtrando de nuevo.
    assert "for h in por_resolver:" in src


def test_el_predicado_vive_UNA_sola_vez_en_el_modulo():
    """Si alguien vuelve a escribir los cuatro cortes a mano en otro lado, se
    reabre el agujero. El criterio se escribe en `pide_trabajo` y se llama."""
    src = inspect.getsource(v)
    assert src.count('h.get("noticia")') == 1, (
        "el criterio de trabajo pendiente quedó escrito más de una vez")


def test_las_marcas_se_marcan_pero_NO_se_filtran_del_payload():
    """Las filas siguen viajando para que la pantalla pueda destaparlas y
    contarlas. Esconder sin poder volver atrás es cómo se consigue que nadie
    marque nada."""
    src = inspect.getsource(v.vista)
    assert '"hallazgos": hallazgos' in src
    # Y los contadores de lo escondido siguen yendo SIEMPRE.
    for campo in ('"atendidos"', '"es_ruido"', '"ignorados_hoy"'):
        assert campo in src, campo
