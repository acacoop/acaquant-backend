# Biblioteca de estrategias — modelado para los datos de TradingAV

Este archivo es el **catálogo técnico** de estrategias que el asistente puede
construir con los datos disponibles en la base. Para cada una:

- **Outlook**: qué view la justifica.
- **Estructura**: piernas (legs) del trade.
- **Datos necesarios**: punta por punta, apuntando a la tool y el campo exacto.
- **Fórmulas clave**: lo mínimo indispensable para cuantificar P&L / breakeven.
- **Construcción paso a paso**: orden real de tool calls.
- **Aplicable en AR cuando**: condiciones de contexto.
- **Caveats**: qué puede salir mal, qué no podemos asumir.

**Regla de oro**: si falta algún dato de los listados, el asistente no
"estima"; declara la limitación y, si hay, propone un proxy explícito.

---

## Mapa de datos → tools → campos

Antes de modelar una estrategia, el asistente consulta este mapa.

| Dato que se necesita | Tool | Campo / lugar |
|---|---|---|
| Último precio de un bono | `cotizacion_renta_fija(instrumento)` | `metrics.last_price` |
| Precio de cierre del día | `cotizacion_renta_fija(instrumento)` | `metrics.closing_price` |
| Book (bid/offer) y spread | `cotizacion_renta_fija(instrumento)` | `book.bids[0]` / `book.offers[0]` |
| VWAP del día | `cotizacion_renta_fija(instrumento)` | `metrics.vwap` |
| Volumen acumulado del día | `cotizacion_renta_fija(instrumento)` | `metrics.total_money` |
| **TEA actual de un bono** | `historico_trades(instrumento)` | último doc: `TEA` |
| **TEM actual (solo tasa fija)** | `historico_trades(instrumento)` | último doc: `TEM` |
| **Duration actual (años)** | `historico_trades(instrumento)` | último doc: `duration` |
| **Paridad CER (solo CER)** | `historico_trades(instrumento)` | último doc: `paridad` |
| Serie diaria por curva completa | `historico_curva(curva)` | `[{fecha, ticker, price, TEA, TEM, duration, paridad}]` |
| Matriz forwards NxN | `forwards_por_curva(curva)` | matriz de forwards implícitos |
| Breakevens Lecap↔CER vigentes | `breakevens_actuales()` | `[{lecap, cer, breakeven, ...}]` |
| Histórico breakevens | `historico_breakevens(desde, hasta)` | serie diaria |
| CER índice BCRA | `serie_cer(desde, hasta)` | `[{fecha, valor}]` |
| BADLAR | `serie_badlar(desde, hasta)` | `[{fecha, valor}]` |
| Dólar A3500 | `serie_dolar_a3500(desde, hasta)` | `[{fecha, valor}]` |
| Dólar MEP último | `mep_actual()` | `{mep, timestamp}` |
| Dólar MEP serie | `historico_mep(desde, hasta)` | serie intradía |
| Metadata de un bono | `metadata_activos(ticker, emisor, clase_activo)` | `{VENCIMIENTO, CLASE_ACTIVO, EMISOR, CALIFICACION, ...}` |
| Cronograma de flujos | `flujos_titulo(ticker, curva, moneda_flujo)` | amortizaciones + cupones |
| Opción GGAL con greeks | `cotizacion_opciones(instrumento, tipo)` | `{bid, offer, delta, gamma, vega, theta, iv, strike, vence, spot}` |
| Spot GGAL | `cotizacion_opciones` | cualquier opción → `spot` |

**NO HAY**: riesgo país / EMBI, licitaciones (rollover, bid-to-cover), REPO BCRA
stock intradía, TAMAR mayorista directo (solo implícita en duales), inflación
IPC histórica (solo vía CER), futuros ROFEX de dólar (sin endpoint directo).

**Campos calculados (tienen lag ~5s sobre el trade real)**: `TEA`, `TEM`,
`duration`, `paridad`. Provistos por `engines.curvas`. Solo existen para tickers
que están en `Trading.Curvas`.

---

# 1. FIXED INCOME — Posiciones de curva

## 1.1 Bullet

**Outlook**: view fuerte sobre un punto específico de la curva. Si espera
compresión, va a tramo más largo. Si espera empinamiento, más corto.

**Estructura**: concentrar capital en bonos con el MISMO vencimiento (o ±30 días).

**Datos necesarios**:
- `metadata_activos()` con filtro por `emisor` y tramo deseado → candidatos.
- `cotizacion_renta_fija(ticker)` para cada candidato → validar liquidez (spread
  y total_money del día).
- `historico_trades(ticker)` → último doc: TEA, duration.

**Fórmulas**:
- Duration del bullet = duration del bono elegido (trivial).
- Yield del bullet = TEA del bono elegido.

**Construcción**:
1. Llamar `metadata_activos()` para listar bonos candidatos por vencimiento.
2. Para el top 2-3 candidatos por liquidez, `historico_trades()` cada uno.
3. Elegir el que tenga mejor carry ajustado por spread.

