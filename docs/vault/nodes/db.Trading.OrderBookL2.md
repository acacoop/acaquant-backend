---
id: db.Trading.OrderBookL2
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.OrderBookL2

> Colección Mongo en DB Trading.

## Qué hace
Order book de profundidad nivel 2 (LOB) por instrumento, en la base `Trading`. Guarda las puntas con cantidades para la vista de Order Book live.

Conecta con: la alimentan los motores de mercado vía el feed WS de pyRofex; la sirve `api/services/order_book.py`. (Nombre citado en la documentación del repo como colección de `Trading`.)

## Lo usan (backlinks) ←
- [[scripts.gen_obsidian]]  ·  _module_
