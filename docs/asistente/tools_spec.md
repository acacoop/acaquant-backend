# Tools Spec — Asistente de Mesa ACA Valores

Contrato canónico de las tools que consume el asistente. Este documento es la
**única fuente de verdad** para:

- Decidir firma + return schema antes de escribir código.
- Trazabilidad: qué tool usa qué test case del golden set.
- Decidir orden de build: Tier 1 (core, habilita la mayoría) → Tier 2
  (extensiones) → Tier 3 (bloqueadas por data externa).

Lee esto antes de implementar una tool nueva o antes de cambiar una existente.

> **Convención**: el nombre de la tool es el que ve el LLM. Los nombres que
> figuran en `docs/asistente/golden_set.yaml` (ej. `obtener_curva_cer`,
> `obtener_curva_hd`) son **aliases aspiracionales** que en el build real
> mapean a la tool canónica con params (`listar_curva(curva="cer")`). Cuando
> armemos el eval runner, incluirá un mapping de aliases → (tool_real, args_extra).

---

## 0. Convenciones compartidas

### Estructura de una entrada

Cada tool documentada tiene:

- **Qué hace** — una frase.
- **Nombre canónico** (el que ve el LLM + el que el runner invoca).
- **Signature** — tipos Python.
- **Return schema** — shape del output (JSON ejemplo).
- **Invariantes** — qué debe cumplir el output (enganchados a
  `api/agent/invariants.py`).
- **Data sources** — colecciones Mongo o endpoints externos.
- **Dependencias** — otras tools, engines o colecciones previas.
- **Tests del golden set** — qué test cases la invocan.
- **Priority** — Tier 1 / 2 / 3.
- **Status** — 🟢 existe · 🟡 existe parcial (adaptar) · 🔴 a construir ·
  🔒 bloqueada por data externa.

### Metadata estándar del response

**TODAS** las responses que devuelve `dispatch()` al LLM pasan por
`_process_service_output()` y vienen con `_meta` (ya implementado en commit
`f4939b4`):

```json
{
  "ok": true,
  "data": [...],
  "_meta": {
    "as_of_ts": 1776721320,
    "data_ts": 1776715200,
    "age_s": 6120,
    "staleness": "fresh|stale|very_stale|unknown",
    "source": "/api/cotizaciones/renta-fija",
    "warnings": ["paridad=180 fuera de [0,150] en BAD1"]   // si aplica
  }
}
```

Ninguna tool individual debe retornar `_meta` por sí sola — el wrapper lo
agrega. El service handler devuelve **solo el `data`** (lista o dict).

### Schemas de error estándar

Tool que no encuentra ticker:

```json
{
  "ok": false,
  "error": "no se encontraron datos para 'TZX26D'",
  "did_you_mean": ["TZXD6", "TZX26"],
  "hint": "Probá con: TZXD6, TZX26"
}
```

Tool que falla (arg inválido, excepción interna):

```json
{"ok": false, "error": "error del service: <mensaje>"}
```

Tool bloqueada por política (carteras/AuM/clientes):

```json
{"ok": false, "error": "ruta bloqueada por política de datos..."}
```

---

## 1. Framework Stats — base compartida

**Archivo propuesto**: `api/agent/stats.py`

Tools Tier 1 de "benchmarks dinámicos" (no absolutos). Reemplazan a las
tools específicas `obtener_tamar_actual`, `obtener_cer_actual`, etc.

### 1.1 Helpers internos

```python
def percentile(coleccion: str, campo: str, ventana_dias: int, fecha_ref: datetime | None = None) -> float | None
def zscore(coleccion: str, campo: str, ventana_dias: int, fecha_ref: datetime | None = None) -> float | None
def classify_level(coleccion: str, campo: str, ventana_dias: int) -> str
```

`classify_level` devuelve uno de: `"minimo"`, `"bajo"`, `"medio"`, `"alto"`,
`"maximo"`, `"tendencia_alcista"`, `"tendencia_bajista"`, `"lateral"`,
`"sin_datos"`.

Cache interno con TTL corto por `(coleccion, campo, ventana)` — 60s.

---