**Aplicable en AR cuando**:
- Hay un bono con volumen abundante en el tramo deseado (típicamente la Lecap
  o Boncer de una licitación reciente).

**Caveats**:
- Un bullet concentra riesgo idiosincrático del emisor (aplica poco en AR para
  Tesoro, pero sí para ONs).
- No tiene convexidad extra; un barbell de misma duration convexa más.

---

## 1.2 Barbell

**Outlook**: la curva podría tener shifts no-paralelos (twist); se quiere más
convexidad que un bullet de misma duration.

**Estructura**: long `w1` en tramo corto (T1) + long `w2` en tramo largo (T2),
evitando el tramo medio T* = (T1+T2)/2.

**Datos necesarios**:
- Duration y TEA de dos bonos: uno corto, uno largo → `historico_trades(ticker)`.
- Precios actuales → `cotizacion_renta_fija(ticker)`.

**Fórmulas**:
```
Dado D_target (duration objetivo), D1 (corto), D2 (largo):
w1 + w2 = 1
w1·D1 + w2·D2 = D_target
Resolviendo: w1 = (D2 - D_target) / (D2 - D1), w2 = 1 - w1

Convexity del barbell:
C_barbell = w1·T1² + w2·T2²  (zero-coupon aprox.)
Convexity del bullet mismo D:
C_bullet ≈ T*² = D_target²
Exceso de convexity: C_barbell - C_bullet = w1·w2·(T2-T1)²
```

**Construcción**:
1. Decidir D_target (ej: 0.5 años).
2. `historico_curva('tasa_fija')` último día → listar Lecaps con durations variadas.
3. Elegir T1 corto (ej: duration 0.15) y T2 largo (ej: duration 1.2).
4. Calcular w1, w2.
5. Validar liquidez de ambos con `cotizacion_renta_fija`.

**Aplicable en AR cuando**:
- Se espera volatilidad en la curva (caución saltando, licitaciones fuertes),
  pero sin view direccional claro sobre el tramo medio.
- Tesis típica del libro reciente: "barbell HD corto (AO27) + HD largo (GD35)"
  como forma de ganar carry corto y convexidad larga.

**Caveats**:
- El barbell cobra menos yield que el bullet de igual D (es el precio de la
  convexidad extra).
- Si la curva se aplana en forma paralela, bullet y barbell se comportan igual.

---

## 1.3 Ladder

**Outlook**: se quiere una posición de duration estable en el tiempo sin tener
que rebalancear mucho, con ingresos de cupones/amortizaciones escalonados.

**Estructura**: capital repartido aproximadamente en partes iguales entre n
bonos con vencimientos equidistantes (Ti+1 = Ti + δ).

**Datos necesarios**:
- `metadata_activos(emisor='Tesoro')` filtrado por tipo → listar bonos por
  vencimiento.
- `flujos_titulo(ticker)` para conocer fechas de cupones.
- Durations individuales → `historico_trades(ticker)`.

**Fórmulas**:
- Duration del ladder ≈ promedio de durations de los bonos.
- Duration promedio ≈ T_medio/2 para zero-coupon equi-espaciados.

**Construcción**:
1. `metadata_activos()` → listar todas las Lecaps disponibles por vto.
2. Elegir n = 6-12 vencimientos equi-espaciados (ej: mensuales los próximos 12 meses).
3. Asignar 1/n del capital a cada uno.

**Aplicable en AR cuando**:
- El inventario de Lecaps cubre 10+ vencimientos correlativos (actualmente sí).
- Estrategia defensiva cuando no hay view claro de curva.

**Caveats**:
- Transacciones de renovación (roll) generan costo de spread.
- En AR las amortizaciones se pueden reinvertir a tasa distinta — riesgo de
  reinversión (como dice el libro).

---

# 2. FIXED INCOME — Butterflies (trades de curvatura)

**Concepto común**: tres bonos con maturities T1 < T2 < T3. Wings son T1 y T3,
body es T2. El trade apuesta a cambios en la CURVATURA de la curva, no a shifts
paralelos.

## 2.1 Butterfly Dollar-Duration-Neutral (zero-cost)

**Outlook**: curva va a cambiar CURVATURA pero no nivel. Típicamente: "el belly
está barato/caro respecto a los wings".

**Estructura**: long `P1` en wing corto + long `P3` en wing largo + short
`P2` en body, con restricciones:
```
P1 + P3 = P2         (dollar-neutral → zero cost)
P1·D1 + P3·D3 = P2·D2 (dollar-duration-neutral → inmune a shift paralelo)
```

**Datos necesarios**:
- Duration de los 3 bonos → `historico_trades(ticker)` (cada uno).
- Precios actuales → `cotizacion_renta_fija(ticker)`.

**Fórmulas** (asumiendo P2 dado):
```
P1 = P2 · (D2 - D3) / (D1 - D3)
P3 = P2 · (D1 - D2) / (D1 - D3)
```

