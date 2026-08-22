"""Tests del INFORME de carteras (api/services/carteras_informe.py).

La vista NEGOCIO → CARTERAS pasó a servirse en un request y con los totales
resueltos del lado del backend. Lo que se cubre acá es exactamente lo que antes
se calculaba en el navegador y por lo tanto podía contradecir al informe: la
composición por cartera, los totales por moneda, el reparto de la plata sin
ficha y los dos denominadores de MÉTRICAS.

Lógica PURA — ninguna de estas funciones toca Postgres.
"""
from __future__ import annotations

import pytest

from api.services import carteras_informe as ci

# Las reglas de Manager → ACA tal como vienen sembradas. Se pasan explícitas para
# que el test no dependa de lo que tenga la base.
REGLAS = {
    "cartera": {"HD": "usd", "DL": "usd", "ARS": "ars"},
    "clase": {"MM USD": "usd", "MM ARS": "ars", "RENTA VARIABLE": "ars"},
}


def _pos(unidad, cartera, valuacion, clase="", emisor="", calif="", ticker=None):
    return {"unidad": unidad, "ticker": ticker or unidad, "cartera": cartera,
            "clase_activo": clase, "emisor": emisor, "calificacion": calif,
            "valuacion": valuacion}


def _resp(posiciones, fecha="2026-08-21", mep=1500.0):
    total = sum(p["valuacion"] for p in posiciones)
    return {"id_cuenta": "805", "fecha": fecha, "mep": mep,
            "posiciones": posiciones, "total": total,
            "total_usd": round(total / mep, 2) if mep else None, "n": len(posiciones)}


# ── Moneda de una posición ──────────────────────────────────────────────────

def test_el_efectivo_resuelve_su_moneda_por_el_instrumento():
    """La cartera MONEDAS tiene los pesos Y los dólares de la cuenta adentro, así
    que ninguna regla por cartera puede partirla. Para el cash el instrumento ES
    la moneda — si esto se rompe, los dólares de la cuenta caen en 'sin
    clasificar' y el Total Dolarizado queda corto sin que nada falle."""
    assert ci._moneda_de(_pos("USD", "MONEDAS", 100), REGLAS) == "usd"
    assert ci._moneda_de(_pos("USDC", "MONEDAS", 100), REGLAS) == "usd"
    assert ci._moneda_de(_pos("ARS", "MONEDAS", 100), REGLAS) == "ars"


def test_fuera_del_cash_manda_la_regla_de_manager_aca():
    assert ci._moneda_de(_pos("AL30", "HD", 100), REGLAS) == "usd"
    assert ci._moneda_de(_pos("X", "FCI", 100, clase="MM USD"), REGLAS) == "usd"


def test_lo_que_ninguna_regla_ubica_no_se_reparte_a_dedo():
    """Cae en sin_clasificar y la vista lo canta. Repartirlo por defecto a pesos
    haría que el informe cierre y esté mal, que es peor que no cerrar."""
    assert ci._moneda_de(_pos("X", "DERIVADOS", 100), REGLAS) is None


# ── RESUMEN ─────────────────────────────────────────────────────────────────

def test_bloque_reparte_por_cartera_y_por_moneda():
    pos = _resp([
        _pos("AL30", "HD", 600.0),
        _pos("GGAL", "RENTA VARIABLE", 300.0, clase="RENTA VARIABLE"),
        _pos("USD", "MONEDAS", 100.0),
    ])
    b = ci._bloque(pos, ["HD", "RENTA VARIABLE", "MONEDAS"], REGLAS)
    montos = {c["cartera"]: c["monto"] for c in b["carteras"]}
    assert montos == {"HD": 600.0, "RENTA VARIABLE": 300.0, "MONEDAS": 100.0}
    assert b["carteras"][0]["ponderacion"] == pytest.approx(0.6)
    # HD (600) + el cash en USD (100); RV va a pesos por regla de clase.
    assert b["total_dolarizado"]["monto"] == 700.0
    assert b["total_pesos"]["monto"] == 300.0
    assert b["sin_clasificar"]["monto"] == 0.0


