"""Registro ÚNICO del Diagnóstico: vista → motores / jobs / APIs que la alimentan.

Fuente de verdad declarativa de la tab MANAGER → DIAGNÓSTICO. Reemplaza las 3
listas hardcodeadas (`_MOTORES`/`_JOBS_STATUS`/`_APIS_EXTERNAS`) que se
desfasaban del sistema real. De acá sale el árbol por vista (HOME / OPERAR /
MERCADOS / NEGOCIO / BACK OFFICE / PORTFOLIOS) con el status de cada pieza.

Anti-drift: `tests/test_diagnostico_registry.py` cruza este registro contra el
inventario REAL (`deploy/systemd/*.service` + `deploy/crontab.txt`) y FALLA si
hay un motor/cron que corre y no está acá (o al revés). Si agregás/sacás un
motor o cron, actualizá este archivo en el mismo cambio (igual que gen_sistema).

Frescura por tipo de pieza:
  - motor / api → última escritura a su colección de salida (`db.coll.field`).
  - job        → último run en `Manager.JobRuns` por `run_tipo` (captura también
                 si el job terminó en error). Si un job no usa JobRunLogger, se
                 cae a su colección de salida (`db/coll/field`).

⚠️ Los `run_tipo` de los jobs son el `tipo` con que cada cron registra en
Manager.JobRuns. Se confirman con `python -m scripts.diag_jobruns_tipos`
(read-only) — si una fila sale "sin_datos", el run_tipo no coincide y se ajusta.
"""
from __future__ import annotations

from dataclasses import dataclass

# Vistas de producto de acaquant (orden de display).
VISTAS = ("HOME", "OPERAR", "MERCADOS", "NEGOCIO", "BACK_OFFICE", "PORTFOLIOS")


@dataclass(frozen=True)
class Pieza:
    vista: str                      # una de VISTAS
    tipo: str                       # "motor" | "job" | "api"
    label: str                      # nombre visible
    grupo: str | None = None        # subgrupo dentro de la vista (ej. "RENTA FIJA")
    unidad: str | None = None       # systemd unit (motor_x) o "jobs.x" — para el check de inventario
    cadencia: str = ""              # texto: "live", "cada 1m", "diario 12:00 UTC"…
    ventana: str = "rueda"          # rueda | rueda_agro | always | diario
    umbral_s: int = 120             # frescura: > umbral → lento, > 3×umbral → crítico
    # Fuente de frescura — JobRuns (jobs) …
    run_tipo: str | None = None
    # … o colección de salida (motores/apis, y fallback de jobs):
    db: str | None = None
    coll: str | None = None
    field: str | None = None
    filtro: dict | None = None
    ts_kind: str = "datetime"       # datetime | iso | ddmmyyyy
    assume: str = "UTC"             # UTC | AR — tz a asumir si el ts es naive
    # Fuente de frescura SQL (decomiso Mongo): si `tabla` está seteada, la frescura
    # se lee de Postgres (max(ts_expr::timestamptz)), no de Mongo. Para snapshots SQL
    # el updated_at fresco vive en `data->>'updated_at'` (la columna queda con el
    # now() del primer insert). `sql_where` filtra (ej. mercado_hist por colección).
    tabla: str | None = None        # tabla SQL (sin schema, search_path)
    ts_expr: str = "updated_at"     # expresión SQL del timestamp
    sql_where: str | None = None    # WHERE opcional


_H = 3600
_D = 86400

