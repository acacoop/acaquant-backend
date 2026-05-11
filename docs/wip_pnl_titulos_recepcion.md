# WIP — Recepción de valores rompiendo PNL TÍTULOS

**Estado al 2026-05-11.** Doc para retomar la charla sin perder contexto.

---

## El problema

En la vista `/aum → VALUACIONES → PNL TÍTULOS` (y TOTALES), algunas
posiciones aparecen con `pnl_no_realizado` desproporcionadamente alto.
Causa: hay boletos en `CashFlow.NegocioMovimientos` que **suman
nominales en `Valuaciones.AuM`** pero **no entran al cost-basis** del
motor PnL, porque caen en `categoria='otro'` y `_CATS_RELEVANTES` de
`api/services/pnl.py:157` no las contempla.

Resultado: `qty_aum` sube, `costo_remanente` no → fila muestra todo el
valor de mercado como ganancia.

Estos boletos vienen tipificados con `informacion` que arranca con
"Recepción ..." (a veces "Recepción de valores", "Recepción ECHEQ",
"Recepción pagaré", etc.), siempre con `op=null`.

---

## Diagnóstico

Se creó `scripts/diag_boletos_recepcion.py` (commits `4d76bc3`,
`f6e32a9`, `c84a8f7`) que normaliza códigos únicos del `informacion`
(ECHEQ `*BIN161200039` → `*XXX`, números largos → `NNN`, fechas →
`DD/MM/YYYY`) y agrupa por la "forma" del texto.

Output al 2026-05-10: **3066 boletos en 325 grupos distintos**.

