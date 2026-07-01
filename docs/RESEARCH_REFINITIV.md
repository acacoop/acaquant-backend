# Análisis — Integración Refinitiv/LSEG → capa ANÁLISIS (Renta Variable)

> Documento de diseño (vivo). Objetivo: capa **ANÁLISIS** con fundamentals puros
> (income statement / balance / cash flow) que alimenta la vista **RESEARCH**
> (armado de informes tipo `docs/prueba.pdf`). Este doc es el análisis previo a
> codear — se actualiza a medida que confirmamos cosas contra Workspace.

## 0. TL;DR

- **"Eikon" ya no existe**: LSEG lo dio de baja el **30-jun-2025**. Hoy la app es
  **LSEG Workspace** y la librería nueva es **`lseg-data`** (antes `refinitiv-data`).
  El paquete legacy `eikon` todavía anda contra Workspace pero está deprecado
  (se elimina en la v2.0 de la lib). Para proyecto nuevo → **`lseg-data`**.
- **Corre en la PC de la oficina, MANUAL**: la librería usa una *Desktop Session*
  que necesita **Workspace ABIERTO y logueado en la misma máquina**. No hay forma
  de correrlo en el Droplet. Mismo patrón que el **feed del dólar MAE**.
- **Cubre casi todo el informe**: income/balance/cash flow, múltiplos, market data,
  perfil de la compañía, estimados de consenso (I/B/E/S) y noticias (Reuters).
- **Arranque**: 1 empresa piloto, script `scripts/refinitiv_fundamentals.py`, upsert
  idempotente a Supabase schema `research`.

## 1. Qué API es (y la corrección importante)

| | Legacy | **Recomendado (nuevo)** |
|---|---|---|
| App de escritorio | Eikon (retirado 30-jun-2025) | **LSEG Workspace** |
| Librería Python | `eikon` (deprecada) | **`lseg-data`** |
| Import | `import eikon as ek` | `import lseg.data as ld` |
| Sesión | `ek.set_app_key(KEY)` | `ld.open_session()` (Desktop) |
| Traer datos | `ek.get_data(...)` | `ld.get_data(...)` (alineado con el viejo) |

La migración es directa: `get_data()` de la lib nueva está **alineado** con
`ek.get_data()` (mismos data items TR.*). Lo que aprendamos con una sirve para la otra.

## 2. Dónde corre y cómo fluye el dato

```
PC oficina (Workspace ABIERTO + logueado)
   │  scripts/refinitiv_fundamentals.py  (lo corrés VOS, manual)
   │  ld.open_session() → habla con Workspace por un proxy local
   ▼
Supabase / Postgres  (schema research)
   ▼
api/  (services + router research)
   ▼
acaquant-web  →  vista ANÁLISIS  →  alimenta  vista RESEARCH
```

- **Manual, no cron**: lo corrés cuando hay balance nuevo (trimestral) o cuando
  querés refrescar una empresa. No es un motor always-on.
- **Precedente**: el feed del dólar MAE ya corre así (script local en la PC de la
  oficina que escribe a la base). Reusamos ese patrón — probablemente la PC ya
  tenga `POSTGRES_URI` configurado (**a confirmar**).

## 3. Funciones de `lseg-data` que vamos a usar

| Necesidad | Función | Para qué (en el informe) |
|---|---|---|
| Fundamentals + snapshot | `ld.get_data(universe, fields)` | income/balance/cash flow, múltiplos, market data, perfil |
| Serie de precios | `ld.get_history(universe, fields, interval, start, end)` | gráfico "últimos 12 meses" + beta |
| Noticias | `ld.news.get_headlines(query)` / `get_story(id)` | hitos recientes (Reuters) |
| Símbolos | `ld.discovery.search(...)` / symbology | resolver ticker BYMA (`YPFD.BA`) ↔ RIC ↔ ADR |

Los estados completos se pueden pedir en bloque con los templates
`TR.F.IncomeStatement`, `TR.F.BalanceSheet`, `TR.F.CashFlowStatement`
(param `Period=FY0`, `FY-1`, … y `Frq`).

