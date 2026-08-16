"""Tests del armado de la tab CURVAS (api/services/curvas_vista.py) — puros.

`_armar` es la parte que decide QUÉ ve el usuario, así que se testea sin base:
las filas crudas se inyectan a mano. Lo que se protege son las reglas que, si se
pierden, cambian la vista más usada de la app sin que nadie lo note.
"""
from __future__ import annotations

from api.services.curvas_vista import _armar


def _fila(tc, emisor_tipo, moneda, ajuste, **kw):
    base = {"ticker_corto": tc, "ticker": f"MERV - XMEV - {tc} - 24hs",
            "curva": kw.get("curva", "x"), "tipo": "Bono",
            "fecha_vencimiento": "2027-01-01", "emisor": kw.get("emisor", "ACME"),
            "emisor_tipo": emisor_tipo, "moneda_eje": moneda, "ajuste": ajuste,
            "ley": kw.get("ley"), "ajuste_alt": kw.get("ajuste_alt"),
            "industria": kw.get("industria"),
            "flujo_vencimiento": kw.get("flujo_vencimiento"),
            "last_price": kw.get("last_price", 100.0), "tea": kw.get("tea", 0.3),
            "duration": kw.get("duration")}
    return base


def test_clasifica_y_cuenta_por_pill_y_por_emisor():
    out = _armar([
        _fila("S30S6", "soberano", "ARS", "fija"),
        _fila("TX26", "soberano", "ARS", "cer"),
        _fila("AE38", "soberano", "USD", "fija", ley="local"),
        _fila("IRCPO", "corporativo", "USD", "fija"),
        _fila("TMVE8", "soberano", "ARS", "tamar", ajuste_alt="fija"),
    ], fijados=set())
    por_pill = {}
    for b in out["bonos"]:
        por_pill.setdefault(b["ticker_corto"], []).append(b["pill"])
    assert por_pill == {"S30S6": ["tasa_fija"], "TX26": ["cer"],
                        "AE38": ["hard_dolar"], "IRCPO": ["hard_dolar"],
                        "TMVE8": ["tamar", "tasa_fija"]}
    n = {p["codigo"]: p["n"] for p in out["pills"]}
    assert n["hard_dolar"] == 2 and n["tamar"] == 1
    assert n["tasa_fija"] == 2      # S30S6 + la pata `fija` del dual
    assert {e["codigo"]: e["n"] for e in out["emisores"]} == \
           {"soberano": 4, "corporativo": 1}


def test_las_6_pills_siempre_estan_aunque_esten_vacias():
    """Si una pill desapareciera del catálogo cuando no tiene bonos, el botón se
    esfumaría de la pantalla un día cualquiera sin explicación."""
    out = _armar([_fila("TX26", "soberano", "ARS", "cer")], fijados=set())
    assert len(out["pills"]) == 5
    assert [p["codigo"] for p in out["pills"] if p["lado"] == "ARS"] == \
           ["tasa_fija", "cer", "tamar"]
    assert [p["codigo"] for p in out["pills"] if p["lado"] == "USD"] == \
           ["hard_dolar", "dolar_linked"]


def test_cer_fijado_sigue_yendo_a_tasa_fija():
    """REGRESIÓN: la regla vive en core.curvas_ejes y el service la respeta."""
    filas = [_fila("TZX28", "soberano", "ARS", "cer")]
    assert _armar(filas, fijados=set())["bonos"][0]["pill"] == "cer"
    out = _armar(filas, fijados={"TZX28"})
    assert out["bonos"][0]["pill"] == "tasa_fija"
    assert out["bonos"][0]["cer_fijado"] is True


def test_sin_ejes_no_desaparece_se_reporta():
    """Un bono sin clasificar NO puede evaporarse en silencio: sale listado."""
    out = _armar([
        _fila("BA37D", None, None, None),          # sin ejes (1816 no lo tiene)
        _fila("BADLO", "corporativo", "ARS", "badlar"),  # ajuste sin pill acordada
        _fila("TX26", "soberano", "ARS", "cer"),
    ], fijados=set())
    assert [b["ticker_corto"] for b in out["bonos"]] == ["TX26"]
    assert out["sin_clasificar"] == ["BA37D", "BADLO"]


def test_el_emisor_viaja_en_cada_bono():
    """El filtro de EMISOR es client-side: cambiarlo no puede costar un request."""
    out = _armar([_fila("IRCPO", "corporativo", "USD", "fija", emisor="IRSA")],
                 fijados=set())
    b = out["bonos"][0]
    assert b["emisor_tipo"] == "corporativo" and b["emisor"] == "IRSA"
    assert b["lado"] == "USD"


