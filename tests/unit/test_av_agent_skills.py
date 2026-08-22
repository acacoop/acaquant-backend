"""EL REGISTRO ÚNICO DE HABILIDADES — `api/services/av_agent_skills.py`.

**Estos tests SON la ley.** El user (2026-08-19):

    *«Por ley y regla, todo lo nuevo que se agregue de funcionalidad o habilidad
    tiene que quedar en esta tab, para que se vaya mapeando todo lo que va
    consolidando. Y a su vez dejar asentado si esa skill usa IA o no.»*

Una regla que depende de que alguien se acuerde no es una regla. Acá se hace
cumplir de dos formas: el catálogo **se deriva** (una skill aparece por existir,
no por acordarse de anotarla) y estos tests fallan si alguien suma una capacidad
por un camino que el catálogo no mira.
"""
from __future__ import annotations

import pytest

from api.services import av_agent_skills as sk


@pytest.fixture(scope="module")
def cat():
    return sk.catalogo()


# ── LA LEY: nada puede quedar afuera ───────────────────────────────────────

def test_TODO_explicador_esta_en_el_catalogo(cat):
    from api.services.av_agent_explicar import EXPLICADORES
    ids = {s.id for s in cat}
    for e in EXPLICADORES:
        assert f"explicar.{e}" in ids, f"el explicador «{e}» no está mapeado"


def test_TODA_accion_esta_en_el_catalogo(cat):
    from api.services.av_agent_hacer import ACCIONES
    ids = {s.id for s in cat}
    for a in ACCIONES:
        assert f"resolver.{a}" in ids, f"la acción «{a}» no está mapeada"


def test_TODO_detector_esta_en_el_catalogo(cat):
    from api.services.av_agent import ACCION_POR_TIPO
    ids = {s.id for s in cat}
    for t in ACCION_POR_TIPO:
        assert f"detectar.{t}" in ids, f"el detector «{t}» no está mapeado"


def test_TODO_control_esta_en_el_catalogo(cat):
    from jobs.controles_datos import CONTROLES
    ids = {s.id for s in cat}
    for c in CONTROLES:
        assert f"detectar.control.{c.id}" in ids, f"el control «{c.id}» falta"


def test_todo_detector_nuevo_tiene_que_describirse():
    """El único pedazo que va a mano son las descripciones de los detectores
    (son funciones sueltas, no un registro con metadatos). Si alguien suma uno
    y no lo describe, esto falla — que es la forma de que la ley no dependa de
    la memoria de nadie."""
    from api.services.av_agent import ACCION_POR_TIPO
    sin_describir = set(ACCION_POR_TIPO) - set(sk._QUE_DETECTA)
    assert not sin_describir, (
        f"detectores sin descripción en _QUE_DETECTA: {sin_describir}. "
        "Una skill sin describir es una capacidad que el agente tiene y nadie "
        "sabe que tiene.")


def test_no_se_describen_detectores_que_no_existen():
    """La lista a mano tampoco puede quedar INFLADA: decir que el agente sabe
    algo que ya no hace es peor que no decir nada."""
    from api.services.av_agent import ACCION_POR_TIPO
    fantasmas = set(sk._QUE_DETECTA) - set(ACCION_POR_TIPO)
    assert not fantasmas, f"describen detectores inexistentes: {fantasmas}"


# ── Cada skill se declara entera ───────────────────────────────────────────

def test_toda_skill_declara_si_usa_IA(cat):
    """Pedido explícito del user. **No es una curiosidad técnica: cambia cuánto
    hay que desconfiar.** Una determinista se audita una vez; una que pasa por
    el modelo hay que mirarla caso por caso."""
    assert cat
    for s in cat:
        assert s.usa_ia in (sk.SIN_IA, sk.IA_OPCIONAL, sk.CON_IA), s.id


def test_la_que_dice_usar_IA_tiene_que_decir_PARA_QUE(cat):
    """«Usa IA» sin decir para qué no informa nada: no se puede saber si el
    modelo decide algo o solo redacta."""
    for s in cat:
        if s.usa_ia != sk.SIN_IA:
            assert s.para_que_la_ia.strip(), f"{s.id} dice usar IA y no dice para qué"


def test_la_que_NO_usa_IA_no_puede_explicar_para_que_la_usa(cat):
    """La contradicción al revés: si no usa, no hay para qué."""
    for s in cat:
        if s.usa_ia == sk.SIN_IA:
            assert not s.para_que_la_ia, f"{s.id} dice no usar IA pero la explica"


def test_toda_skill_dice_de_que_registro_salio(cat):
    """La trazabilidad es lo que permite ir a mirar el código de una capacidad
    sin buscarla. Sin esto el catálogo es una lista de promesas."""
    assert all(s.fuente for s in cat)


