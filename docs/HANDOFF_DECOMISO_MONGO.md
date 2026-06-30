# HANDOFF — Decomiso Mongo (COMPLETO a nivel código, 2026-06-29)

## ✅ MONGO ESTÁ FUERA DEL CÓDIGO

`grep -rE "from core.mongo|import pymongo|MongoClient" --include="*.py" .` → **0 resultados.**
El app vivo (api/core/engines/jobs), el partner_api y los scripts NO tocan Mongo.
`from api.main import app` OK (256 rutas) · suite unit **238 passed, 0 failed**.

### Cómo se llegó (Olas 3.1 → 3.40 + infra, 2026-06-29)
- **Motores/jobs/infra** (3.1-3.8): boot sin warmup Mongo, motores y jobs SQL-only.
- **Auth/mercado/RV/dashboard/PnL** (3.9-3.30): roles, grupos, MCP, renta fija, macro,
  opciones, agro, scanner, day_trading, manager, etc.
- **Cierre de services** (3.31-3.40): derivados_agro, acreencias, debug_curva,
  mejoras_dispo, comercial (1292→432), pnl/pnl_sql (1139→700, motor sin ramas Mongo),
  valuaciones (1572→1147, routers forzados a SQL), partner_user + cargar_accionistas.
- **Infra final:** borrados `api/db.py`, `core/snapshot_writer.py`, `core/mongo.py`,
  `core/mongo_monitor.py`, `partner_api/db.py` + 10 scripts Mongo + skill index-health +
  2 tests Mongo-mock. `api/deps.py` / `partner_api/{settings,main,pg,store}` limpiados.

## ⏳ LO QUE FALTA (NO es código — operación + docs)

1. **Terminar el cluster Atlas (M10)** desde la UI (Clusters → ACAQuant → Terminate).
   Ya está PAUSADO (no se paga compute); terminarlo corta el costo del todo. Mongo
   está vacío → no se pierde nada.
2. **Sacar del `.env` / systemd del Droplet:** `MONGO_URI`, `ATLAS_*`, `PARTNER_MONGO_URI`.
   (El código ya no las lee; quedan como ruido/secreto colgado.)
3. ✅ **Barrido de DOCS — HECHO** (2026-06-29): CLAUDE.md raíz + subdir, ARQUITECTURA,
   SQL.md y los docs de dominio actualizados a SQL; docs de tracking borrados; vault +
   SISTEMA + HERRAMIENTAS regenerados.
4. **Deuda de tests (opcional):** se borraron `test_argentina_datos` y `test_cotizaciones_tier2`
   (mockeaban Mongo). Si se quiere recuperar cobertura, reescribir con mocks SQL.
5. ✅ **Limpieza de scripts/jobs — HECHA** (2026-06-29): borrados los comparadores
   `compare_*_sql_vs_mongo`, `atlas_health`, `uso_mongo_codigo`, `estado_sql`, ~10 diag
   one-shot + `jobs/aunesa_client.py` (muerto); `informe_salud` sin las falsas alarmas
   de `aum`/`sync_postgres`.

## 🔧 Verificación
```bash
grep -rE "from core.mongo|import pymongo|MongoClient" --include="*.py" .   # debe dar 0
.venv/bin/python -c "from api.main import app; print(len(app.routes))"     # 256
.venv/bin/python -m pytest -q                                             # 238 passed
```
