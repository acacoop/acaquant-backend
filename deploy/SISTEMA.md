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
                 ▼  │  (L-V 13–20 UTC → Mongo)  │ │    order_report → OrdenesLive)
              MongoDB Atlas (M10) ◄── crons (aum, bcra, negocio, …)
                 ▲  ▲                            │
            lee  │  │ atlas_cluster.sh pause 03:30 / resume 11:30 UTC
   api.service (:8000) ──────────────────────────┘   + /mcp (Custom Connector Claude)
   partner_api (:8100)
        ▲  nginx → Cloudflare Access (gate de identidad)
        │ HTTPS
   acaquant-web (Vercel) ── trading.acaquant.com
   proveedor externo ────── data.acaquant.com (partner_api → ACAPortfolio.Cartera)
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
| `motor_portfolio_snapshot` | ?–20:05 L-V | `engines.portfolio_snapshot` | Motor de captura del último precio para tickers de tenencia (Trading.PortfolioSnapshot) |
| `motor_rofex` | ?–20:05 L-V | `engines.valores` | Motor de Captura Rofex a MongoDB (main_valores) |
<!-- /AUTOGEN:motores -->

## Jobs / crons (batch)
<!-- AUTOGEN:crons -->
| Horario | Módulo(s) |
|---|---|
| cada hora · 0-3h · Mar-Sáb | `jobs.market_quotes'` |
| cada hora · 12-23h · L-V | `jobs.market_quotes'` |
| cada 15min · 12-23h · diario | `jobs.news_ingesta'` |
| cada 15min · 13-20h · L-V | `jobs.adr_live'` |
| cada 15min · 13-20h · L-V | `engines.dolar_mep'` |
| cada 20min · 14-23h · L-V | `jobs.sync_postgres'` |
| cada 30min · 12-23h · diario | `jobs.news_finnhub'` |
| cada 4min · 13-20h · L-V | `jobs.comercial_warm'` |
| cada 5min · *h · diario | `jobs.watchdog'` |
| 12:00 · diario | `jobs.argentina_datos'` |
| 12:00 · Dom | `jobs.sync_postgres` |
| cada hora · 13-20h · L-V | `jobs.informe_salud'` |
| 14:00 · L-V | `jobs.sync_comitentes'` |
| cada hora · 14-22h · L-V | `jobs.operaciones_informes'` |
| 15:00 · L-V | `jobs.aum'` |
| cada hora · 15-22h · L-V | `jobs.negocio_movimientos` + `jobs.aranceles` + `jobs.fci_bilateral'` |
| 17:00 · L-V | `jobs.aum'` |
| 17:00 · L-V | `jobs.sync_comitentes'` |
| 02:00 · Mar-Sáb | `jobs.cashflow` |
| 02:00 · Mar-Sáb | `jobs.partner_export'` |
| 20:00 · L-V | `jobs.volatilidad_ggal'` |
| 21:00 · L-V | `jobs.aum'` |
| 21:00 · L-V | `jobs.sync_comitentes'` |
| 22:00 · L-V | `jobs.precios_acciones_daily'` |
| 22:00 · L-V | `jobs.flujo_contrapartes'` |
| 22:00 · L-V | `jobs.bcra` |
| 22:00 · L-V | `jobs.market_anchors'` |
| 23:00 · diario | `jobs.informe_salud'` |
| 23:00 · L-V | `jobs.aum'` |
| 20:10 · L-V | `jobs.cleanup_cedears_timesales'` |
| 20:15 · L-V | `jobs.options_rollup'` |
| 20:25 · L-V | `jobs.snapshot_cierre` + `jobs.fair_value'` |
| 12:30 · L-V | `jobs.cleanup_curvas'` |
| 12:30 · L-V | `jobs.cleanup_futuros_dlr'` |
| cada hora · 13-21h · L-V | `jobs.operaciones_informes'` |
| 18:30 · L-V | `jobs.aum'` |
| 20:30 · L-V | `jobs.forwards_zscore'` |
| 21:30 · L-V | `jobs.partner_export'` |
| 22:30 · L-V | `jobs.actividad_mensual'` |
| 23:30 · L-V | `jobs.consolidado_cuentas'` |
| 11:35 · diario | `jobs.news_ingesta'` |
| 11:35 · diario | `jobs.news_finnhub'` |
| 20:35 · L-V | `jobs.cierre_canje'` |
| 11:40 · diario | `jobs.economic_calendar'` |
| 11:40 · L-V | `jobs.descubrir_cuentas'` |
| cada hora · 13-22h · L-V | `jobs.ops_rollup'` |
| 20:40 · L-V | `jobs.snapshot_sinteticos'` |
| cada hora · 14-22h · L-V | `jobs.comercial_rollup'` |
| 23:45 · L-V | `jobs.acreencias` |
| cada hora · 15-22h · L-V | `jobs.pnl_totales_precompute'` |
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
| 11:30 · diario | `deploy/atlas_cluster.sh resume` |
| 13:30 · L-V | `systemctl restart motor_ordenes.service` |
| 03:30 · diario | `deploy/atlas_cluster.sh pause` |
<!-- /AUTOGEN:otros -->

