# Copiloto de Mesa — doc vivo (QuantAI P3)

> **REGLA DE ESTE DOC:** todo cambio que modifique lo que el asistente VE
> (columnas, bloques, reglas del prompt, vistas) o CÓMO se comporta, se asienta
> en el **Changelog** de abajo CON FECHA, en el MISMO commit del cambio. Si el
> changelog no refleja lo que corre en prod, el cambio está incompleto.
> Roadmap y decisiones de programa: `docs/QUANTAI.md` (P3). Acá vive el detalle
> operativo del asistente.

## Dónde vive el código (mapa)

| Pieza | Archivo | Qué hace |
|---|---|---|
| **El cerebro** | `api/services/copiloto.py` | TODO el comportamiento: registro `VISTAS` (qué datos, columnas, reglas), system prompt, detección de tickers, bloques de detalle, serialización TSV, feedback. **Tocar el asistente = tocar este archivo.** |
| Transporte LLM | `core/ai.py` | Gateway único: tarea `copiloto_vista` (tier flash, max_tokens 2000), presupuesto diario, retry, traza de cada llamada. No sabe nada del copiloto. |
| HTTP | `api/routers/ia.py` | 3 endpoints: `GET /api/ia/copiloto/vistas`, `POST /api/ia/copiloto`, `POST /api/ia/copiloto/feedback`. Solo plumbing; gate `ia` en el montaje. |
| Panel UI | `acaquant-web/src/components/ia-vista-panel.tsx` | Drawer + botón "Consultale a la IA". Historial corto client-side. Se oculta si el backend no habilita la vista. |
| Ubicación del botón | `acaquant-web/src/components/renta-variable-shell.tsx` | Monta `<IaVistaPanel vista="renta_variable" />` en la barra de tabs. |
| Observabilidad | tabla `ia.trazas` + Manager → OBSERVABILIDAD → pill IA | Cada pregunta: tokens, latencia, ok/error, feedback 👍/👎. |
| Tests | `tests/unit/test_copiloto.py` | Congelan contrato: TSV, gates, caps, degradación, detección de tickers. |

## Cómo fluye una pregunta

```
Panel (browser) ── {vista, pregunta, historial} ──► POST /api/ia/copiloto
    (los DATOS nunca viajan del browser)                │
                                                        ▼
   copiloto.preguntar():  fetch del service @cached de la vista
                          → enriquecer (columnas derivadas)
                          → TSV + extras (CCL, detalle por ticker mencionado)
                          → core.ai.completar_con_traza("copiloto_vista")
                          → {respuesta, traza_id}  (ok=False si algo falla)
```

## Qué ve el asistente HOY (vista `renta_variable`)

- **Tabla completa** (~187 CEDEARs × 26 columnas): identificación (ticker,
  nombre, underlying, ratio, sector, rubro, país, **es_ia**), precios ARS,
  variaciones (intradía, 1d, 1d USD), liquidez (bid/offer/spread/volumen/
  monto), subyacente USD (precio, variaciones, retornos WTD/7d/MTD/YTD,
  volumen USD) y **zonas de pivots** `piv_anual` / `piv_mensual`.
- **CCL live** (siempre, 1 línea).
- **Detalle por ticker MENCIONADO en la pregunta** (cap 3, detección
  determinista de tokens): pivots 4 marcos con niveles, quant (beta/corr vs
  SPY/QQQ, vol, z-score), últimos 15 retornos diarios, fundamentals Refinitiv
  (si la empresa está en `research.companies`).
- **Conocimiento de mesa en el prompt:** significado de cada columna + lectura
  de pivots (>R3/<S3 = movió muchísimo · R2/S2 = tendencia clara · R1/S1 =
  movió algo · PP = referencia ideal de decisión) + manejo de pedidos de
  recomendación (ranking objetivo con criterio, nunca consejo de inversión).

## Cómo se agrega una vista nueva

1. Entrada en `VISTAS` (copiloto.py): `titulo`, `modulo` RBAC, `fetch` (el
   MISMO service @cached de la vista), `columnas`, `reglas` del dominio, y
   opcionales `enriquecer` / `extras`.
2. `<IaVistaPanel vista="..." />` donde la vista lo quiera (el padre posiciona).
3. Entrada acá en el changelog + estado en QUANTAI.md.

---

## Changelog del asistente (obligatorio, con fecha)

### 2026-07-11 — v1.3
- **[fix detección]** Palabras comunes que colisionan con tickers ("de" →
  Deere) solo matchean escritas en MAYÚSCULAS (`_TOKENS_AMBIGUOS`). Caso real
  del shadow: "acciones de IA" inyectaba el detalle de DE.
- **[prompt +]** Pedidos de recomendación: ranking objetivo con criterio
  explícito en vez de "no puedo recomendar". Sin consejo de inversión.

### 2026-07-11 — v1.2 (primer loop del shadow)
- **[contexto +]** Columnas `es_ia` y `rubro` (la pregunta real "¿qué acciones
  hay de IA?" reveló que faltaban — el modelo improvisaba por nombre).
- **[contexto +]** Zonas de pivots `piv_anual` / `piv_mensual` para TODO el
  universo (2 queries agregadas cacheadas 15 min — no 187 llamadas).
- **[prompt +]** Lectura de pivots de la mesa (conocimiento no inferible,
  aportado por el user): >R3/<S3 subió/cayó muchísimo · R2-R3/S3-S2 tendencia
  clara · R1-R2/S2-S1 movió algo · PP zona ideal de decisión. Uso implícito,
  sin listar niveles salvo pedido.
- **[UI −]** Línea de fuente (📊 vista · filas · hora) fuera de la respuesta;
  el disclaimer fijo del panel cubre la marca de origen IA.

### 2026-07-11 — v1.1 (vista completa)
- **[alcance]** De "tabla CEDEARs" a VISTA Renta Variable completa (ambas tabs).
- **[contexto +]** CCL live (siempre) + detalle por ticker mencionado: pivots
  4 marcos, quant, últimos 15 retornos, fundamentals Refinitiv (cap 3 tickers,
  detección determinista sin LLM).
- **[UI]** Botón "Consultale a la IA" (antes "IA"); luego movido a la barra de
  tabs, margen derecho. Negritas renderizadas. Bienvenida "¿En qué puedo
  ayudarte?". Alias `cedears` creado y borrado tras confirmar el deploy.

### 2026-07-11 — v1 (nacimiento)
- Copiloto CONTEXTUAL por vista (decisión del user: NO chatbot global). Una
  llamada flash por pregunta; datos armados server-side desde el service
  @cached; TSV; gate doble `ia` + módulo de la vista; historial corto
  client-side (4 pares); 👍/👎 → `ia.trazas.feedback`; degradación ok=False.
- Colateral: el copiloto expuso series rotas en `mercado.precios_acciones`
  (2025 incompleto 116/187, split CRWD, vela basura HON) → backfill 2024+ +
  auto-reparación de splits en `jobs/precios_acciones_daily`.
