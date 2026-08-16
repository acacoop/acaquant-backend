"""La FÓRMULA de la TEA la eligen los EJES, no la palabra `curva`.

Congela `engines/curvas.py::rama_calculo` (2026-08-16). Lo que protege no es el
código: es que no vuelva a existir un bono que calcula con la matemática
equivocada porque alguien escribió mal —o se olvidó— una palabra.

El caso real que lo motivó: los 4 BOPREAL del BCRA estaban cargados como
`on_otros` y calculaban con la fórmula de las ONs. Y 6 corporativos estaban
escritos como `soberanos`, contaminando esa curva. Ninguno de los dos daba error.
"""
from engines.curvas import rama_calculo


def _b(**kw):
    """Un bono con los ejes puestos. `curva` a propósito dice OTRA cosa: es la
    única forma de probar que ya no se lee."""
    return {"curva": "on_otros", **kw}


# ── Cada eje manda a su fórmula ──────────────────────────────────────────────

def test_bopreal_del_bcra_en_dolares_va_a_hard_dolar():
    """EL caso del incidente: BCRA + USD + fija es hard dólar, no una ON."""
    assert rama_calculo(_b(emisor_tipo="bcra", moneda_eje="USD", ajuste="fija")) == "soberanos"


def test_soberano_en_pesos_a_tasa_fija_es_tasa_fija():
    assert rama_calculo(_b(emisor_tipo="soberano", moneda_eje="ARS",
                           ajuste="fija")) == "tasa_fija"


def test_cer_es_cer_en_cualquier_emisor():
    for emisor in ("soberano", "provincial"):
        assert rama_calculo(_b(emisor_tipo=emisor, moneda_eje="ARS", ajuste="cer")) == "cer"


def test_dolar_linked_es_dolar_linked():
    assert rama_calculo(_b(emisor_tipo="provincial", moneda_eje="USD",
                           ajuste="dolar_linked")) == "dolar_linked"


def test_provincial_en_dolares_a_tasa_fija_va_a_hard_dolar():
    """Estaban en el cajón `on_otros` y son hard dólar como cualquier otro."""
    assert rama_calculo(_b(emisor_tipo="provincial", moneda_eje="USD",
                           ajuste="fija")) == "soberanos"


def test_tamar_y_badlar_no_tienen_formula_todavia():
    for ajuste in ("tamar", "badlar", "tpm", "caucion"):
        assert rama_calculo(_b(emisor_tipo="soberano", moneda_eje="ARS",
                               ajuste=ajuste)) == "otros"


# ── EL ORDEN: lo más frágil de todo el cambio ────────────────────────────────

def test_corporativo_en_usd_a_tasa_fija_va_a_ON_y_no_a_soberanos():
    """**El invariante que más fácil se rompe.**

    Con la palabra vieja un bono tenía UNA curva y el orden de las preguntas daba
    igual. Con los ejes, un corporativo en dólares a tasa fija cumple la condición
    de `soberanos` (USD + fija) Y la de `on` (corporativo). Si `corporativo` deja
    de preguntarse PRIMERO, las ~140 ONs en dólares se van a la fórmula de los
    soberanos de un día para el otro, sin error.
    """
    assert rama_calculo(_b(emisor_tipo="corporativo", moneda_eje="USD",
                           ajuste="fija")) == "on"


def test_corporativo_gana_sobre_cualquier_ajuste():
    """La rama ON despacha por `moneda_flujo` adentro: el emisor manda sobre todo."""
    for ajuste in ("fija", "cer", "dolar_linked", "tamar"):
        assert rama_calculo(_b(emisor_tipo="corporativo", moneda_eje="USD",
                               ajuste=ajuste)) == "on"


# ── El fallback a la palabra (rampa de transición) ───────────────────────────

def test_sin_ejes_cae_a_la_palabra_vieja():
    """Los que nadie clasificó siguen calculando como hoy. Sin este fallback se
    caerían a 'otros' y perderían la TEA que hoy muestran."""
    assert rama_calculo({"curva": "on_energia"}) == "on"
    assert rama_calculo({"curva": "tasa_fija"}) == "tasa_fija"
    assert rama_calculo({"curva": "soberanos"}) == "soberanos"


def test_ejes_incompletos_no_deciden_a_medias():
    """Con dos de los tres ejes NO se decide: se cae a la palabra. Adivinar el que
    falta es exactamente lo que producía las fórmulas equivocadas."""
    assert rama_calculo({"curva": "on_otros", "emisor_tipo": "soberano",
                         "moneda_eje": "USD"}) == "on"
    assert rama_calculo({"curva": "cer", "ajuste": "fija"}) == "cer"


def test_sin_ejes_y_sin_palabra_no_inventa_una_formula():
    assert rama_calculo({}) == "otros"
    assert rama_calculo({"curva": None}) == "otros"


def test_los_ejes_le_ganan_a_la_palabra():
    """Todo el cambio en una assert: el bono dice `on_otros` y calcula hard dólar."""
    bono = {"curva": "on_otros", "emisor_tipo": "bcra",
            "moneda_eje": "USD", "ajuste": "fija"}
    assert rama_calculo(bono) == "soberanos"


# ── El motor NO puede borrar la TEA que escribe jobs/tamar_1816 (2026-08-16) ──


def test_la_rama_otros_NO_habilita_el_anti_TEA_fantasma():
    """CONTRATO entre el motor y `jobs/tamar_1816`.

    El job escribe la TEA de los TAMAR en `market_snapshot` porque el motor no
    calcula ninguna para ellos (rama `otros`). Eso solo es seguro mientras el
    anti-TEA-fantasma NO se active para esa rama: si `dep_tasa_disponible`
    devolviera True, el motor pondría `tea = NULL` en cada vuelta —cada 5
    segundos— y la única señal sería que la columna vuelve a estar vacía. Sin
    excepción, sin log, sin nada.

    Si este test falla, no lo arregles cambiando el assert: o el job deja de
    escribir en `market_snapshot`, o la rama de los TAMAR deja de ser `otros`.
    """
    from engines.curvas import dep_tasa_disponible
    assert dep_tasa_disponible("otros", 1000.0, 1000.0) is False
    assert dep_tasa_disponible("otros", None, None) is False


def test_un_TAMAR_puro_cae_en_la_rama_otros():
    """La otra mitad del contrato: el conjunto que el job escribe (`ajuste`
    = 'tamar') es exactamente el que el motor deja sin tasa."""
    from engines.curvas import rama_calculo
    for emisor in ("soberano", "provincial", "corporativo", "bcra"):
        rama = rama_calculo({"emisor_tipo": emisor, "moneda_eje": "ARS",
                             "ajuste": "tamar"})
        # el corporativo va a `on` (tiene su propia matemática); el resto, a `otros`
        assert rama == ("on" if emisor == "corporativo" else "otros")


def test_un_dual_CER_TAMAR_lo_sigue_calculando_el_MOTOR():
    """Por eso el job NO le toca el snapshot: ahí la tasa buena es la del motor,
    que es live. Su pata TAMAR vive en `mercado.tamar_1816`."""
    from engines.curvas import rama_calculo
    assert rama_calculo({"emisor_tipo": "soberano", "moneda_eje": "ARS",
                         "ajuste": "cer", "ajuste_alt": "tamar"}) == "cer"
