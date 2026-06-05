Vista `/renta-variable` — módulo de renta variable (Scanner CEDEARs + Mesa de Estrategia). SSR en paralelo del scanner de CEDEARs (`/api/scanner/cedears`, TTL 5s) y el CCL live (`/api/scanner/ccl`) para el KPI del shell; el polling client (10s) hace la lectura efectiva contra el motor que escribe cada 1s. Renderiza `RentaVariableShell`.

Conecta con: backend `GET /api/scanner/cedears` y `/api/scanner/ccl` (service `scanner`); componente `RentaVariableShell`.
