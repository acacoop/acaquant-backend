"""POR QUÉ un bono no tiene precio — la cadena que distingue las cinco causas.

El caso testigo es AO29: toda la fila en `--`, y **ni un error en el log del
motor**. Ese silencio es la pista, no un detalle.
"""
from __future__ import annotations

import inspect

from api.services import av_agent_sin_precio as sp


def _d(**kw):
    base = {"ticker": "AO29", "en_master": True, "simbolo": "MERV - XMEV - AO29 - 24hs",
            "curva": "globales", "moneda_eje": "USD", "patas": [], "precios": {},
            "en_primary": True, "catalogo": 12900, "trades_recientes": 0,
            "cierres": 0}
    return {**base, **kw}


# ── La causa, ordenada aguas arriba ─────────────────────────────────────────

def test_sin_simbolo_gana_sobre_todo_lo_demas():
    """Sin símbolo el motor no pide nada; que además «nunca haya operado» es
    consecuencia, no causa. Sin este orden, el diagnóstico sería cierto e
    inútil."""
    assert sp._causa(_d(simbolo="")) == "sin_simbolo"


def test_fuera_de_primary_gana_sobre_nunca_opero():
    assert sp._causa(_d(en_primary=False)) == "fuera_de_primary"


def test_si_OTRA_pata_tiene_precio_la_causa_es_cual_se_pide():
    """El dato existe; lo que está mal es el símbolo. Es la misma idea del
    control cruzado: no se afirma la causa, se muestra el contraste."""
    d = _d(patas=[{"simbolo": "MERV - XMEV - AO29D - 24hs", "especie": "mep",
                   "moneda": "USD", "plazo": "24hs", "default": False,
                   "validado": True}],
           precios={"MERV - XMEV - AO29D - 24hs": {"last_price": 92.3}})
    assert sp._causa(d) == "pata_equivocada"


def test_nunca_opero_es_ILIQUIDEZ_y_no_un_error():
    """Distinguirlo importa tanto como los otros: perseguir un bug que no existe
    cuesta más que el papel que no opera."""
    assert sp._causa(_d()) == "nunca_opero"
    assert sp.CAUSAS_SIN_PRECIO["nunca_opero"]["nuestro"] is False


def test_si_ya_opero_antes_es_sin_actividad_HOY():
    assert sp._causa(_d(cierres=120)) == "sin_actividad_hoy"


def test_con_precio_no_hay_nada_que_diagnosticar():
    d = _d(precios={"MERV - XMEV - AO29 - 24hs": {"last_price": 92.3}})
    assert sp._causa(d) == "tiene_precio"


# ── El silencio del log, explicado ──────────────────────────────────────────

def test_la_lente_del_SIMBOLO_explica_por_que_el_log_esta_limpio():
    """**Es la observación del user convertida en diagnóstico.** El filtro de
    símbolos escribe un warning cuando descarta algo; si no hay warning, el
    símbolo no llegó hasta él. El silencio no es «no hay problema»: es el
    problema."""
    p = sp._lente_simbolo(_d(simbolo=""))
    assert p["estado"] == "revisar"
    assert "tampoco hay un warning" in p["detalle"]


def test_fuera_de_primary_dice_que_SI_deja_warning():
    """La contracara: acá el log SÍ tiene que tener la línea, y decirlo le da al
    que lee una forma de confirmar el diagnóstico por su cuenta."""
    p = sp._lente_primary(_d(en_primary=False))
    assert "warning" in p["detalle"] and "no se suscriben" in p["detalle"]


def test_sin_catalogo_no_se_afirma_ni_que_existe_ni_que_no():
    """`validos()` devuelve None cuando el catálogo no está disponible, y ahí el
    filtro no filtra nada. Decir «no está» sería inventar."""
    p = sp._lente_primary(_d(en_primary=None))
    assert p["estado"] == "no_se_puede_saber"


# ── Lo que no hace ──────────────────────────────────────────────────────────

def test_no_escribe_en_ninguna_tabla_de_datos():
    src = inspect.getsource(sp)
    for t in ("mercado.curvas", "mercado.especies", "mercado.market_snapshot"):
        assert f"UPDATE {t}" not in src and f"INSERT INTO {t}" not in src


def test_no_pega_a_1816():
    src = inspect.getsource(sp)
    assert "mercado_1816" not in src


def test_todo_sale_de_UNA_conexion():
    """El peaje a Supabase se paga por VIAJE: cinco lentes con su propia
    conexión serían cinco viajes para una pantalla que tiene que abrir rápido."""
    assert inspect.getsource(sp._fila).count("get_pool()") == 1
