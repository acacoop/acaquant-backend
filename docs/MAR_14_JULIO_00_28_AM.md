# Sesión del martes 14 de julio, 00:28 AM — hardening integral

> ## ⚠️ ESTE DOCUMENTO SE AUTODESTRUYE
>
> **Es un doc TEMPORAL de red de seguridad**, no documentación del sistema. Existe solo para
> poder diagnosticar/revertir si algo se rompe después del deploy del 14/07/2026.
>
> **Si estás leyendo esto y el sistema viene funcionando bien (motores escribiendo, API arriba,
> AuM/PnL con números correctos, el proveedor externo recibiendo datos, ningún job en rojo en el
> triage) → BORRÁ ESTE ARCHIVO.** No lo dejes acumulando: viola la REGLA #5 (minimalismo en
> `docs/`, se borra lo cumplido). La arquitectura real vive en `docs/ARQUITECTURA.md`; lo que
> haya que conservar de acá ya está en el mensaje de commit `edcf092`.
>
> Criterio para borrarlo: **una rueda completa (L-V 13-20 UTC) sin incidentes** post-deploy.

---

## Qué pasó en esta sesión

Dos cosas: (1) un fix de UI en la HOME, y (2) una **auditoría completa del código** (seguridad,
eficiencia, escalabilidad) hecha con 3 agentes en paralelo, seguida de la **aplicación de todos
los hallazgos** con un workflow de 12 agentes.

**Restricción que gobernó TODO el trabajo** (pedido explícito del user, en mayúsculas):
> "NO TIENE QUE CAMBIAR NADA DE LA APP Y COMO FUNCIONA, NO QUIERO CAMBIOS DE VISTAS, NO QUIERO QUE
> HAYA CAMBIOS DE FUNCIONES ETC, ESTO ES PARA MEJORAR SER MAS EFICIENTES SEGURIDAD ETC PERO NO
> CAMBIAR COMO FUNCIONAN LAS COSAS EN SI"

Es decir: **mismo input → mismo output**. Si algo cambió de comportamiento visible, es un BUG de
esta sesión, no una feature. Eso es exactamente lo que hay que buscar si algo falla.

---

## Commits (puntos de rollback)

### TradingAV
| Commit | Qué |
|---|---|
| `edcf092` | **Hardening integral** — todo lo de esta sesión (68 archivos, +1.185 / −1.387 líneas) |
| `18cf3fe` | ← **estado ANTERIOR**. Revertir a acá = deshacer todo lo del backend |

### acaquant-web
| Commit | Qué |
|---|---|
| `e200194` | `/api/ia` entra al matcher del proxy (seguridad) |
| `27f28df` | CALENDARIO como chip interno de la watchlist (UI) |
| `662575e` | ← **estado ANTERIOR** (el commit de la otra PC que trajo el calendario) |

**Rollback total del backend:** `git revert edcf092` (NO `reset --hard`: ya está pusheado).
**Rollback de una sola cosa:** los cambios están agrupados por archivo/dominio, ver abajo qué tocó
cada lane — se puede revertir un archivo suelto con `git checkout 18cf3fe -- <archivo>`.

---

## 1. Fix de UI (acaquant-web `27f28df`)

**Problema:** el commit `662575e` (hecho en otra PC) agregó el CALENDARIO como un wrapper *externo*
que apilaba tabs WATCHLIST/CALENDARIO arriba del panel — y adentro el panel seguía mostrando su
propio título "WATCHLIST". Doble header robándole altura a la tabla.

**Fix:** CALENDARIO pasó a ser un chip más dentro de la fila de filtros que el panel ya tenía
(GENERAL · FUTUROS ROFEX · CALENDARIO), un solo header. `CalendarioPanel` se renderiza sin borde
ni título propio. El default sigue siendo FUTUROS ROFEX (CALENDARIO nunca arranca seleccionado).

**Archivos:** `src/components/home-view.tsx`, `watchlist-panel.tsx`, `calendario-panel.tsx`.
**Si algo falla acá:** es puramente visual, sin riesgo de datos.

---

## 2. Auditoría (3 agentes en paralelo, read-only)