def test_bloque_nombra_lo_que_no_supo_clasificar():
    pos = _resp([_pos("DLR/DIC", "DERIVADOS", 500.0, clase="FUTURO")])
    b = ci._bloque(pos, ["DERIVADOS"], REGLAS)
    assert b["sin_clasificar"]["monto"] == 500.0
    assert b["sin_clasificar"]["claves"] == ["FUTURO"]


def test_una_cartera_que_dejo_de_existir_no_pierde_su_plata():
    """El orden de filas lo fija el mes ACTUAL. Una cartera que el mes pasado
    valía algo y hoy no existe no tiene fila propia, así que su monto va a
    `otras_carteras` — si se descartara, el cuadro comparativo sumaría menos que
    su propio total y nadie lo notaría."""
    prev = _resp([_pos("AL30", "HD", 400.0), _pos("SOJ", "AGRO", 100.0)])
    b = ci._bloque(prev, ["HD"], REGLAS)
    assert b["otras_carteras"]["monto"] == 100.0
    assert b["valuacion_ars"] == 500.0


def test_ponderacion_es_none_con_total_cero():
    b = ci._bloque(_resp([]), [], REGLAS)
    assert b["total_pesos"]["ponderacion"] is None
    assert b["valuacion_ars"] == 0.0


# ── ACTIVOS ─────────────────────────────────────────────────────────────────

def test_detalle_agrupa_por_cartera_y_pesa_dentro_del_bloque():
    pos = _resp([
        _pos("AL30", "HD", 600.0), _pos("GD30", "HD", 200.0),
        _pos("GGAL", "RENTA VARIABLE", 200.0),
    ])
    d = ci._detalle(pos, ["HD", "RENTA VARIABLE"])
    hd = d["bloques"][0]
    assert hd["cartera"] == "HD" and hd["total"] == 800.0
    # share_cartera es sobre la CARTERA (600/800), no sobre la cuenta (600/1000).
    assert hd["filas"][0]["share_cartera"] == pytest.approx(0.75)
    assert hd["ponderacion"] == pytest.approx(0.8)


def test_el_titulo_sin_ficha_tiene_su_bloque_y_va_ultimo():
    """Sin ficha en Manager → Títulos no hay cartera. La plata NO se esconde: se
    ve en su propio cuadro (último) y además se lista para el aviso."""
    pos = _resp([_pos("RARO", "", 50.0), _pos("AL30", "HD", 950.0)])
    d = ci._detalle(pos, ["HD"])
    assert [b["cartera"] for b in d["bloques"]] == ["HD", ""]
    assert d["bloques"][-1]["label"] == "Sin cartera"
    assert d["huerfanos"] == ["RARO"]
    assert d["total"] == 1000.0


# ── MÉTRICAS ────────────────────────────────────────────────────────────────

def test_los_dos_denominadores_no_son_el_mismo():
    """La clase de activo responde cómo se compone ESA cartera (denominador: la
    cartera); el emisor responde cuánto pesa ese riesgo en toda la cuenta
    (denominador: el total). Usar el mismo para los dos es el error que hace que
    un informe muestre porcentajes que no suman a nada."""
    pos = _resp([
        _pos("AL30", "HD", 600.0, clase="SOBERANO", emisor="TESORO"),
        _pos("YMCI", "HD", 200.0, clase="ON", emisor="YPF"),
        _pos("GGAL", "RENTA VARIABLE", 200.0, clase="ACCION", emisor="GALICIA"),
    ])
    m = ci._metricas(pos, ["HD", "RENTA VARIABLE"])
    hd = next(b for b in m["por_clase"] if b["cartera"] == "HD")
    assert hd["total"] == 800.0
    # SOBERANO dentro de HD: 600/800, no 600/1000.
    assert hd["filas"][0]["share"] == pytest.approx(0.75)
    tesoro = next(f for f in m["por_emisor"] if f["clave"] == "TESORO")
    assert tesoro["share"] == pytest.approx(0.6)


def test_agrupar_ordena_por_monto_y_cuenta_titulos():
    filas = [_pos("A", "HD", 10.0, emisor="X"), _pos("B", "HD", 30.0, emisor="Y"),
             _pos("C", "HD", 20.0, emisor="X")]
    out = ci._agrupar(filas, "emisor", 60.0, 1500.0)
    assert [f["clave"] for f in out] == ["X", "Y"]
    assert out[0] == {"clave": "X", "monto": 30.0, "monto_usd": 0.02, "n": 2,
                      "share": pytest.approx(0.5)}


