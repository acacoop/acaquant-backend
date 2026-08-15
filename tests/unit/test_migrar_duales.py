"""Duales: de `ajuste='dual'` a dos patas (`ajuste` + `ajuste_alt`).

Lo que se congela no es "que el script ande": es que **no se le invente una pata a
un bono**. La pata primaria tiene que salir de un dato que la mesa ya escribió (la
curva donde lo tenía archivado) y de ningún otro lado. Un dual con la pata mal
puesta aparece en la tabla equivocada y nadie lo nota: los dos números existen y
cierran por separado.
"""
from __future__ import annotations

from scripts.migrar_duales import planificar


def _f(ticker, curva):
    return {"ticker": ticker, "curva": curva, "emisor": "X",
            "emisor_tipo": "soberano", "moneda_eje": "ARS",
            "ajuste": "dual", "ajuste_alt": None}


def test_la_pata_primaria_sale_de_donde_la_mesa_lo_archivo():
    """Los 5 duales en `curva='cer'` y los 3 en `'tamar'` YA traen una de sus dos
    patas escrita. No es una inferencia: es la clasificación de la mesa."""
    migrables, sin_pata = planificar([_f("TZXD7", "cer"), _f("TTM26", "tamar")])
    assert sin_pata == []
    assert {m["ticker"]: m["ajuste"] for m in migrables} == \
           {"TZXD7": "cer", "TTM26": "tamar"}


def test_la_segunda_pata_NUNCA_se_completa_sola():
    """Es el invariante del script. 1816 tampoco la sabe (su curva se llama
    'Soberanos Duales' y no dice el par), así que adivinarla del prefijo del
    ticker sería una heurística sin medir sobre 8 filas."""
    migrables, _ = planificar([_f("TZXD7", "cer")])
    assert "ajuste_alt" not in migrables[0]


def test_una_curva_que_no_nombra_un_ajuste_no_se_toca():
    """Un dual archivado en `on_otros` no dice contra qué ajusta. Elegirle una
    pata a dedo es exactamente lo que este script no hace: se reporta y queda."""
    migrables, sin_pata = planificar([_f("XXXXO", "on_otros"), _f("YYYY", "soberanos")])
    assert migrables == []
    assert [f["ticker"] for f in sin_pata] == ["XXXXO", "YYYY"]


def test_dual_no_puede_ser_su_propia_pata():
    """`dual` dejó de ser un ajuste — es la CONSECUENCIA de tener dos. Si se
    colara como pata volveríamos al punto de partida con otro nombre."""
    migrables, sin_pata = planificar([_f("ZZZZ", "dual")])
    assert migrables == []
    assert len(sin_pata) == 1


def test_curva_vacia_o_nula_no_rompe():
    migrables, sin_pata = planificar([_f("A", None), _f("B", ""), _f("C", "  ")])
    assert migrables == []
    assert len(sin_pata) == 3


def test_es_insensible_a_mayusculas_y_espacios():
    """El master trae la curva tal como se cargó; no puede depender del tipeo."""
    migrables, _ = planificar([_f("A", " CER "), _f("B", "Tamar")])
    assert [m["ajuste"] for m in migrables] == ["cer", "tamar"]


def test_lista_vacia_no_rompe():
    assert planificar([]) == ([], [])
