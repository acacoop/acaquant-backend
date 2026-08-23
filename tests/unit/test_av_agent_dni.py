"""EL DNI DEL PROBLEMA SE RESPETA DE PUNTA A PUNTA (§0.cw, 2026-08-22).

El user: *«cada cosa que pasa no es un objeto con un ID… no hay un DNI: existe
un problema y puede aparecer infinitamente en el día por más que ya lo
soluciones»*. El objeto EXISTÍA (`av_agent_items`, clave sujeto|causa) — lo
que estaba roto era que tres flujos lo ignoraban. Este archivo congela los
tres arreglos:

  1. la IDENTIDAD no se stripea, y el agente no puede CREAR assets por
     accidente (el caso OTC: upsert + sujeto trimmeado = fila fantasma);
  2. «probado sano» marca `en_curso`, no `resuelto` (el caso GD46: el arreglo
     vive en curvas pero el detector lee market_snapshot → VOLVIÓ espurio);
  3. el masivo consulta el objeto ANTES de diagnosticar (no se re-trabaja lo
     atendido) y la cola de respuestas se concilia contra la BASE.
"""
from __future__ import annotations

import inspect


def codigo(fn) -> str:
    return inspect.getsource(fn)


# ── 1. la identidad es el string EXACTO ─────────────────────────────────────

def test_el_sujeto_de_un_caso_NO_se_stripea():
    from api.services import av_agent_hacer as h
    cuerpo = codigo(h._sujeto).rsplit('"""', 1)[-1]   # después del docstring
    assert ".strip()" not in cuerpo


def test_el_agente_escribe_assets_sin_poder_crearlos():
    """Las tres escrituras de `hacer` sobre `portafolio.assets` van con
    `crear=False`: sobre una unidad que no existe EXACTA, levantar es correcto
    y upsertear fabrica un asset fantasma."""
    import api.services.av_agent_hacer as h
    src = inspect.getsource(h)
    assert src.count("crear=False") >= 3


def test_set_campos_sin_crear_NO_upsertea_y_avisa():
    from api.services import assets_sql
    src = codigo(assets_sql.set_campos)
    assert "if not crear:" in src
    assert "no existe EXACTA" in src


# ── 2. «probado sano» = atendido, no resuelto ───────────────────────────────

def test_resolver_sujeto_marca_EN_CURSO_no_resuelto():
    """El diagnóstico no califica su propio trabajo: deja la fila atendida y
    el DETECTOR la cierra cuando deja de verla. `resuelto` acá generaba
    VOLVIÓ espurio (GD46: curvas arreglada, snapshot todavía sin TEA)."""
    from api.services import av_agent_items as items
    src = codigo(items.resolver_sujeto)
    assert "ciclo.EN_CURSO," in src
    assert "resuelto_at = now()" not in src


def test_el_masivo_tambien_atiende_en_vez_de_resolver():
    from api.services import av_agent_masivo as m
    src = codigo(m._cerrar_viejo)
    assert "ciclo.EN_CURSO" in src
    assert "ciclo.RESUELTO" not in src


# ── 3. los lotes consultan el objeto antes de trabajar ──────────────────────

def test_el_masivo_saltea_lo_ya_atendido():
    """GD46 se arregló a las 19:10 y la corrida de las 20:05 lo volvió a
    diagnosticar (y el lote lo re-aplicó). El masivo consulta `estados_de`
    ANTES de arrancar y saltea en_curso/resuelto/ignorado, diciendo cuántos."""
    from api.services import av_agent_masivo as m
    src = codigo(m.arrancar)
    assert "estados_de" in src
    assert "saltados_atendidos" in src


def test_las_noticias_se_declaran_y_no_ensucian_la_lista():
    """REGLA #10.2 — una casa por naturaleza: «LA BASE CAMBIÓ» es una
    observación sin accionable → su casa es AHORA, no ENCONTRÓ. Se DECLARA
    (nunca se infiere del nombre) y la vista la marca para que la pantalla
    la esconda contándola."""
    from api.services import av_agent
    from api.services import av_agent_vista as v
    assert "db_cambio" in av_agent.TIPOS_NOTICIA
    assert "tabla_quieta" in av_agent.TIPOS_NOTICIA
    assert av_agent.es_noticia("db_cambio")
    assert not av_agent.es_noticia("tasa")
    assert 'h["noticia"] = True' in codigo(v.vista)


def test_la_cola_de_respuestas_se_concilia_contra_la_base():
    """«ALTA (16): …» con los bonos ya dados de alta por otra vía era texto
    sin continuación. La cola cruza contra mercado.curvas y sella aplicadas
    las que la realidad ya cumplió."""
    from api.services import av_agent_preguntas as preg
    assert "_conciliar_altas_ya_hechas" in codigo(preg.pendientes_de_aplicar)
    src = codigo(preg._conciliar_altas_ya_hechas)
    assert "mercado.curvas" in src
    assert "aplicada_at = now()" in src
