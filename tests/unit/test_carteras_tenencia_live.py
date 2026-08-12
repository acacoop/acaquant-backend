"""CARTERAS sobre `portafolio.tenencia_live` — el cambio es OPT-IN y no puede filtrarse.

La vista de Carteras pasó a mostrar la posición del DÍA (t1, con lo concertado hoy
adentro) en vez de la foto conciliada de ayer. Pero el mismo motor de PnL y el
mismo cálculo mensual los comparten la vista VALUACIONES, el asistente de IA y el
cron que llena `valuaciones.pnl_totales_cache`.

Estos tests fijan las tres cosas que hacen que eso sea seguro:

  1. el DEFAULT no cambió — quien no pide `live_t1` sigue leyendo `tenencia`;
  2. hay FALLBACK — si el daemon no corrió, Carteras no queda vacía;
  3. `fecha_actual_aum_global` NO se mueve a hoy — es el corte de day-trades, y
     moverlo pondría `pnl_realizado_dia` en CERO sin que nadie se entere.

El (3) es el que justifica el archivo: no rompe nada visible, no falla ningún
otro test, y vacía una columna de la pantalla en silencio.
"""
from __future__ import annotations

import pytest

from api.services import pnl_sql, valuaciones


# ── helpers ───────────────────────────────────────────────────────────────────
def _fake_q(respuestas: dict, vistas: list):
    """_q falso: matchea por substring de la query y registra qué se preguntó."""
    def _q(sql, params=None):
        vistas.append(sql)
        for marca, filas in respuestas.items():
            if marca in sql:
                return filas
        return []
    return _q


def _patch_deps(monkeypatch, respuestas: dict) -> list:
    """Aísla `_deps_sql` de todo menos de la query de posición."""
    vistas: list[str] = []
    monkeypatch.setattr(pnl_sql, "_q", _fake_q(respuestas, vistas))
    monkeypatch.setattr(pnl_sql, "_mapas_assets", lambda: {
        "unidad_to_match": {}, "match_to_display": {}, "instrumentos_by_unidad": {}})
    monkeypatch.setattr(pnl_sql, "_pricing_live", lambda: {})
    monkeypatch.setattr(pnl_sql, "_pricing_cierre", lambda: {})
    monkeypatch.setattr(pnl_sql, "_boletos_by_cuenta", lambda only_cuenta: {})
    return vistas


def _fila(unidad: str, cantidad: float) -> dict:
    return {"id_cuenta": "805", "unidad": unidad, "cantidad": cantidad, "precio": 1.0,
            "valuacion": cantidad, "tipo_titulo": None, "cartera": "ARS"}


_FOTO = {"max(fecha) AS f": [{"f": "2026-08-11"}],
         "FROM portafolio.tenencia WHERE": [_fila("AL30", 100.0)]}
_LIVE = {"FROM portafolio.tenencia_live WHERE": [_fila("AL30", 140.0)]}


# ── 1) el default no cambió ───────────────────────────────────────────────────
def test_default_lee_la_foto_y_ni_toca_tenencia_live(monkeypatch):
    """Quien no pide nada — VALUACIONES, el asistente, el cron de totales — tiene
    que seguir viendo exactamente lo de antes."""
    vistas = _patch_deps(monkeypatch, {**_FOTO, **_LIVE})
    deps = pnl_sql._deps_sql(only_cuenta="805")

    assert deps["aum_rows_by_id_cuenta"]["805"][0]["cantidad"] == 100.0
    assert not any("tenencia_live" in s for s in vistas), \
        "el default consultó tenencia_live — el cambio se filtró fuera de Carteras"


# ── 2) opt-in + fallback ──────────────────────────────────────────────────────
def test_live_t1_toma_la_posicion_del_dia(monkeypatch):
    _patch_deps(monkeypatch, {**_FOTO, **_LIVE})
    deps = pnl_sql._deps_sql(only_cuenta="805", base="live_t1")
    assert deps["aum_rows_by_id_cuenta"]["805"][0]["cantidad"] == 140.0


def test_sin_daemon_cae_a_la_foto_en_vez_de_quedar_vacia(monkeypatch):
    """Fin de semana, daemon caído o tabla sin aplicar: la vista NO puede quedar
    en blanco. Cae sola a la foto conciliada."""
    _patch_deps(monkeypatch, {**_FOTO, "FROM portafolio.tenencia_live WHERE": []})
    deps = pnl_sql._deps_sql(only_cuenta="805", base="live_t1")
    assert deps["aum_rows_by_id_cuenta"]["805"][0]["cantidad"] == 100.0


