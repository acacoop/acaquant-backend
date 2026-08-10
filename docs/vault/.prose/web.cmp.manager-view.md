Vista contenedora del módulo Manager (/manager, admin-only). Organiza en tabs todos los paneles de administración: usuarios, roles, grupos, jobs, logs, comercial, exploradores Aunesa y los paneles de debug (XIRR, segmento, comercial, curva). Usa imports estáticos para que cambiar de tab sea instantáneo.

Conecta con: no hace fetch propio relevante — orquesta los sub-paneles, cada uno con su endpoint `/api/manager/*`. Gateada por el módulo `manager` en `header`.
