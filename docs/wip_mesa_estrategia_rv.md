# WIP — Mesa de Estrategia (Renta Variable)

> **Doc de diseño.** La feature NO está construida — esto es el spec funcional
> para construirla. Estado: brainstorm cerrado el 2026-05-18. Próximo paso:
> Paso 0 (el motor). No hay código todavía.

## Objetivo

Una tab nueva dentro del módulo **Renta Variable** que sirva para **armar y
analizar trades y carteras con métricas calculadas automáticamente**, tanto a
nivel de un trade individual como de un portfolio entero. Tiene que servir
para **ganar plata**: mejores trades con riesgo controlado, hedges baratos y
bien medidos, carteras profesionales para clientes.

Dos usuarios: la **mesa de dinero** (book propio, prop) y **sales** (carteras
de clientes).

## Concepto

**Una herramienta, 4 lentes sobre un mismo motor** — no 4 pantallas sueltas.
Referencias de las que se roban ideas: Portfolio Visualizer (optimización),
Arcana (hedge screens, exposición, crowding), Trade Ideas (scanner que empuja
ideas), IBKR PortfolioAnalyst (riesgo consolidado), Composer (constructor
visual). Ninguna conoce el mercado argentino — ahí está la ventaja.

Todo el análisis corre sobre **precio del activo en USA** (el subyacente USD,
`Trading.PreciosAcciones`), no sobre el precio del CEDEAR en ARS. La serie es
limpia.

## El motor compartido (Paso 0 — base de los 4 módulos)

| Pieza | Estado |
|---|---|
| Universo ~70 activos USD + retornos diarios | Existe (`api/services/scanner.py`, `acciones_retornos`) |
| Betas / alpha / corr vs SPY-QQQ / vol / z-score | Existe (`acciones_quant_stats`) |
| Pivot points | Existe (`quant/pivot_points.py`) |
| **Matriz de correlación/covarianza completa** | **FALTA — pieza nueva, base de todo** |
| **Spread CI/24hs por ticker** | **FALTA — dato a sumar, para costear el short** |
| Conexión a `Valuaciones.AuM` (sleeve equity de un cliente) | Falta |

Sin la matriz de correlación no hay hedge-finder ni optimización. Es el Paso 0.

## Modos de hedge

El short en Argentina es **sintético y rolleado**: se vende en 24hs, se
recompra en CI al día siguiente, y se vuelve a vender en 24hs. Paga un
**carry diario** = el spread CI/24hs (una tasa implícita). Lo puede hacer
**solo la mesa de dinero**, no los clientes.

| Modo | Quién | Menú de hedge |
|---|---|---|
| **Mesa (prop)** | Book propio | Short sintético 24hs/CI + PSQ/VXX/GLD |
| **Cliente** | Carteras de clientes (AuM) | **Long-only**: PSQ, VXX, GLD, rotar a defensivo |

El tool ofrece solo lo operable según el book. **El costo del short se
modela** (carry CI/24hs) — un hedge por short sintético NO es gratis, y se
compara contra long-PSQ (sin roll pero con decaimiento del ETF inverso).

## Módulo 1 — Trade Individual + Hedging

Input: **ticker + monto + dirección** (long/short).

- **Caracterización**: vol 30/60d, beta vs SPY/QQQ, VaR 1 día, z-score
  (caro/barato), exposición de mercado equivalente.
- **Riesgo del trade solo**: swing diario, peor día, peor mes.
- **Buscador de hedge**: rankea todo el universo por correlación con la
  posición.
- **3 caminos comparados con plata**: short sintético (carry CI/24hs + roll
  diario), long inverso PSQ (decaimiento), tail con VXX. Ordenados por
  *vol que saca ÷ costo*, según horizonte del trade.
- **El residual**: la apuesta real sin mercado — lo que se cobra como idea.
- **Niveles**: entrada, soporte/resistencia, stop (pivots).
- **Escenarios**: SPY −10%, VIX +50%, etc. → P&L hedged vs unhedged.
- **Sizing por riesgo**: dado un presupuesto de pérdida diaria, cuánto comprar.
- **Acción**: "mandar al book" → entra al Módulo 2.

