# Observabilidad y robustez de datos — programa del 2026-07-18

> **DOC VIVO** de las 3 features de observabilidad/robustez pedidas el 2026-07-18
> (3 commits separados, en orden). Cada avance se asienta acá en el mismo commit.
> Reemplaza como "documento de sesión" al viejo `MAR_14_JULIO_00_28_AM.md`
> (borrado en esta tanda — era temporal y auto-destruible por diseño).

Las tres piezas atacan la misma pregunta desde tres lados: **¿podemos confiar en
lo que la plataforma muestra y saber cómo se usa?**

1. **Telemetría de uso** — saber QUIÉN usa QUÉ (decidir producto con datos).
2. **Guardrails de datos** — detectar un número PODRIDO antes de que alguien
   decida con él (el watchdog vigila que el motor esté vivo; esto vigila que el
   número esté bien).
3. **Golden tests** — red de regresión sobre el camino que produce los números
   (ingesta/normalización/AuM), hoy sin cobertura.

---

## COMMIT 1 — Telemetría de uso por módulo ✅ HECHO (2026-07-18)

**Qué es:** contador **usuario × módulo × hora** en `manager.uso_modulos`
(agregado, NO log por request → no crece sin control) + panel Manager →
OBSERVABILIDAD → **USO** (heatmap con rango 7/30 días).

**Diseño (decisiones clave):**
- **Hot path intocado:** el middleware (`api/main.py`) solo incrementa un dict en
  memoria (`api/telemetria.py`, lock + nanosegundos). Un thread flushea a SQL
  cada ~60s con upsert incremental (`ON CONFLICT … hits = hits + EXCLUDED.hits`).
  Best-effort: SQL caído → los contadores vuelven al buffer (techo 10k claves) y
  NINGÚN request se rompe jamás. Cada worker de uvicorn flushea lo suyo
  (incrementos aditivos — sin conflicto entre workers).
- **Módulo del path:** REUSA `api.auth.get_module_for_path` /
  `ENDPOINT_MODULE_PREFIXES` (mapa único, cero duplicación).
- **Se ignora:** emails vacíos, `service:*`, portal INVITADO (REGLA #8 — ni se
  mide), `/api/health`, `/api/me`, MCP/OAuth, paths sin módulo mapeado, y
  responses ≥400.
- **Identidad:** los headers saneados del proxy (los mismos de la rama 2a de
  `get_user_email`). Es un contador de producto, no una superficie de seguridad
  — el gate real sigue siendo el RBAC de cada endpoint.
- **Lectura:** `GET /api/manager/uso?dias=7` (gate `manager` umbrella), service
  puro `api/services/uso_modulos.py`, query scopeada por el índice de `hora`.

**Piezas:** `manager.uso_modulos` (schema) · `api/telemetria.py` · middleware en
`api/main.py` · `api/services/uso_modulos.py` · `api/routers/manager/uso.py` ·
pill USO en `manager-view.tsx` · `tests/unit/test_telemetria.py` (6 tests).

**Para activar:** `python -m scripts.apply_schema` + `systemctl restart api.service`.
El panel muestra datos a partir del primer flush (~1 min de uso real).

---

## COMMIT 2 — Guardrails de datos (invariantes post-cierre) ✅ HECHO (2026-07-18)

**Qué es:** `jobs/guardrails.py` — registry de **invariantes de sanidad**:
funciones PURAS y testeables (`check_*(datos) → [{ok, check_id, severidad,
mensaje, valor_medido, umbral}]`) + un runner que lee SQL scopeado (2 fechas
puntuales por tabla, REGLA #4) y las corre post-cierre (cron 20:45 UTC L-V,
después de snapshot_cierre 20:25).

**Set inicial (4 checks sobre datos que EXISTEN):**
1. `aum_delta` (alta) — AuM total día-contra-día (`portafolio.tenencia` aum='si';
   la valuación YA viene con el divisor por cartera del writer — no se recalcula).
2. `sanidad_cierre` (alta, ABSOLUTO sin umbral) — sin precios ≤0/null ni filas
   sin ticker en el cierre.
3. `salto_precio` (media) — |Δ%| de cierre por bono vs cierre previo.
4. `cobertura_curva` (media) — % de bonos del master con cierre, por curva
   (ONs excluidas a propósito: ilíquidas, darían falsos rojos).

