"""Tests de PROFUNDIDAD DE CLIENTES (api/services/profundidad_sql.py).

Congela lo que la tabla NO puede dejar de cumplir. Todo lo de acá es lógica pura
(meses, labels, predicados como string): no toca la base.
"""
from __future__ import annotations

from datetime import date

from api.services import profundidad_sql as P
from api.services.comercial_sql import _ULT_OP_WHERE, _act_where, _arancel_where


# ── El predicado de ACTIVIDAD es UNO solo ────────────────────────────────────
def test_activo_usa_el_mismo_predicado_que_estado_comercial():
    """PROFUNDIDAD y ESTADO COMERCIAL tienen que llamar "operación" a lo mismo.
    Si esto se rompe, las dos tabs pueden decir cosas distintas de la misma cuenta
    y ninguna de las dos falla."""
    assert _act_where() == _ULT_OP_WHERE
    assert _act_where("o") == "o.anulado_en IS NULL"


def test_arancel_no_excluye_los_cierres():
    """El arancel de caución vive SOLO en el boleto de cierre: filtrarlos (como sí
    hace el VOLUMEN) haría desaparecer aranceles reales sin que nada avise."""
    w = _arancel_where()
    assert "arancel > 0" in w
    assert "etapa IS DISTINCT FROM 'solicitud'" in w
    assert "es_cierre" not in w
    assert _arancel_where("o") == "o.arancel > 0 AND o.etapa IS DISTINCT FROM 'solicitud'"


# ── Meses: el eje de la tabla ────────────────────────────────────────────────
def test_label_es_mmm_aa_en_castellano():
    assert P._label(2025, 7) == "jul-25"
    assert P._label(2025, 12) == "dic-25"
    assert P._label(2026, 1) == "ene-26"


def test_arranca_en_el_inicio_del_ejercicio():
    ms = P._meses(None, None)
    assert ms[0]["mes"] == P.PROFUNDIDAD_INICIO
    assert ms[0]["label"] == "jul-25"


def test_meses_ascendentes_y_sin_huecos():
    ms = P._meses("2025-07", "2026-02")
    assert [m["mes"] for m in ms] == [
        "2025-07", "2025-08", "2025-09", "2025-10",
        "2025-11", "2025-12", "2026-01", "2026-02"]


def test_todo_se_mide_al_ultimo_dia_del_mes():
    """La regla central del pedido: 31/07, no 01/07. Y febrero bisiesto incluido."""
    ms = {m["mes"]: m for m in P._meses("2024-01", "2024-03")}
    assert ms["2024-01"]["ini"] == date(2024, 1, 1)
    assert ms["2024-01"]["fin"] == date(2024, 1, 31)
    assert ms["2024-02"]["fin"] == date(2024, 2, 29)   # bisiesto
    ms2 = {m["mes"]: m for m in P._meses("2025-02", "2025-02")}
    assert ms2["2025-02"]["fin"] == date(2025, 2, 28)


def test_nunca_dibuja_meses_futuros():
    """Una fila de un mes que todavía no pasó sería un cero que parece un dato."""
    hoy = P._hoy_art()
    ms = P._meses("2025-07", "2099-12")
    assert ms[-1]["mes"] == f"{hoy.year:04d}-{hoy.month:02d}"
    assert ms[-1]["en_curso"] is True
    assert all(m["en_curso"] is False for m in ms[:-1])


def test_mes_invalido_cae_al_default_sin_reventar():
    assert P._meses("chirimbolo", "2026-01")[0]["mes"] == P.PROFUNDIDAD_INICIO
    assert P._parse_mes("2025-99", "2025-07") == (2025, 7)
    assert P._parse_mes(None, "2025-07") == (2025, 7)


def test_hay_tope_de_meses():
    """Un `desde` mal tipeado no puede disparar un scan de toda la historia."""
    assert len(P._meses("1900-01", None)) <= P.MAX_MESES


def test_desde_posterior_al_hasta_no_devuelve_vacio():
    ms = P._meses("2026-01", "2025-07")
    assert len(ms) == 1 and ms[0]["mes"] == "2025-07"


# ── Una columna nueva no puede quedar a medio conectar ───────────────────────
def test_toda_metrica_sabe_auditarse():
    """Cada columna de la tabla declara QUÉ cuentas listar y CÓMO titularse. Sin esto,
    agregar una columna dejaría una celda que se puede clickear y no abre nada."""
    assert set(P.METRICAS) == set(P._FILTRO_METRICA)
    assert set(P.METRICAS) == set(P._TITULO_METRICA)


def test_los_filtros_de_metrica_particionan_el_universo():
    """`con_aum` + `sin_aum` tienen que dar el universo completo — si los dos
    predicados no son complementarios, las dos columnas dejan de sumar `clientes`."""
    con = P._FILTRO_METRICA["con_aum"][0]
    sin = P._FILTRO_METRICA["sin_aum"][0]
    for aum in (-10.0, 0.0, 0.01, 1_000.0, None):
        fila = {"aum": aum, "arancel": 0.0, "n_boletos": 0}
        assert con(fila) != sin(fila), aum


