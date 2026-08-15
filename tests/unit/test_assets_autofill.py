"""Reglas de autocompletado de `portafolio.assets` (jobs/assets_autofill.py).

Lo que se congela acá es el contrato del motor: completa SOLO lo vacío, nunca
pisa lo cargado a mano, y la firma de FINANCIAMIENTO no puede tragarse una
unidad de renta variable / CEDEAR (el corchete con id de especie).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from jobs.assets_autofill import (
    _UMBRAL_HD,
    REGLAS,
    _regla_especies,
    _regla_fci,
    _regla_financiamiento,
    _regla_financiamiento_clase,
    _regla_herencia,
    _regla_ticker,
    anotar_especies,
    anotar_herencia,
    planificar,
)

_UNIDAD_FIN = "[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 03/10/2026"
_UNIDAD_FCI = "[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B"


def test_financiamiento_deriva_cartera_ticker_y_vto():
    assert _regla_financiamiento({"unidad": _UNIDAD_FIN}) == {
        "cartera": "FINANCIAMIENTO",
        "ticker": "*BIN031000050",
        "vencimiento": "2026-10-03",   # dd/mm/aaaa → ISO
    }
    assert _regla_financiamiento({"unidad": "[#UD0281260001] #UD0281260001 "
                                            "Nro. 155077 Vto. 30/12/2026"})["ticker"] \
        == "#UD0281260001"


def test_financiamiento_no_matchea_el_resto_del_catalogo():
    # El corchete con id de especie NO repite el token siguiente.
    for u in ("[9131] YPFD - CEDEAR YPF",
              "[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos",
              "ARS",
              "[*BIN031000050] *BIN031000050 Nro. 29805263",          # sin Vto.
              "[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 31/02/2026"):  # fecha falsa
        assert _regla_financiamiento({"unidad": u}) == {}


def _clase(nominal):
    return _regla_financiamiento_clase(
        {"unidad": _UNIDAD_FIN, "nominal": nominal}).get("clase_activo")


def test_el_umbral_es_5_millones():
    """PIN del valor, no del comportamiento relativo.

    El primer intento salió con 5.000 y mandó a DL 260 papeles que eran HD. Un
    test que solo usa la constante habría pasado igual — por eso acá el número
    va escrito: si alguien lo mueve, este test lo cuenta.
    """
    assert _UMBRAL_HD == 5_000_000.0


def test_clase_hd_dl_por_nominal_con_el_umbral_incluido_en_HD():
    """≤ umbral → HD · > umbral → DL. El borde EXACTO es HD (regla del user)."""
    assert _clase(_UMBRAL_HD) == "HD"          # el borde entra a HD
    assert _clase(_UMBRAL_HD - 0.01) == "HD"
    assert _clase(_UMBRAL_HD + 0.01) == "DL"


def test_los_nominales_reales_caen_del_lado_que_dice_su_tasa():
    """La evidencia que fijó el umbral (nominales y tasas reales de core/mav_tasa).

    Un papel que rinde 6-7% anual está en dólares; uno que rinde 39,5% está en
    pesos. El corte tiene que respetar eso — es lo que el umbral viejo rompía.
    """
    assert _clase(30_000) == "HD"        # 30.000 @ 7%
    assert _clase(100_000) == "HD"       # 100.000 @ 6%
    assert _clase(500_000) == "HD"       # 500.000 @ -0,5%
    assert _clase(613_700) == "HD"       # lo que se vio mal clasificado en pantalla
    assert _clase(27_000_000) == "DL"    # 27.000.000 @ 39,5%


def test_clase_sin_nominal_no_adivina():
    """Un asset sin tenencia hoy no tiene de qué inferir: no se clasifica.

    Poner una clase al azar sería peor que dejarlo vacío — la vista muestra los
    sin clasificar en su propio grupo, un HD inventado se mezcla con los reales.
    """
    assert _regla_financiamiento_clase({"unidad": _UNIDAD_FIN, "nominal": None}) == {}
    assert _regla_financiamiento_clase({"unidad": _UNIDAD_FIN}) == {}


def test_clase_solo_aplica_a_financiamiento():
    """La heurística del nominal NO puede tocar el resto del catálogo: un CEDEAR
    con 3.000 nominales no es un pagaré hard dollar."""
    for u in ("[9131] YPFD - CEDEAR YPF", _UNIDAD_FCI, "ARS"):
        assert _regla_financiamiento_clase({"unidad": u, "nominal": 3_000}) == {}


def test_clase_inferida_no_pisa_la_corregida_a_mano():
    """El invariante que hace segura a la heurística: si un humano ya puso la
    clase en Manager, el job la respeta y reporta el desacuerdo como conflicto."""
    rows = [{"unidad": _UNIDAD_FIN, "cartera": "FINANCIAMIENTO",
             "ticker": "*BIN031000050", "vencimiento": "2026-10-03",
             "clase_activo": "HD", "nominal": 27_000_000}]   # la regla diría DL
    cambios, reporte = planificar(rows, REGLAS)
    assert "clase_activo" not in cambios.get(_UNIDAD_FIN, {})
    conflictos = reporte["financiamiento_clase"]["conflictos"]
    assert len(conflictos) == 1 and "clase_activo" in conflictos[0]


def test_fci_deriva_codigo_y_nombre():
    assert _regla_fci({"unidad": _UNIDAD_FCI}) == {
        "cartera": "FCI",
        "ticker": "Argenfunds Ahorro Pesos - Clase B",
        "cafci": "CAFCI518-1047",
    }


def test_planificar_completa_solo_lo_vacio():
    rows = [{"unidad": _UNIDAD_FIN, "cartera": None, "ticker": "",
             "vencimiento": "NO APLICA"}]
    cambios, _ = planificar(rows, REGLAS)
    assert cambios[_UNIDAD_FIN] == {"cartera": "FINANCIAMIENTO",
                                    "ticker": "*BIN031000050",
                                    "vencimiento": "2026-10-03"}


def test_planificar_no_pisa_lo_cargado_y_reporta_conflicto():
    rows = [{"unidad": _UNIDAD_FIN, "cartera": "HD",
             "ticker": "*BIN031000050", "vencimiento": None}]
    cambios, reporte = planificar(rows, REGLAS)
    # Solo se completa el vencimiento; la cartera cargada a mano queda intacta.
    assert cambios[_UNIDAD_FIN] == {"vencimiento": "2026-10-03"}
    conflictos = reporte["financiamiento"]["conflictos"]
    assert len(conflictos) == 1 and "cartera" in conflictos[0]


# ── Regla `ticker` (cualquier cartera) ───────────────────────────────────────

@pytest.mark.parametrize(("unidad", "esperado"), [
    # Sin descripción → el ticker es el id del corchete.
    ("[DLR012026]", "DLR012026"),
    ("[COMC17929J]", "COMC17929J"),
    ("[CRN.CME/AGO26]", "CRN.CME/AGO26"),
    # Con descripción y guion → hasta el primer guion.
    ("[43070] NZC6O - NZC6O - T.DEUDA BCO DE LA NACION ARG 6", "NZC6O"),
    ("[45698] TVPP - VALORES NEG VINC PBI $ 15/12/2035", "TVPP"),
    ("[56384] RB56O - ON ROMBO S. 56 $ VTO.19/08/25 C.G.", "RB56O"),
    ("[9131] YPFD - CEDEAR YPF", "YPFD"),
    # Con descripción y SIN guion → la descripción entera, sin romper.
    ("[43068] NZC4O", "NZC4O"),
    ("[620] INTRODUCT. ORD1V", "INTRODUCT. ORD1V"),
    ("[59573] ON PYME SION S.4 17/04/27 $", "ON PYME SION S.4 17/04/27 $"),
    ("[10390] Depósito U$S Ext", "Depósito U$S Ext"),
])
def test_ticker_desde_la_unidad(unidad, esperado):
    assert _regla_ticker({"unidad": unidad}) == {"ticker": esperado}


def test_ticker_no_toca_fci_ni_financiamiento():
    # FCI: el ticker es el nombre del fondo, no 'CAFCI518' (primer guion).
    assert _regla_ticker({"unidad": _UNIDAD_FCI}) == {}
    assert _regla_ticker({"unidad": "[1047] Fondo X", "cartera": "FCI"}) == {}
    # Financiamiento: ya lo resuelve su propia regla.
    assert _regla_ticker({"unidad": _UNIDAD_FIN}) == {}
    # Cash sin corchetes: no hay nada que derivar.
    assert _regla_ticker({"unidad": "ARS"}) == {}


# ── Regla `herencia` — el rebautizo de Aunesa ────────────────────────────────
#
# El caso REAL que la motivó: el cambio normativo reemitió el fondo con otro id
# de especie. Cambia el corchete, NO el código CAFCI ni el nombre.
_VIEJA = "[6461] CAFCI1910-6461 - DXA Multicobertura - Clase B"
_NUEVA = "[28902] CAFCI1910-6461 - DXA Multicobertura - Clase B"


def _fila(unidad, **campos):
    return {"unidad": unidad, **campos}


def test_herencia_el_rebautizo_le_pasa_el_emisor_a_la_unidad_nueva():
    rows = [_fila(_VIEJA, emisor="DRACMA", fee_admin=Decimal("0.0135")),
            _fila(_NUEVA)]
    rep = anotar_herencia(rows)
    cambios, reporte = planificar(rows, REGLAS)

    assert cambios[_NUEVA]["emisor"] == "DRACMA"
    assert cambios[_NUEVA]["fee_admin"] == Decimal("0.0135")
    # Lo que ya se derivaba solo sigue saliendo de la unidad, no de la gemela.
    assert cambios[_NUEVA]["cartera"] == "FCI"
    assert cambios[_NUEVA]["ticker"] == "DXA Multicobertura - Clase B"
    # La vieja no recibe nada (ya tiene todo) y nadie reporta conflicto.
    assert "emisor" not in cambios.get(_VIEJA, {})
    assert not any(r["conflictos"] for r in reporte.values())
    assert rep["receptoras"] == 1 and rep["divergencias"] == []
    # UN instrumento, aunque matchee por código Y por nombre.
    assert rep["grupos"] == 1 and rep["campos"] == {"emisor": 1, "fee_admin": 1}


def test_herencia_va_en_las_dos_direcciones():
    """Si la mesa carga el EMISOR en la unidad NUEVA, la vieja lo recibe.

    La vieja no se puede borrar (la tenencia histórica la referencia), así que
    tampoco puede quedarse sin metadata.
    """
    rows = [_fila(_VIEJA), _fila(_NUEVA, emisor="DRACMA")]
    anotar_herencia(rows)
    cambios, _ = planificar(rows, REGLAS)
    assert cambios[_VIEJA]["emisor"] == "DRACMA"


def test_herencia_no_pisa_lo_cargado_a_mano():
    rows = [_fila(_VIEJA, emisor="DRACMA"), _fila(_NUEVA, emisor="DRACMA S.A.")]
    anotar_herencia(rows)
    cambios, _ = planificar(rows, REGLAS)
    assert "emisor" not in cambios.get(_NUEVA, {})
    assert "emisor" not in cambios.get(_VIEJA, {})


def test_herencia_frena_si_los_donantes_no_se_ponen_de_acuerdo():
    """Dos valores distintos para el MISMO instrumento = uno está mal cargado.

    El job no elige por el humano: no escribe y lo reporta. Este desacuerdo es
    también lo que hace segura la clave por NOMBRE (dos fondos homónimos de
    emisores distintos se frenan solos en vez de contaminarse).
    """
    rows = [_fila(_VIEJA, emisor="DRACMA"),
            _fila("[28902] CAFCI1910-6461 - DXA Multicobertura - Clase B",
                  emisor="DRACMA S.A."),
            _fila("[99999] CAFCI1910-6461 - DXA Multicobertura - Clase B")]
    rep = anotar_herencia(rows)
    cambios, _ = planificar(rows, REGLAS)

    assert "emisor" not in cambios.get("[99999] CAFCI1910-6461 - DXA "
                                       "Multicobertura - Clase B", {})
    assert len(rep["divergencias"]) == 1
    assert "emisor" in rep["divergencias"][0]


def test_herencia_por_nombre_cuando_el_rebautizo_cambia_el_codigo_cafci():
    """Clave de respaldo: mismo fondo, código CAFCI distinto."""
    otra = "[28902] CAFCI2999-28902 - DXA Multicobertura - Clase B"
    rows = [_fila(_VIEJA, emisor="DRACMA"), _fila(otra)]
    anotar_herencia(rows)
    cambios, _ = planificar(rows, REGLAS)
    assert cambios[otra]["emisor"] == "DRACMA"


def test_herencia_no_confunde_fondos_distintos_del_mismo_emisor():
    rows = [_fila("[1] CAFCI1910-6461 - DXA Multicobertura - Clase B", emisor="DRACMA"),
            _fila("[2] CAFCI1910-7777 - DXA Renta Fija - Clase A")]
    rep = anotar_herencia(rows)
    assert rep["receptoras"] == 0
    assert _regla_herencia(rows[1]) == {}


def test_herencia_fuera_de_fci_se_cuenta_pero_no_se_escribe():
    """`HEREDAR_NO_FCI=False`: el resto del catálogo se mide antes de tocarlo."""
    rows = [_fila("[43070] NZC6O - NZC6O - T.DEUDA BNA", emisor="BNA"),
            _fila("[88888] NZC6O - NZC6O - T.DEUDA BNA")]
    rep = anotar_herencia(rows)
    cambios, _ = planificar(rows, REGLAS)

    assert rep["receptoras"] == 0 and rep["no_fci_receptoras"] == 1
    assert rep["no_fci_campos"] == {"emisor": 1}
    assert "emisor" not in cambios["[88888] NZC6O - NZC6O - T.DEUDA BNA"]


def test_herencia_sin_anotar_no_opina():
    """La regla es inerte si nadie le armó el contexto — no inventa un valor."""
    assert _regla_herencia({"unidad": _NUEVA}) == {}


def test_un_numeric_igual_al_guardado_no_es_conflicto():
    """`fee_admin` vuelve de Postgres como Decimal; el resto de las reglas
    propone strings. Sin normalizar, el motor compararía Decimal contra str y
    marcaría conflicto sobre un valor idéntico al que ya está guardado."""
    rows = [_fila(_VIEJA, fee_admin=Decimal("0.0135")),
            _fila(_NUEVA, fee_admin=Decimal("0.01350"))]
    anotar_herencia(rows)
    cambios, reporte = planificar(rows, REGLAS)
    assert not any("fee_admin" in c for r in reporte.values() for c in r["conflictos"])
    assert "fee_admin" not in cambios.get(_NUEVA, {})


def test_reglas_no_se_pisan_entre_si():
    rows = [{"unidad": _UNIDAD_FIN}, {"unidad": _UNIDAD_FCI},
            {"unidad": "[43070] NZC6O - NZC6O - T.DEUDA BCO DE LA NACION ARG 6"}]
    cambios, reporte = planificar(rows, REGLAS)
    assert not any(r["conflictos"] for r in reporte.values())
    assert cambios[_UNIDAD_FIN]["ticker"] == "*BIN031000050"
    assert cambios[_UNIDAD_FCI]["ticker"] == "Argenfunds Ahorro Pesos - Clase B"


# ── Regla `especies` — los dos símbolos de mercado ────────────────────────────

_AL30 = "[7823] AL30 - BONO REP. ARG. USD 2030 L.A."


def _esp(simbolo, ticker, especie, plazo="24hs", es_default=False):
    return {"simbolo": simbolo, "ticker": ticker, "especie": especie,
            "plazo": plazo, "es_default": es_default}


_ESPECIES_AL30 = [
    _esp("MERV - XMEV - AL30 - 24hs", "AL30", "pesos", es_default=True),
    _esp("MERV - XMEV - AL30 - CI", "AL30", "pesos", plazo="CI"),
    _esp("MERV - XMEV - AL30D - 24hs", "AL30", "mep"),
    _esp("MERV - XMEV - AL30C - 24hs", "AL30", "cable"),
]


def test_especies_baja_las_dos_patas_por_ticker():
    """El catálogo deja de ser una segunda verdad: los símbolos salen de especies."""
    rows = [_fila(_AL30, ticker="AL30")]
    anotar_especies(rows, _ESPECIES_AL30)
    cambios, _ = planificar(rows, REGLAS)
    assert cambios[_AL30]["instrumento"] == "MERV - XMEV - AL30 - 24hs"
    assert cambios[_AL30]["instrumento_usd"] == "MERV - XMEV - AL30D - 24hs"


def test_especies_el_cable_no_es_la_pata_usd():
    """`instrumento_usd` es MEP. Mezclar cable volvería a esconder cuál es cuál."""
    rows = [_fila(_AL30, ticker="AL30")]
    anotar_especies(rows, [_esp("MERV - XMEV - AL30C - 24hs", "AL30", "cable")])
    assert _regla_especies(rows[0]) == {}


def test_especies_prefiere_24hs_sobre_ci():
    """CI existe pero no es donde hay liquidez — y por lo tanto precio."""
    rows = [_fila(_AL30, ticker="AL30")]
    anotar_especies(rows, [_esp("MERV - XMEV - AL30 - CI", "AL30", "pesos", plazo="CI"),
                           _esp("MERV - XMEV - AL30 - 24hs", "AL30", "pesos")])
    assert _regla_especies(rows[0])["instrumento"] == "MERV - XMEV - AL30 - 24hs"


def test_especies_nunca_pisa_el_simbolo_cargado_a_mano():
    """EL invariante que hace seguro este cambio: lo que el motor suscribe hoy
    sigue igual. Un símbolo distinto al de especies se REPORTA, no se escribe."""
    rows = [_fila(_AL30, ticker="AL30", instrumento="MERV - XMEV - AL30D - 24hs")]
    anotar_especies(rows, _ESPECIES_AL30)
    cambios, reporte = planificar(rows, REGLAS)
    assert "instrumento" not in cambios.get(_AL30, {})
    assert any("instrumento" in c for c in reporte["especies"]["conflictos"])


def test_especies_deriva_el_ticker_de_la_unidad_si_esta_vacio():
    """El asset que el writer dio de alta hace 40' todavía no tiene TICKER
    escrito (lo completa la regla `ticker` en esta misma corrida)."""
    rows = [_fila(_AL30)]
    rep = anotar_especies(rows, _ESPECIES_AL30)
    assert rep["con_pata_ars"] == 1
    assert _regla_especies(rows[0])["instrumento"] == "MERV - XMEV - AL30 - 24hs"


def test_especies_sin_anotar_no_opina():
    assert _regla_especies({"unidad": _AL30, "ticker": "AL30"}) == {}
