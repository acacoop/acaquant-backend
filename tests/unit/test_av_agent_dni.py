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


def test_todo_control_tiene_accion_o_motivo_declarado():
    """LEY DEL USER (2026-08-23): *«no es aceptable que haya cosas en ENCONTRÓ
    sin solución… un mecanismo que me obligue a encontrarlo»*. El mecanismo:
    un control o tiene su ACCIÓN (POR_CONTROL) o su motivo DECLARADO
    (SIN_ACCION, que dice por dónde se arregla). Un control nuevo sin ninguna
    de las dos rompe este test y no llega a prod."""
    from api.services.av_agent_hacer import POR_CONTROL, SIN_ACCION
    from jobs.controles_datos import CONTROLES
    for c in CONTROLES:
        assert c.id in POR_CONTROL or c.id in SIN_ACCION, (
            f"el control «{c.id}» no tiene acción NI motivo declarado — "
            "en ENCONTRÓ nada queda sin salida")
    # Y nadie está en los dos: sería una contradicción con cara de dato.
    assert not set(SIN_ACCION) & set(POR_CONTROL)


def test_titulos_sin_flujo_va_a_1816_y_el_faltante_SE_DICE():
    """El puente tenencia → TICKER → 1816. Y los dos «no puedo» (sin ticker /
    no está en 1816) generan una fila que LO DICE — nunca silencio."""
    from api.services import av_agent_hacer as h
    assert h.POR_CONTROL["titulos_sin_flujo"] == "mercado.alta_flujos"
    src = codigo(h.AccionAltaFlujos)
    assert "_ficha_1816" in src            # el catálogo 1816, 0 créditos
    assert "av_agent_alta.aplicar" in src  # la MISMA cadena E2 del modal
    assert "NO ESTÁ EN 1816" in src and "SIN TICKER" in src


def test_sin_precio_no_se_rediagnostica_cada_corrida():
    """Un papel que hoy no opera no es un dato roto: el hallazgo vino de un
    precio viejo. Cuenta como «nada que hacer hoy» → desenlace viejo →
    atendido; si vuelve a operar, el detector lo re-ve."""
    import api.services.av_agent_alta as alta
    assert 'dx["causa"] in ("sano", "sin_precio")' in inspect.getsource(alta)


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


def test_dos_fallas_juntas_se_arreglan_compuestas_no_de_a_una():
    """§0.da — el ARREGLO COMPUESTO. 25 bonos del informe #18 tenían
    `moneda_flujo_contradice` Y el cuadro en nominales de emisión: el arreglo
    local parcheaba solo la moneda, simulaba con el cuadro roto y BLOQUEABA
    para siempre. Cuando la lente del cuadro también falla, la propuesta va
    por la rama de RED (que trae el cronograma de 1816) con el parche a bordo,
    se juzga ENTERA, y se escribe todo en UNA pasada."""
    import api.services.av_agent_alta as alta

    src = inspect.getsource(alta)
    # El desvío existe y consulta las DOS condiciones.
    assert "compuesto = parcheable and _cuadro_tambien_roto(dx)" in src
    assert "if parcheable and not compuesto:" in src
    # La propuesta se simula con el parche puesto (no solo con el cuadro).
    assert 'doc_prop.update(dx["parche"])' in src
    # Las causas de cuadro que disparan la composición están declaradas.
    assert alta._CAUSAS_CUADRO == ("escala_del_cuadro", "campo_de_amortizacion")
    # La detección mira TODAS las observaciones, no solo la culpable.
    assert "observaciones" in codigo(alta._cuadro_tambien_roto)


def test_el_parche_compuesto_se_escribe_en_la_misma_pasada_y_con_columna():
    """Dos escrituras dejarían una ventana con una falla arreglada y la otra
    no. Y `moneda_flujo` es columna ADEMÁS de vivir en el blob: escribir uno
    solo recrea la contradicción que el arreglo viene a curar (mismo criterio
    que `_aplicar_parche_local`)."""
    from api.services import av_agent_alta as alta

    src = codigo(alta.aplicar_arreglo)
    assert 'parche.update(sim["parche"])' in src        # entra al mismo UPDATE
    assert 'for col in ("moneda_flujo",):' in src       # espejo en la columna
    assert 'antes["campos"] = sim["_antes_campos"]' in src  # el ANTES, al libro


def test_la_cola_de_respuestas_se_concilia_contra_la_base():
    """«ALTA (16): …» con los bonos ya dados de alta por otra vía era texto
    sin continuación. La cola cruza contra mercado.curvas y sella aplicadas
    las que la realidad ya cumplió."""
    from api.services import av_agent_preguntas as preg
    assert "_conciliar_altas_ya_hechas" in codigo(preg.pendientes_de_aplicar)
    src = codigo(preg._conciliar_altas_ya_hechas)
    assert "mercado.curvas" in src
    assert "aplicada_at = now()" in src
