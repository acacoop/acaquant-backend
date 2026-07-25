# SISTEMA — plano único de TradingAV

> **Fuente de verdad del sistema corriendo.** Las tablas de inventario se
> AUTO-GENERAN desde `deploy/systemd/*.service` + `deploy/crontab.txt` con
> `python -m scripts.gen_sistema` (no editar a mano entre los marcadores
> AUTOGEN). La narrativa (topología, flujo, bases) se mantiene a mano.

## Topología — cómo se conecta todo

```
   mae_forex.py              pyRofex (broker ROFEX/MAE)
   ⚠️ MANUAL ────┐            │ WS market data    ▲ envío/cancel órdenes
   (dólar MAE)   │            ▼                   │
                 │  ┌──── motores de mercado ───┐ │
                 │  │ rofex, options, curvas, … │ │   (motor_ordenes escucha
                 ▼  │  (L-V 13–20 UTC → SQL)    │ │    order_report → ordenes_live)
              Postgres / Supabase ◄── crons (portafolio, bcra, negocio, …)
                 ▲  ▲                            │
            lee  │  └────────────────────────────┘
   api.service (:8000) ──────────────────────────┘   + /mcp (Custom Connector Claude)
   partner_api (:8100)
        ▲  nginx → Cloudflare Access (gate de identidad)
        │ HTTPS
   acaquant-web (Vercel) ── trading.acaquant.com
   proveedor externo ────── data.acaquant.com (partner_api → partner.cartera)
```

## Servicios always-on
<!-- AUTOGEN:servicios -->
| Servicio | Puerto | Target | Qué hace |
|---|---|---|---|
| `api` | 8000 | `api.main:app` (uvicorn) | TradingAV API (FastAPI + uvicorn) |
| `partner_api` | 8100 | `partner_api.main:app` (uvicorn) | Acaquant Partner API (servicio externo de datos de portfolio) |
<!-- /AUTOGEN:servicios -->

## Motores de mercado (cron start/stop L-V)
<!-- AUTOGEN:motores -->
| Servicio | Horario | Target | Qué hace |
|---|---|---|---|
| `motor_agro` | ?–20:05 L-V | `engines.motor_agro` | Motor Futuros Agro - Trigo/Maiz/Soja Rosario (FXXXSX) |
| `motor_agro_opciones` | ?–20:05 L-V | `engines.motor_agro_opciones` | Motor Opciones Agro - Trigo/Maiz/Soja Rosario (OCAFXS/OPAFXS) |
| `motor_breakevens` | ?–20:05 L-V | `engines.breakevens` | Motor Breakevens - Inflacion implicita CER/Lecap en tiempo real |
| `motor_caucion` | ?–20:05 L-V | `engines.caucion` | Motor Caucion - TNA caucion ARS y USD a corto plazo (1D, viernes 3D) |
| `motor_cedears` | ?–20:05 L-V | `engines.motor_cedears` | Motor CEDEARs - TradingAV |
| `motor_curvas` | ?–20:05 L-V | `engines.curvas` | Motor Curvas - Enriquecimiento TEA/Duration TimeSales |
| `motor_dolares` | ?–20:05 L-V | `engines.dolares` | Motor Dolares - MEP/CCL/canje en tiempo real (WS) |
| `motor_forwards` | ?–20:05 L-V | `engines.forwards` | Motor Forwards - Tasas forward en tiempo real |
| `motor_futuros_dlr` | ?–20:05 L-V | `engines.futuros_dlr` | Motor Futuros DLR - Curva outright Dolar A3500 con tasa implicita |
| `motor_options` | ?–20:05 L-V | `engines.options` | Motor Opciones GGAL - TradingAV |
| `motor_ordenes` | ?–20:05 L-V | `engines.motor_ordenes` | Motor Ordenes - escucha order_report y persiste OrdenesLive/Audit |
| `motor_portfolio_snapshot` | ?–20:05 L-V | `engines.portfolio_snapshot` | Motor de captura del último precio para tickers de tenencia (valuaciones.portfolio_snapshot) |
| `motor_rofex` | ?–20:05 L-V | `engines.valores` | Motor de Captura Rofex a SQL (main_valores) |
<!-- /AUTOGEN:motores -->

