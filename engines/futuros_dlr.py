"""Motor de futuros DLR (Dólar A3500) — outrights single-leg.

Discovery dinámico: cada N minutos consulta pyRofex.get_detailed_instruments()
y filtra los outrights vigentes del underlying 'Dólar USA A3500':
  - cficode == 'FXXXSX' (outrights, no calendar spreads que son FXXXXX)
  - maturityDate > hoy
  - ticker tiene exactamente 1 '/' (DLR/MMMYY) — no DLR/MMMYY/MMMYY (spreads)
  - ticker NO termina en 'M' (variantes paralelas, no las queremos)
Un contrato nuevo listado durante la rueda se suscribe EN CALIENTE
(antes el rediscovery solo logueaba "restart del motor para tomar cambios").

Persistencia:
- mercado.futuros_dlr_snapshot: 1 fila por ticker (data jsonb), UPSERT cada 5s.
- mercado.mercado_hist (coleccion='FuturosDLR', k=ticker): 1 fila por
  (fecha, ticker) escrita al apagado del motor (cierre 20:05 UTC).

Tasa implícita: TNA LINEAL = (precio_dlr/spot - 1) × (365 / dias_a_vto).
Antes era TEA compuesta ((precio/spot)^(365/dias) - 1) — la mesa pidió
TNA lineal porque es lo que muestra el terminal Rofex y los traders
comparan tasas en esa convención. Para vencimientos largos (>180 días)
la TNA lineal es 2-4 puntos más baja que la TEA compuesta.

Se persisten 3 tasas separadas — sobre bid, sobre last y sobre offer.
La principal (`tasa_implicita_tna`) es la del last; las otras dos
quedan en `_bid` y `_offer` para mostrar dispersión en la watchlist.

Spot de referencia — feed MAE mayorista UST$T plazo 000:
    1. Valuaciones.DolarOficialLive (script local PC oficina) vía
       core.dolar_oficial.mid_oficial_live. Es el spot que liquida los
       DLR (mayorista A3500 contado).
    2. macro.series_macro DOLAR (BCRA A3500 fixing diario) — fallback si
       MAE está caído (PC apagada).
    3. valuaciones.dolar mep — último fallback para que nunca quede None.

Esqueleto del proceso (señales / WS / snapshot-loop / run): engines/_motor_base.

Ejecutar:
    python -m engines.futuros_dlr
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pyRofex

from core.logs import configurar
from engines._motor_base import (
    SnapshotEngine,
    correr_motor,
    dias_a_vto,
    maturity,
    ticker_de,
)

# El formato (con NIVEL) vive en core/logs — ver `AGENT.md` §0.ac.
configurar()
logger = logging.getLogger("MotorFuturosDLR")

INTERVALO_SNAPSHOT_S = 5
# get_detailed_instruments() baja el padrón COMPLETO de ROFEX (REST pesado) —
# el padrón cambia de a días, no de a minutos. Ahora el resultado sí se usa:
# contratos nuevos se suscriben en caliente (ver _motor_base._maybe_rediscover).
INTERVALO_REDISCOVERY_S = 1800   # cada 30 min re-evalúa universo de tickers

UNDERLYING_DLR = "Dólar USA A3500"
CFICODE_OUTRIGHT = "FXXXSX"


def descubrir_outrights_dlr() -> list[tuple[str, str]]:
    """Devuelve [(ticker, maturity_yyyymmdd)] de outrights DLR vigentes.

    Filtros aplicados (en orden, por seguridad):
    1. underlying == 'Dólar USA A3500'
    2. cficode == 'FXXXSX'
    3. maturityDate > hoy
    4. ticker tiene exactamente 1 '/' (descarta calendar spreads como
       'DLR/MAY26/JUN26')
    5. ticker NO termina en 'M' (descarta variantes paralelas como
       'DLR/JUN26M')
    """
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return []

    hoy_str = datetime.now().strftime("%Y%m%d")
    out: list[tuple[str, str]] = []
    for inst in res.get("instruments") or []:
        if inst.get("underlying") != UNDERLYING_DLR:
            continue
        if inst.get("cficode") != CFICODE_OUTRIGHT:
            continue
        mat = maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = ticker_de(inst)
        if not ticker:
            continue
        if ticker.count("/") != 1:
            continue
        if ticker.endswith("M"):
            continue
        out.append((ticker, mat))
    out.sort(key=lambda x: x[1])  # por vencimiento ascendente
    return out


def _spot_referencia() -> tuple[float | None, str]:
    """Spot de referencia para calcular tasa implícita. Devuelve (valor, fuente).

    Prefiere precioUltimo del oficial mayorista (UST$T MAE) vía
    core.dolar_oficial — fuente única compartida con el watchlist ARGY.
    Cae a A3500 BCRA fixing si el feed MAE está caído, y finalmente a MEP.
    """
    # 1) Spot mayorista MAE — fuente única
    from core.dolar_oficial import mid_oficial_live
    live = mid_oficial_live("oficial")
    if live.get("value"):
        return float(live["value"]), live.get("source") or "oficial_mae"
    # Si MAE está offline (PC apagada, etc.), value=None y caemos al fallback.

    # 2) A3500 BCRA fixing diario (SQL-only: macro.series_macro)
    from core.series_macro import ultimo_valor
    v = ultimo_valor("DOLAR", positivo=True)
    if v is not None:
        return v, "a3500_bcra"

    # 3) Último fallback: MEP (incorrecto conceptualmente pero mejor que None)
    from core import dolar_sql
    doc = dolar_sql.ultimo("mep")
    if doc and doc.get("mep"):
        return float(doc["mep"]), "mep_fallback"

    return None, "none"


def _tasa_implicita_tna(precio_dlr: float | None, spot: float | None, dias: int) -> float | None:
    """TNA lineal: (dlr/spot - 1) × (365/dias), en porcentaje.

    Convención del terminal Rofex y de la mesa local. NO es TEA compuesta
    (que sería ((dlr/spot)^(365/dias) - 1)). Para vencimientos cortos
    convergen, para largos divergen 2-4 puntos.

    None si falta data.
    """
    if not precio_dlr or not spot or precio_dlr <= 0 or spot <= 0 or dias <= 0:
        return None
    try:
        return round((precio_dlr / spot - 1) * (365 / dias) * 100, 4)
    except Exception:
        return None


class FuturosDLREngine(SnapshotEngine):

    INTERVALO_SNAPSHOT_S = INTERVALO_SNAPSHOT_S
    INTERVALO_REDISCOVERY_S = INTERVALO_REDISCOVERY_S

    def __init__(self):
        self.tickers_actuales: list[tuple[str, str]] = descubrir_outrights_dlr()
        if not self.tickers_actuales:
            raise RuntimeError("No hay outrights DLR vigentes — abortando.")
        super().__init__([t for t, _ in self.tickers_actuales])
        self.logger.info(
            "Outrights DLR descubiertos: %d (de %s a %s)",
            len(self.tickers_actuales),
            self.tickers_actuales[0][0],
            self.tickers_actuales[-1][0],
        )

    # ─── Rediscovery (re-suscripción en caliente vía _motor_base) ─────────
    def _pre_snapshot(self):
        self._maybe_rediscover()

    def _descubrir(self):
        return descubrir_outrights_dlr()

    def _ticker_item(self, item):
        return item[0]

    def _aplicar_universo(self, items):
        self.tickers_actuales = items

    # ─── Snapshot ─────────────────────────────────────────────────────────
    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        spot, fuente_spot = _spot_referencia()
        docs = []
        with self._state_lock:
            for ticker, mat in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                doc = self._build_snapshot_doc(ticker, mat, st, spot, fuente_spot, ts)
                if doc:
                    docs.append(doc)
        if docs:
            # SQL-native (decomiso Mongo): snapshot live → mercado.futuros_dlr_snapshot
            # (UPSERT por ticker, incondicional).
            from core import pg_mirror
            pg_mirror.write_native("futuros_dlr_snapshot", ["ticker"], [
                {"ticker": d.get("ticker"), "vencimiento": d.get("vencimiento"),
                 "data": pg_mirror.doc_iso(d)}
                for d in docs if d.get("ticker")
            ])

    def _build_snapshot_doc(
        self, ticker: str, mat: str, st: dict, spot: float | None,
        fuente_spot: str, ts: datetime,
    ) -> dict | None:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        precio_last = last.get("price")
        precio_bid = bid.get("price")
        precio_offer = offer.get("price")
        dias = dias_a_vto(mat)
        return {
            "ticker":                    ticker,
            "vencimiento":               mat,
            "dias_a_vto":                dias,
            "bid_price":                 precio_bid,
            "bid_size":                  bid.get("size"),
            "offer_price":               precio_offer,
            "offer_size":                offer.get("size"),
            "last_price":                precio_last,
            "last_size":                 last.get("size"),
            "open":                      st.get("open"),
            "high":                      st.get("high"),
            "low":                       st.get("low"),
            "closing":                   closing.get("price"),
            "vol_efectivo":              st.get("vol_efectivo"),
            # 3 TNAs separadas — la principal es sobre last, las otras dos
            # se persisten para mostrar la dispersión en la watchlist.
            "tasa_implicita_tna":        _tasa_implicita_tna(precio_last, spot, dias),
            "tasa_implicita_tna_bid":    _tasa_implicita_tna(precio_bid, spot, dias),
            "tasa_implicita_tna_offer":  _tasa_implicita_tna(precio_offer, spot, dias),
            "spot_referencia":           spot,
            "fuente_spot":               fuente_spot,
            "updated_at":                ts,
        }

    # ─── Vuelco al cierre ─────────────────────────────────────────────────
    def vuelco_cierre(self):
        """Persiste el cierre del día en mercado.mercado_hist (coleccion='FuturosDLR',
        SQL-native). Shape del doc IDÉNTICO al que escribía Trading.FuturosDLR → lo lee
        mercado_hist_sql.get_historico_futuros_dlr sin cambios. UPSERT por
        (coleccion, fecha, k=ticker). Lo llama correr_motor al apagado."""
        from core import pg_mirror
        hoy = date.today().isoformat()
        ts = datetime.now(UTC)
        spot, fuente_spot = _spot_referencia()
        rows = []
        with self._state_lock:
            for ticker, mat in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                last = st.get("last") or {}
                closing = st.get("closing") or {}
                precio_cierre = last.get("price") or closing.get("price")
                if precio_cierre is None:
                    continue
                dias = dias_a_vto(mat)
                doc = {
                    "fecha":                       hoy,
                    "ticker":                      ticker,
                    "vencimiento":                 mat,
                    "dias_a_vto":                  dias,
                    "precio_cierre":               precio_cierre,
                    "open":                        st.get("open"),
                    "high":                        st.get("high"),
                    "low":                         st.get("low"),
                    "vol_efectivo":                st.get("vol_efectivo"),
                    "tasa_implicita_tna_cierre":   _tasa_implicita_tna(precio_cierre, spot, dias),
                    "spot_referencia":             spot,
                    "fuente_spot":                 fuente_spot,
                    "persisted_at":                ts,
                }
                rows.append({
                    "coleccion": "FuturosDLR",
                    "fecha": date.fromisoformat(hoy[:10]),
                    "k": ticker,
                    "data": pg_mirror.doc_iso(doc),
                })
        if rows:
            pg_mirror.write_native("mercado_hist", ["coleccion", "fecha", "k"], rows)
            logger.info("Vuelco de cierre OK: %d docs en mercado_hist (FuturosDLR)", len(rows))


def run():
    correr_motor(
        "MotorFuturosDLR", FuturosDLREngine,
        log_arranque=f"Re-discovery cada {INTERVALO_REDISCOVERY_S}s.",
    )


if __name__ == "__main__":
    run()
