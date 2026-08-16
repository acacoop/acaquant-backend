"""Duales: de `ajuste='dual'` a dos patas (`ajuste` + `ajuste_alt`).

Lo que se congela no es "que el script ande": es que **no se le invente una pata a
un bono**. Un dual con la pata mal puesta aparece en la tabla equivocada y nadie lo
nota — los dos números existen y cierran por separado.

El caso central lo encontró la medición, no el diseño: en TMVE8/TTD26/TTS26 las dos
fuentes (`curva` y `tasa_referencia`) dicen LO MISMO. Escribir eso daría un bono
"dual consigo mismo", que no significa nada y **se ve perfectamente normal en
pantalla**. Ese es el test que importa.
"""
from __future__ import annotations

from scripts.migrar_duales import PATAS_MANUALES, eje_de_tasa, planificar


def _f(ticker, curva, tasa=None):
    return {"ticker": ticker, "curva": curva, "emisor": "X",
            "emisor_tipo": "soberano", "moneda_eje": "ARS",
            "ajuste": "dual", "ajuste_alt": None, "tasa_referencia": tasa}


def test_las_dos_patas_cuando_las_fuentes_diCEN_cosas_distintas():
    """TXMD8 real: archivado en `cer`, con `tasa_referencia='TAMAR'` en el blob."""
    migrables, bloqueados = planificar([_f("TXMD8", "cer", "TAMAR")])
    assert bloqueados == []
    assert migrables[0]["ajuste"] == "cer"
    assert migrables[0]["ajuste_alt"] == "tamar"
    assert migrables[0]["estado"] == "completo"


def test_un_dual_NO_puede_ser_dual_consigo_mismo():
    """EL test. TMVE8/TTD26/TTS26 tienen `curva='tamar'` Y `tasa_referencia='TAMAR'`.
    Escribir `ajuste=tamar, ajuste_alt=tamar` no significa nada — y el peligro es
    que sumaría bien igual: el bono aparecería una sola vez, en la tabla correcta,
    y nadie notaría que su segunda pata nunca se cargó."""
    migrables, bloqueados = planificar([_f("XXDUAL", "tamar", "TAMAR")])
    assert migrables == []
    assert bloqueados[0]["estado"] == "degenerado"


def test_sin_tasa_referencia_se_escribe_solo_la_primaria():
    """TXMJ0/TXMJ8/TXMJ9: la mesa nunca cargó `tasa_referencia`. Se guarda la pata
    conocida y la otra queda pendiente — el bono NO se cae de la pantalla."""
    migrables, bloqueados = planificar([_f("TXMJ0", "cer")])
    assert bloqueados == []
    assert migrables[0]["ajuste"] == "cer"
    assert migrables[0]["ajuste_alt"] is None
    assert migrables[0]["estado"] == "parcial"


def test_una_tasa_que_no_conocemos_se_reporta_no_se_normaliza():
    """Traducir con una TABLA y no a lo bruto es lo que hace que un valor nuevo
    aparezca en el reporte en vez de colarse mal escrito."""
    migrables, bloqueados = planificar([_f("XXXX", "cer", "TASA BADLAR PRIVADA 30D")])
    assert migrables == []
    assert bloqueados[0]["estado"] == "tasa_rara"


def test_una_curva_que_no_nombra_un_ajuste_no_se_toca():
    migrables, bloqueados = planificar([_f("XXXXO", "on_otros"), _f("YYYY", "soberanos")])
    assert migrables == []
    assert {b["estado"] for b in bloqueados} == {"sin_pata"}


def test_dual_no_puede_ser_su_propia_pata():
    """`dual` dejó de ser un ajuste — es la CONSECUENCIA de tener dos. Si se colara
    como pata volveríamos al punto de partida con otro nombre."""
    migrables, bloqueados = planificar([_f("ZZZZ", "dual")])
    assert migrables == []
    assert bloqueados[0]["estado"] == "sin_pata"


def test_curva_vacia_o_nula_no_rompe():
    migrables, bloqueados = planificar([_f("A", None), _f("B", ""), _f("C", "  ")])
    assert migrables == []
    assert len(bloqueados) == 3


def test_es_insensible_a_mayusculas_y_espacios():
    """El master trae la curva y la tasa tal como se cargaron a mano; nada de esto
    puede depender del tipeo."""
    migrables, _ = planificar([_f("A", " CER ", " tamar "), _f("B", "Tamar", "CER")])
    assert [(m["ajuste"], m["ajuste_alt"]) for m in migrables] == \
           [("cer", "tamar"), ("tamar", "cer")]


def test_traduccion_de_la_tasa():
    assert eje_de_tasa("TAMAR") == "tamar"
    assert eje_de_tasa("  cer ") == "cer"
    assert eje_de_tasa("") is None and eje_de_tasa(None) is None
    assert eje_de_tasa("lo que sea") is None


def test_lista_vacia_no_rompe():
    assert planificar([]) == ([], [])


def test_la_carga_de_la_mesa_gana_sobre_las_dos_fuentes_automaticas():
    """Los tres `degenerado` (TMVE8/TTD26/TTS26) no tienen la segunda pata en
    ninguna fuente: ni en la base, ni en 1816. La dijo la mesa el 2026-08-16 y por
    eso pisa a `curva` y a `tasa_referencia`, que para estos tres coinciden."""
    filas = [_f(tk, "tamar", "TAMAR") for tk in ("TTD26", "TTS26", "TMVE8")]
    migrables, bloqueados = planificar(filas)
    assert bloqueados == []
    assert {m["ticker"]: (m["ajuste"], m["ajuste_alt"]) for m in migrables} == {
        "TTD26": ("tamar", "fija"),
        "TTS26": ("tamar", "fija"),
        "TMVE8": ("tamar", "dolar_linked"),
    }
    assert {m["estado"] for m in migrables} == {"manual"}


def test_TMVE8_cruza_de_columna_y_eso_es_a_proposito():
    """Es el primer bono con una pata de cada LADO: TAMAR es ARS y DOLAR LINKED es
    USD, así que aparece en las dos tablas. Se congela porque obligó a que el front
    decida el lado de la tabla por `lado` y no por la moneda del bono — con
    `moneda` (que es ARS, y es correcto) le cambiaba las columnas a toda la tabla
    USD según qué fila cayera primera."""
    from core import curvas_ejes as ce
    aj, alt = PATAS_MANUALES["TMVE8"]
    lados = {ce.LADO[p] for p in ce.pills(ce.Ejes("soberano", "ARS", aj, ajuste_alt=alt))}
    assert lados == {"ARS", "USD"}


def test_ninguna_pata_manual_es_degenerada():
    """El invariante que hace segura la carga a mano: un dual es dual porque sus
    dos patas son DISTINTAS. Si alguien tipea la misma dos veces, salta acá y no
    en producción, donde el bono se vería en una sola tabla sumando bien."""
    from core.curvas_ejes import AJUSTES
    for tk, (a1, a2) in PATAS_MANUALES.items():
        assert a1 != a2, tk
        assert a1 in AJUSTES and a2 in AJUSTES, tk
