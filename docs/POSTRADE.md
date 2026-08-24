# Postrade (A3 Mercados / Argentina Clearing) — integración

Estado: **ETAPA 1 COMPLETA — acceso, token y cliente único.** Autenticación
verificada contra producción. Falta la ETAPA 2: relevar qué métodos nos
habilitaron de verdad. Todavía no hay ingesta, ni esquema de datos, ni
endpoints, ni pantalla.

| Pieza | Archivo |
|---|---|
| Cliente | `core/postrade.py` |
| Catálogo de métodos | `core/postrade_catalogo.py` |
| Configuración | `config.py` → bloque `POSTRADE_*` |
| Token compartido | `sql/schema.sql` → `manager.postrade_token` |
| Diag de acceso | `scripts/diag_postrade_auth.py` |
| Diag de relevamiento | `scripts/diag_postrade_metodos.py` |
| Reglas congeladas | `tests/unit/test_postrade_seguridad.py` |

## Qué es

La API **Postrade** de Argentina Clearing y Registro S.A. (ACyRSA), la cámara de
A3 Mercados. Publica lo que pasa **después** de la operación: participantes,
cuentas, posiciones, garantías, márgenes requeridos, contabilidad, tarifas y los
parámetros de los contratos negociados en A3 Mercados.

Es un mundo distinto del que ya tenemos: **pyRofex/Primary es pre-trade**
(precios y órdenes, en vivo, por WebSocket) y **Postrade es post-trade** (lo que
la cámara liquidó, por HTTP y por día). No compiten ni se pisan.

Base URL de **producción**: `https://api.anywhereportfolio.com.ar`
(pruebas: `https://demoapi.anywhereportfolio.com.ar`, que es el host con el que
están escritos todos los ejemplos del manual).

## ⚠️ ESTA API PUEDE OPERAR — no es como Interbanking

La diferencia más importante con la otra integración externa del repo.
Interbanking expone solo GET: no puede mover plata ni por error, y eso se podía
afirmar leyendo la lista de endpoints. **Postrade no.**

| Método | Qué hace si se llama |
|---|---|
| `NewOrderSingle` | **suscribe o rescata un FCI** — mueve plata de verdad |
| `NewOrderList` | lo mismo, masivo |
| `ReplaceOrder` / `CancelOrder` | modifica o cancela una orden |
| `AccountStatus` | **inactiva** una cuenta |
| `AccountRegistration` / `AccountUpdate` | da de alta o modifica cuentas |
| `ChangePassword` | **cambia la contraseña del usuario de la API** — nos deja afuera de nuestra propia integración |

Por eso escribir necesita **dos llaves independientes, las dos a la vez**:

1. `POSTRADE_ESCRITURA=1` en el `.env` — decisión de **operación**, la toma quien
   opera el servidor.
2. `confirmo_escritura=True` en la llamada — decisión de **código**, la toma
   quien escribe el caller.

Son dos porque protegen de cosas distintas: la primera evita que un deploy con
código nuevo empiece a operar sin que nadie lo haya decidido; la segunda evita
que una función que creía estar leyendo termine mandando una orden. **Ninguna de
las dos sola alcanzaría**, y hay un test que falla si alguien las unifica.

Y la marca de lectura/escritura **no es disciplina**: es un dato del catálogo.
`leer()` rechaza un método de escritura aunque el `.env` lo permita, y los dos
conjuntos no pueden compartir path (también congelado por test).

## Cómo se usa

El caller nombra el método; **no arma la URL**. El path vive una sola vez en
`core/postrade_catalogo.py`.

```python
from core import postrade

postrade.ping()                       # ¿el acceso anda? → CurrencyList
postrade.leer("CurrencyList")
postrade.leer("ClosingProcesses", {"EntryDate": postrade.fecha_api(hoy)})
```

Un typo revienta con la lista de métodos válidos, en vez de llegar a la API como
un path inexistente que contesta "no autorizado" — y nos manda a reclamarle al
proveedor algo que es nuestro.

Los errores son **tipados**, porque arriba se decide distinto según cuál sea:

| Excepción | Qué significa | Qué se hace |
|---|---|---|
| `PostradeAuthError` | credenciales o token rechazados | revisar `.env` / reclamar el alta |
| `PostradeNoHabilitado` | el token vale, el método no | reclamo **distinto** al proveedor |
| `PostradeEscrituraBloqueada` | frenó esta librería, no el proveedor | decisión humana |
| `PostradeError` | red, formato, 5xx | reintentar / investigar |

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

Después el token va en el header `Authorization`. **Dura 24 hs** — y eso es dato
del manual, no de la respuesta: el sobre no informa vencimiento, así que la vida
del token la sabe el cliente y no el servidor. Si el proveedor la acortara, nos
enteraríamos por un 401, que el cliente ya sabe recuperar.

### El token se comparte entre procesos

El Droplet corre la API más N jobs de cron. Si cada proceso pidiera el suyo, un
token de **24 horas** se usaría dos segundos y nos loguearíamos decenas de veces
por día contra un proveedor que **no publica** límite de logins.

Se cachea en dos niveles: **memoria → `manager.postrade_token` → API**. Si
Postgres no está, funciona igual con memoria (degradación elegante, mismo
criterio que `core/instrumentos_validos`): un problema de base no puede dejar
sin funcionar a la integración.

Guardarlo en la base es **menos** sensible que lo que ya existe: el `.env` tiene
la contraseña, que no vence nunca; el token vence en 24hs.