# ── 3) LA TRAMPA: el corte de day-trades no se mueve ──────────────────────────
@pytest.mark.parametrize("base", ["tenencia", "live_t1"])
def test_el_corte_de_day_trades_sigue_en_la_foto_conciliada(monkeypatch, base):
    """`fecha_actual_aum_global` decide qué boleto es "del día"
    (`boleto.fecha > fecha_actual_aum` → `pnl_realizado_dia`).

    Si con `live_t1` pasara a ser HOY, ningún boleto de hoy sería posterior y la
    columna de PnL del día quedaría en CERO — sin error, sin log, sin test rojo.
    Tiene que seguir apuntando a la foto conciliada (ayer) en los DOS modos.
    """
    _patch_deps(monkeypatch, {**_FOTO, **_LIVE})
    deps = pnl_sql._deps_sql(only_cuenta="805", base=base)
    assert deps["fecha_actual_aum_global"] == "2026-08-11"


# ── 4) mensual: solo el mes EN CURSO ──────────────────────────────────────────
def _patch_meses(monkeypatch, cierres: list, live: dict | None):
    from api.services import valuaciones_sql
    monkeypatch.setattr(valuaciones, "_cierres_fecha_data", lambda i, c=None: cierres)
    monkeypatch.setattr(valuaciones_sql, "cierre_live_t1", lambda i, c=None: live)
    monkeypatch.setattr(valuaciones, "negocio_movimientos_rows", lambda **kw: [])
    monkeypatch.setattr(valuaciones, "_get_mep_for_date", lambda f: 1000.0)


_CIERRES = [{"_id": "2026-07-31", "valuacion": 1000.0, "n": 3},
            {"_id": "2026-08-11", "valuacion": 1100.0, "n": 3}]
_LIVE_HOY = {"_id": "2026-08-12", "valuacion": 1234.0, "n": 4}


def test_mensual_default_no_toca_nada(monkeypatch):
    _patch_meses(monkeypatch, _CIERRES, _LIVE_HOY)
    meses = valuaciones._calcular_meses("805")
    agosto = next(m for m in meses if m["mes"] == "2026-08")
    assert agosto["cierre"] == 1100.0
    assert agosto["live"] is False


def test_mensual_live_pisa_solo_el_mes_en_curso(monkeypatch):
    _patch_meses(monkeypatch, _CIERRES, _LIVE_HOY)
    meses = valuaciones._calcular_meses("805", mes_actual_live=True)
    por_mes = {m["mes"]: m for m in meses}
    assert por_mes["2026-08"]["cierre"] == 1234.0
    assert por_mes["2026-08"]["ultimo_dia"] == "2026-08-12"
    assert por_mes["2026-08"]["live"] is True
    # El mes anterior es intocable: es histórico conciliado.
    assert por_mes["2026-07"]["cierre"] == 1000.0
    assert por_mes["2026-07"]["live"] is False


def test_mensual_live_no_inventa_un_mes_que_no_existe(monkeypatch):
    """Si la cuenta no tiene historia en el mes del dato live, no se crea un bucket
    de la nada: aparecería un mes sin valor de inicio y su rendimiento sería
    cualquier cosa."""
    solo_julio = [{"_id": "2026-07-31", "valuacion": 1000.0, "n": 3}]
    _patch_meses(monkeypatch, solo_julio, _LIVE_HOY)
    meses = valuaciones._calcular_meses("805", mes_actual_live=True)
    assert [m["mes"] for m in meses] == ["2026-07"]


def test_mensual_sin_daemon_deja_el_mes_como_estaba(monkeypatch):
    _patch_meses(monkeypatch, _CIERRES, None)
    meses = valuaciones._calcular_meses("805", mes_actual_live=True)
    agosto = next(m for m in meses if m["mes"] == "2026-08")
    assert agosto["cierre"] == 1100.0
    assert agosto["live"] is False


# ── 5) el PRECIO de la vista: nuestro, no el de Aunesa ────────────────────────
#
# La posición pasó a ser del día, pero la columna PRECIO seguía saliendo cruda de
# `posicionValuada` — o sea el cierre de ayer mientras el mercado se mueve. Ahora
# se pisa con la MISMA cadena del motor de PnL. Estos tests fijan las dos guardas
# que evitan que ese pisado haga daño, y que la valuación acompañe al precio (si
# se pisa uno y no el otro, la fila se contradice sola).
def _porfolio(unidad="[8032] MSFT", cartera="RV", cantidad=1.15, precio=26520.0):
    return {unidad: {"unidad": unidad, "cantidad": cantidad, "precio": precio,
                     "valuacion": cantidad * precio, "cartera": cartera,
                     "clase_activo": None, "ticker": "MSFT", "emisor": None,
                     "calificacion": None}}


