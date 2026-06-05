Diagnóstico read-only que verifica el match por boleto antes de codear el fix del bruto FCI: confirma que el `boleto` de CashFlow.Operaciones coincide 1:1 con el `comprobante` (BOL) de NegocioMovimientos, que en Operaciones el bruto de suscripción viene en 0, y que el importe de NegocioMov es el valor correcto a estampar. Muestra el desajuste fila por fila. Default HOY. Se corre con python -m scripts.diag_fci_match_boleto [--fecha YYYY-MM-DD].

Conecta con: CashFlow.NegocioMovimientos y CashFlow.Operaciones, cliente Mongo de solo lectura. Insumo del fix de bruto FCI en el pipeline de ingesta.
