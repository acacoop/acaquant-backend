"""Tests de los detectores del AV Agent (E1) — docs/AV_AGENT.md.

Los tres detectores son PUROS: reciben los datos ya leídos, así que se testean
sin Postgres, sin red y sin gastar un crédito de 1816.

**Lo que estos tests protegen es el FALSO POSITIVO.** El riesgo de esta etapa no
es que se escape un bono roto: es que la lista traiga ruido, nadie la mire, y el
agente muera aunque funcione. Por eso la mitad de los casos de acá afirman que
algo NO se reporta.
"""
from __future__ import annotations

from api.services import av_agent


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
    out = av_agent.detectar_faltantes(univ, [], alcance="soberanos")
    assert [h["ticker"] for h in out] == ["TZXD8"]
    assert out[0]["evidencia"]["ejes_sugeridos"]["ajuste"] == "cer"


def test_el_faltante_arrastra_la_FICHA_de_1816():
    """Los nombres de campo son los del proveedor (`emisorNombre`,
    `denominacion`, `monedaDenom`), verificados contra el job de discovery que
    persiste este mismo catálogo. Si 1816 los renombra, esto se rompe acá y no
    en silencio en la pantalla."""
    inst = {**_inst("Soberanos ARS Tamar"), "emisorNombre": "Tesoro Nacional",
            "denominacion": "BONO TAMAR AGO-26", "monedaDenom": "ARS",
            "fechaVencimiento": "2026-08-31", "isinCode": "ARARGE123"}
    ev = av_agent.detectar_faltantes({"M31G6": inst}, [], alcance="todo")[0]["evidencia"]
    assert ev["emisor"] == "Tesoro Nacional"
    assert ev["denominacion"] == "BONO TAMAR AGO-26"
    assert ev["moneda"] == "ARS" and ev["isin"] == "ARARGE123"


def test_un_faltante_QUE_LA_CASA_TIENE_es_severidad_alta():
    """No es lo mismo un bono que no tenemos que uno que SÍ está en la tenencia y
    no valúa. El segundo no es una preferencia: es un arreglo pendiente."""
    univ = {"TB27": _inst("Soberanos ARS Badlar")}
    sin = av_agent.detectar_faltantes(univ, [], alcance="todo")[0]
    con = av_agent.detectar_faltantes(univ, [], alcance="todo", en_cartera={"TB27"})[0]
    assert sin["severidad"] == "media" and sin["evidencia"]["en_cartera"] is False
    assert con["severidad"] == "alta" and con["evidencia"]["en_cartera"] is True
    assert "CARTERA" in con["motivo"]


def test_el_cruce_normaliza_la_ESPECIE_y_no_inventa_un_faltante():
    """1816 publica `AL30` y nuestro master puede tener `AL30D` (la pata en
    dólares). Sin normalizar, TODOS los bonos con especie aparecerían como
    faltantes — el falso positivo más grande posible en este detector."""
    univ = {"AL30": _inst("Soberanos USD Bonares")}
    assert av_agent.detectar_faltantes(univ, [_doc("AL30D")], alcance="todo") == []


def test_el_ALCANCE_deja_afuera_lo_que_no_se_mira():
    """Con alcance `soberanos`, un corporativo de 1816 no es un hallazgo: no es
    que falte, es que todavía no lo miramos (decisión D1, abierta)."""
    univ = {"VSCRO": _inst("Corporativos USD")}
    assert av_agent.detectar_faltantes(univ, [], alcance="soberanos") == []
    assert len(av_agent.detectar_faltantes(univ, [], alcance="todo")) == 1


def test_las_PATAS_de_1816_no_son_instrumentos_faltantes():
    """**Falso positivo de la primera corrida:** `BPOA8 @AFIP`, `BPOC7 @AFIP` y
    `TY30P @PUT`. El sufijo `@` marca una VISTA DE VALUACIÓN por componente, no un
    instrumento — `/cashflow` les da 404 y el cuadro lo tiene el ticker base. De
    yapa, `BPOA8` salía DOS veces (base y pata)."""
    univ = {"BPOA8": _inst("BCRA USD"), "BPOA8 @AFIP": _inst("BCRA USD"),
            "TY30P @PUT": _inst("Soberanos ARS Botes")}
    out = av_agent.detectar_faltantes(univ, [], alcance="todo")
    assert [h["ticker"] for h in out] == ["BPOA8"]


def test_una_moneda_que_no_operamos_no_es_un_faltante():
    """Los 6 Globales en EUROS (GE29…GE46). No es que falten: es un mercado en el
    que no estamos. Se excluye por MONEDA y no anotando los seis tickers — una
    regla estructural sigue valiendo cuando emitan el séptimo."""
    univ = {"GE30": _inst("Soberanos EUR Globales"),
            "GD46": _inst("Soberanos USD Globales")}
    out = av_agent.detectar_faltantes(univ, [], alcance="todo")
    assert [h["ticker"] for h in out] == ["GD46"]


