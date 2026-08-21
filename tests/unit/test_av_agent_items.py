"""UN OBJETO, UN CICLO, Y LA MEMORIA QUE FALTABA.

El user (2026-08-21):

    *«Que todo lo del AV Agent esté como objeto. Va a ser SIEMPRE EL MISMO
    ESTILO, solo que va a cambiar el TIPO DE PROBLEMA —log, aviso, etc.— pero
    CÓMO VAN A ESTAR es lo mismo. Después cambiará la solución, el análisis.»*

    *«5 días es mucho. Es el día siguiente para ver si vuelve. Pero a su vez
    tiene que tener memoria y recursos para que siga con el paso del tiempo:
    puede ser 2 días, 3 días…»*

Las dos son la misma idea aplicada a dos cosas: **la forma es una, lo que varía
es un dato.**
"""
from __future__ import annotations

from core import ciclo

# ── LA IDENTIDAD: esto ES la memoria ────────────────────────────────────────

def test_la_clave_NO_lleva_fecha():
    """Es lo que hace que el mismo problema, visto mañana, sea el MISMO objeto.
    Con fecha, cada corrida crearía uno nuevo — que es exactamente lo que hacía
    `av_agent_hallazgos` y por lo que el agente parecía no acordarse de nada."""
    a = ciclo.clave_de("hallazgo", "detector_precio", "AL30", "pata_equivocada")
    b = ciclo.clave_de("hallazgo", "detector_precio", "AL30", "pata_equivocada")
    assert a == b
    assert not any(c.isdigit() and len(a.split("|")) > 4 for c in a)


def test_la_clave_es_INSENSIBLE_a_mayusculas_y_espacios():
    assert (ciclo.clave_de("hallazgo", "d", " AL30 ", "R")
            == ciclo.clave_de("HALLAZGO", "D", "al30", "r"))


def test_CAMBIAR_DE_CAUSA_es_otro_objeto():
    """Si el agente cambia de diagnóstico sobre el mismo bono, es otro juicio y
    merece su propia historia — el mismo par que ya usa el eval set."""
    a = ciclo.clave_de("hallazgo", "d", "AL30", "pata_equivocada")
    b = ciclo.clave_de("hallazgo", "d", "AL30", "sin_punta")
    assert a != b


def test_el_TIPO_es_un_CAMPO_y_no_una_clase():
    """El punto entero: un bono mal cargado, un job caído y una línea de ERROR
    tienen la MISMA forma."""
    for tipo in ("hallazgo", "chequeo", "log", "aviso", "pregunta"):
        it = ciclo.Item(clave="x", tipo=tipo, sujeto="s")
        assert it.estado == ciclo.NUEVO and it.tipo == tipo


# ── EL SEGUIMIENTO: hitos, no un plazo ──────────────────────────────────────

def test_el_PRIMER_hito_es_a_UN_dia():
    """*«5 días es mucho — es el día siguiente para ver si vuelve.»* Si el
    arreglo no sirvió hay que saberlo mañana, no el viernes."""
    assert ciclo.HITOS_DIAS[0] == 1


def test_y_SIGUE_mirando_con_el_paso_del_tiempo():
    """*«Pero a su vez tiene que tener memoria y recursos para que siga con el
    paso del tiempo.»* Aguantar un día no es lo mismo que aguantar un mes, y esa
    diferencia es la que habilita autonomía."""
    assert len(ciclo.HITOS_DIAS) >= 5 and max(ciclo.HITOS_DIAS) >= 30
    assert list(ciclo.HITOS_DIAS) == sorted(ciclo.HITOS_DIAS)


def test_la_confianza_SUBE_hito_a_hito():
    assert ciclo.confianza(0.5) == 0.0            # ni el primer día
    assert 0 < ciclo.confianza(1) < ciclo.confianza(3) < ciclo.confianza(30)
    assert ciclo.confianza(60) == 1.0


def test_siempre_se_sabe_CUANDO_es_el_proximo_control():
    assert ciclo.proximo_hito(0) == 1
    assert ciclo.proximo_hito(2.5) == 3
    assert ciclo.proximo_hito(100) is None        # ya los pasó todos


