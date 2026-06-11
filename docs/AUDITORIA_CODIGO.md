# AUDITORÍA DE CÓDIGO — TradingAV + acaquant-web

> **Fecha**: 2026-06-11 · **Alcance**: backend completo (api/, core/, engines/, jobs/, quant/)
> y frontend (src/). Auditoría point-in-time: según REGLA #5, este doc **se borra o
> consolida en ARQUITECTURA.md** a medida que los ítems se resuelvan.
>
> **Método**: 3 pasadas de lectura profunda + verificación manual de los hallazgos
> graves + escaneos mecánicos (`perf_scan --strict`, ranking de tamaños, grep de
> duplicados y código muerto). Cada hallazgo marca su nivel de confianza:
> **[V]** = verificado a mano leyendo el código · **[R]** = reportado por review
> profundo (alta confianza, no re-verificado línea por línea).

---

## VEREDICTO EJECUTIVO

**El código está mejor que el promedio de un proyecto asistido por IA, y mucho
mejor que hace dos meses.** Las bases son sólidas: separación de capas declarada
y mayormente respetada, `perf_scan` casi limpio (2 findings en ~50k líneas),
TypeScript strict sin `any`, singletons Mongo correctos, reconexión WS con
backoff, jobs con lock+timeout. Un dev pro NO diría "esto es cualquier cosa".

**Lo que SÍ diría un dev pro**: *"acá cada feature es una isla"*. El patrón
dominante de deuda no es código malo — es **duplicación por generación**: cada
motor, cada vista y cada job reinventa helpers que ya existen en el repo
(formatters, fechas, polling, XIRR, MEP). Eso es la firma típica del código
generado por IA feature-por-feature: correcto en lo local, inconsistente en lo
global. El costo no es CPU hoy — es que **cambiar algo requiere tocarlo en N
lugares**, que es exactamente lo que pediste evitar.

**Riesgo real concentrado en 4 puntos** (ver ALTA abajo): una copia degradada
de XIRR calculando TEAs en producción, lógica de negocio en un router de 1.737
líneas, un fallback silencioso en el motor de PnL, y threads de motores que
pueden morir sin aviso.

---

## CÓMO SE CONECTA TODO (mapa de referencia del doc)

```
pyRofex WS ──> engines/ (9 motores, systemd L-V 13:20-20:05 UTC)
                  │ bulk_write 1s
                  v
            MongoDB Atlas M10  <── jobs/ (cron: rollups, cierres, stats, sync)
                  │ get_mongo_client_read (SECONDARY_PREFERRED)
                  v
            api/services (lógica pura, @cached TTL) <── api/mcp (41 tools)
                  │
            api/routers (HTTP + RBAC require_module)
                  │ Bearer + CF Access
                  v
            acaquant-web (Next 16, Vercel) — route handlers proxy → usePoll en vistas
```

---

## HALLAZGOS — SEVERIDAD ALTA

### A1 · [V] XIRR degradado calculando TEAs en producción
`engines/curvas.py:42-58` tiene su propia copia de `xirr()` (scipy newton, 3
semillas, sin bisección de respaldo, techo de tasa 5.000%) mientras
`quant/xirr.py` tiene la versión robusta (Newton con damping + bisección
garantizada + rango para inflación argentina). El motor de curvas — que escribe
la TEA de cada bono cada 5 segundos — usa la copia débil: en casos límite
(flujos concentrados, tasas extremas) puede devolver `None` o converger mal
donde la versión buena resuelve. Además `macaulay_duration` y `convexity`
están duplicadas ahí mismo (`:60-92`), y el comentario de la sección dice
"idénticos a backfill_curvas" — un script que **ya no existe** (comentario
fósil).
**Fix**: borrar las copias del motor e importar de `quant/` (la TIR de tu
negocio debe tener UNA implementación, la testeada). Esfuerzo: ~2h + comparar
output en paralelo un día de rueda antes de confiar.

### A2 · [V tamaño / R detalle] Lógica de negocio en el router `operaciones.py`
1.737 líneas — el archivo más grande del backend es un **router**, cuando la
regla del repo es "routers = thin HTTP plumbing". Construcción de mapas de
contrapartes, conversión por MEP histórico, helpers de agregación (`_ops_match`,
`_arancel_match`, `_importe_convertido`) viven en la capa HTTP, algunos
duplicados con `services/comercial.py`. Consecuencias: no se puede testear sin
levantar FastAPI, y el MCP/futuros consumidores no pueden reusar esa lógica.
**Fix**: mover los helpers `_*` a `api/services/operaciones_view.py` y dejar el
router como dispatcher. Esfuerzo: 1-2 días, mecánico, riesgo bajo (mover, no
reescribir). Es EL refactor estructural del backend.

