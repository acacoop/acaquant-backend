"""Motor de caución ARS y USD a corto plazo.

Suscribe via WS los 2 tickers de caución (pesos + dólares) cuyo plazo
coincide con "días al próximo día hábil". Lun-jue = 1D, vie = 3D
(cubre fin de semana), vie con lunes feriado = 4D, etc. El plazo se
re-evalúa cada hora; si cambia (cruce de día), el motor se re-suscribe
EN CALIENTE a los tickers nuevos (antes quedaba mudo hasta el restart).

Persistencia:
- mercado.caucion_snapshot: 1 fila por moneda (data jsonb), UPSERT cada 5s.
  {moneda, plazo_dias, ticker, tna_last, tna_bid, tna_offer, tna_open,
   tna_high, tna_low, tna_closing, vol_efectivo, updated_at}
- mercado.mercado_hist (coleccion='Caucion', k=moneda): 1 fila por
  (fecha, moneda) escrita al apagado del motor (cierre de rueda 20:05 UTC).
  Serie histórica, mismo shape que escribía Trading.Caucion.
  {fecha, moneda, plazo_dias, tna_cierre, tna_open, tna_high, tna_low,
   vol_efectivo}

Esqueleto del proceso (señales / WS / snapshot-loop / run): engines/_motor_base.

Ejecutar:
    python -m engines.caucion
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime

from core.logs import configurar
from engines._motor_base import SnapshotEngine, correr_motor

# El formato (con NIVEL) vive en core/logs — ver AV_AGENT.md §0.ac.
configurar()
logger = logging.getLogger("MotorCaucion")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_RECARGA_PLAZO_S = 3600   # re-evaluar plazo cada 1h


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _cargar_dias_habiles() -> list[str]:
    """Fechas hábiles ASC desde mercado.dias_habiles (SQL-only). Cacheado por el caller."""
    from core.calendario import dias_habiles_ordenados
    return dias_habiles_ordenados()


def _proximo_habil(dias_habiles: list[str], hoy: date) -> date | None:
    hoy_str = hoy.isoformat()
    for f in dias_habiles:
        if f > hoy_str:
            return date.fromisoformat(f)
    return None


def _calcular_plazo(dias_habiles: list[str], hoy: date | None = None) -> int:
    """Plazo de caución = días calendario hasta el próximo día hábil.

    Lun-jue → 1, vie → 3 (cubre sáb+dom), vie con lun feriado → 4, etc.
    Si DiasHabiles está vacío o no encontramos próximo hábil, fallback 1.
    """
    hoy = hoy or date.today()
    proximo = _proximo_habil(dias_habiles, hoy)
    if proximo is None:
        return 1
    return max(1, (proximo - hoy).days)


def _tickers_para_plazo(plazo: int) -> tuple[str, str]:
    """Tickers ROFEX para caución pesos + dólares al plazo dado."""
    return (
        f"MERV - XMEV - PESOS - {plazo}D",
        f"MERV - XMEV - DOLAR - {plazo}D",
    )


def _moneda_de_ticker(ticker: str) -> str:
    return "ARS" if "PESOS" in ticker else "USD"


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class CaucionEngine(SnapshotEngine):
    """Mantiene estado en RAM y persiste snapshot cada N segundos."""

    INTERVALO_SNAPSHOT_S = INTERVALO_SNAPSHOT_S

    def __init__(self):
        self.dias_habiles = _cargar_dias_habiles()
        self.plazo_actual = _calcular_plazo(self.dias_habiles)
        self.tickers_actuales: list[str] = list(_tickers_para_plazo(self.plazo_actual))
        self._ultima_recarga_plazo = time.time()
        super().__init__(self.tickers_actuales)
        self.logger.info(
            "Plazo inicial: %d día(s) — tickers: %s",
            self.plazo_actual, self.tickers_actuales,
        )

    def _pre_snapshot(self):
        self._chequear_cambio_plazo()

    def _chequear_cambio_plazo(self):
        """Si pasó >1h y cambió el día, re-suscribe a tickers nuevos."""
        if time.time() - self._ultima_recarga_plazo < INTERVALO_RECARGA_PLAZO_S:
            return
        self._ultima_recarga_plazo = time.time()

        nuevo_plazo = _calcular_plazo(self.dias_habiles)
        if nuevo_plazo == self.plazo_actual:
            return

        nuevos_tickers = list(_tickers_para_plazo(nuevo_plazo))
        self.logger.info(
            "Cambio de plazo: %d → %d días. Tickers viejos %s, nuevos %s.",
            self.plazo_actual, nuevo_plazo, self.tickers_actuales, nuevos_tickers,
        )
        with self._state_lock:
            self.plazo_actual = nuevo_plazo
            self.tickers_actuales = nuevos_tickers
            self.market_state = {t: {} for t in nuevos_tickers}
            self._universo_tickers = set(nuevos_tickers)
        # Re-suscripción EN CALIENTE (aditiva): sin esto el motor quedaba mudo
        # hasta el restart diario — el WS seguía suscripto solo a los viejos.
        if self._ws is not None:
            self._ws.agregar_suscripciones(nuevos_tickers, depth=1)

    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        docs = []
        with self._state_lock:
            for ticker in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                doc = self._build_snapshot_doc(ticker, st, ts)
                if doc:
                    docs.append(doc)
        if docs:
            # SQL-native (decomiso Mongo): snapshot live → mercado.caucion_snapshot
            # (UPSERT por moneda, incondicional).
            from core import pg_mirror
            pg_mirror.write_native("caucion_snapshot", ["moneda"], [
                {"moneda": d.get("moneda"), "data": pg_mirror.doc_iso(d)}
                for d in docs if d.get("moneda")
            ])

    def _build_snapshot_doc(self, ticker: str, st: dict, ts: datetime) -> dict | None:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        return {
            "moneda":       _moneda_de_ticker(ticker),
            "plazo_dias":   self.plazo_actual,
            "ticker":       ticker,
            "tna_last":     last.get("price"),
            "tna_bid":      bid.get("price"),
            "tna_offer":    offer.get("price"),
            "tna_open":     st.get("open"),
            "tna_high":     st.get("high"),
            "tna_low":      st.get("low"),
            "tna_closing": closing.get("price"),
            "vol_efectivo": st.get("vol_efectivo"),
            "updated_at":   ts,
        }

    # ─── Vuelco al cierre ─────────────────────────────────────────────────
    def vuelco_cierre(self):
        """Persiste el último snapshot como cierre del día en mercado.mercado_hist
        (coleccion='Caucion', SQL-native). Shape del doc IDÉNTICO al que escribía
        Trading.Caucion → lo lee mercado_hist_sql.get_historico_caucion sin cambios.

        Lo llama correr_motor al recibir SIGTERM/SIGINT antes de que el proceso
        muera. Idempotente: UPSERT por (coleccion, fecha, k=moneda).
        """
        from core import pg_mirror
        hoy = date.today().isoformat()
        ts = datetime.now(UTC)
        rows = []
        with self._state_lock:
            for ticker in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                last = st.get("last") or {}
                closing = st.get("closing") or {}
                tna_cierre = last.get("price") or closing.get("price")
                if tna_cierre is None:
                    continue
                moneda = _moneda_de_ticker(ticker)
                doc = {
                    "fecha":         hoy,
                    "moneda":        moneda,
                    "plazo_dias":    self.plazo_actual,
                    "ticker":        ticker,
                    "tna_cierre":    tna_cierre,
                    "tna_open":      st.get("open"),
                    "tna_high":      st.get("high"),
                    "tna_low":       st.get("low"),
                    "vol_efectivo":  st.get("vol_efectivo"),
                    "persisted_at":  ts,
                }
                rows.append({
                    "coleccion": "Caucion",
                    "fecha": date.fromisoformat(hoy[:10]),
                    "k": moneda,
                    "data": pg_mirror.doc_iso(doc),
                })
        if rows:
            pg_mirror.write_native("mercado_hist", ["coleccion", "fecha", "k"], rows)
            logger.info("Vuelco de cierre OK: %d docs en mercado_hist (Caucion)", len(rows))


def run():
    correr_motor("MotorCaucion", CaucionEngine)


if __name__ == "__main__":
    run()
