---
name: index-health
description: >
  Usar cuando haya CPU alto inexplicable en el M10, queries lentas, o ANTES de
  confiar en que un campo "está indexado". Audita índices: el trap partial→COLLSCAN
  (causa del incidente 2026-06-04), índices muertos, y campos calientes sin índice
  usable. También al crear índices nuevos, para no caer en el mismo trap.
---

# index-health — que los índices REALMENTE se usen

El incidente 2026-06-04: `Operaciones.uq_boleto` era un índice **PARTIAL**
(`partialFilterExpression: {boleto: {$type: [...]}}`). Regla de Mongo: un índice
parcial NO se usa salvo que la query incluya su filtro. El upsert `{boleto: X}` a
secas no lo incluía → COLLSCAN de 488k por cada escritura → CPU 100% todo el día.

## El trap principal: "tiene índice" ≠ "el índice se usa"

Un campo puede tener índice y la query igual hacer COLLSCAN si:
- El índice es **PARTIAL** y la query no incluye el `partialFilterExpression`.
- El índice tiene **collation** distinta a la de la query.
- La query usa `$ne`/`$nin`/`$regex` no anclado/`$not` (no selectivos → COLLSCAN).
- Es **sparse** y la query necesita docs sin el campo.

## Procedimiento de auditoría

1. **Confirmar con `explain()`**, no con `index_information()`. Que un índice
   exista NO prueba que se use. La prueba es `find(filtro).explain()` →
   `planSummary` (COLLSCAN ❌ / IXSCAN ✓) y `keysExamined > 0`.
   Herramienta: `scripts/diag_indice_boleto.py` (explain + spec de cada índice).

2. **Barrer índices partial** sospechosos en todas las bases:
   `scripts/diag_indices_partial.py` → lista los partial con su filtro. Los UNIQUE
   partial sobre campos de ID (boleto, comprobante, id_cuenta) que la app consulta
   por igualdad son bombas de tiempo → mismo COLLSCAN que boleto.

3. **Diagnóstico en runtime** desde el Atlas Query Profiler (consola Atlas →
   Profiler): mirar `Docs Examined : Returned`. Si es miles:1 = COLLSCAN. El
   `Full Parsed Log` da el `command` exacto + `planSummary` + `appName`.

4. **Índices muertos**: `$indexStats` (`scripts/audit_db.py`) → los que nunca se
   usan ocupan RAM y enlentecen escrituras. Candidatos: medir antes de dropear.

## Fix del trap partial

Recrear el índice como **plano** (usable por la query de igualdad): crear el nuevo
PRIMERO, validar con `explain()` que da IXSCAN, recién ahí dropear el partial (sin
ventana sin restricción única). Solo si no hay docs con el campo vacío (sino daría
E11000 — chequear `count_documents`). Patrón: `scripts/fix_indice_boleto.py`.
Correr **fuera de rueda** (construir el índice escanea la colección una vez).

## Al CREAR un índice nuevo

Si necesitás unicidad solo sobre docs que tienen el campo, preferí **sparse** (lo
usa la query de igualdad) antes que **partial con $type/$exists** (NO lo usa la
query de igualdad). Y siempre validá con `explain()` que la query real lo agarra.
