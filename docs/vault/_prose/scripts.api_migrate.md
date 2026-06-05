Herramienta reusable de infra que re-sincroniza las colecciones `*API` derivadas (drop + insert por contrato de API) desde las colecciones fuente. Normaliza campos como `cuenta` "[139] NOMBRE" → `id_cuenta` + `nombre`. Cada sub-comando migra un par: accionistas, contrapartes, flujo, movimientos, aum, assets, flujos-titulos.
Se corre con `python -m scripts.api_migrate <cmd>`. Es el equivalente manual del encadenado automático de `jobs.sync_api_copies`.
Conecta con: lee CashFlow/Valuaciones/Trading y escribe las copias `*API.*API`; usa `core.mongo`.