## 4. Mapeo: cada dato del PRUEBA → su origen (el corazón del análisis)

Leyenda de origen: 🟢 Refinitiv automático · 🧮 calculado · ✍️ manual analista.
**Los nombres de campo TR.* hay que confirmarlos en el Data Item Browser** (el "?"
al lado del dato en Workspace) — acá van los candidatos, no como verdad absoluta.

### Estado de resultados (página 3)
| Dato | Origen | Campo candidato |
|---|---|---|
| Ingresos | 🟢 | `TR.Revenue` |
| Costos operativos | 🟢 | `TR.CostOfRevenueTotal` / `TR.OperatingExpenses` |
| EBITDA | 🟢 | `TR.EBITDA` |
| Margen EBITDA | 🧮 | EBITDA / Ingresos |
| Depreciación & amort. | 🟢 | `TR.DepreciationDepletionAndAmortizationTotal` |
| Resultado operativo | 🟢 | `TR.OperatingIncome` |
| Resultado neto | 🟢 | `TR.NetIncomeAfterTaxes` |
| BPA (EPS) | 🟢 | `TR.BasicEpsExclExtraItems` / `TR.EPSActValue` |
| Flujo de caja libre | 🟢 | `TR.FreeCashFlow` (o 🧮 CFO − CapEx) |
| **Columnas 2025E / 2026E** | 🟢/✍️ | consenso I/B/E/S `TR.RevenueMean(Period=FY1)`, `TR.EPSMean(Period=FY1)` **o** tus propias estimaciones manuales — **decisión tuya** |

### Balance / cash flow (base completa que pediste)
| Dato | Origen | Campo candidato |
|---|---|---|
| Total activos | 🟢 | `TR.TotalAssetsReported` |
| Deuda total / neta | 🟢 | `TR.TotalDebtOutstanding` / `TR.NetDebt` |
| Patrimonio neto | 🟢 | `TR.TotalShareholdersEquity` |
| Flujo de caja operativo | 🟢 | `TR.CashFromOperatingActivities` |
| CapEx | 🟢 | `TR.CapitalExpenditures` |

### Múltiplos, ratios y datos clave (páginas 1 y 3)
| Dato | Origen | Campo candidato |
|---|---|---|
| Cap. de mercado | 🟢 | `TR.CompanyMarketCap` |
| Valor empresa (EV) | 🟢 | `TR.EnterpriseValue` |
| Acciones en circ. | 🟢 | `TR.SharesOutstanding` |
| P/E | 🟢 | `TR.PE` (o 🧮 precio/BPA) |
| EV/EBITDA | 🟢 | `TR.EVToEBITDA` |
| P/VL | 🟢 | `TR.PriceToBVPerShare` |
| ROE | 🟢 | `TR.ROEActValue` |
| Deuda neta / EBITDA | 🧮 | NetDebt / EBITDA |
| Dividend yield | 🟢 | `TR.DividendYield` |
| Beta (vs índice) | 🟢/🧮 | `TR.Beta` o de `get_history` |

### Perfil, negocio, pares (páginas 1 y 2)
| Dato | Origen | Campo candidato |
|---|---|---|
| Sector / índice | 🟢 | `TR.TRBCEconomicSector`, `TR.ExchangeName` |
| Perfil / a qué se dedica | 🟢→✍️ | `TR.BusinessSummary` como semilla; el analista lo edita |
| Composición de ingresos por segmento | 🟢 | `TR.BGS.*` (business segments) — **puede no venir** según la empresa → acá el semáforo se justifica |
| Hitos recientes | 🟢→✍️ | `news.get_headlines` (Reuters) o manual |
| Comparación con pares | 🟢 + ✍️ | mismos campos sobre la lista de pares (que elegís vos) |

