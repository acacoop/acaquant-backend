---
name: debug-motor
description: Playbook de troubleshooting de los motores de mercado (valores/options/curvas/forwards/breakevens/caucion/futuros_dlr/dolares). Cubre síntomas comunes, queries de diagnóstico y decisiones de recovery.
---

# Debug de un motor

Se aplica cuando el usuario reporta "el motor X no anda", "no llegan datos de Y", o una vista del frontend muestra data vieja / vacía en horario de mercado.

## 1. Confirmar horario

Motores de mercado corren **L-V 13:00–20:05 UTC** (10:00–17:05 ART). Antes de debuggear:

- Fuera de ese horario → NO es un bug, el motor está stopped por cron. Data live es de ayer.
- 04:00–11:20 UTC → Atlas pausado. La API devuelve errores de conexión por diseño. Esperar resume.

## 2. Status del service

En el Droplet:

```bash
systemctl status motor_<nombre>.service
journalctl -u motor_<nombre>.service -n 100 --no-pager
```

Síntomas y decisión:
- **active (running)**: el motor está vivo. Saltar a paso 3.
- **failed**: leer el traceback. Típicos:
  - `pyRofex` auth error → expiró la sesión (revisar `ROFEX_*` en `.env`).
  - `ServerSelectionTimeoutError` → Atlas pausado o MONGO_URI mal.
  - `ImportError` → cambio de imports tras refactor, probar `python -m engines.<nombre>` manual.
- **inactive (dead)**: probablemente fuera de horario o el cron no lo arrancó. `systemctl start motor_<nombre>.service` si hace falta.

## 3. Flujo de datos por motor

### motor_rofex (valores)
- Destino: `Trading.TimeSales` + `MarketSnapshot`.
- Chequeo: `db.TimeSales.findOne({ticker: "<X>"}, sort={timestamp: -1})` — timestamp debería ser de los últimos minutos.
- Si no llega data de un ticker específico: revisar que esté en `Trading.Curvas` o en `config.TICKERS_EXTRA_PRECIOS`.

### motor_curvas (enriquecimiento)
- Destino: setea `duration`/`TEA`/`TEM`/`convexity`/`paridad` en docs existentes de `TimeSales`.
- **Loop infinito conocido**: si `calcular_campos` retorna None para un doc, el motor ahora marca `duration: null` como sentinela para evitar re-procesar. Si un bono nunca se enriquece, chequear que:
  - Esté en `Trading.Curvas` con la `curva` correcta.
  - `flujos[]` tenga el shape correcto (CER porcentual, tasa_fija absoluto, soberanos USD).
  - Para CER: `cer_emision` definido; para soberanos: ticker con sufijo `D` o `C` (ya en USD).
- Query diagnóstico: docs sin enriquecer: `db.TimeSales.find({ticker: "<X>", duration: {$exists: false}}).sort({timestamp: -1}).limit(5)`.

### motor_forwards
- Destino: `Trading.ForwardsLive` (replaced cada 30s) + `ForwardsHistorico` (snapshot diario).
- Requiere TEA escrita por motor_curvas. Si `ForwardsLive` está vieja, el problema suele ser río arriba (curvas).

### motor_breakevens
- Destino: `Trading.BreakevensLive` + `Historico`.
- Debug paso-a-paso: `GET /api/manager/checks/breakevens` muestra todos los pares con método Buscar Objetivo vs Fisher lado a lado.
- Si faltan pares: chequear `MAX_DIFF_DIAS=20` — puede no haber CER con mismo vto. `MIN_DIAS_PLAZO=50` descarta pares con IPC ya publicado.

### motor_caucion / motor_futuros_dlr
- Destino: `Trading.CaucionSnapshot` / `FuturosDLRSnapshot`.
- Ambos replacean cada 5s. Si `updated_at` tiene más de 30s, el WS probablemente se cayó.

### motor_dolares
- Destino: `Valuaciones.DolarSnapshot` (_id='current', replaced cada 5s).
- Requiere que AL30, AL30D, AL30C estén suscriptos y operen. Si no hay operación en alguno, el doc queda con los últimos valores válidos.

## 4. Decisión de recovery

- **Reiniciar motor**: `systemctl restart motor_<nombre>.service`. Seguro siempre.
- **Restart + purge sentinela**: si hay `duration: null` "atascados" por un bug ya arreglado, purgar y dejar que re-enriquezca:
  ```
  db.TimeSales.updateMany({duration: null}, {$unset: {duration: ""}})
  systemctl restart motor_curvas.service
  ```
- **Re-backfill**: si hay histórico faltante (ej. Forwards), hay jobs específicos:
  - `python -m jobs.backfill_forwards`
  - `python -m jobs.backfill_breakevens`

## 5. Escalamiento

Si el problema persiste después de recovery:
1. Copiar últimas 50 líneas de `journalctl` + contexto.
2. Revisar `Manager.JobRuns` (si aplica al job batch) para ver historial de fails.
3. No tocar data histórica sin confirmación del usuario — purgar `TimeSales` viejo es destructivo.

## Criterios de éxito

- ✓ `journalctl` sin errores en los últimos 5 min.
- ✓ Colección destino con `updated_at` / `timestamp` reciente.
- ✓ Vista del frontend muestra data fresca.
