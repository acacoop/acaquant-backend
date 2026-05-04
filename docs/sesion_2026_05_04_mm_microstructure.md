# Sesión 2026-05-04 — MM Microstructure (Cartea cap 1-4 → app)

> Norte: traer 100% el libro de Cartea/Jaimungal/Penalva (caps 1-4) a la vista MM.
> Materia prima: `Trading.OrderBookL2` (motor `engines/order_book_l2.py`, AL30 - CI hoy)
> + `Trading.TimeSales` (motor_rofex). Solo lectura, sin escrituras nuevas a Mongo.

## Resumen ejecutivo

Tres niveles de análisis sobre el mismo tape, mismo ticker:

1. **Live (cap 1)** — métricas puras del book top-5 (sirven al refresh natural del motor, ~1Hz).
2. **Tape enriquecido (cap 4 live)** — cada trade alineado con el book del momento (effective spread, Lee-Ready, walking).
3. **Offline analytics (caps 2-4)** — agregados intradía, smile en U, estimación empírica de impacto, stylized facts.

Cada análisis se expone como (a) endpoint REST, (b) tool MCP, (c) panel del frontend `/mm`. Una pieza única abre acceso desde la mesa, desde Claude.ai, desde el asistente.

## Arquitectura

```
┌────────────────────────────────────────────────────────────┐
│ Sources (read-only)                                        │
│   Trading.OrderBookL2  (TS Collection, append-only)        │
│   Trading.TimeSales    (trades raw)                        │
│   Trading.MarketSnapshot (last_price, metrics)            │
└────────────────────────────────────────────────────────────┘
            │                                │
            ▼                                ▼
┌────────────────────────────────────────────────────────────┐
│ api/services/mm_microstructure.py                          │
│   ── pure functions (book_metrics, lee_ready, ...)         │
│   ── query helpers   (latest_book, trades_window, ...)     │
│   ── analytics       (intraday, smile, impact, ...)        │
└────────────────────────────────────────────────────────────┘
            │                                │
   ┌────────▼─────────┐            ┌─────────▼─────────────┐
   │ api/routers/mm.py│            │ api/mcp/server.py     │
   │  6 endpoints     │            │  6 tools (mismo data) │
   └────────┬─────────┘            └─────────┬─────────────┘
            │                                │
            ▼                                ▼
   acaquant-web /mm                   Claude.ai connector
```

## Endpoints (REST + MCP equivalente)

| Endpoint REST | Tool MCP | Cap | Descripción |
|---|---|---|---|
| `GET /api/mm/live` | `mm_live` | 1 | Book top-5 + mid + microprice + OBI + quoted spread (bps) |
| `GET /api/mm/tape` | `mm_tape` | 4 | Trades de la ventana, cada uno con effective spread, lee_ready_side, walking flag |
| `GET /api/mm/intraday` | `mm_intraday` | 4 | Buckets 1-min: NOF, ES medio + qES, walking incidence, vol realizada, n_trades, volume |
| `GET /api/mm/impact` | `mm_impact` | 4 | OLS sobre histórico: `b` (permanent, Δmid vs NOF) y `k` (temporary, slippage vs size) |
| `GET /api/mm/stylized-facts` | `mm_stylized_facts` | 3 | Kurtosis, skewness, ACF lag-1 (price vs mid), persistencia ACF\|r\|, Jarque-Bera |
| `GET /api/mm/smile` | `mm_smile` | 4 | Buckets 30-min × N días: volumen + vol realizada → forma U |

## Cálculos puros (núcleo del módulo)

### Cap 1 — Book metrics (input: 1 snapshot del book)

```python
def book_metrics(snapshot: dict) -> dict:
    """
    snapshot = {"bids": [{"price","size"}, ...], "offers": [{"price","size"}, ...]}
    """
    best_bid, bid_size = snapshot["bids"][0]
    best_ask, ask_size = snapshot["offers"][0]
    mid = (best_bid + best_ask) / 2
    qs  = best_ask - best_bid
    obi = (bid_size - ask_size) / (bid_size + ask_size)
    micro = mid + (obi / 2) * qs
    return {
      "mid": mid, "microprice": micro,
      "obi": obi, "quoted_spread": qs,
      "qs_bps": (qs / mid) * 10000,
    }
```

### Cap 4 — Trade enrichment (input: trade + book del momento)

```python
def enrich_trade(trade, book) -> dict:
    m = book_metrics(book)
    es = abs(trade["price"] - m["mid"])         # effective spread (per trade)
    lee_ready = (
        "BUY"  if trade["price"] > m["mid"] else
        "SELL" if trade["price"] < m["mid"] else
        "MID"
    )
    side_top = "offers" if trade["side"] == "BUY" else "bids"
    top_size = book[side_top][0]["size"]
    walking = trade["size"] > top_size
    return {**trade, "es": es, "lee_ready": lee_ready, "walking": walking, "mid": m["mid"]}
```

