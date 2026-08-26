"""Tests de ANÁLISIS CUANTITATIVO (api/services/cuantitativo_sql.py).

Lógica pura: cortes, ventanas de meses y las reglas que deciden qué entra en cada
lista. Nada de esto toca la base.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import cuantitativo_sql as C


# ── Los cortes: ninguno viene fijo, y ninguno puede quedar en cualquier cosa ──
def test_todos_los_cortes_declaran_rango_y_para_que_sirven():
    """Un corte sin rango se puede poner en cualquier valor y la lista deja de
    significar algo; uno sin `que` no se puede dibujar al lado del campo."""
    for nombre, c in C.CORTES.items():
        assert {"def", "min", "max", "que"} <= set(c), nombre
        assert c["min"] <= c["def"] <= c["max"], nombre
        assert c["que"].strip(), nombre


def test_el_piso_de_aum_arranca_en_10_millones():
    assert C.CORTES["piso_aum"]["def"] == 10_000_000


@pytest.mark.parametrize("nombre", list(C.CORTES))
def test_un_corte_fuera_de_rango_vuelve_al_default(nombre):
    """El backend NO confía en lo que le mandan: un piso negativo o un múltiplo 0
    darían una lista sin sentido, y no fallaría nada."""
    c = C.CORTES[nombre]
    assert C._corte(nombre, c["min"] - 1) == float(c["def"])
    assert C._corte(nombre, c["max"] + 1) == float(c["def"])
    assert C._corte(nombre, None) == float(c["def"])
    assert C._corte(nombre, "no es un número") == float(c["def"])
    assert C._corte(nombre, c["min"]) == float(c["min"])


# ── La ventana hacia atrás ───────────────────────────────────────────────────
def test_menos_meses_cae_en_fin_de_mes():
    assert C._menos_meses(date(2026, 7, 31), 3) == date(2026, 4, 30)
    assert C._menos_meses(date(2026, 1, 31), 1) == date(2025, 12, 31)
    assert C._menos_meses(date(2026, 3, 31), 1) == date(2026, 2, 28)   # feb corto
    assert C._menos_meses(date(2024, 3, 31), 1) == date(2024, 2, 29)   # bisiesto
    assert C._menos_meses(date(2026, 7, 31), 12) == date(2025, 7, 31)


def test_la_ventana_de_12_meses_incluye_el_mes_en_curso():
    """`_menos_meses(fin, 11)` + día 1 = el primero del mes 12 meses atrás
    contando el actual. Si fuera 12, la ventana sería de 13 meses."""
    assert C._menos_meses(date(2026, 7, 31), 11).replace(day=1) == date(2025, 8, 1)


# ── Hasta dónde se cuenta el tiempo ──────────────────────────────────────────
def test_los_dias_sin_operar_nunca_se_cuentan_contra_una_fecha_futura(monkeypatch):
    """En el mes EN CURSO `fin` es el último día del mes, que todavía no llegó.

    Sin el tope, un cliente que operó anteayer figuraba "lleva 20 días sin
    operar" y entraba a SE ESTÁN APAGANDO por tiempo que no pasó — y el número
    se agrandaba solo a medida que avanzaba el mes.
    """
    monkeypatch.setattr(C, "_hoy_art", lambda: date(2026, 8, 26))
    assert C._hasta(date(2026, 8, 31)) == date(2026, 8, 26)      # mes en curso → hoy


def test_un_mes_ya_cerrado_se_cuenta_contra_su_propio_fin(monkeypatch):
    """La lista de julio tiene que decir lo que se veía el 31 de julio, no lo que
    se ve hoy: si no, mirar un mes viejo daría un número distinto cada día."""
    monkeypatch.setattr(C, "_hoy_art", lambda: date(2026, 8, 26))
    assert C._hasta(date(2026, 7, 31)) == date(2026, 7, 31)


# ── La mediana, que es lo que reemplaza al promedio ─────────────────────────
def test_mediana():
    assert C._mediana([]) == 0.0
    assert C._mediana([5]) == 5
    assert C._mediana([1, 2, 3]) == 2
    assert C._mediana([1, 2, 3, 4]) == 2.5


def test_el_promedio_y_la_mediana_viajan_JUNTOS():
    """Sobre una Pareto, el promedio describe a un cliente que no existe. Mostrar
    los dos pegados es lo que enseña que hay una ballena adentro sin explicar qué
    es una distribución sesgada."""
    # 1 ballena de 6.000.000 + 99 clientes de 12.000 → total 7.188.000.
    # El del medio deja 12.000; el "promedio" da 71.880 — casi 6 veces más, y no
    # hay UN SOLO cliente cerca de esa cifra.
    ar = {"ballena": 6_000_000.0, **{f"c{i}": 12_000.0 for i in range(99)}}
    ctx = C._contexto(ar, n_clientes=200, factor=None)
    assert ctx["mediana"] == 12_000.0
    assert ctx["promedio"] == 71_880.0
    assert ctx["promedio"] > 5 * ctx["mediana"]      # la diferencia ES el mensaje
    # Y la ballena sola es el 83% del total: por eso también va el top-10.
    assert ctx["top10_pct"] > 80
    assert ctx["n_operaron"] == 100
    assert ctx["n_clientes"] == 200                    # el universo NO es "los que operaron"


def test_cuantos_hacen_el_80_por_ciento():
    # 6000 + 4000 + 3000 = 13000 sobre 13600 → los tres primeros pasan el 80%.
    ctx = C._contexto({"a": 6000.0, "b": 4000.0, "c": 3000.0, "d": 500.0, "e": 100.0},
                      n_clientes=10, factor=None)
    assert ctx["cuantos_80"] == 3
    assert ctx["arancel_total"] == 13600.0


def test_sin_aranceles_no_se_inventa_un_corte():
    ctx = C._contexto({}, n_clientes=5, factor=None)
    assert ctx["cuantos_80"] == 0 and ctx["n_operaron"] == 0
    assert ctx["promedio"] == 0.0 and ctx["top10_pct"] is None


# ── Los avisos: un cero que es "no pude mirar" es peor que un número feo ────
def test_sin_foto_de_tenencia_se_avisa_en_vez_de_mostrar_vacio():
    avisos = C._avisos({"snapshot_hoy": None, "snapshot_antes": None,
                        "fin_antes": "2026-04-30"}, date(2026, 7, 31), 10)
    assert any("no puede compararse" in a for a in avisos)


def test_la_foto_corrida_se_avisa_con_los_dias():
    avisos = C._avisos({"snapshot_hoy": "2026-07-22", "snapshot_antes": "2026-04-30",
                        "fin_antes": "2026-04-30"}, date(2026, 7, 31), 10)
    assert any("2026-07-22" in a and "9 días" in a for a in avisos)


def test_siempre_se_avisa_que_los_contadores_no_se_suman():
    """La misma cuenta puede estar en las tres listas — y ése es justo el cliente
    al que hay que llamar primero, no un error de conteo."""
    avisos = C._avisos({"snapshot_hoy": "2026-07-31", "snapshot_antes": "2026-04-30",
                        "fin_antes": "2026-04-30"}, date(2026, 7, 31), 10)
    assert any("NO se suman" in a for a in avisos)


# ── El tope de filas nunca puede mover un contador ──────────────────────────
def test_hay_tope_de_filas_y_es_sano():
    assert 1 <= C.LIMITE_DEF <= C.LIMITE_MAX
