Capa de servicio que reúne las magnitudes derivadas de la curva local y los únicos derivados puros (futuros DLR de ROFEX): futuros DLR, forwards y breakevens. Separa por dominio de `renta_fija.py` (allá los cash bonds, acá lo que se construye sobre ellos). `get_futuros_dlr` filtra contratos ya vencidos que el motor deja como fantasma. Cacheado con TTLs cortos.

Conecta con: lee snapshots escritos por `engines.futuros_dlr`, `engines.forwards` y `engines.breakevens` (`Trading.FuturosDLRSnapshot` y colecciones de forwards/breakevens); lo consume el router `/api/derivados`.
