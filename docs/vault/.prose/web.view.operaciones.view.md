Vista `/operaciones` — flujo de negocio / boletos (movimientos consolidados). Wrapper `force-dynamic` que delega en `OperacionesView`, que hace sus fetches client-side.

Conecta con: componente `OperacionesView` → backend `/api/operaciones` (CashFlow.NegocioMovimientos / Operaciones). Vive bajo el layout raíz.
