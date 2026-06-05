Herramienta DBA reusable, read-only puro, que recorre todas las bases/colecciones del cluster y produce un reporte accionable: inventario (tamaño datos/índices, #docs), salud de índices (muertos / redundantes), retención TTL y, opcional, consistencia de schema por muestreo. No borra nada — solo diagnostica y prioriza FINDINGS al final.
Se corre con `python -m scripts.audit_db [--schema] [--db Trading]`.
Conecta con: `core.mongo.get_mongo_client_read`; base de la auditoría DBA documentada en `project_db_auditoria`.