def test_VOLVER_BORRA_lo_acumulado():
    """Un arreglo que falla al día 8 no es «7 días bueno»: es un arreglo que
    falla. Si la confianza sobreviviera a la vuelta, el número mentiría justo
    en el caso que importa."""
    it = ciclo.Item(clave="x", tipo="hallazgo", estado=ciclo.VOLVIO,
                    resuelto_at="2026-08-01T00:00:00+00:00")
    assert it.confianza_del_arreglo == 0.0


def test_sin_resolver_no_hay_confianza_que_medir():
    assert ciclo.Item(clave="x", tipo="hallazgo").confianza_del_arreglo == 0.0


def test_una_fecha_rota_no_hace_explotar_el_calculo():
    it = ciclo.Item(clave="x", tipo="hallazgo", estado=ciclo.RESUELTO,
                    resuelto_at="no-es-una-fecha")
    assert it.confianza_del_arreglo == 0.0


# ── EL STORE: qué escribe y qué NO pisa ─────────────────────────────────────

def test_ver_NUNCA_pisa_abierto_at():
    """Es lo que convierte «apareció hoy» en «lleva 11 días». Sin esto, un
    problema de hace dos semanas se ve igual de urgente que uno de recién y
    nada acumula antigüedad."""
    import inspect

    from api.services import av_agent_items as st
    sql = inspect.getsource(st.ver)
    do_update = sql.split("DO UPDATE SET")[1].split("RETURNING")[0]
    assert "abierto_at" not in do_update, (
        "el upsert está pisando `abierto_at`: el item perdería su antigüedad")


def test_ver_SI_refresca_el_titulo():
    """El problema es el mismo pero su explicación puede mejorar (una firma
    nueva, una traducción del modelo). Congelar el primer texto sería quedarse
    con el peor."""
    import inspect

    from api.services import av_agent_items as st
    do_update = inspect.getsource(st.ver).split("DO UPDATE SET")[1]
    assert "titulo = EXCLUDED.titulo" in do_update
    assert "afecta = EXCLUDED.afecta" in do_update


def test_si_estaba_RESUELTO_y_reaparece_pasa_a_VOLVIO():
    """No a `nuevo`. Contar como nuevo un problema que vuelve es cómo se pierde
    que algo se arregla y se rompe todas las semanas."""
    import inspect

    from api.services import av_agent_items as st
    src = inspect.getsource(st.ver)
    assert "ciclo.VOLVIO" in src and "vuelto_at" in src
    # Y deja de estar resuelto: si no, el seguimiento le seguiría contando
    # hitos a un arreglo que ya falló.
    assert "resuelto_at = CASE" in src


def test_el_estado_se_decide_en_SQL_y_no_leyendo_primero():
    """Dos detectores corriendo a la vez no se pueden pisar."""
    import inspect

    from api.services import av_agent_items as st
    src = inspect.getsource(st.ver)
    assert "ON CONFLICT (clave) DO UPDATE" in src
    assert src.count("SELECT estado") == 0


def test_un_item_sin_tipo_ni_sujeto_no_se_guarda():
    from api.services import av_agent_items as st
    assert st.ver(tipo="", origen="", sujeto="")["ok"] is False


def test_marcar_RECHAZA_una_transicion_imposible():
    """Un salto imposible se rechaza acá y no se descubre tres pantallas
    después."""
    from api.services import av_agent_items as st
    assert st.marcar("x", "loquesea")["ok"] is False


def test_los_ABIERTOS_incluyen_los_que_VOLVIERON():
    """Un problema que reapareció está abierto — y además merece más atención
    que uno nuevo."""
    import inspect

    from api.services import av_agent_items as st
    src = inspect.getsource(st.abiertos)
    assert "NOT IN ('resuelto', 'ignorado')" in src
    assert "volvio" not in src.split("WHERE")[1].split("ORDER")[0]


def test_el_seguimiento_dice_CUANTOS_hitos_y_cuando_es_el_proximo():
    import inspect

    from api.services import av_agent_items as st
    src = inspect.getsource(st.en_seguimiento)
    for campo in ("hitos", "confianza", "proximo_hito_en_dias", "aguanto"):
        assert f'"{campo}"' in src


# ── la tabla existe y es UNA ────────────────────────────────────────────────

