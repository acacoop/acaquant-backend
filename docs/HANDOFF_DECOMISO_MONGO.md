# HANDOFF — Decomiso Mongo (cambio de PC, 2026-06-29)

Estado al momento del handoff. Para retomar en otra PC: `git pull` y seguir desde acá.

## ✅ HECHO

- **El M10 (Atlas) está PAUSADO** (`bash deploy/atlas_cluster.sh pause` → HTTP 200, `"paused":true`).
  Ya no se paga compute. Para **cortar el costo del todo**: terminar el cluster desde la UI de Atlas
  (Clusters → ACAQuant → "..." → Terminate). Mongo está VACÍO (cero colecciones) → no se pierde nada.
  El cron de pause/resume por hora YA fue eliminado (no existe más).
- **Mongo está 100% vacío.** Esto NO es migración de datos — es limpiar el CÓDIGO que todavía
  "nombra" Mongo para que ningún proceso intente conectar (si no, tira 500 con el M10 pausado).
- **~31 commits `decomiso` pusheados a `main`.** SQL-only y verificado (cada uno con `from api.main
  import app` OK + ruff limpio):
  - Infra/arranque: `api/main` (sin warmup ping), motores (curvas/valores/futuros_dlr/motor_cedears),
    TODOS los jobs, partner_export, health (informe_salud/watchdog), cleanup_futuros_dlr.
  - Auth (CRÍTICO, con fail-safe): `core/roles.py` (login/RBAC), `core/grupos.py`, MCP `api/mcp/oauth.py`.
  - Mercado: renta_fija, macro (+mep/ccl/canje), opciones (motor+service+jobs+MCP), derivados, repo
    (caución), rem, market, agro (lecturas), carry_trade, comparar_inversion, sinteticos.
  - Renta variable: rv_motor, day_trading, **scanner** (delegado a scanner_sql — arregló el Trade Lab).
  - Dashboard `/manager`: jobs, status, checks, diagnostico.
  - PnL totales (`carteras /pnl-todas` → pnl_sql). agro_sql.get_mejoras_dispo (futuros SQL).
  - quant/black_scholes (puro), core/openfigi (borrado), 24 scripts Mongo-only + 3 jobs muertos borrados.

## ⏳ FALTA (lo que queda con referencia a Mongo en el código)

Patrón: o **delegar** la función Mongo a su gemelo `*_sql`, o **portear** la lectura a `mercado.*`
(la data ya está toda en SQL). OJO: algunos `_sql` reusan helpers PUROS del gemelo Mongo — NO borrarlos.

**✅ URGENTES — TODOS CERRADOS (Olas 3.31-3.34, 2026-06-29 noche):**
- ✅ `derivados_agro.py` (Ola 3.31) — `set_pizarra` ya era SQL; se borraron los 3 reads Mongo muertos
  vía router (get_pase_agro/get_panel_opciones/simular_estrategia). Helpers puros conservados.
- ✅ `acreencias.py` (Ola 3.32) — OnsIgnoradas → SQL `mercado.ons_ignoradas`; `cargar_cer`/
  `cargar_dias_habiles` eran SQL-native (param client vestigial). El conciliador manager/bonos ya no 500ea.
- ✅ `debug_curva.py` (Ola 3.33) — todo el handle Mongo era vestigial (`_last_trade` usa snapshot_docs SQL;
  cargar_* SQL-native). Se sacó el cliente + import.
- ✅ `mejoras_dispo.py` (Ola 3.34) — read Mongo muerto vía router borrado; quedan helpers puros para agro_sql.

**Gateados por flag (NO conectan en prod con el flag en SQL — bajar prioridad, son "rastro"):**
- ✅ `api/services/comercial.py` (Ola 3.35) — guteado: 1292→432 líneas. Las 14 funciones Mongo eran
  muertas vía router (operaciones.py usa comercial_sql SIEMPRE). Sobreviven las 4 SQL-clean que el router
  llama directo (cobros_futuros/_cliente, referido_clientes/_fci) + helpers/constantes externas.
