---
id: scripts.gen_obsidian
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/gen_obsidian.py
---

# scripts/gen_obsidian

> Genera el vault de Obsidian (docs/vault/) — el cerebro vivo de TODO el sistema.

**Archivo:** `scripts/gen_obsidian.py`

## Qué hace
Generador determinista del vault de Obsidian (docs/vault/) — el "cerebro vivo" del sistema. Mapea en un único vault navegable ambos repos + base + deploy: módulos backend, vistas/componentes/rutas del frontend, colecciones Mongo (quién escribe/lee) y servicios systemd + crons (qué módulo corre cada uno), con las aristas de import, acceso a colección y ruta-front→endpoint. Deja el esqueleto y los edges mecánicos; la prosa "qué hace" por nodo la rellena la pasada de IA en _prose/ (que sobrevive a la regeneración). Se corre con `python -m scripts.gen_obsidian [--check]`.
Conecta con: escanea TradingAV y acaquant-web; emite docs/vault/_manifest.json y las notas; este mismo archivo es una de sus salidas enriquecidas.

## Usa / conecta con →
- [[db.ACAPortfolio.Cartera]]  ·  _collection_
- [[db.CashFlow.Contrapartes]]  ·  _collection_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.CashFlow.Productores]]  ·  _collection_
- [[db.Clientes.ComercialCache]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
- [[db.CuentasAPI.AccionistasAPI]]  ·  _collection_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_
- [[db.MCP.OAuthCodes]]  ·  _collection_
- [[db.MCP.OAuthTokens]]  ·  _collection_
- [[db.Manager.RoleAudit]]  ·  _collection_
- [[db.Manager.RoleMatrix]]  ·  _collection_
- [[db.Manager.Users]]  ·  _collection_
- [[db.Trading.CanjeCierre]]  ·  _collection_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Trading.DOLAR]]  ·  _collection_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Trading.OrderBookL2]]  ·  _collection_
- [[db.Trading.PreciosAcciones]]  ·  _collection_
- [[db.Trading.SnapshotsCierre]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[db.Trading.UVA]]  ·  _collection_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[db.Valuaciones.ConsolidadoCuentas]]  ·  _collection_
- [[db.Valuaciones.Dolar]]  ·  _collection_
- [[db.Valuaciones.DolarOficialLive]]  ·  _collection_
- [[db.Valuaciones.PnLTotalesCache]]  ·  _collection_