### A3 · [R] Fallback silencioso a N+1 en el motor de PnL
`api/services/pnl.py`: el camino bulk (`_load_pnl_bulk_deps`, 6 queries para
todas las cuentas) salvó al cron de totales, pero si la precarga falla o un
caller olvida pasar los maps, **degrada en silencio al camino por-cuenta**
(N+1, ~100× más lento) sin warning. El cron "funciona" pero tarda una
eternidad y castiga al M10 sin que nadie se entere.
**Fix**: si las deps bulk vienen vacías → log WARNING + fail-fast (que el
watchdog lo vea), nunca degradar callado. Esfuerzo: ~2h.

### A4 · [V parcial / R] Threads de motores pueden morir en silencio
`engines/valores.py:79-81` (y patrón similar en otros motores y
`core/snapshot_writer.py`): threads daemon (`_worker_loop`, `_flush_loop`,
`_snapshot_loop`) sin wrapper de excepción global. Si uno crashea fuera del
try interno, el hilo muere, el proceso systemd sigue "active" y el motor queda
**zombie**: vivo para systemd, muerto para el mercado. Es el mismo modo de
falla del incidente de los motores overnight.
**Fix**: wrapper común `_thread_seguro(target)` que loguea `logger.exception`
y termina el proceso (systemd lo reinicia) o alerta por Telegram. Esfuerzo:
~3h. Se resuelve gratis con M1 (motor base).

### A5 · [V] La plata no tiene tests + CI en rojo
- `api/services/valuaciones.py` (1.579 líneas), `pnl.py` (1.123) y
  `operativa_mep.py` (881) — los services que calculan **plata de clientes** —
  tienen **cero tests**. La matemática de bonos sí está testeada
  (xirr/curvas/breakevens); la de carteras no.
- Además **el CI de main está rojo**: 7 tests rotos por features recientes
  (registro del Diagnóstico sin 4 crons nuevos, `cotizaciones_tier2` con
  KeyError, paginación MAE). Un CI rojo permanente = nadie mira el CI = los
  tests dejan de proteger.
**Fix**: (1) arreglar los 7 tests YA (el del Diagnóstico es agregar 4 crons al
registro); (2) tests de caracterización para `_pesificar`, cost-basis y la
valuación por tipo de asset (los casos del MOTOR_VALUACIONES.md). Esfuerzo:
1 día los rotos + 2-3 días caracterización.

---

## HALLAZGOS — SEVERIDAD MEDIA

### M1 · [R] ~1.200-1.400 líneas duplicadas entre los 9 motores
`_arranque_en_frio`, `_snapshot_loop`, `_flush_loop` se repiten con variaciones
mínimas en los 9 engines (~4.400 líneas totales, ~30-40% es el mismo esqueleto).
Un bug en el arranque en frío se arregla 9 veces. `core/snapshot_writer.py` ya
es el template correcto (dirty-check MD5 + bulk_write + thread).
**Fix**: `core/motor_base.py` con el esqueleto común; cada motor solo define
`_procesar_tick()` y el shape de su doc. Hacerlo **de a un motor por vez**
empezando por el más simple (caucion), comparando output. Esfuerzo: 3-5 días
total, en cuotas. Bonus: A4 queda resuelto en un solo lugar.

### M2 · [V] MEP histórico duplicado
`api/services/_mep.py:19::get_mep_for_date` y
`api/services/valuaciones.py:51::_get_mep_for_date` — misma query, dos copias.
`pnl.py` usa una, `valuaciones.py` la otra: un fix de redondeo en una deja a la
otra desfasada **en cálculo de plata**. **Fix**: una sola en `_mep.py`. ~1h.

### M3 · [V] `quant/` viola su pureza (regla de capas del propio repo)
`quant/black_scholes.py:4` importa `core.mongo` a nivel módulo;
`quant/pivot_points.py` lo importa adentro de 4 funciones (139, 210, 279, 334).
"Cálculo puro" que lee la base no es puro: no se puede testear sin Atlas y
rompe la dirección de dependencias declarada en CLAUDE.md.
**Fix**: las funciones que leen Mongo se mueven a `api/services/` o `core/`;
`quant/` queda solo matemática. ~3h.

