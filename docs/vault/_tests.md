# 🧪 tests — red de seguridad

26 notas.

- [[tests]]
- [[tests.conftest]] — Configuración compartida de pytest.
- [[tests.integration]]
- [[tests.integration.test_comercial_integration]] — Tests de integración de la vista COMERCIAL — contra Atlas.
- [[tests.unit]]
- [[tests.unit.test_argentina_datos]] — Tests del cliente argentinadatos + job de persistencia.
- [[tests.unit.test_aum_valuacion]] — Golden tests de la fórmula de valuación AuM (api/services/portfolio.py).
- [[tests.unit.test_auth_posture]] — Test del fail-closed de auth al boot (EXT-AUTH1, api/main.py).
- [[tests.unit.test_black_scholes]] — Tests de quant/black_scholes.py — pricing, Greeks, IV.
- [[tests.unit.test_breakevens]] — Tests de engines/breakevens.py — cálculo de breakeven CER/Lecap.
- [[tests.unit.test_byma_client]] — Tests unitarios del cliente BYMA (sin red real).
- [[tests.unit.test_comercial]] — Tests del estado comercial (api/services/comercial.py).
- [[tests.unit.test_cotizaciones_tier2]] — Tests unitarios de las tools Tier 2 (snapshot_historico, pendiente, liquidez).
- [[tests.unit.test_curvas_math]] — Tests de funciones cuantitativas en engines/curvas.py.
- [[tests.unit.test_curve_fit]] — Tests del fit cuadrático puro (sin Mongo).
- [[tests.unit.test_descomposicion]] — Tests del cálculo de descomposición de retorno (Lecap / Boncap / Lecer).
- [[tests.unit.test_dias_habiles]] — Tests de jobs/dias_habiles.py — generación de calendario hábil argentino.
- [[tests.unit.test_fair_value_filtros]] — Tests de los filtros del universo del fit (sin Mongo).
- [[tests.unit.test_forwards]] — Tests de la matriz de tasas forward en engines/forwards.py.
- [[tests.unit.test_grupos_scope]] — Golden tests del scope de cuentas por grupo (api/services/_grupos_scope.py).
- [[tests.unit.test_idempotencia]] — Tests de la idempotencia de envío de órdenes (anti doble-orden).
- [[tests.unit.test_mae_client]] — Tests unitarios del cliente MAE (sin red real).
- [[tests.unit.test_mcp_redirect]] — Test del allowlist de redirect_uri del DCR del MCP (anti open-redirect).
- [[tests.unit.test_notify]] — Tests del notificador de alertas (core/notify.py).
- [[tests.unit.test_rbac]] — Golden tests de RBAC (core/roles.py).
- [[tests.unit.test_xirr]] — Tests de quant/xirr — validado contra TIR.NO.PER de Excel.