**Construcción**:
1. Decidir la curva (`tasa_fija` o `cer`).
2. `historico_curva(curva)` último día → ordenar bonos por duration.
3. Elegir T1 (corto), T2 (medio), T3 (largo) — idealmente equi-distantes en duration.
4. Leer durations exactas.
5. Calcular P1, P3 en función de P2 (el tamaño del body define la escala).

**Aplicable en AR cuando**:
- Se observa que el tramo medio (ej: Lecaps de Jul-Ago 2026) está caro respecto
  a los wings (Abr-May y Oct-Nov).
- Post-licitación: la Lecap nueva del body está "fuera de curva" rica → short body.

**Caveats**:
- Inmune a shifts paralelos, NO a cambios de pendiente.
- Requiere que los 3 bonos operen en el mismo horizonte de liquidez (si uno
  no opera por un día, el trade queda descalzado).

---

## 2.2 Butterfly Fifty-Fifty (neutral curve)

**Outlook**: similar a 2.1 pero además se quiere inmunidad a flattening/
steepening pequeños. No es zero-cost.

**Estructura**: los dollar-durations de los dos wings son iguales e iguales a
la mitad del body:
```
P1·D1 = P3·D3 = (1/2)·P2·D2
```

**Datos necesarios**: mismos que 2.1.

**Fórmulas** (asumiendo P2):
```
P1 = P2·D2 / (2·D1)
P3 = P2·D2 / (2·D3)
```

**Construcción**: idéntica a 2.1 pero con fórmulas distintas para P1, P3.

**Aplicable en AR cuando**:
- Se quiere aislar CURVATURA de los dos tipos de twist (flattening entre wing
  corto y body, y steepening entre body y wing largo).

**Caveats**:
- NO es zero-cost: la suma de pesos ≠ 0, hay que poner/liberar capital.

---

## 2.3 Butterfly Regression-Weighted

**Outlook**: los wings tienen volatilidades distintas (wing corto suele ser más
volátil que largo). Se quiere neutralidad "real" ante twist, ponderando por β.

**Estructura**:
```
P1·D1 + P3·D3 = P2·D2  (duration-neutral)
P1·D1 = β · P3·D3      (β = β_regression estimado historicamente)
```

**Datos necesarios**:
- `historico_trades` de los 3 bonos, ventana 60-180 días.
- Calcular series de spread: s1(t) = TEA_body - TEA_wing_corto, s2(t) = TEA_wing_largo - TEA_body.
- Regresión: s1 ~ β · s2 → estimar β.

**Construcción**:
1. Extraer series diarias de TEAs de los 3 bonos (`historico_curva`).
2. Calcular spreads diarios.
3. Regresión OLS → β.
4. Aplicar fórmulas para P1, P3 con β.

**Aplicable en AR cuando**:
- Hay al menos 60-90 días de historia para los 3 bonos (riesgo: bonos recién
  emitidos no sirven).

**Caveats**:
- β es inestable. Rebalancear mensualmente.
- Muy sensible a outliers en la ventana de regresión.

---

## 2.4 Butterfly Maturity-Weighted

**Outlook**: como 2.3 pero sin tener que correr regresión. β se deriva de las
maturities.

**Estructura**: β = (T2 - T1) / (T3 - T2).

**Datos necesarios**: solo maturities (fecha_vencimiento de `metadata_activos`)
y durations (`historico_trades`).

**Construcción**: como 2.3 pero con β de fórmula.

**Aplicable en AR cuando**:
- No hay historia suficiente para regression-weighted (bonos nuevos).

**Caveats**:
- Asume que la volatilidad de los spreads escala linealmente con el tenor. No
  siempre cierto pero razonable como proxy.

---

# 3. FIXED INCOME — Carry, roll-down y factores

## 3.1 Carry Factor (ranking por carry)

**Outlook**: bonos con mayor carry tienden a outperformar en horizontes cortos
si la curva no cambia.

**Estructura**: long top-decile por carry, short bottom-decile.

**Fórmula de carry** (para zero-coupon aprox.; el libro lo detalla en Eq. 5.41):
```
Carry(t, t+Δt, T) = R(t, T)·Δt + C_roll(t, t+Δt, T)

donde C_roll ≈ -ModD(t, T) · [R(t, T-Δt) - R(t, T)]

Simplificación práctica para bono con TEA:
Carry_mensual ≈ TEM + roll_mensual
roll_mensual = -D_mod · ΔTEA_por_mes_en_curva
```

**Datos necesarios**:
- Para cada bono: TEA actual, TEM actual, duration → `historico_trades(ticker)`.
- Para estimar roll: TEA del bono con vto 1 mes antes (proxy de R(t, T-Δt))
  o pendiente local de la curva → `historico_curva(curva)` para armar la curva
  completa.

**Construcción**:
1. `historico_curva('tasa_fija')` hoy → lista de bonos con TEA, TEM, duration.
2. Para cada bono i: estimar slope local ≈ (TEA_{i-1} - TEA_i) / (T_{i-1} - T_i).
3. Carry_mensual_i = TEM_i + ( - D_i · slope_local · (1/12) ).
4. Ordenar, top/bottom quintile.