def test_metrics_omite_los_nulos():
    """Mismo contrato que get_renta_fija: las claves sin valor no viajan."""
    fila = _fila("TX26", "soberano", "ARS", "cer")
    fila["tea"] = None
    b = _armar([fila], fijados=set())["bonos"][0]
    assert "TEA" not in b["metrics"] and b["metrics"]["last_price"] == 100.0


def test_bono_sin_snapshot_no_rompe():
    """El LEFT JOIN puede no traer precio (bono nuevo, o fuera de rueda)."""
    fila = _fila("NUEVO1", "soberano", "ARS", "fija")
    fila["last_price"] = None
    fila["tea"] = None
    b = _armar([fila], fijados=set())["bonos"][0]
    assert b["metrics"] == {} and b["pill"] == "tasa_fija"


def test_un_dual_sale_en_SUS_DOS_tablas():
    """El cambio de modelo, visto desde la vista: la misma ficha aparece dos
    veces, con distinto `pill`/`lado`. Se emite repetido y no como lista de pills
    a propósito — el front ya filtra por `b.pill === pill`, así que el contrato NO
    cambia y los dos deploys no tienen que ser simultáneos (el front sube solo a
    Vercel; el backend va a mano y siempre después)."""
    out = _armar([_fila("TXMD8", "soberano", "ARS", "cer", ajuste_alt="tamar")],
                 fijados=set())
    filas = {b["pill"]: b for b in out["bonos"]}
    assert set(filas) == {"cer", "tamar"}
    assert filas["cer"]["lado"] == "ARS" and filas["tamar"]["lado"] == "ARS"
    # misma ficha en las dos: si divergieran, el trader vería dos bonos distintos
    assert filas["cer"]["vencimiento"] == filas["tamar"]["vencimiento"]
    assert filas["cer"]["ajuste_alt"] == "tamar"


def test_un_dual_NO_infla_el_contador_de_emisores():
    """REGRESIÓN silenciosa: `n_emisor` cuenta BONOS, no filas. Si se incrementara
    dentro del loop de pills, el filtro EMISOR diría 2 donde hay 1 — un número
    apenas alto que nadie mira dos veces."""
    out = _armar([_fila("TXMD8", "soberano", "ARS", "cer", ajuste_alt="tamar")],
                 fijados=set())
    assert len(out["bonos"]) == 2                                  # dos filas
    assert {e["codigo"]: e["n"] for e in out["emisores"]} == {"soberano": 1}


def test_un_dual_con_las_dos_patas_iguales_no_se_duplica():
    """Dato mal cargado: no puede aparecer dos veces en la MISMA tabla."""
    out = _armar([_fila("X", "soberano", "ARS", "cer", ajuste_alt="cer")], fijados=set())
    assert [b["pill"] for b in out["bonos"]] == ["cer"]


def test_la_industria_solo_viaja_para_CORPORATIVOS():
    """Un soberano no tiene industria. Mandarla en null para todos haría que el
    filtro del gráfico muestre un bucket "sin clasificar" con 60 soberanos
    adentro — un pendiente inventado que nadie puede resolver."""
    out = _armar([
        _fila("IRCPO", "corporativo", "USD", "fija", industria="energia"),
        _fila("AE38", "soberano", "USD", "fija", ley="local", industria="energia"),
    ], fijados=set())
    ind = {b["ticker_corto"]: b["industria"] for b in out["bonos"]}
    assert ind == {"IRCPO": "energia", "AE38": None}


def test_un_corporativo_sin_industria_llega_en_null_y_no_como_otros():
    """`null` es SIN CLASIFICAR y tiene que verse como bucket propio. Mapearlo a
    'otros' en el backend lo volvería indistinguible de un emisor clasificado ahí
    a propósito — el mismo cajón que venimos desarmando."""
    out = _armar([_fila("XXO", "corporativo", "USD", "fija")], fijados=set())
    assert out["bonos"][0]["industria"] is None


# ── Tasa RUIDO por duration ~0 y TC breakeven (2026-08-16) ───────────────────

def test_una_duration_casi_cero_marca_la_tasa_como_RUIDO():
    """Visto en pantalla: AFCHO vencía en 3 días y mostraba TEA 142,1%; CS450
    −49,1%. No están mal calculadas — anualizar 3 días amplifica centavos a tres
    dígitos. Con `tasa_ruido` el front las apaga y las saca del gráfico, donde un
    solo 142% estiraba el eje y aplastaba a los otros 120 bonos contra el cero."""
    ruidoso = _armar([_fila("AFCHO", "corporativo", "USD", "fija", duration=0.001)],
                     fijados=set())["bonos"][0]
    normal = _armar([_fila("IRCPO", "corporativo", "USD", "fija", duration=4.2)],
                    fijados=set())["bonos"][0]
    assert ruidoso["tasa_ruido"] is True
    assert normal["tasa_ruido"] is False


