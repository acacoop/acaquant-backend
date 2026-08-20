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
    # Reloj fijo por el mismo motivo que el de abajo: el veredicto no puede
    # depender de a qué hora corra la suite.
    ahora = datetime(2026, 8, 19, 19, tzinfo=UTC)
    p = {"cadencia": "diaria", "ultimo_dato": datetime(2026, 8, 19, 13, tzinfo=UTC)}
    assert ctx.frescura(p, ahora=ahora)["estado"] == "ok"


def test_una_tabla_que_dejo_de_escribir_esta_atrasada():
    """⚠️ EL RELOJ VA FIJO. Las cadencias intradía se miden en segundos de MERCADO
    ABIERTO (§0.u), así que «hace 5 horas» vale distinto según la hora en que
    corra el test: a las 21 ART esas 5 horas son ~35 minutos de rueda y la tabla
    está OK. Sin fijar el reloj, el test pasa de día y falla de noche — y una
    suite que falla por la hora enseña a re-correrla en vez de leerla.

    Miércoles 19:00 UTC (rueda 13-20) con el último dato a las 14:00: cinco horas
    de mercado abierto, sin ambigüedad."""
    ahora = datetime(2026, 8, 19, 19, tzinfo=UTC)
    p = {"cadencia": "tiempo_real", "ultimo_dato": datetime(2026, 8, 19, 14, tzinfo=UTC)}
    f = ctx.frescura(p, ahora=ahora)
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


# ── El reloj del MERCADO, no el de la pared (2026-08-19) ───────────────────

def test_una_tabla_de_rueda_NO_esta_atrasada_de_noche():
    """**El caso que produjo 47 falsos positivos.** *«Si el precio cierra a las
    17 y abre a las 10:30, es obvio que no va a actualizar»* (user). El job corre
    23:30 UTC —tres horas y media después del cierre— así que con tiempo de reloj
    marcaría todas las tablas de rueda TODAS las noches, para siempre."""
    from datetime import UTC, datetime
    ult = datetime(2026, 8, 19, 19, 58, tzinfo=UTC)      # 16:58 ART, casi el cierre
    f = ctx.frescura({"cadencia": "tiempo_real", "ultimo_dato": ult},
                     ahora=datetime(2026, 8, 19, 23, 30, tzinfo=UTC))
    assert f["estado"] == "ok"
    assert f["unidad"] == "de rueda"


def test_pero_si_NO_escribio_EN_TODA_la_rueda_SI_esta_atrasada():
    """No es indulgencia: es medir en la unidad correcta. Una tabla de tiempo
    real que se pasó la rueda entera sin escribir está rota igual."""
    from datetime import UTC, datetime
    ult = datetime(2026, 8, 18, 19, 58, tzinfo=UTC)       # el cierre de AYER
    f = ctx.frescura({"cadencia": "tiempo_real", "ultimo_dato": ult},
                     ahora=datetime(2026, 8, 19, 23, 30, tzinfo=UTC))
    assert f["estado"] == "atrasada"


def test_recien_abierto_el_mercado_no_hay_atraso_todavia():
    """A las 10:10 ART hace diez minutos que abrió. Sin esto, cada mañana entre
    la apertura y el arranque de los motores hay un rato de avisos falsos — el
    mismo agujero que ya se tapó en el detector de motores."""
    from datetime import UTC, datetime
    f = ctx.frescura({"cadencia": "tiempo_real",
                      "ultimo_dato": datetime(2026, 8, 18, 20, 0, tzinfo=UTC)},
                     ahora=datetime(2026, 8, 19, 13, 10, tzinfo=UTC))
    assert f["estado"] == "ok" and f["atraso_s"] == 600


def test_el_finde_no_cuenta_para_una_tabla_de_rueda():
    """El lunes a la mañana no hay 65 horas de atraso: hay media hora."""
    from datetime import UTC, datetime
    seg = ctx._segundos_de_rueda(datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
                                 datetime(2026, 8, 17, 13, 30, tzinfo=UTC))
    assert seg == 30 * 60


def test_una_DIARIA_se_sigue_midiendo_en_tiempo_de_reloj():
    """Un job diario corre a la hora que corre y muchos corren de noche: medirlo
    en tiempo de rueda le daría meses de gracia."""
    from datetime import UTC, datetime
    f = ctx.frescura({"cadencia": "diaria",
                      "ultimo_dato": datetime(2026, 8, 10, 3, 0, tzinfo=UTC)},
                     ahora=datetime(2026, 8, 19, 3, 0, tzinfo=UTC))
    assert f["estado"] == "atrasada" and f["unidad"] == "de reloj"


# ── Una RÁFAGA no es un ritmo ──────────────────────────────────────────────

def test_una_tabla_de_auditoria_NO_es_tiempo_real(monkeypatch):
    """**El otro origen de los 47.** Una tabla de auditoría se escribe a los
    saltos: alguien edita y entran 15 filas con dos segundos de diferencia, y
    después nada por tres semanas. La mediana mira ADENTRO de la ráfaga y dice
    «tiempo real» — así `clientes.aca_valores`, sin escribir hace 43 días, salía
    clasificada como live y por lo tanto atrasada."""
    from datetime import UTC, datetime
    from datetime import timedelta as _td
    base = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    # 20 escrituras en 40 segundos: una ráfaga, un solo día. (DESC, como el SQL.)
    filas = [{"t": base - _td(seconds=2 * i)} for i in range(20)]
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: filas)
    monkeypatch.setattr(ctx, "_dias_con_escritura", lambda *a: 1)
    r = ctx.medir("clientes", "aca_valores", "actualizado_at")
    assert r["cadencia"] == "eventual"
    assert r["degradada_de"] == "tiempo_real"


def test_la_que_SI_escribe_todos_los_dias_conserva_su_cadencia(monkeypatch):
    """La degradación no puede llevarse puestas a las tablas que sí tienen ritmo:
    esas son justamente las que hay que vigilar."""
    from datetime import UTC, datetime, timedelta
    base = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    filas = [{"t": base - timedelta(seconds=30 * i)} for i in range(30)]
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: filas)
    monkeypatch.setattr(ctx, "_dias_con_escritura", lambda *a: 22)
    assert ctx.medir("mercado", "timesales", "ts")["cadencia"] == "tiempo_real"


def test_si_NO_SE_PUEDE_MEDIR_la_regularidad_no_se_degrada_nada(monkeypatch):
    """«No pude mirar» jamás puede convertirse en un veredicto — la misma regla
    que rige en el resto del agente."""
    from datetime import UTC, datetime, timedelta
    base = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    filas = [{"t": base - timedelta(seconds=30 * i)} for i in range(30)]
    monkeypatch.setattr(ctx, "_q", lambda *a, **k: filas)
    monkeypatch.setattr(ctx, "_dias_con_escritura", lambda *a: None)
    assert ctx.medir("x", "y", "ts")["cadencia"] == "tiempo_real"