**Aplicable en AR cuando**:
- La curva tiene al menos 8-10 puntos líquidos (sí, tanto tasa fija como CER).

**Caveats**:
- El carry es un proxy de retorno solo si la curva NO cambia. No es predicción.
- En AR con caución volátil, el "estar en curva" puede cambiar día a día.

---

## 3.2 Rolling Down the Yield Curve

**Outlook**: la curva tiene un tramo muy empinado. Comprar ahí y dejar que el
bono "baje" la curva hacia tramos más planos con el paso del tiempo.

**Estructura**: long en bono de duration media-larga cuyo punto en la curva
está en el segmento más empinado.

**Datos necesarios**:
- `forwards_por_curva(curva)` → matriz de forwards, identifica el par de vtos
  con mayor forward implícito = tramo más empinado.
- Duration de los bonos en ese tramo → `historico_trades`.

**Construcción**:
1. `forwards_por_curva('tasa_fija')` → buscar el par (T1, T2) con máximo
   forward implícito.
2. Seleccionar el bono que esté en el tramo más alto (más cerca de T2).
3. Validar liquidez.

**Aplicable en AR cuando**:
- El spread de forwards entre dos tramos es > 300-500 bps (curva muy empinada).
- Típico en zonas donde el mercado teme eventos puntuales (ej: post-mandato).

**Caveats**:
- El "roll-down" asume que la curva queda quieta. Si se desplaza en su conjunto,
  el efecto roll se diluye.

---

## 3.3 Yield Curve Spread (Flatteners y Steepeners)

**Outlook**: view direccional sobre la PENDIENTE de la curva.
- **Flattener**: se espera que la pendiente baje (corto sube más que largo o
  viceversa según qué esté driving).
- **Steepener**: se espera que la pendiente suba.

**Estructura**:
- **Flattener**: short front leg (corto), long back leg (largo).
- **Steepener**: long front, short back.
- Ponderar para que las dollar-durations se cancelen (inmune a shift paralelo).

**Datos necesarios**:
- Dos bonos, uno corto y uno largo, misma curva.
- Duration y precio de ambos.

**Fórmulas** (dollar-duration-matched):
```
Para N1 bonos cortos y N2 bonos largos:
N1 · P1 · D1 = N2 · P2 · D2
```

**Construcción**:
1. Elegir T1 (corto) y T2 (largo) — ej: Lecap Ene 2026 y Boncap Ene 2027.
2. Leer D1, D2.
3. Calcular ratio N1/N2.

**Aplicable en AR cuando**:
- Hay catalyst conocido para la pendiente (ej: inminente ciclo de baja de tasas
  → steepener por los cortos comprimiendo más).

**Caveats**:
- Si la curva se flattens PARALELAMENTE (ambos tramos bajan pero el corto más),
  la cobertura de duration no protege.

---

## 3.4 Value Factor (residual de credit spread)

**Outlook**: algunos bonos cotizan "caros" o "baratos" relativo a lo que
predeciría su rating + maturity.

**Estructura**: long top-decile por residual (bonos baratos vs. fair value).

**Fórmula** (regresión cross-section):
```
S_i = Σ β_r · I_{i,r} + γ · T_i + ε_i
S*_i = S_i - ε_i  (fair value)
V_i = S_i / S*_i - 1  (value factor)
```

**Datos necesarios**:
- Para cada bono: TEA y rating → `metadata_activos` (CALIFICACION) + último
  TEA → `historico_trades`.
- Risk-free rate en AR: usamos TAMAR implícita o BADLAR como proxy.

**Construcción**:
1. Armar lista de bonos del universo (ej: todas las ONs + soberanos ley local).
2. Cross-section: S_i = TEA_i - TAMAR_proxy, I_{i,r} dummies de rating.
3. OLS → calcular ε_i, V_i.
4. Top y bottom quintile.

**Aplicable en AR cuando**:
- Se usa sobre ONs (donde hay diversidad de calificaciones). En soberanos puro
  no aplica (todos son Argentina).

**Caveats**:
- En AR la "calificación" no es un predictor confiable, muchas ONs cotizan por
  nombre más que por rating.
- Necesita al menos 15-20 bonos para que la regresión tenga grados de libertad.

---

## 3.5 Low-Risk Factor

**Outlook**: bonos con menor riesgo (rating alto + maturity corta) tienden a
outperform en riesgo ajustado.

**Estructura**: long portfolio de bonos en top decile por (rating + maturity).

**Datos necesarios**: `metadata_activos` con CALIFICACION y VENCIMIENTO.

**Aplicable en AR cuando**:
- Se trabaja con ONs. En soberanos puros el factor colapsa (todos mismo rating).

**Caveats**:
- Marginal en AR dada la homogeneidad de rating soberano.

---

# 4. INFLATION — traducción de inflation swaps al mercado AR

