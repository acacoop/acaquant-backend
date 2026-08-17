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
from api.services.av_agent_alta import convertir_flujos


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


# ── 4) huecos de curva (capacidad que le falta al sistema) ───────────────────


def test_un_ajuste_SIN_PILL_se_reporta_una_vez_por_AJUSTE():
    """El caso BADLAR (2026-08-16). 1816 publica «Soberanos ARS Badlar» y
    nuestros ejes aceptan `badlar`, pero no tiene pill → esos bonos están
    cargados y NO aparecen en ninguna pantalla, sin dar error.

    Se agrupa por AJUSTE y no por bono: el problema es el ajuste; los bonos son
    la evidencia de cuánto duele."""
    docs = [_doc("TB27", ajuste="badlar"), _doc("TD26", ajuste="badlar"),
            _doc("AL30")]
    out = av_agent.detectar_huecos_de_curva(docs)
    assert len(out) == 1
    assert out[0]["ticker"] == "BADLAR" and out[0]["severidad"] == "alta"
    assert out[0]["evidencia"]["tickers"] == ["TB27", "TD26"]


def test_un_bono_SIN_EJES_no_cuenta_como_hueco_de_curva():
    """No es lo mismo «este bono no está clasificado» (que lo reporta `sin_ejes`)
    que «el sistema no sabe mostrar este tipo de bono». Mezclarlos haría parecer
    que falta una capacidad cuando lo que falta es completar un dato."""
    assert av_agent.detectar_huecos_de_curva(
        [_doc("BA37", emisor_tipo=None, moneda_eje=None, ajuste=None)]) == []


def test_un_faltante_de_un_ajuste_sin_curva_lo_AVISA_antes_del_alta():
    """Avisarlo ANTES es la diferencia entre una decisión informada y cargar diez
    bonos que no se van a poder mirar."""
    univ = {"TB27": _inst("Soberanos ARS Badlar"), "AL30": _inst("Soberanos USD Bonares")}
    ev = {h["ticker"]: h["evidencia"]
          for h in av_agent.detectar_faltantes(univ, [], alcance="todo")}
    assert ev["TB27"]["ajuste_sin_curva"] is True
    assert ev["AL30"]["ajuste_sin_curva"] is False


def test_el_RESUMEN_cuenta_TODOS_los_tipos_de_hallazgo():
    """Invariante que se rompió el 2026-08-16: el job imprimía 59 en el total y
    58 repartidos en los bloques, porque la lista de tipos a mostrar estaba
    escrita a mano y `hueco_de_curva` no estaba. **Un hallazgo que el reporte no
    imprime es un hallazgo que no existe** — justo el modo de falla que ese
    detector vino a denunciar."""
    hallazgos = [
        _hall("falta_en_base"), _hall("sin_flujo"),
        _hall("tasa_sospechosa"), _hall("hueco_de_curva"),
        _hall("un_tipo_que_todavia_no_existe"),
    ]
    resumen: dict[str, int] = {}
    for h in hallazgos:
        resumen[h["tipo"]] = resumen.get(h["tipo"], 0) + 1
    por_tipo = sum(v for k, v in resumen.items() if not k.startswith("regla:"))
    assert por_tipo == len(hallazgos)


def _hall(tipo: str) -> dict:
    return {"tipo": tipo, "ticker": "X", "regla": "r", "severidad": "alta",
            "motivo": "…", "evidencia": {}}


# ── E2: convertir el cuadro de 1816 a nuestra shape ─────────────────────────


def _cup(fecha: str, amort: float, interes: float) -> dict:
    return {"fechaPagoEfectiva": fecha, "fechaPagoTeorica": fecha,
            "flujoAmortizacion": amort, "flujoInteres": interes}


def test_la_rama_SOBERANOS_usa_las_claves_porcentuales():
    """En `soberanos`, `cupon_sobre_residual` ES un monto por 100 VN — o sea
    exactamente lo que manda 1816, sin convertir."""
    from api.services.av_agent_alta import convertir_flujos
    r = convertir_flujos([_cup("2027-01-15", 0, 2.5), _cup("2027-07-15", 100, 2.5)],
                         "soberanos")
    assert r["flujos"][0] == {"fecha": "2027-01-15", "amortizacion_pct": 0.0,
                              "cupon_sobre_residual": 2.5,
                              # el flujo lo IGNORA; lo usa la paridad (E2.u)
                              "residual_previo_pct": 100.0}
    assert r["escala"] == "vn100" and r["suma_amort"] == 100.0


def test_la_rama_TASA_FIJA_usa_las_claves_absolutas():
    from api.services.av_agent_alta import convertir_flujos
    r = convertir_flujos([_cup("2027-01-15", 0, 2.5)], "tasa_fija")
    assert r["flujos"][0] == {"fecha": "2027-01-15", "amortizacion": 0.0,
                              "interes": 2.5}


def test_un_BULLET_se_guarda_como_flujo_vencimiento_no_como_cronograma():
    """Una LECAP tiene un solo pago y el motor la valúa con `flujo_vencimiento`
    (`tea = (flujo_vto/precio)^(365/días) − 1`). Guardarla como cronograma de un
    cupón la dejaría sin TEA."""
    from api.services.av_agent_alta import convertir_flujos
    r = convertir_flujos([_cup("2027-01-15", 100, 47.5)], "tasa_fija")
    assert r["flujo_vencimiento"] == 147.5


def test_la_ESCALA_se_MIDE_no_se_asume():
    """1816 manda por VN 100 en los bonos por paridad y en NOMINALES en algunas
    ONs (medido). Asumir un divisor global es la falla #2 del catálogo."""
    from api.services.av_agent_alta import convertir_flujos
    assert convertir_flujos([_cup("2027-01-15", 100, 5)], "tasa_fija")["escala"] == "vn100"
    assert convertir_flujos([_cup("2027-01-15", 148869.84, 5)],
                            "tasa_fija")["escala"] == "nominales"


def test_en_CER_el_cupon_se_convierte_a_TASA_sobre_el_residual_vivo():
    """**La trampa del paso 15 de RENTA_FIJA.** En `cer`, `cupon_sobre_residual`
    NO es el monto que manda 1816: es la TASA que el motor MULTIPLICA por el
    residual vivo. Guardar el monto tal cual daría un cupón inflado, y no se ve
    leyendo el código — solo con un chequeo numérico como este.

    Bono que amortiza 50 y 50, pagando 2,0 sobre 100 vivos y 1,0 sobre 50: las dos
    veces es la MISMA tasa del 2%."""
    from api.services.av_agent_alta import convertir_flujos
    r = convertir_flujos([_cup("2027-01-15", 50, 2.0), _cup("2027-07-15", 50, 1.0)],
                         "cer")
    assert r["flujos"][0]["cupon_sobre_residual"] == 0.02
    assert r["flujos"][1]["cupon_sobre_residual"] == 0.02
    assert r["flujos"][1]["residual_previo_pct"] == 50.0


def test_las_ramas_sin_conversion_automatica_dan_su_PROPIO_motivo():
    """El mensaje decía «en CER cupon_sobre_residual es una TASA» hasta para un
    BADLAR, que no tiene nada que ver con CER. Un mensaje que no habla del caso
    que uno está mirando no explica: confunde."""
    from api.services.av_agent_alta import RAMAS_AUTOMATICAS, _motivo_no_aplicable
    from core.curvas_ejes import Ejes
    # `dolar_linked` SE SUMÓ el 2026-08-17: el motor dice, textual, que sus flujos
    # usan «el shape porcentual sobre VN igual que soberanos» y llama a la MISMA
    # `monto_flujo_soberano`, así que la conversión es tan directa como la de un
    # soberano. Excluirla era una hipótesis mía que el código desmiente.
    assert set(RAMAS_AUTOMATICAS) == {"tasa_fija", "soberanos", "cer", "dolar_linked"}
    assert _motivo_no_aplicable("dolar_linked", Ejes("soberano", "USD",
                                                    "dolar_linked")) == ""
    assert _motivo_no_aplicable("cer", Ejes("soberano", "ARS", "cer")) == ""
    # Un TAMAR NO tiene motivo: se da de alta solo. Su tasa la trae un job, así
    # que el cuadro es lo único que hay que guardar — y es el caso más simple.
    assert _motivo_no_aplicable("otros", Ejes("soberano", "ARS", "tamar")) == ""


