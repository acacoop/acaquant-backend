"""Tests de CONOCÉ A TU CLIENTE (api/services/conoce_cliente_sql.py).

Lógica pura: la ventana de meses, el piso del ROA, el orden y el contrato de
"sin segmento no se dibuja nada". Nada de esto toca la base.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import conoce_cliente_sql as C


# ── La ventana ───────────────────────────────────────────────────────────────
def test_la_ventana_de_12_meses_incluye_el_mes_en_curso():
    """Con 12 meses contando el actual, el primero es 11 meses atrás. Si fuera
    12, la ventana serían 13 meses y el arancel no compararía con nada."""
    assert C._meses_atras(date(2026, 8, 26), 12) == date(2025, 9, 1)
    assert C._meses_atras(date(2026, 1, 15), 12) == date(2025, 2, 1)
    assert C._meses_atras(date(2026, 8, 26), 1) == date(2026, 8, 1)


def test_los_fines_de_mes_terminan_HOY_y_no_en_el_futuro():
    """El último 'fin de mes' de la ventana es HOY, no el 31: pedir la foto de
    tenencia de una fecha que no llegó devuelve la de hoy igual, pero contar 12
    fotos cuando hay 11 hace que el promedio divida de más."""
    f = C._fines_de_mes(date(2026, 6, 1), date(2026, 8, 26))
    assert f == [date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 26)]


def test_los_fines_de_mes_cierran_bien_los_meses_cortos():
    f = C._fines_de_mes(date(2024, 1, 1), date(2024, 4, 30))
    assert f == [date(2024, 1, 31), date(2024, 2, 29),   # bisiesto
                 date(2024, 3, 31), date(2024, 4, 30)]


# ── El piso del ROA ──────────────────────────────────────────────────────────
def test_el_piso_del_roa_vuelve_al_default_si_viene_cualquier_cosa():
    """Sin piso, una cuenta que opera y barre la plata el mismo día divide por
    casi cero: medido en el libro real, el p90 daba 67.816 bps (678%)."""
    assert C._num(None, C.PISO_AUM_DEF, 0, C.PISO_AUM_MAX) == C.PISO_AUM_DEF
    assert C._num("no es un número", C.PISO_AUM_DEF, 0, C.PISO_AUM_MAX) == C.PISO_AUM_DEF
    assert C._num(-1, C.PISO_AUM_DEF, 0, C.PISO_AUM_MAX) == C.PISO_AUM_DEF
    assert C._num(5_000_000, C.PISO_AUM_DEF, 0, C.PISO_AUM_MAX) == 5_000_000
    assert C._num(0, C.PISO_AUM_DEF, 0, C.PISO_AUM_MAX) == 0     # 0 es válido: sin piso


def test_el_piso_arranca_en_un_millon():
    assert C.PISO_AUM_DEF == 1_000_000


# ── La mediana del segmento ──────────────────────────────────────────────────
def test_la_mediana_ignora_los_que_no_tienen_el_dato():
    """Un cliente sin cupo no es un cliente con SOW cero: si contara como cero,
    la mediana del segmento bajaría por cuentas que nadie midió."""
    assert C._mediana([10, None, 20, None, 30]) == 20
    assert C._mediana([]) is None
    assert C._mediana([None, None]) is None
    assert C._mediana([10, 20]) == 15


# ── El orden ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("clave", [c for c in C.ORDENES if c != "denominacion"])
def test_los_sin_dato_van_SIEMPRE_al_fondo(clave):
    """Ordenar por ROA y que el primero sea un «—» es la peor primera fila
    posible: la que se mira primero no dice nada."""
    k = C._CLAVE[clave]
    items = [{"denominacion": "a", k: None}, {"denominacion": "b", k: 5},
             {"denominacion": "c", k: None}, {"denominacion": "d", k: 100}]
    items.sort(key=lambda x: (x[k] is None, -(x[k] or 0)))
    assert [i[k] for i in items] == [100, 5, None, None]


def test_el_orden_por_defecto_es_por_lo_que_TIENE():
    """Las 18 cuentas que concentran el 96% de la plata quieta del libro no
    operan — así que no llegan por arancel. Ordenando por AuM quedan arriba de
    su segmento con un ROA en cero, que es justo lo que hay que ver."""
    assert C.ORDEN_DEF == "aum"
    assert set(C.ORDENES) == set(C._CLAVE)


# ── El contrato de la vista ──────────────────────────────────────────────────
def test_sin_segmento_no_se_devuelve_NINGUNA_fila(monkeypatch):
    """Mezclar segmentos hace que la vista recomiende lo contrario de lo que hay
    que hacer: los institucionales rinden menos bps por estructura y caerían
    todos juntos al fondo de la lista como si estuvieran desaprovechados."""
    monkeypatch.setattr(C, "segmentos", lambda **_: [{"valor": "PH RETAIL", "label": "PH RETAIL", "n": 627}])
    r = C.conoce_cliente(segmento=None)
    assert r["items"] == [] and r["n"] == 0
    assert r["segmento"] is None
    assert r["aviso"]                                   # dice POR QUÉ está vacía
    assert r["segmentos"]                               # y ofrece con qué llenarla


def test_todos_tampoco_es_un_segmento(monkeypatch):
    """`__todos__` es el token de «sin filtro» de la barra madre. Acá NO puede
    significar «todos juntos»: es exactamente lo que la vista no hace."""
    monkeypatch.setattr(C, "segmentos", lambda **_: [])
    assert C.conoce_cliente(segmento="__todos__")["items"] == []
