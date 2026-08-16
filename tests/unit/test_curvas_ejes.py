"""Tests de los EJES de renta fija (core/curvas_ejes.py) — puros, sin red ni base.

Congelan las decisiones del rediseño 2026-08-15 (docs/RENTA_FIJA.md §0). Lo que
se protege acá no es "que el código ande": es que **no se pierda una regla de la
vista más usada de la app** al migrar.
"""
from __future__ import annotations

from core import curvas_ejes as ce


def test_las_28_curvas_de_1816_estan_y_son_validas():
    """La tabla es la fuente: si alguien agrega una fila mal tipeada, salta acá.
    Son 28: 26 traducibles + 2 que conocemos y NO se pueden traducir (los duales,
    porque 1816 no dice contra qué ajustan las dos patas)."""
    assert len(ce.EJES_1816) + len(ce.CURVAS_SIN_EJES) == 28
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
    assert ce.pills(bonar) == ce.pills(global_) == ("hard_dolar",)


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
    assert ce.pills(ce.EJES_1816["Soberanos ARS Letras CER"]) == ("cer",)
    assert ce.pills(ce.EJES_1816["Soberanos USD Linked Lelink"]) == ("dolar_linked",)
    assert ce.pills(ce.EJES_1816["Soberanos ARS Botes"]) == ("tasa_fija",)


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
    assert {ce.pills(ce.EJES_1816[c]) for c in para} == {("hard_dolar",)}


def test_cer_fijado_va_a_TASA_FIJA_y_esa_regla_no_se_pierde():
    """REGRESIÓN de la vista: un CER con el CER de liquidación ya publicado se
    comporta como tasa fija y hoy la vista lo muestra ahí. `cer_fijado` es un
    ESTADO (cambia solo cuando el BCRA publica), no un eje del instrumento."""
    cer = ce.EJES_1816["Soberanos ARS CER"]
    assert ce.pills(cer, cer_fijado=False) == ("cer",)
    assert ce.pills(cer, cer_fijado=True) == ("tasa_fija",)


def test_un_dual_entra_a_SUS_DOS_tablas():
    """EL cambio de modelo. Un dual no es una familia aparte: es un bono con dos
    patas, y el trader lo mira en CER *y* en TAMAR. La pill DUALES se eliminó
    justamente porque lo escondía de las dos."""
    dual = ce.Ejes("soberano", "ARS", "cer", ajuste_alt="tamar")
    assert ce.pills(dual) == ("cer", "tamar")
    assert "duales" not in ce.PILLS


def test_1816_no_puede_clasificar_un_dual_y_lo_dice():
    """Su curva se llama "Soberanos Duales" y no nombra el par (verificado: la
    denominación de los ocho es `GOB ARS ARG DUAL (<ticker>)`). Devolver un
    `ajuste='dual'` desde acá hacía que `clasificar_curvas --aplicar` le pisara
    las patas a cada dual ya migrado, en silencio y para siempre."""
    assert ce.desde_1816("Soberanos Duales") is None
    assert "dual" in ce.motivo_sin_ejes("Soberanos Duales")
    # y NO es lo mismo que una curva desconocida: a esta la conocemos
    assert ce.desconocidas(["Soberanos Duales"]) == []


def test_dual_dejo_de_ser_un_ajuste():
    """Es la CONSECUENCIA de tener dos, no un valor cargable. Mientras estuviera
    en `AJUSTES`, el editor de Manager podría re-introducirlo a mano."""
    assert "dual" not in ce.AJUSTES


def test_ninguna_pata_repetida_ni_orden_inestable():
    """Un dual mal cargado con las dos patas iguales no puede aparecer dos veces
    en la misma tabla; y la principal va siempre primero."""
    assert ce.pills(ce.Ejes("soberano", "ARS", "cer", ajuste_alt="cer")) == ("cer",)
    assert ce.pills(ce.Ejes("soberano", "ARS", "tamar", ajuste_alt="cer")) == ("tamar", "cer")


def test_una_pata_sin_pill_no_se_lleva_puesta_a_la_otra():
    """Un dual TAMAR+badlar tiene que seguir viéndose en TAMAR aunque badlar
    todavía no tenga tabla propia."""
    assert ce.pills(ce.Ejes("provincial", "ARS", "tamar", ajuste_alt="badlar")) == ("tamar",)


def test_dolar_linked_cae_del_lado_USD_de_la_vista():
    dl = ce.EJES_1816["Soberanos USD Linked"]
    assert ce.pills(dl) == ("dolar_linked",)
    assert ce.LADO["dolar_linked"] == "USD"
    # y el layout parte las 5 pills en dos columnas, 3 y 2
    assert sorted(p for p in ce.PILLS if ce.LADO[p] == "ARS") == \
           ["cer", "tamar", "tasa_fija"]
    assert sorted(p for p in ce.PILLS if ce.LADO[p] == "USD") == \
           ["dolar_linked", "hard_dolar"]


