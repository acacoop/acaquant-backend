Panel de diagnóstico del informe comercial (Manager → Diagnóstico): elegís operador y/o segmento y muestra, por cuenta, cuántas operaciones reconoce y qué volúmenes/aranceles, auditando el ticket promedio.

Conecta con: fetch a `/api/operaciones/comercial/operadores` (lista de operadores) y `/api/manager/checks/debug-comercial` (cruza `CashFlow.NegocioMovimientos` + `Clientes.Comitentes`). Se monta dentro de `manager-view`.
