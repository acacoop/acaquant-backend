# Postrade (A3 Mercados / Argentina Clearing) — integración

Estado: **ETAPA 1 — ACCESO Y CONEXIÓN.** Están el cliente, la configuración y el
diagnóstico de autenticación. Todavía **no** hay ingesta, ni esquema SQL, ni
endpoints, ni pantalla: eso son las etapas siguientes.

| Pieza | Archivo |
|---|---|
| Cliente HTTP | `core/postrade.py` |
| Configuración | `config.py` → bloque `POSTRADE_*` |
| Diagnóstico de acceso | `scripts/diag_postrade_auth.py` |

## Qué es

La API **Postrade** de Argentina Clearing y Registro S.A. (ACyRSA), la cámara de
A3 Mercados. Publica lo que pasa **después** de la operación: participantes,
cuentas, posiciones, garantías, márgenes requeridos, contabilidad, tarifas y los
parámetros de los contratos negociados en A3 Mercados.

Es un mundo distinto del que ya tenemos: **pyRofex/Primary es pre-trade**
(precios y órdenes, en vivo, por WebSocket) y **Postrade es post-trade** (lo que
la cámara liquidó, por HTTP y por día). No compiten ni se pisan.

Base URL de **producción**: `https://api.anywhereportfolio.com.ar`
(el entorno de pruebas es `https://demoapi.anywhereportfolio.com.ar`, que es el
host con el que están escritos todos los ejemplos del manual del proveedor).

## Autenticación

**No es OAuth.** No hay `client_id`, ni `client_secret`, ni `grant_type`, ni
scopes. Es usuario y contraseña contra un solo endpoint:

```
POST {base}/AuthToken/AuthToken
{"nombreUsuario": "...", "password": "..."}
```

Las credenciales se pueden mandar por **body JSON** o por **querystring** — el
manual dice que las dos valen. El cliente usa **body** por default: la
querystring deja la contraseña escrita en la URL, y una URL termina en los logs
de cualquier proxy que haya en el camino.

La respuesta **no** es un objeto OAuth: viene en el sobre propio de esta API y
el token está en `Value`.

```json
{"Status": "OK", "Code": "200", "Value": "RDEEMCHgolMdra4qBWl5tpIZxu5l8UFvmFN9T8NoJa0="}
```

Después el token va en el header `Authorization` de cada request. **Dura 24 hs**
— y eso es dato del manual, no de la respuesta: el sobre no informa vencimiento,
así que la vida del token la sabe el cliente y no el servidor. `core/postrade.py`
lo cachea en memoria y lo renueva con 30' de margen.

## ⚠️ Dos cosas que el manual NO dice, y por eso se miden

**1. Si el token va crudo o con `Bearer `.** El manual escribe «incluir el
header `Authorization` el token obtenido» y lo ilustra con una **captura de
pantalla** — el texto no lo aclara y de una imagen no se infiere. Lo mide
`diag_postrade_auth` probando las dos formas contra un endpoint real. El default
de `config.py` es **crudo** (lo que dice la letra); si la medición dice otra
cosa, se corrige con `POSTRADE_TOKEN_PREFIJO` sin tocar código.

**2. El sobre trae el error con HTTP 200.** — **VERIFICADO contra producción el
2026-08-24**, no inferido: con credenciales inválidas el servidor contesta
**HTTP 200** y el rechazo va adentro del sobre.

```json
{"Status": "Unauthorized", "Code": "401",
 "ErrorMessage": "Unauthorized", "ErrorDescription": "\"Invalid Authorization\""}
```

Un cliente que mirara solo el status HTTP daría eso por bueno y seguiría con un
token vacío. Además `Code` es un **string**, no un número, y el motivo real
viaja en `ErrorMessage`/`ErrorDescription` — no en `Value`, que llega `null`.
Por eso `get()` valida el sobre en `desempaquetar()`, y el diag usa **esa misma
función**: si el diag afloja el criterio, deja de estar midiendo lo que después
corre en producción.

De la misma medición salió que el host de producción responde y que el path
`/AuthToken/AuthToken` existe: lo único pendiente de probar son las credenciales
reales.

**3. El camino por querystring se colgó.** En esa misma prueba, el intento con
las credenciales en la URL dio `ReadTimeout` mientras el de body contestó al
instante. Con una sola medición no alcanza para afirmar que no funcione, así que
el diag **reintenta** antes de sacar conclusiones y los timeouts son de 60s.
Igual el default es `body`, que es el que se verificó vivo.

Tampoco está publicado ningún límite de llamadas. Sin dato medido (REGLA #2) el
cliente se auto-limita a **5 req/s**, que sobra para consultas batch y no puede
ser tomado por abuso.

## Credenciales (.env)

```
POSTRADE_USUARIO=...
POSTRADE_PASSWORD=...
```

Las asigna **ACyRSA**. Producción se pide a `atencionalcliente@matbarofex.com.ar`;
testing, a `mpi@primary.com.ar`.

Overrides, todos opcionales — existen para que lo que descubra el diag se active
desde el `.env` sin redeployar:

| Variable | Default | Para qué |
|---|---|---|
| `POSTRADE_BASE_URL` | `https://api.anywhereportfolio.com.ar` | apuntar a demo |
| `POSTRADE_AUTH_STYLE` | `body` | mandar las credenciales por `query` |
| `POSTRADE_TOKEN_PREFIJO` | *(vacío)* | poner `Bearer ` si el token no va crudo |

## Cómo probar

```bash
python -m scripts.diag_postrade_auth
```

Corre **desde cualquier PC**: la API es internet público y el diag no toca la
base. Es READ-ONLY — pide un token y hace un GET de consulta; no llama a ningún
método que registre, modifique ni cancele nada.

Hace cuatro pasos, y cada uno que falle es un reclamo **distinto**:

1. **Credenciales presentes** y contra qué entorno apuntan.
2. **Token**, por body y por querystring. Si las dos fallan igual, el problema
   son las credenciales o que el usuario no está habilitado en ese entorno.
3. **Cómo viaja el token** (crudo vs. `Bearer`), medido contra
   `PosTrade/ClosingProcesses`. Si el token se emitió pero acá rebota, las
   credenciales están bien y lo que falta es el **permiso sobre el método** —
   otro reclamo.
4. **El cliente real** de punta a punta, para que lo verificado sea el código
   que va a correr y no una prueba paralela.

Al final imprime qué dejar en el `.env`.

`ClosingProcesses` se eligió como prueba de vida a propósito: informa cuándo
terminaron los procesos de la cámara y **no depende de que tengamos cuentas ni
posiciones cargadas**, así que separa "el acceso anda" de "todavía no hay datos
nuestros".

## Etapas siguientes (todavía sin hacer)

1. ~~Acceso y conexión~~ ✅
2. Relevar qué métodos nos habilitaron de verdad y **medir** qué devuelven con
   nuestro usuario (el manual documenta ~50 métodos; qué vemos nosotros es otra
   cosa y no se puede inferir).
3. Recién con eso: decidir qué se persiste, dónde y para qué pregunta de negocio.

**No se asume ningún cruce con lo que ya existe** (`portafolio.tenencia`,
`operaciones.*`, Interbanking). Si aparece la propuesta de cruzar, primero hay
que medir que exista una correspondencia — mismo error que ya se pagó una vez
con Tesorería ↔ Interbanking.

## Anexo — el manual del proveedor

PDF `PrimaryAPI-BO.pdf` (189 páginas, versión 1.64 del 27/4/2026). También hay
colección de Postman: fork desde `https://www.postman.com/MatbaRofex`.
