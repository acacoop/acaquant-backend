# Modelo de datos SQL (Postgres/Supabase) — diseño limpio, no espejo de Mongo

Subdoc de `docs/SQL.md`. La migración a SQL **rediseña** el modelo; NO copia el desparramo
de Mongo. Objetivo: cada dato vive una vez, tipado, indexado, relacionado por id; lo
derivado son **vistas**, no colecciones-cache mantenidas a mano.

## Conceptos (Mongo → SQL)

- **Colección → Tabla.** La tabla tiene columnas fijas y tipadas → fuerza consistencia
  (no más campos que aparecen/desaparecen, no más 3 grafías del mismo nombre).
- **Base de datos → Schema.** Un schema agrupa tablas por dominio (namespace). Ordena en
  vez de desparramar.
- **Duplicación → Normalización.** El dato vive UNA vez y se referencia por clave (FK). Ej:
  el nombre de la cuenta vive en `core.cuentas`, no copiado en cada operación.
- **Colección-cache → Vista / Vista materializada.** Lo derivado (rollups, informes,
  consolidados) se define como fórmula UNA vez; Postgres lo calcula/mantiene. Reemplaza
  colecciones como OpsSerieDiaria, ComercialCache, PnLTotalesCache, ConsolidadoCuentas + sus crons.

## Organización por schemas

```
core         -- dimensiones maestras (estables): cuentas, comitentes, operadores,
                contrapartes, instrumentos
negocio      -- hechos transaccionales: operaciones, movimientos, flujos
valuaciones  -- aum, pnl  (+ vistas materializadas de totales/consolidados)
mercado      -- datos de mercado: curvas, snapshots, timesales, precios, dolar
                (DISEÑAR tras medir shapes reales — REGLA #2, NO inventar)
comercial    -- actividad_mensual + VISTAS (informe, estado comercial, churn)
```

> El espejo actual (Fase B) vive todo en `public` (7 tablas planas) — fue para VALIDAR la
> migración de lectura, no es el diseño final. A medida que migramos cada dominio a
> source-of-truth, las tablas pasan a su schema y se normalizan.

## Principios de diseño (a raja tabla)

1. **Tipos reales**, no todo `text`: fechas `date`/`timestamptz`, plata `numeric`, flags `boolean`.
2. **Normalizar**: nombres/denominaciones/segmentación viven en la dimensión (`core.cuentas`,
   `core.comitentes`), los hechos referencian por `id_cuenta`. Mata las inconsistencias que
   el harness ya detectó (misma cuenta, varias grafías).
3. **Derivado = vista.** Si un dato se puede calcular de otros, es una `VIEW` (o
   `MATERIALIZED VIEW` con refresh programado si pesa). NO una tabla que se llena por cron.
4. **Índices por patrón de acceso** (medidos con `EXPLAIN`), no "por las dudas".
5. **Calidad de dato en la frontera**: al escribir a SQL (dual-write / ingesta), limpiar lo
   sucio (espacios, decimales, mayúsculas) — la deuda de datos de Mongo no se hereda.

## Cómo se migra cada dominio (patrón repetible)

Por dominio (negocio → comercial → valuaciones → mercado):
1. **Medir** los shapes reales en Mongo (diag read-only, REGLA #2).
2. **Diseñar** las tablas limpias del schema (tipos, normalización, FK donde no haya
   huérfanos, índices).
3. **Migrar lecturas**: servicio SQL + harness de comparación vs Mongo + flag dual-run.
4. **Dual-write**: el job/motor escribe SQL (y Mongo transitorio como plan B).
5. **Cortar Mongo** del dominio solo cuando el check de dependencias da 0.

## Estado

- `core` + `negocio` (operaciones) — espejo en `public`, lectura migrada (vista OPERACIONES). ✅
- Resto — pendiente, siguiendo el patrón de arriba.

## Lo derivado que hoy son colecciones y pasarán a VISTAS

`CashFlow.OpsSerieDiaria`, `Clientes.ComercialCache`, `Valuaciones.PnLTotalesCache`,
`Valuaciones.ConsolidadoCuentas`, rollups de opciones/comercial → en SQL son vistas
(materializadas si pesan). Se elimina el cron que las llena.