def test_un_ticker_IGNORADO_no_vuelve_a_reportarse():
    """El "no me interesa" del user. Sin esto la lista nunca converge a cero y una
    lista que repite lo descartado se deja de leer."""
    univ = {"CUAP": _inst("Soberanos ARS CER")}
    assert av_agent.detectar_faltantes(univ, [], alcance="todo",
                                       ignorados={"CUAP"}) == []


def test_una_curva_DESCONOCIDA_de_1816_se_canta_no_se_clasifica_sola():
    """Si el proveedor publica un nombre de curva nuevo, tiene que ser VISIBLE.
    Clasificarlo por parecido sería adivinar justo donde el catálogo es la única
    fuente confiable."""
    univ = {"XXXX": _inst("Soberanos ARS Cripto")}
    out = av_agent.detectar_faltantes(univ, [], alcance="todo")
    assert out[0]["evidencia"]["curva_desconocida"] is True
    assert out[0]["evidencia"]["ejes_sugeridos"] is None
    # ...y con alcance acotado NO se cuela: sin ejes no se puede afirmar que sea
    # soberano, y ante la duda no entra.
    assert av_agent.detectar_faltantes(univ, [], alcance="soberanos") == []


# ── 2) sin flujo ─────────────────────────────────────────────────────────────


def test_una_LECAP_con_flujo_vencimiento_NO_esta_sin_flujo():
    """**El falso positivo de la primera corrida (2026-08-16): 11 letras.** Una
    LECAP/BONCAP es zero-coupon: no tiene `flujos[]` y no le falta nada — el motor
    la valúa con `flujo_vencimiento` (`engines/curvas.py` rama tasa_fija). El
    predicado correcto ya existía en el conciliador de Manager
    (`acreencias.tiene_flujo_def`) y había que USARLO."""
    lecap = _doc("S30S6", ajuste="fija", flujos=[],
                 flujo_vencimiento=147.5, fecha_vencimiento="2027-09-30")
    assert av_agent.detectar_sin_flujo([lecap], {}) == []
    # ...y tampoco se cuela por la puerta de las tasas
    m = {lecap["ticker"]: {"tea": 0.29, "paridad": None, "duration": 0.9,
                           "last_price": 130.0}}
    assert av_agent.detectar_tasas_sospechosas([lecap], m, {"S30S6"}, {"S30S6"}) == []


def test_un_bullet_YA_VENCIDO_si_cuenta_como_sin_flujo():
    """El predicado exige que el pago sea FUTURO: un `flujo_vencimiento` con
    vencimiento pasado no es un cronograma, es historia."""
    viejo = _doc("XXXX", ajuste="fija", flujos=[],
                 flujo_vencimiento=147.5, fecha_vencimiento="2020-01-01")
    assert len(av_agent.detectar_sin_flujo([viejo], {})) == 1


def test_sin_flujo_distingue_lo_que_1816_puede_resolver():
    docs = [_doc("AAA", flujos=[]), _doc("BBB", flujos=[])]
    out = {h["ticker"]: h for h in
           av_agent.detectar_sin_flujo(docs, {"AAA": _inst("Soberanos ARS CER")})}
    assert out["AAA"]["evidencia"]["resoluble_con_1816"] is True
    assert out["BBB"]["evidencia"]["resoluble_con_1816"] is False
    # el que no se puede resolver NO se marca como más urgente: pedir el
    # prospecto es trabajo humano, no una alarma
    assert out["AAA"]["severidad"] == "alta"
    assert out["BBB"]["severidad"] == "media"


def test_un_bono_CON_flujo_no_aparece():
    assert av_agent.detectar_sin_flujo([_doc("AL30")], {}) == []


# ── 3) tasas sospechosas ─────────────────────────────────────────────────────


def test_sin_tea_con_precio_se_reporta():
    """El caso VSCYO: tiene precio y flujo, el motor no persiste TEA porque el
    XIRR no converge. Es el hallazgo que hoy no existe en ninguna pantalla."""
    d = _doc("VSCYO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": None, "paridad": 95.0, "duration": 3.0,
                       "last_price": 112.0}}
    reglas = [h["regla"] for h in av_agent.detectar_tasas_sospechosas([d], m, {"VSCYO"})]
    assert "sin_tea_con_precio" in reglas


def test_un_TAMAR_sin_tea_NO_es_un_hallazgo():
    """El motor no calcula TEA para la rama `otros` — es una decisión de
    arquitectura, no una falla. Reportarlo serían 18 bonos de ruido fijo todas
    las noches, que es exactamente cómo se entrena a la gente a ignorar la lista."""
    d = _doc("TXMD9", ajuste="tamar")
    m = {d["ticker"]: {"tea": None, "paridad": 84.1, "duration": 1.2,
                       "last_price": 84.1}}
    assert av_agent.detectar_tasas_sospechosas([d], m, {"TXMD9"}) == []