def test_toda_skill_tiene_nombre_en_castellano_y_no_un_id(cat):
    for s in cat:
        assert len(s.nombre) > 10, s.id
        assert "_" not in s.nombre, f"{s.id}: «{s.nombre}» parece un id, no un nombre"


def test_los_ids_son_unicos(cat):
    ids = [s.id for s in cat]
    assert len(ids) == len(set(ids))


def test_los_tres_tipos_estan_representados(cat):
    """Detectar → explicar → resolver es el orden en que crece el agente. Que
    los tres tengan contenido es lo que dice que no se quedó en la mitad."""
    tipos = {s.tipo for s in cat}
    assert tipos == {sk.DETECTAR, sk.EXPLICAR, sk.RESOLVER}


# ── Lo que dibuja la pantalla ──────────────────────────────────────────────

def test_la_vista_cuenta_bien_cuanta_IA_hay_de_verdad():
    """Es el número que contesta *«¿cuánto de esto es IA?»* sin discutir. Contar
    todas como IA infla lo que el modelo hace; contarlas como no-IA esconde
    dónde hay que mirar."""
    v = sk.vista()
    assert v["total"] == sum(v["ia"].values())
    assert v["total"] == sum(len(x) for x in v["por_tipo"].values())


def test_la_mayoria_de_las_skills_NO_usan_modelo():
    """No es una preferencia estética: es el diseño del programa. Si esto se da
    vuelta, alguien está mandando al modelo trabajo que hace una función."""
    v = sk.vista()
    assert v["ia"]["no"] > v["ia"]["opcional"] + v["ia"]["si"]


def test_ninguna_skill_de_DETECCION_usa_modelo(cat):
    """Lo que el agente ENCUENTRA lo encuentra una función determinista que
    corre sola. El modelo aparece después, para leer patrones entre hallazgos —
    nunca para decidir si algo está mal."""
    for s in cat:
        if s.tipo == sk.DETECTAR:
            assert s.usa_ia == sk.SIN_IA, f"{s.id} detecta con IA"