- **Seguridad** → 0 críticos, 0 altos. 3 medios, 6 bajos.
- **Eficiencia** → `scripts/perf_scan` salió limpio; los hallazgos son de caza manual.
- **Escalabilidad** → el hallazgo estructural: `statement_timeout=15000` en ambos pools
  (`core/postgres.py:104,127`) vs queries que crecen con el historial.

---

## 3. Aplicación de los fixes (TradingAV `edcf092`)

Workflow de **10 lanes en paralelo + 1 refactor + 1 verificación**. Cada lane era dueña exclusiva de
un set de archivos disjunto (para que no se pisen), con contrato de cero cambios de comportamiento.

### 3.1 SEGURIDAD

| Fix | Archivo | Detalle |
|---|---|---|
| **CF Access fail-closed** | `api/main.py:66-105` | Si `ENV=prod` y faltan `CF_ACCESS_TEAM`/`CF_ACCESS_AUD`, la API **no arranca** (antes: `logger.error` y seguía, con la identidad cayendo al header forwardeado **spoofeable** → el RBAC no protegía nada). |
| **Diag previo** | `scripts/diag_auth_postura.py` (NUEVO) | Read-only, imprime qué env vars están seteadas (sin revelar valores) y si la API va a arrancar. **Se corrió antes del deploy: dio ✅.** |
| **`/api/ia` al matcher** | `acaquant-web/src/proxy.ts:177` | Era el único route handler inline fuera de `config.matcher` → el proxy no sanitizaba el header `cf-access-authenticated-user-email` entrante, y el handler lo reenviaba al backend con el service token. Identidad spoofeable en el módulo IA. |
| **`compare_digest`** | `api/routers/ingest.py:28` | El token de ingest se comparaba con `!=` (timing side-channel). |
| **Dummy-hash** | `partner_api/odata.py:70` | Enumeración de usuarios por timing en el Basic Auth (si el user no existía, no corría PBKDF2 → respuesta más rápida). `/v1/token` ya lo mitigaba; OData no. |
| **`aud`/`iss` en JWT** | `partner_api/security.py` | Validación **tolerante** (acepta tokens viejos sin `aud`/`iss`) para no invalidar los tokens que el proveedor externo tiene en vuelo. |

### 3.2 CORRECTITUD (bugs reales, no perf)

| Bug | Archivo | Detalle |
|---|---|---|
| **Export silencioso** ⚠️ el más grave | `jobs/partner_export.py` | El `try/except` tragaba el fallo de Postgres e imprimía `"dual-write SQL falló (Mongo OK)"` — pero **Mongo ya no existe**: PG es el único destino. Si PG fallaba, el proveedor externo se quedaba con datos viejos, el job terminaba en ✅ y nadie se enteraba (no usaba `JobRunLogger`). Ahora falla ruidoso + queda en `manager.job_runs` → lo ve el triage. |
| **Guard muerto** | `engines/valores.py:351` | `if last_price is None` nunca disparaba (se inicializa en `0.0`). Un ticker cuyo REST de arranque en frío falló persistía `last_price=0.0` **pisando el cierre previo**. |
| **Motor zombie** | `core/websocket.py` | Tras agotar los 6 reintentos de reconexión (~3.5 min), el motor quedaba `active` para systemd **pero sin datos**, hasta el restart del cron **del día siguiente**. Ahora hace `os._exit(1)` y systemd lo levanta en 10s (verificado: los 15 units tienen `Restart=always`). |

### 3.3 EFICIENCIA (mismo dato, menos round-trips / CPU / escrituras)

- `engines/motor_cedears.py` — **dirty-check por huella** + resync forzado cada 45s. Antes reescribía
  el universo COMPLETO cada 1s (WAL + churn de índices, 7hs/día) incluso para tickers sin un solo tick.
- `engines/curvas.py` — invalida el cache **solo si** el MEP/CER/A3500 efectivamente cambió, y solo
  las curvas que dependen de ese valor. Antes: `ultimo_calculado.clear()` incondicional cada 60s →
  recalculaba xirr/duration/convexity del universo entero para reescribir valores idénticos.
