Vista `/retorno` — retorno total / comparar inversión de bonos. Wrapper mínimo (sin `force-dynamic` ni SSR fetch) que delega todo en `RetornoTotalView`, que hace sus fetches client-side.

Conecta con: componente `RetornoTotalView` → backend `/api/analitica` y `/api/cotizaciones` (descomposición de retorno, comparar inversión, carry trade). Vive bajo el layout raíz.
