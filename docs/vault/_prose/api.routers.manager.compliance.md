Sub-router `/api/manager/compliance` — tab COMPLIANCE. Un único GET de solo lectura que cruza EN VIVO el operador que reporta Aunesa contra el operador asignado en `Clientes.Comitentes` y marca las cuentas donde difieren. No persiste nada. Gateado por `manager_compliance` (rol compliance + admin).

Conecta con: service `api.services.compliance::comparar_operadores` (cache 5 min), que lee `Clientes.Comitentes` y pega a Aunesa. Lo consume la tab COMPLIANCE de la manager-view.
