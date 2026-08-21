"""UN SOLO VOCABULARIO PARA «ABIERTO · LO VI · SE RESOLVIÓ».

El user (2026-08-21), después de la quinta corrección seguida sobre lo mismo:

    *«Los avisos, lo que encuentra, los mensajes… deberían estar codeados como
    OBJETOS CON SUS ESTADOS. Porque si no, esto va a escalar mal y siempre se va
    a solucionar sobre la marcha.»*

Medido: **22 tablas del agente, 8 formas distintas** de decir las mismas tres
cosas. Y la más importante —`av_agent_hallazgos`, la que llena ENCONTRÓ— **no
tiene estado**: es una foto, y su ciclo se deriva en la lectura cruzando otras
cinco tablas, en funciones distintas.

De ahí salieron los bugs de esta semana, y son todos el mismo: dos pantallas
derivando el mismo estado con criterios distintos.
"""
from __future__ import annotations

from core import ciclo

# ── LA GUARDA: una tabla nueva no puede aparecer sin declarar su ciclo ──────

def test_TODA_tabla_del_agente_declara_como_dice_su_estado():
    """Es la guarda que impide que la lista de 8 formas se haga 9. Se compara
    contra el schema (derivado del archivo), no contra una lista a mano."""
    faltan = ciclo.tablas_del_agente() - {f.tabla for f in ciclo.REGISTRO}
    assert not faltan, (
        f"tablas del agente sin declarar en `core.ciclo.REGISTRO`: {faltan}. "
        f"Cada una que se suma sin declarar es una novena forma de decir "
        f"«resuelto», y el próximo bug es que dos pantallas la lean distinto.")


def test_no_se_declaran_tablas_FANTASMA():
    """La lista tampoco puede quedar inflada: decir que se cubre una tabla que
    ya no existe es el mismo modo de falla al revés."""
    sobran = {f.tabla for f in ciclo.REGISTRO} - ciclo.tablas_del_agente()
    assert not sobran, f"declaradas y ya no existen: {sobran}"


def test_ninguna_tabla_se_declara_dos_veces():
    tablas = [f.tabla for f in ciclo.REGISTRO]
    assert len(tablas) == len(set(tablas))


# ── los ESTADOS: seis, y cada uno se atiende distinto ───────────────────────

def test_VOLVIO_no_es_lo_mismo_que_NUEVO():
    """El estado que más importa. Un problema que reaparece contado como nuevo
    es cómo se pierde que algo se arregla y se rompe todas las semanas —
    exactamente lo que pasó con los BOPREALes."""
    assert ciclo.VOLVIO != ciclo.NUEVO
    assert ciclo.puede_pasar(ciclo.RESUELTO, ciclo.VOLVIO)


def test_de_RESUELTO_no_se_vuelve_a_EN_CURSO():
    """La única vuelta atrás desde resuelto es que el problema VUELVA. Si se
    pudiera saltar a cualquier lado, el estado no significaría nada."""
    assert not ciclo.puede_pasar(ciclo.RESUELTO, ciclo.EN_CURSO)
    assert not ciclo.puede_pasar(ciclo.RESUELTO, ciclo.NUEVO)


def test_IGNORAR_es_reversible():
    """Lo que hace barata la decisión de ignorar es poder deshacerla."""
    assert ciclo.puede_pasar(ciclo.NUEVO, ciclo.IGNORADO)
    assert ciclo.puede_pasar(ciclo.IGNORADO, ciclo.NUEVO)


def test_una_transicion_inventada_se_rechaza():
    assert ciclo.puede_pasar("loquesea", ciclo.VISTO) is False


def test_todo_estado_tiene_declarada_su_salida():
    faltan = set(ciclo.ESTADOS) - set(ciclo.TRANSICIONES)
    assert not faltan, f"estados sin transiciones declaradas: {faltan}"


def test_no_se_declara_una_transicion_a_un_estado_que_no_existe():
    for de, destinos in ciclo.TRANSICIONES.items():
        for a in destinos:
            assert a in ciclo.ESTADOS, f"{de} → {a} no es un estado"


# ── EL ÁRBITRO: cada tabla, traducida a lo mismo ────────────────────────────

