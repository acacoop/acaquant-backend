# AUDIT — TradingAV

Plataforma cuantitativa para mercados argentinos (MERVAL/ROFEX). Stream real-time, motores analíticos paralelos, persistencia en MongoDB Atlas, dashboard Streamlit en `www.acaquant.com`.

## Técnicas

- **Event-driven multi-engine**: un WebSocket pyRofex dispatcha ticks a motores independientes (valores, options, curvas, forwards, breakevens). Cada motor implementa `update_price(ticker, data)` y escribe a su propia colección.
- **Streamlit fragments + caches**: vistas pesadas usan `@st.fragment(run_every=N)` para refresh independiente; loaders usan `@st.cache_data(ttl)` y `@st.cache_resource` para pool de clientes Mongo.
- **Dos clientes MongoDB singleton**: `get_mongo_client()` (read-write, motores + Manager) y `get_mongo_client_read()` (read-only, dashboard). Pool compartido, jamás `client.close()`.
- **Capas**: `core/` no importa a nadie; `engines/` y `jobs/` importan `core/` + `quant/`; `dashboard/` solo lee (excepto Manager).
- **Cálculo cuantitativo**: Black-Scholes con IV por Newton-Raphson (`quant/black_scholes.py`); enriquecimiento de trades con TEA/TEM/Duration/Paridad (`engines/curvas.py`); matrices forward NxN y breakevens de inflación implícita.
- **Microestructura**: snapshots 1s con VWAP, VPIN, imbalance, spread, hourly stats, top trades.
- **Invocación uniforme**: todo corre como `python -m <módulo>` desde la raíz del repo; los systemd units respetan `WorkingDirectory=/root/TradingAV`.

## Servicios

**Always-on en Droplet DigitalOcean**
- `cloudflared.service` → tunnel Cloudflare (expone el Streamlit al mundo detrás de Access).
- `streamlit.service` → dashboard web.

**Motores de mercado (L-V, horario de mercado, systemd)**
- `motor_rofex` → microstructure + market snapshots.
- `motor_options` → Greeks y IV de GGAL.
- `motor_curvas` → enriquecimiento TEA/Duration/Paridad.
- `motor_forwards` → tasas forward cada 30s.
- `motor_breakevens` → breakevens inflación cada 30s.

**Jobs batch (crontab)**
- `jobs.carteras` (4×/día L-V) — snapshot posiciones Aunesa.
- `jobs.aum` (23:00 L-V) — snapshot AuM + sync CarterasII del primer día hábil del mes anterior.
- `jobs.cashflow --today` + `jobs.flujo_contrapartes` (02:00 Mar-Sáb) — cash y operaciones diarias.
- `jobs.bcra --today` (20:00 diario) — CER/TAMAR/DOLAR/BADLAR.
- `jobs.volatilidad_ggal` (20:00 L-V) — HV cierre GGAL.
- `engines.dolar_mep` (14:00 / 19:57 L-V) — snapshot MEP.

**Auth**
- Cloudflare Access con OTP por email.
- Vista Manager restringida por header `Cf-Access-Authenticated-User-Email` contra whitelist `MANAGER_EMAILS` (`.env`).

## Tecnologías

| Capa | Stack |
|---|---|
| Ingesta market data | `pyRofex` (WebSocket ROFEX/MERVAL) |
| Fuente externa carteras | API Aunesa (auth + `posicionValuada`) |
| Fuente externa macro | API BCRA (CER, TAMAR, Dólar A3500, BADLAR) |
| Persistencia | MongoDB Atlas — DBs `Trading`, `Opciones`, `Valuaciones`, `CashFlow` |
| Frontend | Streamlit + Altair v4 |
| Cálculo | NumPy / Pandas, Black-Scholes propio, Newton-Raphson para IV |
| Logging/UI terminal | `rich` |
| Infra | Droplet DigitalOcean (systemd + cron), Cloudflare Tunnel + Access |
| Entorno | Python venv en `/root/TradingAV/venv/` |

No hay suite de tests ni linter configurado. Diagnóstico y chequeos manuales vía `scripts/` (`crear_indices`, `check_forwards`, `check_tasa_fija`, `check_aum_raw`, etc.).

## Dashboard — vistas

Nav: **Mercado · Opciones · Portfolios · Operaciones · AuM · Manager**.

- **Mercado** — microstructure, libro en tiempo real (2s), curvas, breakevens (live/histórico/gráfico/simulador), forwards, retorno total, volúmenes.
- **Opciones** — cadena GGAL con smile de volatilidad + estrategias pre-configuradas con payoff.
- **Portfolios** — informe ejecutivo mensual por cuenta (Reportes).
- **Operaciones** — Cash Flow, Contrapartes, Análisis, Flujo vs AuM.
- **AuM** — FCI, Análisis SG, Tasa Fija, CER.
- **Manager** (solo admins) — Diagnóstico, Backfills, Validaciones, Logs, Historial, Setup, Latencia.