## 4.1 Inflation Swap sintético: Lecap + CER del mismo vto

**Insight clave**: un zero-coupon inflation swap del libro (fixed K vs floating
CPI) es conceptualmente equivalente a:
- **Short Lecap + Long Boncer del mismo vto + rebalance** (paga fixed, recibe
  inflación).
- O al revés (paga inflación, recibe fixed) = **Long Lecap + Short Boncer**.

El **fixed rate K del swap = breakeven implícito** entre Lecap y CER.

**Outlook**:
- Si **inflación esperada > breakeven implícito** → long CER, short Lecap
  (equivalente a comprar inflation swap).
- Si **inflación esperada < breakeven implícito** → long Lecap, short CER.

**Datos necesarios**:
- `breakevens_actuales()` → breakeven por par (lecap, cer) + `mes_inflacion` (último IPC publicado).
- `historico_breakevens()` → cómo se movió el breakeven.
- `rem_expectativas()` → consenso de analistas para los meses futuros.
- CER actual → `serie_cer()` último valor.

**Fórmula del breakeven** (del engine):
```
retorno_lecap = (1 + TEM)^(días/30) - 1
inflación_implicita = (1 + retorno_lecap) × (paridad_cer/100) - 1
breakeven_mensual = (1 + inflación_implicita)^(30/días) - 1
```

**Construcción**:
1. `breakevens_actuales()` → ver pares vigentes (cada par trae `mes_inflacion`
   = último IPC publicado).
2. `rem_expectativas()` → consenso de analistas para el mes que el par price.
3. Si breakeven < REM → long CER del par, short Lecap del par (dollar-duration
   matched). Si breakeven > REM y > realizada reciente → simétrico inverso.

**Aplicable en AR cuando**:
- Hay un par (Lecap, CER) con vencimientos muy cercanos (±60 días, filtro del
  engine).

**Caveats**:
- El CER de liquidación se mueve con rezago de 10 días hábiles respecto al
  settlement.
- Si el cupón del Boncer es cero (zero coupon puro, ej. TZX26), el paridad usa
  solo principal; si tiene cupones, hay que ajustar.
- **No confundir**: este breakeven es el de INFLACIÓN, no el de FX.

---

## 4.2 TIPS-Treasury Arbitrage (traducido: Lecap sintético vs Lecap real)

**Insight**: el libro muestra que empíricamente los TIPS están baratos vs
Treasury (el sintético Lecap = CER + short inflation swap cuesta menos que la
Lecap real).

En AR, el análogo directo es:
- **Lecap sintética** = Long Boncer + Short Inflation swap = Long Boncer +
  (Short CER devengado).
- Como el inflation swap no existe como producto, se emula con el par Lecap/CER
  del mismo vto.

**Estructura empírica en AR**:
- Si `paridad_CER_cotizada × (1 + breakeven)^N > precio_Lecap_mismo_vto` →
  el CER está "caro" vs sintético.
- Si `paridad_CER_cotizada × (1 + breakeven)^N < precio_Lecap_mismo_vto` → el
  CER está "barato" → **arbitraje: long CER, short Lecap, dollar-duration
  matched**.

**Datos necesarios**:
- Precio Lecap, precio Boncer del mismo vto.
- CER emisión y CER actual → `serie_cer()`.
- Flujos de ambos → `flujos_titulo(ticker)`.

**Construcción**:
1. Identificar par de mismo vto.
2. Calcular valor sintético del Lecap (cash flow bootstrap con CER proyectado).
3. Comparar con precio real de Lecap.
4. Ejecutar dollar-duration-matched si la diferencia supera costos + transacción.

**Aplicable en AR cuando**:
- Par existe con vtos casi idénticos (difícil, requiere licitaciones coordinadas).
- El spread excede 30-50 bps (por encima de costos).

**Caveats**:
- En AR el "costo de transacción" es alto. Muchos "arbitrajes" se evaporan con
  spread bid-ask.
- **Idéntico en espíritu al simulador breakevens** que ya existe en el sistema.

---

# 5. FX / CARRY — Carry trade sintético

## 5.1 Carry trade sintético Lelink + Short ROFEX

**Outlook**: la tasa en pesos (TEM) está por encima de lo que justifica la
depreciación esperada del peso según A3500 proyectado por ROFEX.

**Estructura (jerga local: "sintético")**:
- **Long Lelink del BCRA** (recibe ajuste DL + tasa).
- **Short futuro ROFEX de dólar** (paga A3500 al vto).
- Neto: recibe tasa en pesos efectiva.

**Equivalencia con el libro** (Eq. 8.4-8.5, UIRP/CIRP):
```
F(t, T) = S(t) · (1 + r_d) / (1 + r_f)

Si el mercado de futures cotiza F' distinto, hay profit/pérdida implícito.
```

**Datos necesarios**:
- A3500 actual → `serie_dolar_a3500()` último valor.
- MEP actual → `mep_actual()`.
- Forward ROFEX → **NO DISPONIBLE** como endpoint directo.
- Lelink TEA → `historico_trades(ticker_lelink)`.