## Jobs / crons (batch)
<!-- AUTOGEN:crons -->
| Horario | Módulo(s) |
|---|---|
| cada hora · 0-3h · Mar-Sáb | `jobs.market_quotes'` |
| cada hora · 12-23h · L-V | `jobs.market_quotes'` |
| cada 10min · *h · diario | `jobs.triage'` |
| cada 15min · 12-23h · diario | `jobs.news_ingesta'` |
| cada 15min · 13-20h · L-V | `jobs.adr_live'` |
| cada 15min · 13-20h · L-V | `engines.dolar_mep'` |
| cada 30min · 10-14h · L-V | `jobs.research_mail'` |
| cada 30min · 12-23h · diario | `jobs.news_finnhub'` |
| cada 30min · 14-22h · L-V | `jobs.negocio_movimientos` + `jobs.aranceles` + `jobs.fci_bilateral'` |
| cada 4min · 13-20h · L-V | `jobs.comercial_warm'` |
| 11:00 · L-V | `jobs.portafolio_backfill` |
| 12:00 · diario | `jobs.argentina_datos'` |
| 12,16,20,23:0 · L-V | `jobs.fred_research'` |
| 12,16,20,23:0 · 1-6 | `jobs.bcra_research'` |
| 14:00 · L-V | `jobs.sync_comitentes'` |
| cada hora · 14-22h · L-V | `jobs.operaciones_informes'` |
| 17:00 · L-V | `jobs.sync_comitentes'` |
| 02:00 · Mar-Sáb | `jobs.cashflow` |
| 02:00 · Mar-Sáb | `jobs.partner_export'` |
| 20:00 · L-V | `jobs.volatilidad_ggal'` |
| 21:00 · L-V | `jobs.sync_comitentes'` |
| 22:00 · L-V | `jobs.precios_acciones_daily'` |
| 22:00 · L-V | `jobs.bcra` |
| 22:00 · L-V | `jobs.market_anchors'` |
| 22:00 · L-V | `jobs.mercado_1816_series'` |
| 21:10 · L-V | `jobs.eikon_cierres'` |
| 20:15 · L-V | `jobs.options_rollup'` |
| 20:15 · L-V | `jobs.cedears_ohlc_daily'` |
| 20:16 · L-V | `jobs.bonos_ohlc_daily'` |
| 03:20 · diario | `jobs.cleanup_retencion'` |
| 20:25 · L-V | `jobs.snapshot_cierre` + `jobs.fair_value'` |
| 12:30 · L-V | `jobs.cleanup_curvas'` |
| 12:30 · L-V | `jobs.cleanup_futuros_dlr'` |
| 12:30 · L-V | `jobs.consolidado_cuentas'` |
| cada hora · 13-21h · L-V | `jobs.operaciones_informes'` |
| 16:30 · L-V | `jobs.controles_datos'` |
| 20:30 · L-V | `jobs.forwards_zscore'` |
| 21:30 · L-V | `jobs.partner_export'` |
| 21:30 · L-V | `jobs.ia_calidad'` |
| 22:30 · L-V | `jobs.actividad_mensual'` |
| 11:35 · diario | `jobs.news_ingesta'` |
| 11:35 · diario | `jobs.news_finnhub'` |
| 20:35 · L-V | `jobs.cierre_canje'` |
| 11:40 · diario | `jobs.economic_calendar'` |
| 20:40 · L-V | `jobs.snapshot_sinteticos'` |
| 12:45 · L-V | `jobs.acreencias` |
| 20:45 · L-V | `jobs.guardrails'` |
| cada hora · 15-22h · L-V | `jobs.pnl_totales_precompute'` |
| 20:50 · L-V | `jobs.archive_options_data` |
| 23:50 · L-V | `jobs.cleanup_cedears_timesales'` |
| 20:06 · L-V | `jobs.day_trading_stats'` |
<!-- /AUTOGEN:crons -->