def test_las_OCHO_formas_dan_todas_el_mismo_vocabulario():
    """El punto entero: la pantalla pregunta acá y no mira la columna, así dos
    pantallas no pueden discrepar sobre si algo está resuelto."""
    casos = [
        ("mercado.av_agent_centinela", {"resuelto_at": "2026-08-21"}, ciclo.RESUELTO),
        ("mercado.av_agent_centinela", {"visto_at": "2026-08-21"}, ciclo.VISTO),
        ("mercado.av_agent_centinela", {}, ciclo.NUEVO),
        ("manager.controles_datos", {"resuelto_at": "x"}, ciclo.RESUELTO),
        ("mercado.av_agent_avisos", {"resuelto": True}, ciclo.RESUELTO),
        ("mercado.av_agent_aviso_items", {"hecho": True}, ciclo.RESUELTO),
        ("mercado.av_agent_preguntas", {"estado": "respondida"}, ciclo.RESUELTO),
        ("mercado.av_agent_propuestas", {"estado": "esperando"}, ciclo.EN_CURSO),
        ("mercado.av_agent_propuestas", {"estado": "rechazada"}, ciclo.IGNORADO),
        ("mercado.av_agent_seguimiento", {"estado": "volvio"}, ciclo.VOLVIO),
        ("mercado.av_agent_ignorados", {}, ciclo.IGNORADO),
        ("manager.proveedor_estado", {"ok": False}, ciclo.NUEVO),
    ]
    for tabla, fila, esperado in casos:
        assert ciclo.estado_de(tabla, fila) == esperado, f"{tabla} {fila}"


def test_el_estado_devuelto_SIEMPRE_es_uno_de_los_seis():
    """Un estado fuera del vocabulario es una rama que ninguna pantalla maneja."""
    for f in ciclo.REGISTRO:
        for fila in ({}, {"estado": "loquesea"}, {"resuelto": None},
                     {"ok": None}, {"hecho": 0}):
            assert ciclo.estado_de(f.tabla, fila) in ciclo.ESTADOS, f.tabla


def test_una_tabla_desconocida_cae_en_NUEVO_y_no_en_RESUELTO():
    """Ante la duda se muestra de más: una fila de sobra molesta, una fila
    escondida que estaba rota no se ve nunca."""
    assert ciclo.estado_de("mercado.no_existe", {}) == ciclo.NUEVO


def test_una_fila_rota_no_hace_explotar_al_arbitro():
    assert ciclo.estado_de("mercado.av_agent_propuestas", None) in ciclo.ESTADOS


def test_avisos_tiene_LAS_DOS_formas_y_se_declara_cual_gana():
    """`av_agent_avisos` tiene `resuelto boolean` Y `resuelto_at`. Sin declarar
    cuál manda, dos lectores eligen distinto — REGLA #9 adentro de UNA tabla."""
    f = next(x for x in ciclo.REGISTRO if x.tabla == "mercado.av_agent_avisos")
    assert "resuelto" in f.campos and "resuelto_at" in f.campos
    assert "redundante" in f.como
    # Gana el booleano: es el que filtran sus queries.
    assert ciclo.estado_de(f.tabla, {"resuelto": False, "resuelto_at": "x"}) \
        == ciclo.NUEVO


# ── LA DEUDA, contada ───────────────────────────────────────────────────────

def test_la_deuda_es_un_NUMERO_y_no_una_sensacion():
    """Que se pueda contar es la mitad del valor: sin esto, «hay que unificar
    los estados» es una intención y no una tarea."""
    deuda = ciclo.sin_migrar()
    assert deuda, "si esto queda vacío, la migración terminó (o se rompió el conteo)"
    assert "mercado.av_agent_centinela" in deuda


def test_los_HALLAZGOS_estan_marcados_como_lo_que_son():
    """`av_agent_hallazgos` es LA tabla de ENCONTRÓ y no tiene estado propio: es
    una foto. Declararlo es lo que convierte «el agente no tiene memoria» en una
    línea de deuda concreta."""
    f = next(x for x in ciclo.REGISTRO
             if x.tabla == "mercado.av_agent_hallazgos")
    assert "SIN estado" in f.como
