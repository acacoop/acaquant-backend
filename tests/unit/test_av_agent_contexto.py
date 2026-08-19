"""EL AGENTE CONOCE LA BASE SIN QUE NADIE SE LO ESCRIBA — `av_agent_contexto`.

    *«Que sepa exactamente cada tabla que hay y cómo funciona esa tabla en cuanto
    a los datos… que no dependa de un git pull, que no dependa de cosas
    estáticas»* (user, 2026-08-19).

Lo que se congela acá es **que no haya ninguna lista**: el inventario sale del
catálogo de Postgres y la cadencia se MIDE. Y las trampas que harían mentir a la
medición, que son las que hacen que un detector así se pueda creer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.services import av_agent_contexto as ctx


def _hace(**kw):
    return datetime.now(UTC) - timedelta(**kw)


def _cada(seg: float, n: int = 40, desde=None):
    """n escrituras separadas por `seg` segundos, la más reciente primero."""
    base = desde or datetime.now(UTC)
    return [{"t": base - timedelta(seconds=seg * i)} for i in range(n)]


# ── NO HAY LISTAS: todo se deriva ──────────────────────────────────────────

def test_no_existe_ninguna_lista_de_tablas_hardcodeada():
    """**El invariante que pidió el user.** Si alguien agrega una lista de tablas
    a este módulo, el agente vuelve a depender de un `git pull` para saber qué
    existe — y una tabla creada el martes tendría que esperar a que alguien se
    acuerde de anotarla."""
    import ast
    import inspect

    # Se mira el CÓDIGO, no el texto del archivo: los nombres de tabla aparecen
    # en la documentación como EJEMPLO de lo que no hay que hacer, y un test que
    # falla por su propio comentario no prueba nada (misma lección que el de los
    # explicadores con la palabra "XIRR").
    arbol = ast.parse(inspect.getsource(ctx))
    literales = {n.value for n in ast.walk(arbol)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    # Los docstrings son constantes también: se sacan mirando dónde cuelgan.
    docs = {ast.get_docstring(n) for n in ast.walk(arbol)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
    codigo = {x for x in literales - docs if x}
    for lit in codigo:
        assert "." not in lit or not any(
            lit.startswith(f"{sch}.") for sch in
            ("portafolio", "mercado", "operaciones", "clientes", "valuaciones")), (
            f"«{lit}» hardcodeada: el inventario tiene que salir de pg_catalog, "
            "si no el agente depende de un git pull para saber qué existe")


def test_el_inventario_sale_del_catalogo_de_postgres():
    import inspect
    src = inspect.getsource(ctx.inventario)
    assert "pg_class" in src and "pg_namespace" in src
    assert "relkind = 'r'" in src


# ── La cadencia se MIDE ────────────────────────────────────────────────────

@pytest.mark.parametrize(("seg", "esperada"), [
    (5,           "tiempo_real"),
    (60,          "tiempo_real"),
    (3600,        "intradiaria"),
    (86400,       "diaria"),
    (7 * 86400,   "semanal"),
    (30 * 86400,  "mensual"),
])
def test_clasifica_la_cadencia_por_lo_que_MIDE(monkeypatch, seg, esperada):
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: _cada(seg))
    r = ctx.medir("s", "t", "updated_at")
    # Una diaria que escribe los 7 días es `diaria`; si solo escribiera de lunes
    # a viernes sería `diaria_habil` (ver el test de abajo).
    assert r["cadencia"] in (esperada, "diaria_habil")
    assert r["intervalo_p50_s"] == pytest.approx(seg, rel=0.1)


def test_la_MEDIANA_evita_que_un_hueco_desplace_la_cadencia(monkeypatch):
    """**La trampa principal.** Con PROMEDIO, un fin de semana o un backfill viejo
    convierten una tabla diaria en semanal — y entonces se le deja de exigir
    frescura justo a la que importa."""
    filas = _cada(86400, n=30)
    filas.append({"t": filas[-1]["t"] - timedelta(days=400)})   # un dato viejísimo
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: filas)
    assert ctx.medir("s", "t", "updated_at")["cadencia"] in ("diaria", "diaria_habil")


def test_una_tabla_de_dias_habiles_se_distingue_de_una_de_7_dias(monkeypatch):
    """Exigirle el sábado a una tabla que solo escribe de lunes a viernes es un
    falso positivo garantizado TODOS los fines de semana."""
    lunes = datetime(2026, 8, 17, 12, tzinfo=UTC)     # es lunes
    filas, d = [], lunes
    while len(filas) < 30:
        if d.weekday() < 5:
            filas.append({"t": d})
        d -= timedelta(days=1)
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: filas)
    assert ctx.medir("s", "t", "fecha")["cadencia"] == "diaria_habil"


def test_con_pocas_escrituras_NO_se_afirma_un_patron(monkeypatch):
    """Con 3 puntos cualquier cosa parece un ritmo."""
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: _cada(60, n=3))
    assert ctx.medir("s", "t", "updated_at")["cadencia"] == "eventual"


def test_una_carga_de_una_sola_vez_es_ESTATICA(monkeypatch):
    """Todas las filas al mismo instante = una siembra o un import, no un ritmo."""
    ahora = datetime.now(UTC)
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: [{"t": ahora} for _ in range(30)])
    assert ctx.medir("s", "t", "creado_at")["cadencia"] == "estatica"


def test_una_tabla_vacia_lo_dice(monkeypatch):
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: [])
    assert ctx.medir("s", "t", "updated_at")["cadencia"] == "vacia"


def test_se_mide_sobre_las_ULTIMAS_escrituras(monkeypatch):
    """Una tabla que hace un año era diaria y hoy es live tiene que decir LIVE.
    Por eso se ordena DESC y se limita: describe el comportamiento actual."""
    import inspect
    src = inspect.getsource(ctx.medir)
    assert "DESC" in src and "LIMIT" in src


# ── La pregunta del user: ¿hay datos o no? ─────────────────────────────────

def test_una_tabla_al_dia_esta_ok():
    p = {"cadencia": "diaria", "ultimo_dato": _hace(hours=6)}
    assert ctx.frescura(p)["estado"] == "ok"


def test_una_tabla_que_dejo_de_escribir_esta_atrasada():
    p = {"cadencia": "tiempo_real", "ultimo_dato": _hace(hours=5)}
    f = ctx.frescura(p)
    assert f["estado"] == "atrasada" and "tiempo real" in f["motivo"]


def test_el_FIN_DE_SEMANA_no_cuenta_como_atraso():
    """Sin esto, toda tabla de días hábiles aparece atrasada cada lunes."""
    lunes = datetime(2026, 8, 17, 14, tzinfo=UTC)
    viernes = datetime(2026, 8, 14, 14, tzinfo=UTC)
    p = {"cadencia": "diaria_habil", "ultimo_dato": viernes}
    assert ctx.frescura(p, ahora=lunes)["estado"] == "ok"


def test_a_una_tabla_SIN_RITMO_no_se_le_exige_frescura():
    """A una de carga manual no se le puede pedir que escriba, y marcarla en rojo
    todos los días es cómo se entrena a alguien para ignorar una pantalla."""
    for cad in ("eventual", "estatica", "vacia", None):
        p = {"cadencia": cad, "ultimo_dato": _hace(days=400)}
        assert ctx.frescura(p)["estado"] == "no_se_puede_saber", cad


def test_los_topes_son_GENEROSOS_no_ajustados():
    """Un detector que avisa al primer atraso avisa todos los días, y de uno así
    no se desconfía: se lo ignora."""
    assert ctx.TOLERANCIA_S["tiempo_real"] >= 15 * 60
    assert ctx.TOLERANCIA_S["diaria"] >= 2 * 86400


# ── El detector no pisa a los contratos declarados ─────────────────────────

def test_las_tablas_CON_CONTRATO_no_se_reportan_dos_veces(monkeypatch):
    """`portafolio.tenencia` la mira `salud.CONTRATOS`, que es MÁS estricto: ahí
    el «debería» es una decisión de negocio y no un promedio observado.

    ⚠️ Y es la razón por la que la cadencia aprendida no alcanza sola: si el job
    lleva tres días roto, la «normalidad observada» se corre y el detector se
    acostumbra al problema."""
    monkeypatch.setattr(ctx, "_ya_tienen_contrato", lambda: {"portafolio.tenencia"})
    monkeypatch.setattr(ctx, "perfiles", lambda solo_con_ritmo=False: [
        {"schema": "portafolio", "tabla": "tenencia", "cadencia": "diaria_habil",
         "ultimo_dato": _hace(days=30), "col_fecha": "fecha",
         "intervalo_p50_s": 86400, "filas": 10},
        {"schema": "otro", "tabla": "x", "cadencia": "diaria_habil",
         "ultimo_dato": _hace(days=30), "col_fecha": "fecha",
         "intervalo_p50_s": 86400, "filas": 10}])
    h = ctx.detectar_tablas()
    assert [x["ticker"] for x in h] == ["otro.x"]


def test_el_hallazgo_explica_de_donde_sale_la_cadencia(monkeypatch):
    """«Nadie declaró esto» es la mitad del valor del aviso: si pareciera una
    regla escrita a mano, el que lee la buscaría para discutirla."""
    monkeypatch.setattr(ctx, "_ya_tienen_contrato", set)
    monkeypatch.setattr(ctx, "perfiles", lambda solo_con_ritmo=False: [
        {"schema": "s", "tabla": "t", "cadencia": "tiempo_real",
         "ultimo_dato": _hace(hours=8), "col_fecha": "updated_at",
         "intervalo_p50_s": 30, "filas": 100}])
    h = ctx.detectar_tablas()[0]
    assert h["severidad"] == "alta"
    assert "cómo se comporta la tabla" in h["evidencia"]["texto"]
    assert isinstance(h["evidencia"], dict)


def test_un_error_no_tumba_el_detector(monkeypatch):
    monkeypatch.setattr(ctx, "perfiles",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert ctx.detectar_tablas() == []
