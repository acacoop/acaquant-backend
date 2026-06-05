Modal con la historia de Fair Value de un bono: trae 60 días de residuos/z-scores y los grafica (curva fiteada + scatter del bono) con recharts para ver cuán caro/barato está vs su propia norma temporal.

Conecta con: consume `/api/cotizaciones/fair-value/historico?ticker=...&dias=60` (service `api.services.fair_value`, datos del job `jobs.fair_value`); se abre desde `web.cmp.fair-value-view`.