# ── FILTRO DE OPERACIÓN ──────────────────────────────────────────────────────
# Lo que este bloque cuida es UNA cosa: que el filtro acote lo que se OPERÓ y
# nada más. Si algún día toca el universo o el AuM, el porcentaje deja de
# significar "qué parte de mi base usa este producto" y no significa nada —
# y no falla nada, simplemente muestra otro número.

class _Espia:
    """Reemplaza `_q` para capturar el SQL sin tocar la base. Devuelve vacío:
    alcanza para ver QUÉ se pregunta, que es lo que este test cuida."""
    def __init__(self):
        self.sqls: list[str] = []

    def __call__(self, sql, params=None):
        self.sqls.append(" ".join(sql.split()))
        return []

    def con(self, *frag):
        return [s for s in self.sqls if all(f in s for f in frag)]


def _espiar(monkeypatch):
    e = _Espia()
    monkeypatch.setattr(P, "_q", e)
    return e


def test_el_filtro_solo_entra_en_la_query_de_operaciones(monkeypatch):
    e = _espiar(monkeypatch)
    P.profundidad_clientes(desde="2025-07", hasta="2025-09", operacion=["caucion_tom_ap"])
    con_filtro = e.con("operacion = ANY")
    assert len(con_filtro) == 1, f"el filtro aparece en {len(con_filtro)} queries, tiene que ser 1"
    assert "FROM operaciones o" in con_filtro[0]


def test_el_universo_y_el_aum_no_saben_del_filtro(monkeypatch):
    e = _espiar(monkeypatch)
    P.profundidad_clientes(desde="2025-07", hasta="2025-09", operacion=["caucion_tom_ap"])
    universo = e.con("fecha_alta_legajo AS f")
    aum = e.con("FROM tenencia")
    assert universo and aum, "cambiaron las queries: este test dejó de mirar lo que creía"
    for s in universo + aum:
        assert "operacion" not in s, "el filtro se coló en el universo o en el AuM"


def test_las_opciones_no_se_filtran_a_si_mismas(monkeypatch):
    """El desplegable se puebla SIN el filtro puesto. Si lo llevara, al elegir
    'caución' quedaría una sola opción y no habría forma de volver."""
    e = _espiar(monkeypatch)
    P.profundidad_clientes(desde="2025-07", hasta="2025-09", operacion=["caucion_tom_ap"])
    opciones = e.con("GROUP BY o.operacion")
    assert len(opciones) == 1
    assert "operacion = ANY" not in opciones[0]


def test_el_modal_filtra_igual_que_la_tabla(monkeypatch):
    """Si el modal no aplicara el mismo filtro, abrirías una celda de 47 y
    saldrían 389 cuentas."""
    e = _espiar(monkeypatch)
    P.detalle_mes(mes="2025-07", metrica="activos", operacion=["caucion_tom_ap"])
    assert e.con("operacion = ANY"), "el modal ignoró el filtro"


def test_solo_se_filtran_las_metricas_de_operaciones():
    assert set(P.METRICAS_FILTRABLES) == {
        "activos", "ratio_actividad", "aranceles", "arancel_por_activo"}
    # Las de la base entera NO pueden estar acá.
    for m in ("clientes", "con_aum", "sin_aum", "aum"):
        assert m not in P.METRICAS_FILTRABLES
    assert set(P.METRICAS_FILTRABLES) < set(P.METRICAS)


def test_normalizacion_del_filtro():
    assert P._ops_lista(None) == []
    assert P._ops_lista([]) == []
    assert P._ops_lista("compra") == ["compra"]
    assert P._ops_lista(["__todos__", "todos", "todas"]) == []
    assert P._ops_lista(["", None, "compra"]) == ["compra"]
    # Duplicado: sin esto el encabezado diría "Compra + Compra".
    assert P._ops_lista(["compra", "compra", "venta"]) == ["compra", "venta"]


def test_sin_filtro_no_se_agrega_condicion():
    p: dict = {}
    assert P._ops_and([], p) == ""
    assert p == {}, "sin filtro no se puede ensuciar el diccionario de params"
    p2: dict = {}
    frag = P._ops_and(["compra"], p2, "o")
    assert frag == " AND o.operacion = ANY(%(ops_filtro)s)"
    assert p2["ops_filtro"] == ["compra"]


def test_el_encabezado_dice_que_cambio_de_sentido():
    """Con filtro, RATIO deja de ser actividad y pasa a ser penetración. Si el
    encabezado no lo dijera, alguien lee ese 5% como que se cayó el negocio."""
    sin = P._columnas([], [])
    assert sin["ratio_actividad"] == "Ratio activ." and sin["sufijo"] is None
    disp = [{"valor": "caucion_tom_ap", "label": "Caución tomadora"}]
    con = P._columnas(["caucion_tom_ap"], disp)
    assert con["activos"] == "Operaron caución tomadora"
    assert con["ratio_actividad"] == "% que operó caución tomadora"
    assert con["aranceles"] == "Aranceles de caución tomadora"
    # Con muchas no se arma un encabezado de tres renglones.
    assert P._columnas(["a", "b", "c"], [])["activos"] == "Operaron 3 operaciones"


def test_un_valor_sin_etiqueta_igual_se_puede_filtrar():
    assert P._op_label("caucion_tom_ap") == "Caución tomadora"
    assert P._op_label("futuro_dlr") == "Futuro dlr"      # fallback, no se esconde
