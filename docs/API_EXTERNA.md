# API EXTERNA — operaciones para accionistas (`/ext`) **[VIVO]**

> Superficie que le entrega a un accionista **sus** operaciones, boleto por
> boleto. Sólo lectura. Vive en `api/ext/`, se monta en `/ext` y **no comparte
> autenticación con `/api`**.
>
> Documentación **para el consumidor**: `https://api.acaquant.com/ext/docs`
> (Redoc, generado del código — no puede quedar desactualizado). Este documento
> es el de adentro: por qué está hecho así y cómo se opera.

---

## 1. La idea en una frase

**El accionista es un dato, no un endpoint.** En el código no hay —ni puede
haber— un solo `id_cuenta` de un cliente: el permiso vive en
`ext.cuentas_autorizadas`, y por eso dar de alta al segundo accionista es un
INSERT, no un deploy.

---

## 2. Las cuatro tablas (schema `ext`)

| Tabla | Qué guarda |
|---|---|
| `ext.clientes` | quién es el consumidor: nombre, activo, allowlist de IPs, si ve aranceles |
| `ext.api_keys` | **N keys por cliente** — es lo que permite rotar sin corte. Sólo el hash |
| `ext.cuentas_autorizadas` | **el permiso**: `(cliente_id, id_cuenta)` |
| `ext.requests_log` | auditoría de cada request (quién, qué filtros, cuántas filas, cuánto tardó) |