## 2. Tier 1 — Tools Core

Las 3 tools de más alto impacto del golden set.

### 2.1 `listar_curva` 🔴

**Qué hace**: devuelve los instrumentos de una curva con metadata enriquecida
en un solo pedido. Reemplaza a los placeholders `obtener_curva_cer`,
`obtener_curva_tasa_fija`, `obtener_curva_hd`, `obtener_curva_tamar`,
`obtener_curva_dl` del golden set.

**Nombre canónico**: `listar_curva`

**Signature**:
```python
def listar_curva(
    curva: Literal["cer", "tasa_fija", "tamar", "soberanos", "dolar_linked"],
    ordenar_por: Literal["vencimiento", "volumen_dia", "tea", "duration"] = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]
```

**Return schema**:
```json
[
  {
    "ticker": "MERV - XMEV - TX26 - 24hs",
    "ticker_corto": "TX26",
    "tipo": "cer",
    "fecha_vencimiento": "2026-11-09",
    "meses_al_vto": 6.8,
    "ultimo_precio": 127.45,
    "tea": -0.03,
    "tem": null,
    "paridad": 98.2,
    "duration": 0.55,
    "convexity": 0.78,
    "total_money_dia": 1234567890.0,
    "ts_ultimo_trade": "2026-04-20T19:45:00Z"
  }
]
```

**Invariantes**:
- `paridad ∈ [0, 150]` para cada item.
- `duration ≥ 0`.
- Si `flujos` presente → `Σ amortizacion_pct ≈ 100` (check existente).

**Data sources**:
- `Trading.Curvas` (filter por `curva`).
- `Trading.TimeSales` (último trade enriquecido con TEA/TEM/duration/paridad).
- `Trading.MarketSnapshot` (volumen del día, `metrics.total_money`).

**Dependencias previas**:
- Para `curva="soberanos"` y `curva="dolar_linked"`: los bonos deben estar
  cargados en `Trading.Curvas` con `curva: "soberanos"` / `"dolar_linked"`
  (ver sección 4).
- Para que traiga `convexity`: `engines/curvas.py` extendido (sección 4.2).
- Para HD: `engines/curvas.py` enriqueciendo hard dollar (sección 4.1).

**Tests del golden set** (15+): look-001, look-003, look-004, cart-001 a
cart-006, conn-002, conn-003, conn-004, fwd-001, fwd-002, fwd-003, risk-002,
risk-003, risk-004, esc-002, esc-003, esc-004, estr-001 a estr-006, mt-001,
mt-003, ambig-001, ambig-002, edge-003.

**Priority**: **Tier 1 · high priority · arrancar por acá**.

**Notas de implementación**:
- Agrupar el pipeline en `$lookup` entre TimeSales y Curvas si es posible;
  si no, iterar por ticker con queries chicas es aceptable.
- El `ordenar_por="volumen_dia"` ordena desc (más líquido primero).
- `vencimiento_min/max_meses` se aplica sobre `fecha_vencimiento` vs now.

---

### 2.2 `obtener_serie_macro` 🔴

**Qué hace**: devuelve el valor actual + serie + stats de una variable macro
o serie por ticker. Tool **genérica** que reemplaza la familia
`obtener_tamar_actual`, `obtener_cer_actual`, `obtener_canje_actual`, etc.

**Nombre canónico**: `obtener_serie_macro`

**Signature**:
```python
def obtener_serie_macro(
    variable: str,
    ventana_dias: int = 90,
) -> dict
```

`variable` acepta:
- **Macros puras**: `tamar | cer | dolar | badlar | riesgo_pais | ccl | mep |
  canje | ipc | ipim | repo | rem_inflacion`.
- **Series por ticker**: `<TICKER>.<CAMPO>` donde `<CAMPO>` es `TEA | TEM |
  paridad | duration | price | TTM`. Ej: `"TX26.TEM"`, `"GD30.paridad"`.

