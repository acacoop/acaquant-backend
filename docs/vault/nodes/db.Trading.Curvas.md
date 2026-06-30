---
id: db.Trading.Curvas
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.Curvas

> Colección Mongo en DB Trading.

## Qué hace
Definición de cada instrumento de renta fija y su flujo de fondos, en la base `Trading`. Fuente de verdad de los flujos (CER porcentual, tasa_fija absoluta, soberanos en USD) que alimentan toda la valuación: TEA/TNA/duration, breakevens, carry, sensibilidad. Shape crítico, no inferible (ver CLAUDE.md).

Conecta con: la consumen los motores (`engines/curvas.py`, breakevens, forwards) y casi todos los services de renta fija (`renta_fija.py`, `comparar_inversion.py`, `sinteticos.py`, `fair_value.py`). Une con `Valuaciones.Assets` por `ticker_corto == TICKER` para entrar al AuM. Se limpia con `jobs/cleanup_curvas.py`.

_Sin conexiones detectadas mecánicamente._
