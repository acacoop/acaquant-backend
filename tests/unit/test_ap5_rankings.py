"""tests/unit/test_ap5_rankings.py — el ranking del reporte de la mesa.

`rankings()` quedó PURO (recibe las filas del acumulado, no toca la base) el
2026-08-25, justo para poder congelarlo acá. Cada test corresponde a una forma
de equivocarse que NO grita: el ranking sale ordenado, plausible, y mal.
"""
from __future__ import annotations

from api.services.ap5_posiciones import rankings


def _f(**kw):
    base = {
        "familia": "agro", "grupo": "Cooperativas", "cuenta": "1",
        "nombre": "COOP", "moneda": "Dólar MtR", "acumulado": 100.0,
        "diaria": 1.0, "semilla_cargada": True,
    }
    return {**base, **kw}


def test_ordena_por_ACUMULADO_y_no_por_la_diferencia_del_dia():
    """El error silencioso más caro de esta vista.

    Si ordenara por `diaria`, el ranking saldría igual de prolijo y mostraría
    otras cuentas. Acá la que más movió HOY es la que menos acumula.
    """
    r = rankings([
        _f(cuenta="A", nombre="MUCHO ACUM", acumulado=900.0, diaria=1.0),
        _f(cuenta="B", nombre="MUCHO HOY", acumulado=100.0, diaria=999.0),
    ])[0]
    assert [i["nombre"] for i in r["positivos"]] == ["MUCHO ACUM", "MUCHO HOY"]


def test_los_negativos_van_del_PEOR_al_menos_malo():
    r = rankings([
        _f(cuenta="A", nombre="LEVE", acumulado=-10.0),
        _f(cuenta="B", nombre="GRAVE", acumulado=-900.0),
    ])[0]
    assert [i["nombre"] for i in r["negativos"]] == ["GRAVE", "LEVE"]


def test_el_total_es_de_TODAS_las_cuentas_no_solo_del_top():
    """El ranking recorta la LISTA, no la suma. Si el total saliera del top,
    mostrar 10 filas cambiaría el número y nadie lo notaría."""
    filas = [_f(cuenta=str(i), acumulado=float(i + 1)) for i in range(25)]
    r = rankings(filas)[0]
    assert len(r["positivos"]) == 10
    assert r["total_positivo"] == sum(float(i + 1) for i in range(25))
    assert r["cuentas"] == 25


def test_las_familias_y_los_grupos_NO_se_mezclan():
    """Cada tab (agro / dólar) y cada lado (Cooperativas / MUNDO ACA) es un
    bloque propio: son plata distinta y monedas distintas."""
    r = rankings([
        _f(familia="agro", grupo="Cooperativas"),
        _f(familia="agro", grupo="MUNDO ACA"),
        _f(familia="dolar", grupo="Cooperativas"),
        _f(familia="dolar", grupo="MUNDO ACA"),
    ])
    assert {(x["familia"], x["grupo"]) for x in r} == {
        ("agro", "Cooperativas"), ("agro", "MUNDO ACA"),
        ("dolar", "Cooperativas"), ("dolar", "MUNDO ACA"),
    }
    assert all(x["cuentas"] == 1 for x in r)


def test_el_acumulado_en_CERO_no_entra_a_ningun_lado():
    """Ni a favor ni en contra: una cuenta en cero no es una posición ganadora
    de $0 — no tiene nada que hacer en un top."""
    r = rankings([_f(cuenta="A", acumulado=0.0), _f(cuenta="B", acumulado=5.0)])[0]
    assert r["cuentas"] == 1
    assert [i["cuenta"] for i in r["positivos"]] == ["B"]


def test_cuenta_SIN_SEMILLA_se_cuenta_y_viaja_marcada():
    """Un acumulado sin semilla está INCOMPLETO, y en un ranking eso importa el
    doble: la cuenta puede estar en el puesto equivocado. Tiene que poder
    decirlo, no quedar indistinguible de una completa."""
    r = rankings([
        _f(cuenta="A", acumulado=50.0, semilla_cargada=True),
        _f(cuenta="B", acumulado=90.0, semilla_cargada=False),
    ])[0]
    assert r["sin_semilla"] == 1
    assert r["positivos"][0]["semilla_cargada"] is False


def test_sin_filas_no_revienta():
    assert rankings([]) == []
