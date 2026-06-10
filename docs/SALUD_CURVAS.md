# SALUD_CURVAS.md — Salud y valuación de `Trading.Curvas`

> Doc de contexto para **mantener sana la valuación de curvas** (TEA, paridad,
> duration) y planificar un sistema que **avise** cuando algo se rompe antes de
> que ensucie las vistas/charts. Reúne: el modelo de datos, cómo se valúa, las
> herramientas que ya tenemos, el **catálogo de fallas conocidas** y el roadmap
> hacia un "health check". No es operación del día a día (eso es el RUNBOOK);
> es el mapa para diseñar la prevención.

---

## 1. Por qué existe este doc

`Trading.Curvas` alimenta TODO lo de renta fija: la curva, las tablas, los
ratios, la home, los breakevens, forwards, AuM/PnL (vía el join a Assets). Un
solo bono mal valuado (TEA absurda, paridad explotada) **rompe el chart entero**
(escala) y contamina cualquier cruce. Los errores casi nunca son de fórmula:
son de **datos** (moneda mal puesta, flujo en otra escala, pata equivocada,
precio stale). Este doc cataloga eso para poder **detectarlo automático**.

---

## 2. Modelo de datos — `Trading.Curvas`

Un doc por instrumento. Campos que importan para la valuación:

| Campo | Qué es | Ojo |
|---|---|---|
| `curva` | Familia: `cer`, `tasa_fija`, `globales`/`bonares` (soberanos), `dolar_linked`, `on` / `on_<sector>` | El **sector de las ONs va acá** (`on_energia`, `on_financiera`, `on_otros`), no en config |
| `ticker` | Ticker ROFEX completo de la **pata** elegida (`MERV - XMEV - <SIMBOLO> - 24hs`) | Define de qué **pata** sale el precio (ver §4) |
| `ticker_corto` | Label humano (clave única) | Puede no reflejar la moneda real |
| `moneda_flujo` | `USD` / `DL` / `ARS` — **cómo se valúa** (ver §3) | Debe coincidir con `Assets.CARTERA` (HD→USD, DL→DL, ARS→ARS) |
| `valor_nominal` | VN base (normalmente 100) | |
| `flujos[]` | Cronograma de pagos | Shape **distinto por familia** (ver abajo) |

**Shape de `flujos` por familia** (no inferible — está en el CLAUDE.md raíz):
- **ONs / soberanos**: nativo BondsMaster, montos absolutos **por 100 VN** →
  `{fecha, amortizacion, interes, valor_residual}`. `monto_flujo = amortizacion + interes`.
- **CER**: porcentual, `amortizacion_pct` + `cupon_sobre_residual` (ya resuelto).
- **tasa_fija**: absolutos, `amortizacion` + `interes`, requiere `flujo_vencimiento`.

**Join a AuM/PnL**: `Curvas.ticker_corto` → `Assets.TICKER` → `Assets.unidad` →
`AuM`. Sin el doc espejo en `Assets`, el bono no aparece en AuM/Portfolios.

---

## 3. Cómo se valúa — el motor (`engines/curvas.py`)

Dos motores escriben a `Trading.MarketSnapshot` (no se pisan):
- **`motor_rofex`** (`engines/valores.py`, refresh 1s) → `metrics.last_price` y
  demás precios. Es **precio de pantalla (market data)** — puede existir sin que
  el bono haya operado.
- **`motor_curvas`** (`engines/curvas.py`, refresh 5s) → `metrics.{TEA, TEM,
  duration, mod_duration, convexity, paridad}`. Lee el `last_price` del snapshot.

> Los motores cargan `Trading.Curvas` **una sola vez al arrancar** → un bono
> nuevo o un cambio de `moneda_flujo`/`flujos` **no impacta hasta reiniciar**
> `motor_rofex` + `motor_curvas`.

### El cálculo, paso a paso (rama ON; las demás son análogas)