**Return schema**:
```json
{
  "variable": "tamar",
  "actual": 0.228,
  "fecha_actual": "2026-04-19",
  "serie": [
    {"fecha": "2026-01-20", "valor": 0.41},
    {"fecha": "2026-01-21", "valor": 0.41}
  ],
  "cambio_dia_pct": -0.5,
  "cambio_semana_pct": -2.1,
  "cambio_mes_pct": -8.3,
  "min": 0.22,
  "max": 0.42,
  "media": 0.31,
  "desvio": 0.055,
  "percentil_actual": 15,
  "zscore_actual": -1.8,
  "clasificacion": "bajo"
}
```

**Invariantes**:
- `percentil_actual ∈ [0, 100]` si hay datos.
- `clasificacion ∈ {minimo, bajo, medio, alto, maximo, tendencia_alcista,
  tendencia_bajista, lateral, sin_datos}`.
- Si `ventana_dias < 5`, `clasificacion = "sin_datos"` (muestra insuficiente).

**Data sources** (por variable):
| Variable | Colección / Fuente |
|---|---|
| `tamar`, `cer`, `dolar`, `badlar` | `Trading.TAMAR/CER/DOLAR/BADLAR` (existe) |
| `mep` | `Valuaciones.Dolar.mep` (existe) |
| `ccl`, `canje` | 🔒 `Valuaciones.Dolar.ccl` (falta data — sección 4.3) |
| `ipc`, `ipim` | 🔒 `Macro.Inflacion` (falta data — sección 4.4) |
| `riesgo_pais` | 🔒 `Macro.RiesgoPais` (falta data — sección 4.5) |
| `repo` | 🔒 `Macro.REPO` (falta data — sección 4.6) |
| `rem_inflacion` | 🔒 `Macro.REM` (falta data — sección 4.7) |
| `<TICKER>.<CAMPO>` | `Trading.TimeSales` (existe) |

**Dependencias**: módulo `stats.py` (sección 1).

**Tests del golden set** (15+): look-001, look-002, look-004, cart-001,
cart-002, cart-004, conn-001, conn-004, fwd-001, fwd-002, fwd-003, risk-001,
risk-002, risk-004, esc-003, estr-001, estr-002, estr-003.

**Priority**: **Tier 1 · high priority · arrancar por acá**.

**Notas de implementación**:
- Parsing del `variable`: si contiene `.`, es serie por ticker; si no, macro.
- Para macros con data faltante, devolver:
  ```json
  {"variable": "ccl", "actual": null, "clasificacion": "sin_datos",
   "hint": "serie CCL no cargada en Mongo — ver docs/asistente/golden_set_backlog.md"}
  ```
  Así el modelo puede avisar al usuario sin alucinar.

---

### 2.3 `clasificar_nivel` 🔴

**Qué hace**: alias conveniente de `obtener_serie_macro` que devuelve
**solo la clasificación y el contexto**, sin la serie entera. Menos tokens
cuando el modelo solo quiere la etiqueta.

**Nombre canónico**: `clasificar_nivel`

**Signature**:
```python
def clasificar_nivel(
    variable: str,
    ventana_dias: int = 90,
) -> dict
```

**Return schema**:
```json
{
  "variable": "tamar",
  "actual": 0.228,
  "clasificacion": "bajo",
  "percentil_actual": 15,
  "zscore_actual": -1.8,
  "ventana_dias": 90,
  "contexto": "TAMAR en percentil 15 de los últimos 90 días; z-score -1.8 (mínimos recientes)"
}
```

**Invariantes**: idem `obtener_serie_macro`.

**Data sources**: idem `obtener_serie_macro`.

**Dependencias**: `obtener_serie_macro` internamente (no duplicar lógica —
wrapper).

**Tests del golden set**: look-001, look-002, look-004, cart-001 a cart-006,
conn-001, conn-002, conn-004, lici-001, lici-003, lici-007, be-001, fwd-001,
fwd-002, fwd-003, risk-003, estr-001, estr-002, estr-003.

**Priority**: **Tier 1** — wrapper trivial de `obtener_serie_macro`.

---

## 3. Tier 2 — Tools de extensión

Se apoyan en Tier 1 + data existente. Menos frecuentes pero valiosas.

### 3.1 `snapshot_curva_historico` 🔴

**Qué hace**: devuelve la curva completa en una fecha pasada.