**Construcción**:
1. Leer A3500 y Lelink TEA.
2. (Falta feed de ROFEX; proxy: asumir forward = A3500 × (1 + TEM_lecap_equiv)).
3. Calcular retorno neto.

**Aplicable en AR cuando**:
- La brecha implícita en ROFEX vs Lelink es significativa (y hay datos).

**Caveats**:
- **Feed de ROFEX futures no está en base** (limitación crítica). El asistente
  debe declararlo y no estimar.

---

## 5.2 Decisión HD vs DL según brecha MEP-A3500

**Ver `estrategia.md` sección "Brecha MEP vs A3500"**. Este es el trade más
directo que replica la lógica del libro sin necesidad de futures:

- Brecha < 1% → rotar DL → HD.
- Brecha 1-5% → neutral.
- Brecha > 5% → DL atractivo para cobertura.

**Datos**:
- `mep_actual()` → MEP.
- `serie_dolar_a3500()` último → A3500.
- Brecha = (MEP / A3500) - 1.

---

# 6. OPTIONS — Estrategias GGAL

Todas las estrategias abajo se construyen con `cotizacion_opciones(instrumento,
tipo)`, que devuelve: bid, offer, last, strike, vence, delta, gamma, vega,
theta, iv, spot. Los payoffs al vto se calculan con las fórmulas estándar del
libro (Cap. 2).

**Notación**: S0 = spot GGAL, ST = spot al vto, K = strike, C = net credit, D =
net debit, H = D (si debit) o -C (si credit).

## 6.1 Estrategias BULLISH (espera GGAL sube)

### Covered Call (buy-write)
- **Estructura**: long stock + short call strike K ≥ S0 (OTM).
- **Outlook**: neutral-a-bullish. Income por prima.
- **Payoff**: f_T = ST - S0 - max(ST - K, 0) + C
- **Break-even**: S* = S0 - C
- **Pmax**: K - S0 + C (si ST ≥ K)
- **Lmax**: S0 - C (si ST = 0)
- **Cuando usarla**: si la mesa tiene GGAL en cartera y espera lateralización.

### Protective Put (married put / synthetic call)
- **Estructura**: long stock + long put strike K ≤ S0.
- **Outlook**: bullish con hedge. Paga prima por cobertura.
- **Payoff**: f_T = ST - S0 + max(K - ST, 0) - D
- **Break-even**: S* = S0 + D
- **Pmax**: unlimited
- **Lmax**: S0 - K + D
- **Cuando**: convicción bullish pero quiere piso. Costoso cuando IV alta.

### Bull Call Spread
- **Estructura**: long call K1 (ATM) + short call K2 > K1 (OTM).
- **Outlook**: bullish moderado. Capital gain con límite.
- **Payoff**: f_T = max(ST - K1, 0) - max(ST - K2, 0) - D
- **Break-even**: S* = K1 + D
- **Pmax**: K2 - K1 - D (si ST ≥ K2)
- **Lmax**: D (si ST ≤ K1)
- **Cuando**: view bullish acotado, quiere limitar prima pagada.

### Bull Put Spread
- **Estructura**: long put K1 (OTM) + short put K2 > K1 (también OTM).
- **Outlook**: bullish. Income por prima neta.
- **Payoff**: f_T = max(K1 - ST, 0) - max(K2 - ST, 0) + C
- **Break-even**: S* = K2 - C
- **Pmax**: C (si ST ≥ K2)
- **Lmax**: K2 - K1 - C
- **Cuando**: view bullish, quiere cobrar prima en vez de pagar.

### Long Synthetic Forward
- **Estructura**: long call ATM + short put ATM (mismo K = S0).
- **Outlook**: bullish fuerte, emula un long forward/futuro.
- **Payoff**: f_T = ST - K - H (lineal).
- **Cuando**: no hay futuros disponibles, se quiere exposición directional pura.

### Long Combo (long risk reversal)
- **Estructura**: long call OTM K1 + short put OTM K2 (K1 > K2).
- **Outlook**: bullish.
- **Cuando**: capturar skew (puts OTM ricos vs calls OTM baratos).

### Bull Call Ladder
- **Estructura**: long call K1 ATM + short call K2 OTM + short call K3 > K2.
- **Outlook**: bullish moderado con piso de vol baja.
- **Caveat**: Lmax = unlimited si ST supera K3 (el segundo short call sin cubrir).

### Diagonal Call Spread
- **Estructura**: long call deep ITM K1 con TTM T' largo + short call K2 > K1
  con TTM T corto.
- **Outlook**: bullish, quiere generar income con la short call corta mientras
  mantiene la ITM larga como proxy de stock.

### Call Ratio Backspread
- **Estructura**: short N_S calls ATM K1 + long N_L calls OTM K2 (N_L > N_S).
- **Outlook**: bullish fuerte.
- **Pmax**: unlimited.
- **Lmax**: N_S · (K2 - K1) + H.