Dos procesos que arranquen a la vez pueden pedir dos tokens. No se puso un lock
distribuido a propósito: el caso es raro, el costo es un login de más, y si el
proveedor invalidara el anterior, el cliente re-autentica ante el 401. Un lock
distribuido mal hecho sería peor que el problema que resuelve.

## ⚠️ Tres cosas que el manual NO dice

**1. Si el token va crudo o con `Bearer `.** El manual escribe «incluir el
header `Authorization` el token obtenido» y lo ilustra con una **captura de
pantalla** — el texto no lo aclara y de una imagen no se infiere. Lo mide
`diag_postrade_auth` probando las dos formas contra endpoints reales. El default
es **crudo** (lo que dice la letra); si la medición dice otra cosa, se corrige
con `POSTRADE_TOKEN_PREFIJO` sin tocar código.

**2. El sobre trae el error con HTTP 200** — **VERIFICADO contra producción**,
no inferido: con credenciales inválidas el servidor contesta **HTTP 200** y el
rechazo va adentro del cuerpo.

```json
{"Status": "Unauthorized", "Code": "401",
 "ErrorMessage": "Unauthorized", "ErrorDescription": "\"Invalid Authorization\""}
```

Un cliente que mirara solo el status HTTP daría eso por bueno y seguiría con un
token vacío. Además `Code` es un **string**, no un número, y el motivo real
viaja en `ErrorMessage`/`ErrorDescription` — no en `Value`, que llega `null`.
Por eso `desempaquetar()` es la única puerta por la que salen los datos, y los
diags usan **esa misma función**: si un diag aflojara el criterio, dejaría de
medir lo que después corre en producción.

**3. Una fecha mal puesta no da error.** El manual lo dice explícito: si no se
especifica fecha, el método devuelve *«la última información disponible»*. O sea
que el modo de fallar es **contestar algo plausible y equivocado**. Por eso el
formato `AAAAMMDD` vive en `postrade.fecha_api()` y los obligatorios se validan
del lado del cliente, antes de salir a la red.

Tampoco está publicado ningún límite de llamadas. Sin dato medido (REGLA #2) el
cliente se auto-limita a **5 req/s**. Y los timeouts se reintentan: no es
teoría — en el primer contacto con producción el endpoint de token cortó con
`ReadTimeout`, y un fallo de red no puede parecer un fallo de permisos.

## Credenciales (.env)

```
POSTRADE_USUARIO=...
POSTRADE_PASSWORD=...
```

Las asigna **ACyRSA**. Producción se pide a `atencionalcliente@matbarofex.com.ar`;
testing, a `mpi@primary.com.ar`.

Overrides, todos opcionales:

| Variable | Default | Para qué |
|---|---|---|
| `POSTRADE_BASE_URL` | `https://api.anywhereportfolio.com.ar` | apuntar a demo |
| `POSTRADE_AUTH_STYLE` | `body` | mandar las credenciales por `query` |
| `POSTRADE_TOKEN_PREFIJO` | *(vacío)* | poner `Bearer ` si el token no va crudo |
| `POSTRADE_ESCRITURA` | *(apagado)* | ⚠️ habilitar métodos con efecto real |

## Cómo probar

```bash
python -m scripts.diag_postrade_auth      # ¿entramos? (etapa 1)
python -m scripts.diag_postrade_metodos   # ¿qué nos habilitaron? (etapa 2)
```

Corren **desde cualquier PC**: la API es internet público y los diags no tocan
la base. Los dos son READ-ONLY, y el segundo no por promesa: itera **solo** la
lista de LECTURAS del catálogo, así que ningún método con efecto real puede
colarse por un typo.

`diag_postrade_auth` hace cuatro pasos, y cada uno que falle es un reclamo
**distinto**:

1. **Credenciales presentes** y contra qué entorno apuntan.
2. **Token**, por body y por querystring (la segunda solo si la primera falla —
   medido: ese camino se cuelga y no cambia ninguna decisión).
3. **Cómo viaja el token** (crudo vs. `Bearer`), probado contra **varios**
   métodos: si probara contra uno solo y justo ese no estuviera habilitado,
   concluiría "el token no sirve" con el token perfecto.
4. **El cliente real** vía `postrade.ping()` — no una llamada paralela, para que
   lo verificado sea el código que va a correr.

`diag_postrade_metodos` clasifica cada método de lectura en cuatro cajas:
**CON DATOS** (se puede construir encima) · **VACÍO** (habilitado pero sin
datos, o falta un filtro) · **NO HABILITADO** (reclamo al proveedor) ·
**ERROR**. Distinguir "vacío" de "denegado" es el punto: son problemas
distintos y se resuelven con gente distinta.

## Etapas

1. ~~Acceso, token y cliente único~~ ✅
2. **Relevar qué métodos nos habilitaron de verdad** ← acá estamos.
   El manual documenta ~40 métodos de lectura; qué contesta *nuestro* usuario es
   otra cosa y no se puede inferir de ningún papel.
3. Recién con eso: decidir qué se persiste, dónde y **para qué pregunta de
   negocio**. Sin el paso 2, cualquier decisión acá sería una apuesta.

**No se asume ningún cruce con lo que ya existe** (`portafolio.tenencia`,
`operaciones.*`, Interbanking). Si aparece la propuesta de cruzar, primero hay
que medir que exista correspondencia — el mismo error ya se pagó una vez con
Tesorería ↔ Interbanking, donde el doc usaba como fundamento una respuesta que
él mismo listaba como pregunta abierta.

## Anexo — el manual del proveedor

PDF `PrimaryAPI-BO.pdf` (189 páginas, versión 1.64 del 27/4/2026). También hay
colección de Postman: fork desde `https://www.postman.com/MatbaRofex`.