- ✅ `api/services/pnl_sql.py` (Ola 3.36) — los `get_db_*` eran VESTIGIALES (el motor solo los deref en su
  rama fallback; el path SQL inyecta todos los deps) → se pasa None, sin import Mongo.
- ✅ `api/services/pnl.py` (Ola 3.37) — guteado: 1139→700 líneas. Borradas las funciones muertas vía router
  + las 4 ramas Mongo del motor compartido (deps SQL son la única fuente; boletos ya vienen ORDER BY
  fecha,comprobante de _deps_sql — cost-basis preservado). `_es_cash`/helpers puros conservados.

**⚠️ valuaciones.py — NO es gut de limpieza, es MIGRACIÓN REAL INCOMPLETA (PENDIENTE, decisión):**
- `api/services/valuaciones.py` (~1540 líneas, gated VALUACIONES_SQL). `valuaciones_sql` cubre SOLO 4
  funciones (posiciones_actuales, serie_valor_cuenta, valuacion_consolidada, variacion_titulos). La tabla
  MENSUAL (`valuacion_mensual`) tiene rama SQL interna (`engine='sql'` → `_cierres_fecha_data` lee
  portafolio.tenencia). PERO siguen **Mongo-only SIN gemelo SQL**: `aum_raw`, `valuacion_mensual_debug`,
  `movimientos_mes` (+ `posiciones_cuenta`). NO se pueden gutear sin romper esos endpoints.
- ⚠️ Con el M10 PAUSADO/vacío NO se puede correr el GATE de paridad SQL↔Mongo → escribir esos gemelos a
  ciegas viola REGLA #2 (peor en plata/AuM). Decisión del user: (a) escribir los twins SQL + validar de
  otra forma, o (b) confirmar que esos endpoints (los 3 son debug/secundarios) se pueden discontinuar.
- El cron `jobs/consolidado_cuentas.py` aún importa `construir_consolidado` (Mongo) — escribe el cache
  `valuaciones.consolidado` (SQL). Revisar si `construir_consolidado` lee Mongo o ya es SQL interno.
- `valuaciones_sql` importa `_es_cash` de valuaciones (pure) — dejarlo al gutear.

**Infra final (hacer AL ÚLTIMO, cuando ningún service use get_db_* — BLOQUEADO por valuaciones.py):**
- `api/db.py` (`get_db_opciones/trading/valuaciones/cashflow/clientes/manager`) — solo conecta cuando
  se LLAMA. Borrar cuando no quede ningún caller.
- `api/deps.py` — reexporta los get_db_* de api/db (compat). Limpiar junto con api/db.
- `core/snapshot_writer.py` — gated SNAPSHOT_SQL; verificar que los motores ya escriben SQL y gutear.
- `core/mongo.py` + `core/mongo_monitor.py` — el cliente Mongo. Borrar/neutralizar al FINAL (cuando NADA
  importe get_mongo_client). `core/mongo.py` registra mongo_monitor al importar (no conecta).

**Después del código:** docs (CLAUDE.md raíz + api/engines/jobs/scripts CLAUDE.md, docs/*, .claude/skills,
regen `python -m scripts.gen_obsidian` + `gen_sistema`), sacar `MONGO_URI`/`ATLAS_*` del `.env`,
borrar `deploy/atlas_cluster.sh`.

## 🔧 Cómo verificar cada cambio (REGLA #1)

```bash
./.venv/Scripts/python.exe -c "from api.main import app; print(len(app.routes))"   # debe dar 256
./.venv/Scripts/python.exe -m ruff check --fix <archivo>
```
Buscar lo que falta: `grep -rln "get_mongo_client\|get_db_\|from core.mongo" api/ core/ quant/ | grep -v core/mongo`

## ⚠️ Nota: 8 tests unit rojos PRE-EXISTENTES (no son de esta campaña)

`tests/unit/test_argentina_datos.py` + `test_cotizaciones_tier2.py` mockean Mongo para servicios que ya
son SQL. Estaban rojos ANTES de empezar. Arreglarlos = actualizar los mocks a SQL (o marcarlos skip).
