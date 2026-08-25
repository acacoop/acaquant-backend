"""tests/unit/test_ap5_rankings.py — el ranking del reporte de la mesa.

`rankings()` quedó PURO (recibe las filas del acumulado, no toca la base) el
2026-08-25, justo para poder congelarlo acá. Cada test corresponde a una forma
de equivocarse que NO grita: el ranking sale ordenado, plausible, y mal.
"""
from __future__ import annotations

from api.services.ap5_posiciones import lado_de_grupo, normalizar_grupo, rankings


def _f(**kw):
    base = {
        "familia": "agro", "grupo": "COOPERATIVAS", "cuenta": "1",
        "nombre": "COOP", "moneda": "Dólar MtR", "acumulado": 100.0,
        "diaria": 1.0, "cargado": True, "arrastre": 0.0, "fecha_arrastre": None,
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


def test_cada_tab_y_cada_lado_es_un_bloque_propio():
    """Son plata distinta y monedas distintas: no se mezclan nunca."""
    r = rankings([
        _f(familia="agro", grupo="COOPERATIVAS"),
        _f(familia="agro", grupo="MUNDO ACA"),
        _f(familia="dolar", grupo="COOPERATIVAS"),
        _f(familia="dolar", grupo="MUNDO ACA"),
    ])
    assert {(x["tab"], x["grupo"]) for x in r} == {
        ("agro", "COOPERATIVAS"), ("agro", "MUNDO ACA"),
        ("dolar", "COOPERATIVAS"), ("dolar", "MUNDO ACA"),
    }
    assert all(x["cuentas"] == 1 for x in r)


# ── Los DOS bugs del 2026-08-25, congelados ────────────────────────────────
def test_la_familia_OTROS_no_entra_en_ninguna_tab():
    """AGRO es trigo, soja y maíz — lo que se mide en TONELADAS.

    El WTI (unidad `Bl`) se plegaba adentro de AGRO y estaba mal: un barril no
    es una tonelada y sumarlo daba un ranking que parece bien. Queda fuera de
    las dos tabs y se declara en `faltantes` — no desaparece, pero tampoco
    infla toneladas que no lo son.
    """
    r = rankings([
        _f(familia="agro", grupo="COOPERATIVAS", cuenta="A", acumulado=10.0),
        _f(familia="otros", grupo="COOPERATIVAS", cuenta="B", acumulado=999.0),
    ])
    assert len(r) == 1
    assert r[0]["tab"] == "agro"
    assert r[0]["cuentas"] == 1, "el WTI se coló en el ranking de agro"
    assert [i["cuenta"] for i in r[0]["positivos"]] == ["A"]


def test_un_grupo_abre_UN_solo_panel_por_tab():
    """BUG REAL: se agrupaba por (familia, grupo), así que un grupo con
    posiciones en dos familias de la misma tab abría DOS paneles con el mismo
    título, uno debajo del otro. No fallaba nada: dibujaba de más."""
    r = rankings([
        _f(familia="agro", grupo="COOPERATIVAS", cuenta="A", acumulado=10.0),
        _f(familia="agro", grupo="COOPERATIVAS", cuenta="B", acumulado=20.0),
        _f(familia="dolar", grupo="COOPERATIVAS", cuenta="C", acumulado=30.0),
    ])
    assert len(r) == 2, "el mismo grupo abrió más de un panel por tab"
    assert {b["tab"] for b in r} == {"agro", "dolar"}


def test_el_grupo_se_compara_NORMALIZADO_no_por_el_string_crudo():
    """BUG REAL (REGLA #9): la base dice `COOPERATIVAS`, el código buscaba
    `Cooperativas`. No matcheaba, TODO caía en "sin clasificar" — con los
    rankings correctos y el título equivocado."""
    r = rankings([
        _f(grupo="COOPERATIVAS", cuenta="A", acumulado=10.0),
        _f(grupo="Cooperativas", cuenta="B", acumulado=20.0),
        _f(grupo=" cooperativas ", cuenta="C", acumulado=30.0),
    ])
    assert len(r) == 1, "la misma cooperativa escrita distinto abrió N paneles"
    assert r[0]["lado"] == "izq"
    assert r[0]["cuentas"] == 3


def test_el_LADO_lo_decide_el_backend():
    """Para que la vista no compare strings — que es donde se rompió."""
    assert lado_de_grupo("COOPERATIVAS") == "izq"
    assert lado_de_grupo("Mundo Aca") == "der"
    assert lado_de_grupo("(sin grupo)") == "otro"
    assert normalizar_grupo(" Coöperativas ") == "COOPERATIVAS"


def test_el_orden_es_izquierda_derecha_y_lo_no_clasificado_ULTIMO():
    """El orden del mail. Lo fija el backend, no el navegador."""
    r = rankings([
        _f(grupo="(sin grupo)", cuenta="C"),
        _f(grupo="MUNDO ACA", cuenta="B"),
        _f(grupo="COOPERATIVAS", cuenta="A"),
    ])
    assert [x["lado"] for x in r] == ["izq", "der", "otro"]


def test_la_etiqueta_que_se_muestra_es_la_de_la_BASE():
    """Se normaliza para COMPARAR, no para dibujar: si la mesa lo escribió
    `Mundo Aca`, la pantalla dice `Mundo Aca`."""
    r = rankings([_f(grupo="Mundo Aca")])
    assert r[0]["grupo"] == "Mundo Aca"
    assert r[0]["lado"] == "der"


def test_el_acumulado_en_CERO_no_entra_a_ningun_lado():
    """Ni a favor ni en contra: una cuenta en cero no es una posición ganadora
    de $0 — no tiene nada que hacer en un top."""
    r = rankings([_f(cuenta="A", acumulado=0.0), _f(cuenta="B", acumulado=5.0)])[0]
    assert r["cuentas"] == 1
    assert [i["cuenta"] for i in r["positivos"]] == ["B"]


def test_la_cuenta_SIN_ARRASTRE_CARGADO_se_cuenta():
    """Un acumulado sin arrastre cargado está INCOMPLETO, y en un ranking eso
    importa el doble: la cuenta puede estar en el puesto equivocado.

    `cargado` es que una PERSONA selló `actualizado`. Una fila con los dos
    importes en 0 que dejó el sembrador NO cuenta como cargada: un cero que
    nadie escribió se lee igual que uno verificado, y esto se imprime."""
    r = rankings([
        _f(cuenta="A", acumulado=50.0, cargado=True),
        _f(cuenta="B", acumulado=90.0, cargado=False),
    ])[0]
    assert r["sin_cargar"] == 1


def test_sin_filas_no_revienta():
    assert rankings([]) == []
