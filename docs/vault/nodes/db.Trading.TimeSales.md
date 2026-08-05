---
id: db.Trading.TimeSales
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.TimeSales

> Colección Mongo en DB Trading.

## Qué hace
Stream de trades (time & sales) por instrumento en la base `Trading`, enriquecido en tiempo real con métricas de curva. Registro tick-a-tick que respalda series intradía y el último trade por ticker.

Conecta con: la escribe/enriquece `engines/curvas.py` (motor de enriquecimiento real-time); la leen `api/services/canje.py`, `carry_trade.py`, `cotizaciones.py` y validaciones del Manager. Para tasas live se prefiere `MarketSnapshot` (TimeSales agregado es más caro).

_Sin conexiones detectadas mecánicamente._
