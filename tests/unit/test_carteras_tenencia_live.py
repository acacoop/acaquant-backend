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
