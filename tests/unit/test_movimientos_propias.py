"""La cartera PROPIA se vuelca entera — y no puede llevarse puesto al job de negocio.

Dos mitades:

  A. **El default de `aplicar_filtros` está congelado.** El parámetro nuevo existe
     para que `jobs/movimientos_propias` NO filtre; el job de comitentes depende
     de que sí. Un default en False sería un cambio invisible: la ingesta de
     negocio empezaría a tragarse bonificaciones, OTC y márgenes MtR sin que
     falle nada — cada mitad del sistema seguiría siendo coherente consigo misma.

  B. **La identidad de una línea.** Sin id en el feed, la PK es un hash del
     contenido. Si el hash no fuera determinista, cada corrida duplicaría el día
     entero; si colapsara dos líneas distintas, se perderían movimientos — que es
     exactamente lo que este job no puede hacer.
"""
from __future__ import annotations

import inspect
import json

from api.services import aunesa_negocio as svc
from jobs.movimientos_propias import _CRUDAS, _id_linea, _numerar, linea_a_doc

# Una compra en USD tal como la manda Aunesa: 3 líneas, mismo comprobante. La de
# ARS es la COMISIÓN — la que el consolidador de comitentes descarta.
_COMPRA_USD = [
    {"comprobante": "BOL 20260901", "cuenta": "[1839] ACA VALORES TRADING",
     "informacion": "Compra [AL30] 1.000,00@71,50 (USD 24hs)", "unidad": "[4711] AL30",
     "estado": "DIS", "lugar": "Local", "uso": "GRAL", "total": -1000.0,
     "operador": "jperez", "fecha": "01/09/2026"},
    {"comprobante": "BOL 20260901", "cuenta": "[1839] ACA VALORES TRADING",
     "informacion": "Compra [AL30] 1.000,00@71,50 (USD 24hs)", "unidad": "USD",
     "estado": "DIS", "lugar": "Local", "uso": "GRAL", "total": 715.0,
     "operador": "jperez", "fecha": "01/09/2026"},
    {"comprobante": "BOL 20260901", "cuenta": "[1839] ACA VALORES TRADING",
     "informacion": "Compra [AL30] 1.000,00@71,50 (USD 24hs)", "unidad": "ARS",
     "estado": "DIS", "lugar": "Local", "uso": "COMI", "total": 1234.5,
     "operador": "jperez", "fecha": "01/09/2026"},
]


def _docs(filas: list[dict]) -> list[dict]:
    movs = [svc._enriquecer(f) for f in filas]
    return _numerar([linea_a_doc(m, "2026-09-01", 1, None, 1234.0) for m in movs])


# ── A. el default no se toca ────────────────────────────────────────────────
def test_fetch_y_consolidar_filtra_por_default():
    """El job de comitentes llama sin el parámetro: tiene que seguir filtrando."""
    firma = inspect.signature(svc.fetch_y_consolidar)
    assert firma.parameters["aplicar_filtros"].default is True


def test_el_job_de_negocio_no_pide_el_modo_sin_filtros():
    """Congela el call-site: si alguien le pasa aplicar_filtros=False al job de
    comitentes, la ingesta de negocio deja de excluir y nada falla."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "jobs" / "negocio_movimientos.py"
    assert "aplicar_filtros" not in src.read_text(encoding="utf-8")


def test_el_job_de_propias_si_lo_pide():
    """Y el nuestro tiene que pedirlo explícito: sin esto, volcaría filtrado."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "jobs"
           / "movimientos_propias.py").read_text(encoding="utf-8")
    assert "aplicar_filtros=False" in src


# ── B. la identidad de la línea ─────────────────────────────────────────────
def test_el_hash_es_determinista():
    """Dos corridas de la misma línea dan el mismo id → el upsert es un no-op.
    Si no, cada media hora se duplicaría el día entero."""
    assert _id_linea(_COMPRA_USD[0]) == _id_linea(dict(_COMPRA_USD[0]))


def test_el_hash_no_depende_del_orden_de_las_claves():
    """Un JSON con las claves en otro orden es la MISMA línea."""
    al_reves = dict(reversed(list(_COMPRA_USD[0].items())))
    assert _id_linea(al_reves) == _id_linea(_COMPRA_USD[0])