def test_un_registro_caido_no_tumba_el_catalogo(monkeypatch):
    """El catálogo tiene que poder dibujarse aunque una fuente falle: si no, un
    import roto deja la pantalla en blanco en vez de mostrar las otras 20."""
    monkeypatch.setattr(sk, "_de_acciones",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert len(sk.catalogo()) > 10


def test_la_vista_publica_las_tareas_de_IA_registradas():
    """Para poder contrastar lo que las skills DICEN que usan contra lo que el
    gateway tiene. Si una declara IA y no hay tarea, alguien se equivocó."""
    v = sk.vista()
    assert "av_agent_accion" in v["tareas_ia"]


# ── Que lo declarado exista de verdad: el catálogo no puede prometer de más ──

def test_todo_detector_declara_EN_QUE_JOB_corre():
    """Sin esto, la tab dice «se ve en ENCONTRÓ» para todos por igual — incluso
    para uno que no lo escribe en ningún lado, que fue exactamente el caso."""
    from api.services import av_agent
    faltan = set(av_agent.ACCION_POR_TIPO) - set(sk._DONDE_CORRE)
    assert not faltan, f"detectores sin declarar dónde corren: {sorted(faltan)}"
    sobran = set(sk._DONDE_CORRE) - set(av_agent.ACCION_POR_TIPO)
    assert not sobran, f"declarados pero ya no existen: {sorted(sobran)}"


def test_el_job_de_cada_detector_ESCRIBE_hallazgos():
    """**El invariante que faltaba.** `permiso_flojo`, `tabla_quieta` y
    `db_cambio` corrían todas las noches y solo imprimían en el log: el hallazgo
    moría ahí y la pantalla no mostraba nada. No alcanza con que el job exista —
    tiene que PERSISTIR, o el detector es un `print` con buena prensa."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[2]
    for tipo, modulo in sk._DONDE_CORRE.items():
        f = raiz / (modulo.replace(".", "/") + ".py")
        assert f.exists(), f"{tipo} dice correr en {modulo}, que no existe"
        src = f.read_text(encoding="utf-8")
        # El daemon del centinela persiste por su SERVICE (tabla propia +
        # espejo en `av_agent_items`), no por la foto de hallazgos: su rastro
        # en el módulo es el import del service que escribe.
        assert ("reemplazar_hallazgos" in src or "av_agent_hallazgos" in src
                or "av_agent_centinela" in src), (
            f"{modulo} no escribe hallazgos: {tipo} no llegaría a ENCONTRÓ")


def test_el_job_de_cada_detector_ESTA_EN_EL_CRONTAB():
    """Un detector que nadie agenda no corre solo, y todo esto existe para no
    tener que pedirle las cosas al agente."""
    from api.services import jobs_catalogo
    agendados = jobs_catalogo.schedules_por_modulo()
    for tipo, modulo in sk._DONDE_CORRE.items():
        # «Agendado» también es un DAEMON de systemd (siempre prendido): el
        # centinela no está en el crontab porque no para nunca.
        assert agendados.get(modulo) or sk._es_daemon(modulo), (
            f"{tipo} corre en {modulo}, que no está agendado ni es un daemon")


def test_el_horario_NO_esta_escrito_a_mano():
    """Se lee del crontab. Un horario copiado en otro archivo se desincroniza el
    día que se cambia uno de los dos — y nadie se entera hasta que importa."""
    import inspect
    src = inspect.getsource(sk._cada_cuanto)
    assert "schedules_por_modulo" in src


# ── JERARQUÍA: de qué habla cada habilidad ────────────────────────────────

def test_el_nombre_y_la_descripcion_NO_son_la_misma_frase(cat):
    """**Lo que el user marcó como «queda feo, se repiten las cosas».** El
    catálogo usaba UN solo string de nombre y de descripción, así que cada fila
    mostraba la misma frase dos veces. Y no era estética: un nombre de veinte
    palabras no se puede escanear, que es lo único que uno hace con 37 filas."""
    for s in cat:
        assert s.nombre.strip().lower() != s.que_hace.strip().lower(), s.id


def test_el_nombre_es_CORTO(cat):
    """Si el nombre vuelve a ser la explicación, la lista deja de escanearse."""
    largos = [s.id for s in cat if len(s.nombre) > 60]
    assert not largos, f"nombres que no se leen de un vistazo: {largos}"


def test_toda_skill_declara_su_DOMINIO(cat):
    """El TIPO dice cómo trabaja (detecta / explica / resuelve); el DOMINIO dice
    SOBRE QUÉ, que es la pregunta que uno se hace primero. Sin él la tab es una
    lista plana donde un chequeo de permisos convive con un bono sin cronograma
    y no hay forma de mirar un área sola."""
    for s in cat:
        assert s.dominio in sk.DOMINIOS, f"{s.id} → dominio {s.dominio!r}"


def test_los_DETECTORES_declaran_dominio_uno_por_uno():
    """Nada de default silencioso: un detector nuevo sin dominio caería en el
    cajón genérico y nadie lo notaría."""
    from api.services import av_agent
    faltan = set(av_agent.ACCION_POR_TIPO) - set(sk._DOMINIO_DETECTOR)
    assert not faltan, f"detectores sin dominio: {sorted(faltan)}"


def test_el_dominio_NO_se_adivina_del_titulo():
    """«¿Hay algún endpoint más lento que lo normal?» contiene la palabra
    endpoint y caía en SEGURIDAD, cuando habla de rendimiento. Adivinar el
    dominio leyendo un título es la clase de heurística frágil que este proyecto
    ya paga en otros lados."""
    assert sk._DOMINIO_EXPLICADOR["velocidad"] == sk.SISTEMA
    assert sk._DOMINIO_EXPLICADOR["protegidos"] == sk.SEGURIDAD


def test_la_vista_agrupa_por_dominio_y_no_muestra_los_vacios():
    """Un título con cero filas es ruido, y además sugiere que falta algo."""
    v = sk.vista()
    assert v["por_dominio"] and all(f for f in v["por_dominio"].values())
    assert v["dominios"] == [d for d in sk.DOMINIOS if d in v["por_dominio"]]
    assert sum(len(f) for f in v["por_dominio"].values()) == v["total"]


# ── (2026-08-19) EL PUENTE ENTRE EL CATÁLOGO Y LA MEDICIÓN ──────────────────

def _literales(nodo, clave: str) -> set[str]:
    """Los strings que en ESTA función se usan como valor de `clave`.

    Cubre las tres formas en que un detector escribe una regla: la clave de un
    dict (`"regla": "rafaga"`), una asignación simple (`regla = "rafaga"`) y una
    desempaquetada (`regla, sev = "rafaga", "alta"`).
    """
    import ast
    out: set[str] = set()
    for n in ast.walk(nodo):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (isinstance(k, ast.Constant) and k.value == clave
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)):
                    out.add(v.value)
        if not isinstance(n, ast.Assign) or not n.targets:
            continue
        destino = n.targets[0]
        if (isinstance(destino, ast.Name) and destino.id == clave
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)):
            out.add(n.value.value)
        if isinstance(destino, ast.Tuple) and isinstance(n.value, ast.Tuple):
            for t, v in zip(destino.elts, n.value.elts):
                if (isinstance(t, ast.Name) and t.id == clave
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)):
                    out.add(v.value)
    return out


def _reglas_emitidas() -> dict[str, set[str]]:
    """Lo que los detectores emiten DE VERDAD, leído del código.

    ⚠️ **Antes esto era un regex sobre `_hallazgo("tipo", …, "regla")`**, o sea
    que solo veía UNA de las formas de escribir un detector: los que arman el
    dict inline eran invisibles, y por eso cinco declaraban `()` — el guardián
    los daba por buenos sin haber mirado nada. Ahora se recorre el AST y se
    emparejan por FUNCIÓN: si una función menciona un solo `tipo`, las reglas
    que escribe son de ese tipo.

    Al estrenarlo apareció la primera: `tabla_quieta` emitía `sin_escribir` sin
    declararla.
    """
    import ast
    import pathlib
    import re
    from collections import defaultdict
    out = defaultdict(set)
    for f in sorted(pathlib.Path("api/services").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        # (a) La forma POSICIONAL: `_hallazgo("tipo", …, "regla")`. Es la que
        #     usan los detectores de bonos y el AST no la puede emparejar — los
        #     dos argumentos son posiciones, no claves. Las dos pasadas se SUMAN:
        #     al reemplazar una por la otra se perdieron seis reglas de golpe.
        for m in re.finditer(r'_hallazgo\(\s*"([a-z_]+)"\s*,\s*[^,]+,\s*"([a-z_]+)"',
                             src):
            out[m.group(1)].add(m.group(2))
        # (b) La forma DICT, que el regex no veía.
        try:
            arbol = ast.parse(src)
        except SyntaxError:                             # pragma: no cover
            continue
        for fn in [n for n in ast.walk(arbol)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            tipos = _literales(fn, "tipo")
            # Con dos tipos en la misma función no se puede saber cuál regla es
            # de cuál, y adivinar sería peor que no mirar.
            if len(tipos) == 1:
                out[next(iter(tipos))] |= _literales(fn, "regla")
    return out


def test_las_reglas_declaradas_son_las_que_se_EMITEN():
    """El catálogo lista por TIPO y el eval set mide por REGLA. Si el puente
    declara una regla que nadie emite, la skill mostraría votos de una causa
    fantasma."""
    from api.services.av_agent_skills import _REGLAS_DETECTOR
    reales = _reglas_emitidas()
    for tipo, reglas in _REGLAS_DETECTOR.items():
        for r in reglas:
            assert r in reales.get(tipo, set()), (
                f"«{tipo}» declara la regla «{r}» y no la emite")


def test_no_falta_ninguna_regla_en_el_puente():
    """Al revés, y es el que importa: una regla nueva sin listar deja sus votos
    fuera de toda fila, y la skill se ve «sin votar» TENIENDO evidencia. Un error
    que no da ningún síntoma."""
    from api.services.av_agent_skills import _REGLAS_DETECTOR
    for tipo, reales in _reglas_emitidas().items():
        if tipo not in _REGLAS_DETECTOR:
            continue                      # eso lo caza el test del catálogo
        # Vacío EXPLÍCITO = la skill se muestra sin medición, a propósito.
        if not _REGLAS_DETECTOR[tipo]:
            continue
        faltan = reales - set(_REGLAS_DETECTOR[tipo])
        assert not faltan, f"«{tipo}» emite {sorted(faltan)} y no está(n) declarada(s)"


# ── (2026-08-19) LAS CAPACIDADES QUE NO SON DETECTOR/ACCIÓN/EXPLICADOR ──────

def test_las_capacidades_declaradas_EXISTEN_de_verdad():
    """Una capacidad listada que ya no está manda a buscar a un lugar vacío —
    peor que no listarla. Es la misma lección del `donde` de los detectores."""
    import importlib

    from api.services.av_agent_skills import _CAPACIDADES
    for c in _CAPACIDADES:
        importlib.import_module(c["modulo"])


def test_las_capacidades_estan_en_el_catalogo(cat):
    """La LEY de §0.o: toda habilidad nueva queda mapeada en SKILLS."""
    from api.services.av_agent_skills import _CAPACIDADES
    ids = {s.id for s in cat}
    for c in _CAPACIDADES:
        assert c["id"] in ids, f"{c['id']} no llegó al catálogo"


def test_mandar_un_mensaje_NO_usa_modelo(cat):
    """El user lo dijo al pedirlo: «tampoco termina de ser IA esto». Sale todo de
    la base — quiénes son los operadores y cuáles son sus saldos."""
    msg = [s for s in cat if s.id.startswith("mensajes.")]
    assert msg, "las capacidades de mensajes no están en el catálogo"
    assert all(s.usa_ia == "no" for s in msg)
