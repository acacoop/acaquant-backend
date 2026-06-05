Para el rol compliance: cruza en vivo el operador asignado por la mesa en nuestra base (`Clientes.Comitentes.operador_email`, editado a mano) contra el que Aunesa reporta en `cuentas/listadoCuentas`. Como el operador de Aunesa solo se copia al crear la cuenta y nunca se vuelve a pisar, con el tiempo divergen. Marca cada fila como ok / distinto / falta_en_nuestra_base / falta_en_aunesa. No persiste nada; cache 5 min.

Conecta con: lee `Clientes.Comitentes` y pega en vivo a Aunesa vía `core.aunesa`; lo consume el sub-router `/api/manager/compliance`.