**Conclusión del mapeo**: ~80% del informe se puede automatizar. Lo genuinamente
✍️ manual queda en: perfil/tesis narrativa, elección de pares, y (opcional) los
estimados si no querés el consenso. Eso es exactamente lo que el **semáforo por
bloque** va a marcar como "falta X".

## 5. Modelo de datos — schema `research`

```
research.companies       -- catálogo (1 fila por empresa)
  ticker_byma  text PK    -- 'YPFD'
  ric          text       -- 'YPFD.BA'  (clave Refinitiv)
  adr          text        -- 'YPF'
  nombre       text
  sector       text
  indice       text
  currency     text        -- reporte en USD o ARS
  updated_at   timestamptz

research.fundamentals    -- TABLA LARGA (1 fila por métrica)
  ticker_byma  text
  period       text        -- 'FY2024', 'FY2025E', 'FQ2026Q1'
  freq         text        -- 'FY' | 'FQ'
  statement    text        -- 'income' | 'balance' | 'cashflow' | 'ratios' | 'market'
  item         text        -- 'Revenue', 'EBITDA', 'NetIncome'...
  value        numeric
  currency     text
  is_estimate  bool         -- consenso/estimado vs reportado
  source       text         -- 'refinitiv'
  updated_at   timestamptz
  PRIMARY KEY (ticker_byma, period, statement, item)
```

**Por qué tabla LARGA y no una columna por métrica** (la decisión de arquitecto):
un balance tiene cientos de líneas y varían por empresa/sector. Con una columna
por métrica, cada concepto nuevo = `ALTER TABLE` + migración + tocar todo el
código. Con tabla larga, agregar un concepto = **insertar una fila**. Es el patrón
estándar para estados financieros (EAV — entity-attribute-value). El costo es que
para "armar" el income statement hay que pivotear (fácil en SQL/pandas).

El gráfico de precios 12m: primero ver si ya lo tenemos en
`mercado.precios_acciones` para la piloto; si no, lo trae `get_history`.

## 6. El script piloto

`scripts/refinitiv_fundamentals.py <TICKER>` — **corre en tu PC con Workspace abierto**:

1. `ld.open_session()` (Desktop).
2. Resuelve el RIC del ticker BYMA (symbology).
3. `get_data` de income + balance + cash flow (varios años + estimados) y datos clave.
4. Upsert idempotente a `research.fundamentals` (cortarlo y re-correrlo no duplica).
5. Log de qué trajo y qué faltó (input del semáforo).

Scopeado a **1 empresa** → liviano y seguro (REGLA #4). No escanea nada masivo.

Requisitos en la PC oficina:
- `pip install lseg-data`
- Config con el **app key** (App Key Generator, dentro de Workspace).
- Acceso a Supabase (`POSTGRES_URI`) — **probablemente ya está** por el feed MAE.

## 7. Pendientes / a confirmar (medir, no asumir — REGLA #2)

1. **Empresa piloto**: ¿cuál? Ideal una real del panel con ADR (YPF, PAMP, GGAL…).
   El PRUEBA (Cordillera Energía) es ficticio.
2. **DB desde la PC oficina**: ¿la PC que corre el feed MAE ya tiene `POSTGRES_URI`
   y llega a Supabase? (Si sí, reusamos.)
3. **App key**: generarlo en Workspace (App Key Generator).
4. **Nombres exactos de campos TR.***: confirmar en el Data Item Browser al primer run.
5. **Estimados**: ¿consenso I/B/E/S automático, o los cargás vos a mano?
6. **Moneda del reporte**: el PRUEBA mezcla ARS (portada/precio) y USD (estados).
   Definir en qué moneda pedimos los estados (Refinitiv permite elegir).

## Fuentes
- Eikon Data API / retiro y migración: developers.lseg.com (Eikon Data API; Upgrade to Data library)
- `lseg-data` (Desktop Session, `get_data`, `get_history`, news): pypi.org/project/lseg-data + LSEG Developer Portal + LSEG-API-Samples/Example.DataLibrary.Python
- Financial statements con `get_data` (TR.F.* templates): LSEG Developer Community
