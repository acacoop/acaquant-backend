"""EL REGISTRO DEJA DE SER UNA PILA Y PASA A SER TRAZABLE.

El user, 2026-08-24, mirando HISTORIAL → TODO LO QUE PASÓ:

    *«es horrible ver eso así uno apilado abajo del otro… quiero ver tipo lo de
     si se arregló o no… acá tiene que ser la tabla donde quede bien todo
     trazable»*

**El modelo, que es lo que faltaba entender.** Cada fila del registro es un
EVENTO (*«el 23/08 a las 02:38 se arregló PN4OO»*). Pero *«¿quedó arreglado?»*
**no es una propiedad del evento**: es del OBJETO que el evento tocó, y ese
objeto vive en `av_agent_items` con su ciclo. Un bono acumula muchos eventos
sobre el mismo problema — por eso la respuesta nunca podía estar en la fila.

Son TRES preguntas y se leían como una:

    SALIÓ   ¿la escritura entró?      → instantánea, la contesta la acción
    HOY     ¿el problema se fue?      → solo la contesta el tiempo, y es del OBJETO
    (falta) ¿el diagnóstico acertó?   → el eval set (§0.de)
"""
from __future__ import annotations

import inspect

from api.services import av_agent_acciones as acc

# ── el CÓMO: qué campo se movió, y de qué a qué ─────────────────────────────


def test_solo_sale_lo_que_REALMENTE_cambio():
    """Un campo que no cambió no es información: es ruido que tapa a los que
    sí. La pantalla mostraba el JSON entero y por eso no se leía nada."""
    c = acc._cambios({"moneda": "ARS", "ejes": None}, {"moneda": "USD", "ejes": None})
    assert c == [{"campo": "moneda", "antes": "ARS", "despues": "USD"}]


def test_un_campo_que_NACE_o_que_SE_VA_tambien_es_un_cambio():
    assert acc._cambios({}, {"cer_emision": 1.5}) == [
        {"campo": "cer_emision", "antes": "", "despues": "1.5"}]
    assert acc._cambios({"cer_emision": 1.5}, {}) == [
        {"campo": "cer_emision", "antes": "1.5", "despues": ""}]


def test_None_y_vacio_no_rompen():
    """El libro tiene filas viejas sin `antes`: la tabla no puede caerse por
    eso — mostraría cero eventos en vez de uno incompleto."""
    assert acc._cambios(None, None) == []
    assert acc._cambios(None, {"x": 1}) == [{"campo": "x", "antes": "", "despues": "1"}]
    assert acc._cambios("no soy un dict", {"x": 1}) == []


def test_se_compara_por_REPRESENTACION_no_por_valor():
    """Los jsonb vuelven con tipos que no siempre son comparables, y un `!=`
    sobre dicts anidados diría que cambió todo."""
    assert "str(va) == str(vd)" in inspect.getsource(acc._cambios)


# ── el CRUCE evento → objeto, que es toda la trazabilidad ───────────────────


def test_el_cruce_se_hace_en_UN_solo_viaje():
    """El peaje a Supabase se paga por VIAJE (~8,5 ms) y el registro trae 100
    acciones: una query por fila serían 100 viajes por cada apertura del modal."""
    src = inspect.getsource(acc._estados_de_los_sujetos)
    assert "= ANY(%s)" in src
    assert src.count("cur.execute") == 1


def test_si_el_cruce_FALLA_el_registro_igual_se_ve():
    """Perder el estado del objeto degrada la tabla; tumbarla la borra."""
    src = inspect.getsource(acc._estados_de_los_sujetos)
    assert "except Exception" in src and "return {}" in src


def test_sin_REGLA_no_se_adivina_el_objeto():
    """⚠️ **REGLA #9.** La mayoría de las acciones no guardan qué regla las
    motivó. Elegir uno de los problemas del bono sería inventar el dato —
    medido: 49 de 122 sujetos tienen más de una regla abierta. `estado_objeto`
    queda en None y la pantalla muestra los problemas SIN afirmar cuál se
    tocó."""
    src = inspect.getsource(acc.listar)
    assert 'if (o["regla"] or "") == regla' in src
    assert "None) if regla else None" in src


def test_la_fila_lleva_las_TRES_cosas():
    src = inspect.getsource(acc.listar)
    for campo in ('d["cambios"]', 'd["objetos"]', 'd["estado_objeto"]'):
        assert campo in src, campo


def test_la_REGLA_viaja_al_front():
    """La columna existe en la tabla desde el 2026-08-19 y la proyección del
    registro no la leía: la pantalla no podía decir POR QUÉ se hizo cada cosa
    aunque el dato estuviera guardado."""
    assert "regla" in acc._COLS
