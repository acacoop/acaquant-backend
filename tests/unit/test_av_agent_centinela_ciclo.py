"""UNA PASADA QUE EXPLOTA NO PUEDE DECIR «SE ARREGLÓ SOLO».

`_observar()` mira tres cosas —precios, tasas y salud— **cada una en su propio
`try`**, para que la caída de una no deje al centinela sin mirar las otras. Eso
está bien y no se toca.

El problema era lo que venía después. Los tres `try` se tragan la excepción y
devuelven una lista más corta, así que el que llama **no podía distinguir «no
encontró nada» de «explotó»**. Y el auto-resuelto decía:

    if hallazgos:      # ← «si algo trajo, cerrá todo lo demás»
        UPDATE ... SET resuelto_como = 'solo' WHERE ultimo_at < marca

El comentario de arriba decía *«solo cuando la pasada fue COMPLETA»* y **eso no
era lo que el código chequeaba**: con el bloque de tasas caído, los precios
igual traían algo, la condición pasaba, y **todos los `tasa_sospechosa` se
marcaban como arreglados solos**. Silencioso y del lado optimista.

Es el mismo modo de falla que `evaluados` tapó en el censo (§0.be).
"""
from __future__ import annotations

import inspect

from api.services import av_agent_centinela as c

from ._fuente import codigo

# ── _observar declara QUÉ ALCANZÓ A MIRAR ───────────────────────────────────

def test_observar_devuelve_tambien_lo_que_evaluo():
    assert "tuple[list[dict], set[str]]" in inspect.getsource(c._observar).split("\n")[0]


def test_cada_pasada_declara_sus_TIPOS():
    """Se declara y no se deduce de lo que devolvió: una pasada que no encontró
    nada y una que explotó devuelven lo mismo — nada."""
    assert set(c._CUBRE) == {"precios", "tasas", "salud", "actividad"}
    for bloque, tipos in c._CUBRE.items():
        assert tipos, bloque


def test_los_tipos_declarados_EXISTEN():
    """Un tipo inventado acá cerraría hallazgos que nadie emite, o —peor— no
    cerraría los que sí."""
    from api.services.av_agent import ACCION_POR_TIPO
    for tipos in c._CUBRE.values():
        for t in tipos:
            assert t in ACCION_POR_TIPO, f"«{t}» no lo emite ningún detector"


def test_cada_bloque_marca_lo_suyo_DESPUES_de_correr():
    """`evaluados.update()` va DENTRO del `try` y después de la llamada: si
    fuera antes, una excepción dejaría el tipo marcado como evaluado.

    ⚠️ **PRECIOS no usa `_CUBRE`** (2026-08-24): esa pasada corre SEIS
    detectores en seis `try` distintos, así que un catálogo fijo daría por
    evaluado lo que explotó. Lo declara `relevar_live()` detector por detector
    y acá se lee de ahí — ver el comentario de `_CUBRE`.
    """
    src = inspect.getsource(c._observar)
    for bloque in ("tasas", "salud", "actividad"):
        i = src.index(f'_CUBRE["{bloque}"]')
        # entre el update y el `except` de su bloque no puede haber otro `try`
        assert "except Exception" in src[i:i + 300], bloque
    i = src.index('r.get("evaluados")')
    assert "except Exception" in src[i:i + 300], "precios"


def test_PRECIOS_no_puede_volver_a_usar_el_catalogo_fijo():
    """El catálogo dice qué VIGILA el daemon (lo lee la tab AGENDA); `evaluados`
    dice qué pudo MIRAR en esta pasada. Son dos preguntas distintas y una sola
    lista no puede contestar las dos: completa, un detector caído cierra todo lo
    suyo; recortada, la agenda miente sobre qué se está vigilando."""
    # Se miran solo las líneas de CÓDIGO: el comentario que explica por qué no
    # se usa lo nombra a propósito, y ese texto es justamente lo que hay que
    # conservar para que el próximo no lo vuelva a poner.
    codigo = "\n".join(l for l in inspect.getsource(c._observar).splitlines()
                       if not l.lstrip().startswith("#"))
    assert '_CUBRE["precios"]' not in codigo
    assert 'r.get("evaluados")' in codigo