def test_lineas_distintas_dan_hashes_distintos():
    """Las 3 líneas del mismo comprobante son 3 filas, no 1."""
    assert len({_id_linea(f) for f in _COMPRA_USD}) == 3


def test_lineas_identicas_no_se_pisan():
    """Dos líneas byte-idénticas del mismo día conviven: `ocurrencia` las separa.
    Colapsarlas sería perder un movimiento, que es lo único que este job no puede."""
    docs = _docs([_COMPRA_USD[1], dict(_COMPRA_USD[1])])
    assert [d["ocurrencia"] for d in docs] == [1, 2]
    assert len({(d["id_linea"], d["ocurrencia"]) for d in docs}) == 2


# ── C. lo que el consolidador perdía, acá está ──────────────────────────────
def test_la_comision_en_ars_sobrevive():
    """El motivo de todo el job: el consolidador de comitentes se queda con la
    línea USD y descarta la de ARS. Acá tienen que estar las tres."""
    docs = _docs(_COMPRA_USD)
    assert len(docs) == 3
    comi = [d for d in docs if d["uso"] == "COMI"]
    assert len(comi) == 1
    assert comi[0]["total"] == 1234.5
    assert comi[0]["moneda"] == "ARS"


def test_el_categorizador_es_el_de_siempre():
    """No se reimplementa: las 3 líneas de una compra se categorizan `compra`."""
    assert {d["categoria"] for d in _docs(_COMPRA_USD)} == {"compra"}


def test_el_signo_es_perspectiva_cliente():
    """`total` queda como lo manda Aunesa (broker) e `importe` invertido, igual
    que en negocio_movimientos. Guardar los dos es lo que hace auditable el signo."""
    docs = _docs(_COMPRA_USD)
    titulo = next(d for d in docs if d["unidad"] == "[4711] AL30")
    assert titulo["total"] == -1000.0 and titulo["importe"] == 1000.0


def test_la_moneda_sale_de_la_unidad_en_lineas_de_dinero():
    docs = _docs(_COMPRA_USD)
    assert next(d for d in docs if d["unidad"] == "USD")["moneda"] == "USD"
    # En una línea de TÍTULO no hay moneda propia → la del texto del boleto.
    assert next(d for d in docs if d["unidad"] == "[4711] AL30")["moneda"] == "USD"


def test_el_operador_llega_a_la_fila():
    """Campo que el pipeline de comitentes ignora por completo: en la propia,
    QUIÉN hizo el movimiento es la mitad del análisis."""
    assert {d["operador"] for d in _docs(_COMPRA_USD)} == {"jperez"}


def test_raw_guarda_las_diez_claves_sin_los_derivados():
    """La red de seguridad: si Aunesa suma una clave, el dato ya está guardado
    aunque la columna no exista. Y los `_derivados` del enricher NO entran."""
    raw = json.loads(_docs(_COMPRA_USD)[0]["raw"])
    assert set(raw) == set(_COMPRA_USD[0])
    assert not [k for k in raw if k.startswith("_")]


def test_todas_las_crudas_viajan_a_la_fila():
    doc = _docs(_COMPRA_USD)[0]
    assert all(k in doc for k in _CRUDAS)


# ── D. sin filtros de verdad ────────────────────────────────────────────────
def test_lo_que_el_filtro_de_comitentes_mataria_entra_igual():
    """Una caución OTC y una bonificación: las dos mueren en la ingesta de
    negocio (EXCLUIR_SUBSTRINGS / EXCLUIR_INFORMACION_CONTAINS) y acá tienen
    que entrar — «hay muchas cosas administrativas que son relevantes»."""
    filas = [
        {**_COMPRA_USD[0], "cuenta": "[1839] OTC COOPERATIVA", "unidad": "ARS",
         "informacion": "Caución tomadora ARS 100.000,00@6% (ARS 7 días) (Apertura)"},
        {**_COMPRA_USD[0], "unidad": "ARS", "informacion": "Bonificación de arancel"},
    ]
    assert all(svc._excluir(f) for f in filas), "el filtro de comitentes las mata"
    assert len(_docs(filas)) == 2, "acá tienen que entrar las dos"
