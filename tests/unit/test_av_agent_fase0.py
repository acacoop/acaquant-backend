"""FASE 0 — los cuatro arreglos que frenan la sangría (2026-08-24).

Los cuatro son la misma enfermedad (REGLA #9: dos lugares que tienen que estar
de acuerdo y nada los obliga) y los cuatro fallaban **en silencio**. Por eso los
tests son de INVARIANTE y no de happy path: lo que hay que impedir es que
vuelvan, no comprobar que hoy andan.
"""
from __future__ import annotations

import inspect

from api.services import av_agent, av_agent_seguimiento

# ── 1 · LA FOTO DE REEMPLAZO GUARDA SU IDENTIDAD ────────────────────────────
#
# Sin `clave`, el `LEFT JOIN` de la vista contra `av_agent_items` no matchea
# NUNCA (en SQL, NULL ≠ NULL) y las ~13 familias del monitor pierden antigüedad,
# «volvió», «ya lo atendiste» e IGNORAR.

def test_reemplazar_hallazgos_escribe_la_clave():
    src = inspect.getsource(av_agent.reemplazar_hallazgos)
    cols = src[src.index("INSERT INTO agente.av_agent_hallazgos"):]
    cols = cols[cols.index("("):cols.index(")")]
    assert "clave" in cols, (
        "el INSERT de los alcances de REEMPLAZO no incluye la columna `clave`: "
        "la memoria queda existiendo pero inalcanzable")
    # y un placeholder por columna, o el INSERT revienta en runtime
    vals = src[src.index('"VALUES ('):]
    assert vals[:vals.index(")")].count("%s") == cols.count(",") + 1


def test_la_clave_NO_se_arma_a_mano():
    """La arma `clave_de_problema`, igual que `persistir` y que el detector.
    Dos implementaciones de la identidad es cómo se llegó hasta acá."""
    src = inspect.getsource(av_agent.reemplazar_hallazgos)
    assert "clave_de_problema(" in src
    assert "'|'" not in src and '"|"' not in src


def test_las_dos_puertas_de_persistencia_usan_LA_MISMA_identidad():
    from jobs.av_agent import persistir
    for fn in (av_agent.reemplazar_hallazgos, persistir):
        assert "clave_de_problema(" in inspect.getsource(fn), (
            f"{fn.__name__} arma la clave por su cuenta")


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


# ── 3 · EL SEGUIMIENTO VIEJO NO PUEDE VOTAR ────────────────────────────────
#
# Sus dos mitades arman la clave con formatos distintos, así que la
# intersección es vacía por construcción y el veredicto solo puede ser
# «aguantó» → un ✔ `verificado` fabricado en la única señal que la compuerta
# de autonomía cuenta como humana.

def test_el_seguimiento_viejo_NO_vota():
    assert av_agent_seguimiento._votar([{"x": 1}], [{"y": 2}]) == 0


def test_no_queda_ninguna_llamada_a_votar_en_el_seguimiento_viejo():
    src = inspect.getsource(av_agent_seguimiento)
    assert "av_agent_evals" not in src, (
        "volvió el voto: mientras las dos puntas armen la clave distinto, "
        "solo puede emitir positivos")


def test_las_DOS_puntas_siguen_sin_coincidir_y_por_eso_no_se_vota():
    """Congela el motivo. El día que las claves se unifiquen, este test falla y
    ahí SÍ hay que volver a habilitar el voto (Fase 2)."""
    from api.services import av_agent_acciones
    anota = inspect.getsource(av_agent_acciones.registrar)
    assert '{accion}:{objetivo}:{regla}' in anota
    import jobs.seguimiento as js
    compara = inspect.getsource(js._claves_abiertas)
    assert "tipo || ':' || ticker || ':' || regla" in compara
