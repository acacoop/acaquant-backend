Cliente puntual de Aunesa `/operaciones/informes` que trae el ÚNICO dato que el feed de negocio no tiene: el arancel por boleto. Devuelve `{boleto: {moneda: arancel}}` para enriquecer por `boleto == comprobante`. Incluye un parser robusto de montos que tolera los dos formatos que manda Aunesa (coma argentina vs punto decimal) y toma el arancel una sola vez por boleto (no suma las filas repetidas por ejecución).

Conecta con: pega a Aunesa vía `core.aunesa`; lo consume `api.services.aunesa_aranceles` para el backfill de aranceles sobre `CashFlow.NegocioMovimientos`.
