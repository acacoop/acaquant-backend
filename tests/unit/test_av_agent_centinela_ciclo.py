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
    """⚠️ **La ley no cambió; se mudó** (Fase 3). El cierre por ausencia vivía
    en `ciclo()`, sobre la tabla propia del centinela. Al no haber tabla propia
    lo hace `_cerrar_ausentes` —el mismo que usa la relevada nocturna— y por lo
    tanto ya no hay dos implementaciones que puedan discrepar sobre qué se
    puede dar por arreglado."""
    from api.services import av_agent_items

    src = codigo(av_agent_items._cerrar_ausentes)
    assert "evaluados" in src
    assert "tipo = ANY(%s)" in src
    assert "resuelto_at = now()" not in codigo(c.ciclo), "volvió a cerrar solo"


def test_la_antiguedad_SALE_DE_LA_UNICA_TABLA():
    """⚠️ El centinela tenía su `abierto_at` y el censo el suyo, y nadie los
    unía: **AHORA podía decir «recién» y ENCONTRÓ «11 días» del MISMO
    problema**. La Fase 2 lo cosió con un JOIN; la Fase 3 sacó la costura: hay
    UN `abierto_at` y no hay cuál elegir."""
    src = codigo(c.estado)
    assert "FROM agente.av_agent_items" in src
    assert "abierto_canonico" not in src, "volvió el segundo reloj"
    assert "JOIN" not in src, "volvió la costura entre dos tablas"


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
    assert src.count("cur.execute") == 3

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


def test_YA_NO_HAY_reloj_local_que_reconciliar():
    """Esto existía para el hueco entre que el daemon escribía su tabla y el
    espejo escribía el objeto: en ese instante había que elegir un reloj. Sin
    tabla propia no hay hueco — el `coalesce` se fue con la costura que lo
    necesitaba."""
    src = codigo(c.estado)
    assert "abierto_canonico" not in src
    assert 'desde = f.get("abierto_at")' in src


def test_y_se_publica_para_que_la_fila_pueda_decirlo():
    src = codigo(c.estado)
    assert '"dias_abierto"' in src


# ── LA IDENTIDAD SE GUARDA, NO SE RECALCULA (Fase 2, 2026-08-24) ────────────

def test_NADIE_reimplementa_la_identidad_en_SQL():
    """En el JOIN vivia `ON i.clave = lower(c.sujeto) || \'|\' || lower(c.regla)`:
    una TERCERA implementacion de `clave_de_problema`, en SQL, **sin
    `causa_canonica()`** (los sinonimos control-detector no matcheaban) y sin el
    caso del sujeto vacio. Fallaba en silencio, que es el modo de falla de la
    REGLA #9.

    La Fase 2 lo cambio por una columna guardada; la Fase 3 saco el JOIN
    entero. Queda congelado lo unico que importa: **en este modulo la identidad
    no se arma a mano, en ninguna de sus formas.**"""
    for fn in (c.estado, c.ciclo, c._escribir_la_foto):
        src = codigo(fn)
        assert "lower(c.sujeto)" not in src
        assert "|| " + chr(39) + "|" + chr(39) + " ||" not in src
        assert chr(34) + "|" + chr(34) not in src


def test_el_centinela_YA_NO_ESCRIBE_su_tabla_propia():
    """**El corazon de la Fase 3.** La tabla no se dropea —borrar codigo se
    revierte, borrar datos no— pero nadie puede volver a escribirla: seria
    reabrir la segunda fuente de verdad de la que salieron «AHORA dice recien y
    ENCONTRO 11 dias» y «ROTO AHORA muestra 8 filas que son 4»."""
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    malos = []
    for f in list((raiz / "api").rglob("*.py")) + list((raiz / "jobs").rglob("*.py")):
        txt = chr(10).join(x for x in f.read_text(encoding="utf-8").splitlines()
                           if not x.lstrip().startswith("#"))
        if re.search(r"(INSERT INTO|UPDATE|DELETE FROM)\s+agente\.av_agent_centinela",
                     txt, re.I):
            malos.append(str(f.relative_to(raiz)))
    assert not malos, f"volvieron a escribir la tabla paralela: {malos}"


