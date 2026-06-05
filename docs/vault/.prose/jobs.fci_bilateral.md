Lleva el FCI bilateral (que el API de informes no trae) desde `CashFlow.NegocioMovimientos` a `CashFlow.Operaciones`, taggeando la `etapa`: SOLICITUD (comprobante DOC, el pedido del día) vs LIQUIDACIÓN (comprobante CL, la plata liquidada al día siguiente). Excluye los BOL (FCI normales que ya entran como boleto) para no duplicar, y corrige el bruto=0 que el API de informes deja en las suscripciones.

Idempotente y NO destructivo (preserva carga manual histórica con $setOnInsert). Default mira últimos 10 días; `--full` toda la historia. Encadenado a `negocio_movimientos`.

Conecta con: lee `CashFlow.NegocioMovimientos` y `Valuaciones.Assets` (CAFCI), escribe `CashFlow.Operaciones` + `CashFlow.TiposOperacion`. Reusa `api.services.operaciones_informes`.