**Signature**:
```python
def snapshot_curva_historico(
    curva: Literal["cer", "tasa_fija", "tamar", "soberanos", "dolar_linked"],
    fecha: str,  # YYYY-MM-DD
) -> list[dict]
```

**Return schema**: mismo que `listar_curva`, pero `ultimo_precio`, `tea`, etc.
son el **último del día X**.

**Data sources**: `Trading.Curvas` + `Trading.TimeSales` (último trade del día).

**Dependencias**: `listar_curva` (comparte pipeline base).

**Tests del golden set**: look-003 (curva hace un mes), estr-002 (butterfly
Z-score historia).

**Priority**: Tier 2.

**Notas**:
- Para `estr-002`, el modelo reconstruye la serie de butterfly spreads
  llamando esta tool iterativamente sobre fechas pasadas (30-60 días atrás).
  Costo aceptable si se cachea.

---

### 3.2 `calcular_pendiente_curva` 🔴

**Qué hace**: pendiente (largo - corto) de una curva, opcionalmente
comparada con fecha pasada.

**Signature**:
```python
def calcular_pendiente_curva(
    curva: Literal["cer", "tasa_fija", "tamar", "soberanos"],
    metrica: Literal["tea", "tem", "ytm"] = "tea",
    fecha_comparacion: str | None = None,  # YYYY-MM-DD
) -> dict
```

**Return schema**:
```json
{
  "curva": "tasa_fija",
  "metrica": "tem",
  "pendiente_actual_bps": -45,
  "corto": {"ticker": "S30O6", "tem": 0.018},
  "largo": {"ticker": "T30J7", "tem": 0.0135},
  "pendiente_comparacion_bps": -30,
  "delta_bps": -15,
  "interpretacion": "aplanamiento"
}
```

**Dependencias**: `listar_curva` + `snapshot_curva_historico`.

**Tests del golden set**: look-003.

**Priority**: Tier 2.

---

### 3.3 `liquidez_secundario` 🟡

**Qué hace**: volumen secundario promedio de un instrumento + clasificación
vs historia propia.

**Signature**:
```python
def liquidez_secundario(
    ticker: str,  # corto o completo
    dias: int = 20,
) -> dict
```

**Return schema**:
```json
{
  "ticker": "TX26",
  "volumen_promedio_dia": 1234567890.0,
  "volumen_dia_actual": 890000000.0,
  "ratio_vs_promedio": 0.72,
  "dias_analizados": 20,
  "clasificacion": "media"
}
```

**Data sources**: `Trading.MarketSnapshot.metrics.total_money` y
`Trading.TimeSales` (suma por día).

**Dependencias**: ninguna — data existente.

**Tests del golden set**: cart-005.

**Priority**: Tier 2.

---

### 3.4 `valor_relativo_en_curva` 🔴 (condicional)

**Qué hace**: dado un bono, calcula si está rico o barato vs fit de la curva.

**Signature**:
```python
def valor_relativo_en_curva(
    ticker: str,
    curva: Literal["cer", "tasa_fija", "tamar", "soberanos"] | None = None,
) -> dict
```

**Return schema**:
```json
{
  "ticker": "TX26",
  "curva": "cer",
  "tea_actual": -0.03,
  "tea_fitted": -0.025,
  "residuo_bps": -50,
  "clasificacion": "rico"
}
```

**Dependencias**: `listar_curva` + regresión lineal simple (TEA vs duration).

**Tests del golden set**: cart-003, estr-002.

**Priority**: Tier 2 **condicional** — se construye solo si en evals el
modelo falla sistemáticamente al calcularlo mentalmente con datos de
`listar_curva`.

---

## 4. Tier 3 — Bloqueadas por data externa

Estas tools están **diseñadas pero no implementables** hasta que carguemos
la data que consumen. Orden de prioridad para cargar la data según
frecuencia en el golden set.

### 4.1 Extender `engines/curvas.py` a Hard Dollar 🟡

**Qué**: el engine hoy enriquece CER (TEA/paridad) y tasa fija
(TEA/TEM/duration). Extender a **Globales/Bonares** con cálculo de
YTM + duration + convexity sobre flujos USD.