## Módulo 2 — Monitor de Book / Exposición (estilo Arcana)

Input: un book — **prop** (lo del Módulo 1) o **cliente** (de AuM, sleeve equity).

- **Exposición**: bruta, neta (long − short), por sector / región / país.
- **Concentración**: top posiciones, % en top 5, índice HHI.
- **Riesgo**: vol del book, VaR/CVaR, beta, drawdown simulado.
- **Contribución de riesgo**: quién aporta el RIESGO (no la plata) — una
  posición chica y volátil puede dominar.
- **Mercado vs selección**: cuánto del riesgo es beta y cuánto alpha propio.
- **Crowding**: qué nombres están en todas las carteras de clientes.
- **Alertas**: concentración sectorial, salto de VaR, límites por nombre.
- **Stress test** del book entero + **sugerencias de rebalanceo**.

## Módulo 3 — Scanner de Ideas (estilo Trade Ideas)

Panel que empuja ideas solo, corre sobre todo el universo.

- **Señales**: z-score extremo (reversión), pares en spread anómalo, activos
  en soporte/resistencia, rupturas de rango, vol comprimida, cambios de
  beta/correlación.
- **Filtros**: sector, vol, beta.
- **Hedge contextual**: "tu book está largo X → los 3 mejores hedges hoy".
- **Accionable**: clic en una idea → abre el Módulo 1 pre-cargado.
- **Watchlist + alertas**.

## Módulo 4 — Constructor de Carteras (para sales)

- **Input**: universo + perfil de riesgo + restricciones (peso máx por
  activo/sector, exclusiones).
- **Output**: frontera eficiente clickeable + 3 carteras pre-armadas —
  Mínima Varianza, Máximo Sharpe, Risk Parity (+ equal-weight de baseline).
- **What-if en vivo**: sliders de peso, agregar/sacar activos → recalcula todo.
- **Comparador**: cartera actual del cliente (AuM) vs la propuesta.
- **Mix con renta fija**: meter Lecaps/bonos (ya en el MCP) → cartera mixta.
- **Export**: vista presentable para el cliente.

## Integración (el loop)

```
Scanner → tira idea → Trade Individual (caracterizar + hedgear)
   → "mandar al book" → Monitor de Book lo absorbe
   → si es cliente, el Constructor sugiere el rebalanceo
```

## Decisiones y caveats

- **Markowitz puro sobreajusta** → default a Mínima Varianza / Risk Parity;
  Máximo Sharpe como modo avanzado.
- Data **diaria** (sin intradía — parkeado).
- **Opciones** y **fundamentals** — parkeados para más adelante.
- **Más historia** de precios — suma para una matriz de correlación robusta.
- AuM: solo la **sleeve equity** (los bonos los analiza renta fija).

## Orden de construcción

1. **Motor**: endpoint de matriz de correlación + sumar el spread CI/24hs.
2. **Módulo 1** (Trade Individual) — el más concreto, el que más rápido se
   siente PRO.
3. **Módulo 2** (Monitor de Book) — reusa el motor + conecta AuM.
4. **Módulo 4** (Constructor) — optimización sobre el mismo motor.
5. **Módulo 3** (Scanner) — al final, se nutre de todo lo anterior.

## Preguntas abiertas

- Ventana de la matriz de correlación: ¿1 año, 2 años?
- ¿De dónde sale el spread CI/24hs — ya está en alguna colección o hay que
  capturarlo? Sin ese dato, el costo del short es estimado, no exacto.
- "Cartera del cliente": ¿se carga a mano o se conecta directo a AuM?

## Referencias

Portfolio Visualizer (frontera eficiente, métodos de optimización), Arcana
(hedge screens, exposición, crowding), Trade Ideas (scanner de ideas), IBKR
PortfolioAnalyst (riesgo consolidado), Composer (constructor visual de reglas).
