"""tests/unit/test_produccion.py — las invariantes del catálogo de PRODUCCIÓN.

Lo que se congela acá no es el cálculo (eso es SQL sobre prod), son las reglas que
hacen que el número sea explicable. Las tres fallas que esto previene:

  · que `total` y su desglose se contradigan,
  · que agregar una fuente la deje suelta (sin declarar de dónde sale ni cómo
    llega a una persona),
  · que un filtro por cuenta recorte a medias una fuente que no cuelga de cuentas.
"""
from __future__ import annotations

import pytest

from api.services import produccion


def _falsa(monto_por_op: dict[str, float]):
    """Una `fn` de fuente que ignora los args y devuelve lo que se le diga."""
    return lambda desde, hasta, moneda, mep_hoy, op_de, ids: dict(monto_por_op)


@pytest.fixture
def fuentes_falsas(monkeypatch):
    """Reemplaza el catálogo por dos fuentes controladas: una que cuelga de cuentas
    y otra que no. Así las invariantes se prueban sin tocar la base."""
    fs = (
        produccion.Fuente(id="mercado", etiqueta="A", concepto="c", atribucion="a",
                          tabla="t", fn=_falsa({"ana@x.com": 100.0, "beto@x.com": 50.0})),
        produccion.Fuente(id="mesa", etiqueta="B", concepto="c", atribucion="a",
                          tabla="t", fn=_falsa({"ana@x.com": 25.0, "caro@x.com": 7.0})),
    )
    monkeypatch.setattr(produccion, "FUENTES", fs)
    return fs


# ── INVARIANTE 1: total == suma de componentes ───────────────────────────────
def test_total_es_la_suma_de_los_componentes(fuentes_falsas):
    """Si el total pudiera calcularse por otro camino que el desglose, la vista
    podría mostrar un total que sus propias columnas no explican."""
    out = produccion.comisiones_por_operador(
        None, None, op_de={}, moneda="ARS", mep_hoy=None)
    for _op, comp in out.items():
        assert comp["total"] == pytest.approx(
            round(sum(comp[f.id] for f in produccion.FUENTES), 2))
    assert out["ana@x.com"]["total"] == pytest.approx(125.0)
    assert out["beto@x.com"]["total"] == pytest.approx(50.0)
    assert out["caro@x.com"]["total"] == pytest.approx(7.0)


def test_todo_operador_trae_todos_los_componentes(fuentes_falsas):
    """Un operador que solo aparece en una fuente igual publica la otra en 0 — si
    faltara la clave, el front tendría que adivinar entre «cero» y «no vino»."""
    out = produccion.comisiones_por_operador(
        None, None, op_de={}, moneda="ARS", mep_hoy=None)
    for comp in out.values():
        for f in produccion.FUENTES:
            assert f.id in comp
    assert out["beto@x.com"]["mesa"] == 0.0
    assert out["caro@x.com"]["mercado"] == 0.0


def test_vacio_tiene_las_mismas_claves_que_una_fila_real(fuentes_falsas):
    """`vacio()` lo usan los callers para la fila neutra: si se desincronizara del
    catálogo, un operador sin actividad traería otras columnas que uno con
    actividad y la tabla quedaría dispareja."""
    out = produccion.comisiones_por_operador(
        None, None, op_de={}, moneda="ARS", mep_hoy=None)
    assert set(produccion.vacio()) == set(out["ana@x.com"])
    assert all(v == 0.0 for v in produccion.vacio().values())


# ── INVARIANTE 4: lo que no se puede filtrar, se excluye entero ──────────────
def test_mesa_aplica_false_excluye_la_fuente_sin_cuenta(fuentes_falsas):
    """Con un filtro por cuenta activo, la intermediación sale ENTERA (no recortada
    a medias) y el total se recalcula sin ella."""
    out = produccion.comisiones_por_operador(
        None, None, op_de={}, moneda="ARS", mep_hoy=None, mesa_aplica=False)
    assert out["ana@x.com"]["mesa"] == 0.0
    assert out["ana@x.com"]["total"] == pytest.approx(100.0)
    # Un operador que SOLO tenía intermediación desaparece: no le quedó producción
    # filtrable, y mostrarlo en 0 diría «no produjo» en vez de «no se puede filtrar».
    assert "caro@x.com" not in out


def test_mesa_esta_declarada_como_fuente_sin_cuenta():
    """`SIN_CUENTA` es lo que hace que el caller sepa que esa fuente no se puede
    recortar por cuenta. Si alguien la sacara, las vistas filtradas empezarían a
    sumar intermediación sin filtrar y nadie lo notaría."""
    assert produccion.MESA in produccion.SIN_CUENTA
    assert produccion.MERCADO not in produccion.SIN_CUENTA