**Alineación trade ↔ book**: two-pointer sweep ordenado por timestamp. O(n+m), no O(n·m).

### Cap 4 — Intraday buckets (input: trades enriquecidos + buckets 1-min)

Por bucket:
- `nof = Σ(size buy_MO) − Σ(size sell_MO)` con Lee-Ready.
- `qES = Σ(size_i · ES_i) / Σ(size_i)` (quantity-weighted effective spread).
- `walking_pct = #trades_walking / #trades`.
- `realized_vol = stdev(retornos a 1-min sobre mid)` recortados al bucket.
- `volume = Σ size`, `n_trades = #trades`.

### Cap 4 — Impact (OLS sobre buckets ya calculados)

- **Permanent b**: `ΔS_n = b·π_n + ε` por bucket de 1-min sobre N días.
  `b̂ = Σ(πΔS) / Σ(π²)` (forma cerrada). Devolver R² + nº de buckets.
- **Temporary k**: `S_exec − mid_at_trade = k·Q + ε` por trade.
  `k̂ = Σ(QΔ) / Σ(Q²)`.
- TODO post-MVP: pasar a Huber/RLM para robustez (hoy OLS plano + winsorización 1%).

### Cap 3 — Stylized facts

- Retornos a 1-min sobre mid (no sobre last → evita bid-ask bounce).
- `kurtosis = E[(r-μ)⁴]/σ⁴`, `skewness = E[(r-μ)³]/σ³`.
- `ACF(1) sobre r_t` (debería ~0 sobre mid).
- `ACF(1) sobre r_t calculados sobre last` (debería ser negativa → bid-ask bounce).
- `ACF(k) sobre |r_t|` para k=1..20 (debería persistir → volatility clustering).
- Jarque-Bera = `(n/6)·[S² + (K-3)²/4]`. p-value vs χ²(2).

### Cap 4 — Smile intradiario

Buckets de 30-min × N últimos días hábiles. Promedio de:
- volumen total del bucket,
- vol realizada del bucket (sobre mid).

Output: 13 puntos (apertura→cierre, ART). Forma U esperada.

## Frontend `/mm` — layout

```
┌──────────────────────────────────────────────────────────────────┐
│ TICKER       MID         MICRO       OBI       SPREAD            │
│ AL30 - CI    62.881      62.892     +0.42     1.1 bps           │
├──────────────┬───────────────────────┬───────────────────────────┤
│ BOOK L2      │ TAPE (live)           │ INTRADAY (1-min agg)      │
│ ┌──────────┐ │ ts   px   sz  side ES│ NOF chart (bars)          │
│ │offers ×5 │ │ ...  ...  ... ... ...│ qES chart (line)          │
│ │ ─────── │ │                       │ vol realizada (line)      │
│ │ bids   ×5│ │ (auto-scroll)         │ walking incidence         │
│ └──────────┘ │                       │                           │
├──────────────┴───────────────────────┴───────────────────────────┤
│ TABS: [STYLIZED FACTS] [IMPACT b/k] [SMILE en U] [LECTURA]      │
│                                                                  │
│ (panel del tab activo)                                           │
└──────────────────────────────────────────────────────────────────┘
```

- Header refresca cada 1s.
- Book L2 + tape también 1s (mismo polling).
- Intraday refresca cada 60s (es agregado).
- Tabs offline son lazy: se calculan al abrir el tab, cache 5min.

## MCP — tools nuevas

Mismas 6 que los endpoints REST. Cada una thin wrapper sobre el service. Documentación completa en `docs/MCP_TOOLS.md` después del MVP.

## Pendientes post-MVP

1. OLS Huber para `impact` (hoy plain OLS + winsorización).
2. ACD para duration entre trades (cap 3).
3. Procesos de Hawkes para order arrivals (cap 4-fundamentos).
4. Cap 5+: modelos óptimos (Avellaneda-Stoikov, Almgren-Chriss).
5. Multi-instrumento (hoy AL30 - CI fijo). Ampliar `config.TICKERS_BOOK_FULL`.

## Por qué esta arquitectura

- **Service puro + router thin**: respeta la convención del repo. El service es invocable desde MCP sin loopback HTTP.
- **OrderBookL2 como TS Collection**: append-only ya garantiza que las queries del intraday sean baratas (compresión columnar).
- **Cache 60s en intraday/impact/smile/sf**: estos son agregados costosos. Live y tape sin cache (~1s refresh natural).
- **Lee-Ready cross-check con `trade.side`**: el motor pyRofex ya marca el side. Lo usamos como ground truth y reportamos divergencia (es señal de mid stale, no bug).
