Precalcula la valuación consolidada por cuenta (valor + base 100 + PnL acumulado en ARS y USD) que necesita la vista TOTALES > POR CUENTA. Hacerlo en vivo recorría N cuentas y reventaba el timeout HTTP (502): este job lo computa offline sin límite de tiempo y lo persiste con swap atómico.

Corre como cron diario después del AuM final (post `jobs.aum`, 23 UTC L-V).

Conecta con: llama `api.services.valuaciones::construir_consolidado`, escribe `Valuaciones.ConsolidadoCuentas`. El endpoint `/api/valuaciones/consolidado` solo lee esa colección (instantáneo).