def test_una_pasada_CAIDA_no_marca_su_tipo(monkeypatch):
    """El caso real: si el bloque de tasas explota, `tasa_sospechosa` NO puede
    quedar en `evaluados` — si no, se cierran todos."""
    from api.services import av_agent
    # El reloj se PINA: el test no puede depender de qué día lo corre CI.
    monkeypatch.setattr(av_agent, "en_rueda", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "dia_habil", lambda ahora=None: True)
    # La pasada de precios declara lo que SUS detectores alcanzaron a mirar.
    monkeypatch.setattr(av_agent, "relevar_live",
                        lambda: {"hallazgos": [],
                                 "evaluados": ["sin_precio", "precio_moneda"]})
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(av_agent, "detectar_salud", lambda *a: [])
    monkeypatch.setattr("api.services.salud.evaluar", lambda: [])
    _h, evaluados = c._observar()
    assert "tasa_sospechosa" not in evaluados
    assert "sin_precio" in evaluados, "la pasada que SÍ corrió tiene que contar"


def test_un_detector_CAIDO_dentro_de_precios_tampoco_marca_lo_suyo(monkeypatch):
    """El caso que faltaba, y el que dejaba a los motores sin cerrarse nunca:
    dentro de la pasada de precios hay SEIS detectores. Si `detectar_motores`
    explota, `motor_caido` no puede quedar evaluado — aunque los otros cinco
    hayan andado."""
    from api.services import av_agent
    monkeypatch.setattr(av_agent, "en_rueda", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "dia_habil", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "relevar_live",
                        lambda: {"hallazgos": [],
                                 # `motor_caido` NO viene: su detector se cayó.
                                 "evaluados": ["sin_precio", "proveedor_caido"]})
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas", lambda *a, **k: [])
    monkeypatch.setattr(av_agent, "detectar_salud", lambda *a: [])
    monkeypatch.setattr("api.services.salud.evaluar", lambda: [])
    _h, evaluados = c._observar()
    assert "motor_caido" not in evaluados
    assert {"sin_precio", "proveedor_caido"} <= evaluados


def test_si_se_cae_TODO_no_se_evalua_nada(monkeypatch):
    """Y con `evaluados` vacío no se cierra una sola fila."""
    from api.services import av_agent
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731
    monkeypatch.setattr(av_agent, "en_rueda", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "dia_habil", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "relevar_live", boom)
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas", boom)
    monkeypatch.setattr("api.services.salud.evaluar", boom)
    _h, evaluados = c._observar()
    assert evaluados == set()


# ── EL CALENDARIO: fuera de rueda, la foto vieja no opina (§0.co) ───────────

def test_FUERA_de_rueda_los_detectores_de_mercado_NO_corren(monkeypatch):
    """Un sábado el centinela vio «volver» 7 tasas y «aparecer» 11 patas desde
    una foto del viernes: churn del detector, no de la base. Fuera de rueda los
    bloques de precios y tasas no corren — y al no correr no cierran, no
    reabren y no hacen nacer nada."""
    from api.services import av_agent
    llamado = {"precios": 0, "tasas": 0}
    monkeypatch.setattr(av_agent, "en_rueda", lambda ahora=None: False)
    monkeypatch.setattr(av_agent, "dia_habil", lambda ahora=None: True)
    monkeypatch.setattr(av_agent, "relevar_live",
                        lambda: llamado.__setitem__("precios", 1))
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas",
                        lambda *a, **k: llamado.__setitem__("tasas", 1))
    monkeypatch.setattr(av_agent, "detectar_salud", lambda *a: [])
    monkeypatch.setattr("api.services.salud.evaluar", lambda: [])
    _h, evaluados = c._observar()
    assert llamado == {"precios": 0, "tasas": 0}
    assert "sin_precio" not in evaluados and "tasa_sospechosa" not in evaluados
    assert "salud" in evaluados, "SALUD sí corre siempre"