⚠️ **Ninguna guarda datos de operaciones.** Los boletos se leen **en vivo** de
`operaciones.operaciones`, la misma tabla que dibuja la vista de la mesa.
Copiarlos acá sería una segunda copia sin árbitro (REGLA #9 B): el día que
difirieran, la mesa y el accionista dirían números distintos, cada mitad
coherente consigo misma, y **nada fallaría**.

---

## 3. Las capas de seguridad (y qué tapa cada una)

| # | Capa | Dónde vive | Qué pasa si falla sola |
|---|---|---|---|
| 0 | **Cloudflare Access — Service Auth** | Zero Trust (no en el repo) | Sin service token el request no llega al Droplet |
| 1 | **API key → token corto** | `api/ext/auth.py::emitir_token` | La key dura meses y viaja 1 vez por sesión; el token dura 30 min y es el que viaja siempre |
| 2 | **Revocación instantánea** | `db.key_vigente`, llamada en CADA request | El token dice con qué key nació (`kid`); revocar la key mata sus tokens al instante, sin tabla de tokens |
| 3 | **Scope fail-CLOSED** | `auth.cliente_actual` + `auth.resolver_cuentas` | Sin cuentas autorizadas → **403**. Pedir una cuenta ajena → **403**, no lista vacía |
| 4 | **Cuota + auditoría** | `api/ext/ratelimit.py`, `db.log_request` | 120/min y 5.000/h por cliente; toda llamada queda registrada |

### Lo que NO se reusó, y por qué

- **`api/deps.py::verify_api_key`** — es la `API_KEY` única del frontend de la
  mesa. Dársela a un tercero es darle los 541 endpoints.
- **`core/grupos.py::cuentas_visibles`** — es **fail-open**: ante un error de
  base devuelve `None` = *ve todo*. Adentro de la mesa es una decisión
  defendible (los grupos no deben tumbar la app); hacia afuera sería una fuga
  silenciosa. `api/ext/auth.py` hace lo contrario, a propósito, y hay un test
  que lo congela.

### Por qué es una sub-app montada y no un router de `/api`

En `/api` un router nuevo nace **alcanzable** y hay que acordarse de gatearlo —
por eso existe `GUEST_PATH_PREFIXES` (REGLA #8), que es una allowlist que
alguien tiene que mantener. Con `app.mount("/ext", ext_app)` el default-deny es
**topología**: un router de la mesa es físicamente inalcanzable desde `/ext`. De
yapa, el OpenAPI de `/ext` muestra sólo sus 4 endpoints — el consumidor externo
no ve ni el nombre de las rutas internas.

---

## 4. El contrato de datos

### Un solo lector

`api/ext/lectura.py` es la única puerta, y **reusa `operaciones_sql._ops_where`**
en vez de copiarlo. Las reglas que definen "una operación" no son inferibles
(el `COALESCE(es_cierre,false)` que hace entrar al FCI bilateral, la regla de
`etapa` que evita contarlo dos veces, el filtro de anulados). Si se duplicaran,
el accionista y la mesa se separarían en silencio. Congelado por
`test_el_lector_externo_usa_el_predicado_de_la_mesa`.

### Los dos modos, y por qué hacen falta los dos

| Modo | Parámetros | Para qué |
|---|---|---|
| **FOTO** | `desde` / `hasta` | Carga inicial y reportes. Ordena y pagina por `id` |
| **INCREMENTAL** | `actualizado_desde` | Mantener la copia al día. Trae **también los anulados** |

⚠️ **Un boleto de un día viejo cambia después**: backfills, la tasa que rellena
`jobs/ops_tasa_mav`, la etapa que escribe `jobs/fci_bilateral`, y sobre todo las
**anulaciones**. Un consumidor que sólo pidiera por fecha de concertación se
quedaría con datos viejos y no se enteraría nunca. (Es el mismo motivo por el que
`jobs/ops_agregado` recomputa por día sucio y no por fecha.)

⚠️⚠️ **La anulación NO toca `ingestado_en`** —
`api/services/operaciones_informes.py:511` y `anulados.py:82` hacen
`SET anulado_en = now()` y nada más. Por eso el reloj de esta API es
`GREATEST(ingestado_en, anulado_en)` y no `ingestado_en`: si mirara sólo la
ingesta, **el aviso de que un boleto se anuló nunca llegaría**, que es justo el
evento que más importa entregar. Sin ese aviso, el sistema del accionista se
queda con el boleto para siempre — el mismo bug que fosilizó 450 boletos y
$824 MM de volumen falso de este lado, corrido a la casa del cliente.

### Decisiones del payload

- **Montos como texto decimal**, nunca float: JSON no tiene decimales y un
  `float` pierde centavos que nadie nota hasta la conciliación.
- **`arancel_moneda: "ARS"` viaja explícito** aunque sea siempre ARS
  (`sql/schema.sql:270`): el contrato se explica solo.
- **`segmento` y `nivel_3` NO se exponen** — son nuestra clasificación comercial
  interna, no un dato de su operación. Default-deny: agregar se puede; sacar lo
  que ya se entregó, no.
- **`tasa`**: sólo boletos MAV, en PORCENTAJE (6 = 6%). `null` ≠ `0`.
- **No se devuelve un total** de filas: contar el universo en cada página es caro
  y no aporta. El contrato es `hay_mas` + `siguiente_cursor`.
- **Paginación keyset**, no `OFFSET`.

### `/v1/meta` no es decorativo

Dice hasta cuándo hay datos y cuándo fue la última ingesta. Sin él, del otro
lado **"no operó ese día" y "todavía no ingestamos ese día" se ven idénticos**.
Es el invariante #1 del AV AGENT —una corrida que no pudo mirar no cierra nada—
aplicado a un tercero.

---

## 5. Operación

### Alta de un cliente

```
python -m scripts.ext_cliente --alta "PEPITO SRL" --cuentas 10452,10453
```

Crea el cliente, sus cuentas y su primera API key (**se imprime una sola vez**).
Opcionales: `--aranceles`, `--ips 200.45.12.8,190.2.0.0/24`, `--notas`.

Del lado de Cloudflare, además: crear el **service token** del cliente y sumar su
`common_name` a `CF_TRUSTED_SERVICE_TOKENS` en el `.env` del Droplet.

### Rotación de key — sin corte

```
python -m scripts.ext_cliente --nueva-key cli_pepito     # las dos conviven
python -m scripts.ext_cliente --revocar avk_live_7f3a    # cuando ya cambió
```

**La rotación sin downtime no es un lujo: es lo que hace que la rotación ocurra.**
Con una sola key por cliente, rotar es coordinar un corte por teléfono — y por
eso no se hace nunca, y terminás con una key de cinco años dando vueltas en un mail.

### Vencimiento

La columna `expira_at` existe pero **arranca en `NULL` a propósito**. Una key que
caduca sola un domingo rompe la integración sin que nadie mire, y el llamado te
lo comés vos. Cuando exista un portal donde el cliente se genere la nueva, el
vencimiento duro pasa a tener sentido.

### Cuándo revocar en el acto

Los tres casos se detectan mirando `ext.requests_log`: se fue el dev que la
tenía · apareció en un mail o un repo · tráfico desde una IP nueva, en horarios
que no son los de ellos, o un pico sin motivo.

### Otros comandos

```
python -m scripts.ext_cliente --listar
python -m scripts.ext_cliente --agregar-cuentas cli_pepito --cuentas 10999
python -m scripts.ext_cliente --quitar-cuentas  cli_pepito --cuentas 10453
python -m scripts.ext_cliente --desactivar cli_pepito
python -m scripts.ext_cliente --podar --dias 90
```

Los cambios de cuentas tienen **efecto inmediato**: el scope se resuelve en cada
request, no viaja dentro del token.

### El interruptor general

Sin `EXT_JWT_SECRET` en el `.env`, `/ext` **no se monta**: la superficie no
existe. Borrar la variable y reiniciar apaga la API externa entera sin tocar una
línea de código.

⚠️ Ese secreto es además el **pepper** con el que se hashean las keys. Rotarlo
invalida los tokens vigentes (se renuevan solos) **y también todas las API keys
emitidas** (habría que regenerarlas). No rotarlo sin plan.

---

## 6. Configuración

| Variable | Default | Para qué |
|---|---|---|
| `EXT_JWT_SECRET` | — | **Enciende la API.** Firma los tokens y hashea las keys |
| `EXT_TOKEN_TTL_SECONDS` | `1800` | Vida del token de acceso |
| `EXT_ISSUER` | `https://api.acaquant.com/ext` | claim `iss` |
| `EXT_MAX_LIMIT` | `1000` | Tope de filas por página |

**Cloudflare** (una vez): Access Application sobre `api.acaquant.com/ext` +
`/ext/*`, policy **Service Auth**, un service token por cliente. Opcional: regla
WAF con las IPs de ellos.

⚠️ Las dos trampas ya pagadas con el MCP aplican igual: **path scoping** (si
Access tapa mal, el cliente recibe el HTML del login en vez de un 401 y su
integración muere en silencio) y el **tope de destinations por app**.

---

## 7. Qué NO hace este v1

- **No escribe.** Sin superficie de escritura no hay superficie de escritura que
  auditar.
- **No hay créditos.** Eso es un modelo de negocio (1816 vende datos); acá hay
  una cuota simple para frenar runaways.
- **No hay push.** El consumidor pregunta cuando quiere. Pull evita abrir nada
  hacia afuera y guardar credenciales de ellos.
- **No hay export CSV/XLSX.** Se agrega si aparece un consumidor que no integra.

---

## Changelog

- **2026-08-26** — v1.0.0. Schema `ext` (4 tablas), sub-app `/ext`, 4 endpoints
  (`auth/token`, `cuentas`, `operaciones`, `meta`), ABM por
  `scripts/ext_cliente.py`, invariantes congelados en
  `tests/unit/test_ext_api.py`. Se agrega `incluir_anulados` a
  `operaciones_sql._ops_where` (default sin cambios) para poder entregar las
  bajas.