def test_una_LECAP_corta_NO_es_ruido_aunque_su_duration_sea_casi_cero():
    """Corregido el 2026-08-16 tras verlo en pantalla: S31G6 (soberano, duration
    0,04, TEA 26,6%) desaparecía del gráfico. Una Lecap a 15 días cotiza con
    volumen todos los días — su tasa es real y es la que la mesa opera. Sacarla
    borraba el TRAMO CORTO de la curva soberana, que es el que más se mira.

    El problema nunca fue el plazo corto: fue plazo corto SIN liquidez."""
    for emisor in ("soberano", "provincial", "bcra"):
        b = _armar([_fila("S31G6", emisor, "ARS", "fija", duration=0.04)],
                   fijados=set())["bonos"][0]
        assert b["tasa_ruido"] is False, emisor


def test_la_tasa_ruidosa_NO_se_borra_solo_se_marca():
    """Ocultar el número sería mentir por omisión: el bono existe, tiene precio y
    vencimiento. Lo que no es comparable es su TASA, y para eso está la marca."""
    b = _armar([_fila("AFCHO", "corporativo", "USD", "fija", duration=0.001,
                      tea=1.421)], fijados=set())["bonos"][0]
    assert b["metrics"]["TEA"] == 1.421 and b["metrics"]["last_price"] == 100.0


def test_sin_duration_no_se_asume_que_es_ruido():
    """`None` es "no sé", no "es ruido". Marcar de más apagaría tasas buenas."""
    assert _armar([_fila("XX", "corporativo", "USD", "fija")],
                  fijados=set())["bonos"][0]["tasa_ruido"] is False


def test_tc_breakeven_solo_en_tasa_fija_y_con_mep():
    """TC_BE = MEP × (flujo_vencimiento / precio). Solo tiene sentido donde el
    flujo final está determinado — en USD no significa nada."""
    ars = _armar([_fila("S30S6", "soberano", "ARS", "fija",
                        flujo_vencimiento=117.54, last_price=114.10)],
                 fijados=set(), mep=1500.0)["bonos"][0]
    assert ars["tc_breakeven"] == round(1500.0 * (117.54 / 114.10), 2)
    usd = _armar([_fila("AE38", "soberano", "USD", "fija", flujo_vencimiento=100.0)],
                 fijados=set(), mep=1500.0)["bonos"][0]
    assert usd["tc_breakeven"] is None


def test_sin_mep_el_tc_breakeven_es_none_y_no_cero():
    """«No se pudo calcular» ≠ «vale cero». Un 0 en pantalla se lee como un TC."""
    b = _armar([_fila("S30S6", "soberano", "ARS", "fija", flujo_vencimiento=117.54)],
               fijados=set(), mep=None)["bonos"][0]
    assert b["tc_breakeven"] is None


# ── TAMAR / DUALES: la tasa de 1816 por PATA (2026-08-16) ────────────────────
#
# El bug que estos tests congelan: un dual CER+TAMAR salía en sus dos tablas con
# la MISMA tasa, porque `market_snapshot` tiene una fila por símbolo y por lo
# tanto una sola TEA. Medido contra 1816: entre las patas de TXMD9 hay ~2.900 bps.


def _t1816(**kw):
    base = {"tea": None, "tna": None, "spread": None, "precio_clean": None,
            "duration": None, "paridad": None, "fecha_operacion": "2026-08-14"}
    base.update(kw)
    return base


def test_un_dual_muestra_LA_TASA_DE_CADA_PATA_y_no_la_misma_dos_veces():
    """EL bug de los duales. Sin esto las dos tablas mienten a la vez."""
    filas = [_fila("TXMD9", "soberano", "ARS", "cer", ajuste_alt="tamar", tea=0.0682)]
    tamar = {("TXMD9", "cer"): _t1816(tea=0.0682),
             ("TXMD9", "tamar"): _t1816(tea=0.3862, spread=0.0973)}
    out = _armar(filas, fijados=set(), tamar=tamar)
    por_pill = {b["pill"]: b for b in out["bonos"]}
    assert set(por_pill) == {"cer", "tamar"}
    assert por_pill["cer"]["metrics"]["TEA"] == 0.0682
    assert por_pill["tamar"]["metrics"]["TEA"] == 0.3862
    # el MARGEN solo tiene sentido en la pata TAMAR
    assert por_pill["tamar"]["margen"] == 0.0973
    assert por_pill["cer"]["margen"] is None
    assert por_pill["tamar"]["pata"] == "tamar" and por_pill["cer"]["pata"] == "cer"