### M4 · [R] Cache sin protección anti-estampida
`api/cache.py`: LRU + TTL + lock están bien, pero cuando un TTL expira, N
requests simultáneos ven el cache vacío y **todos** ejecutan la query a la vez
(N agregaciones idénticas al M10 en el mismo segundo). Con pocos usuarios no
duele; es deuda que explota con el crecimiento.
**Fix**: lock por clave durante el refresh (uno computa, el resto espera el
resultado). ~3h en el decorador, beneficia a TODOS los endpoints a la vez.

### M5 · [R] Helpers de fechas hábiles repartidos en ≥8 jobs
`fecha_t2`, `_ddmmyyyy`, "próximo hábil", etc. — ~15 funciones de calendario
desperdigadas. Si cambia un feriado o la regla T+2, hay que cazarlas todas.
**Fix**: `core/fechas.py` único + tests (ya existe `test_dias_habiles`). ~4h.

### M6 · [V puntual / R] `create_index` en cada arranque de motores/jobs
Ej. `engines/motor_cedears.py:126` y varios `main()` de jobs crean índices al
iniciar: llamada sincrónica a Atlas en cada arranque (los motores arrancan
TODOS a las 13:20). Es idempotente pero suma latencia justo en la apertura y
ensucia la responsabilidad (el runtime no debería definir esquema).
**Fix**: `scripts/setup_indices.py` único, se corre en deploy. ~3h.

### M7 · [V] Frontend: 5 route handlers copiados + el genérico sin usar
`src/app/api/{operaciones,operar,manager,risk,ordenes,operativa}/...` repiten
el mismo proxy (~200 líneas) con variaciones, mientras
`src/lib/proxy-backend.ts::proxyToBackend` — escrito exactamente para esto —
tiene **cero imports** (solo lo menciona un comentario). **Fix**: migrar los
handlers al genérico o borrarlo. ~3h.

### M8 · [R] Frontend: formatters de números redefinidos por vista
`fmtN/fmtAum/fmtUsd/fmtCompact/fmtPct/...` reimplementados en 7+ vistas
(comercial-operaciones, aum-view, contrapartes, derivados-sinteticos, etc.)
+ ~79 `toLocaleString("es-AR")` inline, conviviendo con `lib/fmt-money.ts` que
es la fuente correcta. Cambiar el formato de un número = tocar 8 archivos.
**Fix**: `lib/fmt.ts` (num/pct/signado/compacto) y migración mecánica. ~1 día.

### M9 · [V tamaño / R detalle] Frontend: las 5 vistas gigantes
`manager-view.tsx` **3.884**, `aum-view` 1.517, `comercial-operaciones-view`
1.475, `operar-dashboard-view` 1.463, `valuaciones-view` 1.418. Además son las
que NO usan `usePoll` (polling manual con `setInterval` propio) ni
`usePersistedState` consistente (localStorage directo en operar-dashboard).
**Fix**: partir por tabs/paneles (manager primero: cada tab a su archivo con
`next/dynamic`), migrando el polling a `usePoll` al pasar. Esfuerzo: 2-4 días
manager, 1-2 días c/u las demás. Hacerlo al tocar cada vista, no big-bang.

