Router de datos de cuenta del broker (información sensible). Expone saldo ARS+USD disponible por rueda (`/account/saldo`), el report crudo del broker, y posiciones —simples y detalladas por tipo de instrumento—. Cada endpoint resuelve la cuenta con scope de grupos antes de consultar.

Conecta con: delega en `api.services.risk` (que consulta a ROFEX vía sesión del broker, con cache corto); scope de grupos (`verificar_account`); gate RBAC módulo `operaciones` (admin+trader); lo consumen la UI Dólar MEP y vistas de riesgo del frontend.
