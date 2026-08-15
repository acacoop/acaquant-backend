"""Tests de los EJES de renta fija (core/curvas_ejes.py) — puros, sin red ni base.

Congelan las decisiones del rediseño 2026-08-15 (docs/RENTA_FIJA.md §0). Lo que
se protege acá no es "que el código ande": es que **no se pierda una regla de la
vista más usada de la app** al migrar.
"""
from __future__ import annotations

from core import curvas_ejes as ce


def test_las_28_curvas_de_1816_estan_y_son_validas():
    """La tabla es la fuente: si alguien agrega una fila mal tipeada, salta acá."""
    assert len(ce.EJES_1816) == 28
    for nombre, e in ce.EJES_1816.items():
        assert e.emisor_tipo in ce.EMISORES, nombre
        assert e.moneda in ce.MONEDAS, nombre
        assert e.ajuste in ce.AJUSTES, nombre
        assert e.ley in (None, "local", "ny"), nombre


def test_bonar_vs_global_es_LEY_no_ajuste():
    """El error clásico: tratar Bonares/Globales como si fueran clases distintas.
    Son el mismo emisor/moneda/ajuste y cambia la ley — por eso comparten pill
    pero son curvas distintas (y su spread es lo que mira la mesa)."""
    bonar = ce.EJES_1816["Soberanos USD Bonares"]
    global_ = ce.EJES_1816["Soberanos USD Globales"]
    assert (bonar.emisor_tipo, bonar.moneda, bonar.ajuste) == \
           (global_.emisor_tipo, global_.moneda, global_.ajuste)
    assert (bonar.ley, global_.ley) == ("local", "ny")
    assert ce.pill(bonar) == ce.pill(global_) == "hard_dolar"


def test_inflacion_de_1816_es_el_MISMO_ajuste_que_cer():
    """1816 le dice 'Inflación' a corporativos/provinciales y 'CER' a soberanos.
    Si el modelo copiara el string, serían dos clases distintas."""
    assert ce.EJES_1816["Corporativos ARS Inflación"].ajuste == "cer"
    assert ce.EJES_1816["Provinciales ARS Inflación"].ajuste == "cer"
    assert ce.EJES_1816["Soberanos ARS CER"].ajuste == "cer"


def test_la_forma_del_titulo_NO_es_un_eje():
    """El eje bono/letra se ELIMINÓ (2026-08-15): 1816 solo lo afirma en 3 de sus
    28 curvas y la columna quedó vacía en los 221 bonos. Lo que se congela acá es
    que su desaparición no movió a nadie de pill — una letra CER es CER y un Bote
    es tasa fija, exactamente como antes."""
    assert not hasattr(ce.Ejes("soberano", "ARS", "cer"), "instrumento")
    assert ce.pill(ce.EJES_1816["Soberanos ARS Letras CER"]) == "cer"
    assert ce.pill(ce.EJES_1816["Soberanos USD Linked Lelink"]) == "dolar_linked"
    assert ce.pill(ce.EJES_1816["Soberanos ARS Botes"]) == "tasa_fija"


def test_lo_que_la_fuente_no_afirma_queda_en_None():
    """No se inventa: `ley` solo se completa donde el nombre de la curva la dice
    (Bonares/Globales). `Soberanos ARS tasa fija` no la dice."""
    assert ce.EJES_1816["Soberanos ARS tasa fija"].ley is None
    assert ce.EJES_1816["Corporativos USD"].ley is None


def test_hard_dolar_junta_varios_emisores():
    """La pill NO es la curva: HARD DOLAR = USD + fija, sin importar quién emite.
    Es lo que ya pasa hoy (hay 6 corporativos guardados bajo curva='soberanos')."""
    para = ["Soberanos USD Bonares", "Soberanos USD Globales",
            "Corporativos USD", "Provinciales USD", "BCRA USD"]
    assert {ce.pill(ce.EJES_1816[c]) for c in para} == {"hard_dolar"}


def test_cer_fijado_va_a_TASA_FIJA_y_esa_regla_no_se_pierde():
    """REGRESIÓN de la vista: un CER con el CER de liquidación ya publicado se
    comporta como tasa fija y hoy la vista lo muestra ahí. `cer_fijado` es un
    ESTADO (cambia solo cuando el BCRA publica), no un eje del instrumento."""
    cer = ce.EJES_1816["Soberanos ARS CER"]
    assert ce.pill(cer, cer_fijado=False) == "cer"
    assert ce.pill(cer, cer_fijado=True) == "tasa_fija"


def test_los_duales_existen_como_pill_propia():
    """Hoy están repartidos entre las curvas `cer` (5) y `tamar` (3), por eso no
    se pueden mostrar juntos. Con el eje `ajuste=dual` la pill se llena sola."""
    assert ce.pill(ce.EJES_1816["Soberanos Duales"]) == "duales"
    assert ce.pill(ce.EJES_1816["Provinciales Duales"]) == "duales"


def test_dolar_linked_cae_del_lado_USD_de_la_vista():
    dl = ce.EJES_1816["Soberanos USD Linked"]
    assert ce.pill(dl) == "dolar_linked"
    assert ce.LADO["dolar_linked"] == "USD"
    # y el layout parte las 6 pills en dos columnas, 4 y 2
    assert sorted(p for p in ce.PILLS if ce.LADO[p] == "ARS") == \
           ["cer", "duales", "tamar", "tasa_fija"]
    assert sorted(p for p in ce.PILLS if ce.LADO[p] == "USD") == \
           ["dolar_linked", "hard_dolar"]


def test_sin_pill_los_ajustes_que_todavia_no_tienen_tab():
    """Badlar, TPM y Caución existen como ajuste pero no tienen pill acordada.
    Devolver None es correcto: NO deben caer en otra pill por descarte."""
    for c in ("Corporativos ARS Badlar", "Corporativos ARS TPM",
              "Corporativos ARS Caución"):
        assert ce.pill(ce.EJES_1816[c]) is None


def test_curva_desconocida_no_se_adivina():
    assert ce.desde_1816("Soberanos ARS Algo Nuevo") is None
    assert ce.desde_1816(None) is None
    assert ce.desconocidas(["Soberanos ARS CER", "Curva Rara", None, ""]) == ["Curva Rara"]


def test_todas_las_pills_tienen_display_y_lado():
    for p in ce.PILLS:
        assert ce.DISPLAY[p] and ce.LADO[p] in ("ARS", "USD")
