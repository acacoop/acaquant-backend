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
Manager.JobRuns. Si una fila sale "sin_datos", el run_tipo no coincide y se ajusta.
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
          tabla="market_quotes", ts_expr="data->>'updated_at'"),
    Pieza("HOME", "job", "market_anchors (retornos)", unidad="jobs.market_anchors",
          cadencia="diario 22:00 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          tabla="market_quotes", ts_expr="data->>'anchors_updated_at'"),
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
          tabla="motor_heartbeat", ts_expr="updated_at"),

    # ── MERCADOS · RENTA FIJA ──────────────────────────────
    # `ts` del tape es NAIVE en hora ART (ver sql/schema.sql) → assume="AR".
    Pieza("MERCADOS", "motor", "motor_rofex (trades)", grupo="RENTA FIJA", unidad="motor_rofex",
          cadencia="live", ventana="rueda", umbral_s=300,
          tabla="timesales", ts_expr="ts", assume="AR"),
    Pieza("MERCADOS", "motor", "motor_curvas (TEA/duration)", grupo="RENTA FIJA", unidad="motor_curvas",
          cadencia="live (2s)", ventana="rueda", umbral_s=120,
          tabla="market_snapshot"),
    Pieza("MERCADOS", "job", "argentina_datos (CER/IPC/RP)", grupo="RENTA FIJA", unidad="jobs.argentina_datos",
          cadencia="diario 12:00 UTC", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="argentina_datos"),
    Pieza("MERCADOS", "job", "bcra (CER/TAMAR/DOLAR)", grupo="RENTA FIJA", unidad="jobs.bcra",
          cadencia="diario 22:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="bcra"),
    # snapshot_cierre escribe SQL-native (mercado.snapshots_cierre + _hist) desde el cutover
    # 2026-06-24; Trading.SnapshotsCierre Mongo dropeada → frescura por JobRuns (el job usa
    # JobRunLogger("snapshot_cierre")), no por la colección Mongo.
    Pieza("MERCADOS", "job", "snapshot_cierre", grupo="RENTA FIJA", unidad="jobs.snapshot_cierre",
          cadencia="20:25 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="snapshot_cierre"),
    # La pata TAMAR de los duales NO la calcula ningún motor: la trae 1816. Sin
    # esta pieza, el día que el feed se corta la vista muestra la celda vacía y
    # nadie se entera (la TEA de la otra pata sigue viva y todo «parece bien»).
    Pieza("MERCADOS", "job", "mercado_1816_discovery (catálogo 1816)", grupo="RENTA FIJA",
          unidad="jobs.mercado_1816_discovery",
          cadencia="diario 12:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="mercado_1816_discovery"),
    Pieza("MERCADOS", "job", "tamar_1816 (TEA/margen pata TAMAR)", grupo="RENTA FIJA",
          unidad="jobs.tamar_1816",
          cadencia="cada 30m · 13-20 UTC L-V", ventana="rueda", umbral_s=70 * 60,
          run_tipo="tamar_1816"),
    Pieza("MERCADOS", "job", "fair_value (fit)", grupo="RENTA FIJA", unidad="jobs.fair_value",
          cadencia="20:25 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="fair_value"),
    Pieza("MERCADOS", "job", "bonos_ohlc_daily (pivots RF)", grupo="RENTA FIJA",
          unidad="jobs.bonos_ohlc_daily",
          cadencia="20:16 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="bonos_ohlc_daily"),
    # FCI (vista /fci — docs/FCI.md): el universo (Primary + assets) y el VCP diario.
    Pieza("MERCADOS", "job", "fci_universo (fondos de nuestras gerentes)", grupo="FCI",
          unidad="jobs.fci_universo",
          cadencia="diario 12:20 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="fci_universo"),
    Pieza("MERCADOS", "job", "fci_vcp (VCP diario: Primary + tenencia)", grupo="FCI",
          unidad="jobs.fci_vcp",
          cadencia="20:30 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="fci_vcp"),
    Pieza("NEGOCIO", "job", "saldos_a_operadores (aviso diario)", grupo="CLIENTES",
          unidad="jobs.saldos_a_operadores",
          cadencia="19:45 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="saldos_a_operadores"),
    # AP5 corre TODOS los días (también finde: si el viernes falló, el sábado lo
    # recupera solo), así que su ventana es `diario` y no `habil`.
    Pieza("NEGOCIO", "job", "ap5_portfolio (posición futuros cámara)", grupo="CLIENTES",
          unidad="jobs.ap5_portfolio",
          cadencia="13:00 UTC diario", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="ap5_portfolio"),

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
          run_tipo="dolar_mep"),
    Pieza("MERCADOS", "job", "forwards_zscore", grupo="DERIVADOS", unidad="jobs.forwards_zscore",
          cadencia="20:30 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="forwards_zscore"),
    Pieza("MERCADOS", "job", "cierre_canje", grupo="DERIVADOS", unidad="jobs.cierre_canje",
          cadencia="20:35 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="cierre_canje"),
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
    # ⚠️⚠️ **ESTA PIEZA NO TENÍA DE DÓNDE LEER, Y POR ESO DECÍA «NUNCA».**
    #
    # El comentario de acá decía que la frescura por colección era Mongo-only y
    # que esto quedaba «informativo hasta el follow-up». **Ese follow-up ya se
    # hizo** —`Pieza.tabla` lee de Postgres y lo usan todas las demás— pero a
    # esta nunca le pusieron la tabla. Sin `tabla` ni `run_tipo`,
    # `diagnostico._frescura` devuelve `(None, None)` → estado `sin_datos` → el
    # AV AGENT lo canta como **«nunca dejó un rastro: puede no haber corrido
    # jamás»** con el motor perfectamente vivo. Lo levantó el user el 2026-08-28.
    #
    # El motor escribe `mercado.cedears_snapshot` (su docstring lo dice, y la
    # tabla tiene `updated_at`): no había nada que investigar, solo que declarar.
    Pieza("MERCADOS", "motor", "motor_cedears", grupo="RENTA VARIABLE", unidad="motor_cedears",
          cadencia="live", ventana="rueda", umbral_s=60,
          tabla="cedears_snapshot"),
    Pieza("MERCADOS", "job", "precios_acciones_daily", grupo="RENTA VARIABLE", unidad="jobs.precios_acciones_daily",
          cadencia="22:00 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="precios_acciones_daily"),
    Pieza("MERCADOS", "job", "adr_live", grupo="RENTA VARIABLE", unidad="jobs.adr_live",
          cadencia="cada 15m · 13-20 UTC L-V", ventana="rueda", umbral_s=30 * 60,
          run_tipo="adr_live"),
    Pieza("MERCADOS", "job", "day_trading_stats (costumbre TRADE LAB)", grupo="RENTA VARIABLE",
          unidad="jobs.day_trading_stats",
          cadencia="20:06 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="day_trading_stats"),
    Pieza("MERCADOS", "job", "cedears_ohlc_daily (OHLC diario CEDEARs)", grupo="RENTA VARIABLE",
          unidad="jobs.cedears_ohlc_daily",
          cadencia="20:15 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="cedears_ohlc_daily"),

    # ── MERCADOS · OPCIONES (GGAL) ─────────────────────────
    Pieza("MERCADOS", "motor", "motor_options (GGAL)", grupo="OPCIONES", unidad="motor_options",
          cadencia="live", ventana="rueda", umbral_s=180,
          tabla="options_snapshot"),

    Pieza("MERCADOS", "job", "cedears_bars_1m (velas 1m)", grupo="RENTA VARIABLE",
          unidad="jobs.cedears_bars_1m", cadencia="20:20 UTC L-V", ventana="diario",
          umbral_s=int(3 * _D), run_tipo="cedears_bars_1m"),

    # ── NEGOCIO ────────────────────────────────────────────
    Pieza("NEGOCIO", "job", "operaciones_informes", unidad="jobs.operaciones_informes",
          cadencia="cada 30m · 13:30-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="operaciones_informes"),
    Pieza("NEGOCIO", "api", "Aunesa boletos (negocio_movimientos)", unidad="jobs.negocio_movimientos",
          cadencia="cada 60m en rueda", ventana="rueda", umbral_s=70 * 60,
          run_tipo="negocio_movimientos"),
    Pieza("NEGOCIO", "job", "sync_comitentes (clientes)", unidad="jobs.sync_comitentes",
          cadencia="14/17/21 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="sync_comitentes"),
    Pieza("NEGOCIO", "job", "ops_agregado (hot/cold series)", unidad="jobs.ops_agregado",
          cadencia="cada 60m · 14-22 UTC L-V", ventana="rueda", umbral_s=2 * _H,
          run_tipo="ops_agregado"),
    Pieza("NEGOCIO", "job", "ops_tasa_mav (tasa cauciones MAV)", unidad="jobs.ops_tasa_mav",
          cadencia="cada 30m · 14-22 UTC L-V (cierra la cadena negocio)", ventana="rueda",
          umbral_s=70 * 60, run_tipo="ops_tasa_mav"),
    # Cartera PROPIA: mismo endpoint de Aunesa, tiposCuenta=Propia. Corre en línea
    # propia (no en la cadena) — si falla, no corta lo que alimenta pantallas.
    Pieza("NEGOCIO", "job", "Aunesa cartera propia (movimientos_propias)",
          unidad="jobs.movimientos_propias",
          cadencia="cada 30m · 14-22 UTC L-V", ventana="rueda", umbral_s=70 * 60,
          run_tipo="movimientos_propias"),

    # ── BACK OFFICE ────────────────────────────────────────
    # CUSTODIA (CVSA) — la tenencia según la Caja. Es la ÚNICA pieza de esta vista
    # que no viene de Aunesa, y ese es el punto: si se cae, lo que deja de andar
    # no es una pantalla más, es el control cruzado.
    # Umbral 150 min = dos corridas y media: el job va cada hora y el gateway de
    # BYMA cachea su respuesta 60 min, así que una foto de hasta ~2 h es normal y
    # recién la tercera hora perdida es un problema de verdad.
    Pieza("BACK_OFFICE", "job", "custodia CVSA (tenencia de la Caja)", unidad="jobs.custodia_cvsa",
          cadencia="cada 60m · 11-02 UTC L-V (8-24 ART)", ventana="diario",
          umbral_s=150 * 60, run_tipo="custodia_cvsa"),
    Pieza("BACK_OFFICE", "api", "Aunesa boletos (negocio_movimientos)", unidad="jobs.negocio_movimientos",
          cadencia="cada 60m en rueda", ventana="rueda", umbral_s=70 * 60,
          run_tipo="negocio_movimientos"),
    Pieza("BACK_OFFICE", "job", "operaciones_informes", unidad="jobs.operaciones_informes",
          cadencia="cada 30m · 13:30-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="operaciones_informes"),
    Pieza("BACK_OFFICE", "job", "acreencias (cobros futuros)", unidad="jobs.acreencias",
          cadencia="23:45 UTC L-V", ventana="diario", umbral_s=int(3 * _D),
          run_tipo="acreencias"),
    # Tesorería: la única pieza de esa vista que NO es live contra Aunesa.
    Pieza("BACK_OFFICE", "job", "tesorería · foto BANCOS", unidad="jobs.tesoreria_snapshot",
          cadencia="02:50 UTC Ma-Sá (23:50 ART)", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="tesoreria_snapshot"),
    # El espejo de los DEPÓSITOS de cheque: si se corta, la tab RECIBIDOS deja de
    # poblarse sola y el equipo lo carga a mano sin saber que el job murió.
    Pieza("BACK_OFFICE", "job", "tesorería · e-cheq recibidos", unidad="jobs.tesoreria_echeq_recibidos",
          cadencia="cada 30m · 12-21 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="tesoreria_echeq_recibidos"),
    # Extractos bancarios de Interbanking. Es la ÚNICA fuente de esos saldos: si
    # no corre, la tab no miente — se queda quieta, que es peor de detectar.
    Pieza("BACK_OFFICE", "job", "interbanking (extractos)", unidad="jobs.interbanking_sync",
          cadencia="cada 2h · 12-22 UTC L-V", ventana="rueda", umbral_s=int(2.5 * 3600),
          run_tipo="interbanking_sync"),
    # EL OTRO LADO DE LA CONCILIACIÓN. `interbanking_sync` trae lo que dice el
    # BANCO; esto trae lo que dice CONTABILIDAD. Sin los dos no hay comparación,
    # y la falla es de las silenciosas: la tab no miente, se queda quieta.
    Pieza("BACK_OFFICE", "job", "mayor contable", unidad="jobs.mayor_sync",
          # Dos líneas de cron: cada 10' entre 13-14 UTC (la ventana en que
          # contabilidad carga) y cada hora hasta las 21.
          cadencia="cada 10m 13-14 · cada 1h 15-21 UTC L-V", ventana="rueda",
          umbral_s=int(1.5 * 3600), run_tipo="mayor_sync"),

    # ── PORTFOLIOS / Tenencias (SQL) ───────────────────────
    # AuM Mongo (jobs.aum) eliminado 2026-06-15: el writer de tenencias es el
    # cron diario portafolio_backfill --diario → SQL portafolio.tenencia. La
    # frescura SQL no la chequea este registro (solo inventario).
    # El JobRunLogger de portafolio_backfill --diario registra con tipo "aum" (legado).
    Pieza("PORTFOLIOS", "job", "tenencia (snapshot SQL)", unidad="jobs.portafolio_backfill",
          cadencia="11:00 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="aum"),
    Pieza("PORTFOLIOS", "job", "pnl_totales_precompute", unidad="jobs.pnl_totales_precompute",
          cadencia="cada 30m :05,:35 · 15-22 UTC L-V", ventana="rueda", umbral_s=60 * 60,
          run_tipo="pnl_totales_precompute"),
    Pieza("PORTFOLIOS", "job", "consolidado_cuentas", unidad="jobs.consolidado_cuentas",
          cadencia="23:30 UTC L-V", ventana="diario", umbral_s=int(1.5 * _D),
          run_tipo="consolidado_cuentas"),
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
