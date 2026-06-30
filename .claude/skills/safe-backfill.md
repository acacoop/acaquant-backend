---
name: safe-backfill
description: >
  Aplicar SIEMPRE que el pedido implique correr un backfill, migración, --full,
  update_many, o cualquier operación que toque muchos documentos de prod (Atlas
  M10). Operacionaliza la REGLA #4. NO entregar un backfill sin pasar por estos
  pasos — el incidente 2026-06-04 (CPU 100% tres veces) fue por saltearlos.
---

# safe-backfill — backfills/migraciones que NO tiran el CPU

El M10 tiene 2 vCPU y comparte el CPU con los motores de mercado (13-20 UTC L-V).
Un scan sin índice sobre cientos de miles de docs lo clava. Reglas #2 y #4.

## NUNCA

- `--full` que re-lea toda una colección sin índice (`update_many({campo_sin_indice})`,
  `find({})` sobre 100k+, `find_one({campo: {$ne: ...}}, sort=...)`).
- Declarar algo "liviano/seguro" sin haberlo MEDIDO en prod. Prohibido.
- Correr backfills pesados en horario de rueda (13-20 UTC L-V).

## El procedimiento (en orden, sin saltear)

1. **MEDIR el costo ANTES de codear** (REGLA #2). Escribir un diag read-only que:
   - cuente los docs realmente afectados (`count_documents` con el filtro real),
   - corra `explain()` y verifique IXSCAN vs **COLLSCAN** (`keysExamined > 0`),
   - dé el grano/cardinalidad si es un rollup.
   El user lo corre, devuelve los números. Recién ahí se decide.

2. **SCOPEAR** a lo que realmente cambia. No escanear toda la tabla si se puede
   filtrar por un índice. Ej: corregir solo `WHERE campo = 0 OR campo IS NULL`.

3. **BATCHEAR + THROTTLE.** Procesar por ventanas de fecha **indexadas** (usar el
   índice `fecha`/`concertacion`), con `time.sleep()` entre lotes. Nunca un
   `UPDATE`/`executemany` gigante de una sola transacción.

4. **UPDATE puro, sin upsert duplicador**, cuando la fila ya existe → matchea por
   clave única (PK: boleto, id_cuenta) → no inserta → no duplica. Verificar que la
   clave del match esté **indexada** (`EXPLAIN` para confirmar que usa el índice).

5. **Vía `run_job.sh`** (lock + timeout) y, salvo que sea liviano y scopeado,
   **fuera de rueda** (después de 20 UTC / motores apagados).

6. **Idempotente.** Cortarlo a la mitad y re-correrlo no rompe nada. Incluir
   `--dry-run` (read-only) que estime sin escribir.

## Entrega (REGLA #0 + #3)

Script en `scripts/`, commit + push. Y SIEMPRE la explicación ejecutiva:
`📋 Qué soluciona` / `📋 Qué genera` (qué deployar, costo, riesgo).

## Anti-pattern del incidente (para no repetirlo)

Un `--full` a ciegas que re-lee cientos de miles de filas sin índice, en rueda →
CPU 100% (incidente 2026-06-03/04). La versión segura procesa por ventanas de fecha
indexadas + `sleep` → cobertura total, cero spikes. Medir el costo ANTES (`EXPLAIN`
/ contar filas afectadas) antes de correr.
