# FCI — FONDOS COMUNES DE INVERSIÓN (vista `/fci`) **[VIVO]**

> **Un dominio, un doc.** Qué muestra la vista, de dónde sale cada dato, cómo se
> modela un fondo y cómo se calculan los rendimientos. Parte A describe el
> sistema como es; parte B es el changelog (obligatorio en el mismo commit que
> toque el dominio). Superficie (endpoints, gate, nav): `docs/MAPA_APP.md` §4.13.

---

# PARTE A — El sistema

## 1. Resumen ejecutivo

📋 **Qué es:** el mercado de fondos comunes de inversión de la sección MERCADOS:
los fondos **de las sociedades gerentes con las que opera la mesa** (y solo
esos), con su valor de cuotaparte (VCP) diario y los rendimientos con que se
comparan: 1D · WTD · MTD · YTD · 7D · 30D · 90D · 365D y la TNA «según 30D».
Reemplaza al Excel «Informe FCI Semanal» que se armaba a mano.

📋 **De dónde sale:** de **Primary** (el supermercado de fondos: 776 cuotapartes
con `cficode CIO…`, medido 2026-09-10) y de **Manager** (`portafolio.assets`,
lo que la ALyC tiene, bilaterales incluidos). Primary da el VCP del día pero
**no guarda histórico** (trade history vacío en todos): la serie la construimos
nosotros, un día por corrida, y la completamos con el `precio` de la tenencia
de Aunesa, que sí tiene historia. CAFCI quedó descartado (403 desde el Droplet).

📋 **El modelo en una línea:** una fila por fondo en `mercado.fci`, con su
símbolo de Primary y/o su unidad de Manager; la lista de gerentes de la mesa en
`mercado.fci_gerentes` como **filtro duro**; la serie en `mercado.fci_vcp` con
tres fuentes en orden de prioridad (primary > tenencia > manual). Los
rendimientos se calculan **en la lectura** desde la serie. Nada se precalcula.

## 2. Las tres reglas que no se negocian

1. **No hay fondos de gerentes con las que no operamos.** `fci_gerentes` se
   siembra con los nombres que la mesa ya usa (`clientes.contrapartes` segmento
   Fondos + `assets.emisor` de la cartera FCI) y cada gerente tiene alias de
   cómo empiezan sus fondos en Primary (TORONTO → «Toronto Trust», MARIVA →
   «MAF», ONE618 → «Consultatio»). Un fondo de Primary que no matchea ninguna
   gerente seguida **no entra a `mercado.fci`**; una gerente con `seguida=false`
   saca sus fondos de la vista. Se edita con `scripts/fci_admin gerente`.