**Ubicación**: `engines/curvas.py`.

**Dependencia previa**: seed_soberanos corrido (ya pusheamos `seed_soberanos.py`
con GD30; faltan AL30, AL35, GD30, GD35, etc.).

**Habilita**: `listar_curva("soberanos")` con YTM real + duration.

---

### 4.2 Agregar `convexity` al enriquecimiento 🟡

**Qué**: fórmula `C = (1/P) × Σ[t(t+1) × CF_t / (1+y)^(t+2)]`, ~15 líneas en
`engines/curvas.py`.

**Habilita**: `listar_curva.convexity` para todos los tipos (CER, tasa fija,
HD). Habilita rubrics de risk-002, risk-004, conn-002, esc-002 que calculan
pérdida con Taylor de 2º orden.

---

### 4.3 CCL (Contado con Liqui) 🔒

**Colección**: `Valuaciones.Dolar` (extender con campo `ccl`).

**Fuente**: AL30C/AL30 o GD30C/GD30 (paridad local/cable).

**Implementación**: extender `engines/dolar_mep.py` o motor nuevo
`engines/dolar_ccl.py`.

**Habilita**: `obtener_serie_macro("ccl"|"canje")`.

**Bloqueante para**: look-002, cart-002, lici-003, lici-005, risk-004,
esc-004, conn-001.

---

### 4.4 Inflación INDEC (IPC + IPIM) 🔒

**Colección**: `Macro.Inflacion` con schema
`{fecha, indice: "IPC"|"IPIM", variacion_mensual, variacion_anual}`.

**Fuente**: INDEC (scraping web / PDF parse).

**Implementación**: `jobs/inflacion.py` mensual.

**Habilita**: `obtener_serie_macro("ipc"|"ipim")`.

**Bloqueante para**: look-001, cart-001, conn-004, be-001, be-002, be-003,
risk-001, mt-002.

---

### 4.5 Riesgo País (EMBI+) 🔒

**Colección**: `Macro.RiesgoPais` con schema
`{fecha, riesgo_pais_bps}`.

**Fuente**: ámbito, Rava, JP Morgan.

**Implementación**: `jobs/riesgo_pais.py` encadenado con BCRA.

**Habilita**: `obtener_serie_macro("riesgo_pais")`.

**Bloqueante para**: cart-002, cart-004, conn-002, conn-003, esc-002.

---

### 4.6 Stock de Pases BCRA (REPO) 🔒

**Colección**: `Trading.REPO` con serie diaria.

**Fuente**: API BCRA (variable ID en `jobs/bcra.py`).

**Habilita**: `obtener_serie_macro("repo")`.

**Bloqueante para**: lici-008, conn-001, estr-001.

---

### 4.7 REM BCRA 🔒

**Colección**: `Macro.REM` con schema
`{fecha_publicacion, horizonte_meses, indicador, mediana, percentil_10, percentil_90}`.

**Fuente**: BCRA publica CSV mensual.

**Implementación**: `jobs/rem.py` mensual.

**Habilita**: `obtener_serie_macro("rem_inflacion")`.

**Bloqueante para**: be-001.

---

### 4.8 Futuros Rofex USD/ARS 🔒

**Colección**: `Trading.FuturosRofex` con curva de futuros por vencimiento.

**Fuente**: pyRofex (ya conectado en el proyecto).

**Implementación**: motor nuevo o extender `engines/valores.py`.

**Habilita**: tools de licitación DL (`breakeven_dl_vs_rofex`).

**Bloqueante para**: lici-003, estr-005.

---

### 4.9 Licitaciones — Menu + Resultados 🔒

**Colecciones**:
- `Licitaciones.Menu`: `{fecha_licitacion, instrumentos: [...]}`.
- `Licitaciones.Resultados`: `{fecha, instrumento, tasa_corte, bid_to_cover, rolleo_pct}`.

**Fuente**: Ministerio Economía (comunicados 2-3 días antes / mismo día).

**Implementación**: carga manual inicialmente + scraping después.

