"""Las patas encontradas por FICHA, cuando el nombre del símbolo no alcanza.

EL CASO QUE LO ORIGINA — BOPREAL (user, 2026-08-19, mirando Manager → Títulos ·
Instrumentos):

    MERV - XMEV - BPOA7 - CI     ARS   BOPREAL S. 1 A VTO31/10/27 U$S CG
    MERV - XMEV - BPA7D - 24hs   USD   BOPREAL S. 1 A VTO31/10/27 U$S CG

La pata en pesos es **BPOA7** y la de dólares **BPA7D**: no es `stem + D`, se cae
la O del medio. Las dos convenciones que `core/especies` conocía (sufijo D/C y
par O/D de las ONs) fallan las dos, y los 6 BOPREALes salían como «no tiene pata
en dólares» teniéndola.

Los datos de este archivo son los REALES que devuelve el discovery.
"""
from __future__ import annotations

from core import especies as E

_A7 = "BOPREAL S. 1 A VTO31/10/27 U$S CG"
_B8 = "BOPREAL 4B VTO31/10/28 U$S CG"


def _f(ticker, underlying, maturity, currency):
    return {"ticker": ticker, "underlying": underlying,
            "maturity": maturity, "currency": currency}


# El universo tal como llega: la forma MERV y la forma corta conviven, y la
# corta trae un underlying GENÉRICO compartido por toda la familia.
_UNIVERSO = [
    _f("MERV - XMEV - BPOA7 - CI", _A7, "20271031", "ARS"),
    _f("MERV - XMEV - BPOA7 - 24hs", _A7, "20271031", "ARS"),
    _f("MERV - XMEV - BPA7D - 24hs", _A7, "20271031", "USD"),
    _f("MERV - XMEV - BPA7D - CI", _A7, "20271031", "USD"),
    _f("MERV - XMEV - BPA7C - CI", _A7, "20271031", "USD"),
    _f("MERV - XMEV - BPA7C - 24hs", _A7, "20271031", "USD"),
    # Otro BOPREAL: misma familia, OTRA ficha.
    _f("MERV - XMEV - BPOB8 - CI", _B8, "20281031", "ARS"),
    _f("MERV - XMEV - BPB8D - 24hs", _B8, "20281031", "USD"),
    # La forma corta, con underlying genérico. NO se puede usar para emparejar.
    _f("BPOA7/CI", "Bopreales - Bonos BCRA", "20271031", "ARS"),
    _f("BPOA7D/24hs", "Bopreales - Bonos BCRA", "20271031", "USD"),
    _f("BPOC7D/CI", "Bopreales - Bonos BCRA", "20271031", "USD"),
    _f("BPOD7D/24hs", "Bopreales - Bonos BCRA", "20271031", "USD"),
]


def test_encuentra_la_pata_USD_del_BOPREAL_aunque_el_nombre_no_se_parezca():
    """El caso entero: `BPOA7` → `BPA7D`. Ninguna regla de string lo saca."""
    hs = E.hermanas_por_ficha("BPOA7", _UNIVERSO)
    assert [h["ticker_especie"] for h in hs] == ["BPA7D", "BPA7C", "BPA7C", "BPA7D"] \
        or {h["ticker_especie"] for h in hs} == {"BPA7D", "BPA7C"}
    # 24hs primero: ahí está la liquidez, y por lo tanto el precio.
    assert hs[0]["plazo"] == "24hs"


def test_la_regla_de_STRING_no_lo_hubiera_encontrado():
    """La prueba de por qué hizo falta otra estrategia y no un parche a la regex."""
    base, _ = E.base("BPA7D")
    assert base == "BPA7"          # y el ticker en pesos es BPOA7
    assert base != "BPOA7"


def test_no_se_cruzan_dos_BOPREALES_distintos():
    """A7 y B8 son de la misma familia y vencen distinto: son bonos distintos."""
    hs = E.hermanas_por_ficha("BPOB8", _UNIVERSO)
    assert {h["ticker_especie"] for h in hs} == {"BPB8D"}


def test_la_forma_CORTA_no_entra_ni_para_bien_ni_para_mal():
    """Su `underlying` es genérico («Bopreales - Bonos BCRA»): con ella los 6
    BOPREALes compartirían ficha y cada uno heredaría las patas de los otros
    cinco. **Un emparejamiento silencioso y equivocado es peor que ninguno** — el
    motor pediría el precio de otro bono y la fila se llenaría con un número
    creíble."""
    hs = E.hermanas_por_ficha("BPOA7", _UNIVERSO)
    assert all(h["simbolo"].startswith("MERV - XMEV - ") for h in hs)
    assert not any(h["ticker_especie"] in ("BPOC7D", "BPOD7D") for h in hs)


def test_una_ficha_que_agrupa_DEMASIADO_no_se_usa():
    """Si Primary empieza a mandar underlyings genéricos en la forma MERV, el
    tope corta antes de emparejar mal. «No pude» nunca se vuelve un veredicto."""
    generico = [_f(f"MERV - XMEV - X{i} - 24hs", "GENERICO", "20271031",
                   "ARS" if i == 0 else "USD")
                for i in range(E.MAX_POR_FICHA + 2)]
    assert E.hermanas_por_ficha("X0", generico) == []


def test_ficha_incompleta_no_empareja():
    """Media ficha no identifica a nadie."""
    sin_mat = [_f("MERV - XMEV - AAA - 24hs", _A7, "", "ARS"),
               _f("MERV - XMEV - AAAD - 24hs", _A7, "", "USD")]
    assert E.hermanas_por_ficha("AAA", sin_mat) == []


