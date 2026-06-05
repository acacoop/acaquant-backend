Diagnóstico read-only que mide por qué el job de FCI bilateral tarda tanto: el tamaño de CashFlow.NegocioMovimientos, el subset FCI que el job lee y el plan de la query (COLLSCAN vs IXSCAN, docs examinados). Sirve para confirmar si el cuello es un scan de toda la colección antes de optimizar. No escribe nada. Se corre con python -m scripts.diag_fci_job_perf.

Conecta con: CashFlow.NegocioMovimientos (categorías suscripcion/rescate FCI), cliente Mongo de solo lectura (core.mongo.get_mongo_client_read). Apoyo al diseño de jobs.fci_bilateral.
