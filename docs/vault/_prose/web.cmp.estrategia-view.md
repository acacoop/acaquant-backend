Vista ESTRATEGIA (Módulo 1 de la Mesa de Estrategia, Renta Variable): el usuario carga un trade (ticker + monto + dirección) y obtiene la caracterización de riesgo (vol, beta vs SPY/QQQ, z-score, VaR 1d) más el hedge-finder (hedge por beta y por correlación con notional sugerido).

Conecta con: consume `GET /api/scanner/trade-analysis` (service `api.services.rv_motor` / `scanner`); spec en `docs/wip_mesa_estrategia_rv.md`.