def _patch_pricing(monkeypatch, instrumentos, snap, cierre=None):
    from api.services import pnl_sql
    monkeypatch.setattr(pnl_sql, "_mapas_assets", lambda: {
        "unidad_to_match": {}, "match_to_display": {},
        "instrumentos_by_unidad": instrumentos})
    monkeypatch.setattr(pnl_sql, "_pricing_live", lambda: snap)
    monkeypatch.setattr(pnl_sql, "_pricing_cierre", lambda: cierre or {})


_INSTR = {"[8032] MSFT": "MERV - XMEV - MSFT - 24hs"}


def test_precio_de_la_vista_usa_el_live_y_no_el_de_aunesa(monkeypatch):
    from api.services import valuaciones_sql
    _patch_pricing(monkeypatch, _INSTR,
                   {"MERV - XMEV - MSFT - 24hs": {"last_price": 26100.0,
                                                  "closing_price": 26520.0}})
    pos = _porfolio()
    valuaciones_sql._pisar_precio_live(pos)
    x = pos["[8032] MSFT"]
    assert x["precio"] == 26100.0, "mostró el cierre de Aunesa en vez del live"
    assert x["fuente_precio"] == "live"
    # RV → ×1 (nunca ÷100). La valuación tiene que seguir al precio nuevo.
    assert x["valuacion"] == pytest.approx(1.15 * 26100.0)


def test_sin_last_price_cae_al_closing_y_despues_al_cierre(monkeypatch):
    from api.services import valuaciones_sql
    _patch_pricing(monkeypatch, _INSTR,
                   {"MERV - XMEV - MSFT - 24hs": {"last_price": None,
                                                  "closing_price": 26520.0}})
    pos = _porfolio()
    valuaciones_sql._pisar_precio_live(pos)
    assert pos["[8032] MSFT"]["precio"] == 26520.0

    _patch_pricing(monkeypatch, _INSTR, {},
                   {"MERV - XMEV - MSFT - 24hs": {"last_price": 26400.0}})
    pos = _porfolio()
    valuaciones_sql._pisar_precio_live(pos)
    assert pos["[8032] MSFT"]["precio"] == 26400.0
    assert pos["[8032] MSFT"]["fuente_precio"] == "cierre"


def test_sin_instrumento_se_queda_con_el_de_aunesa(monkeypatch):
    """Cash, FCI y títulos sin mapear: no hay precio nuestro. Se deja el de
    Aunesa — poner cero o inventarlo sería peor que mostrar el de ayer."""
    from api.services import valuaciones_sql
    _patch_pricing(monkeypatch, {}, {})
    pos = _porfolio(unidad="ARS", cartera="MONEDAS", cantidad=525.74, precio=1525.78)
    valuaciones_sql._pisar_precio_live(pos)
    assert pos["ARS"]["precio"] == 1525.78
    assert pos["ARS"]["fuente_precio"] == "aum"


def test_sin_cartera_no_se_pisa_aunque_haya_precio(monkeypatch):
    """El divisor lo decide la CARTERA (renta fija ÷100, el resto ×1) y acá
    `tipoTitulo` es None. Sin cartera el normalizador erraría por 100× — con la
    duda no se toca."""
    from api.services import valuaciones_sql
    _patch_pricing(monkeypatch, _INSTR,
                   {"MERV - XMEV - MSFT - 24hs": {"last_price": 26100.0}})
    pos = _porfolio(cartera="")
    valuaciones_sql._pisar_precio_live(pos)
    assert pos["[8032] MSFT"]["precio"] == 26520.0, "pisó el precio sin saber el divisor"


def test_renta_fija_divide_por_cien_al_valuar(monkeypatch):
    """AO29 cotiza en paridad: la valuación es cantidad × precio / 100. Si se
    pisara el precio sin re-aplicar el normalizador, la fila se iría 100× arriba."""
    from api.services import valuaciones_sql
    instr = {"[9422] AO29": "MERV - XMEV - AO29 - 24hs"}
    _patch_pricing(monkeypatch, instr,
                   {"MERV - XMEV - AO29 - 24hs": {"last_price": 14500.0}})
    pos = _porfolio(unidad="[9422] AO29", cartera="HD", cantidad=41.4, precio=14343.0)
    valuaciones_sql._pisar_precio_live(pos)
    x = pos["[9422] AO29"]
    assert x["precio"] == 14500.0
    assert x["valuacion"] == pytest.approx(41.4 * 14500.0 / 100)


def test_el_pisado_no_corre_en_consulta_historica(monkeypatch):
    """Con `fecha` la consulta es de un día pasado y el precio de ESE día es
    justamente el de Aunesa. El pisado solo puede correr en modo live."""
    import inspect

    from api.services import valuaciones_sql
    src = inspect.getsource(valuaciones_sql.posiciones_actuales)
    assert "if desde_live:\n        _pisar_precio_live" in src, \
        "el pisado dejó de estar condicionado a desde_live"
