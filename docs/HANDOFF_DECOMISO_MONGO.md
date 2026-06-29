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
- `api/services/valuaciones.py` (1500 líneas, gated VALUACIONES_SQL → usa valuaciones_sql). El gemelo
  Mongo. Verificar que el router (api/routers/valuaciones.py + manager/valuaciones.py) flipee SIEMPRE a
  valuaciones_sql, y gutear valuaciones.py (cuidado: valuaciones_sql importa `_es_cash` de valuaciones — pure, dejarlo).
- `api/services/pnl.py` — gemelo Mongo de pnl_sql. Ya NADIE lo usa vía router (pnl + pnl-todas son SQL).
  Verificar que ningún caller directo quede, y gutear (cuidado con helpers puros: `_aplicar_normalizer`,
  `_build_unidad_maps` — si los usa pnl_sql, dejarlos).
- `api/services/pnl_sql.py` — ⚠️ tiene `get_db_cashflow/trading/valuaciones` en la firma de helpers que
  YA leen SQL (vestigial, como pasó en carry_trade/renta_fija). Revisar si son llamadas reales o vestigiales.
- `api/services/comercial.py` (23 refs, el más grande) — el router YA usa comercial_sql (SIEMPRE SQL,
  ver operaciones.py:329). Es el gemelo Mongo muerto vía router. PERO exporta helpers usados directo:
  `_cuentas_de_operador` (lo usa carteras.py:42 scope_aum) y `_CATS_VOLUMEN` (lo usa sin_operador.py).
  → Portear esos 2 helpers a SQL (clientes.comitentes / operaciones), o moverlos a comercial_sql, y
  gutear el resto de comercial.py.

**Infra final (hacer AL ÚLTIMO, cuando ningún service use get_db_*):**
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