**Calibración (REGLA #2, el corazón del diseño):** los umbrales viven en
`config.GUARDRAILS_UMBRALES` y NACEN en `None` = sin calibrar → el check MIDE y
reporta el valor real pero JAMÁS viola/alerta. **El user corre
`python -m jobs.guardrails` (modo report) varios días, mira los valores medidos
y fija los umbrales con esos números.** Recién entonces cambia el cron a
`--alert`. Alertas: Telegram metadata-only con cooldown 20h en
`manager.watchdog_alertas` keyeado `guardrail:<check_id>` (patrón del watchdog,
sin tabla nueva). Idempotente por el cooldown.

**Piezas:** `jobs/guardrails.py` · umbrales en `config.py` · cron en
`deploy/crontab.txt` (modo report; el switch a --alert lo hace el user al
calibrar) · SISTEMA.md regenerado · `tests/unit/test_guardrails.py` (7 tests de
los checks puros, incl. la semántica None-no-viola).

---

## COMMIT 3 — Golden tests del pipeline crítico ✅ HECHO (2026-07-18)

**Qué es:** `tests/unit/test_golden_pipeline.py` (8 tests) + fixture anonimizado
`tests/unit/fixtures/golden_operaciones.json`. Los outputs esperados se
CONGELARON corriendo las funciones REALES (characterization) — un cambio futuro
que altere el resultado rompe a propósito. Cobertura:
1. **Ingesta de operaciones** (`operaciones_informes`): `normalizar_fila`
   (headers con alias/acentos, montos es-AR, cuenta numérica→string, fechas
   DD/MM e ISO), `_aplicar_enrich` (commodity con la excepción agro-OTC del
   2026-06-03, es_cierre materializado, mep estampado, mercado/operacion/
   segmento/nivel_3 con maps inyectados) y `es_otc_excluido` (NDF/Opciones sí,
   Concurrencia no).
2. **Negocio** (`negocio_movimientos`): `_boleto_a_doc` completo (con el mep
   snapshot inmutable) + `_extract_id_cuenta`.
3. **AuM** (`_aum_filters.is_excluded`): las 5 reglas de exclusión (USDL,
   OTC/CDC, contraparte por id, contraparte por nombre, ARS de cuentas propias)
   con sets inyectados — cero SQL.
4. **Valuación por CARTERA** (`pnl._aplicar_normalizer` — la fórmula no
   inferible): ÷100 para HD/DL/ARS, ×1 para MONEDAS/FCI, (precio+1)×cant para
   futuros, fallback.

NO hizo falta refactor: los núcleos ya eran puros/inyectables (maps, mep_cache,
sets de contrapartes van por parámetro).

### ⚠ HALLAZGO de la characterization (marcado, NO tapado — decide el user)

**`_to_float("1.500")` → 1.5.** El parser trata un punto ÚNICO como decimal,
pero en el Excel es-AR histórico "1.500" casi siempre es MIL QUINIENTOS. Es una
ambigüedad intrínseca (una cantidad "1.500" ≠ un precio "1.500") y NO se tocó la
lógica de producción (regla de oro). Mitigantes: la API informes manda números
JSON (no strings) → el camino diario probablemente no lo pisa; el riesgo vive en
cargas históricas por Excel. **Decisión pendiente del user:** (a) dejarlo así
(documentado + golden congelado), o (b) cambiar la heurística (ej. un punto con
exactamente 3 dígitos detrás y sin coma = miles) sabiendo que rompe el golden y
obliga a revisar lo ya ingestado con ese patrón.

---

## Qué corre el user en el Droplet (checklist de activación)

```
git pull
python -m scripts.apply_schema        # crea manager.uso_modulos
systemctl restart api.service          # activa el middleware de telemetría
python -m jobs.guardrails              # 1ª corrida del report de calibración
```
- El panel **Manager → OBSERVABILIDAD → USO** muestra datos tras ~1 min de uso.
- **Calibración de guardrails:** correr/leer el report unos días (el cron 20:45
  ya lo corre solo y el output queda en `logs/guardrails.log`); con los valores
  medidos, fijar los umbrales en `config.GUARDRAILS_UMBRALES` y cambiar el cron
  a `--alert`.
- **Decidir el hallazgo `_to_float("1.500")`** (ver commit 3).

## Registro (con fecha)

- **2026-07-18 — Los 3 commits construidos y verdes** (344 tests totales, ruff/
  typecheck/import-chain OK; vault regenerado). Borrado `MAR_14_JULIO_00_28_AM.md`
  (temporal, auto-destruible, ya cumplió). Hallazgo del golden (`_to_float`)
  pendiente de decisión del user.
