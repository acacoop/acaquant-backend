"""Tests de los detectores del Agente Curador (E1) — docs/AGENTE_CURADOR.md.

Los tres detectores son PUROS: reciben los datos ya leídos, así que se testean
sin Postgres, sin red y sin gastar un crédito de 1816.

**Lo que estos tests protegen es el FALSO POSITIVO.** El riesgo de esta etapa no
es que se escape un bono roto: es que la lista traiga ruido, nadie la mire, y el
agente muera aunque funcione. Por eso la mitad de los casos de acá afirman que
algo NO se reporta.
"""
from __future__ import annotations

from api.services import curador


def _doc(ticker_corto: str, **kw) -> dict:
    """Doc del master como lo devuelve `curvas_sql.cargar_todos()`: el blob
    conserva las claves VIEJAS (`ticker_corto` = AL30, `ticker` = el símbolo de
    Primary) y los ejes llegan de columnas."""
    d = {"ticker_corto": ticker_corto,
         "ticker": f"MERV - XMEV - {ticker_corto} - 24hs",
         "emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "cer",
         "flujos": [{"fecha": "2027-01-01", "amortizacion_pct": 100}]}
    d.update(kw)
    return d


def _inst(curva: str) -> dict:
    return {"_curva": curva, "_curva_id": 7}


# ── 1) faltantes ─────────────────────────────────────────────────────────────


def test_un_ticker_de_1816_que_no_tengo_se_reporta():
    univ = {"TZXD8": _inst("Soberanos ARS CER")}
    out = curador.detectar_faltantes(univ, [], alcance="soberanos")
    assert [h["ticker"] for h in out] == ["TZXD8"]
    assert out[0]["evidencia"]["ejes_sugeridos"]["ajuste"] == "cer"


def test_el_cruce_normaliza_la_ESPECIE_y_no_inventa_un_faltante():
    """1816 publica `AL30` y nuestro master puede tener `AL30D` (la pata en
    dólares). Sin normalizar, TODOS los bonos con especie aparecerían como
    faltantes — el falso positivo más grande posible en este detector."""
    univ = {"AL30": _inst("Soberanos USD Bonares")}
    assert curador.detectar_faltantes(univ, [_doc("AL30D")], alcance="todo") == []


def test_el_ALCANCE_deja_afuera_lo_que_no_se_mira():
    """Con alcance `soberanos`, un corporativo de 1816 no es un hallazgo: no es
    que falte, es que todavía no lo miramos (decisión D1, abierta)."""
    univ = {"VSCRO": _inst("Corporativos USD")}
    assert curador.detectar_faltantes(univ, [], alcance="soberanos") == []
    assert len(curador.detectar_faltantes(univ, [], alcance="todo")) == 1


def test_una_curva_DESCONOCIDA_de_1816_se_canta_no_se_clasifica_sola():
    """Si el proveedor publica un nombre de curva nuevo, tiene que ser VISIBLE.
    Clasificarlo por parecido sería adivinar justo donde el catálogo es la única
    fuente confiable."""
    univ = {"XXXX": _inst("Soberanos ARS Cripto")}
    out = curador.detectar_faltantes(univ, [], alcance="todo")
    assert out[0]["evidencia"]["curva_desconocida"] is True
    assert out[0]["evidencia"]["ejes_sugeridos"] is None
    # ...y con alcance acotado NO se cuela: sin ejes no se puede afirmar que sea
    # soberano, y ante la duda no entra.
    assert curador.detectar_faltantes(univ, [], alcance="soberanos") == []


# ── 2) sin flujo ─────────────────────────────────────────────────────────────


def test_sin_flujo_distingue_lo_que_1816_puede_resolver():
    docs = [_doc("AAA", flujos=[]), _doc("BBB", flujos=[])]
    out = {h["ticker"]: h for h in
           curador.detectar_sin_flujo(docs, {"AAA": _inst("Soberanos ARS CER")})}
    assert out["AAA"]["evidencia"]["resoluble_con_1816"] is True
    assert out["BBB"]["evidencia"]["resoluble_con_1816"] is False
    # el que no se puede resolver NO se marca como más urgente: pedir el
    # prospecto es trabajo humano, no una alarma
    assert out["AAA"]["severidad"] == "alta"
    assert out["BBB"]["severidad"] == "media"


def test_un_bono_CON_flujo_no_aparece():
    assert curador.detectar_sin_flujo([_doc("AL30")], {}) == []


# ── 3) tasas sospechosas ─────────────────────────────────────────────────────


def test_sin_tea_con_precio_se_reporta():
    """El caso VSCYO: tiene precio y flujo, el motor no persiste TEA porque el
    XIRR no converge. Es el hallazgo que hoy no existe en ninguna pantalla."""
    d = _doc("VSCYO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": None, "paridad": 95.0, "duration": 3.0,
                       "last_price": 112.0}}
    reglas = [h["regla"] for h in curador.detectar_tasas_sospechosas([d], m, {"VSCYO"})]
    assert "sin_tea_con_precio" in reglas


