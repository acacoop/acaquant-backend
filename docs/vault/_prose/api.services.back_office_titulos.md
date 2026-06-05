Calcula, para el Back Office, qué títulos hay que ENVIAR al mercado hoy y cuáles hay que RECIBIR, a partir de las operaciones de clientes. Define settlement de hoy como las ops de hoy con plazo CI/Inm (T+0) más las de ayer hábil con plazo 24hs (T+1); por cada match, una venta se envía y una compra se recibe. Solo entran categorías `compra`/`venta`. Cacheado 10s.

Conecta con: lee `CashFlow.NegocioMovimientos` (vía `core.mongo` read); usa el calendario de feriados AR; lo consume el router `/api/back-office`.