# ── PRE-FLIGHT — la cadena completa antes de escribir ───────────────────────
#
# Estos tests existen porque el modo de fallar de un alta NO es una excepción:
# es un bono escrito que nunca recibe precio. Media docena de eslabones rompen
# en silencio, y lo único que los hace visibles es esta lista.

def _ejes_cer():
    from core.curvas_ejes import Ejes
    return Ejes("soberano", "ARS", "cer")


def _conv_ok():
    from api.services.av_agent_alta import convertir_flujos
    return convertir_flujos([_cup("2028-12-15", 100, 5.0)], "cer")


def _chequear(**kw):
    from api.services.av_agent_alta import _chequeos
    base = dict(
        ticker="TZXD8", curva_1816="CER", ejes=_ejes_cer(), rama="cer",
        conv=_conv_ok(), cer_emision=791.4897, nota_cer="",
        simbolo="MERV - XMEV - TZXD8 - 24hs", origen_simbolo="especies",
        ctx={"ok": True, "especies": [{"simbolo": "MERV - XMEV - TZXD8 - 24hs",
                                       "especie": "pesos", "moneda": "ARS",
                                       "plazo": "24hs", "es_default": True,
                                       "activa": True, "validado": True}],
             "ya_en_curvas": False, "simbolo_actual": None,
             "assets": [{"unidad": "[123] TZXD8", "instrumento": "x", "vigente": True}]},
        estado_simbolo={"conocido": True, "nota": "Primary lo lista"},
        precio=95.0, tea=0.0682, fuente_precio="snapshot",
        ref={"precio": 95.0, "tea": 0.0680, "paridad": 0.9500,
             "fecha": "2026-08-15"}, paridad=95.0,
        ficha_curvas={"tipo": "Bono", "emisor": "X", "fecha_emision": "2025-01-01",
                      "cupon_anual": 0.0})
    base.update(kw)
    return _chequeos(**base)


def test_la_cadena_verde_devuelve_TODOS_los_pasos_no_solo_los_rotos():
    """Mostrar solo lo que falla obliga a confiar en que el resto se chequeó.
    Ver los pasos en verde ES la respuesta a «¿qué pasa si aplico?»."""
    from api.services.av_agent_alta import _veredicto
    ps = _chequear()
    assert len(ps) >= 8
    assert not [p for p in ps if p["estado"] == "bloquea"]
    assert _veredicto(ps)["estado"] == "ok"
    # El paso del reinicio NUNCA es verde solo: es una acción manual, y decirlo
    # es la mitad del valor del pre-flight.
    reinicio = next(p for p in ps if "market_snapshot" in p["titulo"])
    assert reinicio["estado"] == "info" and "reiniciar" in reinicio["accion"]


def test_sin_especie_NO_bloquea_porque_sembrarla_es_parte_del_alta():
    """Que el papel no tenga especie todavía no dice «esto no va a funcionar»,
    dice «esto no está listo» — y dejarlo listo ES el trabajo. Se avisa que el
    símbolo está armado, y se agrega un paso FINAL que lo va a sembrar."""
    ps = _chequear(ctx={"ok": True, "especies": [], "ya_en_curvas": False,
                        "simbolo_actual": None, "assets": []},
                   origen_simbolo="armado")
    p5 = next(p for p in ps if p["clave"] == "especie")
    assert p5["estado"] == "info"            # informativo: el alta la siembra
    assert "armado" in p5["detalle"]
    # El paso de siembra existe, va ÚLTIMO, y dice que no hay que correr nada.
    sembrar = next(p for p in ps if p["clave"] == "sembrar")
    assert ps[-1]["clave"] == "sembrar"
    assert "pesos" in sembrar["detalle"] and "dólares" in sembrar["detalle"]
    assert "no hay que correr nada" in sembrar["accion"]


def test_con_especie_ya_sembrada_NO_se_agrega_el_paso_de_siembra():
    """No se propone hacer algo que ya está hecho."""
    assert not [p for p in _chequear() if p["clave"] == "sembrar"]


def test_si_la_base_no_responde_NO_se_afirma_que_falta_nada():
    """«No pude mirar» nunca es «no está» (REGLA #2). Con la base caída los
    pasos de la cadena de precio salen `no_se_puede_saber`, no `falla`."""
    from api.services.av_agent_alta import _veredicto
    ps = _chequear(ctx={"ok": False, "error": "OperationalError"})
    assert not [p for p in ps if p["estado"] == "bloquea"]
    assert [p for p in ps if p["estado"] == "no_se_puede_saber"]
    assert _veredicto(ps)["estado"] == "no_se_puede_saber"


def test_primary_sin_el_simbolo_BLOQUEA_porque_nunca_va_a_tener_precio():
    """`core/websocket.agregar_suscripciones` descarta lo que no está en el
    catálogo: el alta se escribe y la TEA queda vacía para siempre."""
    ps = _chequear(estado_simbolo={"conocido": False, "nota": "⚠ Primary NO lista"})
    assert next(p for p in ps if p["clave"] == "primary")["estado"] == "bloquea"


def test_un_ticker_que_YA_esta_en_el_master_avisa_que_lo_va_a_pisar():
    ps = _chequear(ctx={"ok": True, "especies": [], "ya_en_curvas": True,
                        "simbolo_actual": "MERV - XMEV - TZXD8 - CI", "assets": []})
    assert ps[0]["clave"] == "ya_existe" and "PISA" in ps[0]["detalle"]
    # El `n` es presentación: se numera sobre los pasos que aplicaron.
    assert [p["n"] for p in ps] == list(range(1, len(ps) + 1))


def test_una_rama_sin_formula_avisa_que_la_TEA_va_a_quedar_vacia(monkeypatch):
    """El bono va a tener precio igual — el error es invisible salvo por esto.
    Y la MISMA rama pasa en verde si su curva se valúa con 1816: lo que decide
    no es la rama, es de dónde sale la tasa."""
    from core import curvas_catalogo
    from core.curvas_ejes import Ejes
    badlar = Ejes("corporativo", "ARS", "badlar")

    monkeypatch.setattr(curvas_catalogo, "fuente_valuacion", lambda a: "motor")
    monkeypatch.setattr(curvas_catalogo, "job_de_la_tasa", lambda a: "")
    p9 = next(p for p in _chequear(rama="otros", ejes=badlar) if p["clave"] == "tea_motor")
    assert p9["estado"] == "bloquea" and "vacía" in p9["detalle"]

    monkeypatch.setattr(curvas_catalogo, "fuente_valuacion", lambda a: "1816")
    monkeypatch.setattr(curvas_catalogo, "job_de_la_tasa", lambda a: "jobs/tamar_1816")
    p9 = next(p for p in _chequear(rama="otros", ejes=badlar) if p["clave"] == "tea_motor")
    assert p9["estado"] == "ok" and "jobs/tamar_1816" in p9["detalle"]
    # ...y ahí el alta NO se bloquea: el cuadro es lo único que hay que guardar.
    assert next(p for p in _chequear(rama="otros", ejes=badlar)
                if p["clave"] == "rama")["estado"] == "ok"


# ── El CONTROL CRUZADO contra la TEA de 1816 ────────────────────────────────
#
# Es el chequeo que no se podía tener en un bono nuevo, y el más valioso: un
# cuadro de flujos mal convertido NO tira error — da un número plausible y
# equivocado. Dos cálculos independientes sobre el MISMO precio son la única
# evidencia real de que la conversión está bien.