# ── LEY DE CONEXIÓN: una fuente nueva no puede quedar suelta ─────────────────
def test_toda_fuente_declara_su_procedencia():
    """Cada fuente tiene que decir QUÉ es, de DÓNDE sale y CÓMO llega al operador.
    Es lo que permite contestar «¿por qué este número es mío?» sin leer SQL."""
    assert produccion.FUENTES, "el catálogo no puede estar vacío"
    for f in produccion.FUENTES:
        assert f.id and f.etiqueta and f.concepto and f.atribucion and f.tabla, (
            f"la fuente {f.id!r} tiene declaraciones vacías")
        assert callable(f.fn)


def test_los_ids_de_fuente_son_unicos():
    """Dos fuentes con el mismo id se pisarían en el desglose y una de las dos
    dejaría de sumar, en silencio."""
    ids = [f.id for f in produccion.FUENTES]
    assert len(ids) == len(set(ids))


def test_el_catalogo_es_serializable_y_completo():
    """Lo consume la vista para explicar cada columna: si una fuente no viajara,
    su columna aparecería sin explicación."""
    cat = produccion.catalogo()
    assert len(cat) == len(produccion.FUENTES)
    for fila in cat:
        assert set(fila) == {"id", "etiqueta", "concepto", "atribucion", "tabla"}
        assert all(isinstance(v, str) and v for v in fila.values())


def test_total_no_es_un_id_de_fuente():
    """`total` es la clave del agregado: una fuente que se llamara así lo pisaría."""
    assert "total" not in {f.id for f in produccion.FUENTES}


# ── INFORME: la fila INTERMEDIACIÓN de ARANCELES POR SEGMENTO ────────────────
# Esa tabla agrupa por `nivel_1` de la CUENTA. La intermediación no cuelga de
# ninguna, así que va como fila propia — y tiene que SUMAR lo mismo que se le
# agregó a ARANC. TOTAL del comercial, o la tabla no explicaría su propio total.
from api.services.comercial_sql import _agregar_fila_intermediacion


def test_la_fila_de_intermediacion_suma_lo_mismo_que_se_imputo():
    """Si esta fila y lo que se sumó a ARANC. TOTAL se calcularan por caminos
    distintos, la tabla abierta no daría el número de la fila cerrada."""
    inter = {"ana@x.com": {"total": 100.0, "mes": 10.0},
             "beto@x.com": {"total": 25.5, "mes": 2.5}}
    segs: list[dict] = []
    _agregar_fila_intermediacion(segs, inter)
    assert len(segs) == 1
    assert segs[0]["ar_total"] == pytest.approx(125.5)
    assert segs[0]["ar_mes"] == pytest.approx(12.5)
    assert segs[0]["segmento"] == produccion.SEGMENTO_INTERMEDIACION
    assert segs[0]["es_intermediacion"] is True


def test_sin_intermediacion_no_se_agrega_una_fila_en_cero():
    """Una fila permanente en cero entrena a ignorar la fila — y el día que tenga
    plata, nadie la mira."""
    segs: list[dict] = []
    _agregar_fila_intermediacion(segs, {})
    _agregar_fila_intermediacion(segs, {"ana@x.com": {"total": 0.0, "mes": 0.0}})
    assert segs == []


def test_la_fila_de_intermediacion_no_inventa_volumen_ni_ticket():
    """No hay boletos detrás: un TICKET PROM. distinto de cero estaría afirmando
    operaciones que no existen, y un volumen inflaría el denominador de la tabla."""
    segs: list[dict] = []
    _agregar_fila_intermediacion(segs, {"ana@x.com": {"total": 100.0, "mes": 0.0}})
    assert segs[0]["vol_total"] == 0.0
    assert segs[0]["n_ops"] == 0
    assert segs[0]["ticket_promedio"] == 0.0


def test_la_fila_trae_las_mismas_claves_que_un_segmento_real():
    """La tabla las renderiza con el mismo componente: si faltara una clave, la
    fila rompería la columna en vez de mostrarse vacía."""
    segs: list[dict] = []
    _agregar_fila_intermediacion(segs, {"ana@x.com": {"total": 1.0, "mes": 1.0}})
    reales = {"segmento", "ar_total", "ar_mes", "vol_total", "n_ops", "n_cuentas",
              "ticket_promedio"}
    assert reales <= set(segs[0])
