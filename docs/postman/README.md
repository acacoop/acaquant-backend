# Postman — Interbanking

Colección para **explorar y validar** las APIs de Interbanking antes de integrarlas
al backend. Todo lo de acá es **solo lectura** (únicamente GET): no hay un endpoint
que origine pagos ni que mueva plata, así que probar no tiene riesgo operativo.

## Archivos

| Archivo | Qué es |
|---|---|
| `interbanking.postman_collection.json` | La colección: 12 requests agrupados por API, con scripts que resuelven el token solo y muestran las respuestas legibles en la Console. |
| `interbanking.postman_environment.json` | El Environment con las variables. **Se versiona VACÍO de secretos** — ver abajo. |

## ⚠️ Los secretos NO se commitean

`interbanking.postman_environment.json` está en el repo con `ib_client_id` y
`ib_client_secret` **vacíos**, a propósito. Se completan en Postman después de
importar y **no se vuelve a exportar el archivo al repo con los valores puestos**.

Si algún día hay que exportarlo (para compartirlo con alguien), Postman ofrece
"Export → with unresolved secret variables": esa opción vacía los campos marcados
como `secret`, que es justamente por qué están tipados así.

Las credenciales definitivas van a vivir en el `.env` del Droplet
(`INTERBANKING_CLIENT_ID`, `INTERBANKING_CLIENT_SECRET`, `INTERBANKING_CUSTOMER_ID`),
igual que el resto — ver `docs/SECRETS.md`.

## Puesta en marcha

1. Postman → **Import** → arrastrá los DOS archivos.
2. Arriba a la derecha, seleccioná el environment **"Interbanking — PROD"**.
3. Ojo del environment (👁) → **Edit** → completá:
   - `ib_client_id` y `ib_client_secret` (los del alta de la aplicación en el portal),
   - `customer_id` (código de abonado, formato `A12345B`; sale de Interbanking →
     Administración → ABM → Configuración Datos → Datos de Empresa).
4. Abrí la Console: **View → Show Postman Console** (o `Ctrl+Alt+C`). Los scripts
   escriben ahí el resumen legible de cada respuesta.
5. Corré `00 · Auth → TOKEN · client_credentials (manual)`.
   - **200** → listo, las credenciales andan.
   - **400/401** → cambiá `ib_auth_style` de `basic` a `body` en el environment,
     habilitá los dos campos deshabilitados del body del request, y reintentá.
     Son las dos formas estándar de mandar las credenciales en `client_credentials`;
     no sabemos cuál acepta el CAS de Interbanking hasta probarlo.
6. Corré `01 · Cuentas → Listar cuentas`. Autocompleta `account_number`,
   `bank_number` y `currency` con la primera cuenta que venga, así el resto de los
   requests quedan disparables sin tocar nada.
7. A partir de ahí, cualquier request de la colección.

**El token se maneja solo.** El pre-request de la colección lo pide, lo cachea y lo
renueva cuando faltan menos de 60 segundos para que venza. El request manual del
paso 5 existe solo para diagnóstico.

## Qué mirar en cada respuesta

Lo importante de esta ronda de pruebas no es que devuelva 200, sino contestar
preguntas que hoy no podemos responder sin datos reales:

| Pregunta | Dónde se responde |
|---|---|
| ¿Las cuentas de Interbanking son las mismas que hoy están en `tesoreria_cuentas`? | `01 · Cuentas → Listar cuentas` |
| ¿`initial_operating_balance` coincide con el saldo inicial que el back office carga a mano? | `02 · Saldos → actual`, contra la vista de Tesorería del mismo día |
| ¿El histórico llega realmente a 180 días? | `02 · Saldos → histórico`, moviendo `date_since` |
| ¿El saldo final que calcula la grilla BANCOS coincide con el `ending_balance` del banco? | `03 · Extractos` |
| ¿Los movimientos traen un ID estable para persistir idempotente? | `04 · Movimientos v1` vs `v2` — v1 declara un campo `id` que v2 no tiene |
| ¿Qué valores reales toma `status` en transferencias? | `05 · Transferencias → detalle` (el script los agrupa) |
| ¿Los VEPs pagados traen el número que hoy se tipea a mano? | `05 · Transferencias → comprobantes` |

## Límites a respetar

- **100 llamadas por minuto** (plan contratado). El Collection Runner de Postman
  puede pasarse si le das muchas iteraciones: si vas a barrer todas las cuentas,
  ponele un delay de ~700ms entre requests.
- Consultas históricas: **180 días hacia atrás, en ventanas de 60 días** por llamada.
- `account-type` **filtra**: para ver el universo completo de cuentas hay que correr
  el listado dos veces, con `CC` y con `CA`.
- **Movimientos usa otro base URL** (`.../api/prod` + `/v1` o `/v2` en el path).
  El resto usa `.../api/prod/v1`. Es una inconsistencia de Interbanking, no un error
  de la colección.

## Después de Postman

Cuando las respuestas estén validadas, lo que sigue es un cliente en
`core/interbanking.py` (token con cache + los cinco endpoints) y un
`scripts/diag_interbanking_cuentas.py` que cruce el listado real contra
`tesoreria_cuentas`. Recién con ese número medido se decide el modelo de datos
(REGLA #2: nada de asumir cómo lucen los datos de producción).