def test_el_JUEZ_es_la_PARIDAD_no_la_TEA():
    """**El cambio de criterio del 2026-08-17.** La paridad es precio / valor
    técnico: depende SOLO del precio y del cronograma, que es exactamente lo que
    el cotejo audita. La TEA agrega convención de días y tipo de cambio — dos
    capas que no dicen nada sobre si el cuadro está bien.

    Caso real GD46: paridades que coinciden con TEAs a 139 bps. El cuadro está
    bien y la diferencia es método de anualización."""
    from api.services.av_agent_alta import _cotejo_tea
    p = _cotejo_tea(0.0769, {"paridad": 0.7278, "tea": 0.0908,
                             "convencion_tna": "180-360"}, paridad=72.78)
    assert p["estado"] == "ok"                       # la PARIDAD coincide
    assert "paridad" in p["detalle"].lower()
    # ...y la TEA se informa igual, con el motivo de la diferencia.
    assert "139 bps" in p["detalle"] and "180-360" in p["detalle"]
    assert "NO significa que el cuadro esté mal" in p["detalle"]


def test_la_ESCALA_de_la_paridad_se_normaliza_antes_de_comparar():
    """Nuestro motor devuelve la paridad en PORCENTAJE (72.78) y 1816 como
    FRACCIÓN (0.7278). Compararlas crudas daría «se contradicen» SIEMPRE — la
    clase de bug que no tira error y solo produce alarmas que se aprenden a
    ignorar."""
    from api.services.av_agent_alta import _cotejo_tea
    assert _cotejo_tea(0.07, {"paridad": 0.7278}, paridad=72.78)["estado"] == "ok"


def test_una_paridad_que_SE_CONTRADICE_BLOQUEA_el_alta():
    """**El incidente de GD46 (2026-08-17), congelado.** Paridad nuestra 0,05%
    contra 75,56% de 1816: al MISMO precio, otro valor técnico, o sea otro
    cronograma. Eso salía en ÁMBAR con el texto «no se bloquea el alta: el umbral
    es un primer corte, no una medición», el veredicto decía «se puede aplicar» y
    el botón APLICAR estaba habilitado.

    El razonamiento estaba mal. El umbral es un primer corte para decidir CUÁNDO
    alarmarse; una vez cruzado por 20 veces lo que hay no es una alarma difusa,
    es una demostración. Y lo que se escribiría es un bono que muestra una TEA
    plausible y equivocada — no falla, miente."""
    from api.services.av_agent_alta import _cotejo_tea, _veredicto
    p = _cotejo_tea(0.55, {"paridad": 0.7278}, paridad=41.0)
    assert p["estado"] == "bloquea" and p["frena"] is True
    assert "contradicen" in p["detalle"] and p["accion"]
    # ...y el veredicto, que es lo que el botón mira, dice que NO.
    v = _veredicto([p])
    assert v["puede_aplicar"] is False and v["puede_auto"] is False


def test_una_paridad_que_casi_cierra_NO_bloquea_a_mano_pero_SI_al_ROBOT():
    """La banda del medio existe porque colapsarla en un extremo sería mentir:
    2,4% de diferencia puede ser un cupón de más o una fecha corrida, y eso no
    está probado. Un humano puede decidir con evidencia parcial mirando el cuadro;
    un proceso automático no tiene con qué."""
    from api.services.av_agent_alta import _cotejo_tea, _veredicto
    p = _cotejo_tea(0.10, {"paridad": 0.7278}, paridad=71.0)
    assert p["estado"] == "revisar"
    assert p["frena"] is False and p["frena_auto"] is True
    v = _veredicto([p])
    assert v["puede_aplicar"] is True and v["puede_auto"] is False


def test_sin_PARIDAD_no_se_inventa_un_veredicto():
    """1816 no publica todo para todo. «No pude comparar» no es «coincide»."""
    from api.services.av_agent_alta import _cotejo_tea
    assert _cotejo_tea(0.30, {}, paridad=72.0)["estado"] == "no_se_puede_saber"
    assert _cotejo_tea(0.30, {"paridad": 0.72})["estado"] == "no_se_puede_saber"


def test_si_la_tasa_la_trae_un_JOB_el_cotejo_no_aplica():
    """Comparar la tasa de 1816 contra la tasa de 1816 sale bien siempre y no
    prueba nada. Decir «no aplica» es más honesto que un verde vacío."""
    from api.services.av_agent_alta import _cotejo_tea
    p = _cotejo_tea(None, {"tea": 0.38}, job_tasa="jobs/tamar_1816")
    assert p["estado"] == "ok" and "no aplica" in p["detalle"]


def test_el_precio_de_1816_se_usa_solo_si_NO_hay_snapshot():
    """El precio real es el de Primary. El de 1816 es de referencia: sirve para
    poder calcular en un bono que nunca se suscribió, no para reemplazar al real."""
    ps = _chequear(precio=95.0, fuente_precio="1816",
                   ref={"precio": 95.0, "tea": 0.068, "fecha": "2026-08-15"})
    p = next(x for x in ps if x["clave"] == "precio")
    assert p["estado"] == "ok"
    assert "precio de 1816" in p["detalle"]
    # ...y se dice explícito que NO se persiste: `market_snapshot` es del motor.
    assert "no se guarda" in p["detalle"]


def test_un_CER_CERO_CUPON_conserva_su_CRONOGRAMA_no_se_vuelve_bullet():
    """**El bug de TZXM8 (2026-08-17).** Un CER cero cupón tiene UN solo pago, así
    que caía en el atajo del bullet y el doc salía con `flujo_vencimiento` y SIN
    `flujos`. Pero `calcular_campos` lee `flujo_vencimiento` SOLO en la rama
    `tasa_fija`: la rama `cer` arma su cronograma desde `flujos[]` y ni mira ese
    campo → `flujos_futuros = []` → solo duration, **sin dar error**."""
    from api.services.av_agent_alta import convertir_flujos
    cero = [_cup("2028-03-31", 112.65, 0.0)]
    r = convertir_flujos(cero, "cer")
    assert r["flujo_vencimiento"] is None       # NO se vuelve bullet
    assert len(r["flujos"]) == 1
    # ...y el pago queda expresado como el 100% del capital, normalizado.
    assert r["flujos"][0]["amortizacion_pct"] == 100.0

    # La LECAP, en cambio, SÍ es un bullet: esa es su shape en el master.
    assert convertir_flujos([_cup("2027-09-30", 147.5, 0.0)],
                            "tasa_fija")["flujo_vencimiento"] == 147.5
    # Y un soberano de un solo pago tampoco: su rama arma cronograma.
    assert convertir_flujos(cero, "soberanos")["flujo_vencimiento"] is None


def test_la_ESCALA_no_se_reporta_donde_no_afecta_a_nada():
    """En la rama `cer` la conversión divide todo por `suma_amort`, así que es
    invariante a la escala. TZXM8 salía en ámbar por Σ=112,65 cuando ese número
    no afecta a NINGUNO de los valores que se escriben."""
    conv = {"n": 1, "suma_amort": 112.65, "escala": "nominales", "flujos": [],
            "flujo_vencimiento": None}
    cer = next(p for p in _chequear(rama="cer", conv=conv) if p["clave"] == "cuadro")
    assert cer["estado"] == "ok" and "porcentajes" in cer["detalle"]
    # ...pero en `soberanos` los montos son ABSOLUTOS y ahí sí importa.
    sob = next(p for p in _chequear(rama="soberanos", conv=conv)
               if p["clave"] == "cuadro")
    assert sob["estado"] == "info" and "montos absolutos" in sob["detalle"]


# ── La FICHA y la TASA EXTERNA en el alta (2026-08-17) ──────────────────────

def test_la_ficha_de_1816_completa_lo_que_quedaba_vacio():
    """**Medido**: `upsert_bono` acepta 19 campos y el agente mandaba 14 —
    quedaban vacíos emisor, fecha_emision, tipo, tasa_referencia y cupon_anual.
    El emisor es el que más duele: 1816 es la FUENTE DE VERDAD (`jobs/ficha_1816`
    encontró 74 strings para 67 emisores reales) y estaba a un SELECT."""
    from api.services.av_agent_alta import _ficha_para_curvas
    from core.curvas_ejes import Ejes
    ficha = {"emisor": "Tesoro Nacional", "fecha_emision": "2025-08-31"}
    conv = {"flujos": [{"fecha": "2027-08-31", "amortizacion": 100, "interes": 0}]}
    out = _ficha_para_curvas({}, ficha, Ejes("soberano", "ARS", "tamar"), conv)
    assert out["emisor"] == "Tesoro Nacional"
    assert out["fecha_emision"] == "2025-08-31"
    assert out["tasa_referencia"] == "TAMAR"     # contra qué índice ajusta
    assert out["cupon_anual"] == 0.0             # cero cupón: es INEQUÍVOCO


