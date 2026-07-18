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

## COMMIT 2 — Guardrails de datos (invariantes post-cierre) — EN CURSO

**Qué es:** un job post-cierre (`jobs/guardrails.py`) con un registry de
**invariantes de sanidad** — funciones PURAS y testeables que reciben datos ya
leídos y devuelven `{ok, check_id, severidad, mensaje, valor_medido, umbral}`.

**Calibración obligatoria (REGLA #2):** nace en modo `--report` (default): NO
alerta — imprime qué violaría con el VALOR REAL medido. El user lo corre varios
días y con esos números CALIBRA los umbrales (en `config.py`, editables — nunca
hardcodeados a ojo; `None` = sin calibrar = el check no alerta). Recién con
`--alert` manda Telegram (metadata only, jamás datos de clientes) con cooldown
reutilizando `manager.watchdog_alertas` keyeado `guardrail:<check_id>`.

**Set inicial propuesto** (sobre datos que EXISTEN, lecturas scopeadas):
AuM total día-contra-día · saltos de precio de cierre por bono · completitud de
curvas en el cierre · sanidad básica (precios ≤0 / nulls obligatorios).

---

## COMMIT 3 — Golden tests del pipeline crítico — PENDIENTE

**Qué es:** characterization tests con fixtures ANONIMIZADOS commiteados:
ingesta/normalización de `operaciones_informes.ingestar_filas_sql`,
categorización de `negocio_movimientos`, y clasificación del AuM
(`_aum_filters.is_excluded` + valuación por CARTERA — las "fórmulas no
inferibles"). Todo unit (SQL/red mockeados). REGLA DE ORO: si un golden revela
un bug real, NO se tapa — se marca y se decide con el user.

---

## Registro (con fecha)

- **2026-07-18 — Commit 1 (telemetría) construido y verde** (319 rutas, 6 tests
  nuevos, ruff/typecheck OK). Borrado `MAR_14_JULIO_00_28_AM.md` (temporal,
  auto-destruible, ya cumplió). Commits 2 y 3 a continuación.