def test_un_bono_SIN_FLUJO_no_se_cuenta_tambien_como_tasa_rota():
    """Ya lo reporta el detector 2. Contarlo dos veces infla la lista y hace
    parecer que hay dos problemas donde hay uno."""
    d = _doc("AAA", flujos=[])
    m = {d["ticker"]: {"tea": None, "paridad": None, "duration": None,
                       "last_price": 100.0}}
    assert av_agent.detectar_tasas_sospechosas([d], m, {"AAA"}) == []


def test_la_tasa_RUIDOSA_por_duration_no_se_reporta():
    """AFCHO mostraba TEA 142,1% por vencer en 3 días. No está mal calculada:
    anualizar 3 días amplifica centavos a tres dígitos. La vista ya la marca
    `tasa_ruido` y el AV Agent usa EL MISMO predicado (`es_tasa_ruido`), no una
    copia que pueda divergir."""
    d = _doc("AFCHO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": 1.421, "paridad": 99.0, "duration": 0.008,
                       "last_price": 99.0}}
    reglas = [h["regla"] for h in av_agent.detectar_tasas_sospechosas([d], m, {"AFCHO"})]
    assert "tea_fuera_de_rango" not in reglas


def test_una_LECAP_corta_con_tasa_alta_SI_se_mira():
    """La exclusión por duration es solo para CORPORATIVOS ilíquidos. Un soberano
    corto cotiza con volumen y su tasa es real — si la TEA se le va de rango, es
    un hallazgo de verdad."""
    d = _doc("S30S6", ajuste="fija")
    m = {d["ticker"]: {"tea": 1.42, "paridad": 99.0, "duration": 0.01,
                       "last_price": 99.0}}
    reglas = [h["regla"] for h in av_agent.detectar_tasas_sospechosas([d], m, {"S30S6"})]
    assert "tea_fuera_de_rango" in reglas


def test_paridad_explotada_se_reporta_como_alta():
    d = _doc("YMCTO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": 0.10, "paridad": 14450.0, "duration": 2.0,
                       "last_price": 160000.0}}
    h = [x for x in av_agent.detectar_tasas_sospechosas([d], m, {"YMCTO"})
         if x["regla"] == "paridad_fuera_de_rango"]
    assert h and h[0]["severidad"] == "alta"


def test_un_bono_SIN_EJES_se_reporta_porque_desaparece_en_silencio():
    """Sin ejes no cae en ninguna curva: se va de la vista, de los forwards y del
    fair value sin dar error (paso 14 de RENTA_FIJA). Es el modo de falla más
    peligroso justamente porque no rompe nada."""
    d = _doc("BA37", emisor_tipo=None, moneda_eje=None, ajuste=None)
    m = {d["ticker"]: {"tea": 0.1, "paridad": 90.0, "duration": 4.0,
                       "last_price": 90.0}}
    out = av_agent.detectar_tasas_sospechosas([d], m, {"BA37"})
    assert [h["regla"] for h in out] == ["sin_ejes"]


def test_sin_espejo_en_assets_solo_importa_si_la_casa_TIENE_el_bono():
    """**Falso positivo de la primera corrida: 25 ONs.** Un bono que no está en la
    tenencia no necesita fila en `portafolio.assets` — no le falta nada al AuM
    porque no aporta al AuM. Es el mismo recorte que hace el conciliador de
    Manager, que parte del último AuM."""
    d = _doc("BACAO", emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija")
    m = {d["ticker"]: {"tea": 0.1, "paridad": 90.0, "duration": 4.0,
                       "last_price": 90.0}}
    tengo = [h["regla"] for h in
             av_agent.detectar_tasas_sospechosas([d], m, set(), {"BACAO"})]
    no_tengo = [h["regla"] for h in
                av_agent.detectar_tasas_sospechosas([d], m, set(), set())]
    assert "sin_espejo_en_assets" in tengo
    assert "sin_espejo_en_assets" not in no_tengo


def test_sin_espejo_en_assets_NO_se_reporta_si_no_se_pudo_leer():
    """`None` = la query falló. "No pude mirar" nunca puede convertirse en "no
    está": marcaría los 221 bonos como huérfanos por un error de red. Vale para
    las DOS fuentes — la de assets y la de la cartera."""
    d = _doc("AL30")
    m = {d["ticker"]: {"tea": 0.1, "paridad": 90.0, "duration": 4.0,
                       "last_price": 90.0}}
    for assets, cartera in ((None, {"AL30"}), (set(), None), (None, None)):
        reglas = [h["regla"] for h in
                  av_agent.detectar_tasas_sospechosas([d], m, assets, cartera)]
        assert "sin_espejo_en_assets" not in reglas


def test_un_bono_sano_no_genera_ningun_hallazgo():
    """El caso que más importa y el que menos se testea: **el silencio**. Si un
    bono normal produce hallazgos, la lista es ruido por construcción."""
    d = _doc("AL30")
    m = {d["ticker"]: {"tea": 0.09, "paridad": 92.0, "duration": 3.5,
                       "last_price": 92.0}}
    assert av_agent.detectar_tasas_sospechosas([d], m, {"AL30"}) == []
