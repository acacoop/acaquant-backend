Cache precalculada del Tablero Comercial en la base `Clientes`. Materializa el rollup por operador/cuenta (QUIÉN + ACTIVIDAD + TAMAÑO + estado comercial por días desde última op) para no recomputarlo on-the-fly en cada request.

Conecta con: la escribe `jobs/comercial_rollup.py` (precompute); la lee `api/services/comercial.py` (vista `/api/manager/comercial`). Se precalienta además con `jobs/comercial_warm.py`.