def test_un_TAMAR_sin_tea_NO_es_un_hallazgo():
    """El motor no calcula TEA para la rama `otros` — es una decisión de
    arquitectura, no una falla. Reportarlo serían 18 bonos de ruido fijo todas
    las noches, que es exactamente cómo se entrena a la gente a ignorar la lista."""
    d = _doc("TXMD9", ajuste="tamar")
    m = {d["ticker"]: {"tea": None, "paridad": 84.1, "duration": 1.2,
                       "last_price": 84.1}}
    assert curador.detectar_tasas_sospechosas([d], m, {"TXMD9"}) == []


def test_un_bono_SIN_FLUJO_no_se_cuenta_tambien_como_tasa_rota():
    """Ya lo reporta el detector 2. Contarlo dos veces infla la lista y hace
    parecer que hay dos problemas donde hay uno."""
    d = _doc("AAA", flujos=[])
    m = {d["ticker"]: {"tea": None, "paridad": None, "duration": None,
                       "last_price": 100.0}}
    assert curador.detectar_tasas_sospechosas([d], m, {"AAA"}) == []


def test_la_tasa_RUIDOSA_por_duration_no_se_reporta():
    """AFCHO mostraba TEA 142,1% por vencer en 3 días. No está mal calculada:
    anualizar 3 días amplifica centavos a tres dígitos. La vista ya la marca
    `tasa_ruido` y el curador usa EL MISMO predicado (`es_tasa_ruido`), no una
    copia que pueda divergir."""
    d = _doc("AFCHO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": 1.421, "paridad": 99.0, "duration": 0.008,
                       "last_price": 99.0}}
    reglas = [h["regla"] for h in curador.detectar_tasas_sospechosas([d], m, {"AFCHO"})]
    assert "tea_fuera_de_rango" not in reglas


def test_una_LECAP_corta_con_tasa_alta_SI_se_mira():
    """La exclusión por duration es solo para CORPORATIVOS ilíquidos. Un soberano
    corto cotiza con volumen y su tasa es real — si la TEA se le va de rango, es
    un hallazgo de verdad."""
    d = _doc("S30S6", ajuste="fija")
    m = {d["ticker"]: {"tea": 1.42, "paridad": 99.0, "duration": 0.01,
                       "last_price": 99.0}}
    reglas = [h["regla"] for h in curador.detectar_tasas_sospechosas([d], m, {"S30S6"})]
    assert "tea_fuera_de_rango" in reglas


def test_paridad_explotada_se_reporta_como_alta():
    d = _doc("YMCTO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": 0.10, "paridad": 14450.0, "duration": 2.0,
                       "last_price": 160000.0}}
    h = [x for x in curador.detectar_tasas_sospechosas([d], m, {"YMCTO"})
         if x["regla"] == "paridad_fuera_de_rango"]
    assert h and h[0]["severidad"] == "alta"


def test_un_bono_SIN_EJES_se_reporta_porque_desaparece_en_silencio():
    """Sin ejes no cae en ninguna curva: se va de la vista, de los forwards y del
    fair value sin dar error (paso 14 de RENTA_FIJA). Es el modo de falla más
    peligroso justamente porque no rompe nada."""
    d = _doc("BA37", emisor_tipo=None, moneda_eje=None, ajuste=None)
    m = {d["ticker"]: {"tea": 0.1, "paridad": 90.0, "duration": 4.0,
                       "last_price": 90.0}}
    out = curador.detectar_tasas_sospechosas([d], m, {"BA37"})
    assert [h["regla"] for h in out] == ["sin_ejes"]


def test_sin_espejo_en_assets_se_reporta_pero_NO_si_no_se_pudo_leer():
    """`None` = la query de assets falló. "No pude mirar" nunca puede convertirse
    en "no está": marcaría los 222 bonos como huérfanos por un error de red."""
    d = _doc("AL30")
    m = {d["ticker"]: {"tea": 0.1, "paridad": 90.0, "duration": 4.0,
                       "last_price": 90.0}}
    con = [h["regla"] for h in curador.detectar_tasas_sospechosas([d], m, set())]
    sin = [h["regla"] for h in curador.detectar_tasas_sospechosas([d], m, None)]
    assert "sin_espejo_en_assets" in con
    assert "sin_espejo_en_assets" not in sin


def test_un_bono_sano_no_genera_ningun_hallazgo():
    """El caso que más importa y el que menos se testea: **el silencio**. Si un
    bono normal produce hallazgos, la lista es ruido por construcción."""
    d = _doc("AL30")
    m = {d["ticker"]: {"tea": 0.09, "paridad": 92.0, "duration": 3.5,
                       "last_price": 92.0}}
    assert curador.detectar_tasas_sospechosas([d], m, {"AL30"}) == []
