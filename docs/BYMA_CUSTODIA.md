# BYMA CUSTODIA (CVSA) — **[VIVO]**

Lo que la **Caja de Valores** tiene registrado a nombre nuestro. Doc madre:
`docs/ARQUITECTURA.md`. Cliente: `core/byma_custodia.py`. Vista: `/back-office`
→ tab **CUSTODIA**.

> **Todo lo de acá está MEDIDO contra producción, no leído del OpenAPI.** El
> spec que publica BYMA omite o contradice casi todo lo que importa, y cada
> renglón marcado ⚠️ es algo que costó un error en pantalla antes de entenderse.

---

## 0. Qué es, y qué NO es

| | Aunesa | BYMA / CVSA |
|---|---|---|
| Qué es | El back-office tercerizado | El **registro**: lo que la Caja tiene anotado |
| Nos da | Tenencia valorizada, saldos, informes | Tenencia en nominales, por estado |
| Quién manda | — | **CVSA**: cuando difieren, la razón legal es de la Caja |

No son intercambiables: CVSA es la posición **registral liquidada**; Aunesa
(`tenencia_live`) es una posición **proyectada**. Mezclarlas sería otro dato
partido (`core/duplicados.py`).

---

## 1. Autenticación

```
POST https://api.byma.com.ar/oauth/token/
Content-Type: application/x-www-form-urlencoded
client_id / client_secret / grant_type=client_credentials / scope
```

- **El token dura 86.400 s (24 h).** Se cachea por scope con 10 min de margen.
- Dos scopes, uno por API: `custodysecurities.read` · `custodyfees.read`.
- Env: `BYMA_CLIENT_ID`, `BYMA_CLIENT_SECRET`, `BYMA_PARTICIPANT_CODE` (= `74`).
- IdP: **Okta** (el `client_id` empieza con `0oa` — cero, no letra O).

⚠️ **Las credenciales las genera el portal, no se transcriben.** En el método →
*Autenticación* → *"¿Cómo obtener un token de acceso?"* → elegir la aplicación
en el desplegable: ahí aparecen los cuatro valores. Un carácter mal copiado da
un 400 cuyo cuerpo dice 401 (el gateway envuelve el error de su IdP), y ese
error es indistinguible de una credencial revocada.

Ambientes (los dos públicos; los `*.vortex.byma.com.ar` del OpenAPI **no existen
en el DNS de internet**, son internos de BYMA):

| | Host |
|---|---|
| Producción | `api.byma.com.ar` · `200.42.14.174` |
| Homologación | `hs-api.byma.com.ar` · `200.42.14.176` |

Solo los métodos **POST/PUT** de Custodia y Clearing requieren pasar por
homologación. Los GET van directo a producción.

---

## 2. Los métodos

### Custody Securities v1 — `api.byma.com.ar/custody-securities/v1`

| Método | Params | Modo | Formato | Techo |
|---|---|---|---|---|
| `GET /holdings` | `balanceDate`, `participantCode` | **asíncrono** | CSV | 100/s · 10.000/día |
| `GET /holdings/accounts` | `accountNumber`, `participantCode`, `subBalanceType?` | inmediato | JSON | ídem |
| `GET /transactions` | `settlementDate`, `participantCode` | **asíncrono** | CSV | ídem |
| `GET /transactions/today` | `participantCode` | inmediato | CSV | ⚠️ **2/min · 100/día** |
| `POST /transactionsbyreference` | `instructionReferences`, `participantCode`, `settlementDate` | inmediato | CSV | ⛔ **ESCRIBE** |

⛔ **`transactionsbyreference` NO está en `core/byma_custodia.py`, a propósito.**
El OpenAPI lo presenta como una consulta; la ficha del portal dice *"ejecuta una
tarea de **escritura**… el origen expuesto se verá afectado con cada solicitud"*.
Un cliente de lectura no expone un método que escribe. Congelado por test.

⚠️ **`balanceDate` solo acepta los últimos 7 días** — más atrás CVSA lo purgó.
Por eso el histórico **no se puede reconstruir a pedido**: o se guarda día a día,
o se perdió.

### Custody Fees v2 — `api.byma.com.ar/custody-fees/v2`

Cinco métodos (`/daily-fees/{equity,fixed-income}`, `/monthly-fees/{equity,
fixed-income,nsi}`). **Sin integrar.** El OpenAPI declara cero parámetros; el
propio endpoint reveló que falta **`accountgroupcode`**, cuyo valor todavía no
tenemos. Cuando esté: es la única fuente que trae `cvsaIdentifier` **e** `isin`
en la misma fila, o sea la tabla de traducción, además del costo de custodia
—que hoy no está modelado en ningún lado del sistema—.

---

## 3. ⚠️ Las seis trampas del gateway