def test_un_ticker_desconocido_devuelve_vacio_y_no_levanta():
    assert E.hermanas_por_ficha("NOEXISTE", _UNIVERSO) == []
    assert E.hermanas_por_ficha("", _UNIVERSO) == []


def test_sigue_andando_para_los_que_YA_funcionaban():
    """La estrategia nueva no reemplaza a la vieja: tiene que dar lo mismo donde
    el sufijo ya alcanzaba."""
    al30 = [_f("MERV - XMEV - AL30 - 24hs", "BONAR 2030", "20300709", "ARS"),
            _f("MERV - XMEV - AL30D - 24hs", "BONAR 2030", "20300709", "USD"),
            _f("MERV - XMEV - AL30C - 24hs", "BONAR 2030", "20300709", "USD")]
    hs = E.hermanas_por_ficha("AL30", al30)
    assert {h["ticker_especie"] for h in hs} == {"AL30D", "AL30C"}


# ── El sembrador tiene que poder USAR lo que la ficha encontró ───────────────

def test_patas_de_acepta_los_simbolos_que_encontro_la_FICHA():
    """Sin esto la ficha sería un diagnóstico sin consecuencia: el agente diría
    «la pata existe» y `sembrar_ticker` no la escribiría, porque agrupa por
    nombre y el nombre es justo lo que no coincide."""
    sims = ["MERV - XMEV - BPOA7 - 24hs", "MERV - XMEV - BPOA7 - CI",
            "MERV - XMEV - BPA7D - 24hs", "MERV - XMEV - BPA7C - 24hs"]
    # Sin `extra`: solo las de pesos, que es el bug.
    solas = E.patas_de("BPOA7", sims, moneda_bono="USD")
    assert {p["moneda"] for p in solas} == {"ARS"}

    # Con `extra`: entran las dos de dólares.
    con = E.patas_de("BPOA7", sims, moneda_bono="USD",
                     extra=("MERV - XMEV - BPA7D - 24hs",
                            "MERV - XMEV - BPA7C - 24hs"))
    assert {p["ticker_especie"] for p in con} == {"BPOA7", "BPA7D", "BPA7C"}
    # Todas quedan colgadas del ticker del master, no de un base inventado.
    assert {p["ticker"] for p in con} == {"BPOA7"}


def test_la_moneda_de_la_extra_sale_del_SUFIJO_y_no_se_inventa():
    """Lo único que estaba roto era a QUÉ bono pertenece el símbolo, no QUÉ es.
    `BPA7D` termina en D, así que el clasificador de siempre acierta la moneda."""
    con = E.patas_de("BPOA7", ["MERV - XMEV - BPOA7 - 24hs"], moneda_bono="USD",
                     extra=("MERV - XMEV - BPA7D - 24hs",))
    d = next(p for p in con if p["ticker_especie"] == "BPA7D")
    assert d["moneda"] == "USD" and d["especie"] == "mep"


def test_la_default_se_elige_entre_las_de_la_MONEDA_DEL_BONO():
    """Un bono en dólares no puede quedar con su pata en pesos como default —
    esa es exactamente la «cruzada» que el seeder reporta como error."""
    con = E.patas_de("BPOA7", ["MERV - XMEV - BPOA7 - 24hs"], moneda_bono="USD",
                     extra=("MERV - XMEV - BPA7D - 24hs",
                            "MERV - XMEV - BPA7C - 24hs"))
    default = next(p for p in con if p["es_default"])
    assert default["ticker_especie"] == "BPA7D"      # MEP antes que cable


def test_una_extra_repetida_no_duplica_la_pata():
    con = E.patas_de("BPOA7", ["MERV - XMEV - BPOA7 - 24hs"], moneda_bono="USD",
                     extra=("MERV - XMEV - BPOA7 - 24hs",))
    assert len(con) == 1


# ── (2026-08-19) EL ALFABETO NO ES UN CRITERIO DE MERCADO ───────────────────

def test_la_ficha_devuelve_MEP_antes_que_CABLE():
    """**El bug de la primera corrida**: los 8 BOPREALes emparejaron BIEN y
    devolvieron la pata en CABLE. `hermanas_por_ficha` ordenaba solo por plazo y
    después alfabético, y `BPA7C` < `BPA7D`.

    Cable y MEP son cosas distintas —`CLAUDE.md` lo advierte explícito— y la que
    mira la mesa es MEP. Emparejar bien y elegir mal no se ve distinto de
    emparejar mal."""
    hs = E.hermanas_por_ficha("BPOA7", _UNIVERSO)
    assert hs[0]["ticker_especie"] == "BPA7D"        # MEP, no BPA7C
    assert hs[0]["plazo"] == "24hs"


def test_mejor_es_EL_criterio_y_no_el_abecedario():
    """La puerta del agente y el diag tenían cada uno su copia, las dos con un
    `sorted()` alfabético. Ahora las dos delegan acá."""
    assert E.mejor(["MERV - XMEV - BPA7C - 24hs",
                    "MERV - XMEV - BPA7D - 24hs"]).endswith("BPA7D - 24hs")
    # El plazo desempata DESPUÉS de la moneda, no antes.
    assert E.mejor(["MERV - XMEV - BPA7C - 24hs",
                    "MERV - XMEV - BPA7D - CI"]).endswith("BPA7D - CI")
    assert E.mejor([]) == ""


def test_la_puerta_del_agente_usa_el_MISMO_criterio():
    """Si vuelven a divergir, el diag muestra una pata y el agente pide otra."""
    from api.services import av_agent_pata
    cands = ["MERV - XMEV - BPA7C - 24hs", "MERV - XMEV - BPA7D - 24hs"]
    assert av_agent_pata._elegir(cands) == E.mejor(cands)