def test_el_cupon_anual_NO_se_inventa_cuando_hay_cupones():
    """Con cupones de por medio habría que asumir la frecuencia. Asumir es justo
    lo que no se hace: se deja vacío y el paso FICHA lo canta."""
    from api.services.av_agent_alta import _ficha_para_curvas
    from core.curvas_ejes import Ejes
    conv = {"flujos": [{"fecha": "2027-01-01", "amortizacion": 0, "interes": 2.5},
                       {"fecha": "2027-07-01", "amortizacion": 100, "interes": 2.5}]}
    out = _ficha_para_curvas({}, {}, Ejes("soberano", "USD", "fija"), conv)
    assert "cupon_anual" not in out


def test_un_TAMAR_avisa_que_va_a_nacer_CON_su_margen(monkeypatch):
    """De un TAMAR el MARGEN es el número que mira la mesa. Sin sembrarlo, el
    bono queda escrito y con la celda vacía hasta que corra el cron — 30 minutos
    en rueda, hasta mañana fuera de ella."""
    from core import curvas_catalogo
    from core.curvas_ejes import Ejes
    monkeypatch.setattr(curvas_catalogo, "fuente_valuacion", lambda a: "1816")
    monkeypatch.setattr(curvas_catalogo, "job_de_la_tasa", lambda a: "jobs/tamar_1816")
    ps = _chequear(rama="otros", ejes=Ejes("soberano", "ARS", "tamar"))
    p = next(x for x in ps if x["clave"] == "tasa_1816")
    assert "margen" in p["detalle"].lower() and "no hay que correr nada" in p["accion"]
    # ...y en un bono que SÍ calculamos, ese paso no existe.
    monkeypatch.setattr(curvas_catalogo, "fuente_valuacion", lambda a: "motor")
    monkeypatch.setattr(curvas_catalogo, "job_de_la_tasa", lambda a: "")
    assert not [x for x in _chequear() if x["clave"] == "tasa_1816"]


def test_el_paso_FICHA_dice_QUE_se_escribe_y_QUE_queda_vacio():
    """«¿El bono entra completo o entra pelado?» es una pregunta legítima que no
    se contestaba mirando la pantalla."""
    ps = _chequear(ficha_curvas={"tipo": "Bono", "emisor": "Tesoro Nacional",
                                 "fecha_emision": "2025-08-31"})
    p = next(x for x in ps if x["clave"] == "ficha")
    assert "Tesoro Nacional" in p["detalle"]
    assert p["estado"] == "info" and "cupon_anual" in p["detalle"]


def test_sin_precio_el_mensaje_dice_QUE_ruedas_se_probaron(monkeypatch):
    """**El reclamo del user (2026-08-17):** «esa fecha que estás tomando es
    cualquiera… siempre va a dar error si lo consultás un domingo».

    Tenía razón dos veces. El bug de fondo (el retroceso que no corría) se arregló
    en `core/mercado_1816`; acá se congela la otra mitad: el MENSAJE. Decía «no
    publicó precio al <fecha>» con la fecha del pedido —que puede ser un día sin
    mercado— y sonaba a que 1816 estaba roto. Ahora nombra las ruedas probadas y,
    si el papel simplemente no opera, lo dice con `ultimaOperacion`."""
    import datetime as _dt

    from api.services import av_agent_alta as alta

    # (1) Ninguna rueda trajo nada → el rango probado, no una fecha suelta.
    def _vacio(tickers, campos, *, al_retroceder=None, **kw):
        for f in ("2026-08-18", "2026-08-17", "2026-08-14"):
            al_retroceder(_dt.date.fromisoformat(f))
        return {}

    monkeypatch.setattr(alta.mercado_1816, "indicadores_vigentes", _vacio)
    r = alta._referencia_1816("TMG27")
    assert "3 ruedas" in r["error"] and "2026-08-18" in r["error"]
    assert "2026-08-14" in r["error"]
    assert r["pedido"]["ruedas"] == ["2026-08-18", "2026-08-17", "2026-08-14"]

    # (2) La rueda respondió con tasa pero sin precio: eso NO es un problema de
    # fecha, es un papel que no opera — y la evidencia es `ultimaOperacion`.
    monkeypatch.setattr(alta.mercado_1816, "indicadores_vigentes",
                        lambda t, c, **kw: {
                            "fechaOperacion": "2026-08-14",
                            "instrumentos": {"TMG27": {"precioDirty": None,
                                                       "tea": 0.38,
                                                       "ultimaOperacion": "2026-05-02"}}})
    r = alta._referencia_1816("TMG27")
    assert r.get("precio") is None
    assert "2026-08-14" in r["error"] and "2026-05-02" in r["error"]


# ── El MODELO DE ESTADOS (rediseño 2026-08-17) ──────────────────────────────
#
# El user, mirando GD46 y TMG27: *«tiene que haber una serie de pasos para dar el
# OK o para frenar. Ahora porque lo hago manual, pero el día de mañana que se haga
# solo esto es fundamental. Hay algunos que no van con warning, o sea no son ni
# buenos ni malos. Vos ya deberías saber que es un check verde o una cruz roja y
# listo: el resto son informativos.»*


def test_los_pasos_INFORMATIVOS_no_cuentan_como_avisos():
    """`atencion` significaba dos cosas incompatibles: «esto está mal» y «esto es
    lo que va a pasar». Con las dos en el mismo ámbar, el contador decía «6 a
    mirar» en una cadena donde no había NADA para mirar — y un contador que grita
    siempre se deja de leer, que es como una contradicción real pasa desapercibida.

    Estos cuatro son consecuencias del alta, no defectos: no frenan nada."""
    from api.services.av_agent_alta import INFO, _paso
    for clave in ("especie", "suscripcion", "assets", "sembrar"):
        p = _paso(clave, "x", INFO, "y")
        assert p["frena"] is False, clave
        assert p["frena_auto"] is False, clave


def test_no_poder_VERIFICAR_frena_al_robot_aunque_no_al_humano():
    """REGLA #2 con consecuencias: «no pude leer la base» no es «está bien». Un
    humano puede mirar otra cosa y decidir; un proceso automático no."""
    from api.services.av_agent_alta import NO_SE, _paso, _veredicto
    v = _veredicto([_paso("especie", "x", NO_SE, "la base no respondió")])
    assert v["puede_aplicar"] is True and v["puede_auto"] is False


def test_el_conteo_del_veredicto_separa_las_cinco_categorias():
    """El resumen lo cuenta el BACKEND y viaja resuelto. Que lo recontara el front
    sería la tercera copia del mismo criterio — que es exactamente cómo nacieron
    las contradicciones de GD46 y TMG27."""
    from api.services.av_agent_alta import BLOQUEA, INFO, NO_SE, OK, REVISAR, _paso, _veredicto
    ps = [_paso("a", "t", OK, ""), _paso("b", "t", INFO, ""),
          _paso("c", "t", INFO, ""), _paso("d", "t", REVISAR, ""),
          _paso("e", "t", NO_SE, ""), _paso("f", "t", BLOQUEA, "")]
    v = _veredicto(ps)
    assert v["conteo"] == {"ok": 1, "info": 2, "revisar": 1, "bloquea": 1, "no_se": 1}
    # BLOQUEA gana sobre todo lo demás: el texto tiene que hablar de eso.
    assert v["estado"] == "bloquea" and "NO se puede aplicar" in v["texto"]


def test_hay_precio_y_el_motor_NO_da_TEA_es_un_BLOQUEO_no_un_tilde_verde():
    """En GD46 este paso salía en VERDE ✔ diciendo, en el mismo renglón, «pero el
    motor NO devolvió TEA: revisar la escala». Un tilde verde sobre un texto que
    describe una falla — y no es una sospecha: se le dieron al motor el cuadro
    real y el precio real, y no calculó."""
    ps = _chequear(precio=69.0, tea=None, fuente_precio="1816")
    p = next(x for x in ps if x["clave"] == "precio")
    assert p["estado"] == "bloquea" and "no calculó la TEA" in p["detalle"]


