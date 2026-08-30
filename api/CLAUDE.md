# api/ — contexto del subdirectorio

Se carga al trabajar en `api/`. El `CLAUDE.md` raíz tiene lo project-wide
(overview, REGLA #0, estructura, deploy).

`api/services/` es lógica pura (sin FastAPI), invocada por los routers.
`api/routers/` es solo HTTP plumbing — `manager/` es un paquete de sub-routers.

**Acá NO hay asistente conversacional.** El legacy `api/agent/` + `POST /api/chat`
se eliminaron el 2026-06-03; el MCP server que lo reemplazó (`api/mcp/`, con su
provider OAuth) se borró el 2026-08-28. No documentar ni referenciar ninguno de
los dos: si ves un dir vacío o una mención suelta, es residual.

> **Postura de seguridad de la API consolidada: `docs/SECURITY.md`** —
> capas (CF Access → API_KEY → JWT → RBAC → rate limit), secretos, y el
> checklist al tocar la API.

## ⚠️ REGLA #1 — VALIDAR IMPORTS antes de pushear router/service

**Bloqueante. No opcional.** Un import error en CUALQUIER router/service
montado en `api/main.py` tumba **TODA** la API (no solo el módulo nuevo)
porque `api.service` corre como un proceso único. Cuando el user hace
`git pull && systemctl restart api.service` el proceso queda en `failed`
state y toda la web devuelve 502 — renta-fija, derivados, operaciones, todo cae.

**Antes de `git push` de cualquier cambio que toque `api/routers/*`,
`api/services/*`, `api/main.py`, `api/deps.py`, `api/auth.py`,
`core/roles.py` o cualquier import-chain de `api/main.py`**, ejecutar:

```bash
.venv/bin/python -c "from api.main import app; print(len(app.routes), 'routes OK')"
```

Si tira ImportError / AttributeError / NameError, NO pushear. Fixar primero.

Ruff y typecheck NO capturan esto — son análisis estáticos. Solo importar
realmente el módulo detecta `from X import Y` cuando Y vive en otro módulo
(lo más común). Caso real (2026-05-12): se pusheó `from core.roles import
require_module` cuando vive en `api.auth` — caída total por un solo símbolo.

> Hay un hook (`.claude/hooks/check_imports.sh`) que corre este check en
> cada `git push` y lo **bloquea** si falla. El hook es la red; igual
> validá antes para no comerte el bloqueo.

## Services con `@cached` → siempre invocar con kwargs

El wrapper de cache genera el cache key por nombre de argumento → posicional
revienta con `TypeError` en el wrapper. `await get_X(123)` ❌;
`await get_X(id=123)` ✓. Incidente 2026-05-12 (commit `4e4428a`); volvió a
pasar con `get_historico_curva` (commit `746d8b6`).

## RBAC

Cloudflare Access = quién entra. `core/roles.py` = qué ve.

Módulos canónicos (`core/roles.py::MODULES`): `home, renta-fija, derivados,
renta-variable, operar, operaciones, portfolios, manager`.
(`asistente` se quitó al eliminar `api/agent`; verificá `MODULES` antes de
asumir la lista exacta.)

| Módulo | admin | trader | sales |
|---|---|---|---|
| home / renta-fija / derivados / renta-variable | ✓ | ✓ | ✓ |
| operaciones / portfolios | ✓ | ✓ | – |
| operar (envío/cancel de órdenes) | ✓ | – | – |
| manager | ✓ | – | – |

`operar` es admin-only en `DEFAULT_MATRIX` (decisión 2026-05-17) y `sales` se
sacó de la matriz viva el 2026-05-23. **OJO**: `manager.role_matrix` (SQL)
PISA el default — el enforcement real es lo que esté ahí, editable desde
`/manager → ROLES Y PERMISOS`. Además del gate de módulo, los endpoints de
órdenes aplican scope de cuenta por grupo (`verificar_account`, ver
`api/services/_grupos_scope.py`).

`renta-variable` (Scanner CEDEARs sobre `mercado.cedears_snapshot` +
`mercado.precios_acciones`) está abierto a los 3 roles desde 2026-05-13.
Agregar módulo nuevo: (1) sumar el string a `MODULES`, (2) actualizar
`ENDPOINT_MODULE_PREFIXES` en `api/auth.py`, (3) editar la matriz en
`manager.role_matrix` (o `DEFAULT_MATRIX`).

Tablas `manager.{manager_users, role_matrix, role_audit}`. Helpers: `get_user_role`,
`has_access`, `require_module(m)` (dependency — código nuevo usa esto, NO
`require_manager`, alias legacy). Cache TTL 60s → `invalidate_cache()`
post-mutación. Matriz editable desde `/manager → ROLES Y PERMISOS`.

`GET /api/me` → `{email, role, modules, is_admin}`. Lo consume el frontend
para filtrar nav + `src/proxy.ts` para redirects. Fallback: `MANAGER_EMAILS`
env (legacy) → `DEFAULT_ROLE="sales"`.

## Filtros de cuenta en endpoints

`api/services/_cuentas_filter.py::match_cuenta_filter(filtro)` devuelve el
filtro SQL por tipo: `todas`, `accionistas`
(∈ `clientes.accionistas.cuenta`), `sin_accionistas`, `cooperativas`
(∉ accionistas + regex `\bcoop`), `productores` (matchea por `id_cuenta` ∈
SQL `clientes.comitentes WHERE nivel_1='PRODUCTORES'`; el resto matchea por el
string `cuenta`).

Lo usan operaciones (vista negocio) y portfolio (AuM por cartera, FCI, total,
diff). `VALID_FILTERS` único — sumar tipos nuevos en un solo lugar.

## Patrón "live fallback" (cierre persistido + live de hoy)

Endpoints que sirven data agregada del cierre diario y aceptan `fecha` como
input deben leer `mercado.snapshots_cierre` primero y, si no hay fila para hoy
(cron `jobs.snapshot_cierre` corre 20:25 UTC), caer a
`mercado.market_snapshot` (metrics). Mismos campos, mismo shape.

Sin esto, durante horario de mercado las vistas se quedan en el cierre del
día anterior hábil hasta que corra el cron. Con esto, `fecha=hoy` siempre
devuelve datos vigentes.

Implementado en: `analitica.py::snapshot_curva_historico`,
`renta_fija.py::get_historico_curva`, `carry_trade.py::_precios_diarios_curva`.
Si se agregan endpoints similares, aplicar el mismo patrón. **Frontend
complementa**: las routes de Next necesitan `dynamic = "force-dynamic"` +
`revalidate = 0` + `Cache-Control: no-store`.

## Motor de Valuaciones (PnL Títulos)

Doc dedicado: **`docs/MOTOR_VALUACIONES.md`** — leer antes de tocar
`api/services/pnl.py`.

`/aum → VALUACIONES → PNL TÍTULOS` calcula PnL por (cuenta, ticker) con
cost-basis weighted-average. Tres KPIs: realizado / no-realizado / pasivo
(cupones+divs+amorts). Endpoint `GET /api/portfolio/pnl?id_cuenta=X`.

**Reglas críticas:**
- `pnl_no_realizado = valor_aum − costo_remanente`. NO usar
  `qty × precio_actual` — el precio del AuM viene en paridad cruda.
- Mapping `unidad ↔ ticker` viene de **SQL `portafolio.assets`** (vía
  `assets_sql.py::assets_rows`, campo `TICKER`), no de regex sobre la unidad.
- Cada boleto en `operaciones.negocio_movimientos` tiene `mep` snapshot inmutable.
  Pesificación = `importe × b.mep`. Fallback a `_mep.get_mep_for_date()`
  solo si `mep=null`.
- Categorías que entran al cost-basis: `compra, venta, suscripcion_fci,
  rescate_fci, acreencia`. `comision` se ignora — el `importe` ya viene neto.
- "Licitación" del primario se categoriza como `compra`.

**TOTALES** (`pnl_todas_cuentas`) y **POR CUENTA** (`valuacion_consolidada`)
NO recalculan en vivo — leen `valuaciones.pnl_totales_cache` /
`valuaciones.consolidado`, precalculadas por los crons
`jobs.pnl_totales_precompute` / `jobs.consolidado_cuentas`.