Ninguna está en el OpenAPI. Las seis viven resueltas en `core/byma_custodia.py`.

1. **`Accept: application/json` da HTTP 406.** Cada método elige su formato y el
   gateway no negocia. Se manda `Accept: */*` y se parsea lo que venga.

2. **Los métodos asíncronos contestan 409, no 200.** La primera llamada dispara
   el trabajo y devuelve `{"code": 409, "uuid": "..."}`; hay que repetirla con la
   cabecera **`X-UUID`** hasta que conteste 200. El cliente lo hace con backoff,
   **tope de intentos y timeout**: un poll sin techo no falla nunca, simplemente
   no termina, y nadie se entera.

3. **`meta.count` MIENTE.** Medido: `"count": 3` sobre un `result` de **cuatro**
   filas. No se puede usar para paginar ni para validar. Se cuenta
   `len(result)`; si difieren queda un `warning` — el día que lo arreglen
   queremos saberlo, porque hoy ignoramos un campo a propósito.

4. **El JSON viene envuelto** en `{"meta": {...}, "result": [...]}`. El OpenAPI
   declara un objeto plano.

5. **El CSV se separa con `;`**, sin cabecera garantizada, y trae un campo que no
   figura en ninguna documentación: **`identAccountComposite`** (el id interno y
   estable de la cuenta en CVSA).

6. **Hay sufijos de formato en el path** (`.json`, `.csv`, `.dict`, `.swagger`)
   que el OpenAPI no menciona. `.swagger` devuelve la spec real del método —
   aunque en Custody Fees contesta 500.

---

## 4. ⚠️ Identidad — REGLA #9

### La cuenta: se PARTE, no se interpreta

```
accountNumber = "74/805"
                 │   └── nuestro clientes.cuentas.id_cuenta
                 └────── participantCode (74)
```

`core.byma_custodia.id_cuenta()` es el único lugar que conoce esa forma.
Devuelve `None` si el formato no es el esperado: un formato distinto es algo que
hay que mirar, no algo que se adivina.

### La especie: NO hay pareo por nombre

`cvsaIdentifier` es un **número interno de CVSA** (`5921`, `9422`, `58790`). No
se parece a ningún ticker nuestro. El puente es **`portafolio.assets.codigo_cnv`**.

⚠️ **`codigo_cnv` se llama así por historia pero guarda el código de CVSA.**
Verificado sobre **254 assets** que ya lo tenían cargado a mano: **cero
divergencias** contra el maestro de BYMA. No se renombra la columna (la usan el
panel Manager → ASSETS, `jobs/assets_autofill` y `api/services/assets_sql`), pero
el nombre miente y hay que saberlo.

**El código es por INSTRUMENTO, no por especie ni por plazo.** Medido sobre el
maestro (17.125 filas → 6.362 tickers):

```
AL30 (ARS) · AL30C (EXT) · AL30D (USD)  →  todos cvsa 5921, isin ARARGE3209S6
```

Por eso alcanza **una columna en `assets`** y no hay que tocar `mercado.especies`.
Y por eso **`codigo_cnv` no lleva `UNIQUE`**: 6.362 tickers → 1.725 códigos.

El maestro vive en `scripts/data/cvsa_especies.csv` y se carga con
`python -m scripts.backfill_codigo_cvsa` (dry-run por default; nunca pisa un
valor cargado; lo que no está en el maestro no se toca, así que los FCI quedan
intactos). BYMA va a dar una API para mantenerlo — ahí el CSV se reemplaza por
una llamada y el script queda igual.

---

## 5. `subBalanceType` — lo que Aunesa no nos da

Quince estados. El que más importa: **qué parte de la tenencia NO se puede
entregar ni garantizar**, información que hoy no existe en el sistema.

```
AVAILABLE · RESERVED · PENDING_DELIVERY · BLOCKED · BLOCKED_FOR_PLEDGE
BLOCKED_FOR_CA · CCP_RESERVED · CCP_RESTRICTED · EMBARGO · INTERIM
PENDING_REDEMPTION · ATTEND_GENERAL_MEETING · PROOF_OF_OWNERSHIP
RESERVED_FOR_PLEDGE · BLOCKED_COLLATERAL
```

Una cuenta puede tener el mismo papel en dos estados a la vez: por eso el estado
es parte de la PK de `portafolio.custodia_cvsa`.

---

## 6. Changelog

- **v1** — Discovery completo contra producción, cliente, maestro de especies
  cargado en `assets.codigo_cnv` (154 completados + 254 que ya coincidían), tabla
  `portafolio.custodia_cvsa`, job diario y vista CUSTODIA en `/back-office`.
  Custody Fees queda sin integrar a la espera de `accountgroupcode`.