def test_UNA_sola_tabla_con_ciclo_para_el_daemon():
    """Lo que la Fase 3 promete, medido: el daemon lee y escribe
    `av_agent_items` y nada mas. **Un `SELECT` a la tabla vieja tampoco vale** —
    leer de la vieja es como se descubre, tres semanas despues, que la pantalla
    mostraba otra cosa.

    Se mira el SQL de verdad (los literales que no son docstring) y no el texto
    del archivo: los comentarios de este modulo NOMBRAN la tabla a proposito,
    para contar por que ya no se usa.
    """
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(c))
    # Un docstring es el primer Constant del cuerpo de modulo/clase/funcion.
    docs = set()
    for n in ast.walk(arbol):
        cuerpo = getattr(n, "body", None)
        if (isinstance(cuerpo, list) and cuerpo
                and isinstance(cuerpo[0], ast.Expr)
                and isinstance(cuerpo[0].value, ast.Constant)
                and isinstance(cuerpo[0].value.value, str)):
            docs.add(id(cuerpo[0].value))
    malos = [n.value for n in ast.walk(arbol)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in docs and "av_agent_centinela" in n.value]
    assert not malos, f"todavia toca la tabla paralela en SQL: {malos}"


def test_la_clave_del_objeto_la_arma_LA_funcion_de_siempre():
    """Desde la Fase 3 la arma LA PUERTA, que es el unico que escribe: el
    daemon ya no tiene tabla propia y por lo tanto no tiene identidad propia
    que calcular."""
    from api.services import av_agent_registro as registro

    assert "clave_de_problema(" in inspect.getsource(registro._fila)


def test_la_PK_de_la_fila_y_la_identidad_del_PROBLEMA_son_cosas_distintas():
    """`clave` (tipo:sujeto:regla) dedupea DENTRO de una pasada — dos detectores
    viendo lo mismo son una fila. `clave_item` (sujeto|causa) es quién es el
    problema. Confundirlas es lo que hizo falta separar."""
    h = {"tipo": "sin_precio", "ticker": "AO29", "regla": "no_suscripto"}
    from api.services import av_agent_items
    assert c._clave(h) == "sin_precio:AO29:no_suscripto"
    assert av_agent_items.clave_de_problema("AO29", "no_suscripto") == "ao29|no_suscripto"


# ── EL ESPEJO SE MUDÓ A LA PUERTA (Fase 1, 2026-08-24) ─────────────────────

def test_el_espejo_lo_hace_LA_PUERTA_y_con_la_misma_guarda():
    """⚠️ Acá se exigía que `ciclo()` llamara a `sincronizar` por su cuenta.
    Eran DOS escrituras del mismo hecho —la foto y el objeto— hechas por
    separado, y por eso podían quedar desincronizadas: AHORA y ENCONTRÓ
    contando distinto del mismo problema.

    Ahora salen de la misma llamada. Y la guarda viaja con ellas: lo que el
    daemon no alcanzó a mirar no se cierra por ausencia."""
    src = inspect.getsource(c._escribir_la_foto)
    assert "registro.guardar(" in src
    assert "evaluados=evaluados" in src
    # y `ciclo()` le pasa lo que DECLARÓ haber mirado, no una lista fija
    assert "_escribir_la_foto(list(por_clave.values()), evaluados)" in \
        inspect.getsource(c.ciclo)


def test_los_avisos_TRANSITORIOS_no_se_vuelven_objetos():
    """`recuperado` y `respuesta` vencen en minutos: viajan en la foto porque es
    lo que hay que mostrar ahora, pero darles ciclo de vida llenaría la memoria
    de cosas que nacen y se cierran cada cinco minutos sin que nadie las mire.
    Un problema tiene historia; una buena noticia no."""
    src = inspect.getsource(c._escribir_la_foto)
    assert "objetos=list(hallazgos)" in src


def test_la_foto_no_puede_tumbar_la_pasada():
    """La pasada del centinela es lo que la mesa mira en rueda."""
    src = inspect.getsource(c.ciclo)
    cola = src.split("_escribir_la_foto(")[1][:400]
    assert "except Exception" in cola