def test_la_tabla_esta_en_el_schema_con_la_clave_como_PK():
    import pathlib
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    i = sql.index("CREATE TABLE IF NOT EXISTS mercado.av_agent_items")
    cuerpo = sql[i:sql.index(");", i)]
    # Solo las DECLARACIONES: los comentarios nombran las formas viejas justo
    # para decir que no están, y el test se cazaba a sí mismo con eso.
    cols = "\n".join(x.split("--")[0].rstrip()
                      for x in cuerpo.splitlines() if x.split("--")[0].strip())
    assert "clave" in cols and "PRIMARY KEY" in cols
    assert "estado" in cols and "veces" in cols
    # NO puede tener las formas viejas: si las tuviera, seríamos la novena.
    assert "resuelto boolean" not in cols and "hecho boolean" not in cols


def test_la_tabla_nueva_esta_declarada_en_el_registro_del_ciclo():
    """La guarda de §0.bc aplica también a la que nace hoy."""
    faltan = ciclo.tablas_del_agente() - {f.tabla for f in ciclo.REGISTRO}
    assert not faltan, faltan


# ── EL PUENTE: registrar lo que está y CERRAR lo que ya no ──────────────────
#
# Acá vive la memoria de verdad. `ver()` sabe que algo SIGUE; lo que nadie sabía
# es que algo DEJÓ de estar, porque una foto por corrida no puede decir «esto ya
# no aparece»: simplemente sale una lista más corta.

class _CurFalso:
    """Cursor de mentira que graba los UPDATE y contesta lo mínimo."""

    def __init__(self):
        self.sql: list[tuple] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return (True, "nuevo", 1, None)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _con_base(monkeypatch):
    from api.services import av_agent_items as st
    cur = _CurFalso()

    class _Conn:
        def cursor(self):
            return cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(st, "get_pool",
                        lambda: type("P", (), {"connection": staticmethod(_Conn)})())
    return st, cur


def _updates_de_cierre(cur):
    return [s for s, _ in cur.sql if s.startswith("UPDATE mercado.av_agent_items")
            and "resuelto_at = now()" in s]


def test_SIN_ORIGEN_no_se_cierra_nada():
    """Cerrar sin saber de quién son los hallazgos cerraría los de otro
    detector."""
    from api.services import av_agent_items as st
    assert st.sincronizar("", [{"tipo": "x"}])["ok"] is False


def test_SIN_declarar_que_se_evaluo_NO_se_cierra_nada(monkeypatch):
    """⚠️ **La guarda más importante del módulo.** Una corrida puede mirar MENOS
    de lo que mira siempre: si 1816 no contesta, `falta_en_base` no se evaluó y
    su lista vacía NO significa que no falte ningún bono.

    Cerrar por ausencia sin saber qué se miró convertiría cada caída de un
    proveedor en «se arreglaron 40 problemas» — el tablero en verde exactamente
    el día que está más ciego."""
    st, cur = _con_base(monkeypatch)
    r = st.sincronizar("censo", [], evaluados=())
    assert r["resueltos"] == 0
    assert not _updates_de_cierre(cur), "cerró sin saber qué se había mirado"


def test_solo_se_cierran_LOS_TIPOS_EVALUADOS(monkeypatch):
    """El tipo que no se pudo mirar se deja como está, aunque no haya aparecido."""
    st, cur = _con_base(monkeypatch)
    st.sincronizar("censo", [], evaluados={"sin_precio"})
    cierres = _updates_de_cierre(cur)
    assert len(cierres) == 1
    _sql, params = next((s, p) for s, p in cur.sql if s in cierres)
    assert ["sin_precio"] in [p for p in params if isinstance(p, list)], params


def test_lo_que_SIGUE_apareciendo_no_se_cierra(monkeypatch):
    """Obvio y por eso hay que probarlo: el cierre excluye lo visto en esta
    corrida."""
    st, cur = _con_base(monkeypatch)
    st.sincronizar("censo", [{"tipo": "sin_precio", "ticker": "AL30",
                              "regla": "sin_punta"}],
                   evaluados={"sin_precio"})
    cierre = next(s for s in _updates_de_cierre(cur))
    assert "NOT (clave = ANY(" in cierre


