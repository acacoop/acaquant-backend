# Observabilidad y robustez de datos — programa del 2026-07-18

> **DOC VIVO** de las 3 features de observabilidad/robustez pedidas el 2026-07-18
> (3 commits separados, en orden). Cada avance se asienta acá en el mismo commit.
> Reemplaza como "documento de sesión" al viejo `MAR_14_JULIO_00_28_AM.md`
> (borrado en esta tanda — era temporal y auto-destruible por diseño).

Las tres piezas atacan la misma pregunta desde tres lados: **¿podemos confiar en
lo que la plataforma muestra y saber cómo se usa?**

1. **Telemetría de uso** — saber QUIÉN usa QUÉ (decidir producto con datos).
2. **Guardrails de datos** — detectar un número PODRIDO antes de que alguien
   decida con él (vigila que el número esté bien, no solo que el motor esté
   vivo).
3. **Golden tests** — red de regresión sobre el camino que produce los números
   (ingesta/normalización/AuM), hoy sin cobertura.

---

## COMMIT 1 — Telemetría de uso por módulo ❌ DECOMISADA (2026-08-04)

> **Reemplazada por la telemetría de LATENCIA por endpoint** (pedido del user:
> la tab USO nunca se usó; lo operativamente útil es saber QUÉ endpoint está
> lento y su tendencia). Piezas nuevas: `manager.latencia_endpoints` (schema),
> `api/telemetria.py` (reescrito: acumula n/total_ms/max_ms/lentas/errores por
> endpoint normalizado × hora, mismo patrón de flush best-effort), middleware
> en `api/main.py` (mide `perf_counter` alrededor de cada request),
> `api/services/latencia_endpoints.py` y `GET /api/manager/latencia`.
> `manager.uso_modulos` se DROPea vía apply_schema. Lo que sigue abajo queda
> como registro histórico del diseño original.

## (histórico) Telemetría de uso por módulo — HECHO 2026-07-18, decomisada

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
  mide), `/api/health`, `/api/me`, paths sin módulo mapeado, y
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
reporta el valor real pero JAMÁS marca violación. **El user corre
`python -m jobs.guardrails` varios días, mira los valores medidos y fija los
umbrales con esos números.** El resultado queda en el log del job y como stat
`violaciones` en `manager.job_runs` (visible en Manager → jobs). *(2026-07-25:
se eliminó la pata de alertas Telegram — decomiso total de Telegram.)*

**Piezas:** `jobs/guardrails.py` · umbrales en `config.py` · cron en
`deploy/crontab.txt` · SISTEMA.md regenerado · `tests/unit/test_guardrails.py`
(7 tests de los checks puros, incl. la semántica None-no-viola).

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

- **2026-08-03 — Auditoría de observabilidad de jobs** (`scripts/audit_jobs_observabilidad.py`,
  estática, sin DB ni deps; corre en CI como informativa). Nace del caso
  `jobs.economic_calendar`: falló desde el día 1 sin dejar rastro (no usaba
  JobRunLogger) y el Diagnóstico lo "vigilaba" contra una colección Mongo
  decomisada. Cruza crontab × JobRunLogger × `diagnostico_registry`.
  Hallazgos corregidos en el mismo commit:
  - **8 piezas apuntaban a Mongo** (`db`/`coll` sin `tabla`) → daban `sin_datos`
    permanente. Re-apuntadas a SQL o a `run_tipo`: market_quotes, market_anchors,
    motor_ordenes, motor_rofex (trades), bcra, fair_value, pnl_totales_precompute,
    consolidado_cuentas.
  - **4 piezas ignoraban el JobRunLogger que el job ya emitía** → el árbol se caía
    al frescor de la tabla y un run en ERROR con datos viejos se veía sano. Ojo con
    `jobs.portafolio_backfill`, que registra con `tipo="aum"` (legado).
  - **`diagnostico.py::_leer_frescura` ignoraba `ts_kind`/`assume` en el path SQL**
    (hardcodeaba UTC) → ahora usa `_parse_ts`. Sin eso, el tape (`mercado.timesales`,
    `ts` naive en ART) se leía 3h corrido y se veía crítico siempre.
  - **Pendiente [A]: 16 crons sin JobRunLogger** → si fallan, silencio total.
    Correr el script para la lista actualizada.

- **2026-08-03 — Tanda [A]: rastro para los 5 jobs diarios ciegos.** Criterio: un job
  de alta frecuencia (`market_quotes`, cada minuto, umbral 10') ya queda cubierto por
  la frescura de su tabla; uno **diario con umbral de 3 días** puede fallar el lunes y
  descubrirse el jueves. Se le puso `JobRunLogger` + `run_tipo` a `jobs.fair_value`,
  `jobs.cierre_canje`, `jobs.forwards_zscore`, `jobs.precios_acciones_daily` y
  `engines.dolar_mep` (que además se tragaba TODA excepción en un `except: print`).
  Convención: los modos manuales (`--dry`, `--ticker`, `--backfill`) NO registran, para
  no ensuciar `manager.job_runs` — mismo criterio que `portafolio_backfill`.
  Fallas parciales (una curva/ticker) → `jr.error()` → status `partial`, que NO es
  alerta: queda registrado sin pintar el panel de rojo. Solo un crash da `error`.
  Quedan 10 en [A]: 6 de limpieza/infra (si no corren, no rompen nada) y 4 de alta
  frecuencia ya cubiertos por frescura de tabla.

- **2026-07-18 — Los 3 commits construidos y verdes** (344 tests totales, ruff/
  typecheck/import-chain OK; vault regenerado). Borrado `MAR_14_JULIO_00_28_AM.md`
  (temporal, auto-destruible, ya cumplió). Hallazgo del golden (`_to_float`)
  pendiente de decisión del user.
