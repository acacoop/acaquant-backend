---
id: config
type: module
layer: config
repo: backend
tags: [module, config, backend]
path: config.py
---

# config

**Archivo:** `config.py`

## Qué hace
`config.py` (raíz) es el único punto donde viven las constantes globales, feature flags y secretos del backend. Lee todo desde variables de entorno (`.env` local / systemd unit en el Droplet) vía `load_dotenv()` y expone valores con defaults seguros, de modo que ningún secreto queda hardcodeado en el repo.

Qué centraliza:
- Credenciales de proveedores externos: ROFEX (`Config.USER/PASSWORD/...`), Aunesa, Finnhub, BYMA (OAuth2), MAE (x-api-key).
- Seguridad/auth: `API_KEY`, `DOLAR_INGEST_TOKEN` (token acotado del endpoint de ingesta del dólar), `ENV` (en `prod` activa el fail-closed de auth), Cloudflare Access (`CF_ACCESS_TEAM/AUD`, service tokens "trusted"), `MANAGER_EMAILS` (acceso a la vista Manager) y bloque MCP (`MCP_BEARER_TOKEN`, `MCP_JWT_SECRET`, issuer y redirect hosts permitidos).
- Listas de negocio no inferibles: `TICKERS_EXTRA_PRECIOS` (tickers que suscribe motor_rofex pero ignora motor_curvas), `PARES_CANJE` (par CCL/MEP por bono), `PARTNER_EXPORT_CUENTAS` (cuentas que se exportan a la API externa), Telegram para alertas y flags `API_PROFILING`.

Conecta con: lo importa medio repo. Los motores (`engines/`) y `core/rofex_session` leen credenciales ROFEX; `core/aunesa`, `core/byma`, `core/mae`, `core/finnhub`, `core/notify` toman sus llaves de acá; `api/auth.py` usa `CF_ACCESS_*`/`MANAGER_EMAILS`, `api/mcp/*` los `MCP_*`, `api/routers/ingest.py` el `DOLAR_INGEST_TOKEN`; `jobs/cierre_canje.py` y `api/services/canje.py` comparten `PARES_CANJE`; `jobs/partner_export.py` usa `PARTNER_EXPORT_CUENTAS`.

## Lo usan (backlinks) ←
- [[api.auth]]  ·  _module_
- [[api.deps]]  ·  _module_
- [[api.main]]  ·  _module_
- [[api.mcp.auth]]  ·  _module_
- [[api.mcp.discovery]]  ·  _module_
- [[api.mcp.oauth]]  ·  _module_
- [[api.profiling]]  ·  _module_
- [[api.routers.ingest]]  ·  _module_
- [[api.services.aunesa_negocio]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[core.aunesa]]  ·  _module_
- [[core.byma]]  ·  _module_
- [[core.finnhub]]  ·  _module_
- [[core.fmp]]  ·  _module_
- [[core.mae]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.roles]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[jobs.aum]]  ·  _module_
- [[jobs.cashflow]]  ·  _module_
- [[jobs.cierre_canje]]  ·  _module_
- [[jobs.comercial_warm]]  ·  _module_
- [[jobs.guardrails]]  ·  _module_
- [[jobs.partner_export]]  ·  _module_
- [[jobs.sync_comitentes]]  ·  _module_