def test_sin_pill_los_ajustes_que_todavia_no_tienen_tab():
    """Badlar, TPM y Caución existen como ajuste pero no tienen pill acordada.
    Devolver None es correcto: NO deben caer en otra pill por descarte."""
    for c in ("Corporativos ARS Badlar", "Corporativos ARS TPM",
              "Corporativos ARS Caución"):
        assert ce.pills(ce.EJES_1816[c]) == ()


def test_curva_desconocida_no_se_adivina():
    assert ce.desde_1816("Soberanos ARS Algo Nuevo") is None
    assert ce.desde_1816(None) is None
    assert ce.desconocidas(["Soberanos ARS CER", "Curva Rara", None, ""]) == ["Curva Rara"]


def test_todas_las_pills_tienen_display_y_lado():
    for p in ce.PILLS:
        assert ce.DISPLAY[p] and ce.LADO[p] in ("ARS", "USD")


# ── Fase B: el universo de una curva, traducido a los ejes ───────────────────

def test_el_FIT_no_es_lo_mismo_que_la_VISTA():
    """LA distinción de la fase B. La tabla junta emisores a propósito (querés ver
    el corporativo al lado del soberano); el AJUSTE de la curva no puede — un
    corporativo tiene spread de crédito y corre el fit para todos.

    Medido: con el predicado de vista `soberanos` pasa de 21 a 129 bonos."""
    vista = ce.sql_universo("soberanos")
    fit = ce.sql_universo("soberanos", fit=True)
    assert "emisor_tipo" not in vista
    assert "emisor_tipo = 'soberano'" in fit


def test_los_duales_entran_por_las_DOS_patas():
    """`ajuste_alt` tiene que estar en el predicado o un dual CER+TAMAR quedaría
    fuera de la tabla de TAMAR — el bono existe, la tabla suma bien, y falta."""
    for curva in ("cer", "tamar", "dolar_linked"):
        assert "ajuste_alt" in ce.sql_universo(curva), curva


def test_las_curvas_de_pesos_no_se_mezclan_con_las_de_dolares():
    """`tasa_fija` y `soberanos` son el MISMO ajuste (`fija`) y las separa solo la
    moneda. Sin ese filtro, la curva de LECAPs se comería los Bonares."""
    assert "moneda_eje = 'ARS'" in ce.sql_universo("tasa_fija")
    assert "moneda_eje IN ('USD','EUR')" in ce.sql_universo("soberanos")


def test_una_curva_que_no_existe_devuelve_None():
    """Las `on_*` NO van acá: son `emisor_tipo='corporativo'`, un eje y no una
    curva. Devolver None obliga al caller a decidir en vez de traer un universo
    vacío que parece un resultado."""
    assert ce.sql_universo("on_energia") is None
    assert ce.sql_universo("") is None and ce.sql_universo("cualquiera") is None


def test_hay_predicado_para_cada_pill():
    """Si una pill no tuviera universo, su tabla se dibujaría y el cálculo de esa
    curva quedaría sin insumo."""
    for p in ce.PILLS:
        curva = "soberanos" if p == "hard_dolar" else p
        assert ce.sql_universo(curva), p


def test_la_CURVA_y_la_PILL_son_la_misma_clasificacion():
    """No hay dos tablas de reglas: la curva es la pill con UN nombre traducido
    (la tabla se llama HARD DOLAR, la curva `soberanos`). Si alguien agregara una
    regla nueva solo en un lado, este test lo caza."""
    for nombre, e in ce.EJES_1816.items():
        esperado = tuple(ce._PILL_A_CURVA.get(p, p) for p in ce.pills(e))
        assert ce.curvas_de(e) == esperado, nombre
    assert ce.curvas_de(ce.EJES_1816["Soberanos USD Bonares"]) == ("soberanos",)
    assert ce.curvas_de(ce.EJES_1816["Soberanos ARS CER"]) == ("cer",)


def test_un_dual_pertenece_a_SUS_DOS_curvas():
    """Hoy `por_curva('tamar')` no encuentra a TXMD8 porque su columna dice 'cer'.
    Ese es justo el bono que el trader busca en las dos tablas."""
    assert ce.curvas_de(ce.Ejes("soberano", "ARS", "cer", ajuste_alt="tamar")) == \
           ("cer", "tamar")


def test_la_curva_NO_depende_del_estado_del_dia():
    """`cer_fijado` cambia cuando el BCRA publica. Si entrara acá, un bono
    cambiaría de CURVA de un día para el otro — y las tablas de historia están
    particionadas por curva."""
    cer = ce.EJES_1816["Soberanos ARS CER"]
    assert ce.curvas_de(cer) == ("cer",)


def test_ejes_de_doc_no_inventa_lo_que_falta():
    assert ce.ejes_de_doc({"emisor_tipo": "soberano", "moneda_eje": "ARS"}) is None
    assert ce.ejes_de_doc({}) is None
    e = ce.ejes_de_doc({"emisor_tipo": "soberano", "moneda_eje": "ARS",
                        "ajuste": "cer", "ajuste_alt": "tamar"})
    assert e is not None and e.ajuste_alt == "tamar"