- `engines/options.py` — buffer + flush cada 1s (antes: 1 INSERT + commit **por trade**).
- `engines/valores.py` — deja de armar el payload que el throttle iba a descartar (4 de cada 5).
- `engines/breakevens.py` — 4 queries a la misma tabla → 1 (`core/market_snapshot.cols_map`); muere el
  `ThreadPoolExecutor(4)` que se creaba **en cada ciclo** de 30s.
- `api/services/argy.py` + `core/dolar_sql.py` — el watchlist del HOME (endpoint más golpeado) hacía
  **3 scans** de la historia de `valuaciones.dolar` (MEP/CCL/canje) → **1**.
- `api/services/tenencia_hd.py` + `valuaciones.py` — **N+1 de MEP-por-fecha** (1 query por cada día de
  la serie) → 1 query + dict, **replicando la semántica de arrastre** (último MEP ≤ fecha).
- `jobs/fair_value.py` — N+1 de residuos históricos (1 SELECT por bono) → 1 query por curva.
- `jobs/pnl_totales_precompute.py` + `api/services/pnl_sql.py` — carga **batcheada por cuenta**. Antes:
  UNA query con todos los boletos de toda la historia → iba a cruzar el `statement_timeout` de 15s y
  dejar `pnl_totales_cache` **congelada sirviendo datos viejos en silencio**.
- `api/routers/manager/clientes.py` — 13 `SELECT DISTINCT` → 1 `array_agg`; bulks de hasta 20.000 filas
  fila-a-fila → `executemany`.
- `api/cache.py` — `@cached` normaliza args posicionales con `signature.bind()` (el footgun que causó
  **2 incidentes documentados**: commits `4e4428a`, `746d8b6`); LRU 512 → 2048.
- `engines/{futuros_dlr,motor_agro,motor_agro_opciones}.py` — re-discovery del padrón ROFEX de 5min →
  30min (solo alimentaba un log; 36 → 6 descargas/hora del padrón completo).

### 3.4 RETENCIÓN (lo que el schema prometía y ningún cron ejecutaba)

