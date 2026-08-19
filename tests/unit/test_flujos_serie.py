"""La agregación de DEPÓSITOS & EXTRACCIONES da lo MISMO que hacía el browser.

La tab bajaba el grano (día × cuenta × unidad) y filtraba/agrupaba en cliente:
20.559 filas y 2.512 KB por apertura. Al mover ese cálculo a
`cashflow_sql.flujos_serie`, el riesgo no es que explote — es que devuelva
números DISTINTOS sin dar ningún error, y nadie lo note.

Estos tests fijan la semántica que tenía el front: qué cuenta como entrada y
salida, cómo se agrupa a mes, y sobre todo los cuatro filtros de accionistas,
que son la parte con criterio (la clave es la cuenta COMPLETA `[N] NOMBRE`, y
'cooperativa' es cualquier cuenta con 'coop' en el nombre que NO sea accionista).
"""
import pytest

from api.cache import invalidate
from api.services import cashflow_sql as cf

# Grano de ejemplo: dos días, tres cuentas, dos monedas.
_FILAS = [
    {"dia": "2026-01-10", "cuenta": "[1] ACCIONISTA SA", "unidad": "ARS",
     "entradas": 100.0, "salidas": -30.0, "n": 2},
    {"dia": "2026-01-10", "cuenta": "[2] COOP AGRICOLA", "unidad": "ARS",
     "entradas": 50.0, "salidas": 0.0, "n": 1},
    {"dia": "2026-02-05", "cuenta": "[3] CLIENTE COMUN", "unidad": "USD",
     "entradas": 10.0, "salidas": -4.0, "n": 1},
    {"dia": "2026-02-05", "cuenta": "[1] ACCIONISTA SA", "unidad": "ARS",
     "entradas": 0.0, "salidas": -20.0, "n": 1},
]
_ACC = {"[1] ACCIONISTA SA": "GRUPO A"}


@pytest.fixture(autouse=True)
def _sin_base(monkeypatch):
    """Ni base ni cache: se prueba la AGREGACIÓN, no el SQL."""
    monkeypatch.setattr(cf, "flujos_resumen",
                        lambda **kw: {"filas": list(_FILAS)})
    monkeypatch.setattr(cf, "_accionistas_map", lambda: dict(_ACC))
    invalidate("flujos_serie")
    yield
    invalidate("flujos_serie")


def _serie(**kw):
    return cf.flujos_serie(**kw)


def test_neto_por_dia_y_moneda():
    """Cada barra es entradas + salidas (las salidas ya vienen negativas)."""
    r = _serie()
    por_dia = {f["periodo"]: f for f in r["serie"]}
    assert por_dia["2026-01-10"]["ARS"] == 120.0   # (100-30) + 50
    assert por_dia["2026-02-05"]["ARS"] == -20.0
    assert por_dia["2026-02-05"]["USD"] == 6.0     # 10-4


def test_mensual_agrupa_por_yyyy_mm():
    r = _serie(agg="MENSUAL")
    assert [f["periodo"] for f in r["serie"]] == ["2026-01", "2026-02"]


def test_totales_separan_entradas_de_salidas():
    """El encabezado muestra los tres números por separado, no solo el neto."""
    t = _serie()["totales"]
    assert t["ARS"]["entradas"] == 150.0
    assert t["ARS"]["salidas"] == -50.0
    assert t["USD"]["entradas"] == 10.0


def test_filtro_solo_accionistas_agrupa_por_grupo():
    r = _serie(filtro="solo_accionistas")
    assert r["opciones"] == ["GRUPO A"]
    assert r["totales"]["ARS"]["entradas"] == 100.0   # solo la cuenta [1]
    assert r["totales"]["USD"]["entradas"] == 0.0


def test_filtro_sin_accionistas_excluye_a_los_accionistas():
    r = _serie(filtro="sin_accionistas")
    assert "[1] ACCIONISTA SA" not in r["opciones"]
    assert r["totales"]["ARS"]["entradas"] == 50.0    # solo la coop


def test_cooperativa_es_por_nombre_y_nunca_un_accionista():
    """'coop' en el nombre — pero un accionista que se llamara coop NO entra."""
    r = _serie(filtro="solo_cooperativas")
    assert r["opciones"] == ["[2] COOP AGRICOLA"]
    assert r["totales"]["ARS"]["entradas"] == 50.0


def test_coop_tiene_que_arrancar_palabra():
    """La regex del front era `/\\bcoop/i`. Sin el \\b, 'AGROCOOP' se colaría
    como cooperativa y la cuenta cambiaría de categoría sin que nadie lo note."""
    assert cf._COOP_RE.search("[2] COOP AGRICOLA")
    assert cf._COOP_RE.search("[9] LA COOPERATIVA")
    assert not cf._COOP_RE.search("[8] AGROCOOP SA")


def test_seleccion_acota_a_una_cuenta():
    r = _serie(seleccion="[3] CLIENTE COMUN")
    assert r["totales"]["USD"]["entradas"] == 10.0
    assert r["totales"]["ARS"]["entradas"] == 0.0


def test_opciones_no_se_recortan_con_la_seleccion():
    """Si se recortaran, elegir una cuenta dejaría el desplegable con una sola
    opción y no habría forma de volver a otra."""
    r = _serie(seleccion="[3] CLIENTE COMUN")
    assert len(r["opciones"]) == 3


def test_bounds_son_el_primer_y_ultimo_dia_con_movimientos():
    b = _serie()["bounds"]
    assert b["min"] == "2026-01-10"
    assert b["max"] == "2026-02-05"


def test_el_recorte_del_calendario_no_encoge_opciones_ni_bounds():
    """El calendario y el desplegable se calculan sobre la VENTANA leída, no
    sobre el recorte: si no, achicar el rango los iría vaciando y no habría
    forma de volver a ampliarlo."""
    r = _serie(desde="2026-02-01", hasta="2026-02-28")
    assert r["bounds"] == {"min": "2026-01-10", "max": "2026-02-05"}
    assert len(r["opciones"]) == 3
    # pero lo graficado SÍ se recorta
    assert [f["periodo"] for f in r["serie"]] == ["2026-02-05"]
    assert r["totales"]["ARS"]["entradas"] == 0.0
    assert r["totales"]["USD"]["entradas"] == 10.0


def test_filtro_desconocido_no_rompe_y_cae_a_todas():
    """El router ya valida, pero el service es público: no puede explotar."""
    assert _serie(filtro="cualquiera")["filtro"] == "todas"
