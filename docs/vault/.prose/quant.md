Paquete de cálculo puro de la plataforma: matemática financiera y estadística sin estado (sin Mongo, sin FastAPI, sin cache). Agrupa Black-Scholes/greeks, ajuste de curva cuadrático, pivot points, stats rolling (beta/alpha/vol), helpers estadísticos de clasificación y XIRR. El `__init__.py` está vacío — es solo el namespace del paquete; cada módulo se importa directo.

Conecta con: lo consumen las capas superiores (`engines/`, `jobs/`, `api/services/`) que le pasan series ya leídas de Mongo y reciben los números calculados. No importa nada del proyecto (capa base de la regla de capas).