`jobs/cleanup_retencion.py` (NUEVO) — batcheado + throttle + `--dry-run` (REGLA #4). TTLs **leídos del
schema**, no inventados; donde el schema no decía nada → 90d, salvo tablas de auditoría → 365d.

| Tabla | TTL | Dry-run del 14/07 |
|---|---|---|
| `manager.job_runs` | 60d | borraría **568** filas (de ~4.640) |
| `manager.health_reports` | 45d | 0 |
| `ia.trazas` | 90d | 0 |
| `manager.watchdog_alertas` | 90d | 0 |
| `portafolio.backfill_log` | 90d | 0 (pero **~100.800 filas** → crece ~1.100/día, es la más gorda) |
| `operaciones.ordenes_audit` | 365d | 0 |
| `manager.role_audit` | 365d | 0 |

`deploy/logrotate.conf` (NUEVO) — los logs de `run_job.sh` no rotaban (`market_quotes` corre **cada
minuto** = ~700 escrituras/día al log). Disco lleno tumba todo a la vez.

### 3.5 LIMPIEZA (~1.200 líneas de código muerto post-decomiso de Mongo)

Capa dual-run SQL/Mongo de scanner y operaciones (ambas ramas leían las MISMAS tablas SQL), stubs de
delegación de `scanner.py`/`rem.py`, ~10 funciones sin callers de `core/pg_mirror.py`,
`api/services/import_tenencia.py` (se autodeclaraba deprecado), `jobs/aum.py::procesar`, ramas FX
vacías de `market_quotes`/`market_anchors`, `pymongo` de `requirements.txt`, y docstrings que mentían
sobre Mongo. Nuevo `api/services/_sql.py`: helper `_q`/`_f` que estaba copy-pasteado en ~20 archivos.

---

## 4. Qué NO se hizo (a propósito) — pendientes reales

1. **Defaults de ventana temporal** en endpoints con `desde`/`hasta` opcionales
   (`api/routers/operaciones.py:40 listar_flujo` — además sin `LIMIT`; `operaciones_sql.py:70,207`;
   `comercial_sql.py:244`; `portfolio_sql.py:145,198`). **Es el pendiente más importante**: hoy escanean
   TODO el historial, y el día que crucen los 15s de `statement_timeout` el endpoint **muere
   permanentemente**. No se hizo porque **cambiaría qué datos ve el usuario** (violaría la restricción
   de la sesión). El patrón correcto ya existe en el repo: `_SERIE_VENTANA_DIAS = 550` en
   `operaciones_sql.py:31`. **Decisión pendiente del user.**
2. **Rate-limit del partner** (`partner_api/ratelimit.py:13`) — keyeado por `cf-connecting-ip`, que es un
   header **spoofeable**: rotándolo se anula el anti-fuerza-bruta del login del proveedor. El fix depende
   de la config de **nginx** (si no valida que el request venga de rangos de Cloudflare), que no es
   visible desde el repo.
3. **`api/routers/{ordenes,risk,operativa}.py`** conservan su capa dual-run (`_read_sql` + flag
   `ORDENES_SQL`). NO se limpiaron porque **cuál rama corre depende del valor de `ORDENES_SQL` en el unit
   del Droplet**, no verificable desde el repo (REGLA #2). Regla de oro que se les dio a los agentes: si no
   sabés con certeza cuál rama corre en prod, no borres nada.
4. **`.env` línea 33 malformada** — el diag destapó `python-dotenv could not parse statement starting at
   line 33`. NO afecta a `api.service` (toma las vars del unit de systemd), pero **sí a jobs/scripts/crons**
   que leen el `.env`: si ahí vive una variable que un job necesita, ese job la ve como faltante.
5. **`hourly_stats` en `engines/valores.py`** — un agente reportó que también es cómputo muerto (se calcula
   por trade, nadie lo lee), igual que el VPIN que sí se borró. Sin confirmar.

---

## 5. Verificación que se corrió antes de pushear

- `ruff check .` → limpio
- `python -c "import api.main"` → OK
- `pytest -ra` → **304 passed**
- `npx tsc --noEmit` (acaquant-web) → limpio
- `python -m scripts.gen_sistema --check` → OK
- `python -m scripts.diag_auth_postura` (en el Droplet) → ✅

**3 tests cambiaron, todos legítimos:** se borraron los del selector `motor()` (que dejó de existir), se
registró el cron nuevo en `test_diagnostico_registry`, y se corrigió `test_copiloto::test_vista_home_registrada`
— **que ya venía fallando en `main` desde antes de esta sesión** (el chip "Narrame el briefing" se había
eliminado en el commit `22421de` y el test nunca se actualizó).

---

## 6. Si algo se rompe — dónde mirar primero

| Síntoma | Sospechoso #1 |
|---|---|
| La API no arranca | `api/main.py` fail-closed. Correr `python -m scripts.diag_auth_postura`. |
| Precios/snapshot de CEDEARs stale | dirty-check de `engines/motor_cedears.py` (¿el resync de 45s corre?) |
| Números de renta fija (TIR/duration) raros | invalidación de cache de `engines/curvas.py` — invalida de menos → valores viejos |
| Faltan trades de opciones | buffer + flush de `engines/options.py` (¿flushea en el shutdown?) |
| AuM / consolidado con números distintos | precarga del MEP en `tenencia_hd.py` / `valuaciones.py` — **la semántica de arrastre** (último MEP ≤ fecha) es lo delicado |
| PnL TOTALES desactualizado | batcheo por cuenta de `pnl_sql.py` |
| Motor que se reinicia solo | `core/websocket.py` `os._exit(1)` — es **esperado** si el broker cortó; systemd lo levanta |
| Un ticker con precio 0 | guard de `engines/valores.py` (ahora omite columnas de precio en vez de la fila entera) |
| El proveedor externo sin datos | `jobs/partner_export.py` ahora **falla ruidoso** — mirá `manager.job_runs` |

---

_Doc generado el 14/07/2026 00:28. **Borrame cuando el sistema haya pasado una rueda completa sin incidentes.**_
