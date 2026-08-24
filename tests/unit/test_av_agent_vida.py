"""EL AGENTE TIENE VIDA: no duplica, no trae cosas viejas, y llega diagnosticado.

El user (2026-08-24), un lunes al mediodía, mirando la pantalla:

  · *«en ENCONTRÓ ahora TODO se duplica… figura un aviso y después figura para
    diagnosticar, es tremendo»*
  · *«cosas del 21/08, dios mío… te vengo diciendo hace horas que no quiero ver
    cosas viejas. Cuando por ejemplo cierre_canje ESTÁ FUNCIONANDO»*
  · *«nada más de DIAGNOSTICAR. Ya tiene que venir todo diagnosticado y dejar el
    arreglo para hacer manual. El agente tiene que tener VIDA»*
  · *«el diagnóstico es inentendible»* (PECMO: «✘ Bloqueado» arriba y «lo hace el
    agente» abajo; «paridad 60.3%» arriba y «paridad nuestra 0.04%» abajo)

Cuatro síntomas, cuatro causas distintas. Este archivo las congela a las cuatro.
"""
from __future__ import annotations

import ast
import inspect


def codigo(fn) -> str:
    return inspect.getsource(fn)


def sin_comentarios(txt: str) -> str:
    """El cuerpo SIN los comentarios: los tests de acá buscan strings que también
    aparecen citados en los comentarios (que citan al user, textual)."""
    return "\n".join(l for l in txt.splitlines()
                     if not l.lstrip().startswith("#"))


# ── 1. UN PROBLEMA, UNA FILA ────────────────────────────────────────────────

def test_los_hallazgos_se_colapsan_por_clave():
    from api.services.av_agent_vista import _una_fila_por_problema
    filas = [
        {"clave": "pecko|paridad_fuera_de_rango", "severidad": "alta",
         "abierto_at": None, "motivo": "sin memoria"},
        {"clave": "pecko|paridad_fuera_de_rango", "severidad": "alta",
         "abierto_at": "2026-08-24T12:00:00Z", "motivo": "con memoria"},
        {"clave": "pecko|sin_tea_con_precio", "severidad": "alta",
         "abierto_at": None, "motivo": "otra causa"},
    ]
    out = _una_fila_por_problema(filas)
    assert len(out) == 2, "el mismo (sujeto, causa) tiene que ser UNA fila"
    # Gana la que trae memoria: colapsar no puede costar la antigüedad.
    assert out[0]["motivo"] == "con memoria"
    # Y lo colapsado se DICE, no se tira en silencio.
    assert out[0]["visto_por"] == 2
    assert "visto_por" not in out[1]


def test_a_igual_memoria_gana_la_MAS_severa():
    from api.services.av_agent_vista import _una_fila_por_problema
    out = _una_fila_por_problema([
        {"clave": "k", "severidad": "media", "abierto_at": None, "motivo": "b"},
        {"clave": "k", "severidad": "alta", "abierto_at": None, "motivo": "a"},
    ])
    assert [f["motivo"] for f in out] == ["a"]


def test_sin_clave_NO_se_colapsa():
    """Una fila vieja (anterior a la columna `clave`) no tiene identidad. Fundir
    dos cosas distintas es peor que mostrar un duplicado."""
    from api.services.av_agent_vista import _una_fila_por_problema
    out = _una_fila_por_problema([
        {"clave": "", "severidad": "alta", "motivo": "x"},
        {"clave": None, "severidad": "alta", "motivo": "y"},
    ])
    assert [f["motivo"] for f in out] == ["x", "y"]


def test_la_vista_USA_el_colapso():
    """Que la función exista no alcanza: hay que llamarla. Es el bug de siempre."""
    from api.services import av_agent_vista as v
    cuerpo = sin_comentarios(codigo(v._hallazgos_ultima_corrida))
    assert "_una_fila_por_problema(" in cuerpo