Top grupos (count desc):
- `Recepción ECHEQ *XXX` → 856 (otro)
- `Recepción de valores` (pelado) → 763 (otro)
- `Recepción de valores - Desafectación de garantías ACSA` → 233 (otro)
- `Recepción pagaré #UBINNN` → 123 (otro)
- `Recepción de valores - la segunda` (variantes "LA SEGUNDA", "La Segunda", "la 2da") → ~210 (otro)
- `Recepción pagaré #UMVNNN` → 102 (otro)
- `Recepción de valores - Transferencia entre cuentas mismo titular` → ~85 (transferencia ✓)
- `Recepción de valores - CANJE` / `canje` / `CANJE CREDICUOTAS` / `CANJE ON` / `Cambio de especie` → ~75 (otro)
- otros pagaré (#UCONNN, #UACNNN, #MAVNNN, etc.) → ~150 (otro)
- alquiler / dev alquiler / caución alquiler → ~150 (otro / caucion_otro)
- transferencias entre brokers (BIND, balanz, adcap, INVIU, PPI, COCOS,
  iol, comafi, ST SECURITIES, IOL, BAVSA, Industrial Valores, Patago,
  Galicia) → ~80 (otro)
- desafectación garantías ROFEX/USDC → ~5 (otro)
- splits CEDEAR / conversión CEDEAR↔ADR → ~3 (otro)
- operación por afuera (OTC) → ~2 (otro)
- otros minoritarios (cierre CERA, dividendos en especie, reasignación,
  ajuste CP, etc.) → resto

---

## Clasificación: rompe vs no rompe

El user aclaró el criterio (mensaje del 2026-05-10):

> "Cuando vos recibís algo que no tiene un movimiento de compra es
> cuando se rompe. En desafectación de títulos no pasa nada porque eso
> es algo contable nada más, el movimiento de compra está y en el AuM
> sigue estando."

O sea: rompen SOLO los que traen títulos **desde afuera del sistema**
(sin compra previa registrada). Los movimientos internos no rompen
porque la compra original ya existe y AuM la refleja correctamente.

### ROMPEN (vienen de afuera, sin compra previa)

| Grupo | Boletos aprox |
|---|---|
| `CANJE` / `canje` / `CANJE CREDICUOTAS` / `CANJE ON` / `Canje IRSA` / `Cambio de especie` / `canje del bind` | ~75 |
| `Split CEDEAR` / `Conversión CEDEAR - ADR` / `Split 2-1` | ~3 |
| `Operación por afuera` (OTC) | ~2 |
| Transferencias entre **brokers externos**: `de cocos` / `de adcap` / `TRF de BIND` / `BIND` / `balanz` / `INVIU` / `PPI` / `ST SECURITIES` / `iol` / `comafi bursatil` / `TRF PPI` / `Industrial Valores` / `Patago` / `Galicia` / `BAVSA` / `Bind` / `IOL` | ~80 |
| `Recepción de valores` (pelado, sin sufijo) — ambiguo | ~763 |
| `dividendo de Avaldi` (acreencia en especie?) | 1 |

### NO ROMPEN (movimientos internos, compra previa existe)

| Grupo | Boletos aprox |
|---|---|
| `Desafectación de garantías ACSA/ROFEX` | ~240 |
| Alquiler / dev alquiler / caución alquiler (entre cuentas internas) | ~150 |
| `Transferencia entre cuentas mismo titular` (ya `transferencia` ✓) | ~85 |
| `LA SEGUNDA` / variantes (cuenta interna) | ~210 |
| `Comitente propia` / `Cuenta propia` / `cuenta propia inviu` / `cuenta propia Adcap` | ~20 |
| `CIERRE CUENTA CERA` | ~10 |
| `Reasignación` / `Ajuste de CP` / `Ajuste por conciliación` / `Trf liquidada fuera de mercado` | ~5 |
| ECHEQ / pagaré / cheque (no son títulos, ticker=null) | ~1300 |

---

## Decisiones pendientes (para retomar)

**1. El bucket grande "Recepción de valores" pelado (763 boletos)** —
sin sufijo, no se puede inferir nada del texto. Posibles reglas:

- (a) Asumir "todo `Recepción de valores` sin más sufijo es de afuera
  (rompe)". Simple pero impreciso.
- (b) "Depende — chequear caso por caso": para cada uno, ver si hay
  boleto de compra del mismo ticker en los últimos N días en la cuenta.
  Si no hay, asumir que rompe.
- (c) Otra regla heurística que el user defina.

**Pendiente confirmar con el user.**

**2. ¿La clasificación coincide con la del user?** El user todavía no
confirmó si hay grupos para mover de una pila a la otra (ej. quizá
"LA SEGUNDA" sí rompe porque viene de afuera, o algún alquiler externo).

**Pendiente confirmar con el user.**

---

## Próximo paso (cuando se retome)

Una vez confirmadas las decisiones 1 y 2, el plan tentativo es:

1. **Modificar `api/services/aunesa_negocio.py::categorizar()`** para
   sub-tipificar las "Recepción ..." según el patrón:
   - `recepcion_externa` (rompe → necesita cost-basis)
   - `recepcion_interna` (no rompe → motor lo ignora limpiamente)
   - `recepcion_no_titulo` (ECHEQ/pagaré/cheque)
   - `recepcion_canje` (subtipo especial — ideal heredar cost-basis del
     activo origen, fallback precio=0)

2. **Backfill `jobs/categorizar_negocio_movimientos`** o similar para
   re-categorizar los 3066 boletos históricos.

3. **Modificar `api/services/pnl.py`** para sumar las nuevas
   categorías a `_CATS_RELEVANTES` con tratamiento por tipo:
   - `recepcion_externa` → tratar como compra (precio del boleto si
     viene, sino 0).
   - `recepcion_canje` → como compra heredando cost-basis (complejo)
     o precio=0 (simple).
   - `recepcion_interna` / `recepcion_no_titulo` → no incluidas.

4. **Validar visualmente en `/aum → VALUACIONES → PNL TÍTULOS`** las
   posiciones que estaban infladas. Restart api.service necesario.

---

## Archivos / commits relevantes de esta charla

- `scripts/diag_boletos_recepcion.py` (commits `4d76bc3`, `f6e32a9`, `c84a8f7`)
- `api/services/aunesa_negocio.py` (función `categorizar` — pendiente modificar)
- `api/services/pnl.py:154-157` — `_CATS_RELEVANTES`
- `docs/MOTOR_VALUACIONES.md` — doc canónico del motor PnL

## Histórico relevante de la charla previa

Esta sesión también incluyó (ya cerrado, no es parte del WIP):
- Backfill cierres EOM jul-2025 a feb-2026 con `jobs/aum_backfill_historico` (commit `e42a7c3`).
- Fix bucket en `valuacion_mensual` para no reasignar snaps del 1° al
  mes anterior (commit `ac7dcaa`).
- Cleanup de los 8 snaps viejos del 1° de mes (commit `813e315`,
  ejecutado en droplet por el user).
- Fix `valuacion` para `D16E6` (Letras de Liquidez del BCRA) que
  faltaba `/100` (commits `270458e`, `715cdbd`, ejecutado en droplet).