def test_pero_en_un_TAMAR_que_el_motor_no_de_TEA_es_lo_ESPERADO(monkeypatch):
    """La excepción que hace que el bloqueo de arriba sea correcto y no torpe: a un
    TAMAR/BADLAR **no le calculamos la tasa a propósito** (la trae 1816). Tratar
    eso como falla habría bloqueado justo a los 18 bonos que están bien."""
    from api.services import av_agent_alta as alta
    monkeypatch.setattr(alta, "_tasa_externa", lambda e: ("1816", "jobs/tamar_1816"))
    ps = _chequear(precio=115.2, tea=None, fuente_precio="1816")
    p = next(x for x in ps if x["clave"] == "precio")
    assert p["estado"] == "ok" and "está bien" in p["detalle"]


def test_UNA_sola_funcion_decide_si_la_rama_se_da_de_alta_sola():
    """**El caso TMG27 (2026-08-17): cadena entera en verde y SIN botón APLICAR.**

    Había dos funciones contestando la misma pregunta. El paso «El cuadro se puede
    convertir sin ambigüedad» usaba `_alta_automatica`, que acepta DOS caminos —la
    rama convierte sin ambigüedad, o la tasa la trae 1816— y daba OK. El campo
    `aplicable`, que es lo que el front mira para mostrar el botón, hacía
    `rama in RAMAS_AUTOMATICAS` a secas y daba False para un TAMAR (rama `otros`).

    El bug no es el valor: es que la pregunta tenía dos respuestas. Este test
    congela la que vale."""
    from api.services.av_agent_alta import RAMAS_AUTOMATICAS, _alta_automatica
    from core.curvas_ejes import Ejes
    tamar = Ejes("soberano", "ARS", "tamar")
    assert "otros" not in RAMAS_AUTOMATICAS       # la rama sola diría que NO...
    assert _alta_automatica("otros", tamar) is True   # ...y la respuesta es SÍ


def test_el_CER_de_emision_NO_bloquea_el_alta_pero_deja_un_AVISO():
    """**Decisión del user (2026-08-17), y es un cambio de postura del agente.**

        *«Está bien que se cargue sin CER de emisión. A los CER les perdonamos:
        igual me saca el laburo de cargarlo en la base y hacer todo el trabajo,
        me lo deja sencillo, solo poner el CER de emisión y nada más.»*

    Negarse a hacer el 95% del trabajo —flujos, ejes, ficha, especies— porque no
    se puede hacer el 5% es tirar lo hecho. El aviso reemplaza al bloqueo: el
    bono entra y el pendiente queda anotado. Sigue frenando la lane AUTOMÁTICA,
    porque un robot dejaría el bono sin tasa y sin nadie enterado."""
    ps = _chequear(cer_emision=None, nota_cer="la serie CER no llega hasta 2025-11-28")
    p = next(x for x in ps if x["clave"] == "cer_emision")
    assert p["estado"] == "revisar"
    assert p["frena"] is False and p["frena_auto"] is True
    assert "CER de emisión" in p["aviso"] and "Manager" in p["aviso"]
    from api.services.av_agent_alta import _veredicto
    assert _veredicto(ps)["puede_aplicar"] is True


def test_sin_CER_el_paso_del_precio_dice_la_CAUSA_y_no_culpa_a_la_escala():
    """**El reclamo del user sobre TZXA7: «no lo entiendo, no es claro. Si hay
    flujo y hay precio, ¿por qué no podrías simular?»**

    Tenía razón. `engines/curvas.py:438` sale con solo duration si falta
    `cer_emision`, así que UNA causa pintaba TRES pasos en rojo — y el del precio
    encima mandaba a «revisar la escala del cuadro y la pata», que no tenía nada
    que ver. Un diagnóstico que apunta al lugar equivocado hace perder más tiempo
    que no tenerlo."""
    ps = _chequear(cer_emision=None, nota_cer="x", precio=120.75, tea=None,
                   fuente_precio="1816")
    p = next(x for x in ps if x["clave"] == "precio")
    # NO es un hallazgo propio: es consecuencia del paso de arriba.
    assert p["estado"] == "ok" and p["frena"] is False
    assert "falta el CER de emisión" in p["detalle"]
    assert "escala" not in p["accion"]
    # Y el único que bloquea o avisa es la CAUSA, no el síntoma.
    assert [x["clave"] for x in ps if x["frena_auto"]] == ["cer_emision"]


def test_un_hallazgo_YA_RESUELTO_no_se_sigue_mostrando(monkeypatch):
    """**El «algo tremendo» que detectó el user (2026-08-17): TZXM8 ya estaba en
    curvas y seguía en la lista de faltantes.**

    Su intuición del motivo era correcta —«entiendo que es porque no se ejecutó
    de nuevo»—: la lista es una FOTO de la última corrida, y relevar cuesta ~29
    créditos y 1-2 minutos de throttle, así que no se puede rehacer cada vez que
    se abre la pantalla.

    Pero mostrar como faltante un bono que el agente MISMO acaba de crear destruye
    la confianza en toda la lista: si una fila está mal, ninguna vale. La salida
    no es rehacer la foto, es **contrastarla antes de mostrarla** — una query al
    master. Mismo principio que el `ya_cargado` de los avisos.

    Y solo caducan los tipos que la existencia del ticker resuelve: un `sin_flujo`
    habla de un bono que YA está en el master, así que estar ahí no lo arregla."""
    import datetime as _dt

    from api.services import av_agent_vista as vista

    class _Cur:
        def __init__(self): self.paso = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None): self.sql = sql
        def fetchone(self): return (_dt.datetime(2026, 8, 17, 3, 0),)
        def fetchall(self):
            if "FROM mercado.curvas" in self.sql:
                return [("TZXM8",), ("TZXD8",)]      # ya dados de alta
            return [("falta_en_base", "TZXM8", "r", "media", "m", None),
                    ("falta_en_base", "TZXA7", "r", "media", "m", None),
                    ("sin_flujo", "TZXD8", "r", "alta", "m", None)]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(vista, "get_pool", lambda: type("P", (), {
        "connection": staticmethod(_Conn)})())
    filas, _ = vista._hallazgos_ultima_corrida()
    tickers = {h["ticker"] for h in filas}
    assert "TZXM8" not in tickers          # ya está en curvas → no falta más
    assert "TZXA7" in tickers              # sigue faltando de verdad
    assert "TZXD8" in tickers              # sin_flujo NO caduca por existir


def test_el_VEREDICTO_es_el_UNICO_gate_y_aplicable_es_redundante():
    """**El mismo bug, TRES veces — este test existe para que no haya una cuarta.**

    `aplicable` fue un SEGUNDO gate al lado de `veredicto.puede_aplicar`, y cada
    vez que uno de los dos cambió, el otro quedó viejo y escondió el botón APLICAR
    con la cadena diciendo lo contrario:

    1. TMG27 (E2.k): `rama in RAMAS_AUTOMATICAS` contra `_alta_automatica`.
    2. TZXA7 (E2.m): quedó `and not (cer and not cer_emision)`, así que cuando el
       CER dejó de bloquear la cadena, este renglón lo siguió bloqueando.
    3. Y el front encima hacía `aplicable && puedeAplicar` — el AND convierte al
       más restrictivo en el gate real, que es el que nadie está mirando.

    La causa nunca fue el valor: era tener DOS. Lo que este test congela es que el
    contenido legítimo de `aplicable` —¿la rama convierte sin ambigüedad?— YA
    viaja en la cadena, así que el veredicto lo cubre y `aplicable` no necesita
    (ni debe) gatear nada."""
    from api.services.av_agent_alta import BLOQUEA, _veredicto

    # (a) Rama que NO se da de alta sola → el paso `rama` BLOQUEA por su cuenta,
    #     así que el veredicto ya dice que no. `aplicable` no agregaba nada.
    ps = _chequear(rama="otros")
    rama = next(p for p in ps if p["clave"] == "rama")
    assert rama["estado"] == BLOQUEA
    assert _veredicto(ps)["puede_aplicar"] is False

    # (b) Y el caso que rompió: un CER SIN cer_emision se puede aplicar a mano.
    #     Si algún día alguien vuelve a meter esa condición en un gate, acá falla.
    ps = _chequear(cer_emision=None, nota_cer="la serie no llega")
    assert _veredicto(ps)["puede_aplicar"] is True
    assert _veredicto(ps)["puede_auto"] is False