> Las tablas de arriba solo listan lo **agendado** en `crontab.txt`. Jobs
> manuales / on-demand (backfills, archival: `jobs.*backfill*`,
> `jobs.aum_resumen_fci`, etc.) se corren a mano y NO aparecen. Helpers
> (`jobs._*`, `aunesa_client`, `dias_habiles`) son librerías, no procesos.

## Componentes que NO están en systemd/cron
- **`mae_forex.py` — ⚠️ MANUAL (alguien le tiene que dar play):** feed live del
  dólar mayorista MAE (UST$T plazo 000) → escribe `Valuaciones.DolarOficialLive`.
  **No está automatizado** (ni systemd ni cron). Si nadie lo arranca, el TC
  dólar-linked (`motor_curvas`, `futuros_dlr`, `/argy`, `macro`) se queda con el
  dólar viejo. Es el único proceso del sistema que depende de que un humano lo prenda.
- **acaquant-web (Vercel)**: frontend Next.js, deploy auto sobre `main`. Sin crons propios.
- **MongoDB Atlas (M10)**: la base. Se pausa 03:30 / resume 11:30 UTC = 00:30 / 08:30 ART (cron `atlas_cluster.sh`).
- **Cloudflare Access**: gate de identidad (quién entra). **nginx** (Droplet): reverse proxy `api`→:8000, `partner_api`→:8100.

## Integraciones externas (fuentes de datos)
- **pyRofex** (ROFEX/MAE) — market data WS + envío de órdenes.
- **Aunesa** — movimientos/posiciones (`jobs.cashflow`, `negocio_movimientos`, `descubrir_cuentas`).
- **BYMA Primarias** (licitaciones) · **MAE** (repos/cauciones) · **Finnhub** (data externa) · **BCRA / argentina_datos** (macro).

## Bases de datos (quién escribe qué)
- **`Trading`** — motores de mercado (MarketSnapshot, Curvas, TimeSales, OrderBookL2, DOLAR, SnapshotsCierre, CedearsSnapshot, PreciosAcciones).
- **`Valuaciones`** — `jobs.aum` (AuM, Assets), PnL precompute, DolarOficialLive (PC oficina).
- **`CashFlow`** — `jobs.cashflow`, `jobs.flujo_contrapartes`, `jobs.negocio_movimientos`.
- **`Manager`** — Users, RoleMatrix, Grupos, JobRuns, OrdenesIdempotency.
- **`Operaciones`** — `motor_ordenes` (OrdenesLive/Audit), OperativasMep.
- **`CuentasAPI` / `*API`** — copias derivadas (`jobs.sync_api_copies`).
- **`ACAPortfolio`** — `partner_api` (Cartera) · **`MCP`** — tokens OAuth (TTL).

## Cómo se opera
- Servicios: `systemctl {start|stop|restart|status} <servicio>`; logs `journalctl -u <servicio>`.
- Los motores los prende/apaga el **cron** (fuente: `deploy/crontab.txt`); no arrancarlos a mano fuera de horario (ver RUNBOOK: pausa de Atlas).
- Deploy backend: `git pull` + `systemctl restart api.service`. Frontend: push → Vercel.

> Diagnóstico de incidentes: `docs/RUNBOOK.md` · Secretos: `docs/SECRETS.md`.
