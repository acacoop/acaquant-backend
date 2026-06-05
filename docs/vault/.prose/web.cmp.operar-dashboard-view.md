Dashboard de trading manual: buscador de símbolo, order book live (bids/offers + métricas OHLC), envío/cancelación de órdenes LIMIT/MARKET, gestión de órdenes del día (incluye las que entraron por otra plataforma del broker) y panel de cartera por cuenta. Exporta hooks (`usePortfolio`, `useOrdenesDia`) y sub-componentes reutilizados por la vista FCI.

Conecta con: pega a `/api/ordenes` (envío/cancel/listado contra ROFEX, LIVE), order book live y datos de cuenta del broker. Lo monta `operar-shell` (tab DASHBOARD).
