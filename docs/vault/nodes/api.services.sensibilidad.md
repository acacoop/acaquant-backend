---
id: api.services.sensibilidad
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/sensibilidad.py
---

# api/services/sensibilidad

> Análisis de sensibilidad de retorno total a escenarios de TIR.

**Archivo:** `api/services/sensibilidad.py`

## Qué hace
Análisis de sensibilidad de retorno total para los bonos soberanos: para cada escenario de TIR a N días responde "si el bono cotiza a TIR X, ¿cuál es el retorno total?" = (precio_objetivo proyectado por descuento de flujos futuros + cupones/amortizaciones cobrados hasta el horizonte) / precio_actual − 1. Default: upside instantáneo sin pull-to-par. Cacheado.

Conecta con: lee precios de `Trading.MarketSnapshot` y los flujos del prospecto de `Trading.Curvas`; reusa `fecha_flujo`/`monto_flujo_soberano` de `engines.curvas`. Alimenta la tab "Análisis Sensibilidad" de /retorno en acaquant-web y la tool MCP.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[engines.curvas]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.services.copiloto.renta_fija]]  ·  _module_