# ───────────────────────── REGISTRO ─────────────────────────
PIEZAS: list[Pieza] = [
    # ── HOME ───────────────────────────────────────────────
    Pieza("HOME", "job", "market_quotes (watchlist)", unidad="jobs.market_quotes",
          cadencia="cada 1m · 13-21 UTC L-V", ventana="rueda", umbral_s=10 * 60,
          db="Market", coll="Quotes", field="updated_at"),
    Pieza("HOME", "job", "market_anchors (retornos)", unidad="jobs.market_anchors",
          cadencia="diario 22:00 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          db="Market", coll="Quotes", field="anchors_updated_at"),
    Pieza("HOME", "job", "economic_calendar", unidad="jobs.economic_calendar",
          cadencia="diario ~11:30 UTC", ventana="diario", umbral_s=int(1.5 * _D),
          db="Market", coll="EconomicCalendar", field="fetched_at"),
    # News.Headlines → SQL home.news_headlines (writers news_finnhub/news_ingesta SQL-native).
    Pieza("HOME", "api", "Finnhub news", grupo=None, unidad="jobs.news_finnhub",
          cadencia="*/30m · 12-23 UTC", ventana="always", umbral_s=3 * _H,
          tabla="news_headlines", ts_expr="fecha_publicacion", sql_where="fuente = 'finnhub'"),
    Pieza("HOME", "api", "RSS medios AR", unidad="jobs.news_ingesta",
          cadencia="*/15m · 12-23 UTC", ventana="always", umbral_s=3 * _H,
          tabla="news_headlines", ts_expr="fecha_publicacion", sql_where="fuente <> 'finnhub'"),

    # ── OPERAR ─────────────────────────────────────────────
    Pieza("OPERAR", "motor", "motor_rofex (order book)", unidad="motor_rofex",
          cadencia="live", ventana="rueda", umbral_s=120,
          tabla="market_snapshot"),
    Pieza("OPERAR", "motor", "motor_ordenes (ER WS)", unidad="motor_ordenes",
          cadencia="live (heartbeat 30s)", ventana="rueda_agro", umbral_s=90,
          db="Operaciones", coll="MotorOrdenesHeartbeat", field="updated_at"),

    # ── MERCADOS · RENTA FIJA ──────────────────────────────
    Pieza("MERCADOS", "motor", "motor_rofex (trades)", grupo="RENTA FIJA", unidad="motor_rofex",
          cadencia="live", ventana="rueda", umbral_s=300,
          db="Trading", coll="TimeSales", field="timestamp", assume="AR"),
    Pieza("MERCADOS", "motor", "motor_curvas (TEA/duration)", grupo="RENTA FIJA", unidad="motor_curvas",
          cadencia="live (2s)", ventana="rueda", umbral_s=120,
          tabla="market_snapshot"),
    Pieza("MERCADOS", "job", "argentina_datos (CER/IPC/RP)", grupo="RENTA FIJA", unidad="jobs.argentina_datos",
          cadencia="diario 12:00 UTC", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="argentina_datos"),
    Pieza("MERCADOS", "job", "bcra (CER/TAMAR/DOLAR)", grupo="RENTA FIJA", unidad="jobs.bcra",
          cadencia="diario 22:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          db="Trading", coll="CER", field="fecha", ts_kind="iso"),
    # snapshot_cierre escribe SQL-native (mercado.snapshots_cierre + _hist) desde el cutover
    # 2026-06-24; Trading.SnapshotsCierre Mongo dropeada → frescura por JobRuns (el job usa
    # JobRunLogger("snapshot_cierre")), no por la colección Mongo.
    Pieza("MERCADOS", "job", "snapshot_cierre", grupo="RENTA FIJA", unidad="jobs.snapshot_cierre",
          cadencia="20:25 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="snapshot_cierre"),
    Pieza("MERCADOS", "job", "fair_value (fit)", grupo="RENTA FIJA", unidad="jobs.fair_value",
          cadencia="20:25 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          db="Trading", coll="FairValueResiduos", field="ts_cierre", ts_kind="iso"),

    # ── MERCADOS · DERIVADOS ───────────────────────────────
    Pieza("MERCADOS", "job", "snapshot_sinteticos (serie histórica)", grupo="DERIVADOS",
          unidad="jobs.snapshot_sinteticos",
          cadencia="20:40 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="snapshot_sinteticos"),
    Pieza("MERCADOS", "motor", "motor_forwards", grupo="DERIVADOS", unidad="motor_forwards",
          cadencia="live", ventana="rueda", umbral_s=60,
          tabla="mercado_hist", ts_expr="data->>'updated_at'",
          sql_where="coleccion='ForwardsHistorico'"),
    Pieza("MERCADOS", "motor", "motor_futuros_dlr", grupo="DERIVADOS", unidad="motor_futuros_dlr",
          cadencia="live", ventana="rueda", umbral_s=60,
          tabla="futuros_dlr_snapshot", ts_expr="data->>'updated_at'"),
    Pieza("MERCADOS", "motor", "motor_breakevens", grupo="DERIVADOS", unidad="motor_breakevens",
          cadencia="live", ventana="rueda", umbral_s=60,
          tabla="mercado_hist", ts_expr="data->>'updated_at'",
          sql_where="coleccion='BreakevensHistorico'"),
    Pieza("MERCADOS", "motor", "motor_dolares (MEP/CCL)", grupo="DERIVADOS", unidad="motor_dolares",
          cadencia="live (5s)", ventana="rueda", umbral_s=60,
          tabla="dolar_snapshot", ts_expr="ts"),
    Pieza("MERCADOS", "job", "dolar_mep (histórico)", grupo="DERIVADOS", unidad="engines.dolar_mep",
          cadencia="cada 15m · 13-20 UTC L-V", ventana="rueda", umbral_s=30 * 60,
          tabla="dolar", ts_expr="timestamp"),
    Pieza("MERCADOS", "job", "forwards_zscore", grupo="DERIVADOS", unidad="jobs.forwards_zscore",
          cadencia="20:30 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          tabla="forwards_zscore", ts_expr="data->>'updated_at'"),
    # cierre_canje escribe SQL-native (mercado.canje_cierre) desde el cutover 2026-06-24;
    # Trading.CanjeCierre Mongo dropeada → ya no se chequea esa colección (el diagnostico no
    # lee SQL todavía). TODO: wirear JobRunLogger + run_tipo="cierre_canje" para frescura fina.
    Pieza("MERCADOS", "job", "cierre_canje", grupo="DERIVADOS", unidad="jobs.cierre_canje",
          cadencia="20:35 UTC L-V", ventana="diario", umbral_s=int(3 * _D)),
    Pieza("MERCADOS", "api", "MAE UST$T (dólar oficial, MANUAL)", grupo="DERIVADOS",
          cadencia="cada 30s en rueda (script PC oficina)", ventana="rueda", umbral_s=5 * 60,
          tabla="dolar_oficial_live"),

    Pieza("MERCADOS", "motor", "motor_caucion (TNA)", grupo="DERIVADOS", unidad="motor_caucion",
          cadencia="live", ventana="rueda", umbral_s=60,
          tabla="caucion_snapshot", ts_expr="data->>'updated_at'"),

    # ── MERCADOS · AGRO ────────────────────────────────────
    Pieza("MERCADOS", "motor", "motor_agro", grupo="AGRO", unidad="motor_agro",
          cadencia="live", ventana="rueda_agro", umbral_s=60,
          tabla="agro_snapshot", ts_expr="data->>'updated_at'"),
    Pieza("MERCADOS", "motor", "motor_agro_opciones", grupo="AGRO", unidad="motor_agro_opciones",
          cadencia="live", ventana="rueda_agro", umbral_s=60,
          tabla="agro_opciones_snapshot", ts_expr="data->>'updated_at'"),

    # ── MERCADOS · RENTA VARIABLE ──────────────────────────
    # motor_cedears: CedearsSnapshot migrada a SQL (mercado.cedears_snapshot) 2026-06-24.
    # El monitor de frescura por colección es Mongo-only → queda informativo hasta que la
    # infra de diagnóstico lea frescura de SQL (follow-up, lo necesitan todos los motores
    # que migren). El motor sigue vigilable por systemd `Active` / skill /motor-status.
    Pieza("MERCADOS", "motor", "motor_cedears", grupo="RENTA VARIABLE", unidad="motor_cedears",
          cadencia="live", ventana="rueda", umbral_s=60),
    # precios_acciones_daily: PreciosAcciones (~43k docs) NO tiene índice por
    # `fecha` global (solo {ticker, fecha}) → sortear por fecha sería COLLSCAN
    # cada 10s (REGLA #4). No le ponemos fuente live; quedaría medible si se
    # wirea JobRunLogger o se agrega un índice {fecha:-1}. Por ahora informativo.
    Pieza("MERCADOS", "job", "precios_acciones_daily", grupo="RENTA VARIABLE", unidad="jobs.precios_acciones_daily",
          cadencia="22:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D)),
    Pieza("MERCADOS", "job", "adr_live", grupo="RENTA VARIABLE", unidad="jobs.adr_live",
          cadencia="cada 15m · 13-20 UTC L-V", ventana="rueda", umbral_s=30 * 60,
          run_tipo="adr_live"),
    Pieza("MERCADOS", "job", "day_trading_stats (costumbre TRADE LAB)", grupo="RENTA VARIABLE",
          unidad="jobs.day_trading_stats",
          cadencia="20:06 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="day_trading_stats"),

    # ── MERCADOS · OPCIONES (GGAL) ─────────────────────────
    Pieza("MERCADOS", "motor", "motor_options (GGAL)", grupo="OPCIONES", unidad="motor_options",
          cadencia="live", ventana="rueda", umbral_s=180,
          tabla="options_snapshot"),

    # ── NEGOCIO ────────────────────────────────────────────
    Pieza("NEGOCIO", "job", "operaciones_informes", unidad="jobs.operaciones_informes",
          cadencia="cada 30m · 13:30-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="operaciones_informes"),
    Pieza("NEGOCIO", "api", "Aunesa boletos (negocio_movimientos)", unidad="jobs.negocio_movimientos",
          cadencia="cada 60m en rueda", ventana="rueda", umbral_s=70 * 60,
          run_tipo="negocio_movimientos"),
    Pieza("NEGOCIO", "job", "flujo_contrapartes", unidad="jobs.flujo_contrapartes",
          cadencia="22:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          db="CashFlow", coll="Flujo", field="concertacion", ts_kind="iso"),
    Pieza("NEGOCIO", "job", "sync_comitentes (clientes)", unidad="jobs.sync_comitentes",
          cadencia="14/17/21 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="sync_comitentes"),

    # ── BACK OFFICE ────────────────────────────────────────
    Pieza("BACK_OFFICE", "api", "Aunesa boletos (negocio_movimientos)", unidad="jobs.negocio_movimientos",
          cadencia="cada 60m en rueda", ventana="rueda", umbral_s=70 * 60,
          run_tipo="negocio_movimientos"),
    Pieza("BACK_OFFICE", "job", "operaciones_informes", unidad="jobs.operaciones_informes",
          cadencia="cada 30m · 13:30-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="operaciones_informes"),
    Pieza("BACK_OFFICE", "job", "acreencias (cobros futuros)", unidad="jobs.acreencias",
          cadencia="23:45 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="acreencias"),

    # ── PORTFOLIOS / Tenencias (SQL) ───────────────────────
    # AuM Mongo (jobs.aum) eliminado 2026-06-15: el writer de tenencias es el
    # cron diario portafolio_backfill --diario → SQL portafolio.tenencia. La
    # frescura SQL no la chequea este registro (solo inventario).
    Pieza("PORTFOLIOS", "job", "tenencia (snapshot SQL)", unidad="jobs.portafolio_backfill",
          cadencia="11:00 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D)),
    Pieza("PORTFOLIOS", "job", "tenencia_hd (cuentas propias)", unidad="jobs.tenencia_hd",
          cadencia="12:35 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          db="Valuaciones", coll="TenenciaHD", field="fecha_snapshot", ts_kind="iso"),
    Pieza("PORTFOLIOS", "job", "pnl_totales_precompute", unidad="jobs.pnl_totales_precompute",
          cadencia="cada 30m :05,:35 · 15-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          db="Valuaciones", coll="PnLTotalesCache", field="computed_at"),
    Pieza("PORTFOLIOS", "job", "consolidado_cuentas", unidad="jobs.consolidado_cuentas",
          cadencia="23:30 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          db="Valuaciones", coll="ConsolidadoCuentas", field="computed_at"),
    # jobs/cashflow escribe SQL-native (operaciones.movimientos) desde el cutover 2026-06-24;
    # CashFlow.Movimientos Mongo dropeada → ya no se chequea esa colección. El job corre con
    # JobRunLogger("cashflow") → la frescura sale de Manager.JobRuns por run_tipo (no de la coll).
    Pieza("PORTFOLIOS", "job", "cashflow (movimientos)", unidad="jobs.cashflow",
          cadencia="02:00 UTC mar-sáb", ventana="diario", umbral_s=int(2 * _D),
          run_tipo="cashflow"),
    Pieza("PORTFOLIOS", "job", "actividad_mensual", unidad="jobs.actividad_mensual",
          cadencia="22:30 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="actividad_mensual"),
    Pieza("PORTFOLIOS", "motor", "motor_portfolio_snapshot", unidad="motor_portfolio_snapshot",
          cadencia="live", ventana="rueda", umbral_s=120,
          tabla="portfolio_snapshot"),
]


def unidades_motores() -> set[str]:
    """Units systemd de motores referenciadas por el registro (para el check)."""
    return {p.unidad for p in PIEZAS if p.tipo == "motor" and p.unidad}


def unidades_jobs() -> set[str]:
    """Módulos de cron (`jobs.x` / `engines.x`) referenciados (para el check)."""
    return {p.unidad for p in PIEZAS if p.unidad and p.unidad.startswith(("jobs.", "engines."))}