**Tools habilitadas**:
- `obtener_menu_licitacion(fecha?)` — pre-lici.
- `obtener_resultado_licitacion(fecha?)` — post-lici.

**Bloqueante para**: lici-001 a lici-008.

---

### 4.10 Bonos Dólar Linked cargados 🔒

Similar al seed de soberanos — crear `docs/dolar_linked/*.json` con TZV26,
TZV28, D15F7, etc., y extender `scripts/seed_soberanos.py` o crear
`scripts/seed_dolar_linked.py`.

**Habilita**: `listar_curva("dolar_linked")`.

**Bloqueante para**: lici-003, risk-004, esc-004, cart-002 (parcial).

---

## 5. Tools específicas de licitaciones (Tier 3)

Todas bloqueadas por 4.9 (Menu + Resultados) y en algunos casos 4.8
(Futuros Rofex).

### 5.1 `obtener_menu_licitacion` 🔒
### 5.2 `obtener_resultado_licitacion` 🔒
### 5.3 `calcular_forward_bono_nuevo(nuevo_ticker, vencimiento, tea_estimada, curva)` 🔒
### 5.4 `calcular_breakeven_dl_vs_lecap(dl_ticker, lecap_ticker)` 🔒
### 5.5 `calcular_tamar_breakeven(spread_propuesto, plazo_meses)` 🔒

Firma y return schema a detallar cuando tengamos la data (una vez cargadas
`Licitaciones.Menu/Resultados`, abrimos spec 5.x).

---

## 6. Tools existentes documentadas

Las que ya viven en `api/services/cotizaciones.py` (commit `d774c1a`).
Mantenidas por compat. Para las preguntas del golden set, el modelo puede
seguir llamándolas hasta que existan las Tier 1 nuevas.

### 6.1 `get_breakevens` 🟢

**Nombre canónico expuesto al LLM**: `breakevens_actuales`.

**Qué hace**: devuelve la curva de breakevens Lecap↔CER live, ordenada por
plazo.

**Signature**:
```python
def get_breakevens() -> list[dict]
```

**Return schema**:
```json
[
  {
    "plazo_meses": 4,
    "par_lecap": "S30O6",
    "par_cer": "TX26",
    "be_mensual": 0.021,
    "updated_at": "2026-04-20T19:32:00Z"
  }
]
```

**Data source**: `Trading.BreakevensLive`.

**Invariantes**: `be_mensual ∈ [-0.5, 0.3]` (implícito en `invariants.py`).

**Tests del golden set**: be-001, be-002, be-003, cart-001, risk-001,
esc-001, mt-002.

---

### 6.2 `get_renta_fija` 🟢

**Nombre canónico**: `cotizacion_renta_fija`.

**Signature**:
```python
def get_renta_fija(instrumento: str | None = None) -> list[dict]
```

Ver `api/services/cotizaciones.py` para shape del return (MarketSnapshot
projected).

**Tests**: cart-003, cart-005, edge-001.

---

### 6.3 `get_forwards` 🟢

**Nombre canónico**: `forwards_por_curva`.

**Signature**:
```python
def get_forwards(curva: Literal["tasa_fija", "cer"] | None = None) -> list[dict]
```

**⚠ Limitación**: hoy no soporta `curva="soberanos"`. Extender junto con
sección 4.1 (`engines/forwards.py` a HD).

**Tests**: fwd-001 (requiere extensión), fwd-002, fwd-003, cart-003, cart-004,
lici-001, lici-004, conn-003, estr-005.

---

### 6.4 `get_historico_trades` 🟢

**Nombre canónico**: `historico_trades`.

Matcheo exacto por ticker (commit `307952c` arregló el regex
table-scan). Usa índice `(ticker, timestamp)`.

**Tests**: edge-001 (lookup histórico de precios).

---

### 6.5 `get_historico_forwards` 🟡

**Estado**: endpoint existe (`/api/cotizaciones/historico/forwards`) +
service handler registrado en `api/agent/service_registry.py`. **Falta
agregarla al TOOLS del agente** — hoy no está expuesta. 10 líneas.

**Tests**: fwd-002.

---