def test_la_clave_VIAJA_en_el_select():
    """Sin la identidad en el SELECT no hay nada que colapsar."""
    from api.services import av_agent_vista as v
    assert "clave" in v._COLS_H


# ── 2. LO VIEJO SE FECHA, Y UN JOB QUE TERMINÓ BIEN NO ES TRABAJO ───────────

def test_la_edad_entra_en_la_frase_solo_si_NO_es_de_hoy():
    from datetime import UTC, datetime, timedelta

    from api.services.salud import _edad
    ahora = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)          # lunes 12:00 ART
    assert _edad(ahora - timedelta(hours=2), ahora) == ""      # hoy → nada
    assert _edad(ahora - timedelta(days=1), ahora) == " · hace 1 día"
    assert _edad(ahora - timedelta(days=3), ahora) == " · hace 3 días"
    assert _edad(None, ahora) == ""


def test_una_corrida_PARCIAL_dice_que_termino_bien():
    from api.services import salud
    cuerpo = sin_comentarios(codigo(salud._chequeo_job))
    assert "terminó con errores parciales" not in cuerpo, (
        "«terminó con errores parciales» lee como job roto y el job terminó bien")
    assert "terminó bien pero" in cuerpo


def test_el_chequeo_lleva_la_marca_parcial_y_los_errores_de_verdad():
    """Las dos mitades del arreglo, y las dos son estructurales:

    · `parcial` como CAMPO — el detector decide con un booleano, no leyendo el
      texto del motivo (que es como esto ya se rompió antes);
    · `corridas` — es de donde `_motivo_salud` saca el error REAL para
      traducirlo. Sin ese campo esa traducción nunca corría para un job y la
      fila decía siempre la misma frase mecánica.
    """
    from api.services import salud
    cuerpo = codigo(salud._chequeo_job)
    assert '"parcial":' in cuerpo
    assert '"corridas":' in cuerpo


def test_un_job_que_termino_bien_es_NOTICIA_y_no_pide_trabajo():
    from api.services import av_agent
    assert av_agent.es_noticia("salud", "salud_job_ok_con_avisos")
    # El que FALLÓ sí pide trabajo — si no, se escondería lo que importa.
    assert not av_agent.es_noticia("salud", "salud_job")


def test_detectar_salud_separa_el_que_fallo_del_que_termino_bien():
    from api.services import av_agent
    fallo, parcial = av_agent.detectar_salud([
        {"id": "job:market_anchors", "familia": "job", "estado": "error",
         "titulo": "market_anchors", "motivo": "la corrida falló"},
        {"id": "job:cierre_canje", "familia": "job", "estado": "warn",
         "titulo": "cierre_canje", "motivo": "terminó bien pero…",
         "parcial": True},
    ])
    assert fallo["regla"] == "salud_job"
    assert parcial["regla"] == "salud_job_ok_con_avisos"


def test_la_vista_le_pasa_la_REGLA_a_es_noticia():
    """`es_noticia(tipo)` a secas dejaría los parciales en la lista de trabajo."""
    from api.services import av_agent_vista as v
    cuerpo = sin_comentarios(codigo(v.vista))
    assert 'es_noticia(h.get("tipo") or "", h.get("regla") or "")' in cuerpo


# ── 3. EL DIAGNÓSTICO LLEGA HECHO ───────────────────────────────────────────

def test_ningun_motivo_manda_a_apretar_DIAGNOSTICAR():
    """El agente diagnostica solo: pedirle al user que apriete un botón para
    saber el porqué es exactamente el modelo que se dio de baja."""
    import pathlib
    malos = []
    for f in pathlib.Path("api/services").glob("av_agent*.py"):
        for i, linea in enumerate(f.read_text().splitlines(), 1):
            if linea.lstrip().startswith("#"):
                continue
            if "DIAGNOSTICAR para saber" in linea:
                malos.append(f"{f.name}:{i}")
    assert not malos, f"todavía mandan a apretar DIAGNOSTICAR: {malos}"