def test_RAMA_y_CURVA_son_dos_vocabularios_y_un_TAMAR_va_a_curva_tamar():
    """**El alta de TMG27 murió al escribir: «curva inválida: 'otros'».**

    El agente clasificó BIEN — ejes `soberano · ARS · tamar`, y el paso de la rama
    dijo correctamente que la tasa la trae `jobs/tamar_1816`. Lo que falló fue una
    TRADUCCIÓN en el último paso: se mandó `curva = rama`, y son dos vocabularios:

        rama  = qué FÓRMULA usa el motor  → …, otros
        curva = qué TIPO de instrumento   → …, tamar, dual

    Coinciden en cuatro de los cinco valores, que es exactamente por qué el bug
    sobrevivió con un comentario afirmando que eran el mismo valor."""
    from api.services.av_agent_alta import curva_destino
    from core.curvas_ejes import Ejes
    # Un TAMAR: rama `otros`, pero su curva EXISTE y se llama `tamar`.
    assert curva_destino("otros", Ejes("soberano", "ARS", "tamar")) == "tamar"
    # Las cuatro que sí coinciden siguen igual.
    assert curva_destino("cer", Ejes("soberano", "ARS", "cer")) == "cer"
    assert curva_destino("soberanos", Ejes("soberano", "USD", "fija")) == "soberanos"
    # Y un ajuste SIN curva equivalente no se fuerza a una parecida: devuelve ""
    # para que el pre-flight lo bloquee con el motivo.
    assert curva_destino("otros", Ejes("soberano", "ARS", "badlar")) == ""


def test_el_preflight_BLOQUEA_si_la_escritura_va_a_ser_rechazada():
    """*«¿Por qué lo permitió aplicar?»* — porque el pre-flight validaba 13 cosas
    sobre los DATOS y ninguna sobre si la escritura iba a entrar. Este es el
    eslabón que faltaba, y valida contra la MISMA constante que usa el writer."""
    from api.services.av_agent_alta import _veredicto
    from core.curvas_ejes import Ejes
    ps = _chequear(rama="otros", ejes=Ejes("soberano", "ARS", "badlar"))
    p = next(x for x in ps if x["clave"] == "curva_destino")
    assert p["estado"] == "bloquea" and "badlar" in p["detalle"]
    assert _veredicto(ps)["puede_aplicar"] is False


def test_un_HUECO_DE_CURVA_caduca_por_la_CURVA_no_por_el_ticker(monkeypatch):
    """**BADLAR seguía en «le falta al sistema» con la curva YA creada.**

    Es la misma foto vieja que TZXM8, pero mi filtro de E2.m no la tapaba: aplicaba
    UN predicado (`ticker in mercado.curvas`) a dos tipos de hallazgo **cuyo campo
    `ticker` significa cosas distintas**. En un `hueco_de_curva` el `ticker` es el
    AJUSTE (`BADLAR`), no un bono, así que compararlo contra el master no lo sacaba
    nunca.

    Cada tipo caduca por su propia razón, y la de este es `ajuste_sin_curva` — la
    MISMA función que lo detecta, que ya lee el catálogo. Preguntarle de nuevo no
    puede dar un criterio distinto."""
    import datetime as _dt

    from api.services import av_agent_vista as vista

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None): self.sql = sql
        def fetchone(self): return (_dt.datetime(2026, 8, 17, 3, 0),)
        def fetchall(self):
            if "FROM mercado.curvas" in self.sql:
                return [("TZXM8",)]
            return [("hueco_de_curva", "BADLAR", "ajuste_sin_curva", "alta", "m", None),
                    ("hueco_de_curva", "TPM", "ajuste_sin_curva", "alta", "m", None)]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(vista, "get_pool", lambda: type("P", (), {
        "connection": staticmethod(_Conn)})())
    # BADLAR ya tiene curva (el agente la creó); TPM todavía no.
    monkeypatch.setattr(vista.curvas_ejes, "ajuste_sin_curva",
                        lambda aj: aj != "badlar")
    filas, _ = vista._hallazgos_ultima_corrida()
    assert [h["ticker"] for h in filas] == ["TPM"]


def test_una_curva_CREADA_por_el_agente_queda_escribible_sola(monkeypatch):
    """`CURVAS_BONO` era una tupla a mano, así que el alta de un BADLAR se
    rechazaba con «curva inválida» **por una curva que el sistema ya tenía**: el
    agente puede CREAR curvas (E1.h) y de hecho creó ésa.

    Dos fuentes para «¿qué curvas existen?» y solo una se actualiza — el mismo
    patrón de toda la semana. Ahora la constante es el PISO y el catálogo la
    amplía, así que crear la curva habilita el alta en el mismo acto."""
    from api.services import bonos_admin
    from api.services.av_agent_alta import curva_destino
    from core.curvas_ejes import Ejes

    monkeypatch.setattr(bonos_admin, "CURVAS_BONO", ("cer", "tamar"))
    badlar = Ejes("soberano", "ARS", "badlar")

    # Sin la curva en el catálogo → no se puede escribir, y se dice.
    monkeypatch.setattr("core.curvas_catalogo.todas", lambda: {})
    assert curva_destino("otros", badlar) == ""

    # Creada la curva → escribible, sin tocar una línea de código.
    monkeypatch.setattr("core.curvas_catalogo.todas",
                        lambda: {"badlar": {"pill": "badlar"}})
    assert curva_destino("otros", badlar) == "badlar"
    assert "badlar" in bonos_admin.curvas_validas()


def test_un_DOLAR_LINKED_se_convierte_solo_y_NORMALIZA_la_escala():
    """**D10Y7 y D30O6 bloqueados con «la conversión no es directa».** Era una
    hipótesis mía, y `engines/curvas.py:597` la desmiente textual: los flujos de
    un dólar-linked usan «el shape porcentual sobre VN **igual que soberanos**» y
    llaman a la MISMA `monto_flujo_soberano`. Encima el pre-flight se contradecía
    solo: el paso 3 decía que no se podía convertir y el 10, tres renglones
    abajo, que la rama tiene fórmula.

    **La normalización sí es propia.** Un soberano de 1816 viene en base 100
    (GD46 midió Σ=100,000012), pero los dólar-linked vienen en NOMINALES de la
    emisión: los dos casos reales miden **Σ=148.869,84**. Pasar eso crudo como
    «pct» daría un valor técnico ~1.489 veces más grande y una TEA absurda **sin
    ningún error**. Dividir por la Σ lo lleva a base 100 — y con Σ≈100 la
    operación es la identidad, así que sirve en los dos casos."""
    from api.services.av_agent_alta import convertir_flujos
    conv = convertir_flujos(
        [_cup("2027-05-10", 148869.84, 0.0)], "dolar_linked")
    f = conv["flujos"][0]
    assert conv["escala"] == "nominales"
    assert f["amortizacion_pct"] == 100.0          # normalizado a base 100
    assert "amortizacion" not in f                 # shape de soberanos, no absoluto
    # Y con un cuadro que YA viene en base 100, normalizar no lo toca.
    conv2 = convertir_flujos([_cup("2027-05-10", 100.0, 3.0)], "dolar_linked")
    assert conv2["flujos"][0]["amortizacion_pct"] == 100.0
    assert conv2["flujos"][0]["cupon_sobre_residual"] == 3.0


