# MAPA DE LA APP — qué se puede hacer con AcaQuant

> **Qué es este documento.** Mapa completo y navegable de la plataforma: TODAS las vistas del
> frontend, sus tabs, sus filtros, qué endpoint consume cada cosa, quién puede verla (RBAC) y
> qué puede ESCRIBIR cada usuario. Sirve para responder "¿qué se puede hacer con la app?" y
> "¿qué falta?" sin abrir el código.
>
> **Cómo se generó.** Relevado **leyendo el código** de los dos checkouts (`acaquant-backend`
> FastAPI + `acaquant-frontend` Next.js 16) el **2026-08-09**, en 13 pasadas por dominio
> (10 funcionales + RBAC + navegación + IA).
>
> **Cómo se mantiene al día.** La §0 (inventario de endpoints, gate efectivo y matriz de roles)
> es **auto-generada** por `python -m scripts.gen_mapa_app` desde la app FastAPI montada, y CI
> corre `--check`: si agregás un router y no regenerás, **el build falla**. El resto del doc se
> mantiene a mano — ver la regla de mantenimiento en `CLAUDE.md`.
>
> **⚠️ ADVERTENCIA — lo marcado `SIN VERIFICAR` NO está confirmado.** Aparece cuando el
> relevador no pudo cerrar el dato leyendo el repo (típicamente: contenido real de tablas de
> producción, o un consumidor externo que no está en este checkout). Tratarlo como hipótesis.
>
> **⚠️ ADVERTENCIA #2 — los "roles con acceso" de todo el doc salen de `core/roles.py::DEFAULT_MATRIX`,
> que es solo el BOOTSTRAP.** En runtime la tabla SQL `manager.role_matrix` **PISA** ese default
> (`core/roles.py::_load_matrix`). Para saber qué ve hoy un rol hay que mirar
> `/manager → USUARIOS → ROLES Y PERMISOS`. Nadie pudo leer la DB de prod desde el repo.

---

## 0. INVENTARIO AUTO-GENERADO (no editar a mano)

> Los tres bloques de esta sección los regenera **`python -m scripts.gen_mapa_app`** desde la app
> FastAPI montada. Son lo que más drift tiene (cada router nuevo desactualiza el inventario y la
> matriz) y lo más caro de relevar a mano. **CI corre `--check` y falla si quedaron viejos**, así
> que esta sección no puede mentir. El resto del doc —qué hace cada vista, sus tabs, sus filtros,
> las rarezas— se mantiene A MANO: cambia despacio y ahí está el criterio.
>
> Para el listado completo de las 400+ rutas (no va al doc, lo hace ilegible):
> `python -m scripts.gen_mapa_app --full`.

<!-- AUTOGEN:resumen -->
- **515 endpoints** montados en `api.main.app`, en **32 routers**.
- **189 escriben** (POST/PUT/PATCH/DELETE); 326 son de solo lectura.
- **21 módulos** canónicos y **7 roles** en `core/roles.py`.
<!-- /AUTOGEN:resumen -->

### 0.1 Endpoints y gate efectivo, por router

<!-- AUTOGEN:routers -->
| Router | Rutas | Escriben | Gate efectivo | Módulo declarado | |
|---|---:|---:|---|---|---|
| `(raíz)` | 2 | 0 | — · 1 ruta con gate extra | — | ⚠️ |
| `/api/aca` | 18 | 8 | — · 9 rutas con gate extra | `aca` | ⚠️ |
| `/api/agente` | 11 | 5 | `ia` + `require_admin` | `ia` |  |
| `/api/analitica` | 11 | 1 | — | — | ⚠️ |
| `/api/ap5` | 8 | 2 | `operaciones` · 3 rutas con gate extra | — |  |
| `/api/back-office` | 59 | 35 | `back-office` · 30 rutas con gate extra | `back-office` |  |
| `/api/back-office/interbanking` | 26 | 17 | `back-office` · 22 rutas con gate extra | `back-office` |  |
| `/api/back-office/senebis` | 22 | 14 | `back-office` · 4 rutas con gate extra | `back-office` |  |
| `/api/cotizaciones` | 34 | 1 | — · 1 ruta con gate extra | — | ⚠️ |
| `/api/cuentas` | 2 | 0 | `operaciones` | `operaciones` |  |
| `/api/derivados` | 18 | 6 | — · 5 rutas con gate extra | — | ⚠️ |
| `/api/estrategia` | 4 | 0 | `trading` | — |  |
| `/api/ia` | 1 | 0 | `ia` | `ia` |  |
| `/api/ingest` | 13 | 9 | —`verify_ingest_token` | — |  |
| `/api/manager` | 139 | 68 | varía por ruta (todas gateadas)`require_any_module_manager_manager_comercial_manager_clientes_manager_clientes_bulk` | `manager` |  |
| `/api/market` | 2 | 0 | — | — | ⚠️ |
| `/api/mesa-dinero` | 10 | 5 | — · 9 rutas con gate extra | — | ⚠️ |
| `/api/news` | 3 | 0 | — | — | ⚠️ |
| `/api/operaciones` | 54 | 8 | `operaciones` · 24 rutas con gate extra | `operaciones` |  |
| `/api/operar` | 3 | 1 | `operar` · 2 rutas con gate extra | `operar` |  |
| `/api/operativa` | 6 | 2 | `operar` · 4 rutas con gate extra | `operar` |  |
| `/api/ordenes` | 8 | 3 | `operar` · 5 rutas con gate extra | `operar` |  |
| `/api/portfolio` | 16 | 3 | `portfolios` · 14 rutas con gate extra | `portfolios` |  |
| `/api/research-bcra` | 2 | 0 | `research` | — |  |
| `/api/research-docs` | 2 | 0 | `research` | — |  |
| `/api/research-fred` | 2 | 0 | `research` | — |  |
| `/api/research1816` | 10 | 0 | `research` | — |  |
| `/api/risk` | 5 | 0 | `operar` | `operar` |  |
| `/api/scanner` | 7 | 0 | `renta-variable` · 2 rutas con gate extra | — |  |
| `/api/titulos` | 2 | 0 | — | `portfolios` | ⚠️ |
| `/api/trading` | 7 | 1 | `trading` | `trading` |  |
| `/api/valuaciones` | 8 | 0 | `portfolios` · 8 rutas con gate extra | — |  |

**⚠️ Routers sin gate de módulo, o cuyo gate real no coincide con el módulo que declaran en `ENDPOINT_MODULE_PREFIXES`:**

- `(raíz)` (2 de 2 rutas sin gate de módulo)
- `/api/aca` (declara `aca`, no lo aplica)
- `/api/analitica` (11 de 11 rutas sin gate de módulo)
- `/api/cotizaciones` (33 de 34 rutas sin gate de módulo)
- `/api/derivados` (13 de 18 rutas sin gate de módulo)
- `/api/market` (2 de 2 rutas sin gate de módulo)
- `/api/mesa-dinero` (10 de 10 rutas sin gate de módulo)
- `/api/news` (3 de 3 rutas sin gate de módulo)
- `/api/titulos` (declara `portfolios`, no lo aplica)

No es necesariamente un bug: `ENDPOINT_MODULE_PREFIXES` **no se aplica en runtime** (solo lo consume un test), y para los módulos que todos los roles tienen se decidió no gatear. Lo que sí implica es que **destildar esos módulos en Manager → Roles no bloquea nada server-side**: solo esconde el link en el menú.

<!-- /AUTOGEN:routers -->

> **`/api/mesa-dinero` aparece en esa lista pero NO es un hueco.** El generador solo
> entiende gates de MÓDULO, y esa vista pasó a gate **per-usuario**
> (`require_lectura_mesa`, allowlist en Manager → MESA) el 2026-08-11 — a propósito y
> sobre las 9 rutas. Es más restrictivo que antes, no menos: antes la veía todo el
> módulo `operaciones`. Si algún día el generador aprende a leer los gates
> per-usuario, esta nota se borra.

### 0.2 Matriz rol × módulo (default del código)

<!-- AUTOGEN:rbac -->
| Módulo | admin | trader | sales | empleado_aca | asistente_comercial | back_office | invitado |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `home` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `renta-fija` | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `derivados` | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `agro` | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `sinteticos` | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `renta-variable` | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `trading` | ✓ | · | · | · | · | · | · |
| `operar` | ✓ | · | · | · | · | · | · |
| `operaciones` | ✓ | ✓ | · | · | ✓ | · | · |
| `portfolios` | ✓ | ✓ | · | · | ✓ | · | · |
| `back-office` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| `research` | ✓ | · | · | · | · | · | ✓ |
| `ia` | ✓ | · | · | · | · | · | ✓ |
| `aca` | ✓ | · | · | ✓ | · | · | · |
| `manager` | ✓ | · | · | · | · | · | · |
| `manager_clientes` | ✓ | · | · | · | ✓ | · | · |
| `manager_clientes_bulk` | ✓ | · | · | · | · | · | · |
| `manager_titulos` | ✓ | · | · | · | · | · | · |
| `manager_instrumentos` | ✓ | · | · | · | ✓ | · | · |
| `manager_contrapartes` | ✓ | · | · | · | ✓ | · | · |
| `manager_aunesa` | ✓ | · | · | · | ✓ | · | · |

> Esta matriz es el **DEFAULT del código** (`core/roles.py::DEFAULT_MATRIX`). La tabla SQL `manager.role_matrix` la **PISA**: el enforcement real es lo que esté ahí, editable desde `/manager → ROLES Y PERMISOS`. Para ver la de producción hay que consultarla en la base.
<!-- /AUTOGEN:rbac -->

---

## 1. ÍNDICE DE VISTAS

**19 vistas navegables** (el App Router tiene exactamente 19 `page.tsx`, y las 19 están en el menú:
no hay rutas huérfanas). Los otros 68 archivos de `src/app/api/**/route.ts` son proxies HTTP, no
páginas.

| # | Vista | Ruta | Módulo RBAC | Roles (default) | Qué hace |
|---|---|---|---|---|---|
| 1 | **HOME** | `/` | `home` | admin, trader, sales, asistente_comercial, back_office, invitado | Terminal de apertura: watchlist live (Argentina + índices/futuros/UST + curva DLR + bonos offshore), noticias RSS con reader inline, chart TradingView y modal de briefing. |
| 2 | **OPERAR** | `/operar` | `operar` | **solo admin** | Única pantalla transaccional: manda y cancela órdenes REALES contra ROFEX (títulos, FCI, brackets) y ejecuta la operativa Dólar MEP de 2 patas. |
| 3 | **TRADING** | `/trading` | `trading` | **solo admin** | Escritorio intradía: pivots Floor Trader por activo, radares (movers/volumen/pivotes/contexto), order book, charts live + zonas del ADR, monitor FIFO del día y cuaderno manual de PnL. |
| 4 | **RESEARCH** | `/research` | `research` | admin, invitado | Laboratorio de research: spreads/series 1816, análisis de sensibilidad precio×TIR (ex vista ESTRATEGIA), reportes escritos (mails + PDFs), BCRA, FRED y el tablero/screener de renta variable internacional (feed Reuters/Eikon). |
| 5 | **AGRO** | `/agro` | `agro` | admin, trader, sales, asistente_comercial, invitado (sin tab DATOS) | Mesa de agro: pases TRIGO/MAÍZ/SOJA vs futuros MATBA, cobertura del productor (ON/Pagaré/Sintético), mejoras de precio disponible, opciones agro con simulador y tablero CBOT. |
| 6 | **DERIVADOS** | `/derivados` | `derivados` | admin, trader, sales, asistente_comercial, invitado | Chain de opciones de GGAL con griegas, armado de estrategias multi-pata, payoff, escenarios y post-trade lab. |
| 7 | **RENTA FIJA** | `/renta-fija` | `renta-fija` | admin, trader, sales, asistente_comercial, invitado | Pantalla live de renta fija ARS/HD: tablero por curva, chart de curva (live/histórico/fair value), matriz de forwards, breakevens Lecap↔CER vs REM y el modal SIMULAR INVERSIÓN (importe + bono + precio → TIR y cronograma de cobros). |
| 8 | **RENTA VARIABLE** | `/renta-variable` | `renta-variable` | admin, trader, sales, asistente_comercial, invitado | Scanner de CEDEARs (ARS live) + ADR (USD EOD), con métricas quant (pulso por rubro, pivots/volatilidad, retornos) y chart por ticker. |
| 9 | **SINTÉTICOS** | `/sinteticos` | `sinteticos` | admin, trader, sales, asistente_comercial, invitado | Dos tablas de sintéticos con futuro DLR (LONG ROFEX+LONG LECAP / SHORT ROFEX+LONG DLK) y su curva de TNA por plazo. |
| 10 | **AUM** | `/aum` | `portfolios` | admin, trader, asistente_comercial | Activos bajo administración: evolución del total por cartera, snapshot con drill-down cuenta×asset, sub-vista FCI y comparación de saldos entre dos fechas. |
| 11 | **CARTERAS** | `/valuaciones` | `portfolios` | admin, trader, asistente_comercial | Performance por cuenta: valor del portfolio, tabla mensual con TWR/TEM/XIRR, posiciones a una fecha, atribución de la variación y PnL cost-basis por título. |
| 12 | **CONTRAPARTES** | `/contrapartes` | `operaciones` | admin, trader, asistente_comercial | Contra quién operamos: volumen bruto por contraparte, por grupo/segmento y por mes, con drill-down a los boletos de un día. |
| 13 | **MESA DE DINERO** | `/mesa-dinero` | **ninguno** — allowlist per-usuario | admin + `mesa_dinero_lectores`/`_escritores`; `mesa_dinero_lectores_resultados` ve **solo la tab RESULTADOS** | Registro MANUAL de las operaciones de la mesa (compra+venta) con resultado diario, TC manual, atribución por comercial (regla 50/50) y panel del fondo ACA R.TOTAL. |
| 14 | **OPERACIONES** | `/operaciones` | `operaciones` | admin, trader, asistente_comercial | Volumen y arancel de boletos de mercado, más las verticales AGRO / DÓLAR FUTURO / POSICIONES Y DIFERENCIAS y los depósitos/extracciones. |
| 15 | **OPERADORES** | `/operadores` | `operaciones` (+ `control_comercial` per-usuario para una sub-vista) | admin, trader, asistente_comercial | Tablero Comercial: qué cuentas gestiona cada operador, cuánto AuM/volumen/arancel generan, estado comercial y objetivos. |
| 16 | **REFERIDOS** | `/referidos` | `operaciones` | admin, trader, asistente_comercial | Vista para la empresa referidora: solo sus cuentas — operan, AuM, rendimientos, volumen, aranceles y comisión FCI a la coop. |
| 17 | **BACK OFFICE** | `/back-office` | `back-office` | admin, trader, sales, asistente_comercial, back_office | Operación diaria del back office: SENEBIS, tenencia valorizada, títulos en alquiler, Tesorería (caja del día), títulos a enviar/recibir al mercado y acreencias de clientes, extractos bancarios de Interbanking y saldos de cuentas comitentes. |
| 18 | **ACA** | `/aca` | `aca` | **solo `empleado_aca`** (+ admin + escritores de la mesa) | **Link de primer nivel del header** (no está adentro de NEGOCIO: es la cartera de la casa y la mira gerencia). Resumen ejecutivo de la cartera PROPIA de ACA para gerencia: foto MENSUAL con valuación ARS/A3500/USD, composición por cartera, detalle título por título, métricas de concentración y rendimiento acumulado vs benchmarks. Carga manual tipo Excel (`docs/ACA.md`). |
| 19 | **MANAGER** | `/manager` | `manager` + 6 sub-módulos | admin (todo); asistente_comercial entra por sub-módulos | Panel de administración: observabilidad, validaciones/debug, maestros (assets/bonos/ONs/CEDEARs), segmentación de clientes y contrapartes, backfills/imports, usuarios/roles/grupos y allowlists de escritura. |

**Superficies transversales (no son rutas propias):**

> ⚠️ Las dos últimas son **sub-apps montadas** (`app.mount`), así que `scripts/gen_mapa_app` **no las ve**: su inventario recorre los `include_router`. Sus endpoints se documentan a mano acá y en su doc propio.

| Superficie | Dónde vive | Módulo | Qué hace |
|---|---|---|---|
| **Briefing de apertura ☀** | Botón en el footer de TODAS las páginas + modal automático 10:00 ART L-V | `ia` | Foto de apertura determinista (0 tokens): futuros, oficial, MEP/CCL, cauciones, DLR, bonos off, bonos que pagan hoy, research del día. |
| **API externa (accionistas)** | `https://api.acaquant.com/ext` (sub-app montada, `api/ext/`) | Auth propia: API key → token 30 min, con CF Access **Service Auth** delante. **Ningún** módulo del RBAC de la mesa | Le entrega a un accionista SUS operaciones, boleto por boleto (read-only). El permiso es un DATO (`ext.cuentas_autorizadas`), no código: alta de un cliente nuevo = INSERT, sin deploy. Scope **fail-closed** (al revés que `cuentas_visibles`). Doc: `docs/API_EXTERNA.md`; contrato para el consumidor en `/ext/docs`. |

---

## 2. MATRIZ ROL × MÓDULO

### 2.1 Los 22 módulos canónicos (`core/roles.py::MODULES`)

| Módulo | Qué cubre | Enforcement server-side REAL (verificado sobre las 423 rutas) |
|---|---|---|
| `home` | `/` + market + news | **NINGUNO** — `/api/market`, `/api/news` van `_PUBLIC` (solo bearer) |
| `renta-fija` | `/renta-fija`, cotizaciones, curvas | **NINGUNO** — `/api/cotizaciones` (33), `/api/analitica` (15), `/api/titulos` (2) son `_PUBLIC` |
| `derivados` | `/derivados` (opciones) | **NINGUNO** — los GET de `/api/derivados/*` son `_PUBLIC` |
| `agro` | `/agro` | Solo las **5 PATCH** de `/api/derivados/agro/*` (`require_module("agro")` + `require_no_invitado`) |
| `sinteticos` | `/sinteticos` | **NINGUNO** — `derivados_sinteticos.router` va `_PUBLIC` bajo `/api/derivados` |
| `renta-variable` | `/renta-variable` | `/api/scanner/*` (9 rutas) |
| `trading` | `/trading` | `/api/trading/*` + `/api/estrategia/*` (13) |
| `operar` | `/operar` + envío de órdenes | `/api/ordenes`, `/api/operativa`, `/api/operar`, `/api/risk` (22) |
| `operaciones` | `/operaciones`, `/operadores`, `/contrapartes`, `/referidos` | `/api/operaciones`, `/api/cuentas` (46) — **`/mesa-dinero` salió del módulo el 2026-08-11**: allowlist per-usuario |
| `portfolios` | `/aum`, `/valuaciones` | `/api/portfolio`, `/api/valuaciones` (18) |
| `back-office` | `/back-office` (SENEBIS, tesorería, acreencias, alquiler) | `/api/back-office/*` incl. `/senebis` (67) |
| `research` | `/research` | `/api/research1816`, `/api/research-bcra`, `/api/research-fred`, `/api/research-docs` (17) |
| `ia` | **Solo el briefing** (que ni siquiera usa IA). El resto del prefijo es admin-only y hoy está vacío | `/api/ia/*` (1) |
| `asistente` | Asistente de Negocio (vista `negocio` del copiloto) | **Sin prefijo propio**: gate fino en `copiloto/derivacion.py::_acceso` (+ `solo_internos`) |
| `aca` | `/aca` (resumen ejecutivo de la cartera propia) | `/api/aca/*` (14) — gate `require_lectura_aca` sobre TODO el router: módulo ∪ allowlist de la mesa |
| `manager` | `/manager` umbrella | `/api/manager/*` (~70) + `PUT /api/cotizaciones/opciones/tasa` |
| `manager-aca` ⚠️ | Manager → ACA. **NO es módulo del RBAC** (como `mesa-dinero`): capacidad publicada por `/api/me` = escritura en ACA. El gate real suma el acceso a Manager | `/api/manager/aca/*` (7) |
| `manager_clientes` | Manager → CLIENTES / ACA VALORES / CONTROL AUTO | `clientes.router`, `aca_valores`, `control_automatico` (10) |
| `manager_clientes_bulk` | Manager → cargas masivas | `clientes.bulk_router` (3) |
| `manager_titulos` | Manager → TÍTULOS (assets, ONs, bonos, breakevens, RV) | 28 rutas |
| `manager_instrumentos` | Manager → TÍTULOS → Instrumentos (solo lectura) | `instrumentos.router` (2) |
| `manager_contrapartes` | Manager → CONTRAPARTES | 6 rutas |
| `manager_aunesa` | Manager → AUNESA / IMPORTAR AUM | `import_tenencia.router` (3) |

> **Lectura clave:** **4 módulos (`home`, `renta-fija`, `derivados`, `sinteticos`) no
> tienen gate server-side.** Sacarlos de un rol solo esconde el link del nav; con el bearer del
> frontend la data sigue accesible. Está declarado como decisión en `api/auth.py:289-293` ("sale más
> barato un `_PUBLIC` sin `require_module`"), pero el efecto es que **la matriz miente para esos 4**.

### 2.2 Matriz por DEFAULT (`core/roles.py::DEFAULT_MATRIX`) — bootstrap, NO la verdad de prod

| Módulo | admin | trader | sales | **empleado_aca** | asistente_comercial | back_office | invitado |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| home | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| renta-fija | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ |
| derivados | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ |
| agro | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ |
| sinteticos | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ |
| renta-variable | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ |
| trading | ✓ | – | – | – | – | – | – |
| operar | ✓ | – | – | – | – | – | – |
| operaciones | ✓ | ✓ | – | – | ✓ | – | – |
| portfolios | ✓ | ✓ | – | – | ✓ | – | – |
| back-office | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| research | ✓ | – | – | – | – | – | ✓ |
| ia | ✓ | – | – | – | – | – | ✓ |
| asistente | ✓ | – | – | – | – | – | – |
| manager | ✓ | – | – | – | – | – | – |
| manager_clientes | ✓ | – | – | – | ✓ | – | – |
| manager_clientes_bulk | ✓ | – | – | – | – | – | – |
| manager_titulos | ✓ | – | – | – | – | – | – |
| manager_instrumentos | ✓ | – | – | – | ✓ | – | – |
| manager_contrapartes | ✓ | – | – | – | ✓ | – | – |
| manager_aunesa | ✓ | – | – | – | ✓ | – | – |
| **aca** | ✓ | – | – | **✓** | – | – | – |
| **total** | **23** | **10** | **8** | **9** | **14** | **2** | **9** |

Notas verificadas:
- `admin = MODULES` (todo, incluidos los 6 sub-módulos de manager).
- **`empleado_aca` = `sales` + `aca`** (rol nuevo, 2026-08-13). Es un rol APARTE y no `sales`
  renombrado porque **`sales` es `DEFAULT_ROLE`**: todo email que pasa Cloudflare por primera vez se
  auto-registra ahí, así que colgarle `aca` habría dado la cartera propia de la casa a cualquier alta
  automática. Con el rol separado, entrar a ACA es un acto explícito del admin.
- `aca` **jamás** para `invitado` (REGLA #8) — hay un test que falla si alguien lo agrega.
- `back_office` es mínimo **a propósito** (`home` + `back-office`); el admin lo amplía desde el panel.
- `asistente_comercial` NO tiene el umbrella `manager` ni `manager_clientes_bulk` ni `manager_titulos`:
  entra a Manager solo por los sub-módulos.
- `invitado` **no se asigna por email**: se fuerza por venir del portal www (§2.4).
- El propio `core/roles.py` avisa que en prod la `role_matrix` ya está poblada → el default de `ia`
  (solo admin) y `operar` (solo admin) puede NO ser lo vigente. una nota vieja decía que el user
  sumó `ia` a **trader y sales** el 2026-07-13 (**SIN VERIFICAR** contra la DB).

### 2.3 La tabla SQL pisa el default

`core/roles.py::_load_matrix()` → `core.roles_sql.load_matrix_sql()`. **Si la tabla devuelve algo, ESE
es el enforcement real**; `DEFAULT_MATRIX` solo se usa si la tabla está vacía o si SQL falla (nunca
devuelve matriz vacía → nunca lockea a nadie). Consecuencias:

- **Agregar un módulo nuevo a `MODULES` NO se propaga**: con la matriz poblada nace sin asignar a
  nadie (ni a `admin`) hasta que el admin lo tilde. Es el patrón de canary usado con `ia`.
- Sacar un módulo de un rol es el **kill switch** (efecto en ≤60s por el cache de roles, +30s del
  cache de `/api/me` del proxy Next).
- `set_role_modules()` filtra contra `MODULES` → no se puede persistir un módulo inventado, pero **sí
  se puede CREAR UN ROL NUEVO** cualquiera desde el panel.
- Todo cambio queda en `manager.role_audit` (append-only).
- `INVITADO_MODULES` es la excepción: se toma de `DEFAULT_MATRIX["invitado"]` **en código** y NO se lee
  de SQL — el invitado no es editable desde el panel, a propósito.

### 2.4 Portal INVITADO — www.acaquant.com (REGLA #8)

**Detección (dos mitades):** el frontend (`lib/cf-access.ts::isGuestRequest`) valida el JWT de
Cloudflare y compara el `aud` contra `CF_ACCESS_AUD_GUEST`; si matchea, `src/proxy.ts` agrega el header
**`x-acaquant-portal: guest`** server-side (el JS del browser no lo controla). El backend lo lee en
`api/auth.py::is_guest_portal`.

**Tres capas de corte, no una:**
1. **Middleware duro** `api/main.py::_guard_portal_invitado` + `path_permitido_invitado`: allowlist de
   prefijos `GUEST_PATH_PREFIXES` con match **por segmento**. Todo `/api/*` fuera de la lista → 403,
   exista o no el gate de módulo. **Router nuevo = cerrado por default para www.**
   Permitidos: `/api/health`, `/api/me`, `/api/analitica`, `/api/cotizaciones`, `/api/derivados`,
   `/api/titulos`, `/api/market`, `/api/news`, `/api/scanner`, `/api/research1816`,
   `/api/research-bcra`, `/api/research-fred`, `/api/research-docs`, `/api/ia`.
2. **`require_module`**: si es guest, NO mira el rol del email — chequea contra `INVITADO_MODULES`
   (`home, renta-fija, derivados, agro, sinteticos, renta-variable, research, ia`).
3. **Gates duros**: `require_admin` y `require_control_comercial` rechazan al guest SIEMPRE;
   `require_no_invitado` bloquea escrituras que viven en un módulo de mercado (las 5 PATCH de agro).

**Por qué es default-deny:** el invitado se define por el header, no por su email — su email caería en
`DEFAULT_ROLE=sales` (que tiene `back-office`). Por eso `/api/me` devuelve `role:"invitado"` +
`INVITADO_MODULES` hardcodeado cuando `is_guest_portal`.

**IA para invitados:** identidad `guest:<email>`, tope diario propio 100k tokens
(`AI_BUDGET_TOKENS_DIA_INVITADO`) vs 1M de la mesa; en el copiloto solo alcanza vistas cuyo módulo ∈
`INVITADO_MODULES` y que NO sean `solo_internos` (la guía `ayuda` y `negocio` quedan afuera).

### 2.5 Capas de auth (orden real, de afuera hacia adentro)

1. **Cloudflare Access** (hostname) — quién entra al sitio. ⚠️ Había 5 paths con BYPASS para el MCP; el MCP se borró el 2026-08-28 pero **la app `acaquant-mcp-bypass` sigue en el panel de CF**, ocupando 5/5 destinations contra rutas que ya dan 404. Sacarla es un pendiente de consola, no de repo.
2. **`verify_api_key`** (`Authorization: Bearer <API_KEY>`). Fail-open si `API_KEY` está vacía (solo
   dev: `_validar_postura_auth` aborta el boot con `ENV=prod` sin API_KEY o sin `CF_ACCESS_TEAM/AUD`).
3. **JWT de CF Access** (`api/auth.py::get_user_email`) — identidad criptográfica. User JWT → `email`;
   service-token JWT → `common_name` + allowlist `CF_TRUSTED_SERVICE_TOKENS`, y recién ahí se cree el
   `x-acaquant-user-email` forwardeado. Sin nada → `"anon"`.
4. **RBAC por módulo** (`require_module` / `require_any_module`).
5. **Gates que NO son de módulo** (§6.1).
6. **Rate limit** (slowapi, key = email; anónimos comparten bucket).

Identidades sintéticas (`anon`, `""`, `service:<cn>`) → rol `none`, que no está en ninguna matriz →
**0 módulos, fail-closed**. Un email desconocido que pasó CF Access se **auto-registra** en
`manager.manager_users` con `DEFAULT_ROLE = "sales"` (o `admin` si está en `MANAGER_EMAILS`).

---

## 3. MAPA DE NAVEGACIÓN

### 3.1 Shell global

`src/app/layout.tsx` es `force-dynamic` (si no, el nav de un admin podía servirse cacheado a un
trader). Estructura del `<body>`:

1. `<Header modules={modules}/>` — barra azul `#094293`, alto 40px, nav filtrada por RBAC + botón ✦ IA.
2. `<main>` — la vista.
3. 4 modales de anuncio globales: `AnuncioResearchModal`, `AnuncioChicagoModal`,
   `AnuncioDolarFuturoModal` (gatea por rol: `admin|trader|sales|asistente_comercial`, nunca invitado),
   `AnuncioDiferenciasDiariasModal`. Cada uno se auto-oculta fuera de su ventana de fechas ART y con
   "no volver a mostrar" en `localStorage`.
4. `<footer>` (20px): `ThemeToggle` · "ACA VALORES · MERCADO DE CAPITALES" · **botón ☀ BRIEFING** ·
   "MERVAL / ROFEX". **No hay KPIs numéricos en header ni footer** (verificado).

Resolución de módulos: `getMe()` SSR → `modules = me?.modules ?? (API_URL ? [] : null)`.
`null` (dev sin `API_URL`) → se ve TODO. `[]` (prod con `getMe` fallido) → **fail-closed**, nav vacío.

### 3.2 Árbol de menú (`header.tsx::NAV`, orden exacto izq→der)

```
HOME                      → /                  [home]
OPERAR                    → /operar            [operar]
TRADING                   → /trading           [trading]
RESEARCH                  → /research          [research]
ACA                       → /aca               [aca]
MERCADOS ▼                (grupo, aparece si tiene ≥1 item)
  ├─ Agro                 → /agro              [agro]
  ├─ Derivados            → /derivados         [derivados]
  ├─ Renta Fija           → /renta-fija        [renta-fija]
  ├─ Renta Variable       → /renta-variable    [renta-variable]
  └─ Sintéticos           → /sinteticos        [sinteticos]
NEGOCIO ▼                 (grupo)
  ├─ AUM                  → /aum               [portfolios]
  ├─ Carteras             → /valuaciones       [portfolios]
  ├─ Contrapartes         → /contrapartes      [operaciones]
  ├─ Mesa de Dinero       → /mesa-dinero       [mesa-dinero *]
  ├─ Operaciones          → /operaciones       [operaciones]
  ├─ Operadores           → /operadores        [operaciones]
  └─ Referidos            → /referidos         [operaciones]
BACK OFFICE               → /back-office       [back-office]
MANAGER                   → /manager           [manager ∨ manager_clientes ∨ manager_clientes_bulk]
```

Los items de los dropdowns están ordenados alfabéticamente por label. Los dropdowns son **100 % CSS**
(`group-hover`, sin estado React) → no hay cierre con Esc ni navegación por teclado.

**Portal invitado (www) — nav plana, MAYÚSCULA y A→Z** (`header.tsx::navInvitado`, 2026-08-11). El
invitado no tiene dropdowns: MERCADOS se aplana, así que quedaban mezclados labels top-level en
mayúscula (RESEARCH) con los del dropdown en Title Case (Agro, Renta Fija…), y RESEARCH aparecía
segundo porque heredaba el orden de `NAV`. Ahora, **solo para el invitado**, sobre las entries ya
filtradas por RBAC se aplica `toUpperCase()` + orden alfabético (`localeCompare` en `es`, para que
SINTÉTICOS no se vaya al final por el acento) con HOME pinneado primero por ser la vista por default.
Se ordena la lista ya filtrada — una vista nueva que se sume al portal entra en su lugar sola, sin
lista paralela que mantener. **El portal interno no cambia** (mantiene dropdowns y su orden). Es solo
UX: no toca módulos, roles ni gates.

**`*` = CAPACIDAD, no módulo del RBAC.** `mesa-dinero` **no** está en `core.roles.MODULES` ni en la
matriz: lo publica `/api/me` dentro de `modules` cuando el email está en la allowlist de la vista
(`operaciones.mesa_dinero_lectores` ∪ `_escritores`, o es admin). Se mete en la misma lista a
propósito, para que el nav y `src/proxy.ts` sigan filtrando con UN solo mecanismo. **No agregarlo a
`MODULES`**: pondría un checkbox en ROLES Y PERMISOS que no controlaría nada.

**Qué ve cada rol** (cruzando `NAV` con `DEFAULT_MATRIX`):

| Rol | Entradas visibles |
|---|---|
| `admin` | HOME · OPERAR · TRADING · RESEARCH · **ACA** · MERCADOS (5/5) · NEGOCIO (7/7) · BACK OFFICE · MANAGER |
| `trader` | HOME · MERCADOS (5/5) · NEGOCIO (7/7) · BACK OFFICE. **Sin** OPERAR, TRADING, RESEARCH, ACA, MANAGER |
| `sales` | HOME · MERCADOS (5/5) · BACK OFFICE. **Sin** NEGOCIO, OPERAR, TRADING, RESEARCH, ACA, MANAGER |
| `empleado_aca` | HOME · **ACA** · MERCADOS (5/5) · BACK OFFICE. Es `sales` + la vista ACA, **SOLO LECTURA**. **Sin** NEGOCIO y **sin MANAGER** (por lo tanto tampoco la tab Manager → ACA) |
| `asistente_comercial` | HOME · MERCADOS (5/5) · NEGOCIO (7/7) · BACK OFFICE · MANAGER (entra por `manager_clientes`). **Sin** OPERAR, TRADING, RESEARCH. **ACA y Manager → ACA aparecen si está en la allowlist de escritura de la mesa** — no las da el rol |
| `back_office` | HOME · BACK OFFICE. Nada más |
| `invitado` (www) | HOME · AGRO · DERIVADOS · RENTA FIJA · RENTA VARIABLE · RESEARCH · SINTÉTICOS — los 5 items de MERCADOS **aplanados como links top-level** (sin dropdown), todo en MAYÚSCULA y ordenado A→Z con HOME primero (ver abajo) |

Módulos que **no generan entrada de menú**: `ia` (habilita el ✦ IA y el briefing del footer),
y todos los `manager_*` salvo su efecto sobre el link MANAGER. (El módulo `asistente`
se fue con el copiloto el 2026-08-19.)

**Slot derecho del header — ⚠️ VACÍO.** Acá vivía el botón de copiloto por página
(`VISTA_IA_POR_RUTA`, `RUTAS_CON_PANEL_PROPIO`, el probe a `GET /api/ia/copiloto/vistas`).
**Todo el sistema de copilotos se dio de baja el 2026-08-19** y ninguno de esos
endpoints existe. Lo único de IA que sobrevive en el header es el **briefing** del
footer (módulo `ia`). En `research-view.tsx` quedó el `<div className="ml-auto">`
vacío donde se montaba — se limpió el 2026-08-31.

### 3.3 Tabs por vista (nombre EXACTO, orden, componente)

| Vista | Tabs (en orden; **negrita** = default) | Persistencia | Notas |
|---|---|---|---|
| `/` HOME | *sin tabs de vista*. Watchlist: **General** · FUTUROS ROFEX · NOTICIAS. Panel derecho: noticias + chart | — | La tab FUTUROS ROFEX solo aparece si el endpoint devolvió filas |
| `/operar` | **DÓLAR MEP** · TÍTULOS Y FCI | no persistida; deep-link `?tab=` | Sub-tabs MEP: **COMPRA** · VENTA (estado compartido). TÍTULOS Y FCI: TÍTULOS · FCI |
| `/trading` | **PIVOTS** · INTRADAY · PNL HISTÓRICO | `trading.tab` (keep-alive) | Radar interno: fila arriba **MOVERS ±4%** · VOLUMENES ACCIONES; fila abajo **PIVOTES** · ESTRATEGIA |
| `/research` | **RENTA FIJA ARGENTINA** · ANÁLISIS SENSIBILIDAD · REPORTES FINANCIEROS · BCRA · DATOS INTERNACIONALES · RENTA VARIABLE INTERNACIONAL | no persistida (keep-alive) | RV Internacional tiene sub-vista propia persistida: **COTIZACIONES** · FUNDAMENTALS, + FICHA de empresa |
| `/agro` | **Mercado** · Mejoras Precio Dispo · Chicago · Datos | deep-link `?tab=` | **Datos oculta al invitado** (y si quedó seleccionada, se fuerza a Mercado) |
| `/derivados` | *sin tabs de vista* | — | Tabs dentro de paneles: **CALL** · PUT · ESTRAT.; y PAYOFF · ESCENARIOS · LAB |
| `/renta-fija` | *sin tabs de vista* (grid 2×2) | — | Sub-tabs por panel: RF (TASA FIJA/CER/HARD DOLAR/DOLAR LINKED/LIBRO), CURVAS (LIVE/HISTÓRICO/FAIR VALUE), FORWARDS (LIVE/GRÁFICO/Z-SCORE), BREAKEVENS (LIVE/HISTÓRICO). En la barra de tabs, el botón **SIMULAR INVERSIÓN** abre un MODAL (no una pantalla): importe + bono + precio editable → TIR/TEA + cronograma de cobros escalado |
| `/renta-variable` | *sin tabs de vista* | — | MÉTRICAS: **PULSO** · PIVOTS/VOL · RETORNOS (y dentro ZONAS · VOLATILIDAD & BETA con DIARIO/SEMANAL/MENSUAL/ANUAL). CHART: HISTÓRICO · RETORNOS DIARIOS |
| `/sinteticos` | *sin tabs* | — | 2 tablas + 2 charts |
| `/aum` | **TOTAL** · FCI · ANÁLISIS DE DINERO | en la URL (`?tab=`) | |
| `/valuaciones` | **RESUMEN** · ACTIVOS · MÉTRICAS · EVOLUCIÓN · PNL TÍTULOS (arriba) · **TOTALES** (barra INFERIOR) | en la URL (`?sub=`, `?cuenta=`) | Las 3 primeras son UN componente y UN fetch (`/vista`): cambiar de tab no vuelve a consultar. **TOTALES y AJUSTES viven en la barra de estado de abajo**, al lado de BRIEFING/AV AGENT, y solo con la vista abierta. TOTALES **ignora** el selector de cuenta; tiene 2 modos internos (POR TÍTULO / POR CUENTA) |
| `/operaciones` | **OPERACIONES** · ARANCELES · AGRO · DÓLAR FUTURO · POSICIONES Y DIFERENCIAS · DEPÓSITOS & EXTRACCIONES | `operaciones.tab` (keep-alive) | |
| `/operadores` | **Portfolio & Operaciones** · Análisis Comercial · **Profundidad de Clientes** (con 2 solapas adentro: *Por mes* · *Análisis Cuantitativo*) · Cobros Futuros · Informe · Control Comercial | `operadores.*` | Control Comercial visible solo con `me.control_comercial`. Profundidad de Clientes **ignora el Desde/Hasta** (su eje es el tiempo) y esconde ese control + los KPIs |
| `/referidos` | *sin tabs de vista* | `referidos.*` | Detalle del cliente con mini-tabs `pnl` / `ops`; tabla FCI aparte |
| `/contrapartes` | *sin tabs* — **2 modos excluyentes** (rango / día) | — | El modo lo decide si el filtro Día tiene valor |
| `/mesa-dinero` | **OPERACIONES** · RESULTADOS · ACA VALORES RETORNO TOTAL | `mesaDinero.tab` | |
| `/aca` | **RESUMEN** · CARTERAS · ACTIVOS · MÉTRICAS · HISTÓRICO | no persistida | Todo habla del período elegido en el selector de la barra (`YYYY-MM`). **Sin polling**: es una foto mensual, no hay nada que se mueva solo |
| `/back-office` | Senebis · **Tenencia Valorizada** · Títulos en Alquiler · Tesorería · Títulos / Mercado · Acreencias Clientes · Saldos de Cuentas Comitentes | `backoffice.tab` | ⚠ El orden VISUAL pone Senebis primero pero **el default es la 2ª tab**. Senebis: **Órdenes** · Excel Quantex · Excel MAE. Tesorería: **movimientos** · bancos · cheques · mercados · banco a banco. Alquiler: **Portfolio Alquiler** · Marcas por cuenta |
| `/manager` | **OBSERVABILIDAD** · VALIDACIONES · TÍTULOS · CLIENTES · CONTRAPARTES · ACA VALORES · **ACA** · AUNESA · OPERACIONES · MESA · DOCUMENTOS · USUARIOS | `manager.tab` + sub-pills | 12 top-level, **38 hojas**; ver 3.4. ⚠ **ACA VALORES** (informe de retorno del FCI ACA R.TOTAL) y **ACA** (config + histórico de la vista `/aca`) son cosas distintas |

### 3.4 Sub-pills de Manager (segundo nivel, todas persistidas)

- **OBSERVABILIDAD** (`manager.obs.sub.v2`): **DIAGNÓSTICO** · BASE. **Son DOS**, verificado contra `manager-view.tsx` (2026-08-30). El doc listaba cinco: SALUD, LATENCIA e IA ya no se dibujan.
  - DIAGNÓSTICO (`manager.diag.sub`): **ÁRBOL** · LOGS. RECURSOS (CPU/RAM/disk del Droplet) se ELIMINÓ 2026-08-10 junto con su router y el sampler de fondo: eran métricas crudas que no respondían si el sistema estaba sano.
- **VALIDACIONES** (`manager.valid.sub`): **VALIDACIONES** · OPCIONES VTO · DEBUG XIRR · DEBUG TEA.
- **TÍTULOS**: INSTRUMENTOS (con `manager_instrumentos`) · ASSETS · BONOS · BREAKEVENS · RENTA VARIABLE (estas 4 requieren el maestro). Dentro del ALTA de bonos, toggle `Soberano/Provincial` / `Corporativo (ON)` — el LISTADO ya no se parte: BONOS muestra y edita **todos**, corporativos incluidos.
- **CLIENTES** (`manager.cli.subtab`, def `segmentacion`): **SEGMENTACIÓN** · CONTROL AUTO · SIN OPERADOR · FONDEOS (solo con `canBulk`).
- **CONTRAPARTES**: **LISTADO** · CONCILIADOR (badge `!n`).
- **AUNESA** (`manager.aunesa.sub`): FLUJO · AUM · POSICIÓN · BOLETOS (las 4 solo con umbrella `manager`) · **IMPORTAR AUM** (siempre; es lo ÚNICO que ve `manager_aunesa`).
- **OPERACIONES**: PRECIOS · IMPORTAR AUM.
- **USUARIOS** (`manager.usuarios.sub`): **USUARIOS** · ROLES Y PERMISOS · GRUPOS.
- **MESA**: panel único con 4 secciones al 25% (traders + 3 allowlists).
- **ACA** (gestión de la vista `/aca`) — ⚠️ **la ÚNICA tab que NO la da el umbrella `manager`**: la habilita **poder ESCRIBIR en ACA** (allowlist `operaciones.mesa_dinero_escritores` ∪ admin) **y** tener acceso a Manager por cualquier módulo `manager*`. Un `asistente_comercial` en la allowlist la ve; un admin sin allowlist no (aunque admin siempre está en la allowlist por definición). Server-side: `_ACA` en `api/routers/manager/__init__.py`; el nav se filtra con la capacidad `manager-aca` que publica `/api/me`. Tabs: **HISTÓRICO** · CONFIGURACIÓN. HISTÓRICO carga el rendimiento mensual por serie (se tipea en % y se guarda como fracción; el acumulado es de solo lectura porque es un derivado). CONFIGURACIÓN tiene 4 cards: regla de moneda, emisores destacados, clases destacadas y catálogo de series.
---

## 4. DOMINIOS

> Cada sección lleva las tabs con sus filtros y acciones de escritura, los endpoints con params, las
> fuentes de datos y las rarezas verificadas. El inventario plano de TODOS los endpoints está en §7.

---

## 4.1 HOME Y MERCADO GENERAL

### Vista: HOME (ruta frontend: `/`)
- **Módulo RBAC**: `home` | **Roles con acceso**: los 6 roles del default (todos incluyen `home`).
- **Archivos front**: `src/app/page.tsx` → `home-view.tsx` · `watchlist-panel.tsx` · `watchlist-news.tsx` · `news-panel.tsx` · `news-reader.tsx` · `futuros-dlr-curve-chart.tsx` · `tradingview-chart.tsx` · `briefing-modal.tsx` (global, footer) · `ia-vista-panel.tsx` (`vista="home"`).
- **Router backend**: `api/routers/market.py`, `news.py`, `cotizaciones.py`, `me.py`, `ia.py`.
- **Propósito**: terminal de apertura de la mesa.

**Layout**: grilla 2 columnas. Izquierda = `WatchlistPanel` a toda la altura (el recuadro CANJE se quitó
el 2026-07-24; el dato sigue vivo en `/api/analitica/canje` sin UI). Derecha = 2 filas: `NewsPanel`
(50 %) y chart (50 %) con botón maximizar (Esc cierra).

**Selección ticker ↔ chart**: click en una fila setea el ticker. `DLR/*` o `FUTUROS ROFEX` →
`FuturosDlrCurveChart` (curva entera; TradingView no tiene los outrights ROFEX). ARGY / DOLAR MEP /
CCL / OFICIAL → el chart cae al default `DXY` (esas filas son `clickable:false`). Resto → TradingView.

#### Tabs
| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Watchlist → General** (def.) | Tabla con sub-grupos: **Argentina** (MEP, CCL, CANJE, OFICIAL, RIESGO PAÍS, CAUCION ARS/USD `<plazo>D`), **Índices** (SPY/QQQ/DIA/IWM/EWZ/MERVAL), **Futuros** (S&P, NASDAQ, WTI, BRENT, ORO, SOJA, MAÍZ, TRIGO, BTC, ETH), **US Treasury** (13W/5Y/10Y/30Y) y **Bonos Off Shore** (`source=eikon_off`). Columnas: Símbolo · Último · %Día · %7d · %MTD · %YTD · Act (ART) | `GET /api/market/quotes` (poll 30s) + `GET /api/cotizaciones/argy` (poll 5s) | Chips de grupo (General / FUTUROS ROFEX / NOTICIAS). **No hay buscador ni rango**. `SUBGRUPOS_GLOBALES` es fijo en el código | Ninguna |
| **Watchlist → FUTUROS ROFEX** | Outrights DLR: Ticker · Días · Último · TC · **Directo** · **DEVA** · TNA Bid/Last/Offer, orden por vencimiento asc | `GET /api/cotizaciones/futuros-dlr` (5s) | solo el chip. La tab aparece únicamente si hubo filas | Ninguna. Al entrar auto-selecciona la curva DLR en el chart |
| **Watchlist → NOTICIAS** | Titulares **Reuters** del feed Eikon (universo curado server-side: NVDA/MSFT/AAPL/META/TSLA/SPY/QQQ/RKLB/SPCX/KO/LMT/GGAL/YPF/MELI + RICs de soberanos offshore). Badge ticker · fecha · titular | `GET /api/market/eikon-news` (60s) | **Ninguno** | Ninguna. Los titulares **no son clickeables** (no hay reader para Reuters) |
| **Noticias (panel derecho)** | Feed RSS agregado: hora · badge de fuente · título · excerpt. Las notas nuevas flashean 8s; contador "N notas" | `GET /api/news?categoria=&limit=150` (60s) | **Categoría** (server): `all`/`mundo`/`economia`/`finanzas`/`mercados`. **Fuente** (client, chips dinámicos, uno a la vez): Ámbito, Cronista, Infobae, iProfesional, La Nación, Clarín, BAE, WSJ, Yahoo Finance, Investing, Fed, CNBC, MarketWatch, Finnhub. **No** hay fecha ni buscador en la UI (el backend sí soporta `desde`/`hasta`/`fuente`/`keyword`/`skip`) | Ninguna |
| **Noticias → Reader** | Reader mode: título, autor, fecha, hostname, texto limpio (trafilatura). Fallback "abrir original ↗" con paywall | `GET /api/news/article?url=` | — | Ninguna |
| **Chart** | TradingView del ticker, o la curva DLR. Botón maximizar | `GET /api/cotizaciones/futuros-dlr`; TradingView es widget externo | — | Ninguna |
| **Briefing (modal global)** | ver abajo | `GET /api/ia/briefing` | — | Solo `localStorage` |

#### Modal de BRIEFING
- Montado en el **footer de TODAS las páginas** (no solo HOME). Gate: módulo `ia`; si el endpoint da
  401/403 el componente entero es invisible, botón incluido.
- **Auto-apertura**: solo L-V (ART), desde las **10:00**, **1 vez por carga de página**; re-chequea cada
  60s y en `visibilitychange`. Silenciado del día con `localStorage["briefing.dismiss"]`.
- **Bloques** (100 % deterministas, la IA no es fuente de ningún número): `research_hoy` (mail 1816 del
  día, columna izquierda 360px solo en `lg:`) · `futuros` (agrupados: Índices US · Energía · Metales ·
  Granos · Cripto, columnas HOY·1D·WTD·MTD) · `cauciones` (TNA ARS/USD, se oculta si viene vacío) ·
  `oficial` (Mayorista MAE live + A3500) · `financieros` (MEP + CCL) · `futuros_dlr` ·
  `bonos_off` (sin MTD) · `pagan_hoy` (bonos en cartera que pagan hoy).
- **Semántica**: `HOY` = último valor; `null` → literal **"Sin Ops"** (nunca un número viejo disfrazado
  de vivo). `WTD`/`MTD` se calculan SIEMPRE sobre el último cierre. Flag `stale` pinta ⚠ (`_FUTUROS_STALE_MIN = 30`).
- **Escrituras**: ninguna. "NO VOLVER A MOSTRAR HOY" escribe solo `localStorage`.

#### Endpoints
**`market.py` — `/api/market`, gate `_PUBLIC`**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/api/market/quotes` | Últimas cotizaciones del watchlist (`home.market_quotes`); retornos 7d/MTD/YTD/1y computados on-the-fly desde los anchors. Cache TTL 3s en el path sin `symbols` | `symbols` (CSV opcional; con símbolos NO usa cache) | No |
| GET | `/api/market/eikon-news` | Titulares Reuters del feed Eikon; traduce RIC → ticker display | `limit` (1-200, def 80) | No |

**`news.py` — `/api/news`, gate `_PUBLIC`**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/api/news` | Headlines de `home.news_headlines` ordenados por `fecha_publicacion DESC NULLS LAST` | `desde`, `hasta`, `fuente`, `categoria`, `keyword` (ILIKE, escapa `%` y `_`), `limit` (1-500, def 100), `skip` (0-5000) | No |
| GET | `/api/news/article` | Reader mode con trafilatura. Cache 1h (200 entradas, purga LRU/TTL). **Rate limit `20/minute;200/hour`**. Guard anti-SSRF: scheme http(s), puertos 80/443, resuelve DNS y bloquea IPs privadas/loopback/link-local/reserved/multicast, **re-valida cada redirect** (máx 4 hops, body 8MB). Nunca tira 500 | `url` (req) | No |
| GET | `/api/news/stats` | Agregado por fuente en las últimas N horas (debug) | `horas` (1-720, def 24) | No |

**`me.py` — sin prefijo, montado SIN `dependencies=`**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/api/me` | Identidad del caller: `{email, role, modules, is_admin, control_comercial}`. Guest → rol forzado `invitado` + `INVITADO_MODULES`, sin importar el email | — | No, pero **efecto colateral**: `get_user_role` dispara `_touch_last_seen` (UPDATE de `last_seen_at`, throttle 300s + throttle local por proceso) |

**Los de `/api/cotizaciones` que consume HOME**: `GET /argy` (MEP, CCL, CANJE, OFICIAL, RIESGO PAÍS,
CAUCION ARS/USD + soberanos offshore, cada uno con `value/unit/plazo_dias/ret_day/ret_7d/ret_mtd/ret_ytd/ts/source`,
cache 5s) y `GET /futuros-dlr`.

**Proxies Next relevantes**: `/api/market/quotes`, `/api/eikon-news` (`no-store`), `/api/news`,
`/api/news/article` (`maxDuration=30`), `/api/argy` y `/api/futuros-dlr` (ambos `no-store` explícito —
sin eso el edge cache pisaba el poll de 5s, bug 2026-04-23), `/api/me` (propaga identidad verificada +
`x-acaquant-portal: guest`), `/api/ia/[...path]`.

#### Fuentes de datos
- **`home.market_quotes`** — writer `jobs/market_quotes.py` (Yahoo + Finnhub), cron `* 10-23 * * 1-5` y `* 0-1 * * 2-6` (cada minuto, lock 50s). Catálogos hardcodeados en el job: `HOME_STOCKS`, `HOME_FUTUROS`, `HOME_TREASURIES`, `HOME_INDICES_YAHOO`. El job purga símbolos obsoletos.
- **Anchors 7d/MTD/YTD/1y**: `jobs/market_anchors.py` (`0 22 * * 1-5`). El backend NO persiste los retornos: `market_sql.compute_returns` los deriva en cada lectura.
- **`home.news_headlines`** — `jobs/news_ingesta.py` (RSS: Ámbito ×3, Cronista, iProfesional ×2, Clarín, Infobae, La Nación + WSJ, Yahoo Finance, Investing, Fed, CNBC, MarketWatch) `35 11 * * *` + `*/15 12-23 * * *`; y `jobs/news_finnhub.py` `35 11 * * *` + `*/30 12-23 * * *`.
- **`mercado.eikon_news`** — feed Eikon de la PC de oficina vía `POST /api/ingest/eikon/news`; dedup por `story_id`, **retención 7 días** en el mismo INSERT.
- **ARGY** (`api/services/argy.py`): MEP/CCL/canje de `valuaciones.dolar_snapshot` con **fallback a `valuaciones.dolar`** si el snapshot está stale (>60s, incidente 2026-05-05); oficial live de `valuaciones.dolar_oficial_live` (feed MAE) con anchors sobre el **fixing A3500** (`macro.series_macro` clave `DOLAR`); riesgo país de `macro.series_macro`; cauciones de `mercado.caucion_snapshot` + `mercado_hist`; bonos offshore de `core.eikon_bonos` con anchors en `mercado.eikon_cierres` (`jobs/eikon_cierres`, `10 21 * * 1-5`).
- **Futuros DLR**: `mercado.futuros_dlr_snapshot`.
- **Briefing**: `home.market_quotes` · `valuaciones.dolar_oficial_live` · `macro.series_macro` DOLAR · `valuaciones.dolar` · `mercado.futuros_dlr_snapshot` · `mercado.caucion_snapshot` · `ia.research` · `mercado.curvas` + cartera (`acreencias.bonos_pagan_en_fecha`).

#### Notas / rarezas
- **`/api/market`, `/api/news` y `/api/cotizaciones` no tienen gate de módulo** (`_PUBLIC`). El "módulo `home`" solo gobierna el link del nav.
- **`/api/me` es el único router montado sin `dependencies=`** (ni `verify_api_key`). **SIN VERIFICAR**: si se aplica por otra vía; leyendo `api/main.py` no aparece.
- **El grupo "Monedas" de la watchlist es código muerto**: `watchlist-panel.tsx` mapea `type==="forex"` a "Monedas" pero `SUBGRUPOS_GLOBALES` no lo incluye → si el job escribiera forex, no se vería.
- **`Directo` y `DEVA` se calculan en el cliente**; `DEVA` compara contra la fila **anterior de la tabla ya ordenada por vencimiento** → la primera fila siempre da `—`.
- **Dos poll rates en la misma tabla**: quotes 30s, argy/futuros-dlr 5s. Dedupe por payload crudo para no recomputar memos por tick.
- **Bonos Off Shore sin %MTD/%YTD en la watchlist** (`hideExtRet`, pedido 2026-07-28) aunque `/argy` sí los calcula; en el briefing tampoco (el service los manda `None` a propósito) — **inconsistencia real**: `argy.py` sí ancla los offshore contra `mercado.eikon_cierres`, `briefing.py` no.
- **El reader solo aplica al panel RSS**: Eikon guarda solo titulares, sin URL de nota.
- **`market_sql._quotes_all` tiene `@cached(ttl=3)` a propósito**: sin cache cada poll tomaba una conexión del pool y lo agotaba → `PoolTimeout` que tumbó la API (incidente 2026-06-16).
- **`limit=150` del panel de noticias** está por debajo del tope 500; el filtro de fuente es client-side sobre esas 150 → filtrar una fuente poco frecuente puede mostrar pocas notas aunque haya más en la DB.
- **`/api/news/article` es la única superficie de fetch externo arbitrario de la app** — de ahí el triple guard y el bloqueo explícito de metadata services cloud (169.254.169.254).
- `market_sql._fix_tz` y el docstring de "flag NEWS_SQL" son residuos del decomiso de Mongo.

---

## 4.2 RENTA FIJA y ONs

### Vista: RENTA FIJA (ruta frontend: `/renta-fija`)
- **Módulo RBAC (nav)**: `renta-fija`. **⚠ El gate del backend NO es `renta-fija`**: `analitica`, `cotizaciones` y `titulos` se montan `_PUBLIC` (solo `verify_api_key`). El filtro por módulo es **solo del frontend**.
- **Roles**: admin, trader, sales, asistente_comercial, invitado. NO `back_office`.
- **Archivos front**: `renta-fija-live.tsx` (shell 2×2 + poll unificado), `curvas-tab.tsx` (tab CURVAS: filtro de EMISOR + filtro de TEA + las dos columnas), `bonos-table.tsx`, `bono-modal.tsx` (**la FICHA del bono**), `renta-fija-table.tsx`, `libro-panel.tsx`, `curvas-chart.tsx`, `fair-value-view.tsx`, `fair-value-modal.tsx`, `forwards-panel.tsx`, `forward-matrix.tsx`, `forward-matrix-zscore.tsx`, `breakevens-block.tsx`.
- **Proxies**: `/api/cotizaciones/[...path]` (**GET-only a propósito**, para no exponer el `PUT /opciones/tasa`), `/api/analitica/[...path]` (GET+POST), `/api/historico-curva`, `/api/trades`. `/api/titulos/flujos` NO tiene proxy: se pide en SSR directo al backend.
- **Layout**: grilla **2×2 de paneles**, cada uno con sus botones-filtro. **Un único poll de 5s** a `GET /api/cotizaciones/snapshot-live` alimenta los 3 bloques live (reemplazó 3 polls de 5/15/15s).

#### Tabs (sub-tabs por panel)
| Panel → tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **RENTA FIJA → TASA FIJA** | Lecaps/Boncaps: LAST, Intra %, 1D %, Pago Final, TNA, TEA, TEM, DUR, MOD DUR, CONVEX., **TC BE**, VOL NOM | `snapshot-live` (bloque `renta_fija` + `forwards` para el map TEA); `GET /api/titulos/flujos` (SSR) para ticker→curva | Botón de curva. Orden fijo por `duration` asc; excluye filas sin `last_price`. Los CER ya fijados aparecen ACÁ con badge `FIJ` | ninguna |
| **RENTA FIJA → CER** | Igual pero con VWAP en vez de Pago Final; sin TEM ni TC BE | ídem | ídem | ninguna |
| **RENTA FIJA → HARD DOLAR** | Curva `soberanos` (globales + bonares) | ídem | ídem | ninguna |
| **RENTA FIJA → DOLAR LINKED** | Curva `dolar_linked` | ídem | ídem | ninguna |
| **RENTA FIJA → TAMAR** | Los 18 bonos con pata TAMAR. Mismas columnas que TASA FIJA **+ MARGEN** (spread sobre la TAMAR del BCRA, que es lo que la mesa mira de estos bonos). Las tasas que no vienen del motor live llevan un `*` al lado de la TEA (tooltip con el porqué) — marca POR FILA, no por tabla | `GET /api/cotizaciones/curvas-vista`. La TEA y el margen NO salen del motor: los trae `jobs/tamar_1816` (cada 30′ 10-17 ART) a `mercado.tamar_1816` y `curvas_vista` los junta en la lectura. Ver `docs/RENTA_FIJA.md` paso 18 | Los mismos pills/emisor de la tab. La columna MARGEN aparece sola donde hay dato | ninguna |
| **RENTA FIJA → LIBRO** (**ventana flotante**, botón a la derecha del header ARS) | Time & sales intradía de UN bono; solo trades de HOY (sin fallback a sesión vieja). **No es una tab ni una pill**: abre una ventana que se arrastra y se redimensiona, por `createPortal` al body → la tabla y la curva ARS siguen enteras y visibles detrás. No es modal (sin backdrop, el fondo sigue clickeable); cierra con ✕ o **Esc**. Geometría persistida en `localStorage` (`rentaFija.libro.ventana`); abierto/cerrado NO se persiste | `GET /api/trades?instrumento=` → `/api/cotizaciones/historico/trades`, poll 5s con dedupe | Buscador/dropdown de instrumento. Universo = **el lado ARS entero** (no una pill: el tape se busca por ticker), deduplicado por instrumento y limitado a los que tienen `last_price`. Respeta el filtro de EMISOR → con el default (SOBERANO) son los soberanos ARS | ninguna |
| | ⚠ Se había **perdido** en la migración a la tab CURVAS (el componente quedó vivo y sin montar); se repuso el **2026-08-18** y en el mismo día pasó de pill a ventana flotante — ver `docs/RENTA_FIJA.md` paso 19 | | | |
| **CURVAS → filtro de TEA** (botón `TEA ≥`, a la DERECHA de la fila de EMISOR) | Piso de tasa: deja solo los bonos que rinden de ahí para arriba. Presets 5/7/10/15/25/35/45/60 % + input libre. Aplica a **las dos columnas, la tabla, el gráfico, los contadores de las pills y el universo del LIBRO** — una sola fuente, así la pantalla no puede contradecir a sus propios controles | ninguno: filtra **client-side** sobre los bonos que ya trae `curvas-vista` (el emisor también) | ⚠ Es **GLOBAL y las dos escalas NO son comparables** (ARS 30-60 %, USD 5-15 %): un mismo piso no filtra nada a la izquierda y sí a la derecha. Decisión tomada a conciencia — por eso el botón MUESTRA el número activo. Los bonos **sin TEA o con `tasa_ruido`** quedan afuera y se cuentan aparte (`−N` en el botón): "no llega al piso" y "no se puede comparar" no se muestran igual. NO se persiste | ninguna |
| **CURVAS → FICHA DEL BONO** (**modal**, click en cualquier fila de la tabla) | Cronograma de pagos: **gráfico de barras apiladas** (amortización + interés por 100 VN, los pagos vencidos apagados) + **tabla del cronograma** (fecha, amortización, interés, total, residual) + **bloque de tasas y riesgo por PATA** (TEA/TNA/TEM, duration, mod dur, convexity, paridad, margen s/TAMAR, TC BE, con su procedencia LIVE vs 1816) + **ficha** (emisor, industria, tipo, moneda, ajuste, ley, emisión/vto, VN, cupón, CER de emisión). Toggle SOLO FUTUROS (def. ON). Cierra con ✕, Esc o click afuera | `GET /api/cotizaciones/bono/{ticker}` (TTL 30s) → `api/services/bono_detalle.py` | El toggle SOLO FUTUROS. Un **dual** muestra sus DOS patas, cada una con su tasa: mostrar una sola es el bug que la tabla ya resolvió | ninguna |
| | ⚠ El cronograma lo arma el backend con la **MISMA función que el motor usa para la TEA de la fila** (`rama_calculo` + su `monto_flujo_*`). El master guarda los flujos con **tres shapes distintos** y el nombre del campo no alcanza para distinguirlos: sumar `amortizacion + interes` para todos daría **CERO** en soberanos y CER — un gráfico vacío que parece un dato. Congelado por `tests/unit/test_bono_detalle.py` | | | |
| **CURVAS → LIVE** | Scatter TEA/TEM/TNA vs Duration + línea de tendencia **log ajustada sobre los puntos live** | `soberanos`: `GET /api/analitica/listar-curva?curva=soberanos` (duration Macaulay real). Resto: `snapshot-live` + `flujos` (duration = años al vto) | Curva (TASA FIJA/CER/HARD DOLAR/DOLAR LINKED); métrica TEA/TEM/TNA **solo en tasa_fija** (CER y soberanos se fuerzan a TEA) | ninguna |
| **CURVAS → HISTÓRICO** | Misma curva a una fecha pasada | `GET /api/historico-curva?curva=` (lazy, cacheado por curva en el cliente) | **Slider de FECHA** (default = la más reciente); se resetea al cambiar curva o modo | ninguna |
| **CURVAS → FAIR VALUE** (solo tasa_fija y cer) | Tabla rankeable: TICKER, DUR, TEA, TEA TEÓRICA, RES bps, Z EST, **Z TEMP** (coloreada ±0.5/±1.5), N. Header con β cierre, R², σ, universo. El scatter+cuadrática está **deshabilitado por pedido del user** | `GET /api/cotizaciones/fair-value?curva=` (SSR + poll **90s**); click en fila → modal `fair-value/historico?ticker&dias=60` | Orden por click en header; los Z ordenan por **valor absoluto**. Cambiar a soberanos/DL cae a LIVE sin perder estado | ninguna |
| **FORWARDS → LIVE** | Matriz triangular de forwards implícitos | `snapshot-live` (bloque `forwards`) | Curva TASA FIJA / CER | ninguna |
| **FORWARDS → GRÁFICO** | Serie histórica de los pares elegidos | `GET /api/cotizaciones/historico/forwards` (SSR, TTL 300) | Curva + **multi-select de PARES** con buscador y toggle ✓ (default: los 2 primeros). Deshabilitado sin histórico | ninguna |
| **FORWARDS → Z-SCORE** | Matriz coloreada por z = (fwd hoy − media 30d)/desvío 30d + tooltip | `GET /api/cotizaciones/forwards-zscore` (SSR + poll **5 min**) | Curva. Pares con n_obs<20 o desvío≈0 van "n/d" | ninguna |
| **BREAKEVENS → LIVE** | LECAP/BONCAP · CER · IPC MES · DÍAS · BE MEN. + chart BE mercado vs REM mensual vs REM promedio acumulado | `snapshot-live` (bloque `breakevens`); `GET /api/cotizaciones/rem/breakeven-acumulado` | Toggle **REM** (def ON). **Filtro hardcodeado en el front: solo pares que vencen en 2026** (pedido de la mesa) | ninguna |
| **BREAKEVENS → HISTÓRICO** | Los mismos pares a una fecha pasada | `GET /api/cotizaciones/historico/breakevens` (SSR, TTL 300) | Slider de FECHA + toggle REM | ninguna |

> **La vista ONs (`/ons`) fue ELIMINADA el 2026-08-16.** No se reemplazó por otra
> pantalla: dejó de tener sentido cuando `on_energia`/`on_finanzas`/`on_otros`
> dejaron de ser curvas. Eran el SECTOR del emisor metido dentro del nombre de la
> curva, y con el modelo de ejes ser corporativo pasó a ser `emisor_tipo` — o sea
> que una ON en USD a tasa fija se mira **en la tabla HARD DOLAR de `/renta-fija`,
> al lado de los soberanos**, que es contra quién se compara su rendimiento.
> Se fueron con ella (ya no existen): `ons-live.tsx`, `/api/analitica/ons-calendario`,
> `listar-curva?curva=on` y la vista `ons` del copiloto. Los ~140 corporativos
> **siguen en `mercado.curvas`** alimentando `/renta-fija` y ACREENCIAS; se editan
> desde Manager → TÍTULOS · BONOS.

### Vista: MANAGER → TÍTULOS (parte de RF/ONs)
- **Gate**: `_TITULOS` = `manager` ∨ `manager_titulos`; el sub-tab INSTRUMENTOS usa `_INSTRUMENTOS` (+ `manager_instrumentos`).
- `asistente_comercial` tiene `manager_instrumentos` pero **NO** `manager_titulos` → ve INSTRUMENTOS, no ve BONOS/BREAKEVENS (el front replica el gate con `canMaestro`).

| Sub-tab / cuadrante | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **BONOS → Ver/editar** | Maestro: Ticker · ROFEX · Curva · Tipo · Vto · Mon · VN · Cupón · Flujo (expandible). Rojo si falta flujo o vto | `GET /api/manager/bonos` | Buscador de texto + select de curva | **DELETE `/bonos?ticker_corto=`** (con confirm). "Editar" prefilla el alta |
| **BONOS → CARGAR** | Editor unificado con **selector de forma** *Soberano/Provincial* / *Corporativo (ON)*; avisa si el código ya está cargado. Siguen siendo dos forms porque un corporativo se carga con otros campos y su write path todavía escribe `curva='on_<sector>'` (el motor de TEA ramifica por ahí, `engines/curvas.py:595`) | `GET /bonos`, `GET /ons` | Pill Soberano/Provincial · Corporativo | ver abajo |
| ↳ **alta BONO** | Form por **tipo**: lecap, boncap, tasa fija c/cupón, CER, dual/TAMAR, soberano USD, dólar linked. Cada tipo habilita sus campos y la shape de flujo (bullet = solo `flujo_vencimiento`; resto = array). Preview del cronograma pegado | `POST /bonos/parse-flujos`, `GET /bonos` | tipo de bono | **POST `/bonos`** (upsert por `ticker_corto`), **DELETE**. `parse-flujos` solo transforma texto |
| ↳ **alta ON** | Form: Asset, Emisor, Moneda flujo (USD/DL/ARS), **Sector**, Tasa cupón, Vencimiento, ticker ARS y USD (el `MERV - XMEV - … - 24hs` va fijo). Flujos por **archivo (.csv/.txt)** o pegados; preview con Σ amortización y check ≈100 | `GET /ons`, `POST /ons/parse-flujos` | select "editar existente", select de sector | **POST `/ons`** (upsert por `asset`, escribe directo a `mercado.curvas` con curva `on_<sector>` → se refleja en `/ons` sin reiniciar motores) |
| **BONOS → BONOS (listado)** | **TODOS** los bonos del master (corporativos incluidos, 2026-08-16). Columnas: Ticker · ROFEX · **Clasificación** (los EJES: emisor · moneda · ajuste `+ ajuste_alt` del dual · ley) · Emisor · Vto · VN · Cupón · Flujo (expandible). La columna CURVA se sacó: dejó de decidir nada. Banner ámbar con cuántos están **sin clasificar** (no entran a ninguna tabla de RF) y cuántos tienen ajuste **sin curva** (badlar/tpm/caución) | `GET /bonos` | buscar ticker/emisor · **3 selects por EJE** (emisor / moneda / ajuste — el de ajuste matchea las DOS patas, así un dual sale al filtrar por TAMAR) · pill «solo problemas» | **DELETE `/bonos`** (ya borra corporativos: el guard `curva NOT LIKE 'on%'` hacía que la baja dijera OK sin borrar nada) |
| **BONOS → Conciliar (títulos sin flujo)** | Gap: títulos ARS/DL/HD que los clientes tienen y no están (o están incompletos) en el maestro, con la acción sugerida | `GET /bonos/sin-flujo`, `GET /ons/ignoradas` | — | **POST `/ons/ignorar`**, **DELETE `/ons/ignorar?ticker=`** |
| **BONOS → Errores de tasa** | Bonos con precio pero sin TEA (los que muestran "--") | `GET /bonos/sin-tasa` (read-only, no recalcula) | — | **POST `/jobs/run`** `{tipo:"backfill_tasas"}` + polling. Ese endpoint es del sub-router `jobs` (umbrella `manager`) → un `asistente_comercial` **no podría dispararlo** |
| **BREAKEVENS** (50/50) | **Izq — PARES**: los del motor + los MANUALES (marcados `✎`), con flag `excluido` (fila al 40 %). BE fuera de [0, 15 %] en rojo. **Der — COBERTURA**: por qué CADA bono `tasa_fija` del master entra o no a la matriz (motivo textual: sin CER a ±20d, dedup, plazo mínimo, IPC ya publicado) + inventario del master + frescura del doc publicado | `GET /breakevens/pares`, `GET /breakevens/diagnostico`, `GET /breakevens/candidatos` | ↻ refresh; checkbox "solo los que NO entran" (der) | **POST `/breakevens/exclusion`** — excluir oculta el par de `/renta-fija` **al instante** (se filtra en la lectura pública; el motor no se toca). Update optimista con rollback. **POST `/breakevens/manual`** — el botón **`+ par manual`** (dos selects: tasa fija ↔ CER) crea un par que el motor NO arma; el BE se calcula en la lectura, así que aparece en `/renta-fija` sin reiniciar nada. Los manuales se **borran** (no se excluyen) |

#### Endpoints — `analitica.py` (`/api/analitica`, `_PUBLIC`)
| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/listar-curva` | Instrumentos de una curva con precio/TEA/TEM/paridad/duration/mod_duration/convexity/volumen del día. `tasa_fija` agrega `tc_breakeven` | `curva` (**req**: `cer`\|`tasa_fija`\|`tamar`\|`soberanos`\|`dolar_linked`\|`dual`), `ordenar_por` (`vencimiento`\|`volumen_dia`\|`tea`\|`duration`), `vencimiento_min_meses`, `vencimiento_max_meses`, `limit` | No |
| GET | `/serie-macro` | Serie de una variable macro | `variable` (**req**: `tamar\|cer\|dolar\|badlar\|mep\|ccl\|canje\|ipc\|ipim\|riesgo_pais\|repo\|rem_inflacion` o `<TICKER>.<CAMPO>`), `ventana_dias` (1-3650, def 90) | No |
| GET | `/clasificar-nivel` | Clasifica el nivel actual de una variable vs su ventana | `variable` (**req**), `ventana_dias` | No |
| GET | `/snapshot-curva-historico` | Reconstruye la curva a un cierre. Aplica **live fallback** | `curva` (**req**), `fecha` (**req**) | No |
| GET | `/pendiente-curva` | Pendiente (anchor corto vs largo) | `curva` (**req**), `metrica` (`tea`\|`tem`\|`duration`), `fecha_comparacion`, `dias_min_corto` (0-365, def 30) | No |
| GET | `/sensibilidad-retorno` | Upside de PRECIO por escenario de TIR (capital-only, sin carry). Consumido por RESEARCH → ANÁLISIS SENSIBILIDAD (ex `/retorno`) | `curva` (def `soberanos`), `tirs` (CSV %, def `4..11`), `horizonte_dias` (0-1095), `modo` (`absoluta`\|`relativa`), `tipos` (CSV) | No |
| GET | `/canje` | Serie del canje CCL/MEP intra-bono (`precio_C/precio_D − 1`) | `par` (def `AL30`), `desde`, `hasta` | No |
| GET | `/carry-trade` | Carry en USD = `(1+ret_ars)/(1+var_dolar) − 1` | `curva`, `desde`, `hasta`, `dolar` (`mep`\|`ccl`) | No |
| GET | `/retorno-total` | Precios diarios de la curva + series MEP/oficial (el front calcula los retornos) | `curva` (**req**) | No |
| GET | `/simular-inversion` | Simulador del modal SIMULAR INVERSIÓN (/renta-fija): corre el motor (`calcular_campos`) con el precio inyectado → TEA/TNA/TEM/duration/paridad + cronograma escalado (`vn = importe × 100 / precio`); CER ajustado (proyección constante con flag) | `ticker` (**req**, corto), `importe` (**req**, >0), `precio` (opcional; def last del snapshot) | No |
| POST | `/estrategia-historico` | Serie intradía del costo de una estrategia de opciones (dominio DERIVADOS) | `legs[]` (1-8), `bucket_min` (1-240), `desde`, `hasta` | No (cálculo) |

#### Endpoints — `cotizaciones.py` (`/api/cotizaciones`, `_PUBLIC`) — 33 rutas
`renta-fija` (snapshot live por instrumento) · **`snapshot-live`** (bundle `{renta_fija, forwards,
breakevens}` en una respuesta, leyendo el cache TTL de cada service: 5s/30s/30s) · `forwards` ·
`historico/forwards` · `forwards-zscore` (media/desvío/n_obs por par; el front calcula el z en cada
tick; se refresca 1×/día post-cierre) · `breakevens` · `historico/breakevens` · **`fair-value`** (β del
último cierre + TEAs vivas → residuos + `z_estatico` recomputado; `z_temporal` viene del cierre) ·
`fair-value/cierre` · `fair-value/historico` (`ticker` completo, `dias` 1-365) · `historico/curva`
(cierres + live fallback de hoy) · `historico/trades` · `rem` · `rem/informes` ·
`rem/breakeven-acumulado` · `rem/debug` · `cer` · `badlar` · `dolar` · `mep` (TTL 5s) ·
`historico/mep` · `historico/dolares` · `argy` · `caucion` · `historico/caucion` · `futuros-dlr` ·
`historico/futuros-dlr` · `opciones` · `opciones/meta` · **`PUT /opciones/tasa`** (única escritura;
gate inline `require_module("manager")`; el proxy Next es GET-only → no se llama desde el front) ·
`historico/opciones` · `vr-ggal` · `griegas/opciones`.

#### Endpoints — `titulos.py` (`/api/titulos`, `_PUBLIC`)
| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/assets` | Catálogo normalizado (`unidad, ticker, emisor, cartera, clase_activo, calificacion, vencimiento`). `@cached(600)`. **Sin consumidor en el front** | `unidad`, `ticker`, `cartera`, `emisor`, `clase_activo` (match exacto, filtrado en Python) | No |
| GET | `/flujos` | Flujos + `cer_fijado` (bool) y `curva_efectiva` (`tasa_fija` si está fijado). `@cached(60)` — bajado de 600s porque el set de fijados cambia a diario | `ticker` (corto), `curva`, `moneda_flujo` | No |

#### Endpoints — Manager `ons.py` / `bonos.py` / `breakevens.py` (gate `_TITULOS`)
| Método | Path | Qué hace | Escribe |
|---|---|---|---|
| GET | `/api/manager/ons` (`sector`, `emisor`) · `/ons/values` · `/ons/conciliar` · `/ons/ignoradas` | Lista, valores de selects, gap de cobertura HD/DL, ignorados | No |
| POST | `/api/manager/ons` | Upsert por `asset` en `mercado.curvas` (curva `on_<sector>`); registra `actor` | **Sí** |
| PATCH | `/api/manager/ons/sector` | Cambia la curva `on_<sector>` en vivo. **El front NO lo llama** (el sector va dentro del POST) | **Sí** |
| DELETE | `/api/manager/ons?asset=` | Baja de la ON | **Sí** |
| POST | `/api/manager/ons/parse-flujos` | Parsea flujos (formato BYMA/IAMC o simple), `texto` ≤100k | No |
| POST/DELETE | `/api/manager/ons/ignorar` | Marca/desmarca ticker ignorado (`mercado.ons_ignoradas`) | **Sí** |
| GET | `/api/manager/bonos` (`curva`) · `/bonos/sin-flujo` · `/bonos/sin-tasa` | Maestro no-ON, conciliador unificado, bonos con precio sin TEA | No |
| POST | `/api/manager/bonos/parse-flujos` | Parsea flujos **ya en la shape del tipo** (soberano/CER → `*_pct`; tasa fija → absolutos) | No |
| POST | `/api/manager/bonos` | Upsert por `ticker_corto` (`ticker`, `curva`, `tipo`, `moneda_flujo`, `tasa_referencia`, `fecha_emision`, `fecha_vencimiento`, `valor_nominal`, `cer_emision`, `cupon_anual`, `flujo_vencimiento` o `flujos[]`) | **Sí** |
| DELETE | `/api/manager/bonos?ticker_corto=` | Baja | **Sí** |
| GET | `/api/manager/breakevens/pares` | Pares del motor **+ los manuales** (`manual: true`) + `excluido` + `n_excluidos` + `n_manuales` | No |
| POST | `/api/manager/breakevens/exclusion` | Excluye/reincluye un par (`lecap`, `cer`, `excluir`) | **Sí** |
| GET | `/api/manager/breakevens/candidatos` | Bonos `tasa_fija` y `cer` del master para los dos selects del `+` (con `apto`: si tiene el campo que el BE necesita) | No |
| POST | `/api/manager/breakevens/manual` | Crea/borra un par manual (`lecap`, `cer`, `agregar`). Valida las curvas server-side | **Sí** |
| GET | `/api/manager/breakevens/diagnostico` | Cobertura: por qué cada bono entra o no a la matriz + master + frescura. Mismo dato que `scripts/diag_breakevens_cobertura.py` | No |

#### Fuentes de datos
`mercado.curvas` (maestro único: bonos no-ON **y** ONs `curva LIKE 'on%'`; BondsMaster retirado en la
Fase 3, 2026-06-22) · `mercado.market_snapshot` (live de `motor_curvas`/`motor_rofex`) ·
`mercado.snapshots_cierre(+_hist)` (`jobs.snapshot_cierre`, 20:25 UTC L-V) · `mercado.mercado_hist`
(forwards y breakevens live+histórico) · `mercado.forwards_zscore` (`jobs/forwards_zscore.py`, 1×/día) ·
`mercado.fit_params` + `mercado.fair_value_residuos` · `mercado.timesales` · `mercado.ons_ignoradas` ·
`macro.series_macro` (`jobs.bcra --today` 22 UTC pide hoy+21d para el CER forward; `jobs.argentina_datos`
12 UTC) · `macro.rem` · `portafolio.tenencia`(`aum='si'`) + `portafolio.assets` (conciliador).

#### Notas / rarezas
- **El gate real de `/renta-fija` y `/ons` no es `renta-fija`**: los 3 routers son `_PUBLIC`. El único gate por módulo de esa superficie es el `require_module("manager")` inline del `PUT /opciones/tasa`.
- **`ENDPOINT_MODULE_PREFIXES` mapea `/api/titulos → portfolios` pero eso NO se aplica** — `get_module_for_path` solo se usa en `tests/unit/test_rbac.py`. Histórico: `titulos.router` estaba bajo `_PORTFOLIOS` y para rol `sales` daba 403 → `/renta-fija` quedaba en "MERCADO CERRADO" porque `allFlujos` venía vacío; se movió a `_PUBLIC` y el comentario quedó sin actualizar.
- **CER "fijado" migra de pestaña solo**: un bono CER cuyo CER de liquidación (vto − 10 hábiles) ya publicó el BCRA se comporta como tasa fija y el front lo muestra en TASA FIJA con badge `FIJ`. El TTL bajó de 600s a 60s justo por esto.
- **La línea del chart CURVAS en LIVE NO es la cuadrática del fair value** — es un fit logarítmico local sobre los puntos live. Se cambió porque el fit del cierre quedaba congelado y se disparaba a −80 % cuando ese cierre tenía bonos con flujos malos.
- **En LIVE la "duration" del eje X no siempre es duration**: para `soberanos` se pide `listar-curva` (Macaulay real); para las otras curvas se usa años-al-vencimiento.
- **Breakevens filtra a 2026 hardcodeado en el frontend**. El backend devuelve todos.
- **`/ons` solo muestra ONs con volumen operado HOY** (tabla y scatter). Una ON del maestro sin trades del día no aparece, aunque sí puede estar en el CALENDARIO.
- **El scatter de ONs esconde outliers a propósito** (mediana ± 5·MAD, banda mínima ±3 pts).
- **El sub-tab "ONs" de Manager → TÍTULOS se eliminó el 2026-07-09**; el `PATCH /ons/sector` sigue vivo en el backend pero el front no lo llama.
- **`snapshot-live` pierde los timestamps individuales** de cada bloque: la UI muestra un solo "actualizado a las HH:MM:SS" aunque forwards y breakevens vengan del cache de 30s.
- `RentaFijaLiveView` y `FairValueView` envuelven `initial` en `useMemo` **obligatoriamente**: sin eso `usePoll` dispara un `setState` por render → loop infinito (React #185). Comentado como ⚠ MUST.
- **SIN VERIFICAR**: qué escribe exactamente `mercado.fair_value_residuos` y la cadencia de su job (no se leyó `jobs/fair_value*.py`).

---

## 4.3 RENTA VARIABLE (Scanner) y TRADING

### Vista: SCANNER DE CEDEARS (ruta frontend: `/renta-variable`)
- **Módulo RBAC**: `renta-variable` (gate propio del router: `dependencies=[Depends(require_module("renta-variable"))]`; en `main.py` va con `_PUBLIC`). | **Roles**: admin, trader, sales, asistente_comercial, invitado. NO `back_office`.
- **Archivos front**: `scanner-view.tsx` → `cedears-scanner-table.tsx`, `metricas-panel.tsx` (→ `pivot-points-panel.tsx`, `retornos-chart.tsx`), `ticker-chart-panel.tsx`. **Proxy Next `/api/scanner/[...path]` es solo GET** → cero escrituras posibles desde la vista.
- La vista NO tiene shell de tabs propio (la tab ANÁLISIS FUNDAMENTAL se eliminó el 2026-07-24: el Scanner ES toda la vista). Layout: izquierda tabla; derecha 50 % MÉTRICAS / 50 % CHART & RETORNOS.

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| (Izq) Tabla CEDEAR/ADR | Switch **CEDEAR** (ARS live: LAST, INTRA %, 1D %, **USD %** —retorno real descontando CCL—, VWAP, SPREAD %, VOL) y **ADR** (USD EOD: LAST, 1D, 7D, 15R, MTD, YTD, badge CIERRE si no operó hoy). KPI CCL inline | `GET /api/scanner/cedears` (poll 2s), `GET /api/scanner/ccl` (5s) | Switch CEDEAR/ADR; **buscador** de texto (ticker o nombre, client-side); **orden** por click en CUALQUIER header (default INTRA desc / 1D desc); **chip de RUBRO** (lo setea el PULSO, se limpia con ✕) | Ninguna |
| MÉTRICAS → **PULSO** (def.) | Pulso por RUBRO: retornos del ADR (1D/WTD/15R/MTD/YTD) ponderados por volumen USD + breadth ▲/▼ + $VOL | recalcula client-side sobre `/cedears` | Orden por RUBRO/$VOL/1D/WTD/15R/MTD/YTD/breadth (def $VOL); toggle **A.I** (filtra `es_ia=true`); click en rubro filtra la tabla | Ninguna |
| MÉTRICAS → **PIVOTS / VOL** | Sub-tab **ZONAS** (pivots del subyacente USD en 4 timeframes) y **VOLATILIDAD & BETA** (beta/alpha/corr vs SPY y QQQ + vol 30d/60d + zscore) | `GET /pivot/{ticker}` (60s), `GET /quant/{ticker}` (lazy) | Timeframe **DIARIO / SEMANAL / MENSUAL / ANUAL** | Ninguna |
| MÉTRICAS → **RETORNOS** | Retorno DIARIO a lo largo del tiempo | `GET /returns/{ticker}` | Ventana **1M(21) / 3M(63) / 6M(126) / 1A(252, def) / TODO** (recorte client-side) | Ninguna |
| CHART → **HISTÓRICO** | TradingView Advanced Chart locked al ticker (sin búsqueda de symbol, velas diarias, sigue el tema) | ninguno del backend | — | Ninguna |
| CHART → **RETORNOS DIARIOS** | Histograma SVG de los retornos del último año (~252) + media + σ + marca del último día | `GET /returns/{ticker}` (campo `returns`, lazy) | — | Ninguna |

#### Endpoints — `scanner.py` (`/api/scanner`, gate `renta-variable`; además en `GUEST_PATH_PREFIXES`)
| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/cedears` | Master + snapshot live joineado: `ticker_corto, nombre, underlying, ratio_cedear, sector, rubro, es_ia, industria, region, pais, last, open, high, low, close, bid, offer, vwap, volume, total_money, spread_pct, intraday_pct, vs_1d_pct, vs_1d_usd_pct, adr_*` (`@cached ttl=2`) | — | No |
| GET | `/ccl` | CCL live + var 1D. Cae al último cierre si el snapshot tiene >60s (`ttl=5`) | — | No |
| GET | `/cedears/trades` | Tape intradía del CEDEAR, hora ARGENTINA naive. **SIN CONSUMIDOR en el front** | `ticker` (req), `limite` (def 200) | No |
| GET | `/cedears/intraday` | Serie por minuto desde el tape de hoy. **SIN CONSUMIDOR en el front** | `ticker` (req) | No |
| GET | `/returns/{ticker}` | Retornos diarios aritméticos del subyacente USD (`ttl=60`) | path | No |
| GET | `/quant/{ticker}` | `beta/alpha/corr` vs SPY y QQQ, `vol.d30/d60`, `zscore.d30/d60`, `n_observations` (ventana 60) (`ttl=60`) | path | No |
| GET | `/pivot/{ticker}` | Pivots en 4 timeframes; el `last` se pisa con el live del ADR (`last_source: live\|eod`) (`ttl=60`) | path (resuelve `ticker_corto → underlying`, ej. YPFD→YPF) | No |
| GET | `/day-trading` | **ADMIN-ONLY**. Ranking intradía para scalping: vueltas zigzag ≥ objetivo, rango del día, posición en el rango, momentum 15', vs VWAP, spread, flujo comprador, minutos sin operar, costumbre (~20 ruedas) e `idea {lado, motivo}` (`ttl=15`). **SIN CONSUMIDOR en el front** | `objetivo` % (def 0.5, clamp 0.1–5) | No |
| GET | `/companeros/{ticker}` | **ADMIN-ONLY**. Correlación diaria: top `n` que acompañan y top `n` que van al revés (`ttl=300`). **SIN CONSUMIDOR en el front** | path, `n` (def 6, 1–15) | No |

`require_admin` **no mira la role_matrix**: exige `get_user_role(email)=="admin"` de forma dura y
rechaza siempre al portal invitado. No es delegable desde el panel.

#### Fuentes de datos
`mercado.cedears` (master, `activo IS TRUE`) · `mercado.cedears_snapshot` (`engines/motor_cedears.py`,
~1s, L-V 13:20–20:05 UTC) · `mercado.cedears_time_sales` (se VACÍA al cierre:
`jobs/cleanup_cedears_timesales.py` 23:50 UTC) · `mercado.adr_snapshot` (`jobs/adr_live.py` cada 15') ·
`mercado.precios_acciones` (`jobs/precios_acciones_daily.py` 22 UTC — base de returns/quant/pivots/anchors)
· `mercado.day_trading_stats` (`jobs/day_trading_stats.py` 20:06 UTC) · CCL vía `core.dolar_sql`
(**SIN VERIFICAR** los nombres exactos de tabla) · correlaciones de `/companeros` vía
`rv_motor.get_correlation_matrix()` (252 ruedas).

#### Notas / rarezas
- **4 endpoints del scanner no tienen consumidor en el frontend**: `/cedears/trades`, `/cedears/intraday` (el tape de TRADING usa los gemelos `/api/trading/*` que además resuelven bonos) y los dos admin-only `/day-trading` y `/companeros` — la vista `/trade-lab` que los alimentaba **no existe** en el checkout (solo quedan comentarios que la mencionan). Backend vivo, UI muerta.
- `vs_1d_usd_pct = ((1+vs_1d/100)/(1+ccl_1d/100)−1)×100` — descuenta la devaluación implícita del CCL.
- Timestamps del tape en **hora argentina naive a propósito** (bug 2026-07-14: se emitían naive-UTC y el tape mostraba +3h).
- `es_ia` no es columna: es el filtro "A.I" del PULSO.
- La Mesa de Estrategia HTTP (`/correlaciones`, `/trade-analysis`, `/book-analysis`) se **eliminó del router** el 2026-07-13 y sus services quedaron vivos solo porque los usaba el MCP. Con el MCP borrado (2026-08-28) se fueron también `rv_motor.get_trade_analysis()` y `get_book_analysis()`. Sobrevive **`get_correlation_matrix()`**, que NO era MCP-only: la usa `day_trading.py` para `GET /api/scanner/companeros/{ticker}`.

### Vista: TRADING (ruta frontend: `/trading`)
- **Módulo RBAC**: `trading` — **solo `admin`** en el default. Es módulo normal (no `require_admin`) → un admin PODRÍA delegarlo desde el panel. **SIN VERIFICAR** si en prod está asignado a otro rol.
- **Archivos front**: `trading-shell.tsx` → `trading-view.tsx`, `intraday-view.tsx`, `pnl-historico-view.tsx`; sub-componentes `trading-radar-panel.tsx` (→ movers / volumen / pivot-radar / estrategia), `live-intraday-chart.tsx`, `adr-zonas-chart.tsx`, `order-book-panel.tsx`, `cedears-scanner-table.tsx` (reusada en modo `compact`). Proxies: `/api/trading/[...path]` (GET+POST), `/api/estrategia/[...path]` (GET only).
- **Routers**: `trading.py` + `estrategia.py`, ambos con `_TRADING = [verify_api_key, require_module("trading")]` (los routers no declaran gate propio).

| Tab / bloque | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **PIVOTS** (toolbar) | KPIs: CCL, SPY y QQQ (CEDEAR ARS) y SPY ADR / QQQ ADR (USD, feed Reuters) | `GET /api/scanner/ccl`, `/api/scanner/cedears`, `GET /api/research1816/reuters` (poll 5s) | Toggle de modo de los niveles: **PRECIO / DIF $ / DIF %** | Ninguna |
| **PIVOTS** → 6 cards | Grilla **fija de 6 slots** (2×3). Por card: selector de activo, `last` + `vwap` live, **máx/mín/cierre EDITABLES** y los 7 niveles R3→S3 (Floor Trader, recalculados en el cliente si hay override) | `GET /api/trading/pivots?tickers=CSV` (4s), `GET /api/trading/universo` (1 vez) | **Selector/buscador por card** (typeahead sobre CEDEARs + bonos, tope 30, badge BONO) | **Escritura solo LOCAL**: `localStorage` `trd-fx-trading-pivot-cards-v2` (los 6 tickers) y `trd-fx-trading-pivot-overrides-v1` (overrides, con botón "editado ↺") |
| **PIVOTS** → Order book | Libro comprimido del activo | `GET /api/operar/order-book` (**router `operar.py`, otro módulo**; 202 = suscribiendo) | plazo | Ninguna |
| **RADAR → MOVERS ±4%** | Tabla del Scanner en modo `compact` filtrada a \|intradía\| o \|1D\| ≥ 4 % | `/api/scanner/cedears` (2s), `/ccl` (5s) | Umbral **FIJO en 4 %** (constante, sin selector); switch CEDEAR/ADR y orden siguen disponibles | Ninguna. Click → carga el ticker en la primera card vacía |
| **RADAR → VOLUMENES ACCIONES** | Top 30 por **CASH** (`total_money`, no nominal), con barra proporcional al líder y Σ cash del universo | `/api/scanner/cedears` (2s) | Ninguno (top 30 fijo) | Ninguna |
| **RADAR → PIVOTES** | De TODO el universo, los que tienen el `last` pegado a un nivel (PP/R1..R3/S1..S3): Ticker, Last, Nivel, Precio, Dist ↑/↓ % | `GET /api/trading/pivot-radar` (2s) | **Umbral 0.05 % / 0.1 % / 0.2 % (def) / 0.5 %** — filtra en el cliente, NO re-pega | Ninguna |
| **RADAR → ESTRATEGIA** | Contexto determinista por ticker: Last, ATR %, ER 30, ER día, chip **CHOPPY / MIXTO / LIMPIO** | `GET /api/estrategia/contexto` (10s) | Ninguno (universo FIJO `config.ESTRATEGIA_CONTEXTO_TICKERS` = QQQ, SPY, SNDK, NVDA, RKLB) | Ninguna |
| **PIVOTS** → chart LIVE | Precio intradía por minuto (área) con líneas de los 7 pivots + VWAP; si hay override, las líneas siguen la edición | `GET /api/trading/intraday?ticker` | Zoom/pan; auto-reencuadre si el usuario no interactuó | Ninguna |
| **PIVOTS** → chart ZONAS ADR | Velas diarias del ADR en USD + zonas del timeframe elegido | `GET /api/trading/adr-zonas?ticker&dias=400` | Timeframe **DIARIO/SEMANAL/MENSUAL/ANUAL**; ventana visible **7D/15D/30D/45D**; botón ⟲ | Ninguna |
| **INTRADAY** | Monitor FIFO del día: se sube el **CSV de boletos** (export ROFEX/Aunesa, **latin-1**) y consolida por (cuenta, especie): posición neta, precio ponderado, PnL realizado/no realizado, intereses+IVA, costo en book, detalle por posición y simulador en drawer | `POST /api/operaciones/intraday/{analizar,recalcular,marks}` (**módulo `operaciones`, NO `trading`**) | **Selector de cuenta**; **tilde por especie**; **tilde por trade individual** (re-FIFO en backend); **override manual de mark**; **multiplicador de contrato** (1 acción/CEDEAR, 100 derivado); simulador con escalones ±0.25/0.5/0.75/1/1.25/1.5/2 % | POSTs de cálculo **efímeros: NO persisten en DB**. Todo vive en sessionStorage/localStorage (`intraday_fifo_v2`, `intraday_excl_v1`, `trd-fx-intraday-posiciones-v1` que es el puente al copiloto) |
| **PNL HISTÓRICO** | Cuaderno **MANUAL** de PnL diario: días hábiles desde el 1-jul-2026 hasta fin del mes en curso, monto tipeado por día, acumulado total y mensual, subtotal por mes y 2 gráficos de línea | `GET`/`POST /api/trading/pnl-historico` | **Selector de cuenta** (etiqueta libre, def `General`; el GET devuelve la lista) | **SÍ ESCRIBE**: tipear un monto hace `POST` (upsert en `valuaciones.pnl_historico`); dejar la celda vacía **BORRA** la fila. **Sin allowlist propia** — el gate es el módulo `trading` |

#### Endpoints
**`trading.py`**: `GET /pivots` (`tickers` CSV; resuelve CEDEAR vs bono por el master de RF; devuelve
`{ticker,last,vwap,fecha,high,low,close,pivots{pp,r1..r3,s1..s3}}` o `{sin_datos:true}`) ·
`GET /trades` (CEDEAR → `cedears_time_sales`; bono → `timesales`) · `GET /intraday` ·
`GET /renta-fija` (radar tasa fija + CER por volumen; TNA = TEM×12, `ttl=4`) · `GET /pivot-radar`
(`ttl=2`) · `GET /adr-zonas` (`ticker` req, `dias` def 180 clamp 30–1825; velas + los 4 timeframes en
un hit; vive acá y no en scanner porque TRADING es su propio módulo) · `GET /universo` ·
`GET /pnl-historico?cuenta=` · **`POST /pnl-historico`** (`{fecha, monto: float|null, cuenta}`; `monto`
null → **DELETE** de la fila).

**`estrategia.py`**: `GET /live` (última evaluación por ticker, orden \|score\| desc, `ttl=10`) ·
`GET /track-record` (`dias` def 90, 1–365: `n`, `hit_rate`, `expectativa_pct`, `mfe_prom`, `mae_prom`,
curva de equity, `edge_factores`, flag de muestra mínima; solo señales RESUELTAS no-parciales, `ttl=60`) ·
`GET /senales` (`dias` def 30, `limite` def 200, `ttl=30`) · `GET /contexto` (`ttl=15`).

#### Fuentes de datos
`mercado.cedears_ohlc_daily` (`jobs/cedears_ohlc_daily.py` 20:15 UTC — lee el SNAPSHOT, no el tape) ·
`mercado.bonos_ohlc_daily` (20:16) · `cedears_snapshot` / `market_snapshot` (last y vwap live) ·
`cedears_time_sales` / `timesales` · `mercado.cedears_bars_1m` (20:20, insumo del Efficiency Ratio) ·
`mercado.precios_acciones` (`ttl=900`) · `mercado.curvas` · `mercado.dias_habiles` (arma las filas del
cuaderno) · **`valuaciones.pnl_historico`** (PK fecha+cuenta — **única tabla que esta vista escribe**) ·
`estrategia.senales` / `resultados` / `modelo_pesos` / `eval_live` (emisor único `engines/estrategia.py`,
restart 13:20 / stop 20:05 UTC; resolver `jobs/estrategia_resolver.py` cada 5' + pasada `--cierre` 20:10).
Config en `config.py`: `ESTRATEGIA_TICKERS` (16 papeles), `ESTRATEGIA_INDICES` (QQQ, SPY),
`ESTRATEGIA_SCORE_UMBRAL=40`, `COOLDOWN_MIN=15`, `HORIZONTES=[15,30,60]`, `OBJETIVO_PCT=0.5`,
`STOP_PCT=0.5`, `ER_CHOPPY=0.30`.

#### Notas / rarezas
- ~~**`/api/trading/renta-fija` no tiene consumidor**~~ **— ya se borró.** El comentario decía literal "(RENTA FIJA se removió — no se usa)".
- **`/api/estrategia/live`, `/track-record` y `/senales` tampoco tienen consumidor**: la tab ESTRATEGIA del radar consume SOLO `/contexto`. **Toda la zona LIVE / TRACK-RECORD / auditoría del ledger existe en backend y no está expuesta en UI.**
- Los pivots se calculan **dos veces**: el backend en `/pivots` y el frontend (`calcPivots`) cuando el usuario edita máx/mín/cierre. Si divergieran, el chart mostraría líneas distintas a las cards.
- Los overrides **nunca llegan al backend como estado persistido**: viven en localStorage y se mandan como *parámetro* al copiloto y al vigía. Cambiar de browser pierde la edición.
- La grilla de cards es de tamaño FIJO (`SLOTS = 6`); defaults RKLB / SNDK / ASTS + 3 vacías.
- El vigía se debounce a 500 ms con payload memoizado: sin eso, cada tecla en un input disparaba un POST.
- Un usuario con `trading` y **sin** `renta-variable`/`research` ve el chart ZONAS pero los KPIs y los radares MOVERS/VOLUMENES quedan vacíos (403 silencioso). Hoy no se nota porque solo `admin` tiene `trading`.
- La tab INTRADAY es la única con carga de archivo y su cálculo es 100 % efímero.

---

## 4.4 DERIVADOS, SINTÉTICOS y AGRO

Tres vistas top-level independientes. Históricamente `/derivados` era el paraguas; Agro y Sintéticos se
promovieron a módulos propios (`agro`, `sinteticos`) pero **las URLs del backend siguen bajo
`/api/derivados/*`** por compatibilidad con la frontend deployada.

### Vista: DERIVADOS — OPCIONES (`/derivados`)
- **Módulo RBAC**: `derivados` | **Roles**: admin, trader, sales, asistente_comercial, invitado.
- **Archivos front**: `derivados-shell.tsx` (wrapper vacío), `derivados-view.tsx` (448 líneas, la vista real), `opciones-table-compact.tsx`, `estrategias-tabla.tsx`, `payoff-chart.tsx`, `escenarios-tabla.tsx`, `post-trade-lab.tsx`, `costo-historico-chart.tsx`, `opcion-historico-chart.tsx`, `griegas-historico-chart.tsx`, `src/lib/estrategias.ts` (templates + cálculo client-side).
- **Router**: no tiene propio — usa `cotizaciones.py` (bloque Opciones) y `analitica.py` (`POST /estrategia-historico`). Services: `opciones_sql.py` (lectura), `opciones.py` (escritura de tasa + helpers de costo).
- **Propósito**: chain de opciones de **GGAL** (único subyacente cableado: el panel se titula "OPCIONES GGAL").

| Panel → tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| Izq. sup. **CALL** | Chain de calls: STRIKE, LAST, INTRA, 1D, SPREAD PUNTAS, IV, DELTA, GAMMA, THETA, VEGA, VOL (ordenada por `ev` desc) | `GET /api/cotizaciones/opciones` (poll 30s) | filtro CALL/PUT/ESTRAT. | — |
| Izq. sup. **PUT** | Ídem puts | mismo | mismo | — |
| Izq. sup. **ESTRAT.** | Estrategias generadas client-side desde `STRATEGY_TEMPLATES`, con costo neto, comisión y patas | mismo (calcula sobre `docs`) | selector **categoría** (7: Spread Alcista, Spread Bajista, Cono/Cuna, Ratio, Backspread, Cóndor de Hierro, Venta de Vol) + selector **strike** (de `liquidStrikes`, def ATM) | — |
| Izq. inf. **COSTO HISTÓRICO** | Con contrato: serie intradía del contrato. Con estrategia: costo en buckets de 15 min. 2º eje con spot GGAL local/ADR | `GET /historico/opciones`, `POST /api/analitica/estrategia-historico`, `GET /vr-ggal` | implícito (lo seleccionado) | — |
| Der. sup. **PAYOFF** | Curva de payoff (cálculo client-side) | ninguno | — | — |
| Der. sup. **ESCENARIOS** | Tabla spot × tiempo con la tasa `meta.tasa` | ninguno | — | — |
| Der. sup. **LAB** | Post-trade lab: entry sugerido, patas editables (lado/cantidad), vencimiento. Modo `singleLeg` con contrato individual | ninguno | — | — |
| Der. inf. **GRIEGAS** | Evolución diaria de griegas del contrato clickeado | `GET /griegas/opciones` | — | — |
| Header (KPIs) | SPOT, VR GGAL (40r), VR ADR, **TASA R**, ÚLT. ACT | `GET /opciones/meta` (vía proxy `/api/opciones-meta`) | — | **PUT tasa risk-free — SOLO admin** (para el resto es texto read-only) |

Selección **mutuamente excluyente**: o hay estrategia activa o contrato individual, nunca las dos.
Al primer render arranca con la estrategia ATM.

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/api/cotizaciones/opciones` | Chain desde `mercado.options_snapshot`. **Sin filtro "solo hoy"** | `instrumento` (corto o completo), `tipo` | No |
| GET | `/api/cotizaciones/opciones/meta` | Tasa risk-free + VR local + VR ADR | — | No |
| PUT | `/api/cotizaciones/opciones/tasa` | Tasa libre de riesgo **global**. Mergea SOLO el campo `tasa` del jsonb `config` (no pisa `expiries`); llama `clear_cache()`; el motor la toma en ~5 min | `valor` (query, `gt=0 lt=3`; 0.242 = 24,2 %) | **Sí** (`mercado.options_metadata`) — gate inline `require_module("manager")` |
| GET | `/api/cotizaciones/historico/opciones` | Serie intradía de un contrato, 1 punto por bucket de 15 min | `instrumento`, `tipo` | No |
| GET | `/api/cotizaciones/vr-ggal` | Serie diaria GGAL local + ADR (~40 ruedas) | — | No |
| GET | `/api/cotizaciones/griegas/opciones` | Serie diaria de griegas | `instrumento` (**req**) | No |
| POST | `/api/analitica/estrategia-historico` | Agrupa `options_data` en buckets, detecta el ATM de cada bucket, aplica los offsets de cada pata y suma `precio×qty×100` (buy=offer, sell=bid, fallback last). Los buckets donde la estrategia no es válida **se omiten** (huecos, no ceros) | `legs[]`, `bucket_min`, `desde`, `hasta` | No |

**Fuentes**: `mercado.options_snapshot` (`engines/options.py`) · `options_data` (tick-level del OPEX en
curso) · `options_data_hist` (griegas diarias) · `options_metadata` (`type='config'`) · `options_vr`
(`jobs/volatilidad_ggal.py`). Jobs: `options_rollup` 20:15 UTC, `archive_options_data` 20:50 UTC.

**Rarezas**: el SSR **no trae la chain** (`opciones: []` a propósito, se polea client-side, para que
Vercel no compute opciones si el user solo iba a mirar agro) · poll de 30s aunque el motor escriba cada
1s (decisión explícita: "las opciones no son trade activo en la mesa") · payoff/escenarios/estrategias
son **100 % client-side** · el `costo` de la vista es **ALL-IN** (prima neta ×100 + comisión,
`COMISION_PRIMA_PCT = 0.002`) y para el chart histórico se le resta la comisión porque el backend
grafica prima pura · el invitado entra y ve la TASA R read-only, igual que cualquier no-admin.

### Vista: SINTÉTICOS (`/sinteticos`)
- **Módulo RBAC**: `sinteticos`. Router `derivados_sinteticos.py` (21 líneas) → `api/services/sinteticos.py`. Entra por `_PUBLIC`, **un solo endpoint, sin gate propio**.
- **Sin tabs.** Barra slim (SPOT + fuente + última act.) + 2 tablas 50/50 + 2 charts.

| Bloque | Qué muestra | Endpoint | Filtros | Escrituras |
|---|---|---|---|---|
| Tabla **LONG ROFEX + LONG LECAP** | ticker LECAP, futuro DLR matcheado, Px_TF, Px_Futuro, vtos, Cobro, plazo, descalce, T+0, T+n, TE, TNA | `GET /api/derivados/sinteticos` (poll 5s, `@cached ttl=5`) | ninguno | ninguna |
| Tabla **SHORT ROFEX + LONG DLK** | ticker DLK, futuro, Px_DLK, Px_Futuro, DLR ajuste, vtos, plazo, descalce, TE, TNA | mismo | ninguno | ninguna |
| Charts **Curva TNA ×2** | TNA vs plazo. **Solo grafica filas con `descalce == 0` y TNA finita**. Dominio Y del dataset con padding 15 % (piso 1 %); si el rango cruza 0, fuerza que 0 esté visible | mismo | ninguno | ninguna |

**Fuentes**: `mercado.curvas` (LECAPs y DLK) · `mercado.futuros_dlr_snapshot` ·
`core.dolar_oficial.mid_oficial_live` (SPOT, feed MAE) · `jobs/snapshot_sinteticos` (`40 20 * * 1-5`)
materializa `mercado.snapshots_sinteticos` — **la vista NO lee ese histórico**, solo el live.

**Rarezas**: el match LECAP/DLK ↔ futuro DLR es **automático por (año, mes) de vencimiento** — un ticker
nuevo en `mercado.curvas` aparece solo. Fórmulas: `T+0 = Px_TF/SPOT`, `T+n = Cobro/Px_Futuro`,
`TE = T+n/T+0 − 1`, `TNA = TE×365/días` (anualización **lineal**); short:
`TE = (100×Px_Futuro/DLR_emision)/Px_DLK − 1` con `DLR_emision = 1` para bonos modernos.
**La TNA de esta vista es un input de AGRO**: `agro_cobertura.py` toma `long_rofex_long_lecap[].tna` del
mismo mes que el pase para la columna "Sintético" — tocar sintéticos mueve números en Agro.

### Vista: AGRO (`/agro`)
- **Módulo RBAC**: `agro` | **Roles**: admin, trader, sales, asistente_comercial, invitado (**sin la tab DATOS**, filtrado en el front por `useIsGuest`).
- **Archivos front**: `agro-shell.tsx`, `derivados-agro-view.tsx`, `derivados-agro-pizarra.tsx` (1171 líneas), `derivados-agro-futuros.tsx`, `derivados-agro-opciones.tsx`, `derivados-agro-estrategias.tsx` (771), `agro-mejoras-dispo.tsx`, `agro-chicago.tsx`, `agro-datos.tsx` (876, todos los inputs manuales).
- **Router**: `derivados_agro.py` (414 líneas). Services: `agro_sql.py`, `camara_cereales.py`, `agro_cobertura.py`, `derivados_agro.py`, `core/eikon_chicago.py`.

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Mercado** (def.) | 50/50. Izq arriba: FUTUROS del commodity (ticker, vto, last, Intra %, 1D %, bid, offer, vol). Izq abajo: cadena de OPCIONES agro (CALL bid/ofer/últ · STRIKE · PUT bid/ofer/últ, ATM resaltada). Der: panel PASES con 3 sub-vistas | `GET /api/derivados/agro` (poll 5s + SSR), `GET /agro/opciones/{commodity}` (5s) | selector **commodity** TRIGO/MAIZ/SOJA (compartido); selector **vencimiento** en la chain | ninguna (la fila PIZARRA es **read-only**, se edita en DATOS) |
| ↳ **Pase Agro** | Por bloque TRIGO/MAÍZ/SOJA: filas `pizarra` (ámbar, vto = hoy automático), `dispo` (todo `#N/A`) y `futuro`. Columnas Vto · Posición · US$ · Pase Lleno · Valor $ · TNAV, con **flash** verde/rojo al cambiar | `GET /agro` | — | — |
| ↳ **Pase con Cobertura** | Dos tablas: **Cámara Rosario** y **Cámara Bahía Blanca**, con la fórmula de descuento por posición | mismo (`pase_cobertura`, `pase_cobertura_bahia` embebidos) | — | — |
| ↳ **Tarjetas** | Cards por commodity × posición: pase bruto, pase lleno (neto de gastos), TC, venta dispo ARS, Monto Pesos Cau 7D, tasa ON, interés, TC ON, compra USD, gastos, ganancia ON / Pagaré / Sintético en US$/Tn | mismo | — | — |
| ↳ modal **Simulador Estrategias Cobertura** | Overlay full-screen (portal, Esc o ✕). Panel de opciones + form + gráficos | `GET /agro/opciones/{c}` (5s) + `POST /agro/estrategia/simular` | selector **VTO**, **tipo** `put_sintetico`/`long_put`, **strike** (para put sintético solo con CALL, para long put solo con PUT; def ATM con `last>0`), input **prima override**, toggle chart **Estrategia**/**Diferencias** | ninguna (simulador, no persiste) |
| **Mejoras Precio Dispo** | 2 paneles: **DISPONIBLE ROSARIO** y **DISPONIBLE BAHÍA**, cada uno con 3 tablas (Soja/Maíz/Trigo), una fila por LECAP vigente: ticker · vto · días · **TNA** (derivada de la TEA en el backend, `quant/tasas.py`; el tooltip suma la TEA y el rendimiento real al vto) · tasa diaria · tasa directa · precio commodity · interés ganado · valor final ARS · valor en US$ | `GET /agro/mejoras-dispo`, `/mejoras-dispo-bahia` (5s c/u) | ninguno | ninguna |
| **Chicago** | Tarjetas por familia CBOT (Soja, Aceite de Soja, Maíz, Trigo, Harina de Soja): Mes · USD/t · **Var USD/t (nominal, no %)**. Semáforo FEED EN LÍNEA / APAGADO | `GET /agro/chicago` (10s) | ninguno | ninguna desde la vista (entra por el feed Eikon de oficina) |
| **Datos** (oculta al invitado) | 2 columnas. Izq: Dólares de Referencia, Tasas de Cobertura, Costo Pase (read-only), Descuento a Caución 7D (read-only). Der: Cámara Rosario, Cámara Bahía | `GET /agro/camara`, `/camara-bahia`, `/tasas-cobertura`, `/dolares-referencia`, `/costo-pase`, `/descuento-caucion` (10s c/u) | ninguno | **PATCH ×4**: cámara Rosario por cereal, cámara Bahía por cereal, tasas de cobertura, dólares de referencia. **Sin botón guardar**: input + **debounce 800 ms** → PATCH automático + feedback `…`/`✓`/`✗` |

#### Endpoints — `derivados_agro.py`
| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/api/derivados/agro` | Tabla PASE AGRO (3 bloques) + dólar oficial + freshness + `tasas_cobertura` + `pase_cobertura` + `pase_cobertura_bahia`. `@cached(5)`; las 5 lecturas en `ThreadPoolExecutor(max_workers=5)` — era el endpoint más lento de la plataforma (662 ms avg, telemetría 2026-08-05) | — | No |
| PATCH | `/agro/pizarra/{commodity}` | Upsert de la fila PIZARRA (`vencimiento_pizarra` ISO, `us_pizarra` >0; `null` = no tocar) | path TRIGO/MAIZ/SOJA | **Sí** (`agro_pizarra` + `_audit`) |
| GET | `/agro/opciones/{commodity}` | Cadena agrupada por vencimiento, con `futuro_last`, `futuro_ticker`, `dias_a_vto` | path | No |
| POST | `/agro/estrategia/simular` | Simula `put_sintetico` (futuro comprado + call comprada) o `long_put`: piso, diferencia máxima, zona expuesta y las 2 curvas listas para graficar | `commodity`, `vencimiento` (regex `^\d{8}$`), `tipo`, `strike` (>0), `prima_override` (usa esta en vez del `last_price`) | No |
| GET | `/agro/camara` | Los 5 cereales de Rosario (siempre los 5), con `manual_leg` marcando la pata editable | — | No |
| PATCH | `/agro/camara/{cereal}` | Upsert (`precio_ars` y/o `precio_usd`) | path TRIGO/MAIZ/GIRASOL/SOJA/SORGO | **Sí** (`camara_cereales` + `_audit`) |
| GET | `/agro/camara-bahia` | Ídem Bahía; **todos** se cargan en USD (la ARS se deriva) | — | No |
| PATCH | `/agro/camara-bahia/{cereal}` | Upsert (solo `precio_usd`) | path | **Sí** |
| GET/PATCH | `/agro/tasas-cobertura` | Tasas manuales globales ON / Pagaré / Caución 7D ARS / Caución 7D USD (TNA %, todas `gt=0`) | — | **Sí** (PATCH) |
| GET/PATCH | `/agro/dolares-referencia` | **Solo `dolar_bna` es manual**; `dolar_matba` sale del oficial live y `bna_comprador_t1` del A3500 de T−1 | — | **Sí** (PATCH) |
| GET | `/agro/costo-pase` | **100 % automático**: 0,175 % derechos + 0,05 % apertura = 0,225 % por pata × 2 = **0,45 %**, más el costo US$/Tn por commodity | — | No |
| GET | `/agro/descuento-caucion` | Precio disponible descontado a caución 7D por commodity | — | No |
| GET | `/agro/mejoras-dispo` · `/mejoras-dispo-bahia` | Cruza `camara.precio_ars` + TEA de LECAPs (→ TNA, `quant/tasas.py`) + futuros DLR | — | No |
| GET | `/agro/chicago` | Tablero CBOT, precios convertidos a **USD/tonelada server-side**. `online` = heartbeat < 60s | — | No |
| GET | `/api/ingest/eikon/chicago/universo` | `[{ric, familia}]` que el script de oficina pide a Eikon (de la constante `FAMILIAS`, **sin catálogo editable**) | auth `X-Ingest-Token` | No |
| POST | `/api/ingest/eikon/chicago/quotes` | Ingesta del feed, 1 fila por RIC, crudo sin factor. **Ignora RICs fuera del universo conocido** | auth `X-Ingest-Token` | **Sí** (`eikon_chicago_snapshot`) |

**Fuentes**: `mercado.agro_snapshot` (`engines/motor_agro.py`, restart 13:20 / stop 20:05 UTC L-V) ·
`agro_opciones_snapshot` (`motor_agro_opciones`) · `agro_pizarra(+_audit)` · `camara_cereales(+_audit)` ·
`camara_cereales_bahia(+_audit)` · `agro_tasas_cobertura` (tasas **y** dólares de referencia, mismo store
de parámetros globales) · `futuros_dlr_snapshot` · `curvas` (LECAPs) · `market_snapshot` ·
`eikon_chicago_snapshot` · `macro.series_macro` DOLAR (A3500 para `bna_comprador_t1`) ·
`core.dolar_oficial.mid_oficial_live` · `sinteticos.get_sinteticos()`.

#### Notas / rarezas
- **`PATCH /agro/pizarra/{commodity}` es código huérfano en la práctica**: el endpoint funciona pero **no hay proxy Next** y `PizarraRow` es totalmente read-only, con tooltip *"Editable en la tab Datos (Cámara Arbitral)"*. El parámetro `canEdit` se pasa hasta el componente y no se usa.
- **Gate de escritura de AGRO**: los 4 PATCH activos llevan `require_module("agro")` **+ `require_no_invitado`**. **No hay allowlist por email** — cualquier rol con `agro` escribe (admin/trader/sales/asistente_comercial). Los GET solo llevan `get_user_email` para audit.
- **`agro-shell.tsx` fuerza la tab**: invitado con `datos` seleccionada → cae a `mercado`. Es filtro de frontend: **el backend igual devolvería los GET de DATOS a un invitado** (están bajo `/api/derivados`, permitido en `GUEST_PATH_PREFIXES`).
- **Cámara: una sola pata se carga.** Rosario: SOJA en ARS, el resto en USD. Bahía: TODOS en USD. La otra pata la deriva el backend con el **Dólar Banco Nación**; sin BNA cargado sale `null` y la vista avisa. La celda derivada va en gris, no editable.
- **Dos de los tres "Dólares de Referencia" son automáticos** pero viven en la misma tabla que el manual: cada lectura pisa `dolar_matba` con el oficial live y `bna_comprador_t1` con el A3500 de T−1, cayendo al último valor manual si la fuente no responde. El PATCH acepta los tres, pero escribir esos dos queda tapado en la próxima lectura.
- **Guardado sin botón** (debounce 800 ms). Parseo es-AR (`1.234,56`) y `1234.56`; rechaza ≤0. Cada fila adopta el valor remoto si otro usuario editó, sin pisar lo que estás tipeando.
- **Freshness triestado**: `data_fresh` + ventana de mercado (13:00–20:05 UTC L-V) → `LIVE` / `MERCADO CERRADO` / `DESACTUALIZADO` (rojo). Sin frescura, toda la tabla va con `opacity-50`.
- **Chicago: los factores de conversión a USD/t viven server-side** (`core/eikon_chicago.py`): soja 0,367437 · aceite 22,04585538 · maíz 0,393685 · trigo 0,367444 · harina 1,102292769. Las familias **saltean posiciones** a propósito (aceite y harina usan `c1,c2,c4,c5,c6` — sin `c3`), por eso la posición se parsea del sufijo del RIC.
- **La fila `dispo` del Pase Agro devuelve `#N/A` en las 4 columnas numéricas** — hardcodeado en el frontend, no es un bug de datos.
- El simulador **no llama al backend si el strike no tiene `last_price` y no hay `prima_override`**.
- `derivados-agro-estrategias.tsx` lee el vencimiento por **ref** (no del closure) porque el poll tiene deps `[commodity]` y pisaba la selección del usuario en cada tick (stale closure).
- `POST /agro/estrategia/simular` es la **única ruta POST del router sin `require_no_invitado`** (correcto: no persiste).

---

## 4.5 RESEARCH

### Vista: RESEARCH (ruta frontend: `/research`)
- **Módulo RBAC**: `research`, declarado **DENTRO de cada router** (`dependencies=[Depends(require_module("research"))]`); en `main.py` los 4 routers van `_PUBLIC`. | **Roles (default)**: `admin` e `invitado`. **NO** trader / sales / asistente_comercial / back_office. **SIN VERIFICAR** la matriz de prod.
- **Archivos front**: `research/page.tsx` (SSR de `/mails?limit=30`) → `research-view.tsx` (+ el sub-componente `ReportesFinancieros`); cuadrantes `research-lab.tsx`, `research-forwards.tsx`, `research-retorno-total.tsx`; tabs `sensibilidad-table.tsx` (mudada desde la vista ESTRATEGIA, eliminada 2026-08-30), `research-bcra.tsx`, `research-fred.tsx`, `research-documentos.tsx`; RV int. `reuters-view.tsx` → `reuters-fundamentals.tsx`, `reuters-ficha.tsx`; wrapper `maximizable.tsx`.
- **Routers**: `research1816.py`, `research_bcra.py`, `research_fred.py`, `research_docs.py`. Consume además 2 endpoints `_PUBLIC` de otros dominios.
- Las 6 tabs son **keep-alive**: una tab visitada no se desmonta.

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **RENTA FIJA ARGENTINA** (def.) | Grid 2×2 maximizable: Spread A−B · Forwards · Comparar · Retorno Total | `/research1816/universo`, `/spread`, `/series`; `/api/cotizaciones/historico/forwards`; `/api/analitica/retorno-total` ×3 | ver desglose | **Ninguna** |
| **ANÁLISIS SENSIBILIDAD** | Tabla de sensibilidad de PRECIO por escenario de TIR (upside **capital-only, sin carry**), soberanos. Mudada tal cual desde la vista ESTRATEGIA (`/retorno`, eliminada 2026-08-30) — mismo componente (`sensibilidad-table.tsx`, `compact`), mismo endpoint | `/api/analitica/sensibilidad-retorno` | **modo** `absoluta` (def) / `relativa` (shocks pp sobre la TEA actual) · **TIRs** (CSV editable; def `4..11` absoluta, `-4..4` relativa) · **horizonte_dias** (def `365`) · **tipos** (def `["globales"]`) · `curva` fija en `soberanos` (hardcodeada en el fetch) | **Ninguna** |
| **REPORTES FINANCIEROS** | UN feed unificado por fecha desc: mails 1816/ACA (`kind=mail`) + documentos manuales (`kind=pdf`/`comentario`). Lista izq (w-72), visor der: texto en párrafos (detecta titular en mayúsculas), PDF embebido o comentario plano | `GET /research1816/mails?limit=30` (SSR + paginado con `offset`); `GET /research-docs/list`; `GET /research-docs/{id}/pdf` | **Ninguno** (sin buscador ni filtro de fuente/tipo). Solo selección + botón "Cargar más reportes (N)" | **Ninguna desde la vista** — el alta/baja vive en Manager → DOCUMENTOS |
| **BCRA** | Sub-tabs por bloque, cada uno con chips de series (con su último valor inline) y chart multi-serie | `GET /research-bcra/bloques`; `/series?ids=&desde=` (batch por bloque) | Bloque (orden fijo: RESERVAS · TIPO DE CAMBIO · TASAS · DINERO · INFLACIÓN · CER & UVA · DEPÓSITOS); chips ON/OFF por serie (def: las 3 primeras; en bloques "split" TODAS); rango **3M/6M/1A/5A** (def 1A) | **Ninguna** |
| **DATOS INTERNACIONALES** (FRED) | Sub-tabs por bloque. Dos layouts: un chart multi-serie (con transformación + índice de referencia superpuesto y 2º eje) o **cuadrantes 2×2** (`eeuu_macro`, `commodities`, `riesgo_credito`, `macro_global`) | `GET /research-fred/bloques`; `/series?ids=&desde=` | Bloque (TASAS USA · EEUU MACRO · DÓLAR/FX · COMMODITIES · RIESGO/CRÉDITO · VOLATILIDAD · ÍNDICES BOLSA · MACRO GLOBAL); chips por serie; **transformación** Nivel / Base 100 / Var %; selector **"+ índice…"** (S&P/Nasdaq/Dow); rango 3M/6M/1A/5A | **Ninguna** |
| **RENTA VARIABLE INTERNACIONAL** | `ReutersView`: tablero live de subyacentes US. Sub-vistas **COTIZACIONES** (poll 5s) y **FUNDAMENTALS** (4 cuadrantes). Click en fila → **FICHA** de la empresa | `/reuters`, `/reuters/fundamentals`, `/fundamentals/agregado`, `/ficha`, `/segmentos`, `/segmentos/agregado` | ver desglose | **Ninguna** (solo preferencias en `localStorage`) |

**Tab ARGENTINA — cuadrantes**

| Cuadrante | Endpoint | Filtros |
|---|---|---|
| **Spread A−B** | `GET /research1816/spread?a&b&campo&desde` | Select bono **A** y **B** (optgroup por curva; def AL30 y GD30); **campo** TEA / Paridad / Precio (`precioClean`) / Duration; **rango** 1M(30)/3M(90)/**6M(182)**/Máx(3650). Muestra hoy, percentil (verde ≤20 "angosto" · rojo ≥80 "ancho"), z, min→max |
| **Forwards** | `GET /api/cotizaciones/historico/forwards?curva&desde` | **curva** Tasa fija / CER; select **largo → corto** (solo pares que EXISTEN con dato); rango 1M/3M/6M/**Máx**. Lee `matrix[largo][corto]` en fracción, ×100 |
| **Comparar** | `GET /research1816/series?tickers=&campo&desde` | Chips multi-ticker (**máx 8**), mismo campo y rango que Spread |
| **Retorno Total** | `GET /api/analitica/retorno-total?curva=` ×3 (tasa_fija, cer, soberanos), mergeadas | 3 dropdowns de categoría con checkbox por bono + "Todos"/"Ninguno" (la selección **acumula** entre categorías; def todos los de tasa fija); modo **Ret / Carry** (carry = ÷MEP, solo tasa_fija y CER — soberanos van nativos); ventana **Max/6M/3M/2M/1M/MTD/WTD** (def 3M). **El cálculo `(precio + Σ cupones)/base − 1` lo hace el FRONT** |

**Tab RV INT — COTIZACIONES**: poll 5s. Cabecera de 2 niveles: `activo` · **PRECIO (USD)** (last, ccl,
bid, ask, high, low, prev_close, volumen) · **HOY** (var_pct, var_neta, pre_var_pct, ah_var_pct) ·
**RETORNOS** (5d, wtd, mtd, qtd, ytd, 1m, 3m, 1y, 5y — heatmap normalizado por columna) · **CEDEAR**
(ratio). Filtros: buscador ticker/RIC (Enter abre la ficha del primero); dropdown **RUBRO** (del catálogo
`mercado.cedears`); toggle **AFTER HOURS**; selector **COLUMNAS** (persistido `reuters.cols.ocultas.v2`,
default oculta VOLUMEN/MÍN/CIERRE/RATIO); orden por click (numérico desc primero, texto A→Z, nulls al
final, botón "✕ orden").

**Tab RV INT — FUNDAMENTALS** (poll 60s, 4 cuadrantes maximizables):
1. **SCREENER** — una empresa por fila, ordenable, columnas ocultables (default oculta ev, fwd_pe, fwd_ev_ebitda, p_bv, div_yield, gross_profit, fcf, capex, margen_bruto, margen_operativo, deuda_total, caja, current_ratio, proximo_balance; botones "Todas"/"Por defecto").
2. **AGREGADO** — Σ del universo filtrado **calculada por el BACKEND** (el front no re-suma). Selector: **RESULTADOS** / **MÁRGENES** / **SALUD**. Muestra "N en la canasta · M afuera" con el motivo.
3. **DISPERSIÓN** — scatter con selectores de eje Y y X entre 12 métricas (P/E, P/E FWD, EV/EBITDA, P/VL, MG NETO, MG OPER, MG BRUTO, DIV %, DN/EBITDA, MKT CAP, INGRESOS, EBITDA); toggle "extremos"; color por rubro.
4. **COMPOSICIÓN / SEGMENTOS / GEOGRAFÍA** — peso por rubro (market_cap, revenue, ebitda, net_income, capex, calculado en el front) o ranking de segmentos/países del backend.
- **Filtros comunes**: buscador **MULTI-empresa** ("AAPL, MSFT NVDA"); dropdown RUBRO; chips **ANUAL/TRIMESTRAL** y **CANASTA FIJA** (`constante` ↔ `todas`). La COMPOSICIÓN ignora el filtro de rubro. El ranking de segmentos recibe `tickers=` solo con búsqueda activa (sin filtro serían ~184 tickers en la URL).

**Tab RV INT — FICHA**: header (nombre/ticker/RIC/industria/ratio/próximo balance + PRE, AFTER, último,
var %) + 4 cuadrantes: PRECIO 1 AÑO (velas) · MÉTRICAS (tabs NEGOCIO/SALUD/VALUACIÓN, cada dato con
tooltip) · RETORNOS (10 períodos + market cap + barra de rango 52 semanas) · EVOLUCIÓN/SEGMENTOS
(chips `GRUPOS_5A` o botón SEGMENTOS con selectores negocio/geográfico, anual/trimestral, USD/share;
los negativos —eliminaciones/corporate— se apilan hacia abajo).

#### Endpoints
**`research1816.py`** (gate `research`): `/mails` (`limit` 1-100, `offset`) · `/mails/buscar` (FTS
español, `q` min 2) · `/universo` (bonos 1816 con series, agrupados por curva) · `/series` (`tickers`
**cap 8**, `campo` `tea|paridad|precioClean|duration`, `desde` def 6 meses) · `/spread` (`a`,`b` req;
devuelve stats `{actual,min,max,media,z,percentil,n}`) · `/reuters` (cache **4s**) ·
`/reuters/fundamentals` (60s) · `/reuters/fundamentals/agregado` (`periodo` anual 5a / trimestral 8q,
`rubro`, `canasta` `constante|todas`) · `/reuters/ficha` (`ticker` req; **404** si no existe en ninguna
fuente) · `/reuters/segmentos` (`ticker` req, `tipo` `negocio|geografico`, `periodo`) ·
`/reuters/segmentos/agregado` (`tickers` como **string CSV** para que la key de cache sea hasheable;
los ajustes van sumados aparte en `ajustes`, fuera del ranking).

**`research_bcra.py`**: `/bloques` · `/series` (`ids` hasta **12**, `desde` def 12 meses).
**`research_fred.py`**: `/bloques` · `/series` (`ids` hasta **16**).
**`research_docs.py`**: `/list` (metadata sin bytes) · `/{doc_id}/pdf` (`Content-Disposition: inline`, 404 sin PDF).

**Escritura de documentos — `api/routers/manager/documentos.py`, gate `manager`** (no vive en Research):
`GET /api/manager/documentos` · `POST` (`titulo` 1-300, `fecha`, `tipo` `pdf|comentario`, `fuente` ≤120,
`comentario`, `archivo_b64` requerido si pdf —tolera prefijo `data:…;base64,`—, `nombre_archivo` ≤300;
el `autor` sale de `get_user_email`) · `DELETE /{doc_id}`.

**Ingesta del feed Eikon** (`api/routers/ingest.py`, gate propio `X-Ingest-Token`, lo invoca
`scripts/eikon_feed_simple.py` desde la PC de oficina): `GET /ingest/eikon/universo`,
`POST /ingest/eikon/rics`, `/quotes` (→ `mercado.eikon_snapshot`), `/fundamentals`, `/segmentos`.

#### Fuentes de datos
- **Mails 1816** → `ia.research` (cuerpo crudo + destilado LLM + FTS español). `jobs/research_mail.py` (IMAP), cron `*/30 10-14 * * 1-5`.
- **Market data 1816** → `research.mkt_1816_series` + `mkt_1816_instrumentos` + `mkt_1816_watch`. Writers `jobs/mercado_1816_series.py` (`0 22 * * 1-5`, requiere `MERCADO_1816_API_KEY`) y `mercado_1816_discovery.py`. **Feed SEPARADO de `mercado.curvas`**.
- **BCRA** → `research.bcra_watch` + `bcra_series` + `bcra_variables`. `jobs/bcra_research.py`, `0 12,16,20,23 * * 1-6`.
- **FRED** → `research.fred_watch` + `fred_observations` + `fred_series`. `jobs/fred_research.py`, `0 12,16,20,23 * * 1-5`.
- **Documentos** → `research.documentos` (PDF como `bytea` + `mime` + `nombre_archivo`, o `comentario`).
- **Reuters/Eikon** → `mercado.eikon_snapshot`, `eikon_fundamentals`, `eikon_segmentos` + `mercado.cedears` (ratio/rubro vía LATERAL) + `mercado.precios_acciones` (velas de la ficha). **La PC de oficina no toca la base directo.**

#### Notas / rarezas
- **Contradicción doc vs. código (RBAC del invitado)**: los 4 routers dicen en su docstring "módulo `research` (interno, **JAMÁS invitado** — REGLA #8)", pero `DEFAULT_MATRIX["invitado"]` **SÍ incluye `research`** desde el 2026-07-21, con comentario explícito ("Abre la vista completa: 1816 + reportes + BCRA + FRED + RV internacional, con sus copilotos"). Los docstrings quedaron viejos.
- **`GET /research1816/mails/buscar` no lo consume ningún componente** (grep sobre todo `src/`). Su único consumidor es la tool `buscar_en_mails` del copiloto, que **no va por HTTP** (llama directo al service). Endpoint HTTP huérfano.
- **Prefijo `research1816` ≠ `/api/research`**: existe otro `/api/research` (Análisis Fundamental de RV, gate `renta-variable`) que NO es esta vista.
- **Los endpoints de Reuters vivían en `/api/trading/reuters*`** y se movieron el 2026-07-18 cuando `trading` pasó a admin-only. Residuo: **`trading-view.tsx:672` todavía consume `/api/research1816/reuters`** desde la vista Trading (→ necesita también `research`).
- **Cache compartido en el router, no en el service**: 5 wrappers `@cached` (tablero 4s, resto 60s). El TTL de 4s es deliberadamente menor al poll de 5s: N usuarios comparten UNA query (la LATERAL medía ~68 ms).
- **El PdfViewer esquiva `X-Frame-Options: DENY`**: baja el PDF con `fetch` como blob y lo renderiza desde una URL `blob:`.
- **El Retorno Total lo calcula el FRONT**; contrasta con el AGREGADO de fundamentals, donde el backend suma y el front "no re-suma nada, así el panel no puede contradecir al endpoint".
- **La tab REPORTES mezcla dos fuentes con dos gates**: mails (`ia.research`, gate `research`) y documentos (`research.documentos`, lectura `research` / escritura `manager`).
- **FRED — grupos de escala excluyentes**: marcar una serie de otro `SCALE_GROUP` **apaga** las incompatibles. Los cuadrantes y los grupos están **hardcodeados en el frontend por `series_id`** — agregar una serie al watch en la DB no la mete sola en un cuadrante.
- **BCRA — bloques "split"**: `indexacion` e `inflacion` se renderizan partidos 50/50 con un chart por serie y arrancan con TODAS prendidas. La unidad del bloque se toma de `series[0].unidad`.
- **Segmentos — advertencia del propio código**: el segmento es el que publica cada empresa (Apple y Coca-Cola reportan por región, NVDA y Rocket Lab por producto), los nombres cambian entre períodos, y las eliminaciones/corporate vienen en `ajustes` (sin ellas la suma no cierra contra ingresos totales).
- **El copiloto de `research` NO filtra por tab**: concatena SIEMPRE las 4 fuentes en TSV con columna `fuente`; `params.tab` solo desempata. `celda_max=220` (el default 60 mutilaría la columna `dato`).
- **Toda la vista es READ-ONLY**: cero acciones de escritura, export ni allowlist en las 5 tabs.
- **Gotcha documentado**: cada prefijo nuevo exige agregar a mano el route handler catch-all de Next **y** la entrada en `src/proxy.ts`; si falta una, la tab da 404/403 sin mensaje claro.
---

## 4.6 OPERAR (órdenes, MEP, riesgo)

### Vista: OPERAR (ruta frontend: `/operar`)
- **Módulo RBAC**: `operar` | **Roles (default)**: **SOLO `admin`**. `invitado` bloqueado por doble vía (`operar` ∉ `INVITADO_MODULES` + corte explícito de `require_module` cuando `is_guest_portal`). **SIN VERIFICAR** qué roles tiene `operar` en la `role_matrix` de prod.
- **Gate**: `_OPERAR = [verify_api_key, require_module("operar")]` sobre los 4 routers (`ordenes`, `operativa`, `operar`, `risk`). Pre-gate en `src/proxy.ts` para `/operar` y esos 4 prefijos de API.
- **Archivos front**: `operar-shell.tsx`; MEP: `dolar-mep-shell.tsx`, `dolar-mep-compra-view.tsx`, `dolar-mep-venta-view.tsx`, `dolar-mep-board.tsx`, `dolar-mep-timesales-chart.tsx`, `dolar-mep-detalle-drawer.tsx`, `dolar-mep-shared.tsx`, `account-picker.tsx`; Títulos/FCI: `operar-titulos-fci-view.tsx`, `operar-fci-view.tsx`, `operar-shared.tsx`. Proxies `[[...path]]` con GET/POST/**DELETE**, `maxDuration=30`, que propagan `cf-access-authenticated-user-email` + `x-acaquant-user-email` para el audit.
- **Propósito**: **única pantalla transaccional del producto** — manda y cancela órdenes REALES contra ROFEX/pyRofex (títulos, FCI, brackets) y ejecuta la operativa Dólar MEP de 2 patas, con saldos y posiciones live de la cuenta comitente elegida.

Jerarquía: 2 tabs → cada uno con 2 sub-vistas. Tab inicial **DÓLAR MEP** (decisión user 2026-07-23).
Deep-link `?tab=dolar-mep` | `titulos-fci` (alias legacy `fci`, `dashboard`).

| Tab | Qué muestra | Endpoints | Filtros | Acciones de escritura |
|---|---|---|---|---|
| **DÓLAR MEP** (barra común) | MEP implícito de la rueda + last AL30 y AL30D + hora del último trade. Estado compartido entre sub-tabs. Poll cotización 5s, saldo 60s | `GET /operativa/mep/cotizacion?rueda=`, `GET /risk/account/saldo?rueda=&account=`, `GET /risk/account/listado` | **RUEDA** `CI`(def)\|`24hs`; **CUENTA** (`AccountPicker`, busca por prefijo de id) | — |
| **MEP → COMPRA** | Form ARS→USD + `SaldoBox` (ARS disp/movim, USD D disp/movim, `last calc`) + chart MEP por minuto (izq) + tabla de operativas del día (der, poll 3s con dedupe). Click en fila → drawer de detalle | `POST /operativa/mep`, `GET /mep/dia`, `/mep/timesales`, `/mep/{id}/detalle` | **MODO** `ARS`\|`USD` (en USD calcula los ARS **client-side** y manda `monto_ars`); **MONTO**; **COMISIÓN %** (step 0.01, min 0, def `0.62`); RUEDA; CUENTA | **EJECUTAR** con `confirm()` nativo (lista monto/comisión/nominales/MEP) → `POST /operativa/mep` con `client_order_id: crypto.randomUUID()`. **Manda 2 órdenes MARKET REALES**: BUY AL30 + SELL AL30D |
| **MEP → VENTA** | Espejo inverso (USD→ARS). Misma tabla del día (columna TIPO) y mismo chart | `POST /operativa/mep/venta` + los mismos GET | **MONTO USD**; COMISIÓN %; RUEDA; CUENTA | **EJECUTAR VENTA** con `confirm()` → BUY AL30D + SELL AL30 MARKET |
| **TÍTULOS Y FCI → TÍTULOS** | Izq: card de operación (buscador de ticker + **book enfrentado bid/ask 5 niveles** + spread + último/prev + form). Der arriba `PortfolioPanel` (ARS DISP/MOV, USD D DISP/MOV + tenencias por ticker a market + TOTAL). Der abajo `OrderManagement` (órdenes del día, split activas/cerradas). Polls: book 1s, órdenes 4s, portfolio 8s | `GET /operar/order-book`, `GET /ordenes/symbols`, `POST /ordenes`, `POST /operar/bracket`, `DELETE /ordenes/{cl_ord_id}`, `GET /ordenes/dia`, `GET /risk/account/{saldo,detailed,listado}` | **CUENTA** (`AccountSearch`, top 30); **TICKER** (autocomplete ≥2 chars, debounce 200 ms, ranking exacto→prefijo→resto y 24hs>CI>48hs); **PLAZO** `CI`\|`24hs` (`48hs` solo si el ticker vino así); **SIDE** COMPRAR/VENDER; **TIPO** LÍMITE\|MERCADO; **TIF** DAY\|IOC\|FOK\|GTC; PRECIO / NOMINALES; PRECIO SALIDA (bracket). Deep-link `?ticker=` y `?account=` | **ENVIAR COMPRA/VENTA** → `POST /ordenes` (con UUID). **ENVIAR BRACKET** (exige entrada LIMIT) → `POST /operar/bracket`. **cancelar** por fila → `DELETE /ordenes/{id}?proprietary=`. Click en una punta del book precarga side+precio+size. **Sin `confirm()`** |
| **TÍTULOS Y FCI → FCI** | Panel de suscripción/rescate: buscador de fondo, card con TIPO/MONEDA/PLAZO/CUOTA (refresco 20s + ↻), conversión importe↔cuotapartes. Columna derecha idéntica | `GET /ordenes/fci/search`, `/fci/quote`, `POST /ordenes/fci` + portfolio/órdenes | **CUENTA**; **FONDO** (≥2 chars, debounce 250 ms, limit 40); **SIDE** SUSCRIBIR(BUY)/RESCATAR(SELL); **MONTO** con toggle `importe` (def) \| `cuotapartes`. Deep-link `?fci=` | **SUSCRIBIR / RESCATAR** → `POST /ordenes/fci` con UUID. **Sin `confirm()` previo** (a diferencia de MEP) |

#### Endpoints
**`operar.py` (`/api/operar`)**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/order-book` | Top 5 bid/ask + metrics desde `mercado.market_snapshot`. 3 desenlaces: **200 con puntas**; **200 con `sin_puntas:true`** (fila ≤90s pero libro vacío → devuelve igual el último precio); **202 `subscribing`** si no hay fila o está abandonada (>90s) → registra el ticker en `mercado.adhoc_subscriptions` y el motor lo levanta en ~5s. Valida el ticker contra el **universo live del broker** (`ordenes.ticker_existe`), con `manager.pyrofex_instruments` de fallback: 404 si no existe; sin ninguna de las dos fuentes → subscribe optimista. **429** al cap de 50 suscripciones | `ticker` (**req**), `plazo` (`CI\|24hs\|48hs`, def `24hs`, ignorado si el ticker es full) | Sí por efecto lateral (`adhoc_subscriptions`, TTL 7d rolling, cap 50 con desalojo LRU) — **con método GET** |
| POST | `/bracket` | Manda la LIMIT de **entrada**; si el broker la acepta persiste el bracket (`PENDING_ENTRY`). Al llegar el ER `FILLED` de la entrada, `engines/motor_ordenes.py` dispara la **salida** LIMIT con side opuesto, MISMA cantidad, al `price_exit`. Entrada rechazada → 400 sin persistir. Si falla solo el persist → `ok:true` + `warning` | `ticker` (full), `side`, `size` >0, `price_entry` >0, `price_exit` >0, `tif` (def DAY), `account` (**obligatorio**), `client_order_id` | **SÍ** — orden real + `brackets_live` + `ordenes_live` + `ordenes_audit` (+ idempotency). 201 |
| GET | `/brackets/dia` | Lista de brackets, **todos los estados**, `created_at` desc, LIMIT 200. Pese al nombre, **NO filtra por fecha**. **Sin consumidor en el front** | `account` | No |

**`ordenes.py` (`/api/ordenes`)**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| POST | `/` | Envía la orden vía REST pyRofex y persiste request + respuesta. Valida que LIMIT lleve `price` y `size>0`. Devuelve `{ok, cl_ord_id, status, proprietary, error?, broker_response}`; distingue `REJECTED_LOCAL` (excepción del cliente) de `REJECTED_BROKER`. Escribe `PENDING_NEW`; el motor lo actualiza con cada ER | `ticker` (full), `side`, `size` >0, `order_type` (def LIMIT), `price`, `tif`, `account` (si `None` usa la del `.env`), `client_order_id` | **SÍ** — orden real. **Rate limit propio `30/minute;400/hour`**. 201 |
| DELETE | `/{cl_ord_id}` | Pide cancelación al broker. El estado real llega por order_report; acá solo se registra el intento. Resuelve `proprietary`: query → el persistido. Si no lo encuentra: `ok:false` "abrila desde la web del broker" | path; query `proprietary` (necesario para órdenes *external*) | **SÍ** — cancelación real + audit. **Rate limit `60/minute;600/hour`** |
| GET | `/dia` | Órdenes del día UTC: docs locales **+ merge con el broker** (`get_all_orders_status`, la verdad real-time). Resuelve la cadena `origClOrdId` hasta la raíz (guarda de profundidad 20) y por raíz gana el ER de mayor `transactTime`. Marca `external:true` las que solo existen en el broker. Degradación graceful si el broker falla | `account` | No |
| GET | `/symbols` | Autocomplete: substring sobre `ticker`/`underlying` del **universo LIVE del broker** (`get_detailed_instruments`, cache 5 min compartida con `/fci/search`). **Excluye FCI** por cficode (`CIO…`). Ordena por relevancia (exacto→prefijo→contiene, 24hs>CI>48hs) **antes** de cortar en `limit`. Fallback a `manager.pyrofex_instruments` (SQL) solo si el universo live no está disponible — la tabla la escribe un one-shot manual y puede estar vacía | `q` (min 2), `limit` (1–50, def 20) | No |
| GET | `/fci/search` | Universo OPUESTO (solo cficode `CIO…`), del live `get_detailed_instruments()` cacheado 300s en proceso | `q` (min 2), `limit` (def 30) | No |
| GET | `/fci/quote` | Cuota + metadata operable (moneda, `size_precision`, `min_trade_vol`, plazo T+0/T+1/T+2). **El precio operable NO es el `last`**: es la banda del día (`lowLimitPrice == highLimitPrice`), con fallback a LAST/CLOSE. 404 si no está | `ticker` | No |
| POST | `/fci` | Suscripción (BUY) / rescate (SELL): orden **LIMIT @ cuota del día**, cantidad en cuotapartes fraccionarias. Si `amount_mode='importe'` convierte **server-side** con la cuota live (autoritativa). 400 sin cuota operable o si da 0 cuotapartes | `ticker`, `side`, `amount` >0, `amount_mode` (def `cuotapartes`; el front manda `importe`), `account`, `client_order_id` | **SÍ** — orden real (`kind:"FCI"`, `op:"SUSCRIPCION"/"RESCATE"`). **Sin rate limit propio** |
| GET | `/{cl_ord_id}` | Estado de una orden (sin merge con broker). 404 si no existe | path | No |

**`operativa.py` (`/api/operativa`)**

| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/mep/cotizacion` | Last de AL30 y AL30D (último trade de `timesales`) + `mep_implicito = AL30/AL30D` a 2 decimales | `rueda` (def CI) | No |
| GET | `/mep/timesales` | Serie del MEP por minuto desde el inicio del día ART | `rueda` | No |
| POST | `/mep` | **Compra MEP (ARS→USD)**. `nominales = floor(monto_ars·(1−com%)/(precio_AL30·0.01))`; persiste el wrapper **SIEMPRE** (incluso con `nominales=0` → `FAIL_VALIDACION`); ejecuta BUY AL30 MARKET → espera el ER por REST (timeout 5s, poll 200 ms) → si confirma (`NEW`/`PARTIALLY_FILLED`/`FILLED`) manda SELL AL30D. Si la BUY es `REJECTED/CANCELLED/EXPIRED/UNKNOWN_LOCAL` **NO manda la SELL** (evita short involuntario). Estados: `OK`/`OK_PARCIAL`/`FAIL`/`STALE_BUY`/`FAIL_VALIDACION` | `monto_ars` >0, `comision_pct` 0–5 (def 0.62), `rueda`, `account`, `client_order_id` | **SÍ — 2 órdenes MARKET reales.** Sin rate limit propio |
| POST | `/mep/venta` | **Venta MEP (USD→ARS)**: `nominales = floor(monto_usd·(1−com%)/(precio_AL30D·0.01))`, luego BUY AL30D + SELL AL30. Mismo guard BUY-antes-de-SELL | `monto_usd` >0, `comision_pct`, `rueda`, `account`, `client_order_id` | **SÍ — 2 órdenes MARKET reales** |
| GET | `/mep/dia` | Operativas del día UTC (compra + venta) con join a las 2 patas + métricas derivadas | `account` | No |
| GET | `/mep/{id}/detalle` | Drilldown: doc completo + las 2 patas con su orden live + **timeline de audit** + `precio_compra_al30`, `precio_venta_al30d`, `usd_efectivo`, `ars_operados`, `mep_efectivo`, `slippage_pct` (vs `mep_inicial`), `mep_costo_cliente`, `duracion_ms`. Las métricas salen de `operativa_mep.calcular_efectivos` — la MISMA función que el listado, y la que sabe que en la operativa de **venta** las patas están invertidas (la que está en USD es la BUY, no la SELL). 404 si no existe | path | No |

**`risk.py` (`/api/risk`)**: `GET /account/saldo` (`settle` `"0"`=CI / `"2"`=24hs; devuelve `saldo_ars`,
`saldo_usd_d`, `movimiento_ars`, `movimiento_usd_d`, `monedas{}`, `settlement_date`, `last_calc`;
**`USD D` = dólar MEP**, el broker distingue 8+ tipos de USD; errores del broker → 502) ·
`GET /account/report` (crudo de `get_account_report`) · `GET /account/positions` (**sin consumidor en el
front**) · `GET /account/detailed` (posiciones por tipo de instrumento con valuación a market; alimenta
el `PortfolioPanel`, filtra `totalCurrentSize <= 0` = round-trip intradía) · `GET /account/listado`
(**NO pega al broker**: espejo de `clientes.cuentas`; devuelve `activa:true` y saldos en null;
**filtrado por scope de grupos** — único endpoint del dominio que filtra la lista en vez de rechazar;
el param `solo_activas` **se ignora**).

#### Fuentes de datos
`operaciones.ordenes_live` (PK `cl_ord_id`; escriben `ordenes.py::_upsert_live_sql`,
`engines/motor_ordenes.py` por WS, y `operativa_mep._persistir_orden_live` por REST) ·
`ordenes_audit` (append-only: `SEND_REQUEST/OK/ERROR`, `CANCEL_*`, `FCI_SEND_*`, `REST_SNAPSHOT`,
`BRACKET_EXIT_SENT` — la trazabilidad de quién mandó/canceló qué) · `operativas_mep` ·
`brackets_live` (estados `PENDING_ENTRY`, `EXIT_SENT`, `COMPLETED`, `ENTRY_CANCELLED`, `EXIT_REJECTED`) ·
`ordenes_idempotency` (PK = `client_order_id`, TTL 1 día podado en cada `_ensure()`) ·
`motor_heartbeat` · **`operaciones.triggers_mep` — tabla HUÉRFANA** (ver rarezas) ·
`accounts_descubiertas` (vestigial) · `mercado.market_snapshot` (book live, `engines/valores.py` ~1s) ·
`mercado.timesales` · `mercado.adhoc_subscriptions` · `manager.pyrofex_instruments`
(`scripts.discovery_pyrofex`) · `manager.grupos` · `clientes.cuentas` (`jobs.sync_comitentes`) ·
**feed pyRofex LIVE** (sesión REST-only lazy en uvicorn; el motor tiene sesión aparte con WS suscripto a
`order_report`).

#### Notas / rarezas
**Scope de cuenta por grupo**
- `scope_cuentas` → `None` = sin restricción (admin o usuario sin grupo), o tuple de `id_cuenta`. Tuple vacío = ve nada. Cache 60s, invalidado al mutar grupos.
- `verificar_account`: no-op si `scope is None`; **400** si el usuario scopeado omite la cuenta (para que no caiga al default del `.env`, que puede no ser suya); **403** si la cuenta no está en el scope.
- `cuentas_visibles` es **fail-open**: ante error de DB devuelve `None` (ve todo) — explícito en el código ("los grupos no deben tumbar la app").
- **NO aplican scope**: `/operar/order-book`, `/ordenes/symbols`, `/ordenes/fci/search`, `/ordenes/fci/quote` (son de mercado, sin cuenta).
- **⚠ Mismatch de namespace de cuenta (riesgo verificado)**: el scope guarda el `id_cuenta` crudo de `clientes.cuentas`, pero `_send_order_impl` persiste el `account` ya traducido por `resolver_cuenta_rofex` (prueba crudo / `zfill(3)` / `zfill(4)` contra el broker y cachea el acierto). En `DELETE /ordenes/{id}` y `GET /ordenes/{id}` el scope se verifica contra ese `account` **resuelto** → si el número ROFEX difiere del `id_cuenta` (`100` → `0100`), un usuario scopeado recibiría **403 al cancelar o consultar su propia orden**. Admin / sin-grupo no se ve afectado. **SIN VERIFICAR** cuántas cuentas de prod resuelven distinto.

**Órdenes**
- Idempotencia: clave reservada atómicamente vía PK + `ON CONFLICT DO NOTHING`; reenvío con la misma clave devuelve el resultado del primero (espera hasta 4s). **Degradación deliberadamente insegura hacia "mandar"**: ante error de infra `reservar()` devuelve `True` → la orden se manda. Protege contra reintento de red / doble submit, **no** contra dos clicks separados (el front genera un UUID nuevo por click).
- `send_fci_order` **NO llama a `resolver_cuenta_rofex`** (usa `account or cuenta_default()` crudo), a diferencia de `send_order`. **Inconsistencia verificada**: una cuenta cuyo nº ROFEX difiera funcionaría para títulos y fallaría para FCI.
- **No hay ningún límite de tamaño/notional/fat-finger en el backend**: las únicas validaciones son `size>0`, LIMIT requiere `price`, `comision_pct ∈ [0,5]`, `monto>0`, `nominales>0`. **El único control cuantitativo es el rate limit** (30/min) y el `confirm()` del navegador en MEP — que **no existe** en títulos ni FCI.
- Los timestamps del broker llegan FIX-like (`"20260520-15:11:26.794-0300"`) y se parsean a mano.
- El motor escucha por WS **solo la cuenta master**; su `_recovery` reconcilia agrupando por la cuenta REAL de cada orden local no-final (fix de un bug donde las subcuentas 100/255/805 quedaban `PENDING_NEW` para siempre). Las operativas MEP ya no dependen del WS: confirman por REST.

**Brackets**
- Solo TP, **sin stop-loss** (explícito en `core/brackets.py`). Salida LIMIT, side opuesto, misma cantidad.
- `POST /operar/bracket` exige `account`, a diferencia de `POST /ordenes` donde es opcional.
- **⚠ Riesgo verificado**: `create_bracket` persiste `account` **crudo** mientras la entrada se mandó con la cuenta resuelta; cuando el motor dispara la salida usa el crudo → si esa cuenta necesitaba `zfill`, **la salida automática puede ser rechazada quedando la posición abierta** (`EXIT_REJECTED`, "para intervención manual"). **SIN VERIFICAR** si ocurre en prod.
- **No hay tablero de brackets**: se crean desde la vista pero `GET /brackets/dia` no lo consume nadie.

**Operativa MEP**
- **No es atómica**: entre BUY y SELL los precios pueden moverse (asumido: AL30/AL30D son ultra-líquidos). `OK_PARCIAL` = la BUY entró y la SELL no → hay que liquidar el AL30 a mano. `STALE_BUY` = la BUY no resolvió en 5s.
- El wrapper se persiste **antes** de tocar al broker, para que el usuario vea todos los intentos.
- Convención BYMA `PRICE_FACTOR_BONOS = 0.01` duplicada en backend y frontend.
- La conversión USD→ARS de COMPRA es **client-side** (el backend solo acepta ARS); en FCI, en cambio, la conversión es server-side y autoritativa.
- **La barra dice "refresca cada 2s" pero los intervalos reales son 5s y 60s** — cosmético, pero miente.

**Triggers MEP — feature ELIMINADA, doc desactualizada**
- `docs/API.md` documenta `POST /operativa/mep/trigger`, `DELETE /mep/trigger/{id}`, `GET /mep/triggers/dia`, un scanner asyncio de 1s en el lifespan con stale-guard de 5s y auto-cancel EOD 19:50 UTC, y `api/services/triggers_mep.py`. **Nada de eso existe hoy**: el archivo no está, `main.py` no tiene scanner, `operativa.py` no declara esas rutas. Queda la tabla huérfana `operaciones.triggers_mep` en `sql/schema.sql` y el parámetro `parent_trigger_id` (siempre `None`) en `operativa_venta_mep`.

**Riesgo / risk.py**
- Los docstrings prometen "Cache 3s/5s" pero **no hay decorador `@cached`** — el único `@cached(600)` es `_nombres_por_id_cuenta`. Cada poll pega al broker: `usePortfolio` cada 8s por usuario con la vista abierta (y `saldo` internamente llama a `account_report` → 2 hits a `get_account_report` por ciclo). El docstring del router también promete "pegale todo lo que quieras, no satura al broker": **es falso hoy**.
- Los docstrings de módulo de `risk.py` y `operativa.py` dicen que el gate es `operaciones`. **Es incorrecto**: se montan con `_OPERAR`.
- `listado_cuentas` devuelve las ~1800 comitentes con saldos en null a propósito: resolver el nº ROFEX de cada una implicaría probar 1800 cuentas por request.

**Frontend / seguridad operativa**
- La cuenta **nunca se persiste** entre sesiones (ni en localStorage): un navegador compartido en la mesa heredaría la cuenta del usuario anterior → "disparo a cuenta equivocada". Solo el deep-link `?account=` la precarga, con banner `DerivedAccountBanner`.
- `ACCOUNT_DEFAULT_FALLBACK = ""` — sin cuenta elegida, los botones quedan deshabilitados.
- Los proxies propagan `x-acaquant-user-email` porque **CF Access estripa `cf-access-authenticated-user-email` cuando el request entra con service token** — sin ese header el audit no sabría quién mandó la orden.
- El rate limit se keyea por `x-acaquant-user-email`, header **no validado criptográficamente** en `api/ratelimit.py` (la validación real corre después, en `get_user_email`). Limitación conocida y documentada.
- `GET /operar/order-book` lo consume también `/trading` → un usuario con `trading` sin `operar` vería el book vacío/403.

---

## 4.7 OPERACIONES, CUENTAS, OPERADORES, CONTRAPARTES y REFERIDOS

Cuatro vistas gateadas por el **mismo módulo `operaciones`** (`_OPERACIONES = [verify_api_key,
require_module("operaciones")]` sobre `operaciones.router` y `cuentas.router`), servidas por 2 routers +
un sub-router de Manager para el ABM de contrapartes. **Roles**: admin, trader, asistente_comercial.
NO sales / back_office / invitado (REGLA #8).

### Vista: OPERACIONES (`/operaciones`)
Shell **keep-alive** (cada tab se monta una vez y luego se oculta con CSS), tab persistida en
`operaciones.tab`. El valor legacy `"intraday"` cae a `"operaciones"`.

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **OPERACIONES** (`ops-view.tsx`) | 50/50. Izq: tabla **Por operación** (Σ bruto, Σ arancel, boletos, tasa ponderada MAV, prom/boleto, %) + barras Σ bruto por fecha. Der arriba **Por cuenta**; der abajo **Por título** | `/ops/fechas`, `/ops/meta`, `/ops/serie`, `/ops/resumen`, `/ops/segmentos`, `/ops/niveles3`, `/ops/mercados`, `/ops/carteras`, `/ops/cuentas-list`, `/comercial/operadores` | **Modo de rango**: `ULTIMA`/`SEMANA`/`MES`/`RANGO` — **anclados a la fecha más reciente CON DATOS**, no a hoy; 2 `input[type=date]` (editar cualquiera salta a RANGO y clampea el otro). **Moneda** `ARS`/`USD`/`USD_DOL` ("DOLARIZAR" = ARS+USD a USD con el mep de cada boleto). **Buscador de cuenta** con `datalist`. **MultiSelect**: Segmento (`nivel_1`), Cartera (HD/DL/ARS/FCI/… + `(SIN CARTERA)`), Nivel 3, Mercado, Operador. **Checkbox ACA Valores** (destildado → `aca_valores=sin`). **Cross-filter 3-way acumulativo** operación ↔ cuenta ↔ título, cada uno con chip `✕`. **Ocultar cuentas** (botón 🚫 → `excluir` server-side, persistido en `localStorage`, chips 👁 para restaurar) | Ninguna. Los filtros persisten local; ocultar cuentas NO escribe en la DB |
| **ARANCELES** | Serie de aranceles por periodo + tabla izq por dimensión configurable + tabla der por cliente + por instrumento | `/ops/aranceles`, `/ops/fechas`, `/ops/meta`, `/ops/segmentos`, `/comercial/operadores` | Mismo modo de rango; moneda ARS/USD; **selector de dimensión** `dim` = `nivel3`\|`operacion`\|`mercado`\|`operador`; segmento y operador single-select; cross-filter por `sel_dim`/`cuenta`/`instrumento`; botón **ALL** → `serie_full=true` (por default la serie se acota a ~18 m / 550 días) | Ninguna |
| **AGRO** | Toneladas de futuros+opciones agro: serie por periodo, serie de la cuenta elegida, share nuestro/mercado por commodity (solo futuros), totales por commodity/cuenta/instrumento, desglose FUTURO/OPCIÓN | `/ops/agro`, `/ops/fechas`, `/ops/niveles5` | `desde`/`hasta` (acotado a bounds reales; def YTD del último año con datos), `agg=DIARIO`, **select nivel 5**, **select tipo** FUTURO/OPCION/ambos, cross-filter commodity + cuenta. Tabs internos del chart: volumen / por tipo / share | Ninguna |
| **DÓLAR FUTURO** | Nocional USD de DLR (1 contrato = USD 1.000), arancel ARS y boletos: `por_tipo` (Compra/Venta), `por_cuenta`, `por_instrumento`, serie split | `/ops/dolar-futuro`, `/ops/fechas`, `/ops/niveles5` | Modo de rango (def `MES`), `agg=DIARIO`, select nivel 5, cross-filter 3-way tipo/cuenta/instrumento | Ninguna |
| **POSICIONES Y DIFERENCIAS** (`ap5-posiciones-view.tsx`) | El reporte que la mesa manda por mail, servido desde lo que dice **LA CÁMARA** (`ap5.portfolio`, A3/ACyRSA) y no desde nuestro registro de boletos. **Reemplazó a DIFERENCIAS DIARIAS** (2026-08-25). **CUATRO TABS** — FUTUROS AGRO · FUTUROS DÓLAR · CONSOLIDADOS · **POSICIONES DE ACA**. En las dos primeras, **solo dos paneles**: Cooperativas a la izquierda y MUNDO ACA a la derecha, cada uno con su Top 10 + y Top 10 − (la pantalla completa es para las dos listas). **CONSOLIDADOS** es el cuadro POR INSTRUMENTO del mail: FUTUROS AGRÍCOLAS y FUTUROS U$S con Compra / Venta / Posición Neta (cantidades × multiplicador, no un importe), Acum. al Día, al Día Ant. y la Diaria, **con su TOTAL sumado en el backend**; se agrupa por (tab, MONEDA) — nunca un total con dos monedas mezcladas. **POSICIONES DE ACA** (2026-08-26) es la posición abierta de **UNA** cuenta propia, mitad y mitad (agro izquierda / dólar derecha, cada lado con su banda de color), **una fila por símbolo**: Cantidad (UN número con signo, no las dos columnas de la base con una en cero) · **Nocional** (`cantidad × multiplicador` — rotulado TONELADAS en agro y NOCIONAL U$S en dólar) · P. entrada (`avg_px` ponderado) · P. ajuste (`settlement_price`) · dif px · dif % y, **Diferencias** (`daily_settlement`), con un **TOTAL al pie de cada tabla sumado en el backend y abierto POR MONEDA** — dos monedas en la misma tabla salen como dos totales, nunca uno mezclado; sólo se totaliza Diferencias (sumar toneladas de soja con las de trigo, o promediar precios, no significa nada). Fondo BLANCO y verde/rojo fijos, no los tokens del tema: esta tab se captura para el mail. **Cada fila del CONSOLIDADO se puede AUDITAR**: click abre un modal (`GET /api/ap5/consolidado/detalle`) con las cuentas y los símbolos que la componen —cuenta · nombre · grupo · símbolo · multiplicador · compra/venta/neta · acum. día · acum. día ant. · diaria— y arriba, **el número del cuadro contra la Σ del detalle**, campo por campo, con un ✓ si cierran: si alguna vez se separan se ve en la pantalla y no comparando contra una planilla. Sale del MISMO service y los MISMOS predicados que el cuadro (`consolidado_detalle`), lista **sólo lo que entra** (lo excluido vive en `faltantes`) y **no aparece en la imagen** — `copiar()` arma la captura desde los datos, no desde el DOM. Mismo patrón que el modal por celda de Tesorería → BANCOS y el de DÍAS SIN OPERAR. **COPIAR IMAGEN funciona acá también**: arma las dos tablas de la cuenta elegida, APILADAS (siete columnas numéricas al lado se van al doble del ancho de un mail) y con la cuenta en el título — en el mail ya no está el desplegable que dice de cuál es. **La cabecera del reporte NO se dibuja en esta tab**: sus tres números hablan de la mesa entera y abajo hay UNA cuenta. | `/api/ap5/vista` (**un solo request**: los bloques tienen que hablar de la MISMA fecha), `/api/ap5/cuentas`, `/api/ap5/aca/cuentas`, `/api/ap5/aca` | **NO hay selector de día** (2026-08-26): `ap5.portfolio` guarda DOS días —el de la corrida y el anterior, para poder restar la diaria— así que no hay nada que elegir. En POSICIONES DE ACA, un desplegable de **cuenta** al nivel de las tabs, restringido a las cuentas propias (`AP5_CUENTAS_ACA`) y mostrado por DENOMINACIÓN aunque lo que viaja es el número. La barra declara los **faltantes** (símbolos sin multiplicador, cuentas sin nombre/grupo) y el **sello de última actualización** con sus dos relojes (posición y márgenes) | **Sí**: click en cualquier fila del top abre el modal para cargar el **grupo** (COOPERATIVAS / MUNDO ACA / OTROS — la cámara no lo sabe y NO se deduce del nombre, REGLA #9). ⚠️ **`OTROS` no entra en NINGÚN número de la vista**, no sólo en los rankings: el filtro es un predicado SQL único (`sin_otros()`) que llevan todas las queries sobre `ap5.portfolio`, y `tests/unit/test_ap5_otros.py` recorre el módulo y falla si una query nueva lo olvida — antes vivía sólo en `rankings()` y el consolidado, las diferencias del día y el acumulado sumaban `OTROS` sin que nada fallara. **`OTROS` NO es «sin grupo»**: sin grupo sí entra, justamente para que se note que falta clasificarla). **El ACTIVO INTEGRADO se carga A MANO** (2026-08-27, temporal: el dato de la cámara trae errores). Click en el importe de la card → modal con lo que calcula la cámara a la vista, el importe de la mesa, una nota y «volver al calculado» (`POST /api/ap5/activo-integrado`; `importe: null` borra la carga). ⚠️ El gate es **más angosto que el del resto del router**: escritura de **Mesa de Dinero** (`operaciones.mesa_dinero_escritores` + admin, reusando `mesa_dinero.puede_escribir` — no una copia de la lista), no el módulo `operaciones`. El manual **no borra al calculado**: vive en `ap5.activo_integrado` (PK fecha+moneda) y la card muestra `✎ manual` con quién, cuándo, la nota y cuánto daba la cámara. **No se arrastra al día siguiente** — un importe heredado se leería como el dato de hoy sin que nadie lo revisara. El arrastre manual del acumulado se ELIMINÓ junto con `ap5.acumulado` (2026-08-26): el acumulado ES la Σ de `daily_settlement` del día, que ya viene acumulado de la cámara |
| **DEPÓSITOS & EXTRACCIONES** | Entradas/salidas por (día, cuenta, moneda) de `operaciones.negocio_movimientos` (categorías depósito/transferencia/extracción, sin anulados) **+ complemento** de `operaciones.movimientos` (solo los comprobantes que la principal no trae) | `GET /api/cashflow` (proxy de `/operaciones/flujos/serie`) | Rango desde/hasta (def todo el universo), toggles **ARS** y **USD** independientes, granularidad Diario/Mensual, **filtro de cuenta** Todas / Solo accionistas / Sin accionistas / Solo cooperativas + selector de accionista/cuenta — **todo server-side desde 2026-08-19**: antes bajaba el grano (día × cuenta × unidad) de 2 años y React filtraba y agrupaba en el browser, 20.559 filas y 2.512 KB por apertura (medido `scripts/diag_peso_operaciones`); ahora viaja la serie ya agregada, ~28 KB en diario y ~10 KB en mensual. El toggle de monedas NO refetchea (la serie trae las dos) | Ninguna |
| **FINANCIAMIENTO** (`financiamiento-view.tsx`) | Libro VIVO de pagarés/cheques: `portafolio.assets` con `cartera='FINANCIAMIENTO'` y **vencimiento HOY o posterior**, cruzado con la tenencia del último snapshot. 2×2 al 50%: **①** Σ cantidad por cuenta comitente · **②** cantidad + **tasa** por instrumento · **③** barras Σ cantidad por fecha de vencimiento · **④** **CALCULADORA DE DESCUENTO** (`financiamiento-descuento.tsx`, 2 tabs) — reemplaza la planilla Excel con la que la mesa cotizaba el descuento de un cheque/pagaré. **No cruza con los otros tres**: cotiza una operación hipotética, no lee el libro, así que los filtros de la barra no lo tocan. **Se mira NOMINAL y TASA, nunca bruto** (se compran con descuento y la mayoría son dólar-linked liquidados en pesos → el importe pagado no compara entre filas) | `/financiamiento` (un solo GET: manda el grano cuenta × instrumento y el cruce lo hace el front sin refetch) | **CLASE `HD` / `DL` / `SIN CLASIFICAR`** (con su conteo) — la vista muestra **UNA clase por vez y nunca suma dos**: son escalas distintas y un HD de 500.000 junto a un DL de 27.000.000 deja al HD invisible. Sale de `assets.clase_activo`, NO de `tenencia.moneda` (que viaja como columna informativa). Default = primera con datos en el orden fijo HD→DL→sin clasificar. Además: buscador (cliente/cuenta/ticker/emisor), agg del chart DIA/SEM/MES. **Cross-filter 3-way** cuenta ↔ instrumento ↔ barra de vencimiento: cada panel agrega sobre lo filtrado por los otros dos (el panel ④ queda afuera del cruce). Dentro de ④: tab **CALCULADORA** (monto · tasa · días · instrumento CHEQUE\|PAGARÉ · aval SGR) y tab **DATOS** | **Sí, solo en el panel ④ → tab DATOS**: catálogo de SGRs (costo cheque/pagaré + observación) y arancel ACA + derecho de mercado. Lo puede editar cualquiera que entre a FINANCIAMIENTO (módulo `operaciones`) — decisión explícita del user: son parámetros comerciales, no info sensible. **La calculadora en sí NO persiste nada** |

#### Endpoints — `operaciones.py` (`/api/operaciones`), bloque no-comercial
| Método | Path | Qué hace | Params | Escribe |
|---|---|---|---|---|
| GET | `/flujo` | Flujo de contrapartes — operaciones individuales de un día. Match por `id_cuenta` contra `clientes.contrapartes`. **Excluye Futuros/Opciones y caución colocadora** (vienen en pares y duplicarían) | `contraparte`, `moneda`, `segmento`, `desde`, `hasta` | No |
| GET | `/flujo/resumen` | Agregado por (día, contraparte, moneda) + `grupos` + `monedas`. `@cached(300)` | `desde`, `hasta` | No |
| GET | `/flujos` | Movimientos crudos (depósitos/extracciones). `@cached(300)` | `cuenta` (`[N] NOMBRE`), `unidad`, `desde`, `hasta` + `scope` | No |
| GET | `/flujos/resumen` | Resumen por (día, cuenta, unidad) con entradas y salidas separadas. **Ya no lo consume la vista** (lo usa `/flujos/serie` por dentro); queda expuesto por si algo más lo necesita | `desde`, `hasta` + `scope` | No |
| GET | `/flujos/serie` | Lo que DEPÓSITOS & EXTRACCIONES dibuja, ya agregado: serie por periodo × moneda, totales, opciones del selector y bounds del calendario. **Dos rangos distintos**: `ventana_*` es lo que se LEE y `desde`/`hasta` el recorte que se GRAFICA — el desplegable y los topes del calendario salen de la ventana, si salieran del recorte se irían vaciando solos | `ventana_desde`, `ventana_hasta`, `desde`, `hasta`, `agg`, `filtro`, `seleccion` + `scope` | No |
| GET | `/ops/mercados` · `/ops/carteras` · `/ops/segmentos` · `/ops/niveles3` · `/ops/niveles5` | Valores distintos para los selectores (`@cached` 300/600) | — | No |
| GET | `/ops/fechas` | Fechas con operaciones (desc) + count. `@cached(120)` | — | No |
| GET | `/ops/meta` | Metadata del día: nº boletos, última ingesta, nº mercados | `fecha` (**req**) | No |
| GET | `/ops/serie` | Serie diaria Σ bruto. Valida `moneda ∈ {ARS,USD,USD_DOL}` → 400 | `moneda`, `mercado`, `operacion`, `denominacion`, `cuenta`, `segmento`, `nivel_3`, `aca_valores` (`solo\|sin`), `operador`, `cartera`, `excluir` + `scope` | No |
| GET | `/ops/resumen` | Σ bruto por operación / denominación / instrumento + total, con **cross-filter 3-way** | los de arriba + `desde`(**req**), `hasta`(**req**), `instrumento` | No |
| GET | `/ops/agro` | Σ toneladas por periodo y commodity: `serie`, `serie_cuenta`, `serie_share`, `serie_tipo`, `totales`, `totales_tipo`, `por_cuenta`, `por_instrumento` | `desde`/`hasta`(**req**), `agg`, `commodity`, `cuenta`, `nivel5`, `tipo` + `scope` | No |
| GET | `/ops/dolar-futuro` | DLR mercado A3: nocional USD, arancel ARS, boletos | `desde`/`hasta`(**req**), `agg`, `tipo`, `cuenta`, `instrumento`, `nivel5` + `scope` | No |
| GET | `/ops/diferencias-diarias` | Mark-to-market de futuros; instrumento parseado del texto `informacion` | `desde`/`hasta`(**req**), `moneda` (`USDL\|ARS`), `producto`, `cuenta`, `instrumento`, `nivel5` + `scope` | No |
| GET | `/ops/diferencias-fechas` | Universo de fechas PROPIO de esta vista | `moneda` | No |
| GET | `/ops/aranceles` | Σ arancel por periodo + por dimensión + por cliente + por instrumento | `moneda`, `desde`/`hasta`(**req**), `agg`, `cuenta`, `instrumento`, `sel_dim`, `segmento`, `operador`, `dim`, `serie_full` + `scope` | No |
| GET | `/ops/cuentas-list` | Denominaciones + cuenta distintas. `@cached(3600)` | `scope` | No |
| GET | `/financiamiento` | Libro vivo de FINANCIAMIENTO (tab homónima): grano cuenta × instrumento con cantidad + tasa, para los assets con `cartera='FINANCIAMIENTO'` y vencimiento ≥ hoy. La cantidad sale de `portafolio.tenencia` (posición, no flujo) y la tasa de `operaciones.operaciones.tasa` de los boletos MAV, matcheada por (`id_cuenta`, código del corchete de `negocio_movimientos.informacion`). Sin match → `tasa=null` (no se inventa). `@cached(300)`, tope `_MAX_FILAS=20.000` (avisa en `truncado`) | — (solo `scope`) | No |
| GET | `/financiamiento/datos` | Parámetros de la CALCULADORA de descuento (panel ④): catálogo de SGRs con costo cheque/pagaré + observación, arancel ACA Valores, derecho de mercado, IVA (fijo 21 %) y base 365. Siembra el catálogo la PRIMERA vez si está vacío (valores de la planilla original), después no vuelve a opinar. Degrada a vacío si el schema no está aplicado (`disponible:false` → la vista avisa que los aranceles cuentan 0) — la LECTURA nunca tumba la vista FINANCIAMIENTO, pero las ESCRITURAS devuelven **503** con el mensaje accionable (`TablasFaltantes`) en vez de un 500 pelado | — | No |
| PUT | `/financiamiento/datos/aval` | Alta/edición de una SGR (upsert por `nombre`, que es la clave porque es lo que se elige textualmente en la calculadora). Audita en `financiamiento_datos_audit` | — | **Sí** |
| DELETE | `/financiamiento/datos/aval` | Baja de una SGR del catálogo (física: la calculadora no persiste cotizaciones, nadie referencia estas filas) | `nombre` | **Sí** |
| PUT | `/financiamiento/datos/aranceles` | Arancel ACA Valores + derecho de mercado (fila única `id=1`, con CHECK que impide una segunda) | — | **Sí** |
| POST | `/financiamiento/calculadora` | Corre la planilla: NETO SIN AVAL + NETO CON AVAL + CFT + los dos flujos de efectivo. **NO PERSISTE NADA** — simulador por usuario. El costo del aval NO viaja en el request (se manda el NOMBRE y el backend lo resuelve contra el catálogo) para que nadie cotice con un costo que no es el vigente. Fórmulas y unidades: `api/services/financiamiento_calc.py`; los números del Excel están clavados en `tests/unit/test_financiamiento_calc.py` | — | No (calcula, no escribe) |
| POST | `/intraday/analizar` | Recibe el CSV de boletos y devuelve FIFO por (cuenta, especie). **Efímero: NO persiste.** 400 con CSV vacío | `{csv, archivo?}` | No persiste |
| POST | `/intraday/recalcular` | Re-FIFO con solo los trades incluidos | `{posiciones:[…]}` | No persiste |
| POST | `/intraday/marks` | Marks live frescos por especie | `{especies:[…]}` | No persiste |

> Los 3 `intraday/*` viven en este router pero **la vista se movió a `/trading`**; el gate RBAC sigue siendo `operaciones`.

#### Endpoints — `cuentas.py` (`/api/cuentas`)
`GET /accionistas` (de `clientes.accionistas`; `id_cuenta`/`nombre` derivados por regex de `cuenta`,
`grupo` = campo `accionista`; carga manual con `scripts/cargar_accionistas.py`; `@cached 3600`) ·
`GET /contrapartes` (`cuenta=id_cuenta`, `nombre=contraparte`, `grupo=segmento`; `@cached 3600`).

#### Fuentes de datos
`operaciones.operaciones` (origen `jobs.operaciones_informes` → `ingestar_filas_sql`, que normaliza y
enriquece inline `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/`es_cierre`/`commodity`/`mep`;
`jobs.fci_bilateral` escribe `etapa` con upsert por boleto) · `negocio_movimientos` (Diferencias
Diarias) · Depósitos & Extracciones: fuente PRINCIPAL `operaciones.negocio_movimientos`
(`comprobante`→boleto, `importe`→bruto, `fecha` date→ISO, scope por `id_cuenta`) + COMPLEMENTO
`operaciones.movimientos` por los comprobantes que falten (`total`→bruto, fecha dd/mm/yyyy→ISO,
resuelta en Python porque está cruda). El cambio de fuente (2026-08-10) es porque el writer de
`movimientos` mira un solo día y no vuelve: perdía 33-39% de los DEPÓSITOS ·
`operaciones.ops_agregado_diario` (pre-agregado HOT/COLD por día sucio vía `ingestado_en`) ·
`clientes.contrapartes` / `accionistas` / `comitentes` / `aca_valores` · `portafolio.assets`
(join por `assets.unidad = operaciones.instrumento`, **99,0 % del volumen** medido con `diag_ops_cartera`).

#### Notas / rarezas
- **`_ops_where` es el predicado central de toda la vista.** Reglas no inferibles:
  - `anulado_en IS NULL` siempre.
  - **Volumen y arancel NO comparten filtro de cierre**: para bruto se excluye `es_cierre`; con `arancel=True` entran los cierres con `arancel <> 0` (el **arancel de caución vive SOLO en el cierre**).
  - `COALESCE(es_cierre,false)=false` — `es_cierre` NULL (FCI bilateral) significa NO-cierre y debe INCLUIRSE (el viejo `= false` ocultaba las bilaterales; fix 2026-07-08).
  - **FCI bilateral se cuenta UNA vez**: suscripción por su SOLICITUD, rescate por su LIQUIDACIÓN.
- `_multi()`: los filtros enum aceptan multi-selección por comas → `= ANY(...)`, descartando `todos`/`todas`. **NO se aplica a denominación/cuenta** (contienen comas).
- El filtro **CARTERA** usa `EXISTS`, no JOIN, a propósito: así el filtro no puede alterar las sumas aunque cambie el catálogo. `(SIN CARTERA)` y las carteras reales se combinan con OR.
- El param `cuenta` matchea **`id_cuenta`**, no la denominación (para eso está `denominacion`).
- `USD_DOL` (DOLARIZAR) vive **solo en SQL**: el viejo rollup Mongo no traía el bruto en USD.
- La `tasa_pond` viene ya ponderada del backend (`Σ(tasa·bruto)/Σ(bruto)`). La columna aparece según los DATOS, no según el filtro de mercado. `null` ≠ `0`: `0` es una tasa REAL.
- El header "Σ" de *Por operación* usa `denomTotal`, no `total` (bug reportado 2026-07-21: con una cuenta elegida mostraba el total de la mesa junto a una tabla vacía).
- `selDenom` está persistido en sessionStorage porque sin eso la navegación asistida del copiloto escribía el nombre pero no filtraba (bug 2026-07-21).
- **`api/services/_cuentas_filter.py` está DESACTUALIZADO**: devuelve sub-docs `$match` de Mongo y su docstring habla de `Cuentas.AccionistasAPI`. **Ninguna vista de este dominio lo usa**; el equivalente vivo es `portfolio_sql.py::_cuenta_filter_sql`.
- El proxy de contrapartes/cashflow fuerza `Cache-Control: private, no-store` — PII de clientes no va al CDN de Vercel.

### Vista: OPERADORES / Tablero Comercial (`/operadores`)
- **Módulo**: `operaciones` (+ permiso **per-usuario** `control_comercial` para la última sub-vista).
- **Archivos front**: `operadores-view.tsx` (barra madre) → `comercial-operaciones-view.tsx` → `comercial-informe-view.tsx`, `comercial-control-view.tsx`, `cobros-futuros-view.tsx`, `profundidad-clientes-view.tsx`.

**Barra madre (transversal a las 6 sub-vistas)**: **8 filtros MULTI-SELECT que se CRUZAN entre sí** +
toggle de moneda. Se pueblan de `GET /comercial/dimensiones` y cada dropdown ofrece solo los valores
compatibles con lo elegido en los otros. **`[]` = sin filtro = todos.** Todo baja como array repetido
(`&nivel_1=a&nivel_1=b`) → `= ANY(...)` con AND entre dimensiones.

| Filtro | Valores |
|---|---|
| Operador | `operador_email` (label = nombre, con nº de cuentas) |
| Nivel 1 … Nivel 5 | valores distintos de `clientes.comitentes.nivel_1..5` |
| Referido | empresa referidora |
| División | valores de `division`; `__sin_clasificar__` se muestra como "SIN CLASIFICAR" |
| Moneda | `ARS` / `USD` (toggle, no multi) |

**Arranque inteligente**: si el email logueado está en el catálogo de operadores y no hay selección
previa, se auto-selecciona a sí mismo. `GET /api/me` también trae `control_comercial`.

**Selector de período (Desde/Hasta)**, compartido por Informe + Análisis + Portfolio (se oculta en
Cobros Futuros y en Profundidad de Clientes). Semántica: columnas **TOTAL** = `[Desde, Hasta]`; columnas **MES + CTAS OPS** = el **mes
calendario del HASTA** (hasta=30/06 → junio completo, sin importar el Desde); **AuM = foto al HASTA**.

⚠️ **CTAS OPS (y el modo `operativas` de Q1) NO salen de la misma tabla que el volumen.** El volumen sale de `operaciones.negocio_movimientos` filtrando por las 6 categorías de `comercial._CATS_VOLUMEN`, y ahí las cuentas **OTC no existen**: la ingesta las tira por substring en el nombre (`aunesa_negocio._excluir`). Contar cuentas operativas sobre esa tabla hacía que **la misma fila mostrara arancel cobrado y 0 cuentas operativas** — el arancel sí sale de `operaciones.operaciones`, y si le cobramos arancel, operó. Desde 2026-08-31 las dos cuentan con `comercial_sql._act_where` (cualquier boleto no anulado de `operaciones.operaciones`), el **mismo predicado** que DÍAS SIN OPERAR y que la columna ACTIVOS de Profundidad → hay **una sola** definición de «cuenta operativa» en la app, congelada por `tests/unit/test_comercial.py`. Efecto lateral esperado: CTAS OPS también incluye ahora rescates de FCI y futuros DLR, que el volumen sigue excluyendo a propósito.

**KPIs del header** (salvo en Informe y en Profundidad de Clientes — ahí se calcularían al corte,
que en esa tab está oculto): AUM · CLIENTES · VOL. MTD · VOL. YTD.

| Sub-vista | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Portfolio & Operaciones** (def.) | Izq: gráfico de evolución + **Ficha del cliente**. Der: tabla de clientes (60 %) + panel Tenencia/Operaciones (40 %) | `/comercial/operador`, `/serie`, `/portafolio`, `/operaciones`, `/clientes-por-fecha` | Métrica **Volumen**/**AuM**; agregación DIARIO/SEMANAL/MENSUAL; rango `1W/1M/3M/6M/YTD/1A/ALL` con **pan ◀▶**; click en una barra abre los clientes que operaron ese período; tab del panel derecho tenencia/operaciones | **Export a Excel** (Clientes vuelca TODA la ficha, columnas generadas de `FICHA_DATOS`; Tenencia/Operaciones según el tab). No escribe en la DB |
| **Análisis Comercial** | KPIs de cupo (transaccional/usado/libre USD al MEP), **Distribución por Nivel 1**, desglose por **Nivel 3**, y la tabla **ESTADO COMERCIAL** (cuenta, cliente, estado, días sin operar, AuM, cupo trans., cupo usado, última op, niveles) | `/comercial/analisis`, `/comercial/analisis/detalle` (modal) | Orden por columna (persistido); **3 filtros aditivos por click**: nivel_1 + nivel_3 + estado; los pseudo-estados `SIN_AUM` (aum≤0) y `SIN_OP_YTD` también filtran. Hereda barra madre + Desde/Hasta ("foto al día X") | **Export a Excel** (Estado comercial + Distribución nivel 1) |
| **Profundidad de Clientes** | UNA tabla a ancho completo, **una fila por mes** (`jul-25`, `ago-25`, …) desde `PROFUNDIDAD_INICIO` hasta el mes en curso: CLIENTES · CON AuM · SIN AuM · ACTIVOS · RATIO ACTIV. · ARANCELES · ARANC./ACTIVO · AuM. **Todo medido al ÚLTIMO día del mes**; los flujos, sobre el mes completo. La plata va **entera y sin decimales** (`$1.234.567`), no con el compacto del resto de Comercial | `/comercial/profundidad`, `/comercial/profundidad/detalle` (modal) | **NO usa el Desde/Hasta** (su eje ES el tiempo → el control se esconde). Sí hereda la barra madre completa, y los filtros activos se dibujan como **chips arriba de la tabla**. **MultiSelect OPERACIÓN propio de la vista** (no de la barra madre): acota SOLO activos/ratio/aranceles/aranc.-por-activo; clientes, con AuM, sin AuM y AuM siguen siendo la base entera. Moneda ARS/USD — en USD, **al MEP del mes**, no al de hoy | **Export a Excel** (la tabla; y otro dentro del modal) |
| **Profundidad → Análisis Cuantitativo** (solapa DENTRO de Profundidad, no tab propia) | **Tres listas de llamadas por CLIENTE** con sub-nav horizontal estilo AV AGENT (nombre + contador + bajada): **CONOCÉ A TU CLIENTE** (una fila por cliente de UN segmento: arancel 12m · AUM (promedio 12m) · ROA · cupo · SOW · operación favorita; el corte del piso y el ROA promedio del segmento aparecen recién con el segmento elegido — reemplaza a QUIÉNES IMPORTAN y SE ESTÁN APAGANDO, que eran dos listas que había que cruzar a ojo), **PERDIERON AuM** (tienen mucho menos que hace N meses: tenía, tiene, cuánto cayó y si retiró). Arriba, la franja de contexto del mes (operaron · el del medio vs el promedio · top-10 · cuántos hacen el 80 %) | `/comercial/cuantitativo` + `/comercial/conoce-cliente` | **Todos los cortes son editables en pantalla** y los contadores de la sub-nav se mueven con ellos. Mes + moneda + barra madre | **Export a Excel** |
| **Cobros Futuros** | Acreencias del scope: serie diaria acumulable + totales por cliente + detalle por título | `/comercial/cobros-futuros`, `/cobros-futuros/cliente` | Rango propio de fecha de cobro (def hoy → hoy+60), agg, escala `lin`/`log`, moneda, selección de cliente/ticker/bucket. **Oculta el Desde/Hasta global.** Los filtros madre se degradan a **single** | Ninguna |
| **Informe** | 4 cuadrantes de toda la mesa: **Q1** cuentas por segmento (modos `cuentas`/`operativas`/`aranceles`), **Q2** ranking volumen+aranceles por comercial, **Q3** aranceles por segmento, **Q4** detalle del segmento | `/comercial/informe`, `/informe-segmento`, `/informe-aranceles-segmento`, `/informe-segmento-detalle` | Desde/Hasta, moneda, filtros madre (**el `operador` madre va SOLO al ranking Q2**; en Q1/Q3/Q4 `operador` es el drill-down del comercial clickeado). Click en comercial re-scopea Q1/Q3/Q4; click en segmento filtra Q4; tab de Q4 clientes/operaciones; **toggle SOLO OPERATIVAS** en Q4·Clientes (deja las cuentas con ≥1 boleto en el mes del corte — el MISMO predicado que CTAS OPS; el flag `opero_mes` viaja por fila, así filtra sin refetch y no puede quedar desfasado de lo dibujado). ⚠️ **A propósito NO es un filtro de la vista**: si achicara también el universo, el % de Q1 (`operativas / cuentas del segmento`) daría **100 % en todos los segmentos** y dejaría de significar nada — misma regla que el filtro de operación de Profundidad, que toca el numerador y nunca la base; botón `?` de ayuda | Ninguna |
| **Control Comercial** (solo con `me.control_comercial`) | **Tabla 1** totales ALyC por períodos fijos; **Tabla 2** por comercial en [Desde,Hasta] (activos/inactivos/AuM/volumen/comisiones, cada uno con % vs rango anterior de igual largo); **Tabla 3** Actual vs Objetivo + % alcanzado | `/comercial/control/totales`, `/por-operador`, `/objetivos-vs-actual`, `/objetivos`, `/comercial/operadores` | Desde/Hasta propios (def mes en curso), moneda, filtros madre. **La Tabla 1 NO depende de Desde/Hasta** (períodos fijos Día/Semana/Mes/YTD/12M/2025/2024/Total, anclados a la última fecha con operaciones) | **SÍ — `PATCH /comercial/control/objetivos`**: editor inline (año + mes + inputs volumen/comisiones por comercial, botón guardar por fila). **Export**: un Excel con 3 hojas |

#### Endpoints — bloque `/api/operaciones/comercial/*` (24)
`GET /operadores` (catálogo) · `GET /dimensiones` (combos que pueblan y cruzan los filtros madre) ·
`GET /operador` (KPIs `aum_gestionado`, `n_clientes`, `volumen_mtd`, `volumen_ytd` + lista de clientes
con ficha, en una pasada) · `GET /serie` (sin `id_cuenta` → operador; con → cliente; gate
`verificar_id_cuenta_opcional`) · `GET /clientes-por-fecha` · `GET /portafolio` (**gate
`verificar_id_cuenta`**) · `GET /operaciones` (`limite` 1..1000, def 300; gate `verificar_id_cuenta`) ·
`GET /analisis` (`fecha` = foto al día X) · `GET /analisis/detalle` (modal de auditoría; gate
`verificar_id_cuenta`) · `GET /profundidad` (`@cached 300`; `desde`/`hasta` en **YYYY-MM**, NO usa
`fecha`) · `GET /profundidad/detalle` (`@cached 120`; `mes` req, `metrica`, `limite` 1..5000 def 500) · `GET /cobros-futuros` (`operador` **req**, single, `__todos__`) ·
`GET /cobros-futuros/cliente` (gate `verificar_id_cuenta`) · `GET /referido-clientes` ·
`GET /referido-fci` · `GET /informe` (`@cached 300`) · `GET /informe-segmento` ·
`GET /informe-aranceles-segmento` · `GET /informe-segmento-detalle` (`max_ops` 1..20000 def 1000 capea
el payload; `n_operaciones` trae el total real) · `GET /control/objetivos` (`anio` req) ·
**`PATCH /control/objetivos`** (`{operador_email, anio, mes, volumen_objetivo?, comisiones_objetivo?}`
→ `clientes.objetivos_comerciales`) · `GET /control/totales` · `GET /control/por-operador` ·
`GET /control/objetivos-vs-actual`. **Las 4 rutas `/control/*` llevan `require_control_comercial`**
(incluidas las de lectura).

#### Modal de auditoría "DÍAS SIN OPERAR"
Click en una fila de ESTADO COMERCIAL → `GET /comercial/analisis/detalle`. Mismo patrón que el modal por
celda de Tesorería: el número tiene que poder abrirse y mostrar de qué boleto sale.
- **El predicado vive UNA sola vez**: `comercial_sql._ULT_OP_WHERE = "anulado_en IS NULL"`, compartido por la tabla y el modal → no pueden divergir. Cuenta **CUALQUIER boleto no anulado**, sin filtro de tipo/mercado/etapa/es_cierre.
- Devuelve cabecera (denominación, operador, nivel_1/3, estado legal, fecha de alta de legajo), `ultima_op(+_dmy)`, `dias_sin_operar`, `estado`, **`ecuacion` en texto** (`"09/08/2026 (hoy) − 07/08/2026 (última op) = 2 días"`), `umbrales`, `n_boletos_ultima_fecha`, `n_excluidos`, `n_posteriores_corte` e `items[]` con todos los boletos crudos.
- Cada item trae `es_ultima` (●) y `excluido` + `observacion` con el motivo (`"anulado el DD/MM/AAAA — no cuenta"`, `"boleto sin fecha de concertación"`, `"posterior al corte — no cuenta en la foto"`).
- **Dos queries en vez de una con LIMIT a propósito**: con muchos boletos anulados posteriores, un LIMIT podría dejar afuera justo el boleto que fija el número.

#### PROFUNDIDAD DE CLIENTES — la serie mensual de la base
Service: `api/services/profundidad_sql.py`. Contesta "cuánta base tengo, cuánta está viva y cuánto
deja" mes a mes. **Todo se mide al ÚLTIMO día del mes** (jul-25 → 31/07/2025); los flujos son el mes
completo `[01, fin]`.

| Columna | Definición |
|---|---|
| **Clientes** | `comitentes` con `estado='Activa'` del scope y `fecha_alta_legajo <= fin de mes` (mismo universo que Q1 del Informe). |
| **Con AuM** | valuación > 0 en el snapshot de `portafolio.tenencia` (`aum='si'`) **más reciente <= fin de mes**. |
| **Sin AuM** | clientes − con AuM. |
| **Activos** | ≥ 1 boleto en el mes bajo `comercial_sql._act_where()` — **el MISMO predicado que ESTADO COMERCIAL**. |
| **Ratio activ.** | activos / clientes. |
| **Aranceles** | `comercial_sql._arancel_where()` (arancel > 0, etapa ≠ solicitud, **cierres incluidos**: la caución cobra en el cierre). El arancel se guarda siempre en ARS. |
| **Aranc. / activo** | aranceles / activos. |
| **AuM** | suma del AuM de TODOS los clientes del mes en esa foto. |

- **Tres queries agregadas para toda la tabla**, no una por mes: (1) `comitentes` agrupado por fecha
  de alta → el acumulado por mes se arma en Python; (2) `operaciones` con un `JOIN` contra una CTE de
  meses (rango por mes sobre `ix_ops_concertacion`, meses disjuntos) agregando en **dos pasos**
  —primero por (mes, cuenta), después por mes— porque un `count(DISTINCT id_cuenta)` obliga a
  Postgres a ordenar todos los boletos del período (medido con 210k filas: sort en disco, 0,69 s →
  0,38 s con HashAggregate); (3) `tenencia` con un `LATERAL max(fecha)` por mes.
- **Una celda sin dato vale `null` (—), NO 0.** Si no hay ningún snapshot de tenencia <= fin de mes,
  las columnas de AuM quedan vacías: "no pude mirar" ≠ "no había nada" (mismo invariante que el AV
  AGENT). Las celdas `null` tampoco son clickeables — no hay nada que auditar.
- **La foto de AuM casi nunca cae justo el 31.** Cada fila viaja con `aum_snapshot` y
  `aum_desfasaje_dias`, y la celda se marca con `*` cuando el desfasaje no es 0.
- **`fuera_universo`**: boletos de cuentas del scope que NO estaban en el universo del mes (sin fecha
  de alta, o alta posterior). No suman, pero se cuentan y se reportan — una diferencia que no se ve
  es la que se descubre tarde y mirando una pantalla.
- **USD = al MEP del MES**, no al de hoy (`api/services/_mep.get_mep_for_date`): dolarizar un jul-25
  con la cotización de hoy haría que el histórico cambie todos los días sin que haya pasado nada. Se
  devuelven los DOS factores usados (`mep_aranceles` = último día del mes, `mep_aum` = día del
  snapshot), porque pueden ser distintos.
- **Lo que la tabla NO puede saber va escrito en `meta.advertencias`**, a la vista en el pie: cuántas
  cuentas del scope no tienen fecha de alta, y que `estado='Activa'` **no es histórico** (la base de
  un mes viejo se reconstruye con las cuentas que HOY están activas).

#### ANÁLISIS CUANTITATIVO — tres listas de llamadas
**Dónde vive**: es una **solapa adentro de Profundidad de Clientes**, no una tab de la
barra de Operadores. Profundidad tiene un conmutador fino (*Por mes* / *Análisis
Cuantitativo*) al principio de su barra; la sub-nav grande estilo AV AGENT —con
contador y bajada— existe UNA sola vez, adentro de Análisis Cuantitativo, para sus tres
solapas. La tabla mensual es la vista principal y no paga dos bandas de navegación.

Service: `api/services/cuantitativo_sql.py`. **No es un tablero: son listas de nombres.**
Cada fila lleva un motivo en castellano y cuánta plata hay en juego, y las tres van
ordenadas **por plata**, no por gravedad de la señal.

| Lista | Quién entra | Cortes |
|---|---|---|
| **QUIÉNES IMPORTAN** | los que acumulan el `pct_arancel` % del arancel del mes, clasificados por si vienen seguido | `pct_arancel` (80), `meses_seguido` (8 de 12) |
| **SE ESTÁN APAGANDO** | lleva más de `multiplo` veces **su propio ritmo** sin operar | `multiplo` (3), `min_dias_op` (6) |
| **PERDIERON AuM** | cayó más de `caida_pct` % contra la foto de hace `meses_atras`, con AuM previo > `piso_aum` | `caida_pct` (75), `meses_atras` (3), `piso_aum` (**10.000.000**) |

- **Ningún corte viene fijo.** Llegan por query-string y se validan contra `CORTES`
  (rango + descripción); fuera de rango vuelven al default en vez de generar una lista
  que no significa nada. La respuesta trae `cortes` (lo aplicado) y `cortes_def` (rangos
  y qué hace cada uno) para que la pantalla los dibuje editables.
- **UNA sola llamada** devuelve las tres listas + el contexto: los contadores de la
  sub-nav tienen que corresponder a lo que se va a ver, y cambiar de solapa no puede
  salir a buscar de nuevo. El **contador se calcula sobre todas las filas** y recién
  después se capea la lista (`limite`).
- **El cuadrado de 4** cruza dos preguntas: ¿deja mucho? × ¿viene seguido? La celda que
  importa es **GRANDE IRREGULAR** — mucha plata, poca regularidad: cuando dejan de venir
  su silencio parece normal y con el estado comercial de hoy nunca disparan nada.
- **El ritmo son DÍAS OPERADOS, no boletos** (cinco boletos el mismo día son una sola
  aparición) y es la **mediana** de los gaps del último año. Sin `min_dias_op` días
  distintos la cuenta NO entra: una mediana sobre 2 datos es basura.
- ⚠️ **«Lleva sin operar» se cuenta hasta `_hasta(fin)` = `min(fin, hoy)`, nunca contra
  una fecha futura.** En el mes EN CURSO `fin` es el último día del mes (el 31), así que
  un cliente que operó anteayer figuraba *"lleva 20 días"* y entraba a la lista por
  tiempo que todavía no pasó — y el número crecía solo a medida que avanzaba el mes. En
  un mes ya cerrado manda `fin`: la lista de julio tiene que decir lo que se veía el 31
  de julio, no un número distinto cada día. Congelado por test.
- **PERDIERON AuM muestra hechos y NO los rotula.** Hubo una columna "QUÉ PASÓ" que
  etiquetaba cada fila (*se está yendo* / *fue mercado* / *se llevó los títulos*); se
  sacó porque eran inferencias con el mismo aspecto que los datos de al lado. Los cuatro
  números —tenía, tiene, cuánto cayó y **si retiró** (`cashflow_sql.neto_por_cuenta`, con
  `hasta`)— dicen lo mismo sin pedirle a nadie que confíe en el rótulo. El caso de
  interés sigue siendo legible solo: cayó a cero **sin retirar un peso** es una
  transferencia de títulos a otro agente.
- El contexto (`n_operaron`, **mediana y promedio juntos**, top-10 %, `cuantos_80`) viaja
  siempre: *"SE ESTÁN APAGANDO 5"* no significa nada si no sabés que 17 clientes hacen el
  80 % de la facturación.
- **«¿Cómo se interpretan los datos?» es un DICCIONARIO, y sale en modal**
  (`cuantitativo-ayuda.tsx`): qué entra en la lista · qué significa cada columna ·
  precisiones. **No dice qué hacer con las filas.** La primera versión sí lo hacía
  (*"es una llamada para hoy"*, *"entrá por los grande irregular"*) y estaba mal por dos
  motivos: le ponía a los datos un significado que el sistema no puede saber, y quedaba
  desactualizada sola — seguía explicando la columna QUÉ PASÓ semanas después de que se
  borrara de la tabla. Una definición envejece cuando cambia la columna; un consejo
  envejece solo. Los cortes que el usuario mueve entran al texto como números: un texto
  con el 80 % escrito fijo miente apenas alguien toca el corte.
- **Los contadores NO se suman entre sí**: la misma cuenta puede estar en las tres, y ése
  es justo el cliente al que hay que llamar primero. Va escrito en `avisos`.
- ⚠️ Universo y predicado de actividad son **los mismos que PROFUNDIDAD** (`comitentes`
  activas del scope con alta <= fin de mes, `comercial_sql._act_where`) — si divergieran,
  las dos tabs contarían clientes distintos y ninguna fallaría.

#### CONOCÉ A TU CLIENTE — `GET /comercial/conoce-cliente`
Service: `api/services/conoce_cliente_sql.py`. Una fila por CLIENTE dentro de UN
segmento (`nivel_3`). **Reemplaza a QUIÉNES IMPORTAN y SE ESTÁN APAGANDO**, que eran
dos listas que había que cruzar a ojo para encontrar la fila que importa: un cliente
grande que se está apagando. Ahora el ritmo es una **marca (⚠) en la fila**.

| Columna | Qué es |
|---|---|
| **Arancel 12m** | `_arancel_where` sobre la ventana (cierres INCLUIDOS) |
| **AUM** | AuM **PROMEDIO** de las fotos de fin de mes de la ventana (la columna se llama AUM en pantalla) |
| **ROA** | `arancel ÷ aum`. Se MUESTRA en plata (`roa_pesos_millon`: pesos por año por cada millón guardado); `roa_bps` viaja igual y va en el tooltip. `null` debajo del piso |
| **Cupo** | `comitentes.cupo_transaccional_ars` (carga manual, sin fecha) |
| **SOW** | `aum ÷ cupo`, en % |
| **Operación favorita** | el tipo con MÁS ARANCEL (no el de más volumen) |

⚠️ **SIN `segmento` NO DEVUELVE FILAS** — devuelve la lista de segmentos y un aviso.
No es una validación: el ROA de un institucional y el de un retail no son comparables
(medido: las medianas por segmento van de **22 a 58 bps** y el orden es al revés del
intuitivo — **PJ MEDIANA 58, PJ GRANDE 28**). Mezclados, los institucionales caen
todos juntos al fondo y la tabla marca como oportunidad lo que es estructura.
`__todos__` tampoco es un segmento válido acá.

⚠️ **TODA COLUMNA DERIVADA SE VERIFICA CON LAS DOS DE AL LADO**: `ROA = Arancel ÷
AUM` y `SOW = AUM ÷ Cupo`, con los dos términos de cada cociente en pantalla. Por
eso **AUM es el promedio y no la foto de hoy**: mostrar la foto y dividir por el
promedio hace que el primero que saca la calculadora deje de creerle a la pantalla.
El valor de hoy viaja igual (`aum_hoy`, en el tooltip).

⚠️ **EL ROA SE MUESTRA EN PLATA, NO EN BPS.** Es el mismo número × 100: 20 bps =
**$2.000 por año por cada millón guardado**. Los bps son el estándar de la industria
y siguen viajando (ordenan igual, y están en el tooltip), pero "$2.000 por millón" no
hay que traducirlo — y cierra la vuelta completa a ojo: una cuenta con $100.000.000 y
$2.000 por millón dejó $200.000, que es exactamente la columna de arancel de al lado.

⚠️ **EL PISO DEL ROA NO ES COSMÉTICO.** Con la foto de hoy como divisor, el p90 del
ROA del libro daba **67.816 bps (678%)**: una cuenta que opera y barre la plata el
mismo día divide por casi cero. No es un cliente que rinde muchísimo, es una división
rota. Debajo de `piso_aum` (def **$1.000.000** de AuM promedio) el ROA vale `null` y
viaja el motivo — y son **tres** motivos distintos, no uno: no hay fotos · la cuenta
no tiene tenencia en ninguna · tiene menos que el piso.

⚠️ **`piso_roa` (front) y `piso_aum` (PERDIERON AuM) son DOS cortes**, aunque el
parámetro HTTP de los dos se llame `piso_aum`. En PERDIERON decide **quién entra** a
la lista; acá decide **desde cuándo el ROA significa algo**. Compartir la clave en el
`usePersistedState` haría que mover uno cambie la otra lista en silencio.

**La vista NO trae el ritmo de cada cuenta.** Se probó como una marca ⚠ en la fila y
se sacó: no estaba pedida, y arrastraba una consulta entera para dibujar un símbolo.
`cuantitativo_sql.ritmo_por_cuenta` sigue existiendo (la usa SE ESTÁN APAGANDO).

**Contexto del segmento** (`contexto`): lo único que la pantalla muestra es
**`roa_promedio_pesos`**, pegado al corte del piso —subir el piso saca cuentas del cálculo
y el número se mueve en el acto—. `roa_mediana` viaja igual y va en el tooltip: un
promedio de cocientes se lo lleva puesto la cola, y si los dos están lejos no es que
el segmento rinda eso, es que hay tres cuentas tirando. La franja de contexto que
listaba n clientes · medianas · apagándose **se sacó** (no se pidió y era ruido).
Medido en el libro: **203 cuentas** tienen AuM y no dejaron un peso
en 12 meses, y **18 de ellas concentran el 96%** de esa plata quieta ($23.900M) — no
aparecían en ninguna pantalla porque justamente no operan. Por eso el orden por
defecto es **por AuM**: quedan arriba de su segmento con el ROA en cero.

#### Ficha operativa de un cliente — `GET /comercial/cliente/perfil`
Service: `api/services/perfil_cliente_sql.py`. **Módulo aparte y genérico a propósito**:
nace para el detalle de SE ESTÁN APAGANDO, pero el share por tipo de operación se va a
reusar en otras pantallas, así que no recibe nada de esa vista — solo `id_cuenta`.

Devuelve, de una cuenta y en la ventana de los últimos N meses (def 12, máx 60):
1. **`ultima_op`** — el boleto que la fija, crudo (fecha, días, operación, instrumento,
   mercado, bruto, arancel, etapa, es_cierre). **Sin tope de ventana**: la última es la
   última aunque sea de hace dos años; acotarla haría que un cliente dormido muestre "—"
   y parezca que nunca operó.
2. **`serie_aranceles`** — `[{mes, label, arancel, volumen, n_boletos}]` para el gráfico
   de barras. **La lista de meses se arma en Python, no sale de la query**: un mes sin
   operaciones tiene que dibujar una barra en CERO, porque un mes ausente y un mes en
   cero no se ven igual en un gráfico.
3. **`share_operacion`** — el reparto del volumen por tipo de operación. El `%` se
   calcula en el backend: es el número que se va a reusar, y dos lugares que lo derivan
   terminan mostrando dos porcentajes distintos del mismo cliente.
4. **`serie_aum`** — el AuM del cliente a fin de cada mes. **Un mes sin foto de tenencia
   vale `null`, no 0**: que el cliente no tenga filas en una foto que SÍ existe es cero
   (no tenía nada); que no haya foto es "no pude mirar", y dibujarlo en cero sería
   inventar una caída.
5. **`tenencia`** — la cartera de HOY, reusando `comercial_sql.portafolio_cliente`: es
   la misma que muestra Portfolio & Operaciones, así las dos pantallas no pueden mostrar
   carteras distintas del mismo cliente.

⚠️ **El CIERRE se junta con su APERTURA.** En esta base `operacion` trae valores
separados (`Caución tomadora` / `Caución tomadora cierre`): el cierre nunca tiene volumen
—la apertura ya lo contó— pero se lleva **todo el arancel**, así que separados una fila
muestra 0 % con toda la plata y la otra todo el volumen con arancel cero. La fusión
empareja por nombre normalizado (sin acentos, sin la palabra «cierre») **con las guardas
de la REGLA #9**: solo se fusiona una fila cuyos boletos sean TODOS de cierre, la apertura
tiene que EXISTIR en el mismo resultado, y el destino no puede ser otro cierre. Lo que no
se pudo emparejar queda solo y marcado `solo_arancel` — «no pude» ≠ «lo invento». Las
fusiones aplicadas viajan en `fusiones` para poder auditarlas.

**Cómo lo dibuja el modal** (`perfil-cliente-modal.tsx`, 1.280 px): la última operación
en UNA línea chica (fecha · hace N días · tipo · instrumento · mercado — **sin importe ni
arancel del boleto**: de un boleto suelto no dicen nada y eran lo que partía la línea en
dos), después los dos gráficos lado a lado (arancel por mes | AuM a fin de mes, ejes Y
separados) y abajo los dos inventarios lado a lado (**EN QUÉ OPERA** | **QUÉ TIENE HOY**,
una fila por línea). Ninguno de los cuatro bloques ocupa el ancho entero: una barra
estirada de punta a punta no agrega información, y el ancho que se llevaba es el que
hacía que los nombres de las especies se cortaran hasta no distinguirse.

⚠️ **`ultima_op` no se acota al período NI cuenta los días contra él.** Los días van
contra HOY: contra el fin del mes elegido, un mes en curso da días de más (el 31 todavía
no llegó) y uno viejo devuelve "cuánto había pasado en ese momento", que no es lo que
nadie lee ahí.

⚠️ **VOLUMEN y ARANCEL no se filtran igual, y confundirlos devuelve un número plausible
y equivocado:**

| | Cierres | `etapa='solicitud'` | Anulados |
|---|---|---|---|
| **Volumen** (`bruto`) | **EXCLUYE** — la apertura de la caución ya lo contó | fuera | fuera |
| **Arancel** | **INCLUYE** — el arancel de caución vive SOLO en el cierre | fuera | fuera |

Por eso cada tipo de operación trae **sus dos números con su propio filtro**: la caución
sale con volumen (de la apertura) *y* arancel (del cierre). Con un solo predicado, la
caución mostraría arancel cero y el total de la tabla no cerraría contra el del gráfico.
Un tipo que deja arancel sin volumen propio se marca con `solo_arancel`; uno que no deja
ni volumen ni arancel (solicitudes sueltas) no se lista.

⚠️ **El `bruto` viene en la moneda del boleto** → se pesifica con
`comercial_sql._pesif(alias, col)` usando el **mep DEL BOLETO**, no el de hoy: si no, el
share de un mes viejo se movería con el dólar de esta mañana. Esa expresión vive **una
sola vez** (`_PESIF` de `negocio_movimientos` es la misma función con otra columna).

Gate: **`verificar_id_cuenta`** (403 fuera del grupo del usuario), `@cached 120`.

#### Filtro de OPERACIÓN (Profundidad de Clientes)
`?operacion=` (multi, `= ANY`). Sale de `operaciones.operaciones.operacion`, que es un valor
del catálogo `operaciones.tipos_operacion` (`data->>'operacion'`) — separa **compra / venta /
caución tomadora / caución colocadora / suscripción y rescate de FCI / futuros**. ⚠️ **NO separa
bono de acción**: los dos son `compra`/`venta`; eso necesitaría cruzar `instrumento` contra
`portafolio.assets`.

- **Acota SOLO lo que se operó.** `profundidad_sql.METRICAS_FILTRABLES` = `activos`,
  `ratio_actividad`, `aranceles`, `arancel_por_activo`. `clientes`/`con_aum`/`sin_aum`/`aum`
  salen de `comitentes`/`tenencia` y **no se filtran nunca**: si el denominador también se
  filtrara, el porcentaje dejaría de significar "qué parte de mi base usa este producto".
  Congelado por `test_el_universo_y_el_aum_no_saben_del_filtro`.
- **Con el filtro puesto RATIO cambia de sentido** (pasa de actividad a penetración del
  producto), así que el backend manda **los encabezados ya escritos** (`columnas`:
  `"Operaron caución tomadora"`, `"% que operó caución tomadora"`). El front NO arma ese texto
  — con 3 o más operaciones el sufijo se corta a `"3 operaciones"`.
- **Las opciones se leen de la base** (`operaciones_disponibles`, con label y nº de boletos),
  scopeadas al rango de la tabla y a los filtros madre, **y NUNCA con el filtro de operación
  aplicado**: si se poblara con su propio resultado, al elegir "caución" quedaría una sola
  opción y no habría forma de volver. Congelado por `test_las_opciones_no_se_filtran_a_si_mismas`.
- **El modal recibe el MISMO `operacion`** o abriría una celda de 47 y mostraría 389 cuentas.
- ⚡ La query de actividad asigna el mes con `date_trunc` y acota con un **rango cerrado**, no
  con un join contra una lista de meses: medido con 300k boletos, el join hacía que el planner
  materializara los boletos y los comparara contra los 14 meses (**798.039 filas descartadas
  por el join filter**, O(meses × boletos)). Con el rango cerrado entra por `ix_ops_concertacion`
  una sola vez — el endpoint filtrado bajó de 0,33 s a 0,17 s.

#### Modal de auditoría por CELDA (Profundidad de Clientes)
Click en cualquier celda con dato → `GET /comercial/profundidad/detalle?mes=YYYY-MM&metrica=…`.
Mismo principio que los otros dos modales de la app.
- Las **8 métricas** (`clientes`, `con_aum`, `sin_aum`, `activos`, `ratio_actividad`, `aranceles`,
  `arancel_por_activo`, `aum`) salen de UNA sola query por cuenta para ese mes; la métrica solo decide
  **qué cuentas listar y por qué columna ordenarlas**. Un test exige que toda métrica declarada tenga
  su filtro y su título: una columna nueva no puede quedar clickeable sin abrir nada.
- **Los totales se calculan sobre TODAS las cuentas y recién después se capea la lista** (`limite`,
  def 500) → el tope no puede hacer que el modal contradiga al número de la tabla. El pie lo dice.
- Devuelve `ecuacion` en texto (`"3 clientes − 2 con AuM = 1"`, `"1 activos / 3 clientes = 33,33%"`),
  el `snapshot_aum` usado y el desfasaje. El buscador del modal filtra **client-side** sobre lo ya
  traído: filtrar en el server cambiaría los totales.

#### Estado comercial (`comercial.py::estado_comercial`, puro)
`NUEVA` (nunca operó) · `ACTIVA` (≤ `dias_activa`) · `ENFRIANDOSE` · `DORMIDA` (> `dias_dormida`).
**Defaults del backend: `dias_activa=45`, `dias_dormida=90`.** Labels del front: Activa / Enfriándose /
Dormida / **"Sin Operaciones"**. ⚠ El front usa `30/90` como fallback si la respuesta no trae los
umbrales — **discrepancia con el default real (45)**, aunque el backend siempre los devuelve.

#### Fuentes de datos
`clientes.comitentes` (QUIÉN: operador_email, nivel_1..5, referido, division, cupo_transaccional_ars,
cupo_usado_ars, fecha_alta_legajo, estado) · `clientes.cuentas` · `clientes.operadores` ·
`manager.manager_users` (operador↔usuario + flag `control_comercial`) · `operaciones.operaciones`
(ACTIVIDAD) · `operaciones.negocio_movimientos` (cost-basis / volumen del Control) ·
`portafolio.tenencia` (`aum='si'`, TAMAÑO) · `clientes.objetivos_comerciales` ·
`operaciones.acreencias` · `valuaciones.dolar` (MEP histórico, solo Profundidad en USD). **Todo se cruza por `id_cuenta`, agregando EN VIVO con índices** (sin precompute).

#### Notas / rarezas
- El Tablero es **SQL-only** (`_com_motor` siempre devuelve SQL); los selectores `_motor()`/`_engine` y los flags `*_SQL` son vestigiales.
- `analisis_comercial` hace **UNA pasada sobre comitentes** para universo + ficha + cupos: antes el mismo predicado se evaluaba 5 veces por request, y la query de cupos ya había **divergido** (omitía nivel_2/4/5 del scope).
- **El cupo NO es histórico**: en modo foto todo se recalcula al corte salvo el cupo, que queda actual. Siempre en **USD al MEP del día**, independiente del toggle ARS/USD.
- **El cupo USADO es una foto + el flujo del cliente** (2026-08-09). `clientes.comitentes.cupo_usado_ars` se carga a mano y nada la actualiza — quedó congelada en el 2026-06-01 (`cupo_cargado_en` ni siquiera se escribió: está NULL en las 1.567 cuentas). Ahora el tablero le SUMA al leer la plata que entró/salió del cliente desde esa fecha, con la misma fuente que la vista CASHFLOW (`cashflow_sql.neto_por_cuenta`, sobre `operaciones.movimientos`). El ancla es `config.CUPO_BASE_FECHA` y **hay que moverla el día que se recargue el cupo**. No se persiste (nada que doble-contar); el ajuste aplicado viaja en `cupo_flujo_usd` para auditarlo. Los USD se pesifican con la cotización del día del movimiento — esa tabla no tiene snapshot de MEP — y si no hay cotización, el movimiento se descarta en vez de contarse como pesos.
- `opero_mtd`: con `desde`, el flag pasa a significar "operó en `[desde, corte]`" en vez del mes calendario.
- **PROFUNDIDAD DE CLIENTES es la única sub-vista que ignora el Desde/Hasta**, y por eso ese control (y los KPIs, que se calculan al corte) se esconden mientras está activa: un filtro a la vista que no hace nada se lee como una tab rota.
- El primer mes de PROFUNDIDAD es una **constante** (`profundidad_sql.PROFUNDIDAD_INICIO = "2025-07"`, el arranque del ejercicio), no se deriva de la fecha de hoy. Mover el ejercicio = mover esa constante (o pasar `desde=YYYY-MM`, que el endpoint ya acepta).
- El predicado de "operación" y el de "arancel" viven UNA sola vez, aliasables: `comercial_sql._act_where()` / `_arancel_where()`. Los comparten ESTADO COMERCIAL, su modal, PROFUNDIDAD y `control_comercial_sql` — antes el del arancel estaba copiado en 3 archivos, idéntico y sin nada que lo mantuviera igual.
- **Los filtros madre NO se podan** cuando el cross-filter achica las opciones de otro nivel (rompía selecciones previas).
- `_filtros_madre` convierte cada lista a **tupla** porque `datos_totales_alyc` está `@cached` y la key debe ser hashable.
- Cobros Futuros es la única sub-vista con filtros **single**.
- Cobertura de arancel vs volumen en el Informe (panel de ayuda): volumen excluye cierres de caución; arancel **incluye** los cierres; siempre se excluyen las solicitudes sin liquidar.

### Vista: CONTRAPARTES (`/contrapartes`)
- **Módulo**: `operaciones` (lectura). El **ABM** vive en Manager bajo `manager` ∨ `manager_contrapartes`.
- **Proxy**: sin `dia` compone el resumen de los **últimos ~2 años**; con `?dia=` hace el drill-down. `private, no-store`.
- Sin tabs: **dos layouts mutuamente excluyentes** según el filtro Día.

| Modo | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Rango** (Día vacío, def.) | 50/50. Izq arriba tabla **Contrapartes** (contraparte, bruto, % share). Izq abajo barras Σ bruto por bucket, coloreado ARS (`#094293`) vs USD. Der tabla **Meses** | `GET /api/contrapartes` → `/operaciones/flujo/resumen` | **Desde**/**Hasta** (clampeados a min/max de los datos). **Chips de grupo** (segmento de la contraparte, multi-toggle, arrancan todos). **Chips de moneda** (multi). **Mini-chips de moneda de la tabla** (single). Agregación DIARIO/MENSUAL. **Cross-filter**: contraparte ↔ mes | Ninguna |
| **Día** (Día con valor) | Tabla ancho completo con las **operaciones individuales del día**: boleto, tipo, cuenta, contraparte, segmento, unidad, bruto, moneda — orden por \|bruto\| DESC | `GET /api/contrapartes?dia=` → `/operaciones/flujo` | Filtro **Día**; los chips de moneda y grupo siguen aplicando (client-side) | Ninguna |

**ABM — `api/routers/manager/contrapartes.py`** (gate `manager` ∨ `manager_contrapartes`):
`GET /contrapartes` (`segmento`, `contraparte`, `q`) · `GET /contrapartes/segmentos` ·
**`PATCH /contrapartes`** (`contraparte`/`segmento`/**`codigo_mae`** de una cuenta existente, 404 si no
existe; `codigo_mae=""` borra el código) · **`POST /contrapartes`** (alta de 1 click desde el
conciliador, idempotente) · **`POST /contrapartes/import`** (Excel: completa campos de cuentas ya
existentes, **nunca borra ni da de alta**; las filas sin match se reportan; 1–20000) ·
`GET /contrapartes/reconcile` (pega a Aunesa **LIVE, on-demand** — no cachear ni pollear; `limit` 1–2000
def 500; 502 si Aunesa falla).

**Fuentes**: `clientes.contrapartes` (`id_cuenta` CLAVE, `contraparte`, `segmento` = el "grupo" de la
vista, `codigo_mae` = destino MAE `FXXX` fondo / `C+CUIT` comitente / `SXXX` aseguradora, que consume el
Excel MAE de SENEBIS) · `operaciones.operaciones` · Aunesa live (solo el conciliador).

**Rarezas**: `flujo_operaciones` excluye Futuros/Opciones/caución **colocadora**
(`tipo_operacion !~* 'Futuros|Opciones|colocadora'`) porque las cauciones vienen en pares · el
`segmento` de este endpoint **NO es el nivel_1 del cliente**: es `split_part(tipo_operacion,' ',1)`, la
sesión de mercado del boleto · todos los filtros bajan a SQL desde 2026 (antes se traía todo el scope y
se descartaba en Python, y como el cache es por combinación de params **cada filtro re-ejecutaba el
fetch completo** — el filtro multiplicaba el costo) · el proxy pide **2 años fijos** hacia atrás y los
inputs Desde/Hasta filtran client-side dentro de lo que ya bajó · **este endpoint NO tiene `scope` de
grupos** (a diferencia de `/flujos`) — **SIN VERIFICAR** si es decisión consciente (las contrapartes son
entidades de mercado) o un hueco.

### Vista: REFERIDOS (`/referidos`)
- **Módulo**: `operaciones`. Vista **para la empresa referidora**: solo sus cuentas.

| Bloque | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Vista principal** | KPIs del referido (nº clientes, AuM total, vol mes/año, arancel mes/total) + gráfico + tabla de clientes + panel de detalle | `/comercial/dimensiones`, `/comercial/referido-clientes`, `/comercial/serie` | **Selector de referido** (ordenado por nº de cuentas). **Moneda** ARS/USD. **Métrica del chart**: `valuacion` (AuM, línea) / `rend` (TWR base 100, **requiere cliente elegido**) / `volumen` (barras). **Rango** MTD/1M/3M/YTD/1A/ALL (client-side, desde la última fecha de la serie) | **Export a Excel** por tabla |
| **Detalle del cliente** (sub-tabs `pnl`/`ops`) | `pnl`: posiciones con valor actual, PnL no realizado y total. `ops`: boletos recientes | `GET /api/aum-pnl?id_cuenta=`, `/api/valuaciones/{id}/mensual`, `/comercial/operaciones` | Sub-tab persistido, hereda moneda | Export a Excel |
| **Tabla FCI** | La **COMISIÓN a la coop** por fondo, agrupada por sociedad gerente con subtotal: `saldo`, `fee`, `comision` | `GET /comercial/referido-fci` | **Desde/Hasta** propios (def mes en curso). Hereda referido + moneda | Export a Excel |

**Fórmula de la comisión FCI** (`comercial.py::referido_fci`, no inferible):
- `saldo` = **saldo promedio diario** = Σ(valuación de los días CON foto) ÷ nº de días con snapshot en el rango. Los días sin tenencia ponderan 0.
- `fee` = honorario **ANUAL** del fondo (`assets.FEE_ADMIN`, fracción). Varía por fondo. `None` si no se cargó.
- `comision = saldo × fee × (días_corridos_del_período / 365)`.
- **El `fee` es una tasa: NO se convierte a USD** cuando `moneda=USD` (el resto sí se divide por el MEP).

**Rarezas**: el chart `metric=rend` solo funciona con cliente elegido (usa `mensual.twr_base100 − 100`) ·
el nombre del fondo se limpia en el front (`"[1004] CAFCI632-1004 - Balanz Retorno Total - Clase A"` →
`"Balanz Retorno Total - Clase A"`) · `referido_clientes` resuelve el universo llamando
`_cuentas_de_operador(TODOS, None, None, referido)` (el operador es comodín) · **no hay endpoint dedicado
de catálogo de referidos**: se deriva de `/comercial/dimensiones` sumando `n_cuentas`.

### Filtros de cuenta transversales de este dominio
1. **`scope_cuentas` (grupos)** — el único que es de SEGURIDAD. Lo reciben `/flujos`, `/flujos/resumen`, `/flujos/serie`,
   `/ops/serie`, `/ops/resumen`, `/ops/agro`, `/ops/dolar-futuro`, `/ops/diferencias-diarias`,
   `/ops/aranceles`, `/ops/cuentas-list`. Verificación puntual por cuenta en `/comercial/portafolio`,
   `/operaciones`, `/analisis/detalle`, `/cobros-futuros/cliente` (403) y `/comercial/serie` (opcional).
   **SIN scope**: `/flujo`, `/flujo/resumen`, `/ops/{mercados,carteras,fechas,meta,segmentos,niveles3,
   niveles5,diferencias-fechas}`, **todo `/comercial/*` salvo esos cuatro**, y los dos de `/api/cuentas`.
   Trampa documentada: el regex bracketed `^\[(id)\]` NUNCA matchea en `operaciones.operaciones` (ahí
   `cuenta` es el id pelado) → el scope de esa tabla va por igualdad sobre `id_cuenta`, indexable.
2. **`_cuentas_filter.py`** (`todas`/`accionistas`/`sin_accionistas`/`cooperativas`/`productores`) —
   filtro de NEGOCIO, legacy Mongo, **sin consumidores en este dominio**.
3. **Filtros de negocio propios** — `aca_valores`, `operador`, `excluir` (denominaciones ocultadas,
   comparadas contra `COALESCE(NULLIF(denominacion,''),'(sin)')`), y los filtros madre.

---

## 4.8 PORTFOLIOS — AUM, CARTERAS y ESTRATEGIA

### Vista: AUM (`/aum`)
- **Módulo**: `portfolios` (`_PORTFOLIOS = [verify_api_key, require_module("portfolios")]`) | **Roles**: admin, trader, asistente_comercial.
- **Archivos front**: `aum-view.tsx` (1575 líneas: `AumView`, `AnalisisDinero`, `CuentaCombobox` exportado, `TickerCard`, `Kpi`, `PanelHeader`, `DateStepper`).
- **Router**: `carteras.py` (`/api/portfolio`) → `portfolio_sql.py` (+ `pnl_sql.py`, `comercial{,_sql}.py`).

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **TOTAL** (def.) | Chart de área EVOLUCIÓN AUM + leaderboard POR CARTERA (valuación + % share) + panel DETALLE con dos sub-tablas cruzadas POR CUENTA y POR ASSET | `/portfolio/total-serie`, `/total-snapshot`, `/operadores`, `/niveles`, `/carteras`, `GET /api/me` | **OPERADOR** (filtro MADRE; si el email logueado matchea un operador arranca autoscopeado) · **NIVEL 1 / NIVEL 2 / NIVEL 3 / NIVEL 5** (solo en TOTAL; **multi-select** con buscador — mismo `MultiSelect` que la barra de `/operadores`. Adentro de un nivel los valores van con OR, entre niveles con AND, y todo intersecta con OPERADOR. Se **cruzan**: cada desplegable ofrece sólo lo que convive con lo elegido en los otros, contra los combos de `/niveles`. Botón `LIMPIAR` cuando hay alguno activo) · **CARTERA** (solo en TOTAL; **multi-select**. Ojo: NO es un nivel — los niveles eligen CUENTAS y CARTERA elige POSICIONES dentro de ellas, así que viaja como param propio de `total-serie`/`total-snapshot` y no por `scope_aum`. Recorta el chart, el leaderboard y el TOTAL a la vez; las opciones salen de `/carteras`, que sólo ofrece las que existen en la foto dentro del scope puesto) · **FECHA** (select de todas las fechas de la serie; también se setea clickeando el chart) · **MONEDA** ARS/USD · **CUENTAS** (`todas`/`accionistas`/`sin_accionistas`/`cooperativas`/`productores`) · rango del chart client-side `1M/3M/6M/YTD/ALL` · click en cartera del leaderboard (toggle) = **drill-down**, no filtro: pinea UNA cartera y sólo afecta al panel DETALLE (el filtro CARTERA de la barra, en cambio, recorta también chart y TOTAL) · click en fila POR CUENTA / POR ASSET = pin (toggle, se cruzan con AND) · dos buscadores substring · botón `↺` | **Export .xlsx** con 2 hojas (`Por Cuenta`, `Por Asset`), refleja los filtros activos; archivo `aum-detalle-<fecha>-<ts>.xlsx` |
| **FCI** (`?tab=fci`) | Chart EVOLUCIÓN FCI + leaderboard por **SOC. GERENTE** (emisor) + DETALLE con cards por ticker/fondo y sus cuentas | `/portfolio/fci-serie`, `/fci-snapshot` | OPERADOR · CUENTAS · rango client-side · click en soc. gerente · `DateStepper` ◀▶. **NO tiene selector de MONEDA ni filtros de NIVEL**; el snapshot FCI **no soporta "última fecha"** (`fecha` requerida) | Sólo lectura, **sin export** |
| **ANÁLISIS DE DINERO** | Diferencia de saldo por cuenta entre dos fechas: 3 KPIs (TOTAL DIFERENCIA, CUENTAS NUEVAS, CUENTAS CERRADAS) + tabla ordenable con tags `NUEVA`/`CERRADA` | `/portfolio/diff` (+ la serie solo para poblar fechas) | **PLAZO**: `DÍA ANTERIOR`/`−7 DÍAS`/`−1 MES`/`MTD`/`YTD`/`CUSTOM` (los presets resuelven client-side la fecha disponible más cercana ≤ target); si CUSTOM, dos selects · **MONEDA** · **OPERADOR** heredado · orden client-side. **No pasa `cuenta_filter`** (el proxy lo acepta, el componente no lo manda) | Sólo lectura, sin export |

**Endpoints (`carteras.py` — 12, TODOS GET; el router no tiene un solo POST/PUT/PATCH/DELETE)**:
`/niveles` (combos operador × nivel_1/2/3/5 de las comitentes activas — pueblan y CRUZAN los filtros
madre de AUM; reemplazó a `/niveles-1`, que devolvía una lista suelta y no permitía cruzar) ·
`/carteras` (carteras presentes en el AuM de la última foto, con # de posiciones; pasa por
`scope_aum` para no ofrecer una cartera que con ese scope no devuelve ninguna fila) ·
`/operadores` · `/aum` (filas crudas de `portafolio.tenencia`; `id_cuenta`, `unidad`,
`cuenta`, `desde`, `hasta`, `ultimo` + `scope`; 403 fuera de scope) · `/pnl` (`id_cuenta` req, gate
`verificar_id_cuenta`) · `/pnl-todas` (**lee cache**, no recalcula; `filtro_cuenta` + `scope`) ·
`/cuentas` · `/fci-serie` · `/fci-snapshot` (`fecha` **req**) · `/total-serie` (devuelve
`{serie:[{fecha,total,por_cartera,mep_used?}], moneda, fechas_sin_mep}`) · `/total-snapshot`
(**`fecha` ausente = el backend resuelve la última y la devuelve**) · `/diff` (`fecha_actual` y
`fecha_anterior` **req**; devuelve `filas`, `total_diff`, `n_nuevas`, `n_cerradas`, `mep_missing_*`).

**Fuentes**: `portafolio.tenencia` (fuente ÚNICA, siempre `aum='si'`; writer `jobs.portafolio_backfill
--diario`, `0 11 * * 1-5`) · `portafolio.assets` (en `total_*` es `LEFT JOIN` con
`COALESCE(NULLIF(cartera,''),'OTROS')`; en `fci_*` es INNER filtrando `cartera IN ('FCI','CARTERA FCI')`)
· `valuaciones.dolar` (último `mep` con `timestamp <= fecha 23:59:59`; sin mep la fecha entra en
`fechas_sin_mep` y el valor queda en ARS sin convertir, banderizado por el front) · `clientes.comitentes`
· `clientes.accionistas` (`cooperativas` = NO accionista **y** `cuenta ~* '\ycoop'`) ·
`valuaciones.pnl_totales_cache` (`jobs.pnl_totales_precompute`, `5,35 15-22 * * 1-5`).

**Rarezas**: **dos dependencies de scope distintas y NO intercambiables** — `scope_aum` (grupos ∩
`operador` ∩ `nivel_1`) la usan `fci-serie/snapshot`, `total-serie/snapshot`, `diff`; mientras
`listar_aum`, `pnl-todas` y `cuentas` usan `scope_cuentas` **pelado** → **el filtro madre no alcanza al
listado crudo ni al PnL agregado** · `GET /portfolio/aum` **no tiene proxy Next ni consumidor** ·
`total_snapshot` sin `fecha` devuelve la fecha resuelta en el body, lo que permite pedir serie y snapshot
**en paralelo** al montar (FCI no tiene esa capacidad) · **la cuenta 255 SÍ aparece** en el path SQL (se
sacó la exclusión a pedido del usuario) · cambiar de tab/operador/nivel_1 **resetea** los drill-downs ·
el leaderboard POR CARTERA se cruza con cuenta/asset pero **no consigo mismo**.

### Vista: CARTERAS / VALUACIONES (`/valuaciones`)
- **Módulo**: `portfolios`. Label del nav: **"Carteras"**.
- **Archivos front**: `valuaciones-shell.tsx` → `carteras-informe-view.tsx` (RESUMEN/ACTIVOS/MÉTRICAS), `carteras-evolucion-view.tsx` (EVOLUCIÓN), `pnl-titulos-view.tsx` (721), `pnl-totales-view.tsx` (404) → `por-cuenta-view.tsx`. El `CuentaCombobox` se **importa desde `aum-view.tsx`**; `Pill`/`Panel`/`Dato` y los formateadores, desde **`ui/informe.tsx`** (los comparte con `/aca`); el color y la abreviatura de cada cartera, desde **`lib/carteras.ts`**.
- **Barra superior común**: selector **CUENTA** (tipeable, filtra por id o denominación) + botones ◀/▶ + las 5 pills. La lista viene de `/api/portfolio/cuentas`; el default es la cuenta **`100` (ACA VALORES S.A.)**. Cuenta y sub-tab persisten **en la URL**.
- **Barra INFERIOR (2026-08-21)**: **AJUSTES** y **TOTALES** se mudaron a la barra de estado del layout, al lado de BRIEFING / PARA VOS / AV AGENT, y **solo aparecen mientras esta vista está abierta**. Es jerarquía: arriba está el informe —tabs que se recorren leyendo— y ahí un botón de escritura de eventos corporativos y una pantalla que ni mira la cuenta elegida competían con lo que la gente entra a hacer. TOTALES **sigue siendo una sub-tab de verdad** (mismo `?sub=totales`, mismo componente, mismo cuerpo): lo único que cambió es por dónde se entra, y el botón funciona como toggle (vuelve a RESUMEN). Ver la sección **Patrones transversales → slot de la barra inferior**.

> **REFACTOR 2026-08-21 — la vista pasó a leerse como el informe de `/aca`.** La tab
> PORTAFOLIO era un tablero de cuatro paneles apretados (posiciones + auditoría +
> chart + mensual) donde cada número había que buscarlo. Se partió en las tres tabs
> con las que la mesa ya lee una cartera —**RESUMEN · ACTIVOS · MÉTRICAS**— más
> **EVOLUCIÓN**, que es la mitad de abajo del tablero viejo. PNL TÍTULOS y TOTALES
> quedaron intactas: son otra pregunta.
>
> Lo importante no es el layout: **el front dejó de calcular**. Montos,
> ponderaciones, share, totales por moneda y los espejos en dólares vienen
> resueltos de `GET /{id}/vista` (`api/services/carteras_informe.py`), igual que en
> `/aca`. Con la fórmula duplicada del lado del navegador la pantalla podía
> contradecir al informe y ninguna de las dos versiones era la verdad — y el PDF
> que viene después habría sido una tercera implementación.

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **RESUMEN** (def.) | **La CINTA** — una sola pieza continua (no cards con aire entre medio) con 4 datos: posición al, valuación ARS, valuación USD (con el MEP) y **valuación oficial (con el A3500)**. COSTO / PNL / GANANCIA **se sacaron** (2026-08-21): la primera línea contesta cuánto VALE, no cuánto se ganó — el PnL está título por título en ACTIVOS y mes a mes en EVOLUCIÓN. Debajo, torta de composición **por cartera** + dos cuadros comparativos (el snapshot elegido y el **cierre del mes anterior**) con monto, ponderación, **Total Dolarizado / Total Pesos** y la fila `Sin clasificar` | `GET /{id}/vista` | (los de la barra del informe, comunes a las 3 tabs) | Sólo lectura |
| **ACTIVOS** | Un cuadro **por cartera** (total y ponderación en la cabecera) con ticker, emisor, calificación, clase, vencimiento, cantidad, precio, valuación, **% dentro de la cartera**, PnL y Gan %. A la derecha, el panel **AUDITORÍA** con el PnL, el flujo y los boletos del título elegido | `GET /{id}/vista` | ídem | Sólo lectura. **Click derecho sobre una fila** abre menú **OPERAR** → `/operar` (no se opera efectivo; FCI va a la tab FCI con el buscador prefilleado) |
| **MÉTRICAS** | **3 columnas, una por EJE** (clase de activo · emisor · calificación), cada una una lista continua con una **barra proporcional detrás de cada fila**. En clase de activo la cartera es un renglón de título, no un panel propio. Rediseñada el 2026-08-21: antes era una grilla de paneles de UNA fila cada uno y el 80% de la pantalla era aire. ⚠️ La barra se escala contra la fila más grande del bloque, **no contra 100%** — con efectivo en descubierto hay filas negativas y los shares no suman 100 | `GET /{id}/vista` | ídem | Sólo lectura |
| **EVOLUCIÓN** | Chart mensual (métrica VALOR o RENDIMIENTO) + tabla **MENSUAL** (mes, último día, cierre, flujo neto = depósitos−extracciones, Δ valor real, PnL acumulado, TEM del mes, TEA cartera = base100−100) con los **movimientos del mes desplegables inline**; al elegir un mes aparece abajo el panel de **variación** (efecto mercado vs efecto operado) contra el cierre anterior | `/{id}/mensual`, `/{id}/movimientos?fecha=`, `/{id}/variacion?fecha=` | métrica VALOR/RENDIMIENTO · rango `3M`/**`6M`**/`1A`/`ALL` + paginado ◀▶ · **MONEDA** ARS/USD (en USD el XIRR usa cashflow con MEP por fecha) | Sólo lectura. Export .xlsx de la evolución mensual |
| **PNL TÍTULOS** | Split 50/50: izq posiciones por ticker (cost-basis weighted-average) con KPIs VALOR ACTUAL y PNL NO REALIZADO; der detalle del ticker (KPIs COSTO/VALOR/PNL/NO REAL/COBROS/GAN % + **boletos del stock actual** con compras/ventas/neto y breakdown del PnL pasivo; los pseudo-boletos de ajuste van resaltados en ámbar) | `GET /api/aum-pnl?id_cuenta=` → `/api/portfolio/pnl` | MONEDA · orden por columna · click en fila (toggle) | Sólo lectura. 2 exports .xlsx (`exportarTodo`, `exportarBoletos`). **Botón AJUSTES en la barra del shell** (admin) → modal `pnl-ajustes-modal.tsx` |
| **TOTALES** — **NO depende de la cuenta seleccionada** | **POR TÍTULO** (def.): 5 KPIs (PNL TOTAL, NO REALIZADO, PASIVO, VALOR ACTUAL, POSICIONES) + tabla por (cuenta, ticker) 60 % + detalle 40 %. **POR CUENTA**: una fila por cuenta con valor, PnL acumulado y **base 100** (mostrada como rendimiento %), en ARS y USD | `GET /api/aum-pnl-todas` → `/portfolio/pnl-todas`; `GET /api/valuaciones/consolidado` | POR TÍTULO: select de filtro de cuenta (5 valores, **único filtro server-side**) · dos buscadores substring · MONEDA — **el botón USD se DESHABILITA si el cache no trae valores USD**, con tooltip "Falta recalcular el cache (jobs.pnl_totales_precompute)" · orden por columna. POR CUENTA: mismo select + buscador + orden + **checkbox "ocultar saldo muerto"** (client-side, `\|valor_ars\| < 100.000`, def ON) | Sólo lectura, sin export |

**Barra del INFORME** (segunda fila, común a RESUMEN / ACTIVOS / MÉTRICAS): `input type=date`
para ver la tenencia a cualquier día (sin snapshot exacto el backend resuelve el cierre anterior más
cercano, `asof=True`, y la barra muestra la fecha real) · botón **`HOY ✕`** · **T0 / T1** (solo en modo
actual) · **ARS / USD** —se **deshabilita** si no hay MEP para esa fecha, y la preferencia no se pierde:
la moneda efectiva se deriva, así vuelve a valer sola al elegir una fecha que sí lo tiene— · **⬇ EXCEL**
con las tres tabs en tres hojas, armado con el MISMO payload que la pantalla · **REPORTE**
(ver abajo).

**REPORTE (modal, `carteras-reporte-modal.tsx`, 2026-08-21)** — el informe como DOCUMENTO, hoja por
hoja, con el mismo formato que el REPORTE FIN DE DÍA de Interbanking: azul de la casa `#094293` y logo
arriba de CADA hoja, A4 apaisado. Las hojas son: **Resumen ejecutivo** · **una por cartera** de
ACTIVOS · **Métricas** · **Evolución**. Sale del MISMO payload de `/vista` (no recalcula ni vuelve a
consultar); lo único que pide aparte es `/{id}/mensual` para la hoja de evolución. El PDF es
`window.print()` con reglas `@media print` que viven DENTRO del componente (solo existen mientras el
modal está abierto) — no hay librería de PDF: ninguna reproduce el CSS de la app sin sorpresas y el
resultado de imprimir ES lo que se ve, porque es el mismo DOM.

⚠️ **Tres cosas que rompieron el PDF y quedaron resueltas** (medidas, no supuestas):
- **El modal va por PORTAL a `<body>`.** Renderizado dentro de la vista, su ancestro
  `<main class="overflow-hidden">` recorta todo lo que pase de la primera pantalla al imprimir: 6
  hojas en el modal salían como **1 página**.
- **`visibility: hidden` sobre la app NO alcanza** para esconderla: el elemento sigue ocupando y
  recortando. Lo que pagina es sacarla del flujo (`display: none` a los hermanos del overlay, que con
  el portal son hermanos de verdad).
- **`.reporte-overlay > *` con `display: block` le pegaba también al `<style>`** de las propias reglas
  → un bloque vacío al final = una hoja en blanco de más (6 hojas → 7 páginas). Se apunta al
  contenedor por id.
- **El alto de hoja es `min-height: 205mm`, no 210**: el útil de un A4 apaisado es 210 justos, así que
  pedir 210 hace que cualquier redondeo empuje unos píxeles a una página siguiente.

**Cómo se arma cada cosa** (`api/services/carteras_informe.py`):
- **Las carteras se DERIVAN de la posición**, ordenadas por monto desc. Acá no hay 4 canónicas como en
  ACA: una cuenta tiene las carteras que tiene (HD, RENTA VARIABLE, MONEDAS, FCI…). Ese orden es el
  mismo en el cuadro comparativo, en los bloques de ACTIVOS y en los paneles de MÉTRICAS.
- **El comparativo** es el último snapshot conciliado ANTERIOR al mes del que se está viendo
  (`portafolio.tenencia`, nunca `tenencia_live`). Se pide **sin** `con_pnl`: el cost-basis del motor es
  siempre a HOY, colgarlo de una foto vieja daría un GAN % sobre una posición que ya no existe. Una
  cartera que existía en ese cierre y hoy no, va a la fila **Otras** — si se descartara, el cuadro
  sumaría menos que su propio total y nadie lo notaría.
- **Total Dolarizado / Total Pesos** usan la regla de **Manager → ACA** (`aca.moneda_regla`), no una
  propia: dos tablas de reglas para la misma pregunta es el patrón que prohíbe la REGLA #9 del repo.
  Lo que ninguna regla ubica NO se reparte a dedo — cae en `Sin clasificar` y la vista lo canta.
  **Única excepción, y no es una segunda regla: el EFECTIVO.** Una regla por cartera no puede partir
  `MONEDAS`, que tiene los pesos y los dólares adentro; para el cash el instrumento ES la moneda
  (`USD`/`USDC`/`USDL` → usd, `ARS` → ars), con las constantes que ya usa `api/services/valuaciones.py`.
- **Los dos denominadores de MÉTRICAS no son el mismo**, a propósito: la clase de activo responde cómo
  se compone ESA cartera (denominador: la cartera); el emisor y la calificación responden cuánto pesa
  ese riesgo en toda la cuenta (denominador: el total).
- **Hay DOS tipos de cambio, y los dos vienen del backend**: el MEP (al que se puede salir hoy) y el
  **A3500** del BCRA (el oficial con el que se reporta hacia afuera), que sale de `macro.series_macro`
  clave DOLAR con `punto_asof` — la MISMA fuente que el briefing, y tomando el último cierre con fecha
  <= la pedida porque un sábado no tiene fixing. El comparativo se cuenta al TC de SU día, no al de
  hoy: si no, la variación en dólares sería en parte el movimiento del tipo de cambio.
- **El espejo USD de los agregados viaja resuelto** (`monto_usd`, al MEP del snapshot) para que el
  toggle cambie QUÉ CAMPO se muestra y nunca haga una cuenta. La **ponderación NO se duplica**: es la
  misma en las dos monedas. Ojo: eso es una conversión a UN tipo de cambio — el costo y el PnL de cada
  título traen su propio `*_usd` del motor, que ancla cada compra a SU MEP, y no se derivan de ahí.

> ⚠️ **EL VALOR DE HOY SALE DE `portafolio.tenencia_live` (2026-08-12).** Esta vista —
> y SOLO esta — muestra la posición del DÍA en vez de la foto conciliada de ayer:
> - **PNL TÍTULOS** (`/portfolio/pnl` → `pnl_sql.pnl_por_cuenta_sql(base="live_t1")`):
>   las CANTIDADES salen de `tenencia_live` horizonte `t1` (= con lo concertado hoy
>   adentro). Precios, normalizador por cartera, cost-basis y la cadena de fallback
>   (live → cierre → aum) NO cambian.
> - **MENSUAL** (`/{id}/mensual` → `valuacion_mensual`, `mes_actual_live=True`): solo
>   la fila del **mes EN CURSO** cierra al día de hoy, con la columna `valuacion` que
>   `tenencia_live` ya trae de Aunesa — **la misma clase de precio que el histórico**,
>   así el único cambio contra la serie de siempre es la fecha base. Los meses
>   anteriores salen de `portafolio.tenencia` igual que siempre. La fila trae
>   `live: true` para que la UI la etiquete: ese valor todavía se mueve y **al cerrar
>   el mes se recalcula con la serie histórica** (puede cambiar un poco).
>
> **Es opt-in y no se filtra**: el motor de PnL y `_calcular_meses` los comparten la
> vista VALUACIONES, el asistente de IA y el cron de `valuaciones.pnl_totales_cache`,
> y todos siguen con la foto (default sin tocar). Lo fija
> `tests/unit/test_carteras_tenencia_live.py`.
>
> **Efecto colateral conocido**: la tab **TOTALES** lee `pnl_totales_cache`, que llena
> un cron con la foto → el listado de cuentas y el detalle de una cuenta pueden
> mostrar totales distintos. No es un bug: son dos fuentes con distinta fecha base.
> Si el daemon `tenencia_live` no corrió, todo cae solo a la foto.

**Endpoints (`valuaciones.py` — 8, TODOS GET)**: `_validate_id_cuenta` exige numérico (400); todos los
`/{id_cuenta}/*` llevan `verificar_id_cuenta` (**403 fuera del scope de grupos**).
`/consolidado` (una fila por cuenta: valor, base 100, PnL acumulado, TEM, TEA en ARS y USD; **lee
cache**) · **`/{id}/vista`** (el INFORME entero —resumen + activos + métricas— en UN request; es lo que
sirve RESUMEN/ACTIVOS/MÉTRICAS y de donde va a salir el PDF) · `/{id}/serie` (ya **no** lo consume la
vista: alimentaba el panel de posiciones del tablero viejo) · `/{id}/mensual` (cierre del mes + flujos externos + XIRR ARS/USD + TEM + TWR
base 100) · `/{id}/movimientos` (`fecha` **req**, auditoría boleto por boleto) · `/{id}/variacion`
(`fecha` **req**; separa **efecto mercado (precio)** de **efecto operado (cantidad)**, el efectivo va a
`otros`) · `/{id}/posiciones-actuales` (`asof=True`) · `/{id}/posiciones` (**LEGACY "Phase 2"**: cost
basis + PnL realizado y no realizado, con completeness por ticker).

**Fuentes**: `portafolio.tenencia` (+ LEFT JOIN `assets` — desde el 2026-08-21 el SELECT trae también
`vencimiento`, que hasta entonces volvía `None` y dejaba la columna vacía) · `operaciones.negocio_movimientos` (flujos
externos y boletos del cost-basis; **cada boleto lleva `mep` snapshot inmutable**, la pesificación es
`importe × b.mep` con fallback a `_get_mep_for_date()` solo si es null) · `valuaciones.consolidado`
(cron `jobs.consolidado_cuentas`, `30 12 * * 1-5`) · `valuaciones.pnl_totales_cache` ·
`portafolio.assets` (mapping `unidad ↔ ticker` por campo `TICKER`, **no** por regex) ·
`valuaciones.portfolio_snapshot` (pricing live, cache 5s) · `mercado.snapshots_cierre` (cache 60s) ·
`valuaciones.dolar`.

**Rarezas**:
- **`/api/valuaciones` NO figura en `ENDPOINT_MODULE_PREFIXES`** — el gate viene solo del `dependencies=_PORTFOLIOS`.
- El motor de PnL (`pnl.py::_pnl_por_cuenta_core`) es lógica **pura** sobre dicts inyectados. Regla crítica: `pnl_no_realizado = valor_aum − costo_remanente` (**NO** `qty × precio_actual`, porque el precio del AuM viene en paridad cruda).
- Categorías que entran al cost-basis: `compra, venta, suscripcion_fci, rescate_fci, acreencia`. `comision` se **ignora** (el `importe` ya viene neto). "Licitación" del primario se categoriza como `compra`.
- **PNL TÍTULOS muestra un total distinto al del backend**: la UI calcula `no_realizado + pasivo` y **excluye el realizado** a propósito. En **TOTALES** el total sí suma `no_realizado + pasivo + realizado_dia` → **las dos sub-tabs no usan la misma definición de "PNL TOTAL"**.
- **TOTALES y consolidado NO recalculan en vivo**: si el cron no corrió, la vista muestra datos viejos.
- Bucket de cierre vs mes calendario: el snapshot del **día 1** representa la valuación al INICIO del mes == cierre del mes ANTERIOR → se **reasigna al bucket del mes anterior**.
- `valuacion_mensual_debug` existe en el service pero **no está expuesto** por el router (audita `flujos_detalle` + el cashflow exacto del XIRR, reproducible con TIR.NO.PER).
- `/{id}/posiciones` (legacy) tiene proxy Next pero **ningún componente lo consume**.

**AJUSTES DE PnL (2026-08-10)** — modal desde el botón AJUSTES de la barra (`pnl-ajustes-modal.tsx`,
proxy catch-all `/api/portfolio/pnl-ajustes/*`): ABM de ajustes por **eventos corporativos sin
boleto** (split de CEDEAR, canje de especie, posición pre-data) que rompen el cost-basis. Persisten
en `operaciones.pnl_ajustes` y `pnl_sql._deps_sql` los mergea al stream de boletos (motor:
`ajuste_split` multiplica cantidad sin tocar costo; `ajuste_cantidad` suma/resta con costo
proporcional, sin realizado). `id_cuenta` vacío = GLOBAL (un split se carga UNA vez para todas las
cuentas del ticker). Lectura módulo `portfolios`; **escritura: admin todo (incl. globales) +
el OPERADOR de cada cuenta puede ajustar SOLO sus cuentas** (ownership por
`clientes.comitentes.operador_email`; `require_escritura_ajustes` + `_verificar_alcance` en el
service) con audit before/after (`pnl_ajustes_audit`).
El modal incluye **DESFASES DETECTADOS** (`GET /pnl-ajustes/candidatos`, lee el cache de TOTALES):
tickers con ratio `qty_aum/qty_calc` consistente entre cuentas + factor sugerido → botón USAR
precarga el form. Impacto: `/portfolio/pnl` al instante; TOTALES/consolidado en la próxima corrida
de sus crons. Doc: `MOTOR_VALUACIONES.md` § "Ajustes manuales por eventos corporativos".

### Vista: ESTRATEGIA (`/retorno`) — ELIMINADA (2026-08-30)
La vista se dio de baja entera: COMPARAR INVERSIÓN y DESCOMPOSICIÓN se borraron (código front,
proxies `/api/comparar/*`, endpoints `/analitica/{comparar,comparar/bonos,descomposicion-retorno,
rolldown-esperado}` y services `comparar_inversion.py` / `descomposicion_retorno.py`), y
**ANÁLISIS SENSIBILIDAD se mudó tal cual a RESEARCH** (ver 4.5) — mismo componente, mismo endpoint.
El módulo `estrategia` salió de `core/roles.py::MODULES`; si sigue apareciendo en
`manager.role_matrix` (prod) es residual y no gatea nada. La necesidad de "¿qué pasa si invierto
X?" que cubría COMPARAR la toma el modal **SIMULAR INVERSIÓN** de `/renta-fija` (ver 4.2).

### Router auxiliar TÍTULOS (sin vista propia)
Montado `_PUBLIC` con comentario explícito ("antes estaba bajo `_PORTFOLIOS` y para roles sin ese módulo
`allFlujos` venía vacío"). **Contradicción viva**: `api/auth.py` mapea `("/api/titulos","portfolios")` y
`src/proxy.ts` también → **para `sales`, `back_office` e `invitado` el proxy devolvería 403 antes de
llegar al backend**, anulando el fix. En la práctica no se dispara porque `renta-fija/page.tsx` pide
`/api/titulos/flujos` **server-side con `apiFetch` directo al `API_URL`** → el matcher de `proxy.ts`
nunca ve ese request. `/api/titulos/assets` **no tiene consumidor**.

---

## 4.9 BACK OFFICE · SENEBIS · TESORERÍA · MESA DE DINERO

### Vista: BACK OFFICE (`/back-office`)
- **Módulo**: `back-office` (`_BACK_OFFICE` sobre `back_office.router` y `senebis.router`) | **Roles**: admin, trader, sales, asistente_comercial, back_office. **NO** invitado.
- **Proxies**: tesorería `[[...path]]` GET/PUT/POST/DELETE que **mapea CUALQUIER error a `502 {error}`**; senebis catch-all que pasa **bytes crudos** para no corromper el `.xlsx`; acreencias solo GET; **interbanking GET + escrituras SOLO bajo `/gastos/*`** (la integración hacia el banco es de lectura y el proxy es la cerradura que mira a internet; lo único que escribe es la clasificación de gastos, sobre tablas nuestras).

| Tab | Qué muestra | Endpoints | Filtros | Acciones de escritura |
|---|---|---|---|---|
| **Senebis** | Órdenes SENEBIS + espejo del Excel Quantex y del Excel MAE. Poll 10s (= heartbeat de presencia) — **UN request: `GET /vista`** trae órdenes + los 2 espejos + presencia + próximo ID (2026-08-13; antes eran 3 requests que corrían la MISMA query, ~13 viajes a la base por ciclo y por usuario → ahora ~7). `/ops`, `/excel` y `/excel-mae` siguen vivos pero **DEPRECADOS** | `/senebis/{vista,ops,excel,excel-mae,opciones,comitentes,export,export-mae}` + writes | FECHA: chips `HOY`/`TODO`; ESTADO: `TODAS`/`PENDIENTES (n)`/`COMPLETADAS`; MAE: `CON`/`SIN`(`mae=sin`)/`SOLO`(`mae=solo`). El backend además acepta `especie` — **sin control en la UI** | Alta/edición/borrado (allowlist), toggle estado, botón ⚠ EDITADA, reasignar ID, ABM de agentes, ajustar PRÓXIMO ID (admin), descargar los 2 `.xlsx` |
| **Tenencia Valorizada** (**default**) | Serie diaria de AuM de las cuentas propias **100/255/256** por cartera + posiciones por título del día | `/tenencia-hd`, `/tenencia-hd/posiciones`, `POST /tenencia-hd/precio` | `cartera` = **HD** (Cartera USD, def) / `ARS`; selector de día; toggle de estado `TODOS`/`SIN GAR`/`SOLO GAR`/`SIN ALQUILER`; toggle `÷100` (paridad) en el editor | **Editar a mano el PRECIO** de una unidad de un día (recalcula las 3 cuentas + totales en `portafolio.tenencia`). **Sin allowlist propia**: alcanza el módulo `back-office` |
| **Títulos en Alquiler** | **PORTFOLIO ALQUILER** (lista curada, serie diaria + posiciones) y **MARCAS** (todos los pares título·cuenta tenidos desde `desde`, con marca SI/NO, cantidad, desde/hasta) | `/tenencia-hd/en-alquiler`, `/portfolio-alquiler`, `/portfolio-alquiler/posiciones`, `/instrumentos`; 3 POST | buscador `q` client-side, toggle `soloAlq`, fecha `desde` (def 2026-06-01 en el front) | Marcar/desmarcar alquiler por (título, cuenta) con cantidad y período; agregar/quitar títulos de la lista; editar nominales por día (carry forward). **Sin allowlist propia** |
| **Interbanking** | Los bancos de ACA, para CONCILIAR. **UNA sola vista** (ya no hay sub-tabs) y **UN día**: el selector es una sola fecha; sin fecha el backend usa el **día HÁBIL ANTERIOR a hoy** — el que está cerrado, con extracto y saldo final ya informados. **CONSOLIDADO BANCOS**: UNA fila por cuenta agrupadas bajo el **nombre del banco como título**, con **SALDO AL CIERRE · GASTOS BANCARIOS · IVA · IVAPERCEP · IIBBPERCEP · COM.TRANSF · OTROS IMP** (saldo al inicio, variación y movs. se sacaron el 2026-08-18: no se usaban). Las columnas del desglose las declara el BACKEND, no el front, y **las edita el EQUIPO** (botón DESGLOSE del modal): son un catálogo en la base (`bancos.gastos_baldes` + `gastos_balde_matchers`), no una constante; el **ORDEN se mueve con ▲▼** y es lo único que decide los empates — así se resolvió el pisón real de `IVA PERCEPCION RESOL GRAL`, que llega con el concepto en `IVA`: se sube IVAPERCEP y se le da un matcher por descripción, sin excepciones hardcodeadas — antes sumar la grafía que usa un banco nuevo era un commit y un deploy. **OTROS IMP = solo las 4 descripciones declaradas**, así que las columnas pueden no sumar el total; lo que no cae en ningún balde va a `resto` y el MODAL lo muestra como **MOVIMIENTOS RESTANTES** en vez de esconderlo. En el MODAL el desglose se abre COMPLETO en horizontal, sin contador de movimientos, con GASTOS BANCARIOS separado por una línea de sus partes («= suma de»). La barra tiene **MOVIMIENTOS A CONCILIAR** (`bancos.conciliacion_pendientes`: lo confirmado en CONCILIAR que hay que arreglar en HYGIRUS — cada fila dice QUÉ HACER, «cargar en el mayor» o «sacar del mayor», con la descripción **tal como viene de SU lado** para que sea encontrable allá; sin filtro de fecha, lo resuelto se marca y no se borra), **CONCILIAR** (nuestro saldo al cierre contra el ÚLTIMO saldo del mayor contable que el usuario sube como Excel; el navegador solo abre el archivo y manda la grilla cruda, todo el criterio vive en el backend. ⚠️ La `D`/`A` del saldo es **formato de celda** (`#,##0.00" D";#,##0.00" A"`), o sea que `A` = negativo — medido sobre el export real. Si no concilia, busca qué movimientos del día suman exactamente la diferencia; si coincide al invertir el signo, lo avisa y NO lo corrige. `POST /conciliar`, no persiste nada), **DIFERENCIAS BANCARIAS** (¿la variación del saldo está explicada por sus movimientos? `cierre(hoy) − cierre(día anterior) == Σ movimientos de hoy`; lo que sobra casi siempre es el banco registrando un movimiento con fecha de anteayer que impacta en el saldo de ayer — y como `Σ movimientos = cierre − apertura`, esa diferencia **ES** el salto entre el cierre de un día y la apertura del siguiente, que se muestra al lado como evidencia. Se concilia contra el BANCO: los manuales no entran y los ignorados sí; sin alguno de los dos cierres dice «sin dato» en vez de inventar una diferencia. `GET /diferencias`, solo lectura), un **filtro por BANCO** (client-side sobre lo que ya trajo el consolidado; alcanza también al REPORTE FIN DE DÍA pero no al alta de manuales), **REGISTRAR MOVIMIENTOS MANUALES** (bancos que no están en Interbanking + movimientos que el banco no informa: `bancos.cuentas.origen='manual'` y `bancos.movimientos_manuales`; **siempre impactan el saldo al cierre** del día que muestra la vista —en una cuenta real se suman a su extracto, en una manual son todo el saldo—, el día y la moneda NO se eligen y el selector de cuenta se llena con las del banco elegido; el job no las puede pisar porque recorre lo que le devuelve Interbanking), **REPORTE FIN DE DÍA** (el saldo al cierre de todas las cuentas: **una tabla POR BANCO** —se probaron las dos matrices y las dos quedaban con el 90% de las celdas vacías, porque cada cuenta pertenece a UN banco— empaquetadas hasta 20 filas de alto y 15 columnas de ancho, con espacios grandes entre tablas, ARS primero adentro de cada banco la cabecera azul con el logo UNA vez arriba de todo y la firma «Hecho en ACAQuant» en chico, y botón **COPIAR IMAGEN** que DIBUJA el PNG de cero en un canvas —no es una captura: por eso la UI no se cuela, sale siempre en claro para el mail y no depende del tamaño de pantalla; si el navegador no deja copiar imágenes, la descarga). **Clic en cualquier número del desglose = AUDITOR**: la tabla queda mostrando SOLO las filas que lo componen, con una barra que dice cuántas son y cuánto suman (la suma la calcula la pantalla sobre lo visible: si no coincide con el número clickeado, el desglose y el detalle se contradicen). El DESCARGAR respeta el filtro. El copiar de esos valores se mudó a un ícono ⧉ al lado de la etiqueta. La cuenta va a la izquierda con el **número ENTERO**; las columnas de datos van centradas, de ancho parejo y separadas por una línea. **NO hay subtotales por banco ni totales por moneda** (los sacó el back office). **El CIERRE tiene DOS fuentes y la celda rotula cuál**: sin rótulo = EXTRACTO; **«saldo»** = lo informa el banco pero la cuenta no se movió y no hay extracto que lo respalde; **«≠»** = las dos no coinciden, con la diferencia en el tooltip. Sin ninguna va «—», nunca 0. **GASTOS BANCARIOS lo DERIVA el backend** de las reglas + marcas; sin una sola regla ni marca cargada viene `null` — «—» y jamás cero («no sabemos» ≠ «no hubo gastos»). ⚠️ **La fila tiene DOS gestos**: clic en la CUENTA abre el **MODAL de movimientos del día** (reemplazó a la sub-tab «Detalle por cuenta», que obligaba a elegir banco y cuenta en dos selectores para ver una fila que ya estabas mirando); clic en una celda de DATOS **copia el valor al portapapeles**. El **MODAL** trae la cabecera de la cuenta, el día según el banco (apertura · créditos · débitos · cierre · neto · movimientos) con las alertas **NO CIERRA** / **INCOMPLETO**, la tabla de MOVIMIENTOS con 8 columnas (FECHA · DESCRIPCIÓN · CONCEPTO · COD OP · COD OP BCO · COMPROBANTE · SUCURSAL · IMPORTE) más **GASTO** y **CUENTA**, con la **DESCRIPCIÓN llevándose el sobrante de ancho** para que salga entera (si igual se lee cortada, el corte lo hizo el banco: llega truncada a ~25 chars), los botones **REGLAS** y **DESGLOSE**, y un botón **DESCARGAR** (.xlsx client-side vía `lib/xlsx-export`, con el importe FIRMADO para que la columna sume el neto en Excel). Cierra con Esc, con la ✕ o clickeando el fondo. La CONTRAPARTE no es columna propia: viene en el **13%** de los movimientos (medido) y va debajo de la descripción. La **hora** se dibuja solo si no es `00:00:00` — medido sobre 178 movimientos, Interbanking **no informa la hora**. Lee de `bancos.*`, que llena `jobs/interbanking_sync` cada 2hs de 9 a 19 ART (día hábil anterior + hoy: extracto + saldo por cuenta) y que **retiene solo 3 fechas**. **La vista NUNCA le pega a Interbanking** (el límite de 100 llamadas/minuto es del ABONADO); la barra muestra **«Última actualización DD/MM/AAAA HH:MM»** y **quién más tiene la vista abierta** (`bancos.presencia`, el poll de 60s ES el heartbeat, TTL 180s). **GASTOS BANCARIOS** se clasifica en DOS capas: **reglas** (catálogo `bancos.gastos_reglas`, campo+operador+valor, ABM en modal desde el botón REGLAS) y **marca manual por movimiento** (`bancos.gastos_overrides`, columna GASTO del modal, un clic) — **la marca manual GANA sobre la regla** y la celda muestra de dónde salió (subrayado = manual, con ↺ para volver a la regla). Se deriva en la LECTURA, no se materializa. Sin ninguna regla ni marca la columna va «—», no 0. **IGNORAR un movimiento** (`bancos.movimientos_ignorados`, columna CUENTA del modal) es OTRA pregunta que GASTO: GASTO dice *qué es*, CUENTA dice *si suma* — un duplicado del banco sigue siendo un gasto, lo que no es es DOS gastos. La fila NO se borra: queda **tachada** con quién/cuándo/motivo y afuera de los gastos, del desglose y de la suma del auditor (que los cuenta aparte); los créditos/débitos del día NO se tocan — esa es la aritmética del extracto. **Ignorar gana sobre la marca manual.** Mismo modelo de PUROS OVERRIDES que `tesoreria_exclusiones`, con `ON DELETE CASCADE` sobre la retención de 3 fechas. Escritura: allowlist `operaciones.tesoreria_escritores` + admin, auditada en `bancos.gastos_audit`. **NO se mezcla con Tesorería** — objetos sin clave en común; ver `docs/INTERBANKING.md`. El **número de cuenta va ENTERO**; el **CBU no sale nunca** (hay tests). Cada lectura queda auditada en `bancos.audit_lecturas`. Poll 60s | **`GET /interbanking/consolidado`** (la vista) y **`GET /interbanking/vista?cuenta_id&fecha`** (el modal) + `/cuentas` | **`fecha`** (una sola; futura → 400), `cuenta_id` en el modal | **Solo la clasificación de gastos**: `POST /manual/cuentas`, `DELETE /manual/cuentas/{id}`, `POST /manual/movimientos`, `DELETE /manual/movimientos/{id}`, `POST /gastos/reglas`, `DELETE /gastos/reglas/{id}`, `PUT /gastos/movimiento`, `PUT /gastos/ignorar`, `POST /gastos/desglose`, `POST /gastos/desglose/orden`, `DELETE /gastos/desglose/{clave}`, `POST /gastos/desglose/matchers`, `DELETE /gastos/desglose/matchers/{id}` — sobre tablas NUESTRAS, con allowlist y auditadas; un test las ENUMERA. **Hacia Interbanking no se escribe nunca** (`core/interbanking.py` solo hace GET, congelado por test) y el proxy de Next solo deja pasar escrituras bajo `/gastos/*` y `/manual/*` |
| **Tesorería** | La caja del día. 5 tabs internas | ver bloque | ver bloque | allowlist `tesoreria_escritores` + admin |
| **Títulos / Mercado** | Qué títulos hay que ENVIAR y RECIBIR al mercado hoy, por ticker (expandible a comitentes) o por par ticker·comitente. Poll 10s | `/titulos-mercado` | `fecha` (def hoy); client-side: unidad `nominales`/`dinero`, filtro `ambos`/`enviar`/`recibir`, vista `ticker`/`comitente`, sort por neto | Solo **export .xlsx client-side** (`titulos-mercado-<fecha>.xlsx`) |
| **Acreencias Clientes** | Calendario de cobros futuros: tabla/chart por día + detalle del día por cliente·ticker | `/acreencias/por-dia`, `/acreencias/dia` | `desde` (def hoy) / `hasta` (def hoy+90) con atajos; día seleccionado; client-side `fTicker`, moneda `ALL/ARS/USD` | Ninguna |
| **Saldos de Cuentas Comitentes** | **DOS pantallas con selector** (2026-08-13). **SALDOS (default)** — descubiertos de EFECTIVO desde `portafolio.control_saldos` (daemon `jobs/control_saldos.py`, endpoint `cuentas/{id}/posiciones` de Aunesa): es el saldo **LIQUIDADO**, o sea que NO cuenta lo que todavía no liquidó → un negativo acá es descubierto REAL y no el falso positivo que deja una caución sin vencer. **UNA MONEDA POR VEZ** (pills derivadas de los datos, **ARS por default**; el daemon persiste ARS/USD/USDL/**USDC** — el cable entró el 2026-08-14) — sumar pesos con dólares no significa nada, y por eso tampoco hay columna MONEDA. **50/50**: izq **A FAVOR** (saldo > 0, de mayor a menor) · der **EN DESCUBIERTO** (saldo < 0, el más negativo primero), las dos mitades partidas por signo de las MISMAS filas → sus totales no pueden contradecirse. Columnas: CUENTA · **OPERADOR** (join `clientes.comitentes` → `operadores`; sin operador cargado la fila se muestra igual, en itálica) · SALDO (2 decimales: es plata, no nominales). Total por lado en la cabecera. El backend devuelve **positivos y negativos** y el contador de la solapa es `n_negativos` (no depende de la moneda elegida). **Botón OCULTAS** → modal ABM (`portafolio.control_saldos_ocultas`, `PUT`/`DELETE /saldos/ocultas`): cuentas que el equipo decide NO ver, con el alta desde la propia vista (patrón agentes de SENEBIS) y **quién la ocultó y cuándo a la vista en cada fila**. ⚠️ OCULTAR NO ES EXCLUIR: el saldo se sigue persistiendo, solo no se muestra — por eso el corte vive en la LECTURA y no en el job. **Excluye además por `nivel_5` ∈ {CDC, OTC}** (`NIVEL5_EXCLUIDOS` en el service — el filtro sale de la segmentación, no de un patrón sobre el nombre: reclasificar en Manager la saca sola) y la barra dice **cuántas ocultó**. **TÍTULOS** — el control viejo: nominales < 0 en DOS tableros al 50% desde `tenencia_live`, izq **T0** (liquidada a HOY) · der **T1** (liquidada a MAÑANA con lo concertado hoy). Columnas CUENTA · TICKER · NOMINALES. Poll 20s; **cada pantalla muestra la antigüedad de SU daemon** (son dos y uno puede estar muerto con el otro sano) | `/titulos-negativos` (un solo request: el bloque `saldos` viaja en la misma respuesta) | `incluir_todo` (solo en TÍTULOS, def **no** — MONEDAS y DERIVADOS quedan afuera: ahí el negativo es normal); buscador `q` client-side, que en SALDOS **incluye al operador** | Ninguna (solo lectura) |

**Endpoints no-Tesorería (14)**: `GET /titulos-mercado` (settlement = ops de `fecha` con plazo CI/Inm +
ops del día hábil anterior con plazo 24hs; `op=Venta`→enviar, `Compra`→recibir; día no hábil →
`mercado_cerrado:true`; cache 10s) · `GET /acreencias/por-dia` · `GET /acreencias/dia` (`fecha`
**obligatorio**) · `GET /acreencias/cliente` (`id_cuenta` obligatorio; **gate `verificar_id_cuenta`**) ·
`GET /tenencia-hd` · `GET /tenencia-hd/posiciones` · **`POST /tenencia-hd/precio`** ·
`GET /tenencia-hd/en-alquiler` · **`POST /tenencia-hd/alquiler`** ·
`GET /tenencia-hd/portfolio-alquiler` (+ `/posiciones`, `/instrumentos`) ·
**`POST /tenencia-hd/portfolio-alquiler`** · **`POST /tenencia-hd/portfolio-alquiler/nominal`**.

**Fuentes**: Títulos/Mercado ← `operaciones.negocio_movimientos` (`categoria IN ('compra','venta')`,
`anulado_en IS NULL`), feriados con la lib `holidays.Argentina()` · Acreencias ←
`operaciones.acreencias`, escrita por el cron `jobs.acreencias --commit` (12:45 UTC L-V, **swap
atómico**), que cruza `mercado.curvas` × `portafolio.tenencia`(`aum='si'`) × `clientes.cuentas` ·
Tenencia/alquiler ← `portafolio.tenencia` + `assets`.

### SENEBIS (tab de `/back-office`)
- **Escritura de órdenes**: allowlist propia `operaciones.senebis_escritores` + admin
  (`require_escritura_senebis`), gestionada en **Manager → MESA**.
- **Flujo**: 2 estados, `pendiente` → `completada`. El trader carga; el back office la procesa afuera
  (Quantex o MAE) y la marca completada. `set_estado` es **idempotente** y **no deja tocar el estado de
  una orden de un día ANTERIOR salvo que el actor sea admin**.

| Sub-tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **Órdenes** | Tabla completa con marcas de edición: `campos_editados` → `*` al lado del campo; `editada_completada` → **fila amarilla** + botón ⚠ EDITADA | `GET /ops` (marca presencia), `/opciones`, `/comitentes` | rango, estado, MAE | `POST /ops`, `PATCH /ops/{id}`, `DELETE /ops/{id}` (allowlist); `POST /ops/{id}/estado`; `/visto`; `/reasignar-id`; `PUT /agentes`, `DELETE /agentes/{nombre}`; `POST /proximo-id` (**admin**) |
| **Excel Quantex** | Espejo EN VIVO del archivo destino (10 columnas: ID·OPERACION·INSTRUMENTO·PLAZO·PRECIO·CANTIDAD·CONTRAPARTE·COMITENTE·CARTERA PROPIA·MERCADO) + `proximo_id` | `GET /excel` | solo rango HOY/TODO; **el estado NO se pasa** (el backend ya filtra) | Botón **⬇ GENERAR EXCEL** → `GET /export`; reasignar ID desde la fila |
| **Excel MAE** | Espejo del archivo MAE (8 columnas: Operacion·Instrumento·Plazo·Moneda·Precio·Cantidad·Destino·Segmento) con `sin_destino` marcando las que no tienen código. El SEGMENTO sale de la orden (catálogo `senebis_segmentos`, botón **SEGMENTOS MAE**). **Columna ✓ propia de esta tab** (`mae_completada`, 2026-08-13): el trader tilda lo que ya cargó en el MAE → la fila queda **grisada + tachada** y NO sale más en el `.xlsx` (destildable). Es INDEPENDIENTE del `estado` de la tab Órdenes (ese es el laburo del back office en Quantex). Editar una tildada prende `mae_editada_completada` → **fila amarilla** + botón ⚠ EDITADA (espejo del amarillo de Quantex, con marca y visto PROPIOS: los bajan equipos distintos); destildar baja el amarillo solo | `GET /excel-mae` | rango | `POST /ops/{id}/mae-completada`; `POST /ops/{id}/mae-visto`; `GET /export-mae` |

**Endpoints (20, prefix `/api/back-office/senebis`)**: **`GET /vista`** (mismos filtros que
`/ops`; devuelve `{total, pendientes, ordenes, conectados, excel, excel_mae, proximo_id}` —
lo que consume la vista) · `GET /ops` (`desde`, `hasta`, `estado`,
`especie` ILIKE, `mae` `solo|sin`; devuelve `conectados` + `total` + `pendientes`) · `POST /presencia` ·
`GET /opciones` (agentes + `tipos_contraparte` + `plazos` + `conectados` + `puede_escribir` +
`es_admin`) · `GET /comitentes` (`q` req, min 1) · `GET /excel` · `GET /excel-mae` · `GET /export`
(`senebis_YYYYMMDD.xlsx`) · `GET /export-mae` · **`POST /ops`** · **`PATCH /ops/{id}`** ·
**`DELETE /ops/{id}`** · **`POST /ops/{id}/estado`** · **`POST /ops/{id}/mae-completada`**
(`{completada: bool}`; solo órdenes `es_mae`, sin corte por fecha — la tilde de la tab MAE) ·
**`POST /ops/{id}/visto`** (baja el amarillo de QUANTEX) ·
**`POST /ops/{id}/mae-visto`** (baja el del MAE — marcas separadas a propósito) ·
**`POST /ops/{id}/reasignar-id`** · **`POST /proximo-id`** (`require_admin`; rechaza
`siguiente <= MAX(id)`) · **`PUT /agentes`** · **`DELETE /agentes/{nombre}`**.

**Payload `_OpPayload`**: `operacion` (COMPRA|VENTA, obligatorio), `concertacion` (def HOY ART),
`liquidacion`, `plazo` (`CI`|`24`), `especie` (obligatorio), `vn`, `px` (cada 100 VN), `monto`
(override), `cp` (def `'255'`), `cc`, `contraparte`, `nro_contraparte`, `mercado`
(`GARANTIZADO`|`NO GARANTIZADO`|vacío), `cargan_ellos` (bool SI/NO, tolera texto legacy), `tipo` (obs.
libre), `tipo_contraparte` (`interno` def | `externo`), `agente` (obligatorio si externo, debe estar en
el catálogo), `es_mae` (bool).

**Reglas no inferibles:**
- **Derivados server-side**: `monto = vn × px / 100`; `concertacion` def hoy ART; `cp` def `'255'`; `plazo ↔ liquidacion` se infieren entre sí (`CI` → liq = concertación; `24` → `core.calendario.proximo_habil`; con liq y sin plazo: mismo día → CI, posterior → 24; sin ninguno → CI liquidando hoy). `liquidacion < concertacion` → error.
- **`es_mae=true`** → `tipo` queda fijo `'MAE'` y la orden se excluye del Excel/espejo Quantex.
- **Qué entra al Excel Quantex** (`_filas_quantex`, una sola regla para espejo y archivo): `estado='pendiente'` AND `not es_mae` AND `not cargan_ellos`, orden por `id` asc. El archivo se sube varias veces por día → lo completado ya está cargado.
- **Qué entra al Excel MAE**: `estado='pendiente'` AND `es_mae` AND `not cargan_ellos`.
- **Contraparte en el Excel Quantex**: externo → COMITENTE vacío + CONTRAPARTE = `agente_numero`; interno GARANTIZADO → COMITENTE = `cp`; interno resto → COMITENTE = `cc`. Los numéricos se emiten como número.
- **DESTINO del Excel MAE** (2 queries batch, resuelto **EN VIVO**): interno → `clientes.contrapartes.codigo_mae` por `cc` → prefijo `F`; externo → `senebis_agentes.codigo_mae` por nombre → prefijo `A`. La letra la pone el sistema; si el código no es puramente numérico se respeta tal cual. Sin código → celda vacía + `sin_destino:true`. **El destino MAE de cuentas internas no se edita acá**: es atributo de la contraparte, en **Manager → CONTRAPARTES**.
- **Excel MAE — precio UNITARIO (`px ÷ 100`) y Moneda fija `'ARS'`** (la orden todavía no tiene el campo). **Segmento** ya NO se completa a mano: sale del campo `senebis.segmento`, que se elige al cargar la orden (selector visible solo si ¿MAE?=SÍ) entre los valores del catálogo `operaciones.senebis_segmentos` — ABM en el botón **SEGMENTOS MAE** de la vista, `PUT /segmentos` · `DELETE /segmentos/{nombre}`. Nace en `Bilateral MAEClear` (`senebis.SEGMENTO_MAE_DEFAULT`), que el ABM no deja borrar. Las órdenes anteriores al campo lo tienen NULL y el export cae al default, así ninguna celda sale vacía.
- **Marcas de edición**: se calculan comparando before/after de lo **persistido** (no del payload, que el front echoa entero); `campos_editados` es acumulativo; `editada_completada` se prende si ya estaba `completada`.
- **`id` = secuencia GLOBAL** que espeja la numeración Quantex y **no se resetea**; ajustable con `POST /proximo-id` (admin). Si Quantex ya consumió el número y la carga falló, `reasignar-id` da el siguiente libre y **quema el viejo**, sin renumerar el resto.
- **Presencia**: `senebis_presencia`, TTL 90s; el poll de `/ops` (10s) ES el heartbeat.
- **openpyxl con import lazy** — sin la lib el endpoint devuelve **501**, la API no cae (REGLA #1).

**Fuentes**: `operaciones.senebis`, `senebis_agentes`, `senebis_escritores`, `senebis_presencia`,
`senebis_audit` (before/after) + lecturas cruzadas de `clientes.cuentas`,
`clientes.contrapartes.codigo_mae`, `manager.manager_users`.

### TESORERÍA (tab de `/back-office`)
- **Escritura**: allowlist `operaciones.tesoreria_escritores` + admin (`require_escritura_tesoreria`) en **todos** los writes. Gestionada en Manager → MESA.
- **Poll de la vista: 20s** (silencioso) y ES el heartbeat de presencia (TTL 90s).

| Tab interna | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **MOVIMIENTOS** (def.) | Tabla plana de los movimientos bancarios del día (**LIVE Aunesa, no se persiste**), todas las columnas crudas + `_hora`/`_tipo`/`_echeq`; totales por moneda. **Siempre el día en curso** | `GET /tesoreria/dia` | **Server-side**: `estado` (los 7 estados Aunesa + "Todos", persistido, def `Procesado`). **Client-side**: **RIEL** (`tipoDocSoli`), **TIPO** (ingreso/egreso), **SOLICITUD** (Depósito/Extracción), buscador libre `q`, selector **COLUMNAS** (persistido) | Ninguna |
| **BANCOS** | Grilla tipo planilla: una columna por cuenta operativa (**TODAS las del catálogo**, operen o no) × moneda; filas Saldo inicial · Ingresos · Ingresos e-cheqs · Egresos · Egresos e-cheq · Mercados · FCI · bb (+) · bb (−) · Saldo final. **Cada celda es clickeable** → modal de auditoría | `/tesoreria/dia`, `/detalle`, `/foto`, `/cuentas`, `/registros`, `/snapshots` | **FECHA** (`max=hoy`) — **el selector aparece SOLO en esta tab**; fecha pasada = FOTO guardada, solo lectura. **La grilla IGNORA el selector ESTADO**: siempre calcula sobre `Procesado` | `PUT /saldo-inicial`, `PUT /exclusion` (tildar/destildar del modal), `POST /snapshots` (**SACAR FOTO**), ABM de bancos, modal **REGISTROS MANUALES** |
| **CHEQUES** | 50/50. **Izq RECIBIDOS**: todos del día — los MANUALES por su día de carga (`creado_at` ART) y los ESPEJO por su `fecha_pago`, que es el día en que la plata entra al banco (`tesoreria._DIA_RECIBIDO`; sin esa distinción un espejo de un movimiento del viernes aparecía en el tablero del martes) — estados `pendiente`/`finalizado`, columnas comitente·tipo(`echeq`/`fisico`)·banco·importe·moneda·estado·**fecha de pago**. Conviven carga MANUAL y **ESPEJO automático** (chip AUTO, `origen='aunesa'`) de los DEPÓSITOS de cheque del feed del COMITENTE — lo crea `jobs/tesoreria_echeq_recibidos` (cron */30 12-21 UTC = 9:00-18:30 ART, L-V — terminaba 14:30 y se estiró 2026-08-12: con lookback de 2 días, lo cargado en Aunesa después del corte de D+1 no lo alcanzaba ninguna corrida) porque el back office los carga en Aunesa a la MAÑANA SIGUIENTE con la fecha del día anterior. Nacen **sin banco** (Aunesa no manda la cuenta operativa → celda «falta banco» en ámbar) y en `pendiente`; `fecha_pago` = día hábil siguiente al movimiento. El backend RECHAZA finalizar un recibido sin banco: si no, el ingreso no se imputaría a ninguna cuenta. **Der EMITIDOS**: tablero de seguimiento **sin filtro de fecha**, estados `pendiente`/`emitido`/`completado`, `fecha_pago` futura = fila **NARANJA**. Botón **ver consolidado** → modal por banco×moneda con los grupos `vencido`/`hoy`/`futuro`/`auto`/`pendiente`/`sin_fecha` y el total **IMPACTA EN BANCOS = vencidos + de hoy**; **AUTO (AUNESA) es solo el espejo con `fecha_pago` de HOY** (hoy lo resta su movimiento, mañana pasa a `vencido` — un emitido resta hasta que se marca `completado`) | `/tesoreria/cheques`, `/cheques/comitentes` | `fecha` (solo afecta a RECIBIDOS), `incluir_cerrados` | `POST`, `PUT /{id}`, `PUT /{id}/estado` (click en la celda), `DELETE /{id}` |
| **MERCADOS** | 4 tableros del día al 50 %: bloque MERCADO (`ingreso` izq / `pago` der) y bloque FCI (`rescate` izq / `suscripcion` der). Carga manual, mismo modelo con distinto `tipo` | `/tesoreria/mercados`, `/entidades` | `fecha` (def hoy) | `POST`, `PUT /{id}`, `PUT /{id}/estado`, `DELETE /{id}`; **ABM del catálogo** mercados/FCI en modal (`DELETE` = **baja lógica**) |
| **BANCO A BANCO** | Transferencias INTERNAS del día entre cuentas propias (débito → crédito). **Suman cero entre bancos** | `/tesoreria/banco-a-banco`, `/export-txt` | `fecha` (def hoy) | `POST`, `PUT /{id}`, `PUT /{id}/estado`, `DELETE /{id}`; botón **TXT HYGIRUS** |

**Modal REGISTROS MANUALES** (botón en la barra de BANCOS): 2 tabs — **RESCATE ACA VALORES**
(`grupo='rescate'`, `tipo` del catálogo fijo PROVEEDORES/FONDOS FIJOS/VEP/HABERES/IMPUESTO/TARJETA
VISA/OTROS) y **OTROS REGISTROS** (`grupo='otros'`, `tipo` **texto LIBRE**). Cada tab 50/50: izq carga
(TIPO · IMPORTE · BANCO · `egreso` def / `ingreso`), der resumen por TIPO. Filtro propio de **moneda**
(def ARS). La fila **SALDOS** solo existe en el rescate y es manual. **Los dos grupos entran igual a las
filas Ingresos/Egresos** del banco según su sentido; el `grupo` NO cambia el saldo, solo separa los
resúmenes. El **TOTAL de RESCATE ACA VALORES** se muestra en la barra de la vista, al lado de SACAR FOTO,
calculado por el backend con la MISMA fuente que el modal.

**Endpoints (36, prefix `/api/back-office/tesoreria`)**: `GET /dia` (grilla + resumen por moneda +
MOVIMIENTOS + `rescate` + `catalogo` + `conectados` + `puede_editar_saldo`; `fecha`, `estado` acepta
lista `;`) · `GET /detalle` (`banco`, `unidad`, `fila` ∈
`saldo_inicial|ingresos|ingresos_echeq|egresos|egresos_echeq|mercados|fci|bb_mas|bb_menos|saldo_final`,
`fecha`) · `GET /foto` · `GET /snapshots` · **`POST /snapshots`** · **`PUT /exclusion`**
(`{fecha, fuente ∈ aunesa|cheque|mercado|bb|registro, ref, excluido}`) · **`PUT /saldo-inicial`**
(`null` borra) · `GET /cheques` · `GET /cheques/comitentes` · **`POST`/`PUT /{id}`/`PUT /{id}/estado`/
`DELETE /{id}` de cheques** · `GET /cuentas` · **`POST /cuentas`** (alta manual del banco que aún no
operó) · **`PUT /cuentas`** (número, `numero_hygirus`, **renombrar** —solo bancos de alta MANUAL; el de
los descubiertos lo manda la fuente y se rechaza. Arrastra saldos/cheques/mercados/registros/veps/
banco-a-banco en UNA transacción— y alta/baja lógica) · **`DELETE /cuentas?cuenta_operativa&unidad`** (borra la fila si
nadie la referencia y no tiene `aunesa_id`; si no, baja lógica) · `GET /entidades` · **`POST`/`PUT /{id}`/`DELETE /{id}` de
entidades** · `GET /mercados` · **`POST`/`PUT /{id}`/`PUT /{id}/estado`/`DELETE /{id}` de mercados** ·
`GET /banco-a-banco` · **`POST`/`PUT /{id}`/`PUT /{id}/estado`/`DELETE /{id}`** ·
`GET /banco-a-banco/export-txt` · `GET /registros` (`fecha`, `unidad` def ARS) ·
**`POST`/`PUT /{id}`/`DELETE /{id}` de registros** · **`PUT /registros-saldo`**.

**Payloads / valores válidos**
- `_Cheque`: `lado` (`emitido` def | `recibido`), `tipo` (solo recibidos: `echeq`|`fisico`; en emitidos se fuerza a NULL), `comitente`, `comitente_denominacion`, `cuit`, `banco` (**debe existir en `tesoreria_cuentas`**), `unidad` (def ARS), `importe` >0, `estado` (emitido: `pendiente|emitido|completado`; recibido: `pendiente|finalizado`), `fecha_pago`.
- `_Mercado`: `tipo` ∈ `ingreso|pago|rescate|suscripcion`; `entidad` **validada contra el catálogo del bloque**; `banco` validado; `importe` >0; `estado` ∈ `pendiente|completado`.
- `_BancoABanco`: `cta_debito ≠ cta_credito`, **ambas en el catálogo y de la MISMA moneda**; `importe` >0; `estado` ∈ `pendiente|completado`.
- `_Registro`: `grupo` ∈ `rescate` (def) | `otros`; `tipo` acotado en rescate / libre en otros; `sentido` ∈ `egreso` (def) | `ingreso`; `banco` validado; `importe` >0. `SALDOS` **no es un tipo cargable**.
- Unidades: `ARS`, `USD`. Estados Aunesa: `Procesado, Pendiente, Pendiente de autorizar, Demorado, Rechazado, Anulado, Incompleto`.

**Fórmula del saldo (no inferible)**
```
saldo_final = saldo_inicial + ingresos + ingresos_echeq − egresos − egresos_echeq
              + mercados + fci + bb_mas − bb_menos
```
- `mercados = ingreso − pago`, `fci = rescate − suscripcion` (netos con signo, agregados en SQL con `CASE`).
- Las **dos** filas e-cheq van separadas de los totales SOLO para distinguirlas: **las dos entran al saldo**. `egresos_echeq` = RIEL con código `[E CHEQ]` de Aunesa; `ingresos_echeq` = cheques RECIBIDOS **finalizados** (carga manual o espejo de depósitos) — esa plata NO viene en los movimientos de Aunesa, no hay doble conteo.
- Los **registros manuales** (los dos grupos) se suman a ingresos/egresos según su sentido.
- La grilla se calcula **SIEMPRE sobre `ESTADO_EFECTIVO = "Procesado"`** e ignora el selector ESTADO.
- Sin carga manual el `saldo_inicial` vale **0** (no null); `saldo_cargado: bool` distingue "cargado en cero" de "sin cargar" (el front lo pinta apagado).
- El **TOTAL del tablero EMITIDOS es lo que impacta HOY**: un `emitido` con `fecha_pago` FUTURA no suma (se muestra aparte como «+X futuros»); los vencidos SÍ cuentan y los `pendiente` también.

**Fuentes**: **Aunesa LIVE** `cuentas/consultaMovDocsSolicitados` (se pide `liquidacionDesde=día`,
`liquidacionHasta=día+1` porque la API exige desde<hasta, y se filtra al día objetivo; Ingreso =
`solicitud='Depósito'`, Egreso = `'Extracción'`, comparación normalizada NFKD sin diacríticos — NFC/NFD
rompía el match; **no se persiste**) · SQL `operaciones.tesoreria_{saldos, cuentas, cheques, mercados,
entidades, banco_a_banco, registros, registros_saldo, exclusiones, snapshots, presencia, escritores,
audit}` · cron `50 2 * * 2-6` UTC (= 23:50 ART) → `jobs.tesoreria_snapshot` (soporta `--fecha` y
`--dias N` con throttle 0.5s).

**Notas / rarezas (Tesorería)**
- **Movimientos de Aunesa SIN HORA arrancan DESTILDADOS** (`default_excluido = not _hora`): su `id` no trae fecha-hora y no se distinguen de un duplicado. Se tildan a mano. Las exclusiones persisten SOLO como override.
- **El detalle de una celda se calcula server-side** con las MISMAS fuentes y filtros que la grilla (`_detalle_dia`, una pasada: 1 llamada a Aunesa + 1 query por fuente); el modal live y la FOTO usan la misma función → no pueden divergir. `fila=saldo_final` devuelve la ecuación fila por fila.
- **El ESTADO del detalle no miente**: mercados/FCI/banco-a-banco cuentan también lo `pendiente` (decisión del back office) y el modal muestra el estado REAL, resaltado en ámbar cuando no es plata cerrada. Los registros manuales van con estado **vacío** a propósito.
- Filas sin `cuentaOperativa` caen al placeholder `SIN CUENTA OPERATIVA` y **nunca** se persisten en el catálogo.
- El catálogo de bancos se **auto-alimenta** con todo lo visto en el día (idempotente, con guarda para que el poll de 20s sea no-op), incluso si el movimiento terminó rechazado; también hay alta manual y siembra hacia atrás (`scripts/diag_tesoreria_cuentas --registrar`). El campo `numero_hygirus` se guarda pero **NO se muestra en la grilla**.
- **Renombrar** (`editar_cuenta`) arrastra en UNA transacción las 5 tablas que apuntan al banco por nombre (`tesoreria_saldos`, `_cheques`, `_mercados`, `_registros`, `_veps`) + las dos puntas de `tesoreria_banco_a_banco`. En un banco **descubierto** (`aunesa_id`) el rename está PROHIBIDO: el nombre lo manda la fuente y el próximo poll lo recrearía, partiendo los históricos en dos bancos.
- **Borrar** (`borrar_cuenta`) es físico solo si NADIE lo referencia (las mismas 5 tablas + banco-a-banco) y no tiene `aunesa_id`; en cualquier otro caso degrada a **baja lógica**. Incidente 2026-08-10: `_REFS_CUENTA` no miraba `tesoreria_registros` ni `tesoreria_veps`, así que un banco de alta MANUAL con solo registros contaba 0 referencias → se borraba físicamente y, al no traerlo Aunesa, no volvía nunca.
- **`fuera_catalogo`** (campo de `/tesoreria/dia`): los bancos que están en la GRILLA y no en el ABM, con su motivo — `sin_catalogo` (no hay fila) o `dado_de_baja` (la hay con `activa=false` y el banco sigue operando; `registrar_cuentas` NO la revive, su UPDATE no toca `activa`). El ABM los muestra arriba con un botón **DAR DE ALTA** que manda el nombre exacto, para no depender de que alguien lo tipee igual.
- **TXT HYGIRUS**: cabecera `DD/MM/AAAA HH:MM:SS Asiento de ajuste` + 2 líneas por transferencia **≠ `completado`** (`-importe⇥hygirus_débito⇥moneda`, `importe⇥hygirus_crédito⇥moneda`), coma decimal sin separador de miles, formateado desde `Decimal`. Se genera SIEMPRE (sin pendientes sale solo la cabecera); si falta `numero_hygirus` la celda va vacía y la cuenta aparece en `faltantes`. **El endpoint nunca devuelve error** y manda el contenido dentro de un JSON, porque el proxy de Next mapea cualquier error a un 502 mudo.
- **FOTO**: `hash_sha256` del payload canónico detecta ediciones hechas por fuera de la API (`hash_ok`). **Una foto por fecha** (re-sacarla PISA; el historial queda en `tesoreria_audit`), **TTL de 30 fechas** purgadas en el mismo INSERT. Si el detalle falla, la foto se toma igual y deja `detalle._error`.
- Casi todas las lecturas SQL están en `try/except` que degradan a vacío con warning: la vista no se cae si falta una tabla.
- El service **revalida** el permiso además del dependency del router (lo invocan también scripts/jobs).
- **El histórico de movimientos NO se duplica**: se navega desde la celda de BANCOS que los usa.

### Vista: MESA DE DINERO (`/mesa-dinero`)
- **Acceso (2026-08-11)**: **NINGÚN módulo** — allowlist **per-usuario**
  `operaciones.mesa_dinero_lectores` ∪ `mesa_dinero_escritores` + admin
  (`require_lectura_mesa`, montado sobre todo el router). Antes lo daba el módulo
  `operaciones`, o sea todo NEGOCIO. Se cambió porque el criterio de acceso a esta
  vista es **quiénes**, no **qué puesto**: con un módulo hacía falta un rol por cada
  combinación de personas. Mismo patrón que `require_control_comercial`.
- **Acceso PARCIAL — SOLO RESULTADOS (2026-08-17)**: allowlist aparte
  `operaciones.mesa_dinero_lectores_resultados`. Entra a la vista pero solo a la tab
  **RESULTADOS**: NO ve el detalle operación por operación (activo, VN, precios, trader,
  cliente de cada trade) ni ACA VALORES RETORNO. El corte es **server-side** —
  `require_vista_completa` sobre `/ops`, `/resumen` y `/retorno`; el front esconde las
  solapas, pero esconder una solapa no es un permiso. Nació para dar el tablero de
  resultados a operadores comerciales sin abrirles la operatoria de la mesa.
  **Gana el acceso más amplio** (`alcance` = `todo` \| `resultados` \| `None`): estar en la
  lista chica nunca recorta a un admin, lector completo o escritor.
- **Escritura**: allowlist `operaciones.mesa_dinero_escritores` + admin (`require_escritura_mesa`).
- **Escribir implica ver**: `puede_ver` es la UNIÓN de las listas, así no puede existir un
  usuario que cargue en una vista que no ve. Quitar a alguien de LECTORES no le saca el acceso si
  sigue siendo ESCRITOR — el panel lo avisa (`sigue_viendo` en la respuesta del DELETE); lo mismo
  al quitarlo de la lista de solo-resultados si entra por otra.
- **`/api/me` publica la capacidad `mesa-dinero`** dentro de `modules` para que el nav y `proxy.ts`
  la filtren igual que a un módulo. NO es un módulo del RBAC (ver la nota del árbol de nav).

| Tab | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **OPERACIONES** (def.) | 55/45. Izq: tabla de ops (fecha·trader·activo·VN/PX compra·VN/PX venta·montos·resultado·%·cliente·observación) + formulario de alta/edición. Der: arriba tabla RESULTADO diario (Σ resultado + TC + USD + acumulado), abajo barras por fecha | `/ops`, `/resumen`, `/resultados`, `/opciones` | **mes** (`input type=month`, persistido → `desde`/`hasta`), **trader** (Todos + catálogo), **moneda ARS/USD del gráfico** | `POST /ops`, `PATCH /ops/{id}`, `DELETE /ops/{id}` (con confirm), **`PUT /tc`** (TC del día editable desde la tabla RESULTADO) |
| **RESULTADOS** | Agregados del período POR CLIENTE y POR COMERCIAL en ARS y USD, con `n`, `desde_operadores_ars/usd` y `dias_sin_tc`. **Única tab que ve el grupo de acceso parcial** (ahí las otras dos ni se dibujan) | `/resultados` | mismos mes/trader | Ninguna |
| **ACA VALORES RETORNO TOTAL** | Filas crudas del informe Excel del fondo ACA R.TOTAL, agregadas en el cliente por OPERACIÓN / AGENTE / PAPEL / DÍA, con **cross-filter** interactivo | `/retorno` | `periodo` (`YYYY-MM`, persistido, def el más reciente) — **independiente del selector de mes**; cross-filter client-side | **`POST /retorno/import`** — subir el Excel del informe, **ADMIN-ONLY** (`require_admin`; el botón sale de `opciones.es_admin`). Dos pasos: preview (`?dry_run=true`, no escribe) → confirmar. Reemplaza por `periodo`. La CLI `scripts/import_acavalores_retorno.py` sigue existiendo y llama a la MISMA función de service |

**Endpoints (10)**: `GET /ops` (`desde`, `hasta`, `trader` exacto; orden `fecha DESC, id DESC`) ·
`GET /resumen` (`SUM(resultado) GROUP BY fecha` + TC manual + USD + acumulados) · `GET /resultados` ·
`GET /opciones` (traders + observaciones + clientes usados + `puede_escribir` + **`alcance`** + **`es_admin`**) ·
`GET /retorno` · **`POST /retorno/import`** (multipart, ADMIN) ·
**`POST /ops`** · **`PATCH /ops/{id}`** · **`DELETE /ops/{id}`** · **`PUT /tc`** (`{fecha, tc>0}`).

**`_OpPayload`**: `fecha` (obligatorio), `trader` (obligatorio, **debe estar en
`mesa_dinero_traders`**), `activo`, `vn_compra`, `px_compra`, `vn_venta`, `px_venta`, `resultado` (solo
para registros SIN patas), `cliente` (libre), `observacion` (**debe ser `"Mesa"` o un operador de
`clientes.operadores`**).

**Fórmulas / reglas no inferibles**
- `monto = vn × px / 100` (cada pata); `resultado = monto_venta − monto_compra`; `pct = resultado / monto_compra`. **Sin las dos patas → `resultado` manual OBLIGATORIO** (ej. "Pase OPS"), si no error 400.
- El **resultado diario NO se persiste**: se deriva con `SUM(resultado) GROUP BY fecha` (verificado contra la planilla el 2026-07-29). USD solo si hay TC cargado ese día.
- **REGLA 50/50** (solo en el lado COMERCIAL de `/resultados`): si la observación no es `"Mesa"` es un operador comercial y el resultado se reparte **mitad al operador, mitad a la Mesa**. Es SOLO atribución: el total del período y el lado por CLIENTE no cambian. **Observación vacía NO se reparte** (queda íntegra en `(sin observación)`). `n` cuenta operaciones **ORIGINADAS** → la Mesa puede tener resultado sin ops propias, y `desde_operadores_ars/usd` dice cuánto vino de ese 50 %.
- El lado comercial lista **todo el catálogo** aunque estén en cero, como la planilla.
- El USD se calcula **op por op** con el TC del día; `dias_sin_tc` avisa que el USD está incompleto.
- ACA VALORES RETORNO TOTAL: la métrica es el **CASH** (`bruto`, "Moneda de Concertación Bruto") y el front lo toma en **valor absoluto** (no distingue compra/venta).
- **La carga es ADMIN, no ESCRITOR de la mesa** — es la única escritura de la vista que no pasa por `mesa_dinero_escritores`. Motivo: importar no agrega una fila, **reemplaza el mes entero** del fondo (`DELETE ... WHERE periodo = ANY(...)` + insert), así que el radio de daño de un archivo equivocado es de otro orden que el de cargar una operación.
- **Un archivo puede traer más de un período** y la importación reemplaza TODOS los que trae. Por eso el flujo tiene preview obligatorio: `dry_run` devuelve, por período, cuántas filas trae el archivo y **cuántas hay hoy en la base** (lo que se pisa). Un Excel con 3 filas que borra un mes de 287 se ve ANTES, no después.
- Filas sin fecha de concertación se **descartan** (no tienen `periodo`, que es la clave del reemplazo) y se cuentan en `sin_fecha`.

**Fuentes**: `operaciones.mesa_dinero`, `mesa_dinero_tc` (PK fecha), `mesa_dinero_traders`,
`mesa_dinero_escritores`, `mesa_dinero_lectores`, `mesa_dinero_lectores_resultados`,
`mesa_dinero_audit` (before/after de op, TC, traders, escritores y lectores) ·
`clientes.operadores` · `manager.manager_users` · `operaciones.acavalores_retorno`.

### Allowlists de escritura — resumen consolidado

| Allowlist (tabla SQL) | Qué habilita | Bypass | Dónde se gestiona |
|---|---|---|---|
| `operaciones.tesoreria_escritores` | **TODOS** los writes de Tesorería: saldo inicial, exclusiones, cheques, mercados + su catálogo de entidades, banco a banco, registros manuales + saldo del rescate, ABM de bancos, SACAR FOTO | `role=="admin"` siempre | Manager → MESA |
| `operaciones.senebis_escritores` | Solo **crear / editar / borrar órdenes** SENEBIS. **NO cubre** marcar estado, `/visto`, `reasignar-id` ni el catálogo de agentes (esos los puede hacer todo el módulo `back-office`) | `role=="admin"` siempre | Manager → MESA |
| `operaciones.mesa_dinero_escritores` | Alta/edición/borrado de ops de Mesa de Dinero + `PUT /tc` | `role=="admin"` siempre | Manager → MESA |
| `operaciones.mesa_dinero_lectores` | **VER** la vista Mesa de Dinero (las 9 rutas). Única allowlist de **LECTURA** del sistema junto con el flag `control_comercial` — el resto gobierna escrituras | `role=="admin"` siempre; **y los `mesa_dinero_escritores` entran por unión** | Manager → MESA (fila de abajo) |
| `operaciones.mesa_dinero_lectores_resultados` | Ver **SOLO la tab RESULTADOS** de Mesa de Dinero. `/ops`, `/resumen` y `/retorno` responden 403 (`require_vista_completa`) | `role=="admin"` siempre; también entran por unión los lectores completos y los escritores (gana el acceso más amplio) | Manager → MESA (fila de abajo) |

Además **admin-only fuera de allowlist**: `POST /senebis/proximo-id` (`require_admin`) y cambiar el
estado de una orden SENEBIS de un **día anterior** (chequeo dentro de `set_estado`).

**Notas transversales del dominio**: los tres auditan todo (`tesoreria_audit` / `senebis_audit` /
`mesa_dinero_audit` con `ts, actor, action, target, data`; el insert de audit está en `try/except` y
nunca bloquea la operación real) · **Presencia** en Tesorería (poll 20s) y SENEBIS (poll 10s); **Mesa de
Dinero NO tiene presencia** · los dependencies de permiso son **dependencies, no chequeos en el
handler**, explícitamente para que `scripts/audit_rbac.py` los vea (y los services igual revalidan).

---

## 4.10 MANAGER

### Vista: MANAGER (`/manager`)
- **Módulo RBAC**: **no hay un gate único**. Base fail-closed en `api/main.py` (`_MANAGER_BASE` =
  `verify_api_key` + `require_any_module(("manager","manager_comercial","manager_clientes",
  "manager_clientes_bulk"))`) + **gate fino POR SUB-ROUTER** en `api/routers/manager/__init__.py`.
- **Roles**: `admin` (todo) y `asistente_comercial` (entra por `manager_clientes`, `manager_instrumentos`, `manager_contrapartes`, `manager_aunesa` — sin el umbrella).
- **Front**: `manager/page.tsx` (404 si el user no tiene ninguno de `["manager","manager_clientes","manager_clientes_bulk","manager_titulos"]`) → `manager-view.tsx` + 24 paneles.
- **Proxy**: catch-all con GET/POST/PATCH/PUT/DELETE, `maxDuration = 90 s` (los backfills largos se cortan ahí; por eso el front batchea y reintenta con backoff ×3). Devuelve **502 `{error}`** ante cualquier excepción y reenvía el status del backend tal cual.
- **Router**: paquete `api/routers/manager/` (27 sub-routers). `manager_resources.py` (CPU/RAM del Droplet, fuera del paquete) se ELIMINÓ 2026-08-10 junto con la tab RECURSOS.

#### Gates por sub-router
| Constante | Módulos que habilitan (OR) | Sub-routers |
|---|---|---|
| `_MGR` | `manager` | status, latencia, controles, diagnostico, checks, jobs, options, logs, users, roles, grupos, aunesa, valuaciones, operaciones, documentos, mesa, salud |
| `_CLIENTES` | `manager` ∨ `manager_clientes` | clientes.router, aca_valores, control_automatico |
| `_CLIENTES_BULK` | `manager` ∨ `manager_clientes_bulk` | clientes.bulk_router |
| `_TITULOS` | `manager` ∨ `manager_titulos` | assets, ons, bonos, breakevens, renta_variable |
| `_INSTRUMENTOS` | `manager` ∨ `manager_titulos` ∨ `manager_instrumentos` | instrumentos |
| `_CONTRAPARTES` | `manager` ∨ `manager_contrapartes` | contrapartes |
| `_AUNESA` | `manager` ∨ `manager_aunesa` | import_tenencia |

#### Tabs (11 top-level, 36 hojas)
| Tab / hoja | Qué muestra | Endpoints | Filtros | Escrituras |
|---|---|---|---|---|
| **OBS → SALUD** (default) | Un veredicto único + un chequeo por job / dato / control (peor primero; lo verde oculto salvo toggle). Cada fila se despliega con evidencia, diagnóstico IA, detalle crudo (corridas con log · fechas cargadas · anomalías) e historial de transiciones. **La misma fila y el mismo detalle se renderizan en el mini-panel del botón SALUD de la barra inferior** (`salud-chequeo.tsx`, compartido): desde ahí también se despliega y se silencia, sin pasar por Manager | `GET /salud`, `/salud/detalle`, `/salud/diagnostico`, `/salud/historial` | toggle "ver también lo que está bien" | **PUT `/salud/alerta`** (silenciar / reactivar un chequeo) · **POST `/salud/vistos`** (el "entendido" del modal) |
| **OBS → CONTROLES** | 6 controles de calidad de datos (FORWARDS, RF SIN TASA, CARTERAS, ROFEX, NIVEL 1, CONTRAPARTES) con items activos/resueltos, antigüedad y botón "ir a la tab donde se corrige". Badge `!N` | `GET /controles?resueltos_dias=7` (y `=0` para el badge); `POST /jobs/run` `tipo=controles_datos` + polling | sub-tab por control; toggle activos/resueltos; `resueltos_dias` (0–90) | **"CORRER AHORA"** dispara `jobs.controles_datos`. Auto-ejecuta solo si la última corrida tiene >60 min |
| **OBS → DIAG → ÁRBOL** | Árbol de salud por VISTA (HOME/OPERAR/MERCADOS/NEGOCIO/BACK OFFICE/PORTFOLIOS) con motores/jobs/APIs, cadencia, "hace", última corrida y badge OK/LENTO/ATRASADO/CRÍTICO/FUERA RUEDA/SIN DATOS. Auto-refresh 10s | `GET /diagnostico` | colapsar/expandir por vista (localStorage) | ninguna |
| **OBS → DIAG → LOGS** | journalctl de los servicios systemd (motores + `api` + `cloudflared`), o TODOS mezclados cronológicamente | `GET /logs/services`, `GET /logs?servicio=&lines=` | dropdown SERVICIO (incl. `__todos__`), líneas (1–500), botones ALL/WARN/ERROR, buscador client-side, toggle auto-refresh | ninguna |
| **OBS → JOBS → CATÁLOGO** | TODOS los crons parseados de `deploy/crontab.txt` **en runtime** + último run por módulo; marca SIN REGISTRO a los no instrumentados | `GET /jobs/catalogo` | buscador client-side | ninguna |
| **OBS → JOBS → HISTORIAL** | Historial crudo de corridas + stats por tipo | `GET /jobs/history`, `/jobs/history/stats` | TIPO, STATUS (`ok`/`partial`/`error`), `desde`/`hasta`, `limit` ≤500 | ninguna |
| **OBS → BASE** | Espacio/salud de Postgres: total vs límite del plan (`DB_DISK_LIMIT_GB`, def 8 GB), por schema, top tablas con bloat (dead tuples). Refresh 60s | `GET /db-observabilidad` | — | ninguna |
| **OBS → LATENCIA** | Ranking de endpoints por tiempo total consumido (req × avg) + serie horaria; semáforo <500 ms / 500-1000 / >1s | `GET /latencia?horas=` | ventana 24 h / 7 d / 30 d (`horas` 1–720); `top` 1–200 (**la UI no lo expone**) | ninguna |
| **VALIDACIONES → VALIDACIONES** | Checks/debug de cálculos: tasa fija en AuM, breakevens paso a paso, debug soberano, debug TEA, TNA futuros DLR, pivot points, títulos sin flujo | `/checks/*` (9 endpoints) + `POST /jobs/run` | selects/inputs de ticker por bloque | **BACKFILL TASAS** (`jobs.backfill_tasas`) |
| **VALIDACIONES → OPCIONES VTO** | Vencimientos publicados por el motor vs activos (elegidos a mano); flag `auto_pick` si la lista está vacía | `GET`/`PUT /options/expiries` | checkboxes (formato `YYYYMMDD`, len 8) | **PUT** persiste en `options_metadata.config` (merge jsonb; el motor los toma en ~5 min) |
| **VALIDACIONES → DEBUG XIRR** | Desglose mes a mes del XIRR: flujos individuales, cashflow pegable en Excel, TEA mensual, base 100 | `GET /valuaciones/debug?id_cuenta=` | input `id_cuenta` | ninguna |
| **VALIDACIONES → DEBUG TEA** | Debug paso a paso de TEA/TNA/Duration de un ticker | `GET /checks/debug-curva-tea` | input ticker | ninguna |
| **TÍTULOS → INSTRUMENTOS** | Instruments de pyRofex agrupados por CFI code + detalle (read-only) | `GET /checks/discovery-pyrofex`, `/checks/instruments-by-cfi` | select CFI, select underlying, buscador | ninguna |
| **TÍTULOS → ASSETS** | Catálogo `portafolio.assets` editable: CARTERA, EMISOR, INSTRUMENTO, CLASE_ACTIVO, CALIFICACIÓN, TICKER, VENCIMIENTO, CODIGO_CNV, FEE_ADMIN | `GET /assets`, `/assets/values`, `PATCH /assets` | buscador de unidad (client), select CARTERA, select EMISOR, select **CAMPO VACÍO** | **PATCH** fila a fila. Setea `actualizado_por`/`actualizado_at`. **Cambiar CARTERA invalida el cache `tenencia_dias`** |
| **TÍTULOS → BONOS** | 4 cuadrantes (ver §4.2) | ver §4.2 | ver §4.2 | Alta/edición/baja de bonos y ONs con cronograma, ignorar/designorar del conciliador |
| **TÍTULOS → BREAKEVENS** | 50/50: izq matriz de pares (motor + manuales `✎`) con flag `excluido`; der cobertura (por qué cada bono entra o no) | `/breakevens/pares`, `/breakevens/diagnostico`, `/breakevens/candidatos` | checkbox "solo los que NO entran" | Excluir/reincluir · **`+ par manual`** (`POST /breakevens/manual`) · borrar manual |
| **TÍTULOS → RENTA VARIABLE** | Grid de CEDEARs: ticker, nombre, rubro, `es_ia`, RIC Refinitiv, `ratio` | `GET /renta-variable`, `/rubros`, `POST /rubro`, `PATCH`, `DELETE` | buscador; select de rubro por fila (**catálogo cerrado**) | **PATCH** por fila, **crear rubro**, **DELETE** saca el CEDEAR del universo (borra master + snapshot) |
| **CLIENTES → SEGMENTACIÓN** | Grid editable de `clientes.comitentes`: operador + nivel_1..5 + primer_contacto_comercial, riesgo_la_ft, division, adc, dma, observaciones, sucursal, referido | `GET /clientes`, `/clientes/values`, `PATCH /clientes`, `POST /clientes/bulk` | select OPERADOR (incl. `__vacio__`), **selects NIVEL 1..5 en cascada** (cambiar uno resetea los inferiores), select CAMPO VACÍO, buscador `q` | **PATCH** fila a fila; **📁 Importar archivo** (.csv/.xlsx) → `POST /clientes/bulk` (requiere `manager_clientes_bulk`) |
| **CLIENTES → CONTROL AUTO** | Conciliación de un Excel de CUITs (columna `Nº ident.fis.1`) contra comitentes → `tenemos` / `no_tenemos` | `POST /control-automatico/reconciliar`, `/segmentar` | archivo (parseado en el browser) | **Segmentar**: setea `nivel_1 = PRODUCTORES` en las matcheadas |
| **CLIENTES → SIN OPERADOR** | Cuentas con volumen que caen en "(sin operador)", separadas en clientes reales / no-clientes / sin clasificar | `GET /clientes/sin-operador` | categoría (def `SIN CLASIFICAR`) | ninguna |
| **CLIENTES → FONDEOS** | Cupo de fondeo del custodio (ARS) por cuenta + nivel_3 editable inline. Solo con `canBulk` | `GET /clientes`, `/values`, `PATCH /clientes`, `POST /clientes/bulk-fondeo`, `/recalcular-niveles` | buscador `q`, toggle "solo cargados", select nivel_3 | **Import de Excel** de cupos; **PATCH** inline; **RECALCULAR NIVELES** (`apply=false` preview / `true` aplica) |
| **CONTRAPARTES → LISTADO** | cuenta + denominación (read-only, de Aunesa) y `contraparte`/`segmento`/`codigo_mae` editables | `GET /contrapartes`, `/segmentos`, `PATCH`, `POST /import` | select SEGMENTO, select CAMPO VACÍO, buscador | **PATCH** fila a fila; **import de Excel** (nunca da de alta ni borra) |
| **CONTRAPARTES → CONCILIADOR** | Cuentas de Aunesa (**LIVE, on-demand**) ausentes en Contrapartes cuya denominación matchea una contraparte conocida. Badge `!N` | `GET /contrapartes/reconcile`, `POST /contrapartes` | `limit` 1–2000 | **Alta de 1 click** (idempotente) |
| **ACA VALORES** | Set de cuentas "ACA VALORES" (lo usa el filtro de la vista OPERACIONES). 50/50 set ↔ buscador | `GET /aca-valores`, `/candidatos`, `POST`, `DELETE` | buscador (debounce 300 ms) | **Alta** (idempotente) y **baja** |
| **AUNESA → FLUJO** | Explorador **LIVE** de Aunesa: consolidado + boletos + movimientos crudos + categorías de un día | `GET /aunesa/explorar` | date picker (def hoy ART), vista consolidado/boletos/raw, filtro capturados/descartados/all, categoría, buscador | ninguna |
| **AUNESA → AUM** | Docs crudos de tenencia por (cuenta, fecha) con `valuacion_esperada` y `desvio` para detectar precios mal traídos | `GET /aum` | `id_cuenta`, select de fecha | ninguna |
| **AUNESA → POSICIÓN** | Posición valuada CRUDA pegando EN VIVO a Aunesa (`posicionValuada`) | `GET /aunesa/posicion` | `id_cuenta`, `desde` (**regla H1**: `desde=X` devuelve el día hábil anterior) | ninguna |
| **AUNESA → BOLETOS** | FALTANTES (boletos sin arancel en un rango) + BACKFILL (runner en background con historial) | `GET /aunesa/boletos/faltantes`, `POST /backfill`, `GET /backfill/{job_id}`, `GET /backfill?limit=` | DESDE/HASTA (def inicio de mes → hoy), ID CUENTA, `limit` 1–20000; backfill: cuentas, `workers` 1–20, toggle `apply` | **Arranca el backfill de aranceles** (`apply=true` escribe; `false` = dry-run) |
| **AUNESA → IMPORTAR AUM** | Import masivo a `portafolio.tenencia`: modo **PRECIOS** (`unidad·precio·fecha`) o **IMPORTAR AUM** (`Cuenta·Unidad·Cantidad·Fecha·Precio·Valuación`) + paso 2 de recálculo. **Es la ÚNICA sub-vista que ve `manager_aunesa` sin `manager`** | `POST /import-precios-sql`, `/import-aum-sql`, `/recalcular-valuacion-sql` | toggle PRECIOS / AUM; "descargar plantilla" (genera el .xlsx en el browser) | **Import** `commit=false` (previsualiza) → `true` (aplica; AUM hace **delete+insert por (fecha, id_cuenta)**); **recalcular valuación** |
| **OPERACIONES** | Backfill de `operaciones.operaciones` desde CSV/Excel parseado en el browser y enviado en lotes + panel BOLETOS ANULADOS | `GET /operaciones/stats`, `POST /faltantes`, `/fechas`, `/backfill`, `GET /anulados/resumen`, `POST /anulados/barrido`, `POST /anulados` | modo **FALTANTES** (def) / **FECHAS** (solo corrige `concertacion`) / **REEMPLAZAR** (upsert, el Excel pisa); dedup por boleto client-side + lotes | **Inserta/corrige/pisa operaciones** (con preview `commit=false` salvo `/backfill`); **anula** boletos; **barrido** de la marca ` (A)` que arrastra el gemelo sin marca (con confirm) |
| **MESA** | 4 paneles al 25 %: catálogo de TRADERS + las 3 allowlists de escritura | `/mesa/traders` y `/mesa/{,senebis-,tesoreria-}escritores` (+ `/candidatos`) | buscador de candidatos (debounce 300 ms) | **Alta/baja de traders y de escritores** en las 3 allowlists. Todo auditado |
| **DOCUMENTOS** | Carga manual de PDFs y comentarios para REPORTES FINANCIEROS de Research | `GET /documentos`, `POST`, `DELETE /{id}` | toggle **PDF / COMENTARIO** | **Alta** (PDF base64 — el front avisa si pesa >3.3 MB — o comentario) y **baja** con confirm |
| **USUARIOS → USUARIOS** | ABM de `manager.manager_users`: email, role, enabled, `control_comercial`, notes, last-seen | `GET /users`, `POST`, `PATCH /{email}`, `DELETE /{email}` | select ROLE (roles de la matriz vigente) | **Crear/editar/borrar** usuario (**self-delete permitido**). Todo audita en `role_audit` |
| **USUARIOS → ROLES Y PERMISOS** | Matriz roles × 22 módulos (checkboxes), con "crear rol nuevo" como columna y guardado en batch | `GET /roles`, `PATCH /roles/{role}` (uno por rol dirty), `GET /roles/audit` | input "nuevo rol…" | **PATCH** reemplaza la lista de módulos de cada rol modificado; los desconocidos se filtran en silencio. **Esta matriz SQL pisa `DEFAULT_MATRIX`** |
| **USUARIOS → GRUPOS** | CRUD de grupos de acceso por cuenta (`nombre`, `emails`, `id_cuentas`) + selector con las cuentas reales del último snapshot | `GET /grupos`, `POST`, `PATCH /{id}`, `DELETE /{id}` | buscador de cuenta; dropdown "+ agregar usuario…" | **Crear/editar/eliminar** grupo. **Borrar un grupo devuelve a esos usuarios a ver TODO** |

#### Endpoints — resumen por sub-router
`status`(1) · `latencia`(1) · `controles`(1) · `diagnostico`(2: `/diagnostico`, `/db-observabilidad`) ·
`checks`(9: `/checks/{debug-comercial, tasa-fija, tickers-curvas, debug-soberano, breakevens-debug,
futuros-dlr, debug-tna-futuros, debug-curva-tea, debug-pivot}`) · `jobs`(5: **`POST /jobs/run`**
whitelist `{cashflow, bcra, cleanup_curvas, backfill_tasas, controles_datos}`, subprocess con timeout
360 s, **rate limit `5/hour;20/day`**, estado en dict **in-process**; `/jobs/catalogo`, `/jobs/history`,
`/jobs/history/stats`, `/jobs/{job_id}` catch-all declarado **después** de los estáticos a propósito) ·
`options`(2) · `logs`(2, **whitelist estricta** derivada de `unidades_motores()` + `{api, cloudflared}`;
cualquier otro nombre → 400; cache 2s) · `users`(4) · `roles`(3) · `grupos`(4) · `aunesa`(6) ·
`valuaciones`(2: `/valuaciones/debug`, `/aum`) · `operaciones`(7) · `documentos`(3) · `mesa`(16) ·
`import_tenencia`(3) · `clientes.router`(4) · `clientes.bulk_router`(3) · `aca_valores`(4) ·
`control_automatico`(2) · `assets`(5, incluye **`PATCH /assets/{unidad}` DEPRECATED** que delega —
rompía con caracteres especiales URL-encoded) · `ons`(10) · `bonos`(6) · `breakevens`(2) ·
`renta_variable`(5) · `instrumentos`(2) · `contrapartes`(6).
**Total declarado en el relevamiento: 121** (la suma de sus propias tablas da **122** — 1 de diferencia
sin reconciliar).

#### Fuentes de datos
`manager.*` (`manager_users`, `role_matrix`, `role_audit`, `job_runs` TTL 60 d, `latencia_endpoints`
—telemetría endpoint × hora que flushea el middleware `api/telemetria.py`—, `controles_datos`,
`pyrofex_discovery`/`pyrofex_instruments`) · `portafolio.assets` + `tenencia` · `mercado.*` (`curvas`,
`options_metadata.config`, `cedears`+`cedears_snapshot`+`rubros`, **`breakevens_overrides`** —creada con
`CREATE TABLE IF NOT EXISTS` **desde el propio service**, no depende de `sql/schema.sql`—,
`ons_ignoradas`, y todos los snapshots como sensores de frescura de `/status`) · `clientes.*`
(`comitentes`, `cuentas`, `contrapartes`, `operadores`, `aca_valores`) · `operaciones.*` (`operaciones`,
`negocio_movimientos`, `mesa_dinero_*`, `senebis_escritores`, `tesoreria_escritores`, `movimientos`,
`motor_heartbeat`) · `research.documentos` · `ia.*` vía `/api/ia/*` · **feeds/host**: API Aunesa LIVE,
`journalctl`, `psutil` + `deploy/systemd/*.service`, `deploy/crontab.txt` parseado en runtime,
`pg_stat_user_tables`, subprocess `python -m jobs.<x>`.

#### Notas / rarezas
- **El gate base NO cubre 4 de los 6 sub-módulos**: `manager_titulos`, `manager_contrapartes`, `manager_aunesa` y `manager_instrumentos` **no están** en `_MANAGER_BASE`. Un rol con SOLO uno de esos quedaría bloqueado en la puerta aunque el sub-router lo permita. En la práctica no se nota porque `asistente_comercial` trae también `manager_clientes`.
- **`manager_comercial` aparece en `_MANAGER_BASE` pero YA NO EXISTE** en `MODULES` → `has_access` lo trata fail-closed y **loguea `has_access: módulo desconocido` en CADA request** de un rol sin `manager`. Rama muerta + ruido. El mismo string stale está en 3 docstrings.
- **Tres listas para el mismo gate de MANAGER, y NO coinciden** (lo dice el comentario del propio código): `header.tsx::MANAGER_MODULES` = 3 módulos; `app/manager/page.tsx::MANAGER_MODULES` y `proxy.ts::PATH_MODULES["/manager"]` = 4 (agregan `manager_titulos`); `manager-view.tsx::TAB_MODULES` es otra. Consecuencia real: **un rol con SOLO `manager_titulos` entra a `/manager` por URL y ve la tab TÍTULOS, pero no ve el link MANAGER en el header**; y un rol con solo `manager_contrapartes`/`manager_aunesa` **recibiría 404 de la página** aunque el backend lo autorice.
- **`modules === null` es permisivo**: si `getMe()` falla o no hay `API_URL` (dev), `ManagerView` muestra TODAS las tabs. El gate real sigue siendo el backend.
- **`GET /api/manager/status` está montado pero el frontend no lo consume** (la tab usa `/diagnostico`). Igual `GET /checks/debug-comercial` y `GET /checks/futuros-dlr`: candidatos a poda o de uso vía curl.
- **Patrón preview → commit** en casi todas las escrituras masivas. **La excepción es `POST /operaciones/backfill`, que pisa directo sin preview.**
- **La tab USO fue decomisada** (telemetría de uso de módulos, tabla `manager.uso_modulos` eliminada 2026-08-04); el union de estados la conserva solo para migrar el localStorage viejo.
- **La pill IA se dio de baja** (2026-08-19) y sus 5 endpoints se borraron (2026-08-28). El gasto y los errores del gateway los vigila ahora el chequeo `ia:gateway` del AV AGENT.
- **AUNESA con `manager_aunesa` sin `manager`** colapsa a una sola sub-vista: `AunesaGroup` fuerza `subEff="importar"`.
- `import_tenencia.py` conserva un modelo `_Row`/`_ImportReq` **sin usar** (residuo del endpoint viejo que escribía la colección Mongo `Valuaciones.AuM`).

---

## 4.11 IA — EL MOTOR SIN NINGÚN AUTO

**No hay una sola feature de IA corriendo.** Ni una llamada a un modelo, ni una
pantalla, ni un job. Lo que SÍ quedó, a propósito, es el **núcleo del gateway**:

| Pieza | Qué es | Estado |
|---|---|---|
| `core/llm.py` | transporte y ruteo: HTTP, retry, dialecto de cada proveedor, precios, y el **ruteo fail-closed** (una tarea marcada `datos:"negocio"` SOLO corre en un proveedor con `no_entrena=True`; si no, el gateway **niega la llamada**) | vive, sin tráfico |
| `core/ai.py` | tareas registradas, presupuesto diario como kill-switch, y la traza obligatoria | vive, **1 tarea: `smoke`**, que no produce nada |
| `ia.trazas` | una fila por llamada al modelo: tarea, modelo, usuario, tokens, latencia, la pregunta y la respuesta | vacía |
| `ia.config` | los topes diarios de tokens (precedencia: tabla > env > default) | vacía |

**Por qué se conservó** (decisión del user, 2026-08-28, al ver que el borrado se
llevaba puesto el gateway): es lo que costó construir y lo que no conviene
rehacer desde cero. El criterio que tiene adentro —privacidad fail-closed,
presupuesto como corte duro, traza obligatoria— es más caro que el código.

**Lo que NO sobrevivió** y hay que reescribir el día que haya una tarea:
`api/services/ia_obs.py` (leía las trazas), el chequeo `ia:gateway` de SALUD
(vigilaba el gasto) y `scripts/smoke_ai.py` (probaba el gateway). Están en git.

El resto de esta sección es qué se probó y por qué no quedó — para que nadie lo
vuelva a proponer sin novedad.

### Lo que hubo, y por qué se fue

| Qué | Cuándo se fue | Por qué |
|---|---|---|
| **Asistente legacy** (`api/agent/` + `POST /api/chat`) | 2026-06-03 | reemplazado por el copiloto |
| **Copiloto de mesa** por vista, **asistente de negocio**, **guía de la plataforma** con navegación asistida, **vigía** de `/trading` | 2026-08-19 | *«sirvió como inicial pero no cumplió con la necesidad»*. Un copiloto **contesta lo que le preguntás**: requiere que ya sepas qué preguntar, y no deja nada atrás. El backfill de tenencias falló dos días y nadie iba a preguntarle «¿corrió el backfill?» |
| **Triage de incidentes** (cada 10 min) y **control de calidad de conversaciones** (diario) | 2026-08-19 | escribían en tablas que **nadie abrió nunca** |
| **MCP server** (13 tools de renta variable + OAuth 2.1) | 2026-08-28 | no lo consumía nadie, y su provider OAuth quedaba alcanzable sin autenticar |
| **Destilado del research 1816** — la última tarea | 2026-08-28 | *«ese destilado no tiene sentido, no se usa en absoluto; el research se guarda y se muestra así nomás»* |
| **Los lectores del gateway**: `api/services/ia_obs.py`, el chequeo `ia:gateway` de SALUD, `scripts/smoke_ai.py` | 2026-08-28 | sin llamadas al modelo no hay nada que leer ni que vigilar. El gateway en sí **se conservó** (arriba) |

### La lección, que es lo único que vale la pena guardar

El destilado tenía **dos candados independientes**, y ninguno de los dos lo puso
nadie a propósito:

1. **Casi nunca corrió**: era opt-in por el flag `--destilar` y la línea del cron
   no lo llevaba. ⚠️ Al ir a dropear las columnas, la guarda del script encontró
   **4 mails CON destilado**: alguien lo corrió a mano alguna vez. La afirmación
   «nunca corrió» era una DEDUCCIÓN desde el crontab, no un dato medido — REGLA
   #2 en su forma más común: una inferencia razonable escrita como hecho.
2. **No tenía dónde mostrarse**: el campo viajaba en el payload de
   `/research1816/mails` y ningún componente lo dibujaba (`ResearchDestilado`
   estaba declarado en `research-view.tsx` sin un solo uso en JSX).

O sea: una feature apagada, sin lector, justificando ella sola el gateway. **Y no
fallaba nada** — ese es el punto. No había error, no había alerta; simplemente
nadie lo miraba.

> **LA REGLA, si algún día vuelve la IA: ¿QUIÉN MIRA SU SALIDA?**
> `triage_incidente` corrió cada diez minutos durante semanas contra una tabla
> que nadie abrió, y no se notó **porque funcionaba**: no fallaba, no daba error,
> solo gastaba. Eso es más difícil de detectar que un bug. Antes de sumar una
> tarea de IA, la respuesta no puede ser «queda en una tabla».

### Lo que la gente confunde con IA, y no lo es

- **El BRIEFING** (`GET /api/ia/briefing`, modal de HOME 10:00 ART) — futuros US,
  dólar oficial (MAE live + A3500) y cierres MEP/CCL con variaciones. **100%
  determinista, cero tokens, nunca gastó uno.** Es lo único que queda bajo el
  prefijo `/api/ia`, que hoy es un nombre histórico.
- **El AV AGENT** (`agente/`, 18 habilidades) — `usa_ia=False` en las 18, y un
  test lo congela. Que el gateway exista no lo cambia: ninguna habilidad lo llama.
- **La vista RESEARCH** — el mail de 1816 se guarda crudo y se muestra tal cual.
  BCRA, FRED y las series de 1816 son ingestas de API, sin modelo en el medio.

### RBAC del prefijo `/api/ia`

Queda un endpoint (`/briefing`) y **el invariante de REGLA #8 sigue congelado**:
`test_rbac.py::test_al_invitado_solo_le_queda_el_BRIEFING_bajo_api_ia` recorre el
router y exige que todo lo demás bajo ese prefijo lleve `require_admin`. Es
estructural, así que cubre también al endpoint que todavía no existe.

## 4.12 ACA — RESUMEN EJECUTIVO DE INVERSIONES

> Doc dedicado **[VIVO]**: `docs/ACA.md`. Acá va la superficie; ahí van las fórmulas,
> las decisiones y el changelog.

### Vista: ACA (`/aca`)
- **Módulo RBAC**: `aca`. Lo tiene el rol **`empleado_aca`** (+ admin). El gate real
  (`require_lectura_aca`, montado sobre TODO el router en `api/main.py`) es la **UNIÓN** del
  módulo con la allowlist de escritura: escribir implica ver, así no puede existir alguien que
  cargue el informe en una pantalla que no ve. `/api/me` publica `aca` dentro de `modules`
  también para esos escritores, para que el nav y `src/proxy.ts` filtren igual.
- **Escritura**: **reusa `operaciones.mesa_dinero_escritores`** + admin. La mesa es la que maneja
  la cuenta de ACA; una allowlist propia sería una lista más para desincronizar.
- **REGLA #8**: `aca` NO está en `INVITADO_MODULES` ni `/api/aca` en `GUEST_PATH_PREFIXES`.
  Es el negocio de la casa. Hay tests que fallan si alguien lo agrega.
- **Nav**: link de PRIMER NIVEL del header (entre RESEARCH y el dropdown
  MERCADOS), **no** un item de NEGOCIO — decisión del user 2026-08-13: es la
  cartera de la casa mirada por gerencia, no una vista más de la operación diaria.
- **No es live**: es una **foto MENSUAL**. El selector de la barra elige el período (`YYYY-MM`)
  y todo lo de abajo habla de ese mes. **Sin polling**.

| Tab | Qué muestra | Endpoints | Escrituras |
|---|---|---|---|
| **RESUMEN** (def.) | Fila de 6 datos (informe al · MEP · A3500 · Valuación ARS/A3500/USD MEP), torta de composición por cartera y los **dos cuadros comparativos** (mes actual y mes anterior) con monto y ponderación por cartera + Total Dolarizado / Total Pesos | `/vista` | `POST /periodos` (+ PERÍODO, EDITAR MEP/A3500), `DELETE /periodos/{p}` |
| **CARTERAS** | Los 3 gráficos de rendimiento **ACUMULADO** vs benchmarks: `total_ars`, `total_usd`, `pesos`. Series de grupo `cartera` sólidas, `benchmark` punteadas | `/vista` (`graficos`) | Ninguna |
| **ACTIVOS** | Detalle por cartera (las 4 canónicas + cualquier otra que aparezca cargada) con total y % share. **Acá se carga lo manual**: VN y PX por fila | `/vista`, `/titulos`, `/precios-sugeridos` | `POST /activos`, `DELETE /activos`, `POST /clonar` |
| **MÉTRICAS** | Apertura por clase de activo (CARTERA FCI, CARTERA ARS) y por emisor (CARTERA HD, CARTERA DL, CRÉDITOS PRIVADOS MÁS REPRESENTATIVOS) | `/vista` | Ninguna |
| **HISTÓRICO** | La planilla mensual completa: mensual + acumulado por serie. **Solo lectura** | `/historico` | Ninguna — se carga en Manager → ACA |

**Filtros**: uno solo, el **período** (`YYYY-MM`) en la barra. Sin período cargado la vista se
explica sola en vez de tirar error.

**Ayudas de carga en ACTIVOS** (las tres, solo para escritores):
- **COPIAR DEL MES ANTERIOR** (`POST /clonar`) — clona títulos, VN y observación del período previo.
  **NO copia el precio**, a propósito: arrastrarlo dejaría un informe que parece cargado y está
  mintiendo. Idempotente (`ON CONFLICT DO NOTHING`): no pisa lo ya cargado.
- **VER PRECIOS DE REFERENCIA** (`GET /precios-sugeridos`) — muestra bajo cada campo PX el último
  precio conocido en `portafolio.tenencia` **con su fecha**. Orientativo; no se aplica solo.
- **+ AGREGAR TÍTULO** (`GET /titulos`) — buscador sobre `portafolio.assets` que muestra la ficha
  que el título va a heredar ANTES de agregarlo.
- **⬆ IMPORTAR EXCEL** (`POST /activos/importar`) — sube un `.xlsx`/`.csv` con las mismas columnas
  de la tabla. **Dos pasos**: preview con `dry_run` (default **true**: pedir el import sin decir
  nada NO escribe) → CONFIRMAR. Lee TICKER/VN/PX/TASA/OBS con encabezados normalizados y
  sinónimos; ignora emisor/calificación/clase/vencimiento (los da el maestro) y el monto (se
  deriva). Resuelve el título por unidad → ticker → el ticker antes de un `" - "` → instrumento;
  **si es AMBIGUO o no está en el maestro NO lo importa** y lo devuelve con su motivo y su fila.
  Duplicado en el archivo → gana el último, marcado. Título ya cargado → lo PISA, y avisa cuántos
  antes de confirmar.

**Endpoints (14)**: `GET /vista` (la pantalla entera en UN request) · `/periodos` · `/resumen` ·
`/detalle` · `/metricas` · `/historico` · `/graficos` · `/titulos` · `/precios-sugeridos` ·
`/catalogos` · **`POST /periodos`** · **`DELETE /periodos/{periodo}`** · **`POST /clonar`** ·
**`POST /activos`** · **`DELETE /activos`** · **`POST /historico`** · **`DELETE /historico`**.

**Reglas no inferibles** (todas server-side — el front NO recalcula nada):
- `monto = vn × px / 100` si cartera ∈ {ARS, DL, HD} (paridad), `vn × px` si no (FCI/RV/cash).
  Es la **misma regla de divisor que la valuación del AuM**. Override manual por fila (`monto`).
- `vn` o `px` en null → monto **null, no 0**: "sin cargar" ≠ "vale cero". Esas filas van en ámbar.
- `valuacion_a3500 = valuacion_ars / a3500`; `valuacion_usd_mep = valuacion_ars / mep`. Sin el TC
  cargado devuelven **null** (dividir por un dato inexistente inventa el número más visible).
- **Total Dolarizado / Total Pesos** salen de `aca.moneda_regla` (editable), y la regla de **CLASE
  gana sobre la de CARTERA** — eso es lo que permite que el FCI se parta por moneda (MM USD y HD T1
  a dólares; MM ARS, ARS T1 y RENTA VARIABLE a pesos) **sin dejar de ser su propia cartera**. Lo que
  ninguna regla resuelve cae en **`sin_clasificar`**, que se muestra como **fila propia del cuadro
  de totales** (el banner ámbar arriba del informe se sacó el 2026-08-14 — ensuciaba la vista; el
  backend no cambió): un activo nuevo tiene que aparecer como pendiente, no colarse en el lado
  equivocado.
- **La ficha del título NO se guarda acá**: cartera, emisor, calificación, clase de activo,
  vencimiento y ticker se resuelven en cada lectura contra `portafolio.assets`. Un asset borrado del
  maestro deja la fila viva marcada `sin_ficha` (el monto ya contado no se evapora); un asset con
  asset SIN FICHA cae en `huerfanos`, que es el ÚNICO caso que queda ahí.
- **La cartera del asset abre su propio cuadro, sea cual sea** (2026-08-19): las 4 canónicas
  siempre (aunque cierren en cero) y cualquier otra que aparezca cargada —DEUDORES,
  FINANCIAMIENTO…— detrás, alfabética. Antes era una constante de 4: una cartera nueva sumaba al
  total pero no entraba a ningún cuadro y NO se podía agregar desde la aplicación. El divisor NO
  se adivina: fuera de ARS/DL/HD vale `vn × px` (ver `_DIVISOR_PARIDAD`).
- **Métricas**: dos denominadores distintos — clases y emisores de una cartera van sobre el total de
  ESA cartera; **créditos privados va sobre la valuación TOTAL**. Los catálogos hacen que una fila
  aparezca aunque cierre en cero, y lo que aparece sin estar catalogado se muestra marcado
  `fuera_catalogo` (el catálogo agrega filas, nunca esconde plata). Excepción: `privados` muestra
  SOLO su catálogo, porque mostrar todo convertiría "los más representativos" en "todos".
- **Acumulado** = `(1 + acum_anterior) × (1 + mensual) − 1`. **No se persiste** (se deriva en la
  lectura). Un mes sin dato **arrastra** el acumulado anterior; antes del primer dato es null.

**Fuentes**: schema `aca` — `periodos`, `activos`, `moneda_regla`, `emisor_destacado`,
`clase_destacada`, `series`, `historico`, `audit` · **`portafolio.assets`** (la ficha) ·
`portafolio.tenencia` (solo los precios de referencia) · `macro.series_macro` (benchmarks
automáticos) · `operaciones.mesa_dinero_escritores` (permiso de escritura).

### Vista: MANAGER → ACA
Dos sub-pills. **HISTÓRICO**: carga del rendimiento mensual por serie × período (se tipea en
**porcentaje**, se guarda como fracción; la columna acumulada es de solo lectura porque es un
derivado). **CONFIGURACIÓN**: regla de moneda (con aviso de qué clases del catálogo de títulos
todavía NO tienen regla — que son justo las que van a caer en "sin clasificar"), emisores y clases
destacadas de las métricas, y el catálogo de series (nombre, grupo, fuente, en qué gráficos entra).

**Endpoints (9)**: `GET /api/manager/aca/catalogos` · `PUT|DELETE /aca/moneda-regla` ·
`PUT|DELETE /aca/emisor` · `PUT|DELETE /aca/clase` · `PUT|DELETE /aca/serie` (la baja de serie es
**lógica**: sus valores históricos se conservan).

Entrar a la tab lo da el módulo `manager`; **escribir** lo gobierna la misma allowlist que escribe en
la vista (la configuración mueve números del informe, así que no puede ser un permiso más flojo que
cargarlo).

**NO hay automatización — TODO se carga a mano** (2026-08-19). Ninguna celda de ACA se completa sola
desde otra fuente del sistema: el histórico tenía series que traían el mensual de `macro.series_macro`
(el A3500 salía de la variación del dólar) y se dio de baja entera. Lo único derivado es el
**ACUMULADO**, que encadena los mensuales tipeados — deriva de lo cargado, no importa un dato.
`aca.series.fuente`/`escala` quedan como columnas VESTIGIALES (nadie las lee; `apply_schema` las
normaliza a `'manual'`). Congelado por test. Ver `docs/ACA.md` §5.

---

## 5. PATRONES TRANSVERSALES

### 5.1 Gates que NO son de módulo

| Gate | Dónde vive | Dónde se aplica (verificado) | Qué exige |
|---|---|---|---|
| `require_admin` | `api/auth.py:398` | **Las 11 rutas de `/api/agente`** (el AV AGENT entero) + `GET /api/scanner/day-trading`, `GET /api/scanner/companeros/{ticker}`, `POST /api/back-office/senebis/proximo-id` — **14 rutas** (medido con `api/superficie.py`; las 5 de `/api/ia` se borraron el 2026-08-28) | `get_user_role(email) == "admin"` **directo, sin mirar la matriz** → **no delegable** desde el panel. Rechaza guest siempre |
| `require_control_comercial` | `api/auth.py:425` | Las 5 rutas `/api/operaciones/comercial/control/*` (objetivos GET+PATCH, objetivos-vs-actual, por-operador, totales) — **incluidas las de lectura** | Permiso **PER-USUARIO**: `admin` **o** flag `control_comercial=true` en `manager.manager_users` (tildado en Manager → Usuarios). Cache 60s. Rechaza guest |
| `require_no_invitado` | `api/auth.py:442` | Las **5 PATCH** de `/api/derivados/agro/*` | Bloquea www en escrituras que caen dentro de un módulo que el invitado SÍ tiene |
| `require_lectura_mesa` | `mesa_dinero.py` → `mesa_dinero.puede_ver` | **Las 9 rutas** de `/api/mesa-dinero` (montado a nivel router en `api/main.py`) | Permiso **PER-USUARIO**: admin **o** email en `mesa_dinero_lectores` ∪ `mesa_dinero_escritores`. Cache 60s. **Reemplazó al gate de módulo `operaciones`** (2026-08-11) |
| `require_escritura_mesa` | `mesa_dinero.py` → `mesa_dinero.puede_escribir` | `POST/PATCH/DELETE /api/mesa-dinero/ops*`, `PUT /api/mesa-dinero/tc` (4) | admin **o** email en `operaciones.mesa_dinero_escritores` |
| `require_escritura_senebis` | `senebis.py` → `senebis.puede_escribir` | `POST /ops`, `PATCH /ops/{id}`, `DELETE /ops/{id}` (3) | admin **o** email en `operaciones.senebis_escritores` (allowlist separada) |
| `require_escritura_tesoreria` | `back_office.py` → `tesoreria.puede_editar_saldo` | **24 rutas** de `/api/back-office/tesoreria/*` | admin **o** email en `operaciones.tesoreria_escritores` |
| `verify_ingest_token` | `api/routers/ingest.py` | 9 POST + 4 GET de `/api/ingest/*` | Header `X-Ingest-Token` propio — **no** pasa por `verify_api_key` ni por RBAC |

Las **3 allowlists** se gestionan desde **Manager → MESA** (con `/candidatos?q=` que lista usuarios que
aún no están), todo bajo `require_module("manager")`. Las 3 son **default-deny** y **admin siempre
puede** (no queda afuera de su propia gestión).

### 5.2 Scope de cuentas por GRUPO (segunda dimensión, ortogonal al módulo)

El módulo dice *qué vista*; el grupo dice *qué cuentas dentro de la vista*.
- `cuentas_visibles(email)`: `None` = sin restricción (**admin** y usuario **sin grupo**); `set` = unión
  de `id_cuenta` de sus grupos (`manager.grupos`, ABM en Manager → GRUPOS). Cache TTL 60s.
  **Fail-open**: ante error de DB devuelve `None` (ve todo) — decisión explícita en el docstring.
- Dependencies: `scope_cuentas` (inyecta un **tuple ordenado**, hashable → sirve de cache key),
  `verificar_id_cuenta` / `verificar_id_cuenta_opcional` (403), `verificar_account` (para `/api/ordenes*`:
  además **400** si un usuario scopeado omite la cuenta, para que no caiga al default del `.env`),
  helpers `filtrar_rows`, `filtrar_cuentas_str`, `verificar_cuenta_str`, `aplicar_scope_cuenta`.
- Se usa en `api/routers/{carteras, valuaciones, operaciones, ordenes, operativa, risk, operar,
  back_office}.py` y `api/services/comercial_sql.py`.
- **Variante propia de AUM**: `carteras.py::scope_aum` = scope de grupos **∩** cuentas del `operador`
  **∩** `nivel_1`. La usan `fci-serie/snapshot`, `total-serie/snapshot`, `diff`; **NO** la usan
  `listar_aum`, `pnl-todas` ni `cuentas` (esas van con `scope_cuentas` pelado).
- **Borrar un grupo devuelve a esos usuarios a ver TODO.**

### 5.3 Filtro de cuenta por TIPO (`_cuentas_filter.py`) — **NO es un permiso**

Es un selector de UI: `VALID_FILTERS = todas | accionistas | sin_accionistas | cooperativas |
productores`. `productores` matchea por **`id_cuenta`** contra `clientes.comitentes.nivel_1='PRODUCTORES'`;
los otros tres por el **string `cuenta`**; `cooperativas` = NOT IN accionistas **y** regex `\bcoop`
(case-insensitive). El módulo original es **legacy Mongo** (devuelve sub-docs `$match`); el equivalente
vivo en SQL es `portfolio_sql._cuenta_filter_sql`. Lo consumen AUM y CARTERAS→TOTALES; el filtro
equivalente del tab DEPÓSITOS & EXTRACCIONES es **client-side en React**.

### 5.4 Patrones de UI que se repiten

| Patrón | Implementación | Dónde |
|---|---|---|
| **Cuadrantes 50/50 (grid 2×2)** | `grid-cols-2` + `grid-rows-2` con `min-w-0 min-h-0` (comentado: evita "grid blowout") | HOME, `/renta-fija`, `/renta-variable`, `/ons`, `/derivados`, Research→RF ARGENTINA, `/mesa-dinero`, Tesorería (MERCADOS, CHEQUES, BANCO A BANCO) |
| **Tabs keep-alive** (se montan la 1ª vez y luego se ocultan con `display:none` → no re-fetchean ni pierden estado) | `visited: Set<Tab>` + `<Pane active>` | `trading-shell`, `operaciones-view`, `research-view` |
| **Tabs que sí desmontan** | render condicional simple | `back-office-shell`, `manager-view` y sus grupos, `agro-shell`, `operar-shell`, `valuaciones-shell` |
| **Panel maximizable** (header con ⛶/⊡ + Esc) | `Panel expandable` → overlay por `createPortal` | `/renta-fija`, `/ons`, `/renta-variable`, `/derivados`, `/sinteticos`, agro-datos, agro-mejoras-dispo |
| **Wrapper maximizable genérico** (botón flotante on-hover, NO desmonta el hijo) | `<Maximizable>` `position: fixed inset-0` | los 5 panes de `/research` y los 4 cuadrantes de RF ARGENTINA |
| **Preferencias persistidas** | `usePersistedState(key, initial, "session"\|"local")` — `sessionStorage` por default (arranca limpio cada sesión), `localStorage` para preferencias duraderas | tabs (`trading.tab`, `operaciones.tab`, `backoffice.tab`, `manager.*`, `tes.tab`, `senebis.tab`, `mesaDinero.tab`), filtros (`operadores.*`, `referidos.*`, `reuters.*`), columnas (`tes.cols`, `reuters.ocultas` → `"local"`) |
| **Estado en la URL** (sobrevive F5 y se puede compartir) | `history.replaceState` + `URLSearchParams` | `/aum` (`?tab=`), `/valuaciones` (`?sub=`, `?cuenta=`); deep-links de entrada en `/agro` y `/operar` |
| **Polling** | `usePoll(endpoint, initial, ms)` — compara el **texto crudo** del payload y NO hace `setData` si no cambió (preserva identidad de referencia); expone `lastAt` y `error` | `/renta-fija` 5s (endpoint consolidado), `/renta-variable` 2s + 5s, `/ons` 10s, `/operar`→MEP 5s/60s, Manager→DIAG 10s, Manager→BASE 60s, Tesorería 20s (= heartbeat), SENEBIS 10s (= heartbeat) |
| **Selector de columnas** | menú con checkboxes + set persistido | Tesorería (`tes.cols`), Reuters (`reuters.ocultas`) |
| **Selector de cuenta con ◀ / ▶** | `CuentaCombobox` exportado desde `aum-view.tsx` y reusado | `/valuaciones` |
| **Piezas del INFORME** (pill de tab, panel de cabecera azul, dato de cabecera, `fmt0`/`fmt2`/`fmtPct`) | `components/ui/informe.tsx` | `/aca` y `/valuaciones` — una sola implementación para que los dos informes no se vean (ni redondeen) distinto |
| **Slot de la BARRA INFERIOR** — una vista baja sus acciones secundarias a la barra de estado del layout | `components/ui/slot-barra-inferior.tsx`: el footer renderiza `<SlotBarraInferior/>` (publica su nodo en un store de módulo) y la vista envuelve sus botones en `<EnLaBarra>`, que portaliza. **«Solo con la vista abierta» no se programa**: el portal vive adentro de la vista, así que se desmonta con ella — sin matcheo de rutas ni registro que mantener. Los botones se DEFINEN en la vista (cierran sobre su estado; el modal que abren no se mueve) y solo se DIBUJAN abajo. ⚠️ **El slot no consulta nada ni decide permisos**: el footer también se renderiza en el portal invitado, así que solo dibuja lo que le pasa una vista ya gateada por su módulo | `/valuaciones` (AJUSTES, TOTALES). Precedente del mismo patrón un nivel más abajo: Tesorería portaliza sus ABM de catálogo a la barra de tabs (`tesoreria-view.tsx` → `tesoreria-mercados.tsx`) |
| **Modal ABM genérico** | `components/ui/abm-modal.tsx` | Tesorería (bancos, entidades), catálogos de Manager |
| **Multi-select genérico** | `components/ui/multi-select.tsx` | `/operadores`, `/operaciones`→OPERACIONES |
| **Modal de auditoría "de dónde sale este número"** | click en celda/fila → endpoint `/detalle` que devuelve las operaciones individuales con `excluido` + `observacion`, calculadas **server-side con las mismas fuentes y filtros que el total** | Tesorería → BANCOS (cada celda) y Operadores → ESTADO COMERCIAL (días sin operar) |
| **Preview → commit** | `commit=false` previsualiza, `commit=true` aplica | casi todas las escrituras masivas de Manager (excepción: `POST /operaciones/backfill`) |
| **Botón ✦ IA por vista** | `IaVistaPanel` (drawer; el probe al backend decide si se renderiza) | header global + in-view en `/trading` y `/research` |
| **Navegación asistida** | `usePersistedState` escucha `ESTADO_APLICADO_EVENT` (`lib/aplicar-estado`) → el guía IA escribe claves de sessionStorage y deja la vista abierta en una tab/filtro concreto | toda la app; los modales de anuncio lo usan |
| **Guards de estado persistido obsoleto** | sanear el valor leído contra la lista viva de tabs | `trading.tab='reuters'`→`pivots`, `operaciones.tab='intraday'`→`operaciones`, `manager.obs.sub='uso'`→`controles`, `tes.tab='saldo_al2'`→`movimientos` |
| **Tema claro/oscuro** | clase `light` en `<html>` + script anti-parpadeo inline que lee `localStorage['aca-theme']` antes del paint | global |
| **Export a Excel** | client-side (`exportToXlsx`) con lo que está en pantalla | AUM, Operadores (×3), Referidos (×4), Carteras (×4), Títulos/Mercado |

### 5.5 El patrón de proxies de Next

El front **nunca** pega directo al backend desde el browser: pasa por route handlers en
`src/app/api/**/route.ts` (**67 archivos**). El proxy:
1. **Sanitiza la identidad entrante**: borra `cf-access-authenticated-user-email` y
   `x-acaquant-user-email` (spoofeables si alguien saltea Cloudflare, ej. la URL `*.vercel.app`) y setea
   solo el email verificado del sello firmado de CF.
2. **Agrega el bearer** `Authorization: Bearer API_KEY` + `CF-Access-Client-Id/Secret`, y propaga
   `x-acaquant-user-email` porque **CF Access estripa el header de email cuando el request entra con
   service token** — sin eso el audit no sabría quién mandó una orden.
3. **Marca `x-acaquant-portal: guest`** si `isGuestRequest()` (validando el `aud` del JWT contra
   `CF_ACCESS_AUD_GUEST`).
4. **Pre-gate por módulo** (`src/proxy.ts::PATH_MODULES` + `matcher`), con cache de `/api/me` en un `Map`
   en memoria, TTL 30s por `email` (o `guest:email`).

Variantes y trampas verificadas:
- **`dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store`** son obligatorios en las
  routes "live fallback"; sin `no-store` el edge cache de Vercel pisaba el poll de 5s (bug 2026-04-23 en
  `/api/argy` y `/api/futuros-dlr`).
- **`/api/cotizaciones/[...path]` es GET-only a propósito**, para no exponer el `PUT /opciones/tasa`.
  `/api/analitica` sí acepta POST (lo necesita `estrategia-historico`). `/api/scanner` es GET-only.
- **El proxy de SENEBIS pasa bytes crudos** (`arrayBuffer`) para que el `.xlsx` llegue intacto.
- **El proxy de Tesorería parsea TODA respuesta como JSON y mapea cualquier excepción a `502 {error}`** —
  por eso `export-txt` devuelve el contenido dentro de un JSON con 200 en vez de `text/plain`.
- **Los proxies de contrapartes/cashflow fuerzan `private, no-store`**: PII de clientes no va al CDN
  compartido de Vercel.
- **`maxDuration`**: 30s en `/api/news/article` y en los 4 de `/operar`; **90s** en `/api/manager` (los
  backfills largos se cortan ahí, por eso el front batchea con backoff ×3).
- **El matcher NO cubre todas las páginas**: gatea `/manager`, `/operaciones`, `/operadores`,
  `/referidos`, `/contrapartes`, `/operar`, `/aum`, `/valuaciones`, `/back-office`, `/research`,
  `/mesa-dinero`, `/renta-variable` (+ sus `/api/*`). **NO** cubre `/trading`, `/agro`, `/derivados`,
  `/sinteticos` ni `/`.
- **Un request SSR con `apiFetch` directo al `API_URL` no pasa por el matcher** (caso `/api/titulos/flujos`).
- **Fail-closed en las dos capas**: `layout.tsx` con `modules = []` en prod si `getMe()` falla; `proxy.ts`
  redirige a `/` o devuelve 502 en `/api/*` (el comentario "fail-open" del docstring quedó viejo).
- **Agregar un prefijo nuevo exige tocar DOS lugares**: el route handler catch-all **y** la entrada en
  `src/proxy.ts`; si falta una, la vista da 404/403 sin mensaje claro.

---

## 6. INVENTARIO DE ENDPOINTS

**423 rutas** montadas en `api.main.app` (medido sobre el árbol de dependencies ya armado).
La suma de las tablas por router de este documento da **424** — hay 1 de diferencia sin reconciliar
(el relevamiento de Manager declara 121 endpoints pero sus propias tablas suman 122; candidatos: el
`PATCH /assets/{unidad}` DEPRECATED o `/api/health`).

**≈120 endpoints escriben** (persisten o mandan órdenes reales), de los cuales **9 son de `/api/ingest`**
con token propio. Sin ingest y sin los de presencia, ~110.

Leyenda de la columna **W**: ✍ = persiste / manda orden real · ⚡ = POST pero **no persiste** (cálculo) ·
👁 = solo marca presencia · — = lectura pura.

### `me.py` — sin prefijo, sin `dependencies=`
| M | Path | W |
|---|---|---|
| GET | `/api/me` | — (toca `last_seen_at`) |

### `health` — `_PUBLIC`
| M | Path | W |
|---|---|---|
| GET | `/api/health` | — |

### `market.py` — `/api/market`, `_PUBLIC`
GET `/quotes` · GET `/eikon-news` · GET `/candle` · GET `/profile` — **4, ninguno escribe**

### `news.py` — `/api/news`, `_PUBLIC`
GET `/` · GET `/article` · GET `/stats` — **3, ninguno escribe**

### `cotizaciones.py` — `/api/cotizaciones`, `_PUBLIC` (33)
GET `/renta-fija` · `/snapshot-live` · `/forwards` · `/historico/forwards` · `/forwards-zscore` ·
`/breakevens` · `/historico/breakevens` · `/fair-value` · `/fair-value/cierre` · `/fair-value/historico` ·
`/historico/curva` · `/historico/trades` · `/rem` · `/rem/informes` · `/rem/breakeven-acumulado` ·
`/rem/debug` · `/cer` · `/badlar` · `/dolar` · `/mep` · `/historico/mep` · `/historico/dolares` ·
`/argy` · `/caucion` · `/historico/caucion` · `/futuros-dlr` · `/historico/futuros-dlr` · `/opciones` ·
`/opciones/meta` · `/historico/opciones` · `/vr-ggal` · `/griegas/opciones` — **32 GET, ninguno escribe**
| **PUT** | `/opciones/tasa` | **✍** (gate inline `manager`) |

### `analitica.py` — `/api/analitica`, `_PUBLIC` (11)
GET `/listar-curva` · `/serie-macro` · `/clasificar-nivel` ·
`/snapshot-curva-historico` · `/pendiente-curva` · `/sensibilidad-retorno` · `/canje` · `/carry-trade` ·
`/retorno-total` · `/simular-inversion`
— **10 GET, ninguno escribe**
| **POST** | `/estrategia-historico` | ⚡ |

### `titulos.py` — `/api/titulos`, `_PUBLIC`
GET `/assets` · GET `/flujos` — **2, ninguno escribe**

### `scanner.py` — `/api/scanner`, módulo `renta-variable` (9)
GET `/cedears` · `/ccl` · `/cedears/trades` · `/cedears/intraday` · `/returns/{ticker}` ·
`/quant/{ticker}` · `/pivot/{ticker}` · `/day-trading` **(admin)** · `/companeros/{ticker}` **(admin)**
— **ninguno escribe**

### `trading.py` — `/api/trading`, módulo `trading` (9)
GET `/pivots` · `/trades` · `/intraday` · `/renta-fija` · `/pivot-radar` · `/adr-zonas` · `/universo` ·
`/pnl-historico`
| **POST** | `/pnl-historico` | **✍** (upsert/DELETE en `valuaciones.pnl_historico`) |

### `estrategia.py` — `/api/estrategia`, módulo `trading` (4)
GET `/live` · `/track-record` · `/senales` · `/contexto` — **ninguno escribe**

### `derivados_agro.py` — `/api/derivados/agro`, `_PUBLIC` + gates por ruta (17)
GET `/agro` · `/agro/opciones/{commodity}` · `/agro/camara` · `/agro/camara-bahia` ·
`/agro/tasas-cobertura` · `/agro/dolares-referencia` · `/agro/costo-pase` · `/agro/descuento-caucion` ·
`/agro/mejoras-dispo` · `/agro/mejoras-dispo-bahia` · `/agro/chicago` — **11 GET**
| **PATCH** | `/agro/pizarra/{commodity}` | **✍** (módulo `agro` + `require_no_invitado`) — **sin proxy Next, huérfano** |
| **PATCH** | `/agro/camara/{cereal}` | **✍** |
| **PATCH** | `/agro/camara-bahia/{cereal}` | **✍** |
| **PATCH** | `/agro/tasas-cobertura` | **✍** |
| **PATCH** | `/agro/dolares-referencia` | **✍** |
| **POST** | `/agro/estrategia/simular` | ⚡ (única POST del router sin `require_no_invitado`) |

### `derivados_sinteticos.py` — `/api/derivados/sinteticos`, `_PUBLIC`
GET `/` — **1, no escribe**

### `operar.py` — `/api/operar`, módulo `operar` (3)
| GET | `/order-book` | ✍ efecto lateral (`adhoc_subscriptions`) |
| **POST** | `/bracket` | **✍** orden real + 4 tablas |
| GET | `/brackets/dia` | — (**sin consumidor**) |

### `ordenes.py` — `/api/ordenes`, módulo `operar` (8)
| **POST** | `/` | **✍ ORDEN REAL** (rate limit 30/min;400/h) |
| **DELETE** | `/{cl_ord_id}` | **✍ CANCELACIÓN REAL** (60/min;600/h) |
| GET | `/dia` · `/symbols` · `/fci/search` · `/fci/quote` · `/{cl_ord_id}` | — |
| **POST** | `/fci` | **✍ ORDEN REAL** (sin rate limit propio) |

### `operativa.py` — `/api/operativa`, módulo `operar` (6)
| GET | `/mep/cotizacion` · `/mep/timesales` · `/mep/dia` · `/mep/{id}/detalle` | — |
| **POST** | `/mep` | **✍ 2 ÓRDENES MARKET REALES** |
| **POST** | `/mep/venta` | **✍ 2 ÓRDENES MARKET REALES** |

### `risk.py` — `/api/risk`, módulo `operar` (5)
GET `/account/saldo` · `/account/report` · `/account/positions` (**sin consumidor**) ·
`/account/detailed` · `/account/listado` — **ninguno escribe**

### `operaciones.py` — `/api/operaciones`, módulo `operaciones` (44)
**Bloque ops/flujo (22)**: GET `/flujo` · `/flujo/resumen` · `/flujos` · `/flujos/resumen` ·
`/ops/mercados` · `/ops/carteras` · `/ops/fechas` · `/ops/meta` · `/ops/serie` · `/ops/resumen` ·
`/ops/agro` · `/ops/dolar-futuro` · `/ops/diferencias-diarias` · `/ops/diferencias-fechas` ·
`/ops/aranceles` · `/ops/cuentas-list` · `/ops/segmentos` · `/ops/niveles3` · `/ops/niveles5`
| **POST** | `/intraday/analizar` · `/intraday/recalcular` · `/intraday/marks` | ⚡ **cero persistencia** |

**Bloque comercial (22)**: GET `/comercial/operadores` · `/dimensiones` · `/operador` · `/serie` ·
`/clientes-por-fecha` · `/portafolio` · `/operaciones` · `/analisis` · `/analisis/detalle` ·
`/cobros-futuros` · `/cobros-futuros/cliente` · `/referido-clientes` · `/referido-fci` · `/informe` ·
`/informe-segmento` · `/informe-aranceles-segmento` · `/informe-segmento-detalle` ·
`/control/objetivos` **(control_comercial)** · `/control/totales` **(cc)** · `/control/por-operador`
**(cc)** · `/control/objetivos-vs-actual` **(cc)**
| **PATCH** | `/comercial/control/objetivos` | **✍** (`clientes.objetivos_comerciales`, gate `control_comercial`) |

### `cuentas.py` — `/api/cuentas`, módulo `operaciones` (2)
GET `/accionistas` · `/contrapartes` — **ninguno escribe**

### `mesa_dinero.py` — `/api/mesa-dinero`, **sin módulo**: allowlist per-usuario (9)
| GET | `/ops` · `/resumen` · `/resultados` · `/opciones` · `/retorno` | — |
| **POST** | `/ops` | **✍** (allowlist) |
| **PATCH** | `/ops/{op_id}` | **✍** (allowlist) |
| **DELETE** | `/ops/{op_id}` | **✍** (allowlist) |
| **PUT** | `/tc` | **✍** (allowlist) |

### `carteras.py` — `/api/portfolio`, módulo `portfolios` (12)
GET `/niveles` · `/carteras` · `/operadores` · `/aum` (**sin consumidor**) · `/pnl` · `/pnl-todas` · `/cuentas` ·
`/fci-serie` · `/fci-snapshot` · `/total-serie` · `/total-snapshot` · `/diff`
— **12, NINGUNO escribe** (el router no tiene un solo verbo de escritura)

### `valuaciones.py` — `/api/valuaciones`, módulo `portfolios` (7)
GET `/consolidado` · `/{id}/serie` · `/{id}/mensual` · `/{id}/movimientos` · `/{id}/variacion` ·
`/{id}/posiciones-actuales` · `/{id}/posiciones` (legacy, **sin consumidor**)
— **7, NINGUNO escribe**

### `back_office.py` — `/api/back-office`, módulo `back-office` (50)
**No-Tesorería (14)**: GET `/titulos-mercado` · `/acreencias/por-dia` · `/acreencias/dia` ·
`/acreencias/cliente` · `/tenencia-hd` · `/tenencia-hd/posiciones` · `/tenencia-hd/en-alquiler` ·
`/tenencia-hd/portfolio-alquiler` · `/portfolio-alquiler/posiciones` · `/portfolio-alquiler/instrumentos`
| **POST** | `/tenencia-hd/precio` | **✍** (sin allowlist) |
| **POST** | `/tenencia-hd/alquiler` | **✍** (sin allowlist) |
| **POST** | `/tenencia-hd/portfolio-alquiler` | **✍** (sin allowlist) |
| **POST** | `/tenencia-hd/portfolio-alquiler/nominal` | **✍** (sin allowlist) |

**Tesorería (36)** — todas las escrituras con `require_escritura_tesoreria`:
| M | Path | W |
|---|---|---|
| GET | `/tesoreria/dia` · `/detalle` · `/foto` · `/snapshots` · `/cheques` · `/cheques/comitentes` · `/cuentas` · `/entidades` · `/mercados` · `/banco-a-banco` · `/banco-a-banco/export-txt` · `/registros` | 👁 (varios marcan presencia) |
| POST | `/snapshots` | **✍** |
| PUT | `/exclusion` · `/saldo-inicial` | **✍** |
| POST/PUT/PUT-estado/DELETE | `/cheques` (4) | **✍** |
| POST/PUT | `/cuentas` (2) | **✍** |
| POST/PUT/DELETE | `/entidades` (3) | **✍** |
| POST/PUT/PUT-estado/DELETE | `/mercados` (4) | **✍** |
| POST/PUT/PUT-estado/DELETE | `/banco-a-banco` (4) | **✍** |
| POST/PUT/DELETE | `/registros` (3) | **✍** |
| PUT | `/registros-saldo` | **✍** |

### `senebis.py` — `/api/back-office/senebis`, módulo `back-office` (17)
| M | Path | W |
|---|---|---|
| GET | `/ops` · `/opciones` · `/excel` · `/excel-mae` | 👁 presencia |
| GET | `/comitentes` · `/export` · `/export-mae` | — |
| POST | `/presencia` | 👁 |
| POST | `/ops` · PATCH `/ops/{id}` · DELETE `/ops/{id}` | **✍** (allowlist `senebis_escritores`) |
| POST | `/ops/{id}/estado` · `/ops/{id}/visto` · `/ops/{id}/reasignar-id` | **✍** (todo el módulo, sin allowlist) |
| POST | `/proximo-id` | **✍** (`require_admin`) |
| PUT | `/agentes` · DELETE `/agentes/{nombre}` | **✍** (todo el módulo) |

### `research1816.py` — `/api/research1816`, módulo `research` (11)
GET `/mails` · `/mails/buscar` (**sin consumidor front**) · `/universo` · `/series` · `/spread` ·
`/reuters` · `/reuters/fundamentals` · `/reuters/fundamentals/agregado` · `/reuters/ficha` ·
`/reuters/segmentos` · `/reuters/segmentos/agregado` — **ninguno escribe**

### `research_bcra.py` (2) · `research_fred.py` (2) · `research_docs.py` (2) — módulo `research`
GET `/research-bcra/bloques` · `/research-bcra/series` · `/research-fred/bloques` ·
`/research-fred/series` · `/research-docs/list` · `/research-docs/{id}/pdf` — **ninguno escribe**

### `ia.py` — `/api/ia`, módulo `ia` (1)
| M | Path | W |
|---|---|---|
| GET | `/briefing` | — |

Los del copiloto se fueron con él (2026-08-19); los cinco de observabilidad y
presupuesto, el 2026-08-28 (ver §4.11).

### `manager/` — `/api/manager` (121 declarados / 122 por suma de tablas)
| Sub-router | Gate | Endpoints | Escriben |
|---|---|---|---|
| `status` | `manager` | GET `/status` (**sin consumidor**) | — |
| `latencia` | `manager` | GET `/latencia` | — |
| `controles` | `manager` | GET `/controles` | — |
| `diagnostico` | `manager` | GET `/diagnostico` · `/db-observabilidad` | — |
| `checks` | `manager` | GET `/checks/{debug-comercial*, tasa-fija, tickers-curvas, debug-soberano, breakevens-debug, futuros-dlr*, debug-tna-futuros, debug-curva-tea, debug-pivot}` (*sin consumidor) | — |
| `jobs` | `manager` | **POST `/jobs/run`** ✍ (whitelist de 5 tipos, rate limit 5/h;20/d) · GET `/jobs/catalogo`, `/jobs/history`, `/jobs/history/stats`, `/jobs/{job_id}` | 1 |
| `options` | `manager` | GET `/options/expiries` · **PUT `/options/expiries`** ✍ | 1 |
| `logs` | `manager` | GET `/logs/services` · `/logs` | — |
| `users` | `manager` | GET `/users` · **POST `/users`** ✍ · **PATCH `/users/{email}`** ✍ · **DELETE `/users/{email}`** ✍ | 3 |
| `roles` | `manager` | GET `/roles` · **PATCH `/roles/{role}`** ✍ · GET `/roles/audit` | 1 |
| `grupos` | `manager` | GET `/grupos` · **POST** ✍ · **PATCH `/grupos/{id}`** ✍ · **DELETE `/grupos/{id}`** ✍ | 3 |
| `aunesa` | `manager` | GET `/aunesa/explorar`, `/aunesa/posicion`, `/aunesa/boletos/faltantes`, `/aunesa/boletos/backfill/{job_id}`, `/aunesa/boletos/backfill` · **POST `/aunesa/boletos/backfill`** ✍ | 1 |
| `valuaciones` | `manager` | GET `/valuaciones/debug` · `/aum` | — |
| `operaciones` | `manager` | **POST `/operaciones/backfill`** ✍ (**sin preview**) · **POST `/operaciones/faltantes`** ✍ · **POST `/operaciones/fechas`** ✍ · GET `/operaciones/stats` · **POST `/operaciones/anulados`** ✍ · GET `/operaciones/anulados/resumen` · **POST `/operaciones/anulados/barrido`** ✍ | 5 |
| `documentos` | `manager` | GET `/documentos` · **POST** ✍ · **DELETE `/documentos/{id}`** ✍ | 2 |
| `mesa` | `manager` | GET `/mesa/traders` · **POST/DELETE `/mesa/traders`** ✍✍ · GET `/mesa/escritores` + `/candidatos` · **POST/DELETE** ✍✍ · idem `senebis-escritores` (4) · idem `tesoreria-escritores` (4) | 8 |
| `import_tenencia` | `manager` ∨ `manager_aunesa` | **POST `/import-precios-sql`** ✍ · **POST `/import-aum-sql`** ✍ · **POST `/recalcular-valuacion-sql`** ✍ | 3 |
| `clientes.router` | `manager` ∨ `manager_clientes` | GET `/clientes` · `/clientes/values` · `/clientes/sin-operador` · **PATCH `/clientes`** ✍ | 1 |
| `clientes.bulk_router` | `manager` ∨ `manager_clientes_bulk` | **POST `/clientes/bulk`** ✍ · **POST `/clientes/bulk-fondeo`** ✍ · **POST `/clientes/recalcular-niveles`** ✍ | 3 |
| `aca_valores` | `manager` ∨ `manager_clientes` | GET `/aca-valores` · `/aca-valores/candidatos` · **POST** ✍ · **DELETE** ✍ | 2 |
| `control_automatico` | `manager` ∨ `manager_clientes` | POST `/control-automatico/reconciliar` (solo lee) · **POST `/control-automatico/segmentar`** ✍ | 1 |
| `assets` | `manager` ∨ `manager_titulos` | GET `/assets` · `/assets/gaps` · `/assets/values` · **PATCH `/assets`** ✍ · **PATCH `/assets/{unidad}`** ✍ (DEPRECATED) | 2 |
| `ons` | `manager` ∨ `manager_titulos` | GET `/ons`, `/ons/values`, `/ons/conciliar`, `/ons/ignoradas` · **POST `/ons`** ✍ · **PATCH `/ons/sector`** ✍ (**el front no lo llama**) · **DELETE `/ons`** ✍ · POST `/ons/parse-flujos` · **POST `/ons/ignorar`** ✍ · **DELETE `/ons/ignorar`** ✍ | 5 |
| `bonos` | `manager` ∨ `manager_titulos` | GET `/bonos`, `/bonos/sin-flujo`, `/bonos/sin-tasa` · POST `/bonos/parse-flujos` · **POST `/bonos`** ✍ · **DELETE `/bonos`** ✍ | 2 |
| `breakevens` | `manager` ∨ `manager_titulos` | GET `/breakevens/pares` · **POST `/breakevens/exclusion`** ✍ | 1 |
| `renta_variable` | `manager` ∨ `manager_titulos` | GET `/renta-variable` · `/renta-variable/rubros` · **POST `/renta-variable/rubro`** ✍ · **PATCH `/renta-variable`** ✍ · **DELETE `/renta-variable`** ✍ | 3 |
| `instrumentos` | + `manager_instrumentos` | GET `/checks/discovery-pyrofex` · `/checks/instruments-by-cfi` | — |
| `contrapartes` | `manager` ∨ `manager_contrapartes` | GET `/contrapartes` · `/contrapartes/segmentos` · `/contrapartes/reconcile` · **PATCH `/contrapartes`** ✍ · **POST `/contrapartes`** ✍ · **POST `/contrapartes/import`** ✍ | 3 |

**Total de escrituras en Manager: 51.**

### `ingest.py` — `/api/ingest`, gate propio `X-Ingest-Token` (13: 9 POST + 4 GET)
| M | Path | W |
|---|---|---|
| GET | `/ingest/eikon/universo` | — |
| GET | `/ingest/eikon/chicago/universo` | — |
| POST | `/ingest/eikon/rics` | **✍** |
| POST | `/ingest/eikon/quotes` | **✍** (`mercado.eikon_snapshot`) |
| POST | `/ingest/eikon/fundamentals` | **✍** (`mercado.eikon_fundamentals`) |
| POST | `/ingest/eikon/segmentos` | **✍** (`mercado.eikon_segmentos`) |
| POST | `/ingest/eikon/chicago/quotes` | **✍** (`mercado.eikon_chicago_snapshot`) |
| POST | `/ingest/eikon/news` | **✍** (`mercado.eikon_news`) |
| … | resto (dólar oficial MAE, bonos/cierres Eikon) | **✍** — **SIN VERIFICAR**: el árbol de rutas cuenta 9 POST + 4 GET; el relevamiento nominó 8 |

---

## 7. HUECOS Y RAREZAS

Ordenados por qué tan accionables son. Lo que dice **SIN VERIFICAR** no se pudo cerrar leyendo el repo.

### 7.1 Seguridad / RBAC — la matriz miente en varios lugares

1. **5 módulos NO tienen gate server-side**: `home`, `renta-fija`, `derivados`, `sinteticos`,
   `estrategia`. Sacarlos de un rol solo esconde el link; con el bearer del frontend la data sigue
   accesible (`/api/market`, `/api/news`, `/api/cotizaciones` (33), `/api/analitica` (15),
   `/api/titulos` (2), `/api/derivados/*` GET, `/api/derivados/sinteticos`). Está declarado como decisión
   en `api/auth.py:289-293`, pero **la matriz del panel promete algo que el backend no cumple**.
2. **`manager_comercial` no existe** pero está en el gate base de `/api/manager/*` (`api/main.py:342-344`).
   `has_access` lo trata fail-closed y **loguea `has_access: módulo desconocido` en CADA request** de un
   rol sin `manager`. Rama muerta + ruido en los logs. Mismo string stale en 3 docstrings.
3. **El gate base de `/api/manager` no cubre 4 de los 6 sub-módulos** (`manager_titulos`,
   `manager_instrumentos`, `manager_contrapartes`, `manager_aunesa`). Un rol con SOLO uno de esos recibe
   **403 en la base** aunque el sub-router lo habilite. `asistente_comercial` se salva de casualidad.
4. **Tres listas desincronizadas para el gate de MANAGER en el front**: `header.tsx::MANAGER_MODULES` (3),
   `app/manager/page.tsx::MANAGER_MODULES` y `proxy.ts::PATH_MODULES["/manager"]` (4), y
   `manager-view.tsx::TAB_MODULES`. Efecto real: un rol con solo `manager_titulos` **entra por URL y ve la
   tab pero no ve el link**; uno con solo `manager_contrapartes`/`manager_aunesa` **recibe 404 de la
   página** aunque el backend lo autorice.
5. **Front y back discrepan en `/api/titulos`**: el backend lo movió a `_PUBLIC` a propósito (con
   `_PORTFOLIOS`, rol `sales` daba 403 y `/renta-fija` quedaba en "MERCADO CERRADO"), pero
   `src/proxy.ts:47` sigue exigiendo `portfolios` y el matcher lo cubre → **para `sales`, `back_office` e
   `invitado` el proxy devolvería 403 antes de llegar al backend**. Hoy no se dispara porque la vista lo
   pide en SSR directo al `API_URL`, salteando el matcher.
6. **`ENDPOINT_MODULE_PREFIXES` no enforcea nada** (`api/auth.py:294-332`): su único consumidor es
   `tests/unit/test_rbac.py`. Es documentación que puede divergir del enforcement real. Contiene
   `("/api/mm","mm")` con un módulo **inexistente** y sin router montado, y le faltan prefijos reales:
   `/api/scanner`, `/api/research*`, `/api/estrategia`, `/api/valuaciones`, `/api/derivados/agro`,
   `/api/back-office/senebis`.
7. **La tooling de auditoría RBAC está CIEGA en este checkout**: `scripts/audit_rbac.py` imprime **5 rutas
   de 423** y `tests/unit/test_rbac_superficie.py` da **9 failed / 10 passed**, donde los invariantes de
   seguridad ("ninguna ruta del negocio llega al invitado", "toda ruta `/api/*` exige bearer") pasan
   **vacíos** porque no ven ninguna ruta. Causa: **FastAPI 0.141.1** instalado materializa los
   `include_router` como `_IncludedRouter` lazy y ambos consumidores iteran `app.routes` esperando
   `APIRoute`; `requirements.txt:21` pinea `fastapi==0.136.1`. **SIN VERIFICAR** si con el pin funciona en
   el Droplet/CI. **Un guardrail que falla en verde es peor que no tenerlo.**
8. **`/api/me` no exige bearer** (`api/main.py:290`): es el único `/api/*` sin `verify_api_key` además de
   `/api/health`, y devuelve rol + módulos + `control_comercial` de quien llame. Sigue detrás de CF
   Access, pero es superficie de recon.
9. **Fail-opens declarados** (decisiones, no bugs, pero conviene tenerlos a la vista):
   `core/grupos.py:53-75` (scope de cuentas ante error de DB → **ve TODO**), `api/deps.py` (sin `API_KEY`
   pasa todo; mitigado por `_validar_postura_auth` en prod), `api/auth.py:218-223` (con
   `CF_TRUSTED_SERVICE_TOKENS` vacío se acepta el email forwardeado de cualquier service token válido).
10. **`docs/SECURITY.md` está desactualizado**: lista `_ASISTENTE | chat` (router eliminado), da `_OPERAR`
    a "admin, trader, (sales)" cuando el default es admin-only, **no menciona** `back-office`, `research`,
    `ia`, `trading`, `agro`, `asistente` ni los 6 sub-módulos de manager, y dice "32 tools del MCP" contra
    las 13 del `CLAUDE.md`.
11. **`GET /api/operaciones/flujo` y `/flujo/resumen` (Contrapartes) NO reciben `scope` de grupos**, a
    diferencia de `/flujos`. **SIN VERIFICAR** si es consciente (las contrapartes son entidades de
    mercado) o un hueco.
12. **El invitado puede leer los GET de la tab DATOS de Agro**: el filtro que la esconde es
    **solo del frontend**; el backend los sirve porque están bajo `/api/derivados`, permitido en
    `GUEST_PATH_PREFIXES`. Las escrituras sí están cortadas por `require_no_invitado`.
13. **El proxy de Next no gatea todas las páginas**: `/trading` (admin-only), `/agro`, `/derivados`,
    `/ons`, `/sinteticos`, `/retorno` y `/` se sirven a cualquiera con URL directa (los datos igual dan
    403 donde hay gate). Decisión explícita ("las rutas públicas no pagan el fetch"), pero `/trading`
    queda del lado permisivo.

### 7.2 Riesgos operativos verificados (plata real)

14. **Mismatch de namespace de cuenta en OPERAR**: el scope de grupos guarda el `id_cuenta` crudo, pero
    `ordenes_live` persiste el `account` ya resuelto por `resolver_cuenta_rofex` (`100` → `0100`). En
    `DELETE /ordenes/{id}` y `GET /ordenes/{id}` el scope se verifica contra el resuelto → **un usuario
    scopeado podría recibir 403 al cancelar su propia orden**. **SIN VERIFICAR** cuántas cuentas de prod
    resuelven distinto.
15. **`create_bracket` persiste `account` CRUDO** mientras la entrada se mandó con la cuenta resuelta →
    si esa cuenta necesitaba `zfill`, **la salida automática puede ser rechazada quedando la posición
    abierta** (`EXIT_REJECTED`, "para intervención manual"). **SIN VERIFICAR** si ocurre en prod.
16. **`send_fci_order` NO llama a `resolver_cuenta_rofex`** (a diferencia de `send_order`) →
    inconsistencia verificada: una cuenta con nº ROFEX distinto funcionaría para títulos y fallaría para
    FCI.
17. **No hay ningún límite de tamaño/notional/fat-finger en el backend de órdenes.** Las únicas
    validaciones son `size>0`, LIMIT requiere `price`, `comision_pct ∈ [0,5]`, `monto>0`, `nominales>0`.
    El único control cuantitativo es el rate limit; el `confirm()` del navegador existe **solo en MEP**,
    no en títulos ni FCI.
18. **La idempotencia degrada hacia "mandar"**: ante cualquier error de infra `reservar()` devuelve
    `True` y la orden se manda. Es defensa contra duplicados accidentales, no contra tragar una orden.
19. **Los docstrings de `risk.py` prometen un cache que NO existe** ("Cache 3s/5s", "pegale todo lo que
    quieras, no satura al broker"): **no hay `@cached`**. Cada usuario con la vista abierta hace 2 hits a
    `get_account_report` cada 8s.
20. **`editar_cuenta` de Tesorería (renombrar banco) NO arrastra `tesoreria_banco_a_banco` ni
    `tesoreria_registros`** (sí arrastra saldos, cheques y mercados). Esas dos tablas también referencian
    el banco por nombre → renombrar dejaría filas huérfanas. **SIN VERIFICAR** si ya pasó en prod.
21. **`POST /api/manager/operaciones/backfill` pisa directo sin preview**, siendo la única excepción al
    patrón preview→commit del panel.
22. **`POST /api/back-office/tenencia-hd/precio` y las 3 de alquiler NO tienen allowlist**: cualquier rol
    con el módulo `back-office` (que incluye `sales` en el default) puede **editar a mano el PRECIO de una
    unidad** y recalcular la valuación de las 3 cuentas propias.
23. **`POST /api/trading/pnl-historico` tampoco tiene allowlist** (el gate es el módulo `trading`), y
    dejar la celda vacía **BORRA** la fila.
24. **`PATCH /api/cotizaciones/opciones/tasa` cambia la tasa risk-free GLOBAL** de toda la mesa (afecta
    los Greeks de todos). Está admin-only y el proxy es GET-only, así que hoy **solo se puede llamar por
    fuera del front**.

### 7.3 Funcionalidad construida que NO está expuesta en la UI

25. **Toda la vista de la ESTRATEGIA QUANT**: `GET /api/estrategia/live`, `/track-record` y `/senales`
    existen (con hit rate, expectativa, MFE/MAE, curva de equity, `edge_factores` y el ledger auditable)
    y **ningún componente los consume** — la tab ESTRATEGIA del radar usa solo `/contexto`. Hay un motor
    (`engines/estrategia.py`), un resolver, 4 tablas y config, sin pantalla.
26. **Trade Lab / scalping**: `GET /api/scanner/day-trading` (ranking intradía con vueltas zigzag,
    momentum, flujo comprador, `idea{lado,motivo}`) y `/companeros/{ticker}` (correlación) son admin-only
    y **no tienen consumidor**: la vista `/trade-lab` que los alimentaba **no existe en el repo**.
27. **Triage de incidentes con IA**: `jobs/triage.py` corre cada 10 min, diagnostica jobs fallidos con
    LLM y persiste `{causa, hecho, hipotesis, recomendacion, confianza}` en `ia.triage_incidentes`.
    **No hay endpoint ni tab**: la lectura hoy es SQL directo. Está declarado como pendiente.
28. **Control de calidad de conversaciones de IA**: `ia.calidad_flags` se llena todas las noches y
    **tampoco tiene vista**.
29. **Tablero de brackets**: `GET /api/operar/brackets/dia` existe pero no hay pantalla; los brackets se
    crean y después solo se ven las órdenes sueltas.
30. **Endpoints huérfanos sin consumidor en el front** — ⚠️ **relevado de nuevo el 2026-08-31
    contra la app montada**, porque la lista anterior tenía ocho entradas que YA NO EXISTEN
    (una lista de huérfanos que envejece se convierte en la propia basura que denuncia).
    - **Se dieron de baja desde aquel relevamiento** (ya no hay nada que borrar):
      `/api/market/candle`, `/api/market/profile`, `/api/scanner/cedears/trades`,
      `/api/scanner/cedears/intraday`, `/api/trading/renta-fija`, `/api/portfolio/aum`,
      `/api/research1816/mails/buscar`, `/api/manager/checks/{debug-comercial,futuros-dlr}`.
    - **Siguen vivos y siguen sin consumidor**: `/api/risk/account/positions`,
      `/api/titulos/assets`, `/api/valuaciones/{id}/posiciones` (legacy, con proxy y todo),
      `/api/manager/status`.
31. **`PATCH /api/derivados/agro/pizarra/{commodity}` es huérfano en la práctica**: funciona en el
    backend, **no hay proxy Next**, y `PizarraRow` es read-only con tooltip "Editable en la tab Datos".
    El parámetro `canEdit` se pasa hasta el componente y no se usa.
32. **`PATCH /api/manager/ons/sector`** sigue vivo pero **el front no lo llama** (el sector va dentro del
    `POST /ons`).
33. **`valuacion_mensual_debug`** existe en el service (audita `flujos_detalle` + el cashflow exacto del
    XIRR, reproducible con TIR.NO.PER) y **no está expuesto** por ningún router.
34. **El dato del CANJE** sigue vivo en `/api/analitica/canje` pero **el recuadro se quitó de la HOME** el
    2026-07-24 — hoy no tiene UI.
35. **`GET /senebis/ops` acepta `especie` (ILIKE contiene)** y **no hay control en la UI** que lo emita.
36. **`GET /api/manager/latencia` acepta `top` (1–200)** y la UI no lo expone.
37. **`GET /api/news` soporta `desde`/`hasta`/`fuente`/`keyword`/`skip`** y el panel de HOME **no expone
    ninguno** (solo categoría; el filtro de fuente es client-side sobre las 150 que ya bajaron).
38. **`jobs/snapshot_sinteticos` materializa `mercado.snapshots_sinteticos` todos los días** y **la vista
    `/sinteticos` no lee ese histórico**, solo el live.

### 7.4 Inconsistencias de cálculo / semántica (pueden confundir al usuario)

39. **"PNL TOTAL" significa dos cosas distintas dentro de la MISMA vista `/valuaciones`**: en PNL TÍTULOS
    la UI calcula `no_realizado + pasivo` y **excluye el realizado** a propósito; en TOTALES suma
    `no_realizado + pasivo + realizado_dia`.
40. **Bonos Off Shore: `argy.py` sí ancla contra `mercado.eikon_cierres` pero `briefing.py` manda
    `ret_wtd`/`ret_mtd` hardcodeados a `None`** → el mismo dato existe en una superficie y no en la otra.
41. **El front usa `30/90` como fallback de los umbrales de estado comercial, cuando el default real del
    backend es `45/90`.** En la práctica el backend siempre los devuelve, pero la constante está mal.
42. **`sensibilidad-retorno` y `simular-inversion` devuelven `{"error": …}` con HTTP 200** ante input
    inválido; el front chequea `j.error` a mano en vez de confiar en el status.
43. **Los pivots se calculan dos veces** (backend en `/pivots`, frontend en `calcPivots` al editar
    máx/mín/cierre). Si las fórmulas divergieran, el chart mostraría líneas distintas a las cards.
44. **Breakevens filtra a 2026 hardcodeado en el frontend** (el backend devuelve todos): cambiar de año
    exige tocar el componente.
45. **El front hardcodea `curva=soberanos`** en RESEARCH → ANÁLISIS SENSIBILIDAD aunque el endpoint
    acepta el parámetro.
46. **La barra de DÓLAR MEP dice "refresca cada 2s"** y los intervalos reales son 5s y 60s.
47. **La fila `dispo` del Pase Agro devuelve `#N/A` en las 4 columnas numéricas** por hardcodeo del
    frontend — no es un problema de datos.
48. **Dos de los tres "Dólares de Referencia" de Agro son automáticos** pero el PATCH acepta los tres:
    escribir `dolar_matba` o `bna_comprador_t1` queda tapado en la próxima lectura.
49. **`snapshot-live` pierde los timestamps individuales** de sus 3 bloques: la UI muestra un solo
    "actualizado a las HH:MM:SS" aunque forwards y breakevens vengan de un cache de 30s.
50. **`/ons` solo muestra ONs con volumen operado HOY**: una ON del maestro sin trades del día desaparece
    de la tabla y del scatter (aunque puede seguir en el CALENDARIO).
51. **El scatter de ONs esconde outliers** (mediana ± 5·MAD) — se avisa al pie, pero un dato roto queda
    invisible en el gráfico.
52. **El proxy de Contrapartes baja 2 años fijos** y los inputs Desde/Hasta filtran **client-side** dentro
    de lo que ya bajó: no hay control real del usuario sobre la ventana.
53. **`Directo` y `DEVA` de FUTUROS ROFEX se calculan en el cliente**, y `DEVA` compara contra la fila
    anterior de la tabla ya ordenada → la primera fila siempre da `—`.
54. **El grupo "Monedas" de la watchlist es código muerto**: el mapeo existe pero `SUBGRUPOS_GLOBALES` no
    lo incluye.

### 7.5 Deuda / residuos

55. **`operaciones.triggers_mep` es una tabla huérfana** y `docs/API.md` documenta 3 endpoints y un
    scanner asyncio que **no existen** en el código.
56. **`api/services/_cuentas_filter.py` sigue devolviendo sub-docs `$match` de Mongo** y su docstring
    habla de `Cuentas.AccionistasAPI`; ninguna vista de operaciones lo usa.
57. **`import_tenencia.py` conserva `_Row`/`_ImportReq` sin usar** (residuo del endpoint que escribía la
    colección Mongo `Valuaciones.AuM`).
58. **`operaciones.accounts_descubiertas`** es vestigial (el job que la llenaba fue eliminado).
59. **Residuos de Mongo** en comentarios/código: `market_sql._fix_tz`, el docstring "flag NEWS_SQL",
    `valuaciones.py:151` ("Mongo stores fechas como strings"), y los selectores `_motor()`/`_engine` y
    flags `*_SQL` del Tablero Comercial.
60. **`mercado.breakevens_overrides` se crea con `CREATE TABLE IF NOT EXISTS` en runtime** desde el
    service: no depende de `sql/schema.sql`.
61. ~~**`PATCH /api/manager/assets/{unidad}` está marcado DEPRECATED**~~ — **ya no existe**
    (verificado 2026-08-31): quedó solo `PATCH /api/manager/assets`, que era el que delegaba.
    Rompía con caracteres especiales URL-encoded.
62. **Los docstrings de los routers de Research dicen "JAMÁS invitado"** cuando
    `DEFAULT_MATRIX["invitado"]` **sí** incluye `research` desde el 2026-07-21. (Son 4:
    `research1816`, `research_bcra`, `research_fred`, `research_docs`.)
63. **`trading-view.tsx:672` sigue consumiendo `/api/research1816/reuters`** desde la vista Trading, así
    que un usuario con `trading` y sin `research` ve esos KPIs vacíos.
64. **El estado de `POST /jobs/run` vive en un dict in-process** → se pierde al reiniciar `api.service`.
65. **`/back-office` abre en la 2ª tab**: la barra pinta `Senebis` primero pero el default persistido es
    `tenencia`.
66. **Los dropdowns del nav son 100 % CSS** (`group-hover`, sin estado React) → sin cierre con Esc ni
    navegación por teclado.
67. **`/trade-lab` se menciona en comentarios del código como "vista propia admin-only"** y **no existe en
    el repo**.

### 7.6 Contradicciones entre relevadores (declaradas, no resueltas)

- **Conteo de endpoints de Manager**: el relevamiento declara **121** pero la suma de sus propias tablas
  por sub-router da **122**. Con 121, el total de la app cierra exactamente en las **423** rutas medidas
  sobre `api.main.app`; con 122 daría 424.
- **Conteo de endpoints del dominio Operaciones**: el relevador escribe "37 endpoints documentados" y
  acto seguido detalla "20 + 21 + 2 + 6" (= 49); el conteo real de sus tablas es **44 en
  `operaciones.py` + 2 en `cuentas.py` + 6 en el ABM de contrapartes de Manager**. La cifra "37" es
  errónea.
- **Roles con `ia`**: verificado contra `core/roles.py::DEFAULT_MATRIX` (2026-08-30) — lo tienen
  **`admin` e `invitado`**; `trader` y `sales` NO. La duda se apoyaba en `docs/QUANTAI.md`, borrado, que ya no
  existe, así que se resolvió contra el código. Lo que sigue sin poder verificarse desde el repo es si
  `manager.role_matrix` (la matriz editable en la base) pisa ese default.
- **Gate del portal invitado sobre `research`**: docstrings de los routers vs. `DEFAULT_MATRIX` (§7.5.62).
- **Gate declarado de `risk.py`/`operativa.py`**: sus docstrings dicen módulo `operaciones`; el montaje
  real es `_OPERAR`.