def test_el_campo_vacio_no_desaparece():
    """Un emisor sin cargar es una fila '—' con su plata, no un agujero: la suma
    de las filas de una métrica tiene que dar el total del bloque."""
    out = ci._agrupar([_pos("A", "HD", 10.0, emisor="")], "emisor", 10.0)
    assert out[0]["clave"] == "—" and out[0]["monto"] == 10.0


# ── Espejo USD de los agregados ─────────────────────────────────────────────

def test_el_toggle_usd_no_obliga_al_front_a_hacer_una_cuenta():
    """Cada agregado viaja con su `monto_usd` al MEP del snapshot. Si el front
    dividiera por el MEP, la pantalla tendría su propia copia de la fórmula —
    exactamente lo que este refactor vino a sacar."""
    pos = _resp([_pos("AL30", "HD", 1500.0)], mep=1500.0)
    b = ci._bloque(pos, ["HD"], REGLAS)
    assert b["carteras"][0]["monto_usd"] == 1.0
    assert b["total_dolarizado"]["monto_usd"] == 1.0


def test_sin_mep_el_espejo_usd_es_none_y_no_cero():
    """Sin feed de MEP para esa fecha el front deshabilita el toggle. Un 0 se
    leería como 'la cuenta no vale nada en dólares'."""
    pos = _resp([_pos("AL30", "HD", 1000.0)], mep=None)
    b = ci._bloque(pos, ["HD"], REGLAS)
    assert b["carteras"][0]["monto_usd"] is None
    assert b["carteras"][0]["monto"] == 1000.0


def test_la_ponderacion_no_depende_de_la_moneda():
    """Mismo divisor arriba y abajo: el porcentaje es el mismo en ARS y en USD.
    Por eso NO se duplica — dos campos que siempre valen lo mismo son dos
    lugares donde puede aparecer una diferencia."""
    pos = _resp([_pos("AL30", "HD", 750.0), _pos("USD", "MONEDAS", 250.0)], mep=1000.0)
    b = ci._bloque(pos, ["HD", "MONEDAS"], REGLAS)
    hd = b["carteras"][0]
    assert hd["ponderacion"] == pytest.approx(0.75)
    assert hd["monto_usd"] == pytest.approx(hd["monto"] / 1000.0)


# ── El segundo tipo de cambio ───────────────────────────────────────────────

def test_la_valuacion_al_oficial_sale_del_mismo_total():
    """MEP y A3500 son la MISMA plata a dos cambios. Si alguna vez salieran de
    dos totales distintos, el informe mostraría dos patrimonios."""
    pos = _resp([_pos("AL30", "HD", 1_000_000.0)], mep=1250.0)
    b = ci._bloque(pos, ["HD"], REGLAS, a3500=1000.0)
    assert b["valuacion_ars"] == 1_000_000.0
    assert b["valuacion_a3500"] == 1_000.0
    assert b["a3500"] == 1000.0


def test_sin_a3500_la_card_queda_vacia_y_no_en_cero():
    pos = _resp([_pos("AL30", "HD", 1_000.0)], mep=1000.0)
    b = ci._bloque(pos, ["HD"], REGLAS, a3500=None)
    assert b["valuacion_a3500"] is None
    assert b["valuacion_ars"] == 1_000.0


def test_cada_bloque_de_clase_dice_cuanto_pesa_en_la_cuenta():
    """El panel muestra dos porcentajes distintos y hay que no confundirlos: la
    clase pesa sobre SU cartera, y la cartera sobre la cuenta. El segundo lo
    manda el backend para que la pantalla no lo derive por su cuenta."""
    pos = _resp([
        _pos("AL30", "HD", 750.0, clase="SOBERANO"),
        _pos("GGAL", "RENTA VARIABLE", 250.0, clase="ACCION"),
    ])
    m = ci._metricas(pos, ["HD", "RENTA VARIABLE"])
    hd = next(b for b in m["por_clase"] if b["cartera"] == "HD")
    assert hd["ponderacion"] == pytest.approx(0.75)     # sobre la cuenta
    assert hd["filas"][0]["share"] == pytest.approx(1.0)  # sobre la cartera