def test_lo_que_NO_cotiza_en_Primary_deja_de_reportarse_salvo_que_lo_TENGAMOS():
    """*«Si no está en Primary ni me interesa: si no le puedo meter el last price
    no tiene valor. ¿Cómo hacemos para que no aparezca constantemente?»*

    Es el filtro más duro y el más correcto: un bono que no cotiza no se puede
    valuar NUNCA, así que reportarlo cada corrida es ruido permanente que empuja
    hacia abajo a los que sí importan.

    Con UNA excepción que invierte el criterio: si la casa lo TIENE en cartera se
    reporta igual: ahí el problema es más grave, no menor — una posición que no
    valúa — y esconderlo sería lo contrario de lo que hay que hacer."""
    univ = {"B31P": _inst("Soberanos ARS Badlar"),   # no cotiza
            "TZXD8": _inst("Soberanos ARS CER")}     # sí cotiza
    primary = {"MERV - XMEV - TZXD8 - 24hs"}
    out = av_agent.detectar_faltantes(univ, [], alcance="todo",
                                      simbolos_primary=primary)
    assert [h["ticker"] for h in out] == ["TZXD8"]

    # ...pero si lo tenemos en cartera, se reporta AUNQUE no cotice.
    out2 = av_agent.detectar_faltantes(univ, [], alcance="todo",
                                       simbolos_primary=primary,
                                       en_cartera={"B31P"})
    assert {h["ticker"] for h in out2} == {"B31P", "TZXD8"}

    # Sin universo de Primary NO se filtra: filtrar de más esconde bonos reales.
    out3 = av_agent.detectar_faltantes(univ, [], alcance="todo")
    assert {h["ticker"] for h in out3} == {"B31P", "TZXD8"}


def test_la_pata_CI_tambien_cuenta_como_que_cotiza():
    """El símbolo se arma por convención con `24hs`. Un bono que cotiza SOLO en
    contado inmediato existe, y descartarlo sería invisible — no se propone y
    nadie se entera."""
    univ = {"XXXX": _inst("Soberanos ARS CER")}
    solo_ci = {"MERV - XMEV - XXXX - CI"}
    assert len(av_agent.detectar_faltantes(univ, [], alcance="todo",
                                           simbolos_primary=solo_ci)) == 1


def test_el_filtro_de_PRIMARY_tambien_corre_al_LEER_la_foto(monkeypatch):
    """**Los BPO seguían apareciendo aunque el filtro ya existía.**

    Porque lo puse SOLO en el detector, que corre al relevar — y relevar cuesta
    ~29 créditos, así que no se hace por pantalla. El filtro no surtía efecto
    hasta la próxima corrida y la lista seguía igual: **la misma trampa de TZXM8 y
    BADLAR, la tercera vez en la misma función.**

    Y por eso el predicado es UNO (`descartar_por_primary`), compartido entre el
    detector y la lectura: dos versiones de «¿se descarta?» terminarían
    contradiciéndose, que es el patrón de toda la semana."""
    import datetime as _dt

    from api.services import av_agent_vista as vista

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None): self.sql = sql
        def fetchone(self): return (_dt.datetime(2026, 8, 17, 3, 0),)
        def fetchall(self):
            if "FROM mercado.curvas" in self.sql:
                return []
            return [("falta_en_base", "BPO27", "r", "media", "m", {"en_cartera": False}),
                    ("falta_en_base", "TZXD8", "r", "media", "m", {"en_cartera": False}),
                    # Lo TENEMOS y no cotiza: se reporta igual, es MÁS grave.
                    ("falta_en_base", "BPOA8", "r", "alta", "m", {"en_cartera": True})]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(vista, "get_pool", lambda: type("P", (), {
        "connection": staticmethod(_Conn)})())
    monkeypatch.setattr(vista.av_agent, "simbolos_primary",
                        lambda: {"MERV - XMEV - TZXD8 - 24hs"})
    filas, _ = vista._hallazgos_ultima_corrida()
    assert {h["ticker"] for h in filas} == {"TZXD8", "BPOA8"}


def test_la_MONEDA_del_pedido_la_decide_lo_que_el_MOTOR_espera():
    """**El bug de GD46 (2026-08-17), y lo introduje yo en E2.g.**

    Ahí cambié el pedido a 1816 a `moneda=mep` para cerrar los 202 bps del cotejo
    de TEA, sin ver que el motor hace su PROPIA conversión: `precio_soberano_a_usd`
    divide por el MEP **salvo que el símbolo termine en D o C**. Como el símbolo se
    arma `MERV - XMEV - GD46 - 24hs` —sin sufijo—, el motor asumía pesos y dividía
    un precio que 1816 ya había devuelto en dólares:

        69 (USD) / 1.517,63 = 0,0455  →  paridad 0,05% contra 75,56% de 1816

    Verificado con aritmética: sin la doble división la paridad da **75,57%**
    contra el **75,56%** de ellos. **El cuadro de flujos estaba perfecto** — el
    problema era la UNIDAD del insumo, y no daba error: devolvía duration ingenua
    y TEA vacía.

    La regla que evita que vuelva: la moneda no se elige por criterio propio, se
    DERIVA del mismo predicado que usa el motor (el sufijo del símbolo)."""
    from api.services.av_agent_alta import moneda_pedido_1816

    # Símbolo SIN sufijo → el motor divide por MEP → hay que pedirle PESOS.
    assert moneda_pedido_1816("MERV - XMEV - GD46 - 24hs", "USD") == "ars"
    # Con sufijo D/C el precio ya viene en dólares y el motor NO convierte.
    assert moneda_pedido_1816("MERV - XMEV - GD46D - 24hs", "USD") == "mep"
    assert moneda_pedido_1816("MERV - XMEV - GD46C - 24hs", "USD") == "mep"
    # Un bono en pesos nunca pasa por el MEP.
    assert moneda_pedido_1816("MERV - XMEV - TZXD8 - 24hs", "ARS") == "ars"


def test_la_doble_division_por_MEP_daba_una_paridad_1500_veces_menor():
    """La aritmética del incidente, congelada: es lo que hace evidente que el
    problema era la unidad y no el cronograma."""
    precio_en_usd, mep = 69.0, 1517.6262
    assert round(precio_en_usd / mep, 4) == 0.0455       # lo que mostraba la pantalla
    vt_de_1816 = precio_en_usd / 0.7556232992733721      # su valor técnico implícito
    assert round(precio_en_usd / vt_de_1816 * 100, 2) == 75.56   # la paridad correcta


def test_el_TIPO_DE_CAMBIO_de_la_simulacion_lo_decide_la_RAMA(monkeypatch):
    """D30O6 (2026-08-17): un dólar-linked simulaba SIN A3500 y salía por la puerta
    de emergencia de `engines/curvas.py` (línea 579) con la duration ingenua y sin
    TEA. Ningún error: el mensaje culpaba «la escala del flujo o la pata».

    La causa era la de siempre —**dos criterios para una pregunta**—: el simulador
    preguntaba `moneda_flujo == "USD"` para decidir si hacía falta un TC, mientras
    el motor decide por RAMA. Un dólar-linked tiene `moneda_flujo` USD y necesita
    A3500, no MEP: con ese criterio no cargaba ninguno de los dos.

    Ahora el predicado es `curva_depende_de`, el mismo que usa el motor."""
    import engines.curvas as ec
    from api.services import av_agent_alta as alta

    vistos: dict = {}

    def _fake_calcular(_trade, _doc, _cer, _hab, mep=None, tc_a3500=None):
        vistos.update(mep=mep, tc_a3500=tc_a3500)
        return {"TEA": 0.12, "duration": 0.2, "paridad": 99.21}

    monkeypatch.setattr(ec, "calcular_campos", _fake_calcular)
    monkeypatch.setattr(ec, "cargar_cer", lambda **_: {})
    monkeypatch.setattr(ec, "cargar_dias_habiles", lambda: [])
    monkeypatch.setattr(ec, "cargar_a3500_actual", lambda: 1488.6984)
    monkeypatch.setattr(alta, "_referencia_1816", lambda *a, **k: {})

    doc = {"curva": "dolar_linked", "ajuste": "dolar_linked", "moneda_flujo": "USD",
           "emisor_tipo": "soberano", "moneda_eje": "USD", "valor_nominal": 100,
           "flujos": [{"fecha": "2026-10-30", "amortizacion_pct": 100.0}]}
    out = alta._simular_tasa(doc, "MERV - XMEV - D30O6 - 24hs", 147690.0)

    assert vistos["tc_a3500"] == 1488.6984, "la rama dolar_linked NECESITA el A3500"
    assert out["_a3500"] == 1488.6984
    assert out["tea"] is not None