def test_lo_IGNORADO_no_se_toca_al_cerrar(monkeypatch):
    """Si el cierre lo tocara, un «no me interesa» se perdería solo y el
    hallazgo volvería como nuevo la corrida siguiente."""
    st, cur = _con_base(monkeypatch)
    st.sincronizar("censo", [], evaluados={"sin_precio"})
    cierre = next(s for s in _updates_de_cierre(cur))
    assert "estado NOT IN (%s, %s)" in cierre


def test_se_DICE_que_tipos_quedaron_sin_evaluar(monkeypatch):
    """El silencio se lee igual que un verde: si un tipo no se miró, la corrida
    lo tiene que decir."""
    st, _cur = _con_base(monkeypatch)
    monkeypatch.setattr(st, "_tipos_abiertos", lambda o: {"falta_en_base", "sin_precio"})
    r = st.sincronizar("censo", [], evaluados={"sin_precio"})
    assert r["no_evaluados"] == ["falta_en_base"]


def test_el_job_declara_que_NO_evaluo_los_faltantes_cuando_1816_no_contesto():
    """Es el caso real que el job ya imprime («los FALTANTES no se evaluaron en
    esta corrida») y que la persistencia ignoraba."""
    import inspect

    from jobs.av_agent import _espejar_en_items
    src = inspect.getsource(_espejar_en_items)
    assert 'evaluados.discard("falta_en_base")' in src
    assert 'u.get("faltantes_evaluados")' in src


def test_espejar_NUNCA_tumba_la_corrida():
    """La foto —que es lo que hoy se ve— ya está escrita y no se deshace por un
    problema del espejo nuevo."""
    import inspect

    from jobs import av_agent as job
    src = inspect.getsource(job.persistir)
    assert "_espejar_en_items(res)" in src
    cola = src.split("_espejar_en_items(res)")[1]
    assert "except Exception" in cola


# ── EL BACKFILL: un preview que no puede previsualizar no es un preview ─────

def test_el_PREVIEW_no_depende_de_haber_escrito_antes():
    """⚠️ La primera versión filtraba por `clave IS NOT NULL` — y como esa
    columna recién se llena en el paso que solo corre con `--aplicar`, **el
    dry-run devolvía 0 hallazgos**. Eso no era «no hay nada que migrar»: era
    «no pude mirar», mostrado como un cero.

    Es la misma regla que el agente le exige a sus detectores: *«no pude» no es
    «no existe»*."""
    import inspect

    import scripts.backfill_items as b
    src = inspect.getsource(b.main)
    preview = src.split("WITH todo AS")[1].split('"""')[0]
    assert "clave IS NOT NULL" not in preview, (
        "el preview vuelve a depender de la columna: sin --aplicar mostraría 0")
    assert "_CLAVE_SQL" in preview, "el preview tiene que calcular la clave al vuelo"


def test_la_clave_de_SQL_y_la_de_PYTHON_se_comparan_antes_de_escribir():
    """Conviven dos implementaciones de la identidad y eso es REGLA #9 en su
    forma más cara: si difirieran, el backfill escribiría items con una clave
    que nadie va a buscar nunca — memoria inalcanzable, sin un solo error."""
    import inspect

    import scripts.backfill_items as b
    src = inspect.getsource(b.main)
    assert "ciclo.clave_de" in src and "ABORTADO" in src
    # Y aborta ANTES de escribir.
    assert src.index("ABORTADO") < src.index("INSERT INTO mercado.av_agent_items")


def test_la_expresion_SQL_de_la_clave_esta_escrita_UNA_vez():
    """La usan el UPDATE y el preview. Dos expresiones harían que el dry-run
    muestre una migración y el `--aplicar` haga otra."""
    import inspect

    import scripts.backfill_items as b
    src = inspect.getsource(b)
    assert src.count("array_to_string(array_remove(ARRAY[") == 1


def test_el_backfill_NO_cierra_nada():
    """Una clave que está en el histórico y no en la última corrida pudo
    arreglarse o pudo no evaluarse, y desde un backfill no hay forma de
    distinguirlo. Cerrar sería inventar «se arreglaron N»."""
    import inspect

    import scripts.backfill_items as b
    src = inspect.getsource(b.main)
    assert "resuelto" not in src.lower().split("insert into")[1]
    assert "ON CONFLICT (clave) DO NOTHING" in src
