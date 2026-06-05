Captura los movimientos de efectivo de las cuentas comitente: pega a Aunesa `consolidadosGenerales` día hábil por día, filtra las filas que son depósitos/transferencias/extracciones (match por palabra clave) e invierte el signo (entradas positivas, salidas negativas). Persiste idempotente por `comprobante` (índice único, `$setOnInsert`); descarta defensivamente las filas sin comprobante para no tumbar el bulk_write. `--today` para el cron diario (corre 02:00 UTC ≈ 23:00 ART).

Conecta con: pega a Aunesa, escribe `CashFlow.Movimientos`. Cron diario en `deploy/crontab.txt`.