def test_la_paridad_se_muestra_en_LA_MISMA_UNIDAD_que_la_de_1816():
    """El cuadro «CÓMO SE CALCULÓ» imprimía «nuestra 68.8575 · 1816 0.7278»: el
    motor devuelve PORCENTAJE y 1816 FRACCIÓN. Se lee como un error de escala de
    100× cuando la diferencia real era del 5%. El paso del cotejo ya normalizaba;
    el cuadro no."""
    from api.services.av_agent_alta import _pct

    assert _pct(68.8575) == "68.86%"
    assert _pct(0.7278 * 100) == "72.78%"
    assert _pct(None) == "—"


def test_el_RESIDUAL_VIVO_sale_del_cronograma_COMPLETO_de_1816():
    """GD46 (medido 2026-08-17 con `scripts/diag_av_agent_flujos`): 1816 manda el
    cuadro ENTERO desde la emisión —51 cupones, el primero de 2021-07, Σ=100— no
    solo lo que falta pagar. Entonces el residual vivo SÍ se deriva del cuadro:
    se descuenta lo ya amortizado y se lee el residual del primer flujo futuro.

    Sin `residual_previo_pct` el motor lo defaulteaba a 100 y la paridad salía
    igual al precio en dólares (68,86% contra 72,78%)."""
    # 44 amortizaciones iguales a partir del cupón 8; hoy van 4 pagadas.
    amort = 100.0 / 44
    cupones = ([{"fechaPagoEfectiva": f"202{k}-07-09", "flujoAmortizacion": 0,
                 "flujoInteres": 0.5625} for k in range(1, 5)]
               + [{"fechaPagoEfectiva": f"20{25 + k}-07-09",
                   "flujoAmortizacion": amort, "flujoInteres": 0.5}
                  for k in range(0, 44)])
    conv = convertir_flujos(cupones, "soberanos")
    assert conv["escala"] == "vn100"
    # El primero que amortiza todavía tiene el nominal entero.
    primero_con_amort = next(f for f in conv["flujos"] if f["amortizacion_pct"])
    assert round(primero_con_amort["residual_previo_pct"], 4) == 100.0
    # Tras 4 amortizaciones el residual vivo es 90,909 — el número de GD46.
    quinto = [f for f in conv["flujos"] if f["amortizacion_pct"]][4]
    assert round(quinto["residual_previo_pct"], 3) == 90.909
    # Y la paridad que sale de ahí es la que cierra contra 1816.
    assert round(68.8575 / 90.909 * 100, 2) == 75.74      # nuestra
    assert round(abs(75.74 - 75.5623) / 75.5623 * 100, 2) == 0.24   # vs su `mep`


def test_la_moneda_del_PEDIDO_y_la_del_COTEJO_son_preguntas_distintas():
    """Un soberano recibe el precio en PESOS (el motor divide por MEP) y devuelve
    la paridad en DÓLARES. Comparar contra la paridad `ars` de 1816 dejaba GD46
    4,07% afuera; contra la de `mep`, 0,24%. Medido, no estimado."""
    from api.services.av_agent_alta import moneda_cotejo_1816, moneda_pedido_1816

    assert moneda_pedido_1816("MERV - XMEV - GD46 - 24hs", "USD") == "ars"
    assert moneda_cotejo_1816("soberanos") == "mep"
    # El dólar-linked pesifica los DOS lados con el mismo TC → el cociente no
    # depende de la moneda, y 1816 no publica `mep` para ellos.
    assert moneda_cotejo_1816("dolar_linked") == "ars"
    assert moneda_cotejo_1816("cer") == "ars"
    assert moneda_cotejo_1816("tasa_fija") == "ars"


def test_la_diferencia_ESPERADA_de_paridad_es_el_INTERES_CORRIDO():
    """BPOA8 (2026-08-17): paridad nuestra 92,91% contra 92,08% de 1816 → 0,90% de
    diferencia y el paso quedaba en «revisar» — con la TEA clavada (7,08% contra
    7,0814%) y la duration también (2,1285 vs 2,1266). No había NADA que revisar.

    Las dos paridades no miden lo mismo: la nuestra es sobre el residual y la de
    ellos sobre el valor técnico (residual + devengado). La nuestra da siempre un
    poco MÁS alta, con techo de un cupón entero — una COTA derivada del cuadro, no
    un umbral a ojo."""
    from api.services.av_agent_alta import cota_devengado

    # Semestral 2,5% sobre 100 de residual → un cupón entero es 2,5%.
    cupones = [_cup("2027-01-15", 0, 2.5), _cup("2027-07-15", 100, 2.5)]
    assert round(cota_devengado(cupones, desde="2026-08-17"), 4) == 2.5
    # Ya amortizado a la mitad: la cota sube porque el residual bajó.
    parcial = [_cup("2027-01-15", 50, 2.5), _cup("2027-07-15", 50, 1.25)]
    assert round(cota_devengado(parcial, desde="2026-08-17"), 4) == 2.5
    # Sin cupones futuros no hay cota que dar — y no se inventa un 0.
    assert cota_devengado(cupones, desde="2030-01-01") is None
    # Un cero cupón no devenga: sin interés no hay cota.
    assert cota_devengado([_cup("2027-01-15", 100, 0)], desde="2026-08-17") is None


def test_la_DURATION_es_el_testigo_del_cronograma():
    """La paridad es invariante a las FECHAS y la duration a la ESCALA: cada una es
    ciega justo donde la otra ve, así que hacen falta las dos. Un cuadro con la
    escala ×1.000 mueve la paridad y NO la duration; uno al que le falta un cupón
    mueve la duration y casi no la paridad."""
    from api.services.av_agent_alta import BLOQUEA, OK, _cotejo_tea

    ref_ok = {"a_nuestro_precio": {"paridad": 0.9208}, "duration": 2.1266}
    # BPOA8 real: 0,90% de diferencia, bajo la cota de un cupón, duration clavada.
    p = _cotejo_tea(0.0708, ref_ok, paridad=92.91, duration=2.1285, cota_ic=2.5)
    assert p["estado"] == OK and "INTERÉS CORRIDO" in p["detalle"]

    # Misma paridad, pero la duration de ellos es otra → el cronograma no es el
    # mismo, y eso BLOQUEA aunque la paridad esté perfecta.
    ref_mal = {"a_nuestro_precio": {"paridad": 0.9208}, "duration": 3.4}
    p2 = _cotejo_tea(0.0708, ref_mal, paridad=92.08, duration=2.1285, cota_ic=2.5)
    assert p2["estado"] == BLOQUEA and "duration no coincide" in p2["detalle"]


def test_el_veredicto_solo_dice_A_MANO_si_hay_algo_que_cargar():
    """El texto decía «se puede aplicar A MANO» ante cualquier `revisar`, incluso
    cuando no faltaba ningún dato — el user preguntó, con razón, qué era lo que
    tenía que aplicar a mano. Un `revisar` CON aviso pide cargar algo; uno sin
    aviso es un juicio."""
    from api.services.av_agent_alta import OK, REVISAR, _paso, _veredicto

    juicio = [_paso("x", "El cuadro coincide con el de 1816", REVISAR, "casi"),
              _paso("y", "Hay precio", OK, "sí")]
    v = _veredicto(juicio)
    assert "A MANO" not in v["texto"] and "no falta ningún dato" in v["texto"]
    assert v["puede_aplicar"] and not v["puede_auto"]

    con_dato = [_paso("cer", "CER de emisión", REVISAR, "falta",
                      aviso="cargar el CER de emisión de TZXA7")]
    v2 = _veredicto(con_dato)
    assert "para cargar a mano" in v2["texto"] and "AVISOS" in v2["texto"]