## Otros crons (scripts / shell)
<!-- AUTOGEN:otros -->
| Horario | Comando |
|---|---|
| 13:20 · L-V | `systemctl restart motor_rofex.service` |
| 13:20 · L-V | `systemctl restart motor_options.service` |
| 13:20 · L-V | `systemctl restart motor_curvas.service` |
| 13:20 · L-V | `systemctl restart motor_forwards.service` |
| 13:20 · L-V | `systemctl restart motor_breakevens.service` |
| 13:20 · L-V | `systemctl restart motor_caucion.service` |
| 13:20 · L-V | `systemctl restart motor_futuros_dlr.service` |
| 13:20 · L-V | `systemctl restart motor_dolares.service` |
| 13:20 · L-V | `systemctl restart motor_cedears.service` |
| 13:20 · L-V | `systemctl restart motor_agro.service` |
| 13:20 · L-V | `systemctl restart motor_agro_opciones.service` |
| 13:20 · L-V | `systemctl restart motor_portfolio_snapshot.service` |
| 13:30 · L-V | `systemctl restart motor_ordenes.service` |
<!-- /AUTOGEN:otros -->

> Las tablas de arriba solo listan lo **agendado** en `crontab.txt`. Jobs
> manuales / on-demand (backfills, archival: `jobs.*backfill*`,
> `jobs.aum_resumen_fci`, etc.) se corren a mano y NO aparecen. Helpers
> (`jobs._*`, `aunesa_client`, `dias_habiles`) son librerías, no procesos.

## Componentes que NO están en systemd/cron
- **`mae_forex.py` — ⚠️ MANUAL (alguien le tiene que dar play):** feed live del
  dólar mayorista MAE (UST$T plazo 000) → escribe `valuaciones.dolar_oficial_live`.
  **No está automatizado** (ni systemd ni cron). Si nadie lo arranca, el TC
  dólar-linked (`motor_curvas`, `futuros_dlr`, `/argy`, `macro`) se queda con el
  dólar viejo. Es el único proceso del sistema que depende de que un humano lo prenda.
- **acaquant-web (Vercel)**: frontend Next.js, deploy auto sobre `main`. Sin crons propios.
- **Postgres / Supabase**: la base (única, decomiso Mongo 2026-06-29). Acceso: `core.postgres.get_pool` (app) / `partner_api/pg.py` (partner).
- **Cloudflare Access**: gate de identidad (quién entra). **nginx** (Droplet): reverse proxy `api`→:8000, `partner_api`→:8100.

## Integraciones externas (fuentes de datos)
- **pyRofex** (ROFEX/MAE) — market data WS + envío de órdenes.
- **Aunesa** — movimientos/posiciones (`jobs.cashflow`, `negocio_movimientos`, `descubrir_cuentas`).
- **BYMA Primarias** (licitaciones) · **MAE** (repos/cauciones) · **Finnhub** (data externa) · **BCRA / argentina_datos** (macro).

## Bases de datos (schemas SQL — quién escribe qué)
- **`mercado`** — motores de mercado (market_snapshot, curvas, timesales, snapshots_cierre, cedears_snapshot, precios_acciones, futuros_dlr_snapshot, options_*, agro_*).
- **`macro`** — `jobs.bcra`/`jobs.argentina_datos` (series_macro, uva, rem).
- **`valuaciones`** — PnL precompute (pnl_totales_cache, consolidado), dolar_oficial_live (PC oficina), portfolio_snapshot.
- **`portafolio`** — `jobs.portafolio_backfill` (tenencia=AuM, assets).
- **`operaciones`** — `jobs.negocio_movimientos`/`jobs.operaciones_informes` + `motor_ordenes` (operaciones, negocio_movimientos, acreencias, ordenes_*).
- **`clientes`** — comitentes, cuentas, contrapartes, accionistas, actividad_mensual.
- **`manager`** — manager_users, role_matrix, grupos, job_runs · **`home`** — quotes/calendar/news · **`mcp`** — OAuth (TTL).
- **`partner`** — `partner_api` (cartera, api_users).

## Cómo se opera
- Servicios: `systemctl {start|stop|restart|status} <servicio>`; logs `journalctl -u <servicio>`.
- Los motores los prende/apaga el **cron** (fuente: `deploy/crontab.txt`); no arrancarlos a mano fuera de horario (ver RUNBOOK).
- Deploy backend: `git pull` + `systemctl restart api.service`. Frontend: push → Vercel.

> Diagnóstico de incidentes: `docs/RUNBOOK.md` · Secretos: `docs/SECRETS.md`.