### 6.6 `get_historico_breakevens`, `get_historico_mep`, `get_historico_opciones`, `get_historico_curva`, `get_opciones`, `get_opciones_meta`, `get_badlar`, `get_cer`, `get_dolar`, `get_ultimo_mep`

Todas existen en el service layer y el registry las expone. Algunas ya están
declaradas en `TOOLS` del agente; otras no (el modelo no las ve). Revisar
caso por caso al armar el build.

---

## 7. Tools locales (sin HTTP, sin service)

### 7.1 `consultar_framework_analitico` 🟢

**Endpoint interno**: `__local__:framework`.

Lee `docs/asistente/estrategia.md` y lo devuelve como bloque de texto para
que el modelo aplique el framework de 4 capas.

**Tests**: view-001 (cat 2 del flujo anterior, no está en golden_set.yaml
pero rubric de muchos cart-* implícitamente lo pide).

---

### 7.2 `consultar_catalogo_estrategias(tema)` 🟢

**Endpoint interno**: `__local__:catalogo`.

Lee `docs/asistente/estrategias.md` sección `tema` y la devuelve.

**Tests**: estr-001, estr-002, estr-005.

---

## 8. Orden de build sugerido

Siguiendo frecuencia en golden set + dependencias:

### Fase 1 (semana 1) — Core
1. `api/agent/stats.py` — módulo con helpers de percentile/zscore/classify.
2. `listar_curva` (sec 2.1) — en `api/services/cotizaciones.py`.
3. `obtener_serie_macro` + `clasificar_nivel` (sec 2.2, 2.3) — en
   `api/services/macro.py`.
4. Registrar en `SERVICE_HANDLERS` + `TOOLS` del agente.
5. Agregar al menos 1 test case del golden set pasando (ej. look-001 con
   inflación workaround via IntelDoc).

**Impacto estimado**: ~40% del golden set habilitado.

### Fase 2 (semana 2) — Extensiones engines
6. Convexity en `engines/curvas.py` (sec 4.2).
7. Hard dollar en `engines/curvas.py` (sec 4.1).
8. Hard dollar en `engines/forwards.py` (sec 4.1 dependency).
9. Expose `historico_forwards` como tool (sec 6.5).

**Impacto estimado**: +20% del golden set.

### Fase 3 (semana 3) — Data externa
10. `jobs/inflacion.py` → INDEC (IPC + IPIM).
11. `jobs/rem.py` → BCRA REM.
12. `jobs/riesgo_pais.py` → EMBI.
13. `engines/dolar_ccl.py` → CCL.

**Impacto estimado**: +25% del golden set.

### Fase 4 (semana 4) — Licitaciones
14. Colecciones Licitaciones.Menu + Resultados + proceso carga manual.
15. Tools de sección 5.

**Impacto estimado**: +10% del golden set.

### Fase 5 — Extras
- `snapshot_curva_historico`, `liquidez_secundario`, `valor_relativo_en_curva`.
- Futuros Rofex (sec 4.8).
- Bonos DL cargados (sec 4.10).

**Impacto estimado**: últimos ~5%.

---

## 9. Convenciones para el build

1. **Siempre** en capa de servicios primero, router + registry después.
2. **Siempre** cachear en el service (`@cached(ttl=N)`). El agente se
   beneficia automáticamente.
3. **Signatures kwargs-only** (no positional args). El decorator `@cached`
   los necesita como tupla ordenada.
4. **Tolerar kwargs extra** con `**_ignore`: a veces el LLM manda params
   que no existen. Log warning pero no crashear.
5. **No HTTP dentro del service**. Si necesita data externa, queda
   `data_source=None` y el modelo sabe por `_meta.hint`.
6. **Cada tool nueva = 1 test del golden set pasando**. Sin esto, se
   acumula deuda.

---

## 10. Cómo actualizar este documento

- Al agregar una tool nueva, copiar template de 2.1 / 2.2.
- Al cambiar signature de una existente, bumpear "Versión" en el encabezado
  y anotar breaking change.
- Si una tool queda obsoleta, marcar ~~tachada~~ con link al reemplazo.

**Versión**: 1.0 — 2026-04-20