1. **Settlement** = T+1 hábil desde la fecha del trade.
2. **Precio → escala de cálculo**, según `moneda_flujo`:

   | `moneda_flujo` | Conversión del precio | TC usado |
   |---|---|---|
   | `USD` | sufijo `D`/`C` → tal cual (ya USD); si no → **÷ MEP** | `get_ultimo_mep` |
   | `DL` | precio **< 1000** → tal cual (pata USD); **≥ 1000** → **÷ A3500** | **dólar oficial live** (`DolarOficialLive`, el de la home) |
   | `ARS` | tal cual (peso) | — |

3. **Flujos futuros** = los de `fecha > settlement` con `monto_flujo > 0`.
4. **TEA** = `XIRR(-precio_calc, flujos_futuros)`. **Sólo persiste si
   `-0.5 < TEA < 50`** — si no, cae a duration naïve y **no escribe TEA** (queda `--`).
5. **Paridad** = `precio_calc / (Σ amortizaciones futuras) × 100`. (El "residual
   vivo" = nominal que falta amortizar = Σ de amort futura. Robusto, no depende
   del campo `valor_residual`.)
6. **Duration / convexity** = Macaulay sobre los flujos.

### Dependencias externas (si fallan, TEA/paridad salen mal o faltan)

| Dependencia | Fuente | Qué pasa si está mal/caída |
|---|---|---|
| **MEP** | `get_ultimo_mep` (live, TTL 5s) | ONs/soberanos USD en pesos quedan sin TEA |
| **A3500 / dólar oficial live** | `Valuaciones.DolarOficialLive` (feed MAE, PC oficina) | DL pata peso quedan sin TEA. **Mismo TC que la home** (`core.dolar_oficial.mid_oficial_live`) |
| **CER** | `Trading.DOLAR`/BCRA (T-10 hábiles) | CER salen con CER viejo si el bono no operó |

---

## 4. Las dos patas (ARS / USD) — la causa raíz más sutil

Casi toda ON cotiza en **dos patas**: una en **pesos** (`VSCIO`, precio ~144.000)
y una en **dólares** (sufijo `D`, `VSCIOD`, precio ~100). `Trading.Curvas` guarda
**una sola** en `ticker` → de ahí sale el precio. La regla:

- **HD (hard-dollar)** → debería usar la **pata USD** (precio ~100, as-is). Si
  guardás la pata peso, el motor hace ÷MEP y el MEP **no siempre recupera el
  precio dólar real** → paridad/TEA infladas. *(Ej. `YMCTO`: pata peso 160.000
  ÷MEP = 110 → TEA −25%.)*
- **DL (dólar-linked)** → debería usar la **pata peso** (precio ~144.000, ÷A3500).
  Si guardás la pata USD (~100), el motor ya la detecta por escala (<1000) y la
  usa tal cual. *(Ej. `TTCEO`.)*
- **ARS (peso nativo)** → pata peso, precio directo.

