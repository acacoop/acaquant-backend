Diagnóstico read-only del incidente de CPU 100%: los upserts por `boleto` en CashFlow.Operaciones hacían COLLSCAN de 488k documentos en vez de usar el índice uq_boleto. Imprime el spec completo de los índices (detectando si uq_boleto tiene collation, la causa clásica de no-uso) y el explain() de un find por boleto para confirmar IXSCAN vs COLLSCAN. No escribe nada. Se corre con python -m scripts.diag_indice_boleto.

Conecta con: CashFlow.Operaciones (índices y plan de query), cliente Mongo de solo lectura. Diagnostica la ingesta horaria de operaciones_informes.
