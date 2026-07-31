# ⚙️ engines — motores WS→Mongo

18 notas.

- [[engines]]
- [[engines._curvas_loader]] — Carga común del master de renta fija para todos los motores.
- [[engines._universo_portfolio]] — Universo dinámico para motor_portfolio_snapshot.
- [[engines.breakevens]] — main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.
- [[engines.caucion]] — Motor de caución ARS y USD a corto plazo.
- [[engines.curvas]] — curvas.py — Motor de enriquecimiento analítico en tiempo real (SQL-only).
- [[engines.dolar_mep]]
- [[engines.dolares]] — Motor de dólares MEP/CCL/canje en tiempo real (WebSocket).
- [[engines.estrategia]] — engines/estrategia.py — motor ESTRATEGIA QUANT (señal intradía con trazabilidad).
- [[engines.forwards]] — main_forwards.py — Motor de tasas forward en tiempo real.
- [[engines.futuros_dlr]] — Motor de futuros DLR (Dólar A3500) — outrights single-leg.
- [[engines.motor_agro]] — Motor de Futuros Agro Rosario — Trigo / Maíz / Soja.
- [[engines.motor_agro_opciones]] — Motor de Opciones Agro Rosario — Trigo / Maíz / Soja.
- [[engines.motor_cedears]] — motor_cedears.py — feed live de CEDEARs vía pyRofex WS.
- [[engines.motor_ordenes]] — Motor de órdenes — escucha execution reports y persiste el ciclo de vida.
- [[engines.options]] — Motor de Opciones GGAL - Servicio Headless
- [[engines.portfolio_snapshot]] — Motor dedicado a captura del último precio para tickers de tenencia.
- [[engines.valores]]