2. **El símbolo de Primary vive en `assets.instrumento`.** Es el campo que ya
   existe para eso (es por donde se suscribe la market data) y es editable en
   Manager → TÍTULOS → ASSETS. `jobs/fci_universo` lo completa solo cuando el
   match por nombre normalizado es **único** y nunca pisa lo cargado; los
   ambiguos se listan en el log para que la mesa los cierre a mano. A partir de
   ahí el link asset ↔ fondo es por símbolo, no por texto (REGLA #9).
3. **Una convención de rendimientos**, la misma que el watchlist de HOME (§4).

## 3. Modelo SQL (schema `mercado`)

| Tabla | Grano | Quién escribe | Qué es |
|---|---|---|---|
| `fci_gerentes` | `gerente` (el nombre de la MESA: SCHRODER, TORONTO, IAM) | `fci_universo` inserta las que faltan; la mesa edita `alias` y `seguida` | El filtro duro del universo. |
| `fci` | `fci_id` | `fci_universo` (Primary + assets); `fci_admin alta` (bilateral manual) | El objeto: nombre, gerente, `simbolo_primary` (UNIQUE), `unidad` (UNIQUE, = assets), `cafci`, moneda, `tipo_renta` (el `underlying` de Primary), `plazo` (settlType 1/2/3/4 → T+0/1/2/3), `categoria` (el estante; la mesa manda, el job solo sugiere donde está vacío), `origen`, `activo`. |
| `fci_vcp` | `(fci_id, fecha)` | `fci_vcp` (primary, tenencia); `fci_admin vcp` (manual) | La serie. `fuente` con prioridad primary > tenencia > manual: el upsert no deja que una fuente débil pise una fuerte. |

Identidad con Manager: `assets.cafci` = `CAFCI<fondo>-<clase>` (ids de CAFCI) y
`assets.unidad` viajan a la fila del fondo; el join tenencia ↔ mercado queda
listo para «cuánto tenemos en cada fondo de la tabla».

## 4. Los rendimientos — UNA convención (`api/services/fci_sql.py`)

`r = vcp_hoy / vcp_ancla − 1`, con `vcp_hoy` el último VCP del fondo (`fecha`) y
`vcp_ancla` el último VCP **en o antes** de la fecha ancla. Igual que
`jobs/market_anchors.py`.

| Ventana | Ancla | Ejemplo con `fecha` = lunes 07/09 |
|---|---|---|
| **1D** | el VCP anterior al último | viernes 04/09 |
| **WTD** | el último VCP **antes del lunes** de la semana | viernes 04/09 (el lunes WTD = 1D, es correcto) |
| **MTD** | el último VCP antes del 1° del mes | 31/08 |
| **YTD** | el último VCP antes del 1° de enero | 31/12/2025 |
| **7D / 30D / 90D / 365D** | el último VCP en o antes de `fecha − N` días corridos | 31/08 · 08/08 · 09/06 · 07/09/2025 |
| **TNA 7D / 30D** | `r × 365 / N` (la fórmula del Excel: `=+G13/30*365`) | — |

Fracciones (0,0123 = 1,23 %). Sin ancla (serie corta) → `null` → «—», nunca 0.
Un fondo en USD rinde en USD: no se convierte, se compara dentro de su estante.
`tabla()` y `ficha()` cachean 120 s; el front pollea cada 5 min.

## 5. Jobs y herramienta

| Qué | Cuándo (UTC, L-V) | Qué hace |
|---|---|---|
| `jobs.fci_universo` | 12:20 (después de `discovery_pyrofex`) | (1) asegura gerentes; (2) **una** llamada `get_detailed_instruments` → upsert de los CIO de gerentes seguidas por `simbolo_primary`; lo que Primary dejó de listar pasa a `activo=false`; (3) assets FCI: con `instrumento` → link; sin él → match único por nombre → escribe `assets.instrumento` y linkea; si no → fila propia (bilateral) con `gerente = assets.emisor`; (4) sugiere `categoria` donde está vacía (tipo de renta + plazo + moneda). |
| `jobs.fci_vcp` | 20:30 (fuera de rueda) | Por fondo con símbolo: `get_market_data(LA)` → punto `primary` **en la fecha del timestamp del LA** (si Primary no publicó, el LA es el de ayer y va a ayer). Después, tenencia de los últimos 3 días para los linkeados → `tenencia` donde no hay `primary`. ~300 requests REST throttleadas. |
| `jobs.fci_vcp --backfill-tenencia --desde` | a mano, fuera de rueda, vía `run_job.sh` | La historia del `precio` de la tenencia para los fondos linkeados: scopeado por unidad sobre el índice `(fecha, unidad)`, **por mes** con sleep, idempotente. `--medir` muestra el EXPLAIN y cuenta filas del primer mes antes de escribir (REGLA #4). |
| `scripts.fci_admin` | a demanda | `gerentes` · `gerente X --alias … / --seguir / --dejar` · `fondos --gerente/--q/--sin-simbolo` · `categoria <ids> --set` · `alta` (bilateral manual) · `vcp <id> <fecha> <valor>`. |

## 6. Decisiones y límites

- **Primary primero, tenencia después, manual último.** La tenencia trae el VCP
  al que Aunesa valuó ese día; puede diferir del de Primary en el redondeo o en
  un día de rezago, por eso no pisa a `primary`. La ficha muestra de qué fuente
  salió cada tramo.
- **La fecha del LA es la del timestamp, no la de la corrida.** Si el job corre
  y Primary todavía no publicó el día, se guarda el de ayer en ayer y mañana
  entra el de hoy. Sin esto un feriado o una publicación tardía corría toda la
  serie un día.
- **Hipótesis sin medir:** que la banda low/high de Primary sea el VCP del día
  anterior (ADCABLD: banda 1.040368 vs LA 1.040419). Si se confirma, el job
  puede recuperar un día perdido con la misma llamada del catálogo. Se mide con
  `scripts/diag_fci_primary` un día de estos.
- **Estantes ≠ tipo de renta.** `categoria` es la clasificación de la mesa; la
  sugerencia automática solo cubre lo obvio (Mercado de Dinero → T+0 MONEY
  MARKET / MONEY MARKET USD; Renta Fija T+0/T+1 en ARS → T+0/T+1; Renta Fija
  USD → RENTA FIJA USD). Lo demás queda sin estante hasta que alguien lo ponga.
- **Portal invitado:** `fci` NO está en `INVITADO_MODULES` (REGLA #8).
- **REGLA #10:** las dos piezas están en el árbol del Diagnóstico
  (`diagnostico_registry`); una habilidad propia del agente queda pendiente.
- **Pendientes visibles:** pantalla en Manager para gerentes/alias/estantes y
  altas manuales (hoy `fci_admin`); métricas del panel derecho; cuánto tenemos
  de cada fondo (el join ya está).

## 7. Archivos

`core/fci_match.py` · `jobs/fci_universo.py` · `jobs/fci_vcp.py` ·
`scripts/fci_admin.py` · `scripts/diag_fci_primary.py` ·
`api/services/fci_sql.py` · `api/routers/fci.py` · `tests/unit/test_fci.py` ·
front: `src/app/fci/page.tsx`, `components/fci-view.tsx`, `fci-table.tsx`,
`fci-ficha.tsx`, `lib/types-fci.ts`, proxy `src/app/api/fci/[...path]/route.ts`.

---

# PARTE B — Changelog

### 2026-09-10 — nace el mercado FCI (MVP sobre Primary + tenencia)

- Diags previos (`diag_fci_primary`, `diag_fci_cafci` — este último ya borrado) medidos en el Droplet:
  Primary lista 776 CIO sin histórico; CAFCI devuelve 403. Se decide Primary
  como fuente principal y la tenencia como segunda; CAFCI afuera.
- Modelo `mercado.fci_{gerentes, fci, vcp}`; `core/fci_match` (normalización de
  nombres, gerente por alias, plazo desde settlType, sugerencia de estante);
  jobs `fci_universo` (12:20) y `fci_vcp` (20:30, + backfill de tenencia);
  `scripts/fci_admin`.
- `assets.instrumento` pasa a llevar el símbolo Primary del fondo (match único,
  nunca pisa). Universo = gerentes de contrapartes/assets, filtro duro.
- Endpoint `GET /api/fci/tabla` y `GET /api/fci/fondo/{id}`; módulo RBAC `fci`
  (mesa sí, invitado no). Vista `/fci` 50/50: tabla por estante con ranking
  adentro + ficha con rendimientos, curva del VCP base 100 y fuentes.