def test_el_job_diagnostica_solo_despues_de_persistir():
    """El orden importa: diagnosticar ANTES de persistir miraría objetos que
    todavía no existen, y por lo tanto no podría saltear lo ya atendido."""
    import pathlib
    src = pathlib.Path("jobs/av_agent.py").read_text()
    assert "diagnosticar_pendientes(" in src
    assert src.index("n = persistir(res)") < src.index("diagnosticar_pendientes(")


def test_el_diagnostico_se_escribe_en_TODAS_las_claves_del_bono():
    """Un papel dispara varias reglas y el diagnóstico es POR BONO. Guardarlo en
    una sola clave dejaría la otra fila sin explicación — el bug original
    sobreviviendo en la mitad de la pantalla."""
    from api.services import av_agent_masivo as m
    cuerpo = sin_comentarios(codigo(m.diagnosticar_pendientes))
    assert "for k in grupos[par]:" in cuerpo
    assert "guardar_diagnostico(k, fila)" in cuerpo


def test_el_diagnostico_vive_en_el_OBJETO_y_lo_sirve_la_vista():
    from api.services import av_agent_items, av_agent_vista
    assert hasattr(av_agent_items, "guardar_diagnostico")
    assert "diagnostico" in av_agent_vista._COLS_MEM
    assert "i.diagnostico" in av_agent_vista._SELECT_H


def test_un_problema_que_VUELVE_pierde_su_diagnostico_viejo():
    """Se dio por arreglado y reapareció: la explicación de la vez pasada es
    justo la que se demostró incompleta."""
    from api.services import av_agent_items
    cuerpo = codigo(av_agent_items.ver)
    assert "diagnostico = CASE WHEN" in cuerpo
    assert "diagnostico_at = CASE WHEN" in cuerpo


def test_no_diagnosticar_de_nuevo_lo_que_ya_tiene_diagnostico():
    """Es lo que hace que correrlo en cada pasada sea barato."""
    from api.services import av_agent_items, av_agent_masivo
    assert hasattr(av_agent_items, "con_diagnostico")
    assert "con_diagnostico(" in codigo(av_agent_masivo.diagnosticar_pendientes)


def test_con_diagnostico_ante_un_fallo_devuelve_VACIO():
    """«No sé» tiene que significar «diagnosticá todo», no «ya está hecho»:
    darlo por hecho dejaría filas sin explicación para siempre."""
    from api.services import av_agent_items
    cuerpo = codigo(av_agent_items.con_diagnostico)
    arbol = ast.parse(cuerpo.strip())
    handler = next(n for n in ast.walk(arbol) if isinstance(n, ast.ExceptHandler))
    retornos = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
    assert retornos and all(isinstance(r.value, ast.Call)
                            and getattr(r.value.func, "id", "") == "set"
                            and not r.value.args for r in retornos)


# ── 4. LA TARJETA NO SE CONTRADICE ──────────────────────────────────────────

def test_la_conclusion_NO_promete_lo_que_la_cadena_puede_frenar():
    """Decía «✘ Bloqueado» arriba y «Lo hace el agente» abajo. La conclusión
    corre ANTES del juez: no puede hablar en futuro."""
    from api.services import av_agent_alta
    src = inspect.getsource(av_agent_alta)
    assert "**Lo hace el agente**, y se verifica antes de escribir." not in src


def test_los_dos_cotejos_dicen_de_QUIEN_es_la_paridad():
    """La misma tarjeta mostraba «paridad 60.3%» (hoy) y «paridad nuestra 0.04%»
    (con el parche) — las dos ciertas, las dos llamadas igual."""
    from api.services import av_agent_alta as a
    src = inspect.getsource(a)
    assert '"CON EL ARREGLO"' in src and '"HOY")' in src
    assert "de_quien" in inspect.signature(a._cotejo_de).parameters
