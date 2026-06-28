"""Motor de Opciones Agro Rosario — Trigo / Maíz / Soja.

Análogo a motor_agro.py pero para opciones (cficode OCAFXS = call,
OPAFXS = put). Filtra opciones sobre futuros Rosario (excluye Chicago)
y persiste 1 doc por ticker en Trading.AgroOpcionesSnapshot.

Pipeline:
- Discovery: pyRofex.get_detailed_instruments() filtrado por cficode +
  underlying matcheando trigo/maíz/soja Rosario (mismos matchers que
  motor_agro). El strike NO viene como field separado en
  get_detailed_instruments — se parsea del symbol con el formato
  '{ROOT}.ROS/{MES}{YR} {STRIKE} {C|P}', ej. 'SOJ.ROS/NOV26 340 C'.
- Subscription: WS pyRofex con depth=1 (alimenta el panel comprador/
  vendedor + último).
- Persistence: Trading.AgroOpcionesSnapshot, ReplaceOne cada 5s,
  shape:
    {ticker, commodity, underlying, vencimiento, strike, tipo,
     dias_a_vto, bid_price, bid_size, offer_price, offer_size,
     last_price, last_size, closing, vol_efectivo, updated_at}
- NO se escribe TimeSales ni histórico de cierre (mismo criterio que
  motor_agro). El simulador de estrategias trabaja con el último precio.
- Re-discovery cada 5 min, log si hay altas/bajas.

Uso:
    python -m engines.motor_agro_opciones

Cron: L-V 13:00–20:05 UTC (motor_agro_opciones.service).
"""
from __future__ import annotations

import logging
import re
import signal
import threading
import time
import traceback
import unicodedata
from datetime import UTC, date, datetime

import pyRofex

from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorAgroOpciones")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_REDISCOVERY_S = 300

CFICODE_CALL = "OCAFXS"
CFICODE_PUT = "OPAFXS"

# Matcher por keywords normalizadas (lower + sin tildes). Cubre 'Trigo
# Rosario', 'TRIGO ROSARIO', 'Maíz Rosario', 'Maiz Rosario', etc.
# Idéntico al de motor_agro.py — solo Rosario, NO Chicago.
COMMODITY_MATCHERS: dict[str, tuple[str, ...]] = {
    "TRIGO": ("trigo", "rosario"),
    "MAIZ":  ("maiz",  "rosario"),
    "SOJA":  ("soja",  "rosario"),
}

# Symbol format: '{ROOT}.ROS/{MES}{YR} {STRIKE} {C|P}'
# Ejemplos: 'SOJ.ROS/JUL26 312 C', 'MAI.ROS/DIC26 200 P', 'TRI.ROS/ENE27 248 C'.
# Strike admite decimal por defensiva (los listings de hoy son enteros).
TICKER_RE = re.compile(
    r"^(?P<root>[A-Z]{3})\.ROS/(?P<mes>[A-Z]{3})(?P<yr>\d{2})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+(?P<tipo>[CP])$"
)

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida — apago WS.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    """lower + strip de tildes para matching robusto."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _classify_commodity(underlying: str) -> str | None:
    n = _norm(underlying)
    if not n:
        return None
    for commodity, kws in COMMODITY_MATCHERS.items():
        if all(kw in n for kw in kws):
            return commodity
    return None


def _ticker_de(inst: dict) -> str:
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    return iid.get("symbol") if isinstance(iid.get("symbol"), str) else ""


def _maturity(inst: dict) -> str:
    return inst.get("maturityDate") or inst.get("maturity_date") or ""


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
        commodity = _classify_commodity(underlying)
        if not commodity:
            continue
        mat = _maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = _ticker_de(inst)
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


def _dias_a_vto(mat_str: str) -> int:
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - date.today()).days)
    except Exception:
        return 1


def _borrar_stale_sql(table: str, tickers_actuales: list[str]) -> None:
    """Borra del espejo SQL los tickers que ya no están en el universo vigente.

    Reemplaza el `delete_many` de Mongo (cutover SQL-native): sin esto, una opción
    vencida / strike retirado quedaría zombie en la tabla y el GET lo renderearía.
    Best-effort — un fallo de PG al arranque deja zombies hasta el próximo restart,
    no tumba el motor (idéntico criterio al write_native del loop)."""
    if not tickers_actuales:
        return
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE ticker <> ALL(%s)", (tickers_actuales,))
            if cur.rowcount:
                logger.info("Limpieza stale SQL: %d snapshots viejos borrados", cur.rowcount)
    except Exception as e:
        logger.error("Limpieza stale SQL %s: %s", table, str(e).splitlines()[0][:200])


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class AgroOpcionesEngine:
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
        _borrar_stale_sql("mercado.agro_opciones_snapshot", [u["ticker"] for u in self.universo])

        self.market_state: dict[str, dict] = {u["ticker"]: {} for u in self.universo}
        self._state_lock = threading.Lock()
        self._ultimo_discovery = time.time()

        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

    # ─── WS handler ───────────────────────────────────────────────────────
    def update_price(self, ticker: str, data: dict):
        with self._state_lock:
            if ticker not in self.market_state:
                return
            st = self.market_state[ticker]
            if "BI" in data:
                st["bid"] = data["BI"][0] if data["BI"] else None
            if "OF" in data:
                st["offer"] = data["OF"][0] if data["OF"] else None
            if "LA" in data:
                st["last"] = data["LA"]
            if "CL" in data:
                st["closing"] = data["CL"]
            if "EV" in data and data["EV"] is not None:
                st["vol_efectivo"] = data["EV"]

    # ─── Snapshot loop ────────────────────────────────────────────────────
    def _snapshot_loop(self):
        while _running:
            time.sleep(INTERVALO_SNAPSHOT_S)
            try:
                self._maybe_rediscover()
                self._volcar_snapshot()
            except Exception:
                logger.error("Error en snapshot_loop:\n%s", traceback.format_exc())

    def _maybe_rediscover(self):
        if time.time() - self._ultimo_discovery < INTERVALO_REDISCOVERY_S:
            return
        self._ultimo_discovery = time.time()
        nuevos = descubrir_opciones_agro()
        viejos_set = {u["ticker"] for u in self.universo}
        nuevos_set = {u["ticker"] for u in nuevos}
        if nuevos_set != viejos_set:
            agregados = nuevos_set - viejos_set
            sacados = viejos_set - nuevos_set
            logger.info(
                "Discovery cambió: +%d -%d (restart del motor para tomar cambios)",
                len(agregados), len(sacados),
            )

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
            "dias_a_vto":    _dias_a_vto(u["maturity"]),
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


# ─────────────────────────────────────────────────────────────────────────────
# Bucle principal
# ─────────────────────────────────────────────────────────────────────────────


def run():
    logger.info("Motor Agro Opciones iniciando...")
    if not inicializar_sesion():
        return

    try:
        engine = AgroOpcionesEngine()
    except RuntimeError as e:
        logger.error(str(e))
        return

    ws = WebSocketManager(engine)
    tickers = [u["ticker"] for u in engine.universo]

    if not ws.iniciar_ws(tickers, depth=1):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d opciones agro. Snapshot cada %ds. "
        "Re-discovery cada %ds.",
        len(tickers), INTERVALO_SNAPSHOT_S, INTERVALO_REDISCOVERY_S,
    )

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
