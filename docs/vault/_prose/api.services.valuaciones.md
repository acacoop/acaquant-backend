Service de performance e historia por cuenta, con dos enfoques convivientes: (A) AUM-BASED para /serie y /mensual, que suma el snapshot MTM diario y lo combina con flujos externos (depósitos/extracciones) para la mensualización "valor de cierre + flujo neto" y la TIR; (B) COST-BASIS LEDGER para /posiciones, que reconstruye lots de boletos para PnL realizado vs no realizado por ticker. Pesifica monedas USD al MEP del día.

Conecta con: lee `Valuaciones.AuM`, `CashFlow.NegocioMovimientos` (flujos) y `Trading`; usa `quant.xirr` para la TIR. Lo invoca el router `/api/valuaciones`.
