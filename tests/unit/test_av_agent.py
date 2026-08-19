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

    Cada tipo caduca por SU razón: un `falta_en_base` lo resuelve que el ticker
    exista; un `sin_flujo` NO —el bono ya estaba en el master— sino que tenga
    CRONOGRAMA. Las dos preguntas se contestan con la misma query."""
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
                return [
                    ("TZXM8", {}),                      # existe, sin cronograma
                    ("TZXD8", {}),                      # ídem
                    # DICP: el user acaba de completarlo → el hallazgo caducó.
                    ("DICP", {"flujos": [{"fecha": "2033-12-31",
                                          "amortizacion_pct": 5.0}]}),
                ]
            return [("falta_en_base", "TZXM8", "r", "media", "m", None),
                    ("falta_en_base", "TZXA7", "r", "media", "m", None),
                    ("sin_flujo", "TZXD8", "r", "alta", "m", None),
                    ("sin_flujo", "DICP", "r", "alta", "m", None)]

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
    assert "TZXD8" in tickers              # existe pero SIGUE sin cronograma
    assert "DICP" not in tickers, (
        "un sin_flujo tiene que caducar cuando el bono YA tiene cronograma — si "
        "no, el user completa el cuadro y la fila se queda ahí, que es "
        "exactamente lo que pasó el 2026-08-17")


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
                return [("TZXM8", {})]
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


def test_el_CER_tipeado_a_mano_GANA_y_deja_de_pedirlo(monkeypatch):
    """El pedido del user (2026-08-17): *«¿no podría ser acá mismo interactivo y
    que me pida el CER de emisión para continuar? Y que rehaga la simulación con
    ese dato y si va todo bien ya lo aplique con eso»*.

    Nuestra serie CER no llega a la fecha de emisión de un bono recién emitido, así
    que `_cer_de_emision` devuelve `None` y el bono nacía SIN TASA: había que
    aplicar a ciegas, ir a AVISOS, cargar el número y recién ahí ver si cerraba.

    El valor tipeado GANA sobre el derivado —lo sacó del prospecto o del BCRA, o
    sea de una fuente que el sistema no tiene— y un valor inválido se ignora en
    vez de romper la simulación."""
    from api.services import av_agent_alta as alta

    # Sin el dato: el paso PIDE el campo y deja aviso.
    p_falta = alta._paso("cer_emision", "CER de emisión resuelto", alta.REVISAR,
                         "la serie no llega", aviso="Cargar el CER",
                         pide={"campo": "cer_emision", "label": "CER de emisión",
                               "tipo": "numero", "ayuda": "…"})
    assert p_falta["pide"]["campo"] == "cer_emision" and p_falta["aviso"]
    # Un paso normal NO pide nada — `pide` es None, no un dict vacío.
    assert alta._paso("x", "y", alta.OK, "z")["pide"] is None

    # El doc simulado lleva el número, que es lo que hace que el motor calcule.
    doc = alta._doc_simulado("TZXA7", type("E", (), {
        "emisor_tipo": "soberano", "moneda": "ARS", "ajuste": "cer",
        "ajuste_alt": "", "ley": ""})(),
        {"flujos": [{"fecha": "2027-04-30", "amortizacion_pct": 100.0,
                     "cupon_sobre_residual": 0.0}], "flujo_vencimiento": None,
         "escala": "vn100", "suma_amort": 100.0, "n": 1, "rama": "cer"},
        "2027-04-30", "MERV - XMEV - TZXA7 - 24hs", 12.3456)
    assert doc["cer_emision"] == 12.3456


def test_el_TZXA7_el_dato_ESTABA_y_el_mensaje_culpaba_a_la_serie():
    """2026-08-17, el user con la captura de `macro.series_macro` en la mano:
    *«JUSTAMENTE TE ESTOY MOSTRANDO QUE PARA ESA FECHA HABÍA»*. Y tenía razón.

    El agente decía *«la serie CER no llega hasta 2025-11-28 (T−10 hábiles)»* con
    el CER de esa semana presente en la base. Dos errores encadenados:

    1. **El mensaje mentía la fecha**: interpolaba la fecha de EMISIÓN y la
       etiquetaba «(T−10 hábiles)», nombrando un día que nunca se buscó. El T−10
       real de una emisión del 2025-11-28 es el **2025-11-12** — y ESE estaba
       cargado (651,898…).
    2. `get_cer_liquidacion` resuelve el T−10 **indexando `mercado.dias_habiles`**
       y devuelve `None` cuando esa tabla no llega diez hábiles atrás. El llamador
       leía ese `None` como «no hay CER»: una conclusión que la función nunca
       afirmó.

    Este test congela la aritmética, que es lo único que no puede cambiar."""
    from datetime import date

    from core.calendario import es_habil, restar_habiles

    emision = date(2025, 11, 28)
    assert es_habil(emision)
    objetivo = restar_habiles(emision, 10)
    assert objetivo == date(2025, 11, 12), "el T−10 hábiles NO es la fecha de emisión"
    assert es_habil(objetivo)
    # No es «10 días corridos»: entre medio hay 2 findes y 2 feriados AR.
    assert (emision - objetivo).days == 16
    # Y es PURO: no depende de que `mercado.dias_habiles` cubra la fecha, que es
    # justo lo que fallaba.
    assert restar_habiles(date(2019, 3, 5), 10) == date(2019, 2, 18)
    assert restar_habiles(emision, 0) == emision


def test_el_NO_ME_INTERESA_vale_para_TODOS_los_tipos():
    """2026-08-17: *«¿cómo podríamos hacer para ignorar algunos, tipo decir "no me
    interesan", así no vuelven a aparecer?»*.

    El mecanismo existía pero (a) solo se disparaba contestando una PREGUNTA del
    agente y (b) el filtro se pasaba por parámetro **solo a `detectar_faltantes`**:
    los otros tres detectores seguían reportando un ticker ya descartado.

    Ahora el filtro se aplica UNA vez, sobre la lista completa — que es lo que hace
    imposible que un detector nuevo se olvide de mirarlo."""
    import inspect

    from api.services import av_agent

    fuente = inspect.getsource(av_agent.relevar)
    # El filtro está DESPUÉS de armar la lista, no adentro de un detector.
    assert 'if (h.get("ticker") or "").strip().upper() not in ignorados' in fuente
    # Y hay UN solo lector de la lista, que comparten relevar y la lectura.
    assert callable(av_agent.tickers_ignorados)


def test_completar_flujos_NO_puede_pisar_los_ejes_del_bono():
    """El hallazgo `flujos_vacios` se vuelve accionable (E3.a). Es el alta al
    revés: el bono YA existe y **sus ejes los cargó la mesa**, así que son la
    verdad y no se derivan de nuevo.

    La garantía no es «tener cuidado»: el UPDATE es un merge sobre el blob con un
    parche que **solo contiene el cronograma** — emisor, curva, símbolo y ejes ni
    siquiera están en el payload, así que no se pueden pisar.

    La ÚNICA excepción es `cer_emision`, y es explícita: se escribe solo cuando el
    user lo tipeó en la cadena Y el bono no lo tenía (`cer_manual`). Sin ese número
    la rama `cer` del motor sale por su puerta de emergencia y el bono queda con
    cronograma y sin tasa — la mitad del trabajo. Que sea una excepción NO la hace
    un agujero: el guard es lo que la mantiene incapaz de pisar un valor cargado."""
    import inspect

    from api.services import av_agent_alta

    fuente = inspect.getsource(av_agent_alta.aplicar_flujos)
    assert 'parche: dict = {"flujos": conv["flujos"]}' in fuente
    assert "|| %s::jsonb" in fuente, "tiene que MERGEAR el blob, no reemplazarlo"
    for prohibido in ("emisor", "moneda_eje", "curva"):
        assert f'parche["{prohibido}"]' not in fuente
    # `cer_emision` sí puede escribirse, pero SOLO detrás del guard de «lo tipeó el
    # user y el bono no lo tenía». Si alguien saca ese `if`, este test cae.
    assert ('if sim.get("cer_manual") and sim.get("cer_emision"):\n'
            '        parche["cer_emision"] = sim["cer_emision"]') in fuente, (
        "cer_emision quedó sin el guard de cer_manual — así SÍ puede pisar "
        "el valor que cargó la mesa")
    # Y `cer_manual` solo es True si el doc NO lo traía: la otra mitad del candado.
    sim_src = inspect.getsource(av_agent_alta.simular_flujos)
    assert 'cer_manual and not doc.get("cer_emision")' in sim_src

    # Y la rama sale del DOC, no de una curva de 1816 traducida de nuevo.
    sim = inspect.getsource(av_agent_alta.simular_flujos)
    assert "rama = rama_calculo(doc)" in sim
    # Un bono que YA tiene cronograma no se re-escribe: el hallazgo quedó viejo.
    assert "YA tiene cronograma" in sim


def test_la_ACCION_se_mapea_por_TIPO_y_no_por_REGLA():
    """El botón COMPLETAR CRONOGRAMA no aparecía y **no daba ningún error**: el
    front comparaba `h.tipo === "flujos_vacios"`, y `flujos_vacios` no es el TIPO
    sino la REGLA — el tipo es `sin_flujo`.

    Un string mágico copiado a mano en la otra punta del sistema falla exactamente
    así: en silencio. Ahora la acción la decide el BACKEND (`ACCION_POR_TIPO`) y
    viaja en el hallazgo; el front solo lee `h.accion`.

    Este test congela lo que el front no puede verificar: que las claves del mapa
    sean TIPOS que los detectores realmente emiten."""
    from api.services import av_agent

    # Los tipos que emite cada detector, tomados de sus propias llamadas.
    # `salud` es el quinto (fusión 2026-08-17): lo emite `detectar_salud`, que
    # convierte un chequeo de SALUD en un hallazgo del agente.
    # `sin_precio` y `precio_moneda` son los de RUEDA (2026-08-18): los emite
    # `relevar_live`, que corre cada 5 minutos con el mercado abierto y busca lo
    # que de noche no existe (un símbolo sin suscribir, un precio en la moneda
    # equivocada).
    tipos_reales = {"falta_en_base", "sin_flujo", "tasa_sospechosa",
                    "hueco_de_curva", "salud", "sin_precio", "precio_moneda"}
    assert set(av_agent.ACCION_POR_TIPO) <= tipos_reales, (
        "una clave del mapa no es un TIPO que algún detector emita — "
        "probablemente se escribió la REGLA")
    assert av_agent.ACCION_POR_TIPO["sin_flujo"] == "flujos"
    assert av_agent.ACCION_POR_TIPO["falta_en_base"] == "alta"
    assert av_agent.ACCION_POR_TIPO["tasa_sospechosa"] == "arreglo"
    # **CADA TIPO DECLARA SU ACCIÓN** (2026-08-17). Congelarlo tiene un
    # sentido concreto: un detector nuevo que emita un tipo sin acción sale como
    # comentario en la pantalla y **nadie se entera** —no falla nada, solo no se
    # puede hacer nada con él—, que es exactamente lo que le pasó a
    # `tasa_sospechosa` durante 38 filas. Si agregás un tipo, o le ponés acción o
    # cambiás este test a propósito.
    assert set(av_agent.ACCION_POR_TIPO) == tipos_reales, (
        "hay un tipo de hallazgo sin acción: va a aparecer en la lista como un "
        "comentario que nadie puede accionar, sin dar ningún error")
    # `flujos_vacios` es la REGLA de `sin_flujo`: nunca puede ser una clave.
    assert "flujos_vacios" not in av_agent.ACCION_POR_TIPO


def test_el_detector_de_sin_flujo_emite_tipo_sin_flujo_y_regla_flujos_vacios():
    """La distinción TIPO / REGLA, congelada donde nace: el tipo agrupa la
    sección de la pantalla, la regla dice cuál chequeo la disparó. Confundirlas
    es lo que rompió el botón."""
    docs = [{"ticker_corto": "DICP", "curva": "cer", "flujos": [],
             "emisor": "Argentina", "moneda_flujo": "ARS"}]
    hs = av_agent.detectar_sin_flujo(docs, {"DICP": {}})
    assert len(hs) == 1
    assert hs[0]["tipo"] == "sin_flujo"
    assert hs[0]["regla"] == "flujos_vacios"


def test_EL_CASO_DICP_un_doc_de_curvas_NO_es_el_blob_data():
    """DICP y PARP (2026-08-17). La primera simulación de un `flujos_vacios` real
    devolvió duration 7,3781 y 12,3808 **sin TEA** — que son, clavados, sus días al
    vencimiento sobre 365: el valor que devuelve el motor cuando sale por su puerta
    de emergencia (`if not cer_emision: duration = dias/365; return`).

    La causa no estaba en el cuadro de 1816 sino en cómo se leía el bono: un
    `SELECT data` trae el blob, pero los EJES viven en COLUMNAS y `core/curvas_sql`
    los mezcla encima (`_COLS_FUERA_DEL_BLOB`). Sin ellos `ajuste` es `None`, el
    bono deja de ser CER para el simulador y **todo el resto miente en cascada**
    (el encabezado mandaba a revisar la escala del cuadro, que estaba perfecta).

    Este test congela la regla general: **el simulador lee la MISMA fuente que el
    motor**. Dos lectores del mismo dato son dos verdades que se separan solas."""
    import inspect

    from api.services import av_agent_alta

    fuente = inspect.getsource(av_agent_alta._doc_de_curvas)
    assert "curvas_sql" in fuente, (
        "volvió a leer la tabla por su cuenta — los ejes viven en columnas y un "
        "SELECT data los deja en None")
    assert "SELECT data" not in fuente


def test_los_cupones_YA_PAGADOS_se_guardan_y_la_cadena_lo_DICE():
    """1816 manda el cronograma COMPLETO desde la emisión (medido en GD46: 51
    cupones desde 2021-07-12, Σ=100,000012), así que un bono de 2004 trae decenas
    de cupones ya cobrados. Guardarlos es lo correcto —el cuadro es el DEL BONO, no
    el de hoy— y el motor ya los filtra al valuar (`fecha_flujo(f) >
    fecha_settlement`, la misma regla en las cuatro ramas).

    Lo que faltaba no era el filtro: era **decirlo**. Ver «60 cupones» sin ninguna
    aclaración da a entender que se valúan los 60. La cadena ahora parte el número
    en pagados y futuros, y si no queda NINGUNO futuro BLOQUEA: un bono que ya
    venció no tiene nada que valuar y escribirle el cuadro no lo arregla."""
    import inspect

    from api.services import av_agent_alta
    from engines import curvas as motor

    # (1) El motor filtra — no es una promesa del agente, está en el motor.
    fuente_motor = inspect.getsource(motor)
    assert fuente_motor.count("fecha_flujo(f) > fecha_settlement") >= 3, (
        "alguna rama del motor dejó de filtrar los cupones ya pagados")

    # (2) La cadena lo explica y cuenta las dos mitades.
    ch = inspect.getsource(av_agent_alta._chequeos_flujos)
    assert 'pagados = conv["n"] - futuros' in ch
    assert "ya se pagaron" in ch and "valúa solo los futuros" in ch
    # (3) Sin cupones futuros no se aplica: BLOQUEA, no «revisar».
    assert "OK if futuros else BLOQUEA" in ch


def test_EL_500_DE_PARP_toda_llamada_al_LIBRO_tiene_que_entrar_en_su_firma():
    """PARP se aplicó y devolvió **HTTP 500 sin llegar a QUÉ HIZO** (2026-08-17).

    La causa: `aplicar_flujos` llamaba a `acc.registrar(..., tabla="mercado.curvas")`
    y `registrar()` **no tiene** ese parámetro — la tabla la resuelve él solo por la
    ACCIÓN (`DESTINOS`), justamente para que la misma acción no se anote con dos
    destinos según quién la llame. `TypeError` **después** de que el UPDATE ya había
    commiteado: el cronograma quedó escrito y el user vio un 500 pelado.

    Lo peligroso no es el typo: es DÓNDE cae. El libro de acciones está diseñado
    para no romper nunca la acción (decisión 3 del módulo) y lo cumple para
    cualquier fallo de la BASE — pero un `TypeError` pasa ANTES de entrar a su
    `try`, así que ese blindaje no lo cubre. Se congela desde afuera: **toda
    llamada a `registrar` tiene que entrar en su firma, y toda `accion=` tiene que
    ser una clave de `DESTINOS`** (si no, el libro anota destino «?» — no falla,
    solo deja de decir dónde escribió)."""
    import ast
    import inspect
    import pathlib

    from api.services import av_agent_acciones as acc

    firma = set(inspect.signature(acc.registrar).parameters)
    raiz = pathlib.Path(av_agent_acciones_dir())
    revisadas = 0
    for py in sorted(raiz.glob("av_agent*.py")):
        arbol = ast.parse(py.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            fn = nodo.func
            nombre = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if nombre != "registrar":
                continue
            kw = {k.arg for k in nodo.keywords if k.arg}
            sobra = kw - firma
            assert not sobra, (
                f"{py.name}: registrar(...) recibe {sorted(sobra)}, que no está en su "
                f"firma → TypeError en runtime, y en la ruta de aplicar cae DESPUÉS "
                f"de escribir en la base")
            for k in nodo.keywords:
                if k.arg == "accion" and isinstance(k.value, ast.Constant):
                    revisadas += 1
                    assert k.value.value in acc.DESTINOS, (
                        f"{py.name}: la acción «{k.value.value}» no está en DESTINOS "
                        f"→ el libro la anota con destino «?»")
    assert revisadas >= 8, "el walk no encontró las llamadas — ¿se movió el módulo?"


def av_agent_acciones_dir() -> str:
    import pathlib

    from api.services import av_agent_acciones
    return str(pathlib.Path(av_agent_acciones.__file__).parent)


def test_EL_CASO_DICP_el_divisor_del_cuadro_CER_es_el_RATIO_no_la_suma():
    """DICP dio TEA 3,91% contra 9,25% de 1816 y PARP dio EXACTO. Medido, la
    diferencia estructural es una sola: **PARP no amortizó nada todavía** (sus 20
    cuotas arrancan en 2029) y DICP amortiza desde 2024.

    La causa: 1816 manda cada flujo **en pesos ajustados por el CER de su propia
    fecha** — los pasados en pesos de cuando se pagaron, los futuros en pesos de
    hoy. Nuestro divisor era la Σ del cuadro COMPLETO, o sea **sumar pesos de 2024
    con pesos de 2026**. Con las cifras reales de prod:

        PARP  Σ/ratio = 100,0025 → cada amortización 5,000126%   ← de manual
        DICP  Σ/ratio = 118,3040 → pero las 20 valdrían 126,9969
                                    (6,8% menos = la inflación de lo ya pagado)

    Y contra los indicadores de 1816 al MISMO precio:

        ÷ Σ (lo viejo)        TEA  3,9132%  dur 3,4830
        ÷ residual futuro     TEA 10,8880%  dur 3,1928
        ÷ ratio de CER        TEA  9,2268%  dur 3,2591
        1816                  TEA  9,2475%  dur 3,2512

    Este test congela las dos mitades: que con ratio el cuadro queda en % del VN
    ORIGINAL, y que **para un bono sin amortizar el cambio es la identidad** — que
    es la garantía de que PARP y los 24 CER intactos del master no se movieron."""
    from api.services.av_agent_alta import convertir_flujos

    # ── PARP sintético: 4 cuotas de 25%, TODAS futuras, CER de hoy (ratio 561,42)
    ratio = 561.4192
    parp = [_cup(f, 25 * ratio, 1.0 * ratio) for f in
            ("2029-03-31", "2029-09-30", "2030-03-31", "2030-09-30")]
    con_ratio = convertir_flujos(parp, "cer", ratio_cer=ratio)
    sin_ratio = convertir_flujos(parp, "cer")
    assert con_ratio["divisor_es"] == "ratio_cer"
    assert sin_ratio["divisor_es"] == "suma"
    for a, b in zip(con_ratio["flujos"], sin_ratio["flujos"], strict=True):
        assert abs(a["amortizacion_pct"] - b["amortizacion_pct"]) < 1e-9, (
            "sin amortizaciones pasadas las dos normalizaciones TIENEN que dar lo "
            "mismo — si no, este cambio movería los 24 CER intactos del master")
        assert abs(a["residual_previo_pct"] - b["residual_previo_pct"]) < 1e-9
    assert abs(con_ratio["suma_pct"] - 100.0) < 1e-6

    # ── DICP sintético. Son DOS cosas a la vez, y las dos las arregla el ratio:
    #
    #   (1) CAPITALIZÓ interés → el total a amortizar NO es 100 del VN original
    #       sino 125 (5 cuotas de 25). Dividir por Σ lo fuerza a 100 igual.
    #   (2) las 2 cuotas YA PAGADAS vienen en pesos de SU fecha (CER × 0,7), así
    #       que Σ ni siquiera es un número en una sola unidad.
    #
    # Σ = 2×25×0,7 + 3×25 = 110 (en unidades de ratio) contra el divisor correcto,
    # que es 100 → las cuotas futuras se encogen a 22,73 en vez de 25. Esa es la
    # dirección del error real de DICP (Σ/ratio = 118,30 contra 100).
    entonces = ratio * 0.7
    dicp = ([_cup(f, 25 * entonces, 1.0 * entonces)
             for f in ("2024-06-30", "2024-12-31")]
            + [_cup(f, 25 * ratio, 1.0 * ratio)
               for f in ("2027-06-30", "2027-12-31", "2028-06-30")])
    r = convertir_flujos(dicp, "cer", ratio_cer=ratio)
    # Con el ratio, cada cuota FUTURA vale sus 25% del VN original.
    for f in r["flujos"][2:]:
        assert abs(f["amortizacion_pct"] - 25.0) < 1e-6
    # Y la Σ deja de ser una constante decorativa: dice que el bono capitalizó.
    assert abs(r["suma_pct"] - 110.0) < 1e-6

    # El bug: por Σ las cuotas futuras se encogen ~9% y la TEA se desploma **sin
    # dar ningún error** — DICP mostraba 3,91% contra 9,25%.
    malo = convertir_flujos(dicp, "cer")
    assert abs(malo["flujos"][2]["amortizacion_pct"] - 25 / 110 * 100) < 1e-6
    assert malo["flujos"][2]["amortizacion_pct"] < 23.0


def test_el_CER_de_emision_pasa_a_ser_BLOQUEANTE_en_completar_flujos():
    """Antes era «revisar»: se escribía el cuadro y el CER quedaba pendiente. Con
    el divisor por ratio eso ya no se sostiene — **sin el CER de emisión el cuadro
    ni siquiera se puede convertir**, porque el número que lleva los pesos de 1816
    a % del VN es `CER de hoy / CER de emisión`. Escribir igual dejaría un
    cronograma en una escala inventada, que es peor que no tener cronograma: se
    ve cargado y valúa mal."""
    import inspect

    from api.services import av_agent_alta

    ch = inspect.getsource(av_agent_alta._chequeos_flujos)
    assert 'ps.append(_paso("cer_emision", "CER de emisión resuelto",\n' \
           '                        OK if cer_e else BLOQUEA,' in ch
    assert '_paso("divisor"' in ch

    # **UNA CAUSA, UN SOLO BLOQUEO.** Si falta el CER, el paso del DIVISOR y el
    # del CER se ponían rojos por lo MISMO: dos alarmas para un dato, y el user
    # leyendo «2 pasos lo bloquean» cuando hay UNA casilla que llenar. El divisor
    # frena solo cuando el CER ESTÁ y aun así no se pudo armar el ratio (un hueco
    # en la serie) — que es un problema distinto y del sistema, no del user.
    assert 'OK if por_ratio else (NO_SE if falta_cer else BLOQUEA)' in ch
    assert 'falta_cer = not doc.get("cer_emision")' in ch


def test_el_ratio_de_CER_sale_de_las_MISMAS_funciones_que_el_motor():
    """El invariante que sostiene todo esto es que `monto_flujo_cer(f) × ratio`
    reproduzca el importe en pesos que publica 1816. Si el agente calculara el
    ratio con otra fuente o con otro lag, el cuadro guardado dejaría de reproducir
    el de ellos **y nadie se enteraría** — es la misma familia de bug que leer el
    doc por `SELECT data` en vez de por `curvas_sql`."""
    import inspect

    from api.services import av_agent_alta

    fuente = inspect.getsource(av_agent_alta.ratio_cer_hoy)
    for fn in ("cargar_cer", "cargar_dias_habiles", "get_cer_liquidacion",
               "siguiente_dia_habil"):
        assert fn in fuente, f"{fn} es la función del motor y tiene que usarse acá"
    assert "n=10" in fuente, "el lag T−10 hábiles es el del motor, no se re-elige"


def test_EL_CASO_DICP_dos_la_TEA_al_mismo_precio_mas_la_duration_son_una_PRUEBA():
    """Con el divisor arreglado, DICP volvió así:

        TEA (al MISMO precio)  nuestra 9,2475%  ·  1816 9,2475%   → 0 bps
        duration               nuestra 3,2512   ·  1816 3,2512    → 0,00%
        paridad                nuestra 86,57%   ·  1816 90,19%    → 4,01%

    …y la cadena **BLOQUEABA** diciendo «se contradicen → otro valor técnico, o
    sea otro cronograma». Eso era **aritméticamente imposible**: la TEA a un
    precio dado es una función del cronograma y de las fechas, así que dos
    cuadros distintos no pueden dar la misma tasa al mismo precio Y la misma
    duration hasta el cuarto decimal.

    El error era de JERARQUÍA. La paridad era el juez y la duration el testigo,
    cuando la paridad es lo único de los tres que depende de una DEFINICIÓN
    (qué precio va arriba y qué va abajo) y no solo de los flujos.

    Lo que difería: 1816 divide su precio **CLEAN** por el valor técnico; nosotros
    dividimos el que **OPERA** —su `precioDirty`, que para DICP es exactamente
    nuestro 48.600— por VN × ratio. La cuenta cierra:

        DICP  86,57 × (50.589,80 / 48.600) = 90,11  contra 90,19  → 0,08%
        PARP  63,77 × (35.419,08 / 35.800) = 63,09  contra 63,34  → 0,25%

    O sea que el valor técnico de ellos y el nuestro **son el mismo número**.

    Este test corre el cotejo con los datos REALES de DICP y exige que no
    bloquee, que reconozca la prueba, y que muestre la reconciliación."""
    from api.services.av_agent_alta import _cotejo_tea

    ref = {
        "paridad": 0.9018683589274514, "tea": 0.09247520032936829,
        "duration": 3.251214950900394,
        "precio": 48600.0, "precio_clean": 50589.7956,
        # `a_nuestro_precio`: su fórmula sobre NUESTRO número (input manual).
        "a_nuestro_precio": {"paridad": 0.9018683589274514,
                             "tea": 0.09247520032936829,
                             "convencion_tna": "180-360"},
    }
    p = _cotejo_tea(0.092475, ref, paridad=86.5677, duration=3.2512,
                    cota_ic=2.9, precio=48600.0)
    assert p["estado"] == "ok", (
        f"con TEA 0 bps y duration 0,00% no puede bloquear: {p['detalle']}")
    assert "no hay dos cuadros distintos" in p["detalle"]
    assert "90.11%" in p["detalle"] and "0.08%" in p["detalle"], (
        "tiene que MOSTRAR la reconciliación, no afirmarla: quien audita la rehace")

    # Y la contraprueba: si la duration NO coincide, sigue bloqueando. La prueba
    # necesita las DOS patas — si no, sería una puerta para colar cualquier cuadro.
    malo = _cotejo_tea(0.092475, {**ref, "duration": 6.5},
                       paridad=86.5677, duration=3.2512, cota_ic=2.9, precio=48600.0)
    assert malo["estado"] == "bloquea"


def test_un_sin_flujo_CADUCA_cuando_el_bono_ya_tiene_cronograma():
    """DICP se completó, el libro de acciones lo registró, la fila decía «✔
    CRONOGRAMA ESCRITO» **y el hallazgo seguía en la lista** (2026-08-17).

    Es la CUARTA vez que aparece el mismo patrón —TZXM8, BADLAR, IGNORAR, y ahora
    esto— y la regla ya estaba escrita en el doc: *todo criterio que decida si algo
    se MUESTRA tiene que poder evaluarse en la LECTURA*. La lista es una FOTO de la
    última corrida y relevar cuesta ~29 créditos, así que lo que se arregla entre
    corridas hay que TACHARLO al leer.

    Lo que fallaba acá era distinto de las veces anteriores: el criterio no faltaba
    por olvido, estaba **descartado por escrito** con un comentario que decía «no
    hay forma barata de saber si se arreglaron sin rehacer la corrida». Era falso:
    el cronograma vive en el mismo `mercado.curvas` que la función ya lee, así que
    es CERO queries extra — solo una columna más en la que ya estaba.

    Este test congela las dos mitades: que el tipo caduque, y que lo haga con
    `tiene_flujo_def` —**el mismo predicado que lo DETECTA**— y no con un
    `bool(flujos)` propio, que marcaría distinto a una LECAP zero-coupon."""
    import inspect

    from api.services import av_agent_vista

    fuente = inspect.getsource(av_agent_vista._hallazgos_ultima_corrida)
    assert 'if h["tipo"] == "sin_flujo":' in fuente, (
        "el tipo `sin_flujo` volvió a no caducar: un bono ya completado sigue "
        "apareciendo como pendiente")
    assert "tiene_flujo_def" in fuente, (
        "tiene que usar el MISMO predicado que el detector — un bool(flujos) "
        "propio caducaría una LECAP por un motivo distinto del que la marcó")
    # Y sin pagar un viaje más a la base: la columna sale de la query que ya estaba.
    assert fuente.count("cur.execute") == 3, (
        "se agregó una query: el peaje de Supabase se paga por VIAJE — el doc "
        "tiene que venir en el mismo SELECT que ya traía el ticker")
    assert "SELECT ticker, data FROM mercado.curvas" in fuente

    # `tasa_sospechosa` NO caduca, y es a propósito: depende del precio del día.
    assert "tasa_sospechosa` habla de la TASA" in fuente


def test_ARREGLAR_solo_pisa_si_la_propuesta_coincide_Y_lo_de_hoy_NO():
    """El último tipo que era solo un comentario: `tasa_sospechosa` (38 filas,
    tres síntomas — `sin_ejes`, `sin_tea_con_precio`, `paridad_fuera_de_rango`
    con valores como 156.570% en LOC6O y 0,0% en PECKO).

    Los tres son el MISMO problema visto de tres lados: un INSUMO está mal —los
    ejes o la escala del cuadro— y el motor no puede avisar porque no tiene con
    qué comparar. Nosotros sí: 1816 publica la curva del bono y su cronograma.

    **Pero esta puerta es distinta de las otras dos y necesita otra garantía.** El
    alta escribe un bono que no existe y completar-cronograma llena un campo
    vacío: lo peor que puede pasar es no mejorar nada. Acá se PISA un dato que ya
    está, así que la regla de aplicación tiene DOS mitades:

        se aplica solo si la propuesta coincide con 1816 **y lo de hoy NO**

    Sin la primera se pisaría con algo no verificado. Sin la segunda se pisaría un
    bono que YA estaba bien —el hallazgo pudo quedar viejo, o el umbral pudo ser
    angosto para ese instrumento— y eso es estrictamente peor que no hacer nada.

    Este test congela las dos mitades y el detalle que las hace posibles: que el
    ANTES y el DESPUÉS se juzguen con **la misma función** (`_cotejo_tea`), porque
    dos varas distintas harían que «mejoró» dependa de cuál se aplicó a cuál."""
    import inspect

    from api.services import av_agent, av_agent_alta

    assert av_agent.ACCION_POR_TIPO["tasa_sospechosa"] == "arreglo"

    ch = inspect.getsource(av_agent_alta._chequeos_arreglo)
    # (1) La propuesta se coteja contra 1816.
    assert 'cot_prop["clave"], cot_prop["titulo"] = "cotejo_propuesto"' in ch
    # (2) Y lo de HOY también — y si YA coincide, BLOQUEA.
    assert "ya_estaba_bien = cot_hoy[\"estado\"] == OK" in ch
    assert "BLOQUEA if ya_estaba_bien else OK" in ch
    assert "pisarlo sería empeorarlo" in ch
    # (3) La MISMA vara para los dos: un solo helper, invocado dos veces.
    assert ch.count("_cotejo_de(") == 2
    assert "_cotejo_tea" in inspect.getsource(av_agent_alta._cotejo_de)

    # Y los EJES se escriben en COLUMNAS, no en el blob: `_COLS_FUERA_DEL_BLOB`
    # se mergea ENCIMA del jsonb, así que un eje escrito solo en `data` lo pisa
    # el NULL de la columna y queda invisible para el motor y para la vista.
    ap = inspect.getsource(av_agent_alta.aplicar_arreglo)
    assert 'sets.append(f"{col} = %s")' in ap
    assert '"emisor_tipo", "moneda_eje", "ajuste", "ajuste_alt", "ley"' in ap
    assert "|| %s::jsonb" in ap, "el blob se MERGEA, no se reemplaza"
    # El ANTES queda congelado en el libro: sin eso, «revertir» es una promesa.
    assert "antes=antes" in ap


def test_los_ejes_propuestos_salen_de_la_CURVA_que_1816_publica():
    """Un `sin_ejes` no se arregla adivinando: 1816 dice en qué curva está el bono
    (`research.mkt_1816_instrumentos.curva`) y `curvas_ejes.desde_1816` la
    traduce — la MISMA función que usa el alta. El campo no se estaba trayendo,
    así que la propuesta no tenía de dónde salir."""
    import inspect

    from api.services import av_agent_alta

    ficha = inspect.getsource(av_agent_alta._ficha_1816)
    assert "curva" in ficha and '"curva_1816": r[7]' in ficha

    sim = inspect.getsource(av_agent_alta.simular_arreglo)
    assert "curvas_ejes.desde_1816(curva_1816)" in sim
    # **Solo si FALTAN.** Los ejes que cargó la mesa son la verdad y no se
    # discuten — misma regla que en completar-cronograma.
    assert "if ejes_actuales is None:" in sim


def test_el_DIAGNOSTICO_le_pregunta_a_1816_UNA_SOLA_VEZ():
    """El «auth HTTP 429» que rompió la pantalla **y los cuatro jobs de 1816 a la
    vez** (2026-08-17) no era un problema de autenticación: era que el botón
    DIAGNOSTICAR, recién estrenado, golpeaba demasiado.

    `simular_arreglo` corre el motor DOS veces —el bono de HOY y la propuesta— y
    **cada corrida salía a pedir su propia referencia de precio a 1816**. Es la
    MISMA pregunta sobre el MISMO bono: ese precio no cambia entre «cómo está» y
    «cómo quedaría». Con el retroceso de ruedas (hasta 5 intentos) más el cotejo
    al mismo precio, un click llegaba a ~13 requests con la mitad duplicados;
    probar seis bonos seguidos alcanzó para que 1816 nos aplicara el rate limit a
    **todo**, endpoint de login incluido — de ahí que el mensaje dijera «auth».

    Compartir la referencia no es solo la mitad de costo: **es lo correcto**. Los
    dos estados quedan juzgados con la MISMA vara, que es el principio que ya rige
    el cotejo (E3.j).

    Se congela el CONTEO porque es la única forma de que esto no vuelva: nada en
    el código impide que alguien agregue una tercera corrida del motor y con ella
    una tercera consulta, y el síntoma tardaría días en aparecer."""
    import inspect

    from api.services import av_agent_alta

    src = inspect.getsource(av_agent_alta.simular_arreglo)
    # Tres corridas del motor y no dos desde que existe el diagnóstico local
    # (2026-08-17) — pero **la tercera no toca la red**: va con `sin_red=True`, que
    # es lo que este test tiene que custodiar. El número solo era un proxy de
    # "cuántas veces se le pregunta a 1816"; lo que importa es esa pregunta.
    assert src.count("_simular_tasa(") == 3, "el diagnóstico compara DOS estados"
    assert src.count("sin_red=True") == 1, (
        "la corrida del diagnóstico local NO puede salir a la red: es justo la que "
        "tiene que andar con 1816 caído")
    assert src.count("_referencia_1816(") == 1, (
        "la referencia de 1816 se pide UNA sola vez y se comparte — dos consultas "
        "para la misma pregunta es lo que nos ganó el rate limit")
    assert src.count("mercado_1816.cashflow(") == 1
    assert "ref_1816=ref_unica" in src

    # Y el orden importa: la moneda con la que se le pregunta a 1816 sale de los
    # EJES, así que la consulta va DESPUÉS de resolverlos. Con los ejes vacíos
    # —justo el caso `sin_ejes`— la pregunta saldría mal formulada.
    assert src.index("doc_prop = dict(doc)") < src.index("ref_unica = _referencia_1816")


def test_las_puertas_del_agente_tienen_PRESUPUESTO_de_tiempo():
    """Detrás de Cloudflare hay un reloj de 100s. Al ponerle backoff a `_auth`
    contra el 429, una simulación pasó a poder tardar minutos — y el resultado no
    fue lentitud sino un **HTTP 524**: el proxy corta, la respuesta se pierde y el
    usuario ve un error que no dice nada.

    La paciencia correcta depende de QUIÉN espera: un cron aguanta minutos, un
    click no. Por eso el presupuesto es un contextvar declarado en las puertas del
    agente —las únicas interactivas— y no una constante del módulo."""
    import inspect

    from api.services import av_agent_alta
    from core import mercado_1816

    assert hasattr(mercado_1816, "presupuesto")
    src = inspect.getsource(av_agent_alta)
    # Las SEIS puertas públicas del agente lo llevan.
    assert src.count("@_interactivo\ndef ") == 6, (
        "alguna puerta del agente quedó sin presupuesto de tiempo: si 1816 se "
        "pone lento, esa devuelve un 524 en vez de un error que se entiende")
    # Y los jobs NO: su paciencia larga es correcta, nadie los está mirando.
    assert "_interactivo" not in inspect.getsource(mercado_1816)


def test_el_DIAGNOSTICO_LOCAL_no_depende_de_1816():
    """*«No entiendo qué tiene que ver 1816 si esto ya existe, ya tenemos precio y
    flujo»* (user, 2026-08-17). Tenía razón, y era la falla de diseño de la puerta
    ARREGLO: nació consultando a 1816 en la PRIMERA instancia, así que cuando 1816
    no está —rate limit, caída, un bono que no cubre— no decía **nada**, ni
    siquiera lo que se deduce de una división.

    La paridad ES `precio / residual`. Si el resultado se va de escala, uno de los
    dos lados está en la unidad equivocada, y **cuál de los dos se sabe mirando el
    residual**: un cuadro sano lo tiene cerca de 100 (el bono cotiza por 100 de
    VN). Con los números REALES de producción, los mismos datos dan diagnósticos
    OPUESTOS:

        OLC3O  paridad 0,06%      residual 166.000  → EL CUADRO (nominales)
        RC1CO  paridad 167.830%   residual 100      → EL PRECIO (otra moneda)
        DHSGO  rama tamar                           → no lo valuamos nosotros

    Ninguno necesita la red. 1816 queda donde corresponde: para CONFIRMAR y para
    traer el cuadro de reemplazo, no para poder abrir la boca."""
    from datetime import date as _date
    from datetime import timedelta

    from api.services.av_agent_alta import _diagnostico_local, _residual_vivo

    fut = (_date.today() + timedelta(days=200)).isoformat()

    # OLC3O — el cuadro está en NOMINALES DE LA EMISIÓN.
    doc = {"flujos": [{"fecha": fut, "amortizacion": 166000.0}], "ajuste": "dolar_linked"}
    ps = _diagnostico_local(doc, "on", {"paridad": 0.06, "precio": 137280.0})
    txt = " ".join(p["detalle"] for p in ps)
    assert "NOMINALES DE LA EMISIÓN" in txt
    assert "EL CUADRO" in txt and "EL PRECIO" not in txt.split("→")[-1]

    # RC1CO — el cuadro está BIEN; el que está fuera de escala es el precio.
    doc2 = {"flujos": [{"fecha": fut, "amortizacion": 100.0}], "ajuste": "fija"}
    ps2 = _diagnostico_local(doc2, "on", {"paridad": 167830.0, "precio": 167830.0})
    txt2 = " ".join(p["detalle"] for p in ps2)
    assert "base 100, como corresponde" in txt2
    assert "EL PRECIO" in txt2

    # DHSGO — un TAMAR no lo valuamos nosotros: el hallazgo no se arregla acá.
    ps3 = _diagnostico_local({"ajuste": "tamar"}, "otros", {})
    assert "no la calculamos nosotros" in ps3[0]["detalle"]

    # El residual sale con los MISMOS accesores del motor y solo cuenta FUTUROS.
    doc3 = {"flujos": [{"fecha": "2001-01-01", "amortizacion": 999.0},
                       {"fecha": fut, "amortizacion": 50.0}]}
    assert _residual_vivo(doc3, "on") == (50.0, 1)


def test_el_diagnostico_LOCAL_va_PRIMERO_en_la_cadena():
    """Va antes que cualquier paso que toque 1816, y por una razón concreta: es
    exactamente cuando 1816 NO responde que hace falta. Si fuera después, un rate
    limit lo dejaría fuera de la pantalla."""
    import inspect

    from api.services import av_agent_alta

    ch = inspect.getsource(av_agent_alta._chequeos_arreglo)
    assert "ps.extend(_diagnostico_local(" in ch
    assert ch.index("_diagnostico_local(") < ch.index('_paso("cuadro"')
    assert ch.index("_diagnostico_local(") < ch.index("cot_prop")


# ── `moneda_flujo`: el vocabulario que la migración a ejes dejó atrás ────────


def test_moneda_flujo_esperada_son_las_TRES_puertas_del_motor():
    """`moneda_flujo_esperada` tiene que devolver EXACTAMENTE lo que el `if` de la
    rama ON sabe leer. Si devolviera otra palabra, el agente "arreglaría" un bono
    dejándolo igual de roto — que es justo lo que pasó al revés con `HD`."""
    from engines.curvas import moneda_flujo_esperada

    on = {"emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija"}
    assert moneda_flujo_esperada(on) == "USD"
    assert moneda_flujo_esperada({**on, "ajuste": "dolar_linked"}) == "DL"
    assert moneda_flujo_esperada({**on, "moneda_eje": "ARS"}) == "ARS"
    assert moneda_flujo_esperada({**on, "moneda_eje": "EUR"}) == "USD"
    # Sin ejes NO se afirma nada: "no puedo saber" nunca es "está mal".
    assert moneda_flujo_esperada({"emisor_tipo": "corporativo"}) == ""


def test_el_detector_ve_el_DEFECTO_y_no_solo_el_sintoma():
    """Medido el 2026-08-17: **30 de 140 bonos** de la rama ON tienen
    `moneda_flujo` contradiciendo a sus ejes, y solo **8** habían disparado algún
    hallazgo. Los otros 22 estaban igual de mal valuados y no aparecían en ninguna
    pantalla, porque las demás reglas miran una MÉTRICA fuera de rango y esa solo
    se ve cuando el error es grande y ese día hubo precio.

    Por eso esta regla mira los dos campos del doc y **no necesita precio**: es
    cierta un domingo, con 1816 caído y sin snapshot."""
    from api.services.av_agent import detectar_tasas_sospechosas

    base = {"emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija",
            "flujos": [{"fecha": "2027-06-01", "amortizacion": 100.0}]}
    # LOC6O real: `HD` es el vocabulario de la CARTERA, que el motor no conoce.
    loc6o = _doc("LOC6O", **base, moneda_flujo="HD")
    # PN40O real: `ARS` en un dólar-linked.
    pn40o = _doc("PN40O", **{**base, "ajuste": "dolar_linked"}, moneda_flujo="ARS")
    sano = _doc("PLC5O", **base, moneda_flujo="USD")

    # SIN una sola métrica: el diccionario de snapshot va vacío a propósito.
    hs = detectar_tasas_sospechosas([loc6o, pn40o, sano], {})
    reglas = {(h["ticker"], h["regla"]) for h in hs}
    assert ("LOC6O", "moneda_flujo_contradice") in reglas
    assert ("PN40O", "moneda_flujo_contradice") in reglas
    assert ("PLC5O", "moneda_flujo_contradice") not in reglas

    ev = next(h for h in hs if h["ticker"] == "LOC6O")["evidencia"]
    assert ev["moneda_flujo"] == "HD" and ev["moneda_flujo_esperada"] == "USD"


def test_la_causa_se_elige_AGUAS_ARRIBA():
    """Una causa por hallazgo, y en orden: con `moneda_flujo` mal, todo lo demás
    que se mida está medido en la unidad equivocada. Ordenarlo mal fue el error que
    hizo proponer «cambiá la pata» sobre bonos cuya pata estaba perfecta."""
    from datetime import date, timedelta

    from api.services.av_agent_alta import diagnosticar_local

    fut = (date.today() + timedelta(days=200)).isoformat()
    on = {"emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija"}

    # LOC6O: `moneda_flujo` mal Y cuadro sano → gana `moneda_flujo`.
    dx = diagnosticar_local({**on, "moneda_flujo": "HD",
                             "flujos": [{"fecha": fut, "amortizacion": 100.0}]},
                            "on", {"precio": 156570.0, "paridad": 156570.0})
    assert dx["causa"] == "moneda_flujo_contradice"
    assert dx["parche"] == {"moneda_flujo": "USD"}
    assert "no conozca" in dx["detalle"] or "conozca" in dx["detalle"]

    # Con los dos mal, `moneda_flujo` sigue ganando: la escala se mide después.
    dx2 = diagnosticar_local({**on, "moneda_flujo": "HD",
                              "flujos": [{"fecha": fut, "amortizacion": 146300.0}]},
                             "on", {"precio": 137280.0})
    assert dx2["causa"] == "moneda_flujo_contradice"

    # OLC3O: `moneda_flujo` OK (DL) → recién ahí se ve el cuadro.
    dl = {"emisor_tipo": "corporativo", "moneda_eje": "USD",
          "ajuste": "dolar_linked", "moneda_flujo": "DL"}
    dx3 = diagnosticar_local({**dl, "flujos": [{"fecha": fut,
                                                "amortizacion": 146300.0}]},
                             "on", {"precio": 137280.0})
    assert dx3["causa"] == "escala_del_cuadro" and not dx3["parche"]

    # CO3D7: el CER va ANTES que la forma del cuadro — sin él el motor no
    # calcula nada y lo que se ve guardado es un fósil.
    cer = {"emisor_tipo": "provincial", "moneda_eje": "ARS", "ajuste": "cer",
           "flujos": [{"fecha": fut, "amortizacion": 29.25}]}
    dx4 = diagnosticar_local(cer, "cer", {"precio": 122.0})
    assert dx4["causa"] == "falta_cer" and "número viejo" in dx4["detalle"]

    # TX26: el bono está BIEN — la paridad la calcula mal el motor.
    tx26 = {"emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "cer",
            "cer_emision": 22.544, "valor_nominal": 100,
            "flujos": [{"fecha": fut, "amortizacion_pct": 20.0}]}
    dx5 = diagnosticar_local(tx26, "cer", {"precio": 727.3})
    assert dx5["causa"] == "paridad_del_motor" and not dx5["parche"]
    assert "está bien" in dx5["detalle"]

    # DHSGO: precio 0 NO es precio.
    dhsgo = {"emisor_tipo": "corporativo", "moneda_eje": "ARS", "ajuste": "tamar",
             "moneda_flujo": "ARS",
             "flujos": [{"fecha": fut, "amortizacion": 100.0}]}
    assert diagnosticar_local(dhsgo, "on", {"precio": 0.0})["causa"] == "sin_precio"


def test_el_arreglo_LOCAL_se_decide_ANTES_de_tocar_la_red():
    """Si la decisión quedara después de `_referencia_1816`, la puerta seguiría
    muriendo con el rate limit exactamente igual que antes — y encima habría
    pagado la llamada para nada."""
    import inspect

    from api.services import av_agent_alta

    src = inspect.getsource(av_agent_alta.simular_arreglo)
    assert src.index("_arreglo_local(") < src.index("_referencia_1816(")
    assert src.index("diagnosticar_local(") < src.index("_referencia_1816(")

    # Y el arreglo local NO puede escribir sin que la métrica vuelva al rango.
    loc = inspect.getsource(av_agent_alta._arreglo_local)
    assert 'OK if\n                    (en_rango and estaba_mal) else BLOQUEA' in loc \
        or "(en_rango and estaba_mal) else BLOQUEA" in loc


def test_1816_caido_NO_frena_la_relevada_entera():
    """El 429 del proveedor dejaba a `jobs.av_agent` muerto **antes del primer
    detector** — y de los cuatro, el único que necesita el universo de 1816 es
    `detectar_faltantes`. Los otros tres miran NUESTRA base.

    Es el mismo error de diseño que la cadena del arreglo, en otro archivo: una
    dependencia externa colgando de algo que casi no la necesita.

    Y la degradación tiene que ser HONESTA: sin universo no se buscan faltantes,
    porque «no pude mirar» jamás puede convertirse en «no falta nada»."""
    import inspect

    from api.services import av_agent

    src = inspect.getsource(av_agent.relevar)
    assert "universo_local()" in src, "tiene que caer al catálogo local"
    assert "if universo_1816 else []" in src, (
        "sin universo NO se buscan faltantes: reportar cero sería afirmar que no "
        "falta nada cuando en realidad no se pudo mirar")
    assert '"fuente": fuente_univ' in src, (
        "una corrida degradada no puede leerse igual que una completa")

    # Y el censo local devuelve las claves de 1816, no las de la tabla: el
    # detector no puede tener que saber de dónde salió el universo.
    loc = inspect.getsource(av_agent.universo_local)
    for clave in ("_curva", "emisorNombre", "monedaDenom", "isinCode",
                  "fechaEmision", "fechaVencimiento"):
        assert f'"{clave}"' in loc


def test_el_agente_MIRA_POR_VARIOS_LADOS_y_no_solo_donde_encuentra():
    """Pedido del user (2026-08-17): *«lo que yo quiero es el ANÁLISIS, que el
    agente tenga varias formas de detectar qué es lo que pasa… es por el valor
    técnico, es por la paridad, es por la moneda, es porque falta esto»*.

    La versión anterior devolvía en el PRIMER match: acertaba la causa pero no
    mostraba el razonamiento. Y quien lee la pantalla es el que decide si escribir,
    así que necesita las dos cosas — una lente en verde también informa, porque es
    la que descarta un camino."""
    from datetime import date, timedelta

    from api.services.av_agent_alta import LENTES, analizar

    fut = (date.today() + timedelta(days=200)).isoformat()
    loc6o = {"emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija",
             "moneda_flujo": "HD", "ticker": "MERV - XMEV - LOC6O - 24hs",
             "flujos": [{"fecha": fut, "amortizacion": 100.0}]}
    r = analizar(loc6o, "on", {"precio": 156570.0, "paridad": 156570.0})

    # TODAS las lentes contestan, siempre y en orden.
    assert [o["clave"] for o in r["observaciones"]] == [c for c, _ in LENTES]
    assert all(o["detalle"] for o in r["observaciones"])

    por = {o["clave"]: o for o in r["observaciones"]}
    # La que encuentra el problema se lleva la causa…
    assert r["causa"] == "moneda_flujo_contradice"
    assert por["moneda"]["causa"] == "moneda_flujo_contradice"
    # …y explica el mecanismo, no solo el síntoma.
    assert "else" in por["moneda"]["detalle"] and "CARTERA" in por["moneda"]["detalle"]
    # …pero las otras igual dicen lo suyo: el cuadro está SANO y hay que verlo.
    assert por["cuadro"]["estado"] == "ok" and "base 100" in por["cuadro"]["detalle"]
    # La paridad muestra LA DIVISIÓN, no un veredicto.
    assert "156,570.0000 / 100.0000" in por["paridad"]["detalle"]
    # Y el precio dice en qué ESCALA está, que es la mitad del razonamiento.
    assert "PESOS" in por["precio"]["detalle"]
    # El XIRR que no converge se explica y NO se lleva la causa: apunta a otra lente.
    assert por["tasa"]["estado"] == "revisar" and "escalas distintas" in por["tasa"]["detalle"]

    # Un bono sano: ninguna lente encuentra nada y la conclusión lo dice.
    sano = {**loc6o, "moneda_flujo": "USD"}
    r2 = analizar(sano, "on", {"precio": 103.5, "paridad": 103.5, "tea": 0.08})
    assert r2["causa"] == "sano" and not r2["parche"]
    assert all(o["estado"] in ("ok", "info") for o in r2["observaciones"])


def test_las_lentes_van_a_la_PANTALLA_completas():
    """Si el modal mostrara solo la conclusión, el análisis existiría y nadie lo
    vería — que es exactamente lo que el user vino a pedir que cambie."""
    import inspect

    from api.services import av_agent_alta

    src = inspect.getsource(av_agent_alta._diagnostico_local)
    assert 'for o in dx["observaciones"]' in src, "van TODAS, no solo la culpable"
    assert 'f"lente_{o[\'clave\']}"' in src
    assert src.index('for o in dx["observaciones"]') < src.index("LA CONCLUSIÓN"), \
        "primero el razonamiento, después la conclusión"


# ── SALUD entra al agente (fusión 2026-08-17) ───────────────────────────────


def test_un_CHEQUEO_de_salud_es_un_HALLAZGO_del_agente():
    """User: *«quiero que el agente abarque tareas de SALUD»* — y encajan sin
    forzar nada, porque **un chequeo y un hallazgo son el mismo objeto**: algo que
    se evalúa, tiene estado, guarda evidencia congelada y pide una decisión. Lo
    único distinto es el sujeto: un bono o un job."""
    from api.services.av_agent import ACCION_POR_TIPO, detectar_salud

    chequeos = [
        {"id": "job:portafolio_backfill", "familia": "job", "titulo": "backfill",
         "estado": "error", "motivo": "debía correr 11:00 y la última fue ayer",
         "evidencia": "exit 1", "schedule": "0 11 * * 1-5"},
        {"id": "dato:tenencia", "familia": "dato", "titulo": "tenencia",
         "estado": "warn", "motivo": "1 día hábil de atraso", "evidencia": ""},
        {"id": "job:bcra", "familia": "job", "titulo": "bcra", "estado": "ok",
         "motivo": "al día", "evidencia": ""},
    ]
    hs = detectar_salud(chequeos)
    # El verde NO es un hallazgo.
    assert {h["ticker"] for h in hs} == {"job:portafolio_backfill", "dato:tenencia"}
    # La severidad traduce el estado de SALUD sin inventar una escala nueva.
    sev = {h["ticker"]: h["severidad"] for h in hs}
    assert sev["job:portafolio_backfill"] == "alta" and sev["dato:tenencia"] == "media"
    # La evidencia viaja COMPLETA: es lo que sostiene el hallazgo cuando el motivo
    # ya no exista (mismo criterio que el resto del agente).
    ev = hs[0]["evidencia"]
    assert ev["chequeo_id"] == "job:portafolio_backfill" and ev["familia"] == "job"
    assert ev["schedule"] == "0 11 * * 1-5"
    # Todos comparten la MISMA forma que los hallazgos de bonos.
    assert all(set(h) == {"tipo", "ticker", "regla", "severidad", "motivo",
                          "evidencia"} for h in hs)
    # `salud` abre una puerta de SOLO LECTURA: el agente razona el chequeo pero
    # no escribe de ese lado. Sin valor, la fila quedaría muda en el front — y un
    # chequeo en rojo que no se puede ni mirar es peor que no tenerlo en la lista.
    assert ACCION_POR_TIPO["salud"] == "salud"


def test_SALUD_no_puede_tumbar_la_relevada_de_bonos():
    """Que la observabilidad se caiga no puede dejar sin correr a los detectores
    de renta fija — el mismo contrato que ya rige el universo de 1816."""
    import inspect

    from api.services import av_agent

    src = inspect.getsource(av_agent.relevar)
    assert "chequeos_salud = []" in src, "SALUD se lee en su propio try"
    assert src.index("try:\n        from api.services import salud") < \
        src.index("detectar_salud(chequeos_salud)")


def test_las_lentes_de_SALUD_hablan_el_MISMO_idioma_que_las_de_un_bono():
    """El modal es UNO solo: si SALUD devolviera otra forma habría que escribir una
    segunda pantalla, y a las dos semanas dirían cosas distintas con los mismos
    nombres. Por eso los pasos y el veredicto se IMPORTAN, no se copian."""
    import inspect

    from api.services import av_agent_salud

    src = inspect.getsource(av_agent_salud)
    assert "from api.services.av_agent_alta import" in src
    assert "_veredicto" in src and "_paso" in src

    # La lente con IA es la ÚNICA que gasta tokens, y va ÚLTIMA.
    d = inspect.getsource(av_agent_salud.diagnosticar)
    assert "if con_ia:" in d
    assert d.index("_lente_historial") < d.index("_lente_ia")
    # Y NO escribe: el dueño del estado de SALUD sigue siendo SALUD.
    assert "INSERT" not in src.upper() and "UPDATE " not in src.upper()


def test_el_EVAL_SET_no_acepta_un_NO_sin_motivo():
    """Un ✖ sin causa correcta ni nota no es un dato: no se puede aprender de
    «está mal». Y `suficiente` existe para que 2 de 2 no se lea como «100% de
    acierto» — que es cómo se toman decisiones de autonomía sobre ruido."""
    from api.services import av_agent_evals as ev

    r = ev.votar(caso="LOC6O", dominio="bono", causa="moneda_flujo_contradice",
                 acierta=False)
    assert r["ok"] is False and "no se puede aprender" in r["error"]

    assert ev.votar(caso="", dominio="bono", causa="x", acierta=True)["ok"] is False
    assert ev.MIN_VOTOS >= 10


# ── La MEMORIA: nada se desperdicia (2026-08-17) ────────────────────────────


def test_el_agente_DETECTA_SUS_PROPIAS_CONTRADICCIONES():
    """El caso real que lo motivó: en OLC3O la lente del precio decía «ninguna
    fuente local tiene un precio mayor que 0» y tres pasos más abajo la MISMA
    pantalla mostraba «precio 137.280 (snapshot)».

    **No era un bono mal cargado: era el agente contradiciéndose**, y eso es peor
    que un dato malo porque destruye la confianza en todo lo demás que dice.

    El bug pasó los tests, el lint y una lectura humana. Lo único que lo caza es
    comparar dos frases separadas por seis renglones en una pantalla larga — y eso
    lo hace mejor una máquina, y lo hace SIEMPRE."""
    from api.services.av_agent_memoria import contradicciones

    # Las lentes declaran HECHOS, no solo prosa: comparar texto se rompe con un
    # sinónimo, y lo que hay que cazar son incoherencias que ya se le escaparon a
    # una lectura humana.
    obs = [{"clave": "precio", "hechos": {"precio": None},
            "detalle": "ninguna fuente local tiene un precio **mayor que 0**"},
           {"clave": "paridad", "hechos": {}, "detalle": "paridad = **0.0631%**"}]
    inc = contradicciones(obs, {"precio": 137280.0})
    assert [i["id"] for i in inc] == ["precio_fantasma"]
    assert "137,280" in inc[0]["detalle"]

    # Sin precio de verdad, la misma observación NO es una contradicción.
    assert contradicciones(obs, {"precio": None}) == []

    # Y al revés: la TASA no puede culpar a un precio que sí existe.
    obs2 = [{"clave": "tasa", "hechos": {"precio": None},
             "detalle": "sin TEA porque no hay precio"}]
    assert [i["id"] for i in contradicciones(obs2, {"precio": 100.0})] == \
        ["tea_culpa_al_precio"]


def test_la_CONTRADICCION_se_muestra_ANTES_de_la_conclusion():
    """Si el agente se contradice, el diagnóstico de abajo no es confiable — y eso
    hay que verlo antes de leerlo, no después."""
    import inspect

    from api.services import av_agent_alta

    src = inspect.getsource(av_agent_alta._diagnostico_local)
    assert "EL AGENTE SE CONTRADICE" in src
    assert src.index("incoherencia") < src.index("LA CONCLUSIÓN")
    # Las lecciones también van antes: sirven cuando alguien está por decidir.
    assert src.index("YA APRENDIMOS") < src.index("LA CONCLUSIÓN")
    # Y la traza se registra siempre, con las observaciones COMPLETAS.
    assert "registrar_traza(" in src and 'observaciones=dx["observaciones"]' in src


def test_una_LECCION_necesita_decir_QUE_SE_CAMBIO():
    """Una lección sin el cambio técnico es una anécdota: no sirve para que la
    próxima vez sea distinta, que es exactamente para lo que existe."""
    from api.services.av_agent_memoria import guardar_leccion
    from scripts.sembrar_lecciones import LECCIONES

    assert guardar_leccion(slug="x", titulo="t", sintoma="s", causa_raiz="c",
                           cambio="")["ok"] is False

    # Las sembradas cuentan el ciclo COMPLETO y dicen quién las detectó.
    slugs = {l["slug"] for l in LECCIONES}
    assert len(slugs) == len(LECCIONES), "los slugs son la identidad: no se repiten"
    for lec in LECCIONES:
        assert lec["cambio"] and lec["sintoma"] and lec["causa_raiz"], lec["slug"]
        assert lec["detectado_por"] in ("user", "agente", "test")
    # La del contradicción la cazó el USER mirando la pantalla, no un test.
    assert next(l for l in LECCIONES
                if l["slug"] == "el-agente-se-contradice")["detectado_por"] == "user"


def test_el_agente_LEE_EL_LOG_en_vez_de_mandar_a_buscarlo():
    """El diagnóstico de `mercado_1816_series` decía *«revisar los logs del
    scheduler»* — teniéndolos a mano: `salud.detalle()` ya devolvía los errores y
    las últimas 40 líneas del `JobRunLogger`, y el agente no las leía.

    **Un diagnóstico que manda a buscar lo que ya tiene enfrente no es un
    diagnóstico: es una derivación.**"""
    from api.services.av_agent_salud import _lente_arreglo, _lente_firma, _lente_log

    c = {"id": "job:mercado_1816_series", "familia": "job",
         "modulos": ["jobs.mercado_1816_series"],
         "motivo": "debía correr 17/08 22:00 UTC y la última fue 14/08 22:00",
         "corridas": [{"status": "error", "inicio": "2026-08-17T22:00:00",
                       "elapsed_s": 12, "stats": {},
                       "errores": ["Error1816: auth HTTP 429"],
                       "log": ["conectando a 1816", "auth falló"]}]}

    log = _lente_log(c)
    assert "auth HTTP 429" in log["detalle"], "el error REAL tiene que estar a la vista"
    assert log["estado"] == "revisar"

    # La firma conecta el error con la lección YA aprendida: eso es cerrar el
    # bucle — «revisá los logs» pasa a ser «esto es lo mismo de la vez pasada».
    lecc = {"backoff-contra-una-cuota": {"titulo": "El backoff contra una cuota",
                                         "cambio": "token COMPARTIDO"}}
    f = _lente_firma(c, lecc)
    assert "RECHAZÓ por límite" in f["detalle"] and "token COMPARTIDO" in f["detalle"]

    # Y el arreglo es un COMANDO, no una categoría. «Relanzar el job» no es una
    # acción: es una categoría. La acción es lo que hay que tipear.
    arr = _lente_arreglo(c)
    assert "python -m jobs.mercado_1816_series" in arr["detalle"]
    # Y avisa del cupo de 1816 ANTES de que la re-corrida lo gaste al pedo.
    assert "50 tokens por día" in arr["detalle"]


def test_un_job_DECLARA_QUE_ALIMENTA():
    """El diagnóstico decía «no se puede precisar qué vista queda tocada porque el
    chequeo no declara qué alimenta». Es honesto, y también es un agujero con
    arreglo trivial: **nadie lo había escrito**. Sin eso una alerta no se puede
    priorizar — no es lo mismo «un cron falló» que «el AuM de hoy está mal»."""
    from api.services.av_agent_salud import JOBS, _lente_aguas_abajo

    c = {"id": "job:mercado_1816_series", "familia": "job"}
    d = _lente_aguas_abajo(c)["detalle"]
    assert "RESEARCH" in d and "1816" in d

    # El job más caro de perder tiene que decirlo con todas las letras.
    assert "AuM" in JOBS["portafolio_backfill"]["alimenta"]
    # Y los que dependen de 1816 están marcados: es lo que deja conectar un fallo
    # con la cuota de 50 tokens/día sin que nadie se acuerde.
    assert "1816" in JOBS["mercado_1816_series"]["depende_de"]


# ── EN RUEDA: los dos detectores que solo tienen sentido con el mercado abierto ──

def test_un_simbolo_que_no_esta_en_el_snapshot_es_NO_SUSCRIPTO():
    """El caso AO29: el bono está en `mercado.curvas` y el motor nunca pidió su
    símbolo. No es «no operó» — es que nadie lo está escuchando."""
    from api.services.av_agent import detectar_sin_precio
    hs = detectar_sin_precio(
        [{"ticker": "MERV - XMEV - AO29 - 24hs", "ticker_corto": "AO29"}], {})
    assert len(hs) == 1
    assert hs[0]["regla"] == "no_suscripto" and hs[0]["severidad"] == "alta"


def test_un_precio_CERO_no_es_un_precio():
    """Mismo criterio que `_precio_local`: un 0 en el snapshot no dice «vale
    cero», dice que no hay punta."""
    from api.services.av_agent import detectar_sin_precio
    hs = detectar_sin_precio([{"ticker": "X", "ticker_corto": "XX"}],
                             {"X": {"last_price": 0}})
    assert hs and hs[0]["regla"] == "sin_punta"


def test_un_bono_que_cotiza_en_PESOS_no_es_un_error_sino_CONTEXTO():
    """**La corrección de 2026-08-18, y es la parte que importa.** El detector
    marcaba `alta` y dio 46 de 230 contra prod. Esa proporción obligó a mirar el
    motor: `precio_soberano_a_usd` YA divide por el MEP cuando el símbolo no
    termina en D/C, así que la TEA y la paridad de esos 46 están BIEN — es lo que
    dijo el user de GD46: «por más que la tasa y eso esté bien».

    Un detector que llama «alta» a 46 casos sanos no es estricto: enseña a
    ignorar la lista. Queda como contexto (`baja`), y lo accionable es nombrar la
    pata en dólares."""
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - GD46 - 24hs"
    bono = {"ticker": sim, "ticker_corto": "GD46", "moneda_eje": "USD",
            "valor_nominal": 100}
    hs = detectar_precio_fuera_de_moneda(
        [bono], {sim: {"last_price": 102700.0}}, 1520.44,
        {"MERV - XMEV - GD46D - 24hs"})
    assert len(hs) == 1
    assert hs[0]["regla"] == "cotiza_en_pesos" and hs[0]["severidad"] == "baja"
    assert "La valuación está bien" in hs[0]["motivo"]
    # Lo ÚNICO accionable: cuál es la pata que mostraría dólares.
    assert hs[0]["evidencia"]["pata_dolar"] == "MERV - XMEV - GD46D - 24hs"


def test_un_simbolo_que_DICE_dolares_y_trae_pesos_SI_es_un_error():
    """El otro lado de la misma moneda: si el símbolo termina en D, el motor lo
    toma como dólares TAL CUAL — no hay conversión que explique una paridad de
    102.700%, así que ahí sí algo está roto."""
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - GD46D - 24hs"
    bono = {"ticker": sim, "ticker_corto": "GD46", "moneda_eje": "USD",
            "valor_nominal": 100}
    hs = detectar_precio_fuera_de_moneda([bono], {sim: {"last_price": 102700.0}},
                                         1520.44, set())
    assert len(hs) == 1
    assert hs[0]["regla"] == "precio_fuera_de_escala" and hs[0]["severidad"] == "alta"


def test_sin_MEP_no_se_afirma_nada():
    """Sin el tipo de cambio no hay forma de probar la hipótesis, y una causa sin
    prueba es exactamente lo que este agente no emite."""
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - GD46 - 24hs"
    bono = {"ticker": sim, "ticker_corto": "GD46", "moneda_eje": "USD"}
    assert detectar_precio_fuera_de_moneda([bono], {sim: {"last_price": 102620.0}},
                                           None) == []


def test_un_precio_USD_sano_no_dispara_nada():
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - GD46D - 24hs"
    bono = {"ticker": sim, "ticker_corto": "GD46", "moneda_eje": "USD",
            "valor_nominal": 100}
    assert detectar_precio_fuera_de_moneda([bono], {sim: {"last_price": 74.19}},
                                           1385.0) == []


def test_los_hallazgos_de_rueda_NO_son_accionables_desde_el_modal():
    """`None` explícito y por un motivo distinto al resto: no es que falte
    construirlo, es que no se arreglan tocando `mercado.curvas` — un símbolo sin
    suscribir se resuelve en el universo del motor."""
    from api.services import av_agent
    assert av_agent.ACCION_POR_TIPO["sin_precio"] is None
    assert av_agent.ACCION_POR_TIPO["precio_moneda"] is None


def test_un_precio_VIEJO_se_detecta_y_no_revienta_por_la_zona_horaria():
    """`updated_at` es un timestamptz. Compararlo contra un naive levanta, y un
    monitor que se cae por un detalle de tipos deja de avisar justo cuando hace
    falta — por eso se normaliza antes de comparar."""
    import datetime as dt

    from api.services.av_agent import PRECIO_VIEJO_MIN, detectar_sin_precio
    # Hora FIJA y dentro de la rueda (martes 15 UTC = 12 ART). Con `now()` el
    # test pasaba o fallaba según la hora en que se corriera — y desde que la
    # frescura solo se juzga con el mercado abierto, eso lo volvía un test que
    # miente la mitad del día.
    ahora = dt.datetime(2026, 8, 18, 15, tzinfo=dt.UTC)
    bono = {"ticker": "X", "ticker_corto": "XX"}
    viejo = ahora - dt.timedelta(minutes=PRECIO_VIEJO_MIN + 5)
    hs = detectar_sin_precio([bono], {"X": {"last_price": 74.0, "updated_at": viejo}},
                             ahora)
    assert hs and hs[0]["regla"] == "precio_viejo"
    # Y el mismo caso con un naive: tiene que salir igual, no explotar.
    hs2 = detectar_sin_precio(
        [bono], {"X": {"last_price": 74.0, "updated_at": viejo.replace(tzinfo=None)}},
        ahora)
    assert hs2 and hs2[0]["regla"] == "precio_viejo"


def test_un_precio_FRESCO_no_dispara_nada():
    import datetime as dt

    from api.services.av_agent import detectar_sin_precio
    ahora = dt.datetime(2026, 8, 18, 15, tzinfo=dt.UTC)     # martes, en rueda
    assert detectar_sin_precio(
        [{"ticker": "X", "ticker_corto": "XX"}],
        {"X": {"last_price": 74.0, "updated_at": ahora}}, ahora) == []


def test_cols_map_no_castea_lo_que_no_es_un_numero():
    """El bug que tumbó el monitor en su primera corrida: `cols_map` casteaba
    TODA columna a float y `updated_at` es un datetime. La función promete
    servir cualquier columna del snapshot — que tiene `book jsonb` y dos
    timestamps— así que el cast incondicional era la mentira, no el caller."""
    import datetime as dt
    from decimal import Decimal

    from core.market_snapshot import _num
    assert _num(Decimal("74.19")) == 74.19 and isinstance(_num(Decimal("1")), float)
    ahora = dt.datetime.now(dt.UTC)
    assert _num(ahora) is ahora
    assert _num({"bids": []}) == {"bids": []}


def test_FUERA_de_rueda_un_precio_viejo_NO_es_un_hallazgo():
    """La primera corrida real marcó los 230 bonos del universo justo después
    del cierre, todos con «282 min sin actualizar». Eso no era el sistema roto:
    era el mercado cerrado. Sin la pregunta «¿está abierto?», el detector no
    dice nada."""
    import datetime as dt

    from api.services.av_agent import PRECIO_VIEJO_MIN, detectar_sin_precio
    cerrado = dt.datetime(2026, 8, 18, 21, tzinfo=dt.UTC)     # 18 ART
    viejo = cerrado - dt.timedelta(minutes=PRECIO_VIEJO_MIN + 200)
    assert detectar_sin_precio(
        [{"ticker": "X", "ticker_corto": "XX"}],
        {"X": {"last_price": 74.0, "updated_at": viejo}}, cerrado) == []


def test_pero_SIN_PUNTA_y_NO_SUSCRIPTO_valen_a_cualquier_hora():
    """Son afirmaciones sobre el CATÁLOGO, no sobre la actividad del día: que un
    símbolo no exista en el snapshot está mal a las 3 de la madrugada igual."""
    import datetime as dt

    from api.services.av_agent import detectar_sin_precio
    cerrado = dt.datetime(2026, 8, 18, 21, tzinfo=dt.UTC)
    hs = detectar_sin_precio([{"ticker": "X", "ticker_corto": "XX"}], {}, cerrado)
    assert hs and hs[0]["regla"] == "no_suscripto"