### Strap
- **Estructura**: long 2 calls ATM + long 1 put ATM, mismo K.
- **Outlook**: bullish con expectativa alta de vol.
- **Cuando**: espera movimiento grande, más probabilidad al alza que a la baja.

## 6.2 Estrategias BEARISH (espera GGAL baja)

Simétricas a las bullish. Las listo con sus datos clave.

### Covered Put (sell-write)
- Short stock + short put K ≤ S0.
- Pmax: S0 - K + C | Lmax: unlimited.

### Protective Call
- Short stock + long call K ≥ S0.
- Pmax: S0 - D | Lmax: K - S0 + D.

### Bear Put Spread
- Long put K1 ATM + short put K2 < K1 OTM.
- Pmax: K1 - K2 - D | Lmax: D.

### Bear Call Spread
- Long call K1 OTM + short call K2 < K1 OTM.
- Pmax: C | Lmax: K1 - K2 - C.

### Short Synthetic Forward
- Short call ATM + long put ATM.
- Emula short forward/futuro.

### Short Combo / Short Risk Reversal
- Long put OTM K1 + short call OTM K2 (K2 > K1).
- Lmax: unlimited.

### Bear Call Ladder / Bear Put Ladder
- Variantes simétricas de los bull ladders.

### Diagonal Put Spread
- Long put ITM largo + short put OTM corto.

### Put Ratio Backspread
- Short N_S puts ATM + long N_L puts OTM (N_L > N_S). Bearish fuerte.

### Strip
- Long 2 puts ATM + long 1 call ATM. Bearish con vol alta.

## 6.3 Estrategias NEUTRAL / SIDEWAYS (GGAL en rango)

### Short Straddle
- **Estructura**: short call ATM + short put ATM, mismo K.
- **Payoff**: f_T = -max(ST - K, 0) - max(K - ST, 0) + C
- **Pmax**: C (si ST = K) | **Lmax**: unlimited.
- **Cuando**: convicción fuerte de rango, vol alta (prima gorda).

### Short Strangle
- **Estructura**: short call OTM K1 + short put OTM K2 (K2 < K1).
- **Pmax**: C | **Lmax**: unlimited (ambos lados).
- **Cuando**: menos agresivo que straddle, más margen.

### Short Guts
- Idéntico a short strangle pero con strikes ITM.
- **Lmax**: unlimited | **Pmax**: C - (K2 - K1).

### Long Call / Put Butterfly
- **Estructura**: long K1 + short 2×K2 (ATM) + long K3 (strikes equidistantes).
- **Pmax**: κ - D (donde κ = K2 - K1 = K3 - K2) | **Lmax**: D.
- **Cuando**: view neutral pero con limite de pérdida definido (mejor que short
  straddle si no quiere riesgo ilimitado).

### Modified Butterfly (con bias)
- Strikes NO equidistantes, introduce bias bullish o bearish.

### Iron Butterfly ("long"/"short")
- Straddle + strangle de signo contrario. Combina income con pérdida limitada.

### Long Call / Put Condor
- Long K1 + short K2 + short K3 + long K4 (K1 < K2 < K3 < K4).
- Extensión del butterfly con "meseta" de profit más ancha.
- Pmax: K2 - K1 - D | Lmax: D.

### Long / Short Iron Condor
- Short call vertical + short put vertical con strikes alejados del ATM.
- Income strategy para rango más amplio que iron butterfly.

### Long Box
- Bull call spread + bear put spread con los mismos strikes.
- Perfectamente flat P&L = diferencia de strikes. Arbitraje si el precio total
  difiere del valor presente.

### Collar
- Long stock + long put OTM + short call OTM.
- Cap superior + piso inferior. Típico cobertura zero-cost.

### Calendar Call / Put Spread
- Long option largo + short option corto, mismo strike.
- Profit by theta decay del corto > theta del largo. Sideways bet.

## 6.4 Estrategias VOLATILIDAD (GGAL se mueve fuerte, dirección incierta)

### Long Straddle
- Long call ATM + long put ATM.
- **Pmax**: unlimited | **Lmax**: D.
- **Break-evens**: S*up = K + D, S*down = K - D.
- **Cuando**: evento binario inminente (ej: paquete económico, anuncio).

### Long Strangle
- Long call OTM + long put OTM. Más barato que straddle pero requiere movimiento
  mayor para breakeven.

### Long Guts
- Long call ITM + long put ITM. Más caro, mayor piso (intrinseco).

### Short Call / Put Butterfly
- **Estructura**: short K1 + long 2×K2 (ATM) + short K3.
- **Pmax**: κ - |H| | **Lmax**: C o D según estructura.
- Bet a que GGAL se mueve fuera de [K1, K3].

### Long Call / Put Synthetic Straddle
- Equivalente via stock + 2 opciones. Para cuando no se pueden combinar
  derivadas puras.

## 6.5 Notas generales opciones AR

