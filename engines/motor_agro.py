"""Motor de Futuros Agro Rosario — Trigo / Maíz / Soja.

Análogo al motor_futuros_dlr pero para el universo agro Rosario. Suscribe
los outrights single-leg (cficode FXXXSX) cuyos underlyings matchean
'Trigo Rosario', 'Maíz Rosario' y 'Soja Rosario' (matcher tolerante a
tildes y mayúsculas para que sobreviva cambios de spelling de Primary).
Un contrato nuevo listado durante la rueda se suscribe EN CALIENTE.

Persistencia (decisión consciente de la mesa):
- mercado.agro_snapshot: 1 fila por ticker, UPSERT cada 5s.
  {ticker, commodity, underlying, vencimiento, dias_a_vto,
   bid_price, bid_size, offer_price, offer_size,
   last_price, last_size, open, high, low, closing,
   vol_efectivo, updated_at}
- NO se escribe timesales (a diferencia del motor DLR). La vista PASE AGRO
  sólo necesita el último precio para calcular pase y TNAV.
- NO se escribe histórico diario al cierre. Si en el futuro se necesita
  serie histórica por commodity, agregar vuelco_cierre() análogo al del
  motor DLR.

Esqueleto del proceso (señales / WS / snapshot-loop / run) y helpers de
discovery (ticker_de/maturity/dias_a_vto/matchers): engines/_motor_base.

Uso:
    python -m engines.motor_agro

Cron: L-V 13-20 UTC (mismo horario que el resto de motores ROFEX).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import pyRofex

from core.logs import configurar
from engines._motor_base import (
    SnapshotEngine,
    borrar_stale_sql,
    classify_commodity,
    correr_motor,
    dias_a_vto,
    maturity,
    ticker_de,
)

# El formato (con NIVEL) vive en core/logs — ver AV_AGENT.md §0.ac.
configurar()
logger = logging.getLogger("MotorAgro")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_REDISCOVERY_S = 1800

CFICODE_OUTRIGHT = "FXXXSX"


def descubrir_outrights_agro() -> list[dict]:
    """Devuelve [{ticker, maturity, commodity, underlying}] de outrights agro vigentes."""
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return []

    hoy_str = datetime.now().strftime("%Y%m%d")
    out: list[dict] = []
    for inst in res.get("instruments") or []:
        if inst.get("cficode") != CFICODE_OUTRIGHT:
            continue
        underlying = inst.get("underlying") or ""
        commodity = classify_commodity(underlying)
        if not commodity:
            continue
        mat = maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = ticker_de(inst)
        if not ticker or ticker.count("/") != 1:
            continue
        # Variantes paralelas (TRI.ROS/JUL26M, etc.) — mismo filtro que en
        # motor_futuros_dlr. La mesa quiere el outright canonical, no la
        # variante que sólo cotiza en cámaras alternativas.
        if ticker.endswith("M"):
            continue
        # Placeholders del propio mercado: TRI.ROS/DISPO, TRI.ROS.P/DISPO,
        # SOJ.ROS/DISPO, etc. La fila DISPO se renderea manualmente desde el
        # service (placeholder #N/A) y la PIZARRA es manual del trader.
        if "DISPO" in ticker:
            continue
        out.append({
            "ticker":     ticker,
            "maturity":   mat,
            "commodity":  commodity,
            "underlying": underlying,
        })
    out.sort(key=lambda x: (x["commodity"], x["maturity"]))
    return out


class AgroEngine(SnapshotEngine):

    INTERVALO_SNAPSHOT_S = INTERVALO_SNAPSHOT_S
    INTERVALO_REDISCOVERY_S = INTERVALO_REDISCOVERY_S

    def __init__(self):
        self.universo: list[dict] = descubrir_outrights_agro()
        if not self.universo:
            raise RuntimeError("No hay outrights agro vigentes — abortando.")

        commodities = sorted({u["commodity"] for u in self.universo})
        logger.info(
            "Outrights agro descubiertos: %d (commodities: %s)",
            len(self.universo), commodities,
        )

        # Limpiar stale: si una corrida anterior dejó tickers que ya no
        # están en el universo (variantes M, DISPO, vencimientos cumplidos),
        # los borramos para que el GET no los rendere zombie.
        borrar_stale_sql("mercado.agro_snapshot", [u["ticker"] for u in self.universo], logger)

        super().__init__([u["ticker"] for u in self.universo])

    # ─── Rediscovery (re-suscripción en caliente vía _motor_base) ─────────
    def _pre_snapshot(self):
        self._maybe_rediscover()

    def _descubrir(self):
        return descubrir_outrights_agro()

    def _ticker_item(self, item):
        return item["ticker"]

    def _aplicar_universo(self, items):
        self.universo = items

    # ─── Snapshot ─────────────────────────────────────────────────────────
    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        docs = []
        with self._state_lock:
            for u in self.universo:
                st = self.market_state.get(u["ticker"], {})
                doc = self._build_snapshot_doc(u, st, ts)
                if doc:
                    docs.append(doc)
        if docs:
            # SQL-native (sin Mongo): la tabla es la fuente. Passthrough jsonb;
            # `commodity` columna para que la vista filtre. write_native nunca levanta.
            from core import pg_mirror
            pg_mirror.write_native(
                "mercado.agro_snapshot", ["ticker"],
                [{"ticker": d["ticker"], "commodity": d.get("commodity"),
                  "data": pg_mirror.doc_iso(d)} for d in docs],
            )

    def _build_snapshot_doc(self, u: dict, st: dict, ts: datetime) -> dict:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        return {
            "ticker":        u["ticker"],
            "commodity":     u["commodity"],
            "underlying":    u["underlying"],
            "vencimiento":   u["maturity"],
            "dias_a_vto":    dias_a_vto(u["maturity"]),
            "bid_price":     bid.get("price"),
            "bid_size":      bid.get("size"),
            "offer_price":   offer.get("price"),
            "offer_size":    offer.get("size"),
            "last_price":    last.get("price"),
            "last_size":     last.get("size"),
            "open":          st.get("open"),
            "high":          st.get("high"),
            "low":           st.get("low"),
            "closing":       closing.get("price"),
            "vol_efectivo":  st.get("vol_efectivo"),
            "updated_at":    ts,
        }


def run():
    correr_motor(
        "MotorAgro", AgroEngine,
        log_arranque=f"Re-discovery cada {INTERVALO_REDISCOVERY_S}s.",
    )


if __name__ == "__main__":
    run()