### M10 · [R] Manejo de errores sin estándar
Backend: algunos endpoints tiran `HTTPException` (corta con 4xx/5xx), otros
devuelven `{"error": ...}` con HTTP 200 (fair_value, ordenes). El frontend
tiene que adivinar. Handlers de Next: algunos devuelven 502 propio, otros
propagan el status.
**Fix**: documentar el criterio en `api/CLAUDE.md` (sugerido: HTTPException =
input inválido/infra; dict con `error` = resultado de negocio "vacío pero
válido") y aplicarlo al tocar cada endpoint. Sin big-bang.

---

## HALLAZGOS — SEVERIDAD BAJA

- **B1 · [V] `scripts/` es un cementerio**: 89 archivos, 44 son one-shot
  (`diag_/fix_/backfill_/seed_`) que según REGLA #5 debían borrarse al cerrar
  cada tema. Barrida de limpieza: ~2h (confirmando cada uno).
- **B2 · [V] CLAUDE.md desactualizado**: documenta `api/agent/` + `POST
  /api/chat` como "legacy en el repo" pero **fueron eliminados** (la carpeta no
  existe); dice "Next.js 15" cuando el front es **Next 16** (con `proxy.ts`,
  no middleware). La doc que miente es peor que la que falta — es el contexto
  que cargan las IAs que te asisten.
- **B3 · [V] Código muerto frontend**: `components/auto-refresh.tsx` y
  `components/breakeven-chart.tsx` sin ningún import real. Borrar.
- **B4 · [R] Defensividad redundante**: chequeos de `None` repetidos en
  `_pesificar` (valuaciones/pnl), patrón try/float repetido 5+ veces en
  `valuaciones.py` (extraer `_safe_float`), casts `as unknown as
  Record<string,unknown>` para sort dinámico en 3 tablas (tipear genérico).
- **B5 · [R] Sort/filter de tablas reimplementado en 4 componentes** →
  hook `useSortedTable()` cuando se toque alguna.
- **B6 · [V] perf_scan**: 2 findings reales — `jobs/informe_salud.py:98,167`
  `find_one` dentro de loop (N+1 chico; acotado porque corre 1×/hora, pero
  fácil de arreglar con un `$in`).

---

## ¿HAY "CÓDIGO OBVIO DE IA"?

Veredicto honesto: **no hay sopa de IA** — los comentarios explican decisiones
(no narran lo obvio), no hay abstracciones inventadas porque sí, el tipado es
serio. Un dev pro no diría "esto es cualquier cosa".

Lo que SÍ delata generación asistida, y es lo que esta auditoría ataca:
1. **Cada feature es una isla** — la duplicación A1/M1/M2/M5/M7/M8 es el rastro
   de generar feature-por-feature sin pasada de consolidación.
2. **Defensividad por las dudas** (B4) — triple chequeo de `None` porque el
   generador no confía en sus propias precondiciones.
3. **Doc que quedó atrás del código** (B2) — el código evolucionó más rápido
   que su documentación-contexto.

## LO QUE ESTÁ BIEN (no tocar, y usar de template)

- `core/mongo.py` (singletons, pool, compresión, SECONDARY_PREFERRED),
  `core/websocket.py` (backoff, purga, throttle Telegram), `core/snapshot_writer.py`
  (dirty-check + bulk — **el template para M1**), `jobs/sync_postgres.py`
  (batcheo+throttle — el template de REGLA #4), `jobs/aum.py` (paralelismo
  thread-safe + retry).
- `api/auth.py` (CF Access + JWT + RBAC en capas), `api/cache.py` (LRU+TTL,
  solo falta anti-estampida), el cost-basis de `pnl.py` (sutil y correcto),
  el patrón rollup-no-escanear de operaciones.
- Frontend: `use-poll`, `use-persisted-state`, `fmt-money`, `cf-access`,
  `Panel`/`TableHelp`, tokens de tema, `let alive = true` en efectos, strict TS.
- El sistema de auto-documentación (`gen_sistema`, `gen_obsidian`, `perf_scan`,
  hooks de import) es algo que la mayoría de los equipos profesionales NO tiene.

## PLAN DE ACCIÓN SUGERIDO (orden por valor/esfuerzo)

| # | Qué | Hallazgos | Esfuerzo |
|---|-----|-----------|----------|
| 1 | Arreglar los 7 tests rotos → CI verde | A5 | 1 día |
| 2 | XIRR único (motor importa de quant/) | A1 | 2h + validación |
| 3 | MEP único + fail-fast en PnL bulk | M2, A3 | 3h |
| 4 | Threads seguros en motores | A4 | 3h |
| 5 | Quick wins de limpieza: componentes muertos, CLAUDE.md veraz, scripts/ barrida, informe_salud N+1 | B1-B3, B6 | ½ día |
| 6 | Anti-estampida en `@cached` | M4 | 3h |
| 7 | Extraer lógica de `operaciones.py` router a service | A2 | 1-2 días |
| 8 | `core/fechas.py` + `quant/` puro + setup_indices | M3, M5, M6 | 1 día |
| 9 | Front: proxy genérico + `lib/fmt.ts` | M7, M8 | 1-2 días |
| 10 | Tests de caracterización de valuaciones/pnl | A5 | 2-3 días |
| 11 | `core/motor_base.py` — de a un motor por vez | M1, A4 | 3-5 días en cuotas |
| 12 | Partir manager-view (y las gigantes, al tocarlas) | M9 | en cuotas |

**Regla de oro para ejecutarlo**: los ítems 7, 11 y 12 son refactors de
*mover*, no de *reescribir* — mismo comportamiento, mejor lugar. Cada uno se
valida con el comparador correspondiente (output idéntico antes/después) y se
mergea solo. Nada de big-bang.

> **Nota REGLA #2**: las severidades de CPU/latencia (estampida, índices al
> arranque, N+1 de PnL) son análisis de código, no mediciones de prod. Antes de
> optimizar por performance, medir con timing/`explain()`; los fixes de
> corrección (A1, A3, A4, A5) no necesitan medición — son correctitud.
