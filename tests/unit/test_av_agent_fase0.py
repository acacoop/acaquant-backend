"""FASE 0 — los cuatro arreglos que frenan la sangría (2026-08-24).

Los cuatro son la misma enfermedad (REGLA #9: dos lugares que tienen que estar
de acuerdo y nada los obliga) y los cuatro fallaban **en silencio**. Por eso los
tests son de INVARIANTE y no de happy path: lo que hay que impedir es que
vuelvan, no comprobar que hoy andan.
"""
from __future__ import annotations

import inspect

from api.services import av_agent

# ── 1 · LA FOTO GUARDA SU IDENTIDAD ────────────────────────────────────────
#
# Sin `clave`, el `LEFT JOIN` de la vista contra `av_agent_items` no matchea
# NUNCA (en SQL, NULL ≠ NULL) y las ~13 familias del monitor pierden antigüedad,
# «volvió», «ya lo atendiste» e IGNORAR.
#
# ⚠️ Los tres tests que vivían acá miraban `av_agent.reemplazar_hallazgos`, que
# **se mudó a la puerta única** en la Fase 1. El invariante no cambió, cambió de
# dueño: lo cubre `test_av_agent_puerta.py`, que además prohíbe escribir la foto
# desde cualquier otro módulo — o sea que ya no hay «las dos puertas» que
# mantener de acuerdo. Hay una.

def test_la_foto_SIEMPRE_guarda_la_clave():
    from api.services import av_agent_registro as registro
    cols = registro._INSERT[registro._INSERT.index("("):registro._INSERT.index(")")]
    assert "clave" in cols
    assert "clave_de_problema(" in inspect.getsource(registro._fila)


# ── 2 · CADA DETECTOR DECLARA QUÉ MIRÓ, Y SOLO SI CORRIÓ ────────────────────

def test_relevar_live_declara_lo_que_evaluo():
    src = inspect.getsource(av_agent.relevar_live)
    assert '"evaluados"' in src, "relevar_live no dice qué alcanzó a mirar"


def test_el_tipo_se_marca_DESPUES_de_que_el_detector_termino():
    """Si se marcara antes del `try`, un detector caído cerraría todo lo suyo
    por ausencia — la mentira optimista que la guarda `evaluados` vino a matar."""
    src = inspect.getsource(av_agent.relevar_live)
    cuerpo = src[src.index("for nombre, tipos, fn in ("):]
    i_ext = cuerpo.index("hallazgos.extend(fn())")
    i_upd = cuerpo.index("evaluados.update(tipos)")
    i_exc = cuerpo.index("except Exception")
    assert i_ext < i_upd < i_exc, (
        "`evaluados.update` tiene que ir DESPUÉS de que el detector devolvió y "
        "ANTES del except — si no, un detector roto declara que miró")


def test_TODOS_los_tipos_que_produce_el_monitor_son_cerrables():
    """El bug: `motor_caido`, `motor_ruidoso`, `proveedor_caido` y `latencia`
    los producía el monitor y NO estaban declarados, así que no se cerraban
    nunca — un motor que volvía seguía en AHORA para siempre."""
    src = inspect.getsource(av_agent.relevar_live)
    for tipo in ("sin_precio", "precio_moneda", "latencia", "motor_caido",
                 "motor_ruidoso", "proveedor_caido"):
        assert f'"{tipo}"' in src, (
            f"«{tipo}» lo produce el monitor y ningún detector lo declara: "
            f"sus hallazgos no se cierran jamás")


def test_los_tipos_declarados_por_el_monitor_existen_en_el_registro():
    """Un tipo mal escrito en la declaración es un tipo que no se cierra nunca,
    y no da ningún error."""
    import re
    src = inspect.getsource(av_agent.relevar_live)
    cuerpo = src[src.index("for nombre, tipos, fn in ("):src.index("try:")]
    declarados = set(re.findall(r'"([a-z_]+)"', cuerpo))
    conocidos = set(av_agent.ACCION_POR_TIPO)
    # los nombres de los detectores no son tipos: solo se exige que TODO lo que
    # coincide con un tipo conocido esté bien escrito.
    inventados = {d for d in declarados
                  if d.endswith(("_caido", "_ruidoso", "_precio", "_moneda"))
                  and d not in conocidos}
    assert not inventados, f"tipos declarados que no existen: {inventados}"


# ── 3 · EL SEGUIMIENTO VIEJO SE BORRÓ ENTERO (Fase 2) ──────────────────────
# El invariante vive ahora en `test_seguimiento.py`: no puede volver a haber dos
# medidores de «¿el arreglo aguantó?».


# ── LOS TOPES SILENCIOSOS Y EL CRASH LATENTE (Fase 2) ──────────────────────

def test_el_tope_de_ABIERTOS_corta_por_lo_MAS_NUEVO_no_por_lo_mas_viejo():
    """⚠️ Iba al revés de la pantalla: `ORDER BY ultimo_at DESC` descartaba **lo
    más viejo**, y `que_importa` prioriza justamente eso (`arrastra` y
    `estancado` son «lleva días abierto y nadie lo miró»). El tope tiraba
    primero lo que la pantalla pone arriba."""
    from api.services import av_agent_items
    codigo = "\n".join(l for l in inspect.getsource(av_agent_items.abiertos).splitlines()
                       if not l.lstrip().startswith("#"))
    assert "ORDER BY abierto_at ASC" in codigo
    assert "ORDER BY ultimo_at DESC" not in codigo


def test_si_el_tope_CORTO_la_pantalla_se_entera():
    """Un contador que promete «de N abiertos, M piden algo» y está topeado en
    silencio miente sobre las dos mitades."""
    from api.services import av_agent_items
    src = inspect.getsource(av_agent_items.que_importa)
    assert '"topeado": topeado' in src and '"tope": limite' in src


def test_el_reloj_mira_los_MAS_VIEJOS_primero():
    """`LIMIT 1000` sin ORDER BY: pasados los 1000 resueltos —que no se purgan
    nunca— el reloj dejaba de correr para un subconjunto arbitrario."""
    from api.services import av_agent_items
    src = inspect.getsource(av_agent_items.cerrar_hitos)
    assert "ORDER BY resuelto_at ASC" in src


def test_AGUANTAN_no_se_congela_al_llegar_a_500():
    """Traía los 500 resueltos más recientes sin importar hace cuánto, así que
    el contador se clavaba y los que seguían en prueba se caían de la lista."""
    from api.services import av_agent_items
    src = inspect.getsource(av_agent_items.en_seguimiento)
    assert "interval '60 days'" in src


def test_la_vista_NO_revienta_si_nunca_hubo_una_corrida():
    """La firma promete `str | None` y el código hacía `corrida.isoformat()` sin
    guarda: con la tabla vacía, la pantalla entera devolvía 500."""
    from api.services import av_agent_vista
    src = inspect.getsource(av_agent_vista._hallazgos_ultima_corrida)
    assert "corrida is not None" in src