- **Liquidez**: GGAL opciones tienen liquidez suficiente en ATM ± 2 strikes, y
  vencimientos próximos (1-3 meses). Profundidad cae rápido fuera de eso.
- **Comisiones y arancel**: un porcentaje no despreciable, los spreads calcados
  de los ejemplos del libro a veces no son rentables en ARS.
- **Dividendos**: GGAL paga dividendos; las opciones lo incorporan via spot -
  PV(div). No recalcules la paridad put-call sin esto.

---

# 7. MACRO — Framework de momentum (conceptual)

## 7.1 Fundamental Macro Momentum (traducido al framework de la mesa)

El libro propone 4 state variables cross-country. En una ALYC single-country,
el concepto se traduce a **regímenes internos** y mapea directo al framework
de 4 capas del usuario (ver `estrategia.md`):

| State variable del libro | Traducción al framework local |
|---|---|
| Business cycle (GDP + CPI) | Capa 3: economía real vs. financiera (riesgo K) |
| International trade (FX vs basket) | Capa 1: régimen cambiario (tríada REPO/compras/canje) |
| Monetary policy (short-term rate) | Capa 1: REPO y tasa overnight |
| Risk sentiment (equity excess) | Capa 3: Merval en USD vs riesgo país |

**El algoritmo**: en vez de rankear países, rankear **asset classes internos**
(CER, Tasa Fija, Duales, HD, DL) según cómo se comportan en el régimen actual.

**Datos necesarios**: lo que ya está en el framework de 4 capas — no es una
estrategia mecánica con fórmula cerrada sino el modo de pensar estructurado.

## 7.2 Pass-through de inflación (HI → CI)

El libro (Eq. 19.1) sugiere asignar commodities según spread HI-CI. En AR **no
tenemos HI ni CI como data estructurada** (solo CER que proxea el ex-post).

**Aplicación indirecta**: comparar **inflación núcleo implícita** (via
breakevens de Lecaps cortas vs CER cortas) con la **inflación esperada por REM**
(`rem_expectativas()`). Si el spread se amplía, puede indicar presión
inflacionaria no incorporada → sesgar CER.

---

# 8. ESTRATEGIAS QUE NO APLICAN (y por qué)

| Estrategia del libro | Por qué no aplica |
|---|---|
| CDS Basis Arbitrage | No hay mercado de CDS sobre emisores AR en la base. |
| Swap-Spread Arbitrage (LIBOR) | No hay mercado de interest rate swaps en AR que tengamos en base. |
| VIX Futures Basis / Vol ETN carry | No hay VIX local en la base, no hay ETNs de vol. |
| Variance Swaps | No hay mercado de variance swaps en AR. |
| Dispersion Trading (index vs single names) | Opciones de MERVAL individual no son líquidas. |
| Pairs Trading de stocks | MERVAL tiene pocos nombres líquidos con correlación estable. |
| Sector Momentum Rotation (ETFs) | No hay ETFs sectoriales en AR. |
| Commodity strategies (roll yields, hedging pressure) | No hay feed de commodities AR en la base. |
| Structured Assets (CDO tranches, MBS) | No hay mercado visible de ABS/CDO AR. |
| Tax Arbitrage | Específico legislación US (munis, cross-border). |
| Distressed Investing | Requiere data de bonos en default con procesos; no modelado. |
| Real Estate (REITs, fix-and-flip) | No aplica al scope. |
| Cryptocurrency (ANN / sentiment) | Fuera del scope ACA Valores. |
| Cross-country Carry Trade | Single currency (ARS); no tiene sentido. |
| Triangular FX Arbitrage | No tenemos feed de cross-rates en tiempo real. |
| FOMC Announcement Trading | Data de anuncios no está en la base. |
| Global Fixed Income cross-country | No hay data cross-country. |
| Bond Immunization | Requiere liability conocida de cliente (fuera de alcance). |
| Weather / Energy derivatives | Fuera del scope. |

---

# 9. REGLA DE CONSTRUCCIÓN PARA EL ASISTENTE

Cuando un usuario pregunte por una estrategia:

1. **Identificar outlook**: el usuario busca dirección / curvatura / vol /
   carry / arb?
2. **Matchear con sección**: buscar la estrategia del catálogo.
3. **Verificar datos**: recorrer la sección "Datos necesarios" y consultar
   CADA tool listada. Si alguna falta → declarar limitación.
4. **Aplicar fórmulas**: usar las fórmulas de la sección, con números reales.
5. **Chequear contra 4 capas** (ver `estrategia.md`): ¿el régimen actual
   valida la estrategia? ¿cómo se comporta en escenario de error?
6. **Sanity checks**: cuantificar asimetría, articular escenario de error,
   detectar si el mercado price algo que no vemos.
7. **Entregar**: estructura + fórmulas + números + caveats.

**Nunca inventar datos que faltan. Nunca proponer una estrategia que requiere
feeds que no tenemos. Si la estrategia ideal no es construible, proponer el
proxy más cercano y declararlo.**
