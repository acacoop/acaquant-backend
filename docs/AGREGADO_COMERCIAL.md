# Agregado del Tablero Comercial — diseño

**Estado: DISEÑO APROBADO, sin implementar.** Este doc es el paso previo al código.
Cuando se implemente, se pliega a la sección "Tablero Comercial" del `CLAUDE.md` raíz
y este archivo se borra (REGLA #5).

## 1. Por qué, con números medidos (2026-08-13)

`/api/operaciones/comercial/operador` y `/comercial/serie` suman **~3.100 s por
semana** de tiempo de API. Del lado de la base son el mayor bloque de LECTURA:

| query | llamadas | media | total acumulado |
|---|---|---|---|
| `SUM(valuacion)` por fecha sobre `portafolio.tenencia` | 48.420 | 35,0 ms | 1.696 s |
| `SUM(CASE WHEN moneda…)` por fecha sobre `negocio_movimientos` | 40.703 | 39,0 ms | 1.589 s |
| ídem agrupado por `id_cuenta` | 39.588 | 38,2 ms | 1.512 s |
| ídem, otra ventana | 22.295 | 39,0 ms | 870 s |
| ídem | 22.265 | 38,1 ms | 848 s |
| `SUM(valuacion) AS aum` sobre `tenencia` | 40.156 | 14,3 ms | 573 s |

El perfil del endpoint (cProfile) da **8 viajes a la base de ~56 ms cada uno** y
Python en el 11%. O sea: **no es N+1** (ese fue el caso de `/api/derivados/agro`, que
se arregló agrupando y salió gratis) — son agregaciones caras de verdad.

Y **crece solo**: `negocio_movimientos` va por 413.919 filas y suma todos los días.

## 2. Lo que YA se descartó, para no re-proponerlo

- **Índices.** `index_advisor` (con `hypopg`) sobre las 6 queries: mejoras de **0% a
  3%**. La mejor sugerencia (`btree(categoria)`) no justifica el costo de escritura en
  una tabla que el cron toca cada 30'. **Descartado con datos.**
- **Cache.** Cambia frescura (el user pidió explícitamente no cambiar funcionalidad) y
  **no escala**: la query sigue engordando y el primer usuario después de cada
  expiración paga el precio completo. **Descartado por criterio.**

## 3. El diseño

Mismo patrón que `operaciones.ops_agregado_diario` (HOT/COLD), que ya funciona en
esta casa:

- **Días CERRADOS** → pre-agregados en tabla. No cambian nunca (salvo corrección, ver §4).
- **HOY** → se calcula en vivo, como ahora.
- La lectura hace `UNION` de las dos partes.

**Grano propuesto:**

```
operaciones.comercial_agregado_diario
  fecha, id_cuenta, moneda  →  volumen_ars, volumen_usd, n_boletos
portafolio.tenencia_agregado_diario
  fecha, id_cuenta          →  aum
```

**Por qué ese grano alcanza (la clave de todo):** el tablero filtra por operador,
`nivel_1..5`, `referido` y `division` — y **todos esos son atributos de la CUENTA**
(viven en `clientes.comitentes`), no del boleto. Agregando a `(fecha, id_cuenta)` no se
pierde ninguna combinación de filtros: se agrega el hecho y se sigue joineando la
dimensión en la lectura. Si algún filtro futuro fuera atributo del BOLETO (ej. mercado
o especie), habría que sumarlo al grano o quedaría fuera del agregado.

**Ganancia esperada:** leer ~200 filas de agregado por cuenta-mes en vez de sumar
413.919. Los mismos números exactos.

## 4. ⚠️ La trampa de corrección (verificada en el código, 2026-08-13)

`ops_agregado_diario` detecta días sucios con `ingestado_en`. Acá **eso no alcanza**:

```python
# jobs/negocio_movimientos.py — marcar_anulados()
"UPDATE negocio_movimientos SET anulado_en = now() "
" WHERE fecha = %(fecha)s AND anulado_en IS NULL AND comprobante <> ALL(%(vivos)s)"
```

**La anulación NO toca `ingestado_en`.** Un boleto que Aunesa deja de devolver se marca
anulado y su día **no se vería sucio** → el agregado seguiría contando un boleto
anulado y el tablero mostraría de más, **en silencio y para siempre**.

Es exactamente el incidente de los movimientos de tesorería que el back office detectó
por un faltante clavado: un error silencioso de plata es peor que un endpoint lento.

**Solución elegida:** el detector de días sucios mira **las dos** columnas —

```sql
WHERE GREATEST(max(ingestado_en), max(COALESCE(anulado_en, 'epoch'))) > <último recompute>
```

No se toca el writer (`ingestado_en` conserva su significado: "cuándo se ingestó").

## 5. Plan de implementación

1. **Tabla + writer**, sin leerla todavía. Job que recomputa por día sucio.
2. **Backfill** de días cerrados: scopeado, batcheado, fuera de rueda (REGLA #4).
3. **Verificación ANTES de leerla**: `scripts/diag_comercial_agregado.py` compara
   agregado vs live para N cuentas × N fechas y exige **diferencia 0.000000** — el
   mismo patrón que `diag_tesoreria_front_vs_back`, que ya se usó para validar el saldo
   final de Tesorería (48 filas, diferencia 0).
4. **Recién ahí**, cambiar la lectura a agregado+hoy.
5. Medir con `diag_costo_real` y comparar contra los números de §1.

## 6. Riesgos

- **Drift** (el agregado deja de coincidir con la realidad). Mitigado por §4 + el diag
  de comparación, que puede quedar como control periódico.
- **Filtro nuevo sobre el boleto** rompería el grano (§3). Documentado arriba.
- **Doble fuente de verdad**: la fórmula de volumen (con conversión por `mep`) tiene que
  vivir **una sola vez**. Si el writer y la lectura live la escriben por separado, van a
  divergir — es el mismo error que se corrigió sacando la fórmula del saldo final del
  frontend.