def test_en_dia_NO_habil_corre_la_pasada_de_actividad(monkeypatch):
    """El razonamiento invertido: en no hábil no se mira si el dato está bien —
    se mira que no haya dato nuevo."""
    from api.services import av_agent
    monkeypatch.setattr(av_agent, "en_rueda", lambda ahora=None: False)
    monkeypatch.setattr(av_agent, "dia_habil", lambda ahora=None: False)
    monkeypatch.setattr(av_agent, "detectar_actividad_no_habil",
                        lambda ahora=None: [{"tipo": "actividad",
                                             "ticker": "market_snapshot",
                                             "regla": "actividad_en_no_habil",
                                             "severidad": "alta",
                                             "motivo": "x"}])
    monkeypatch.setattr(av_agent, "detectar_salud", lambda *a: [])
    monkeypatch.setattr("api.services.salud.evaluar", lambda: [])
    h, evaluados = c._observar()
    assert any(x["tipo"] == "actividad" for x in h)
    assert "actividad" in evaluados


# ── el auto-resuelto usa esa declaración, no «si trajo algo» ────────────────

def test_el_auto_resuelto_se_limita_a_los_tipos_EVALUADOS():
    src = codigo(c.ciclo)
    assert "if evaluados:" in src, "volvió a cerrar por «si trajo algo»"
    assert "AND tipo = ANY(%s)" in src
    assert "if hallazgos:" not in src


def test_el_espejo_en_items_usa_LA_MISMA_guarda():
    """Si el centinela y su espejo cerraran con criterios distintos, AHORA y
    ENCONTRÓ volverían a contar historias diferentes del mismo problema — que
    es justo lo que la migración vino a terminar."""
    src = codigo(c.ciclo)
    assert "sincronizar(\"live\"" in src and "evaluados=evaluados" in src


def test_el_espejo_no_puede_tumbar_la_pasada():
    """La pasada del centinela es lo que la mesa mira en rueda."""
    cola = codigo(c.ciclo).split("av_agent_items.sincronizar")[1][:400]
    assert "except Exception" in cola


# ── UN SOLO RELOJ PARA LAS DOS PANTALLAS ────────────────────────────────────

def test_la_antiguedad_sale_del_OBJETO_y_no_de_la_tabla_del_centinela():
    """⚠️ El centinela tenía su `abierto_at` y el censo el suyo, y nadie los
    unía: **AHORA podía decir «recién» y ENCONTRÓ «11 días» del MISMO
    problema**. Dos relojes para un hecho es la definición de la contradicción
    que esta migración vino a terminar."""
    src = codigo(c.estado)
    assert "LEFT JOIN agente.av_agent_items" in src
    assert "abierto_canonico" in src


def test_el_JOIN_va_en_la_MISMA_query():
    """El peaje de Supabase se paga por VIAJE (~8,5 ms), no por plan.

    Cuatro `execute`, todos en la misma conexión: el latido · los abiertos (con
    el JOIN al objeto) · los resueltos · los tipos que van a AHORA siempre
    (motores y proveedor, que viven en `av_agent_items` y no en la tabla del
    centinela). Lo que este test protege es que el número sea **constante**: si
    sube con la cantidad de filas es un N+1, y ahí la pantalla pasa de 34 ms a
    varios segundos sin que nadie lo note en local.
    """
    import ast
    import inspect
    import textwrap

    src = codigo(c.estado)
    assert src.count("cur.execute") == 4

    # Y ninguno adentro de un bucle: eso es lo que convierte 4 en 400. Se mira
    # con el AST y no buscando texto — «hay un `for` más arriba» no dice nada
    # sobre si el execute está ADENTRO.
    arbol = ast.parse(textwrap.dedent(inspect.getsource(c.estado)))

    def _executes(nodo) -> int:
        return sum(1 for n in ast.walk(nodo)
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "execute")

    for n in ast.walk(arbol):
        if isinstance(n, (ast.For, ast.While, ast.AsyncFor)):
            assert _executes(n) == 0, "una query adentro de un bucle es un N+1"


def test_si_el_objeto_no_existe_todavia_se_usa_el_reloj_LOCAL():
    """Un hallazgo de este mismo ciclo, antes de espejarse, no puede quedar sin
    antigüedad: en ese instante los dos relojes dicen lo mismo."""
    src = codigo(c.estado)
    assert 'f.pop("abierto_canonico", None) or f.get("abierto_at")' in src


def test_y_se_publica_para_que_la_fila_pueda_decirlo():
    src = codigo(c.estado)
    assert '"dias_abierto"' in src
