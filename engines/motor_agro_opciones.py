"""Motor de Opciones Agro Rosario — Trigo / Maíz / Soja.

Análogo a motor_agro.py pero para opciones (cficode OCAFXS = call,
OPAFXS = put). Filtra opciones sobre futuros Rosario (excluye Chicago)
y persiste 1 fila por ticker en mercado.agro_opciones_snapshot.
Un strike nuevo listado durante la rueda se suscribe EN CALIENTE.

Pipeline:
- Discovery: pyRofex.get_detailed_instruments() filtrado por cficode +
  underlying matcheando trigo/maíz/soja Rosario (mismos matchers que
  motor_agro, en _motor_base). El strike NO viene como field separado en
  get_detailed_instruments — se parsea del symbol con el formato
  '{ROOT}.ROS/{MES}{YR} {STRIKE} {C|P}', ej. 'SOJ.ROS/NOV26 340 C'.
- Subscription: WS pyRofex con depth=1 (alimenta el panel comprador/
  vendedor + último).
- Persistence: mercado.agro_opciones_snapshot, UPSERT cada 5s, shape:
    {ticker, commodity, underlying, vencimiento, strike, tipo,
     dias_a_vto, bid_price, bid_size, offer_price, offer_size,
     last_price, last_size, closing, vol_efectivo, updated_at}
- NO se escribe timesales ni histórico de cierre (mismo criterio que
  motor_agro). El simulador de estrategias trabaja con el último precio.

Esqueleto del proceso (señales / WS / snapshot-loop / run) y helpers de
discovery: engines/_motor_base.

Uso:
    python -m engines.motor_agro_opciones

Cron: L-V 13:00–20:05 UTC (motor_agro_opciones.service).
"""
from __future__ import annotations

import logging
import re
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
logger = logging.getLogger("MotorAgroOpciones")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_REDISCOVERY_S = 1800

CFICODE_CALL = "OCAFXS"
CFICODE_PUT = "OPAFXS"

# Symbol format: '{ROOT}.ROS/{MES}{YR} {STRIKE} {C|P}'
# Ejemplos: 'SOJ.ROS/JUL26 312 C', 'MAI.ROS/DIC26 200 P', 'TRI.ROS/ENE27 248 C'.
# Strike admite decimal por defensiva (los listings de hoy son enteros).
TICKER_RE = re.compile(
    r"^(?P<root>[A-Z]{3})\.ROS/(?P<mes>[A-Z]{3})(?P<yr>\d{2})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+(?P<tipo>[CP])$"
)


def _parse_ticker(ticker: str) -> tuple[float, str] | None:
    """Extrae (strike, tipo C|P) del symbol. None si no matchea el patrón."""
    m = TICKER_RE.match(ticker)
    if not m:
        return None
    try:
        strike = float(m.group("strike"))
    except (TypeError, ValueError):
        return None
    return strike, m.group("tipo")


def descubrir_opciones_agro() -> list[dict]:
    """Devuelve [{ticker, maturity, commodity, underlying, strike, tipo}] de opciones agro vigentes."""
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return []

    hoy_str = datetime.now().strftime("%Y%m%d")
    out: list[dict] = []
    descartados_parse = 0
    for inst in res.get("instruments") or []:
        cfi = inst.get("cficode")
        if cfi not in (CFICODE_CALL, CFICODE_PUT):
            continue
        underlying = inst.get("underlying") or ""
        commodity = classify_commodity(underlying)
        if not commodity:
            continue
        mat = maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = ticker_de(inst)
        if not ticker:
            continue
        parsed = _parse_ticker(ticker)
        if not parsed:
            # Symbol no matchea el patrón canónico — variantes raras se
            # ignoran defensivamente (la mesa opera el outright canonical).
            # Lo contamos: un salto brusco delata un cambio de formato de
            # Primary (que dejaría el universo en 0 → RuntimeError).
            descartados_parse += 1
            continue
        strike, tipo_from_ticker = parsed
        tipo_from_cfi = "C" if cfi == CFICODE_CALL else "P"
        if tipo_from_ticker != tipo_from_cfi:
            logger.warning(
                "Inconsistencia tipo en %s: cficode=%s pero ticker dice %s",
                ticker, cfi, tipo_from_ticker,
            )
            continue
        out.append({
            "ticker":     ticker,
            "maturity":   mat,
            "commodity":  commodity,
            "underlying": underlying,
            "strike":     strike,
            "tipo":       tipo_from_cfi,
        })
    if descartados_parse:
        logger.info(
            "Discovery: %d símbolos agro (CFI call/put) descartados por no "
            "matchear el patrón de ticker — revisar si Primary cambió el formato.",
            descartados_parse,
        )
    out.sort(key=lambda x: (x["commodity"], x["maturity"], x["tipo"], x["strike"]))
    return out


class AgroOpcionesEngine(SnapshotEngine):

    INTERVALO_SNAPSHOT_S = INTERVALO_SNAPSHOT_S
    INTERVALO_REDISCOVERY_S = INTERVALO_REDISCOVERY_S
    # Las opciones no trackean OHLC ni volumen nominal — solo book/last/cierre/EV.
    ENTRIES = ("BI", "OF", "LA", "CL", "EV")

    def __init__(self):
        self.universo: list[dict] = descubrir_opciones_agro()
        if not self.universo:
            raise RuntimeError("No hay opciones agro vigentes — abortando.")

        by_com: dict[str, int] = {}
        for u in self.universo:
            by_com[u["commodity"]] = by_com.get(u["commodity"], 0) + 1
        logger.info(
            "Opciones agro descubiertas: %d (por commodity: %s)",
            len(self.universo), dict(sorted(by_com.items())),
        )

        # Limpieza stale: si una corrida anterior dejó tickers que ya no
        # están en el universo (vencimientos cumplidos, strikes retirados),
        # los borramos para que el GET no los rendere zombie.
        borrar_stale_sql(
            "mercado.agro_opciones_snapshot", [u["ticker"] for u in self.universo], logger)

        super().__init__([u["ticker"] for u in self.universo])

    # ─── Rediscovery (re-suscripción en caliente vía _motor_base) ─────────
    def _pre_snapshot(self):
        self._maybe_rediscover()

    def _descubrir(self):
        return descubrir_opciones_agro()

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
            # `commodity` columna para que el panel filtre. write_native nunca levanta.
            from core import pg_mirror
            pg_mirror.write_native(
                "mercado.agro_opciones_snapshot", ["ticker"],
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
            "strike":        u["strike"],
            "tipo":          u["tipo"],
            "dias_a_vto":    dias_a_vto(u["maturity"]),
            "bid_price":     bid.get("price"),
            "bid_size":      bid.get("size"),
            "offer_price":   offer.get("price"),
            "offer_size":    offer.get("size"),
            "last_price":    last.get("price"),
            "last_size":     last.get("size"),
            "closing":       closing.get("price"),
            "vol_efectivo":  st.get("vol_efectivo"),
            "updated_at":    ts,
        }


def run():
    correr_motor(
        "MotorAgroOpciones", AgroOpcionesEngine,
        log_arranque=f"Re-discovery cada {INTERVALO_REDISCOVERY_S}s.",
    )


if __name__ == "__main__":
    run()