def test_la_tasa_de_1816_viaja_con_su_procedencia_y_su_fecha():
    """Una tasa sin procedencia obliga a adivinar de dónde vino. La del motor es
    live; la de 1816 tiene delay y puede ser de la rueda anterior.

    En un TAMAR puro la TEA llega por el SNAPSHOT (se la escribió el job, porque
    el motor no calcula nada ahí) — la marca sale de que exista la fila de 1816.
    """
    filas = [_fila("TMF27", "soberano", "ARS", "tamar", tea=0.3037)]
    tamar = {("TMF27", "tamar"): _t1816(tea=0.3037, spread=0.0343,
                                        fecha_operacion="2026-08-14")}
    b = _armar(filas, fijados=set(), tamar=tamar)["bonos"][0]
    assert b["tea_fuente"] == "1816" and b["tea_fecha"] == "2026-08-14"
    assert b["metrics"]["TEA"] == 0.3037 and b["margen"] == 0.0343


def test_sin_dato_de_1816_manda_el_motor_y_la_fuente_queda_en_None():
    """Donde el motor sí calcula (un CER puro) esta tabla no tiene fila y no
    puede cambiar nada. `tea_fuente=None` significa "motor, live"."""
    b = _armar([_fila("TX26", "soberano", "ARS", "cer", tea=0.055)],
               fijados=set(), tamar={})["bonos"][0]
    assert b["metrics"]["TEA"] == 0.055
    assert b["tea_fuente"] is None and b["margen"] is None


def test_donde_el_MOTOR_calcula_1816_no_se_mete():
    """LA REGLA. Un dual CER+TAMAR tiene `ajuste='cer'`: esa pata la calcula el
    motor, EN VIVO. 1816 tiene el dato igual (`TXMD9 @CER`) y aun así no se usa —
    reemplazar una tasa live por una de media hora atrás es empeorar la vista
    para ganar consistencia con un proveedor."""
    filas = [_fila("TXMD9", "soberano", "ARS", "cer", ajuste_alt="tamar", tea=0.0690)]
    tamar = {("TXMD9", "cer"): _t1816(tea=0.0682),          # 1816 dice otra cosa
             ("TXMD9", "tamar"): _t1816(tea=0.3862, spread=0.0973)}
    por_pill = {b["pill"]: b for b in _armar(filas, fijados=set(), tamar=tamar)["bonos"]}
    assert por_pill["cer"]["metrics"]["TEA"] == 0.0690      # la del MOTOR
    assert por_pill["cer"]["tea_fuente"] is None
    assert por_pill["tamar"]["metrics"]["TEA"] == 0.3862    # la pata secundaria sí
    assert por_pill["tamar"]["tea_fuente"] == "1816"


def test_la_pata_SECUNDARIA_sin_dato_de_1816_queda_VACIA_no_con_la_otra():
    """El bug que el user vio en pantalla: TTD26 es TAMAR+FIJA y 1816 no publica
    su pata fija, así que en la tabla TASA FIJA aparecía con la TEA de la pata
    TAMAR (28,6%) como si fuera suya. Vacío es la respuesta correcta."""
    filas = [_fila("TTD26", "soberano", "ARS", "tamar", ajuste_alt="fija", tea=0.286)]
    tamar = {("TTD26", "tamar"): _t1816(tea=0.286, spread=0.02)}
    por_pill = {b["pill"]: b for b in _armar(filas, fijados=set(), tamar=tamar)["bonos"]}
    assert por_pill["tamar"]["metrics"]["TEA"] == 0.286
    assert "TEA" not in por_pill["tasa_fija"]["metrics"]
    assert por_pill["tasa_fija"]["tea_fuente"] is None


def test_un_bono_SIN_nada_de_1816_no_se_marca_ni_pierde_su_tasa():
    """Los 8 corporativos TAMAR que 1816 no cubre: conservan lo que haya en el
    snapshot y no dicen venir de ninguna parte."""
    b = _armar([_fila("DHSGO", "corporativo", "ARS", "tamar", tea=0.31)],
               fijados=set(), tamar={})["bonos"][0]
    assert b["metrics"]["TEA"] == 0.31
    assert b["tea_fuente"] is None and b["margen"] is None


def test_al_tomar_la_TEA_de_1816_se_descarta_la_TEM_del_motor():
    """La TEM del snapshot es de la OTRA pata (o de ninguna): dejarla mezclaría
    dos tasas distintas en la misma fila y la mensual contradiría a la anual."""
    filas = [_fila("TXMD9", "soberano", "ARS", "cer", ajuste_alt="tamar")]
    filas[0]["tem"] = 0.004
    out = _armar(filas, fijados=set(),
                 tamar={("TXMD9", "tamar"): _t1816(tea=0.3862)})
    por_pill = {b["pill"]: b for b in out["bonos"]}
    assert "TEM" not in por_pill["tamar"]["metrics"]
    assert por_pill["cer"]["metrics"].get("TEM") == 0.004   # esa pata no la tocó
