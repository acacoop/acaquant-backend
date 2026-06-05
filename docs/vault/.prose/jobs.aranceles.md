Completa el arancel de los boletos que entraron sin él: pega a Aunesa `/informes` y rellena los últimos 7 días (ventana robusta a liquidación T+2 y reintentos). Reusa exactamente la lógica de `api.services.aunesa_aranceles.run_backfill` (misma que el botón de la UI). Encadenado al cron de `jobs.negocio_movimientos` (corre justo después, cada hora L-V).

Conecta con: escribe el arancel sobre `CashFlow.NegocioMovimientos`; registra el run en `Manager.JobRuns` (alerta Telegram si parcial) y deja un doc en `Manager.AranceelesJobRuns` para el HISTORIAL de la vista BOLETOS→BACKFILL.
