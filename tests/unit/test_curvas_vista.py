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
            "flujo_vencimiento": kw.get("flujo_vencimiento"),
            "last_price": kw.get("last_price", 100.0), "tea": kw.get("tea", 0.3)}
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
