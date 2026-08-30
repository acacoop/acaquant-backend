---
description: Corre perf_scan en modo estricto y resume findings por categoría
---

Corré `python -m scripts.perf_scan --strict` y resumí el output.

Pasos:

1. Ejecutá el comando. El script es estático (no toca la base), debería tardar < 5s.
2. Agrupá findings por código:
   - **PERF001** — `find()` sin projection (trae todo el doc).
   - **PERF002** — query en `for` (N+1, mover a `$in` o aggregate).
   - **PERF003** — `count(*)` sobre la tabla entera.
   - **PERF004** — misma query repetida (candidata a `@cached`).
3. Para cada finding, mostrá archivo:línea + sugerencia corta de cómo arreglarlo.
4. Si hay 0 findings → "Todo limpio ✓".
5. Recordá que se puede suprimir por línea con `# noqa: PERF00X` si el finding es un falso positivo legítimo (ej. query necesaria por consistencia del mes actual).

No apliques fixes automáticamente — solo reportá. Fixear requiere decisión del usuario caso por caso.