> **Elegir bien la pata por bono es el frente abierto (#③).** Hoy no hay regla
> automática que garantice que un HD use la pata USD.

---

## 5. Herramientas que YA tenemos

| Herramienta | Qué hace | Cuándo usarla |
|---|---|---|
| **DEBUG TEA** (`/manager → checks`, `api/services/debug_curva.py`) | Replica el motor para 1 ticker: muestra precio, TC, flujos, cashflow XIRR y compara calculado vs persistido | Entender por qué un bono da `--` o una TEA rara |
| **`scripts/diag_ons_clasificacion.py`** | Cruza `Assets.CARTERA` vs `moneda_flujo` vs escala precio↔flujo de TODAS las ONs; marca `MONEDA?` / `ESCALA?` | Auditar clasificación masiva |
| **`scripts/fix_ons_moneda_from_cartera.py`** | Alinea `moneda_flujo` ← CARTERA | Cuando la moneda no condice con CARTERA |
| **`scripts/fix_ons_flujo_a_per100.py`** | Normaliza flujos DL de escala peso a ~100 | Flujo cargado en pesos |
| **`scripts/fix_ons_valor_residual.py`** | Recalcula `valor_residual = Σ amort futura` | Si la paridad explota por residual mal escalado |
| **`scripts/perf_scan.py`** | Anti-patterns Mongo | CI / antes de pushear |

> El DEBUG TEA lee el precio de **`MarketSnapshot`** (la misma fuente que el
> motor), no de `TimeSales` — por eso muestra bonos que cotizan sin haber operado.

---

## 6. Catálogo de fallas conocidas

Síntoma visible → causa → cómo detectarlo → cómo arreglarlo.

| # | Síntoma | Causa | Detección | Fix |
|---|---|---|---|---|
| 1 | TEA `--` o falsa | `moneda_flujo` ≠ tipo real (CARTERA) | `diag_ons_clasificacion` flag `MONEDA?` | `fix_ons_moneda_from_cartera` |
| 2 | XIRR no converge | Flujo en escala peso vs precio USD (o al revés) | `diag` flag `ESCALA?` / DEBUG: precio_usd vs monto flujo | `fix_ons_flujo_a_per100` |
| 3 | **Paridad explotada** (ej. 144.500%) | `valor_residual` en otra escala que el flujo | Paridad >> 150% en la vista | Motor ya usa Σ amort; `fix_ons_valor_residual` limpia el dato |
| 4 | TEA negativa/inflada en HD | **Pata peso** guardada en un HD → ÷MEP infla el precio | DEBUG: precio_usd > ~108 con paridad rara | **Frente #③**: usar la pata USD |
| 5 | TEA absurda (>50% o <−50%) | **Precio stale/ilíquido** (last viejo) o bono distressed | DEBUG: comparar precio vs último trade real | Verificar precio; no es bug de cálculo |
| 6 | Bono sin TEA pero con precio | Precio de pantalla sin trade (market data) | DEBUG muestra precio, TimeSales vacío | Normal; revisar si el precio es representativo |
| 7 | Bono no cotiza tras alta/cambio | Motores cargan Curvas al arrancar | — | `systemctl restart motor_rofex motor_curvas` |

---

## 7. Lo que falta — hacia un "health check" de Curvas (a planificar)

Ideas para que el sistema **avise solo** en vez de descubrir los errores a ojo:

1. **Job de sanity diario** (`jobs/curvas_healthcheck.py`, post-cierre) que corra
   reglas sobre `MarketSnapshot.metrics` + `Curvas` y avise por **Telegram**
   (ya existe el bot, wired en `JobRunLogger`):
   - paridad fuera de `[40, 160]%`
   - `|TEA|` fuera de `[-30, 60]%` (revisar precio/pata)
   - `moneda_flujo` ≠ CARTERA (regla del `diag_ons_clasificacion`)
   - escala flujo ≠ escala precio (ARS con precio peso y flujo ~100)
   - bono con `flujos` pero sin TEA hace > N días
   - bono en `Curvas` sin espejo en `Assets` (no entra a AuM)
2. **Validación en el ALTA de Manager**: al cargar/editar una ON, correr las
   mismas reglas y mostrar el warning antes de guardar (prevención > corrección).
3. **Guard en el chart** (frontend): filtrar/clamp de outliers (paridad o TEA
   fuera de rango) para que un bono roto no rompa la escala de toda la curva.
4. **`moneda_flujo` derivado de CARTERA**: en vez de mantener dos fuentes que
   driftean, que un sync (o el motor) lea CARTERA → así la falla #1 no puede
   existir. CARTERA queda como única fuente de verdad del tipo.
5. **Resolver la pata (#③)**: regla para que cada HD use la pata USD y cada DL la
   pata peso (por sufijo del ticker o por un campo explícito), eliminando la #4.

> Prioridad sugerida: **3 (guard chart, rápido) → 1 (alertas) → 4/5 (estructural)**.

---

## Archivos clave

- Motor: `engines/curvas.py` · precios: `engines/valores.py`
- DEBUG: `api/services/debug_curva.py` · vista: `api/services/renta_fija.py`
- TC: `core/dolar_oficial.py` (dólar oficial live) · MEP: `get_ultimo_mep`
- Diags/fixes: `scripts/diag_ons_clasificacion.py`, `scripts/fix_ons_*.py`
- Modelo/fórmulas no inferibles: **`CLAUDE.md` raíz** (sección "Trading.Curvas")
