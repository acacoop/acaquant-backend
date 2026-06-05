Shell del módulo Dólar MEP: maneja el estado compartido entre las sub-tabs COMPRA y TRADING (rueda, monto ARS/USD, comisión, cuenta) y centraliza los polls de cotización y saldo para no duplicarlos. Trae las cuentas descubiertas una vez al montar.

Conecta con: renderiza `web.cmp.dolar-mep-compra-view` y `dolar-mep-venta-view`; lee cuentas de `jobs.descubrir_cuentas` (vía API), saldo de `/api/risk/account/saldo` y cotización MEP live.
