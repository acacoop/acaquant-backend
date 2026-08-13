# ACA — Resumen ejecutivo de inversiones (vista `/aca`) **[VIVO]**

> **Doc VIVO con changelog obligatorio.** Si tocás `api/services/aca.py`,
> `api/routers/aca.py`, `api/routers/manager/aca.py`, el schema `aca` o las
> vistas `aca-view.tsx` / `manager-aca-panel.tsx`, actualizá este doc **en el
> mismo commit**. Si el doc no refleja el estado real, el trabajo está incompleto.

---

## 1. Qué es

La cartera **propia de ACA** contada para los gerentes: valuación, composición
por cartera, detalle título por título, métricas de concentración y rendimiento
acumulado contra benchmarks. Reemplaza una planilla de Excel que se armaba a
mano cada mes.

**No es una vista live.** Es una **foto mensual**: se elige un período
(`YYYY-MM`) y todo lo que se ve habla de ese mes. No hay polling, porque no hay
nada que se mueva solo.

El criterio de diseño, en una línea: **un Excel con las fórmulas ya puestas.**
Todo lo que se puede derivar se deriva server-side; lo único que se tipea son
los inputs que ninguna fuente del sistema tiene.

| Se carga a mano | Sale solo |
|---|---|
| `px` — precio de corte del mes (el dato central; el last price de hoy no reconstruye un cierre pasado) | Monto por activo, total por cartera, % share |
| `vn` — nominal al cierre | Valuación ARS / A3500 / USD MEP |
| MEP y A3500 del informe | Ponderación de cada cartera |
| Rendimientos mensuales externos (Caspi) y todavía-no-automatizados (Badlar, Inflación) | Total Dolarizado / Total Pesos |
| | Métricas por clase de activo y por emisor |
| | Acumulado de toda serie del histórico |
| | **La ficha completa de cada título** (ver §3) |

---

## 2. Permisos

| | Quién |
|---|---|
| **VER** | módulo `aca` (rol **`empleado_aca`**) ∪ **admin** ∪ escritores |
| **ESCRIBIR** | allowlist de Mesa de Dinero (`operaciones.mesa_dinero_escritores`) ∪ admin |

Tres decisiones que conviene no revertir sin pensarlas:

- **`empleado_aca` es un rol NUEVO, no `sales` renombrado.** `sales` es
  `DEFAULT_ROLE`: todo email que pasa Cloudflare por primera vez se auto-registra
  ahí (`core/roles.py::_auto_register`). Colgarle `aca` significaba que cualquier
  alta automática pasa a ver la cartera de la casa sin que nadie lo decida. Con
  el rol aparte, entrar a ACA es un acto explícito del admin en
  `/manager → USUARIOS`. `empleado_aca` = los mismos módulos que `sales` + `aca`.
- **Escritura reusa la allowlist de la mesa** (decisión del user): la mesa maneja
  la cuenta, y una segunda lista sería una lista más para desincronizar.
- **Escribir implica ver.** `aca.puede_ver` es la unión del módulo con la
  allowlist. Sin eso, un escritor sin el rol pasaba el gate del backend pero no
  veía el link en el nav: entraba solo tipeando la URL. Por eso `/api/me`
  publica `aca` dentro de `modules` también para los escritores.
- **REGLA #8 — jamás al portal invitado.** Es el negocio de la casa.
  `/api/aca` no está en `GUEST_PATH_PREFIXES` ni `aca` en `INVITADO_MODULES`.
  Hay un test que falla si alguien lo agrega.

El gate de lectura (`require_lectura_aca`) se monta sobre **todo** el router en
`api/main.py` → cubre también los endpoints que se agreguen mañana.

**El rol NO da escritura.** `empleado_aca` es SOLO LECTURA: ve las 5 tabs y nada
más. La vista le esconde `+ PERÍODO`, `EDITAR MEP/A3500`, `+ AGREGAR TÍTULO`,
`COPIAR DEL MES ANTERIOR`, `VER PRECIOS DE REFERENCIA`, los inputs de VN/Px y los
botones por fila, y le muestra un cartel **SOLO LECTURA**. Esconder botones es
cosmética: lo que impide escribir es `_check_escritura()` adentro de CADA función
de escritura del service, y hay un test que recorre las 15 y falla si a alguna le
falta. Escribe quien está en la allowlist de la mesa (o es admin), tenga el rol o
no — y al revés, tener el rol no alcanza.

---

## 3. La ficha del título NO se duplica

Un activo del informe guarda **solo sus inputs** (`vn`, `px`, `tasa`, `obs`).
Todo lo demás —cartera, emisor, calificación, clase de activo, vencimiento,
ticker— se resuelve en cada lectura contra **`portafolio.assets`**, el mismo
catálogo maestro que edita `Manager → Títulos`.

Por qué importa: copiar la ficha adentro de `aca.activos` habría creado una
segunda verdad que se desincroniza sola. El rebautizo de especies de Aunesa (ver
la regla `herencia` de `jobs/assets_autofill`) ya mostró lo caro que sale tener
la misma ficha en dos lados.

Consecuencias visibles:

- Para agregar un título al informe hay que tenerlo en el catálogo. El
  buscador (`GET /api/aca/titulos`) pega contra `portafolio.assets` y el backend
  rechaza una unidad que no exista ahí, con el motivo escrito.
- Corregir un emisor o una calificación se hace en `Manager → Títulos` y se ve
  en **todos** los períodos, incluidos los ya cerrados.
- Si alguien borra un asset del maestro, la fila del informe **no desaparece**
  (el monto ya contado no puede evaporarse): sale marcada `sin_ficha` con un ⚠.
- Si la cartera del asset no es ARS/DL/HD/FCI, la fila cae en `huerfanos` y la
  vista lo canta. Suma al total pero no entra a ningún cuadro por cartera.

---

## 4. Fórmulas (todas server-side)

### Monto por activo

```
monto = monto_manual                       si hay override
      = vn × px / 100                      si cartera ∈ {ARS, DL, HD}   (paridad)
      = vn × px                            si no                        (FCI, RV, cash)
```

Es la **misma regla de divisor que la valuación del AuM** (`jobs/portafolio_backfill`
y `pnl.py::_aplicar_normalizer`): la renta fija cotiza por cada 100 de nominal y
el resto por unidad. Verificado contra la planilla: *FCI IAM Performance
Americas*, 84.903 × 1,16 = 98.487, que es exactamente el monto del informe.

`vn` o `px` en null → monto **null**, no 0. "Todavía no cargado" y "vale cero"
son cosas distintas; devolver 0 haría que un informe a medio cargar parezca
completo. La vista pinta esas filas en ámbar.

### Valuaciones

```
valuacion_ars     = Σ monto de todos los activos del período
valuacion_a3500   = valuacion_ars / a3500
valuacion_usd_mep = valuacion_ars / mep
```

Sin el TC cargado devuelven **null**, no 0: dividir por un dato que nadie cargó
es inventar el número más visible del informe.

### Total Dolarizado / Total Pesos — regla de moneda

Tabla **`aca.moneda_regla`**, editable en `Manager → ACA → CONFIGURACIÓN`. Dos
scopes, y **el de `clase` gana sobre el de `cartera`**:

| scope | clave | moneda |
|---|---|---|
| cartera | HD, DL | usd |
| cartera | ARS | ars |
| clase | MM USD, HD T1 | usd |
| clase | MM ARS, ARS T1, RENTA VARIABLE | ars |

Eso es lo que permite que **el FCI se parta por moneda sin dejar de ser su
propia cartera**: los cuatro renglones del cuadro (ARS / DL / HD / FCI) no
cambian, pero cada fondo suma al total de la moneda que le corresponde.

Confirmado contra los números de la planilla: en el informe de julio,
HD + DL = 80.250.484.642 (83,0%) mientras el cuadro dice Total Dolarizado
89.651.454.303 (92,7%). La diferencia —9.401 millones— son exactamente los FCI
en dólares.

**Lo que ninguna regla resuelve NO se reparte a dedo**: cae en `sin_clasificar`,
con la lista de clases culpables, y la vista lo muestra en ámbar arriba de todo.
Un activo con una clase nueva tiene que aparecer como pendiente, no colarse en
el lado equivocado y desbalancear el informe en silencio.

### Acumulado del histórico

```
acumulado = (1 + acumulado_anterior) × (1 + mensual) − 1
```

- **No se persiste.** Se deriva en cada lectura. Guardarlo sería guardar un
  número que puede terminar contradiciendo a sus propios insumos.
- Un mes **sin dato arrastra** el acumulado anterior (no lo reinicia ni lo corta):
  "no sé cuánto rindió" no es "rindió 0". Es lo que hace la planilla con las
  columnas todavía en blanco.
- Antes del primer dato el acumulado es **null**, no 0.

### Métricas generales

Dos denominadores distintos, a propósito:

- **Clases y emisores de una cartera** → sobre el total de **esa cartera** (es su
  composición interna).
- **Créditos privados más representativos** → sobre la **valuación total**,
  porque la pregunta que responden es cuánto pesa ese riesgo en toda la cartera.

Los catálogos (`aca.emisor_destacado`, `aca.clase_destacada`) hacen que una fila
aparezca **aunque cierre en cero** — que YPF valga 0 es información; que la fila
desaparezca no dice nada. Y al revés: lo que aparece en el período y **no** está
catalogado igual se muestra, marcado `fuera_catalogo`. Mismo criterio que la
grilla BANCOS de Tesorería: **el catálogo agrega filas, nunca esconde plata.**
Única excepción: el bloque `privados` muestra SOLO su catálogo, porque mostrar
todo convertiría "los más representativos" en "todos".

---

## 5. Automatización de los benchmarks (y por qué está a medias)

`aca.series.fuente` admite tres modos:

| fuente | qué hace | riesgo |
|---|---|---|
| `manual` | se tipea | ninguno |
| `macro_var:<SERIE>` | último valor del mes ÷ último del mes anterior − 1 | **ninguno**: es un cociente, no depende de la unidad de la serie |
| `macro_pct:<SERIE>` | el valor del mes ÷ `escala` como rendimiento | **sí depende de la unidad** |

Sembrado hoy: **`a3500` → `macro_var:DOLAR`**. Badlar e Inflación quedan
`manual`, a propósito y por **REGLA #2**: no está medido si `InflacionMensual`
guarda `2.5` o `0.025`, y errarle es un factor 100 que en un gráfico acumulado no
se ve como un error sino como una serie. Además Badlar/TAMAR son **TNA**: no son
el rendimiento del mes ni dividiendo por 100, hay que mensualizarlas.

Para decidirlo hay un diag read-only:

```bash
python -m scripts.diag_aca_benchmarks
```

Imprime las unidades reales y qué daría cada lectura. Con eso se cambia la
fuente desde `Manager → ACA → SERIES DEL HISTÓRICO`.

**El valor MANUAL siempre gana sobre el automático.** La automatización rellena
huecos, no pisa criterio — así que activarla nunca puede romper lo ya cargado.
Cada celda dice de dónde salió (`origen: manual | auto`, marcada `·a` en la UI).

---

## 6. Modelo de datos (schema `aca`)

| Tabla | Qué guarda |
|---|---|
| `periodos` | cabecera del informe: `periodo` (PK), `fecha_informe`, `mep`, `a3500` |
| `activos` | PK (periodo, unidad) — **solo inputs**: `vn`, `px`, `monto` (override), `tasa`, `obs`, `orden` |
| `moneda_regla` | PK (scope, clave) → `usd`/`ars`. Parte Dolarizado/Pesos |
| `emisor_destacado` | PK (bloque, emisor) — filas fijas de las métricas por emisor. Sembrada con los emisores del informe actual (11 en HD, 8 en DL, 6 en privados) |
| `clase_destacada` | PK (cartera, clase) — filas fijas de las métricas por clase |
| `series` | catálogo del histórico: `fuente`, `escala`, `graficos[]`, `grupo`, `orden`, `activo` |
| `historico` | PK (periodo, serie) — `monto`, `ingreso_retiro`, `mensual` (FRACCIÓN: 0,0245 = 2,45%) |
| `audit` | before/after de toda escritura |

`aca.historico.periodo` es **independiente** de `aca.periodos`: la serie
histórica arranca mucho antes que el primer informe cargado.

`sql/schema.sql` siembra los catálogos (reglas de moneda, emisores y clases
destacadas, series) y **las filas de RBAC** (`manager.role_matrix` para
`empleado_aca` y `admin`). Sin esa siembra la vista nacería invisible hasta para
el admin, porque en prod la matriz está poblada y pisa a `DEFAULT_MATRIX`.

**Las semillas son SEMILLA, no estado forzado.** Cada bloque corre solo si su
tabla está vacía (y el de RBAC, solo si el rol/módulo no existe todavía).
`ON CONFLICT DO NOTHING` alcanza para no duplicar pero **no** para no resucitar:
estos catálogos se editan desde Manager → ACA, y sin el guard un `apply_schema`
posterior le devolvía a un rol un módulo que el admin le había sacado, o
reponía una regla de moneda borrada — moviendo plata de lado en el informe sin
que nadie lo pidiera. Verificado contra un Postgres real: se aplicó el schema
sobre una base con la matriz poblada, se borraron a mano un módulo, la regla de
`MM USD`, una clase y se desactivó una serie, se re-aplicó, y **nada volvió**.

⚠️ **El orden de los dos bloques de RBAC importa**: primero se copian los módulos
base de `sales`, después la fila `aca`. Al revés, la fila `aca` haría existir a
`empleado_aca` en la matriz y el bloque de los módulos base se saltearía para
siempre — el rol quedaría con la vista ACA y **nada más**.

---

## 7. Superficie

### Vista `/aca` — 5 tabs

| Tab | Qué muestra | Escritura |
|---|---|---|
| **RESUMEN** | TC + 3 valuaciones, torta de carteras, los dos cuadros comparativos (mes actual y anterior) | cabecera del período (`+ PERÍODO`, `EDITAR MEP/A3500`) |
| **CARTERAS** | los 3 gráficos de rendimiento **acumulado** vs benchmarks | — |
| **ACTIVOS** | detalle por cartera con total y % share | **acá se carga VN y PX**, se agregan/quitan títulos, se clona el mes anterior |
| **MÉTRICAS** | apertura por clase (FCI, ARS) y por emisor (HD, DL, privados) | — |
| **HISTÓRICO** | la planilla mensual completa | solo lectura (se carga en Manager) |

Tres ayudas de carga en ACTIVOS:

- **COPIAR DEL MES ANTERIOR** — clona títulos, VN y observación. **No copia el
  precio**: arrastrarlo dejaría un informe que parece cargado y está mintiendo.
  Idempotente, no pisa lo ya cargado.
- **VER PRECIOS DE REFERENCIA** — muestra bajo cada campo PX el último precio
  conocido en `portafolio.tenencia`, con su fecha. Orientativo; **no se aplica
  solo**.
- **+ AGREGAR TÍTULO** — buscador sobre `portafolio.assets` que muestra la ficha
  que va a heredar antes de agregarlo.

### `Manager → ACA` — 2 sub-tabs

- **HISTÓRICO** — carga del rendimiento mensual por serie y período. Se tipea en
  **porcentaje** (2,45) y se guarda como fracción; la columna acumulada es de
  solo lectura porque es un resultado.
- **CONFIGURACIÓN** — regla de moneda (con aviso de qué clases del catálogo
  todavía **no** tienen regla, que son las que van a caer en "sin clasificar"),
  emisores y clases destacadas, y el catálogo de series.

### Endpoints

`GET /api/aca/vista` sirve **toda la pantalla en un request** (resumen +
detalle + métricas + gráficos). Es el mismo criterio que `senebis.vista`: los
cuatro bloques leen exactamente las mismas filas, y pedirlos por separado
significaba repetir la misma query 4 veces por refresh y por usuario. Con el
peaje medido de ~8,5 ms por round-trip a Supabase, agrupar es la diferencia
entre ~6 viajes y ~20.

Los endpoints por bloque (`/resumen`, `/detalle`, `/metricas`, `/graficos`,
`/historico`) siguen vivos: los usa el panel de Manager y sirven para depurar un
bloque sin traerse todo.

Inventario completo y gate efectivo: `docs/MAPA_APP.md` (§0, auto-generada).

---

## 8. Qué NO hace todavía

- **No lee la cartera de ACA de `portafolio.tenencia`.** Podría —los títulos son
  los mismos— pero no está medido que las cuentas propias de ACA estén ahí con
  la composición del informe (REGLA #2). Si se confirma, el camino natural es un
  botón "traer posición del cierre" que **precargue VN** y deje el precio manual,
  reusando el mismo modelo (no hay que cambiar tablas).
- **No exporta a Excel/PDF.** El informe se sigue armando a mano para
  distribuirlo.
- **No cierra períodos.** Un informe publicado se puede editar; queda el rastro
  en `aca.audit` pero nada lo congela. Si hace falta, la pieza que falta es un
  flag `cerrado` + gate de escritura, no un modelo nuevo.
- **Badlar e Inflación son manuales** (ver §5).

---

## Changelog

### 2026-08-13 — ACA sale de NEGOCIO
- Pasa a ser un **link de primer nivel del header** (entre RESEARCH y el
  dropdown MERCADOS) en vez de un item del dropdown NEGOCIO. Pedido del user: es
  la cartera de la casa mirada por gerencia, no una vista más de la operación
  diaria. Solo nav — no toca módulos, roles ni gates.

### 2026-08-13 — Verificación post-deploy
- `scripts/diag_aca_estado.py` (read-only): confirma que el schema, los
  catálogos y **el RBAC** quedaron bien parados, y lista lo que falta con la
  acción concreta. Existe porque "el statement corrió sin error" no es "insertó
  lo que tenía que insertar": las semillas de `role_matrix` están guardadas
  contra la resurrección, así que **lo que hacen depende de cómo esté la matriz
  REAL de prod**, que no se puede ver desde afuera (REGLA #2).
  El caso que caza: `empleado_aca` se arma COPIANDO los módulos de `sales`; si
  en esa base `sales` no existe con ese nombre, el rol nuevo queda con la vista
  ACA **y nada más** y la persona entra a la app sin ver casi nada. Probado
  contra Postgres real en los dos escenarios (con y sin `sales`).

### 2026-08-13 — Deploy en un comando + semillas a prueba de resurrección
- `deploy/deploy.sh`: pull + apply_schema + restart + smoke, cortando al primer
  fallo. El deploy del backend pasa a ser UN comando.
- **Fix de las semillas** (encontrado probando contra un Postgres real, no
  leyendo): dentro de un `VALUES` suelto, `'{total_ars}'` se infiere `text` y no
  se coacciona a `text[]` como sí pasa en un `INSERT ... VALUES` — el
  `INSERT` de `aca.series` reventaba y **abortaba el resto del script**, así que
  las filas de RBAC tampoco se creaban. Resuelto con `::text[]` explícito.
- Todas las semillas quedaron guardadas contra la resurrección (ver §6).
- Sembrados los emisores destacados del informe: sin eso, la card CRÉDITOS
  PRIVADOS nacía vacía (ese bloque muestra SOLO su catálogo).

### 2026-08-13 — Nace la vista
- Schema `aca` (8 tablas) + semillas de catálogos y de RBAC.
- Módulo `aca` y rol `empleado_aca` en `core/roles.py`; gate
  `/api/aca → aca` en `api/auth.py`; `/api/me` publica la capacidad para los
  escritores de la mesa.
- `api/services/aca.py` (toda la lógica), `api/routers/aca.py`,
  `api/routers/manager/aca.py`.
- Frontend: vista `/aca` con 5 tabs, `Manager → ACA` con histórico y
  configuración, proxy `/api/aca/*`, nav en NEGOCIO, gate en `src/proxy.ts`.
- `scripts/diag_aca_benchmarks.py` para medir las unidades macro antes de
  automatizar Badlar/Inflación.
- 26 tests unitarios sobre las fórmulas y el RBAC (`tests/unit/test_aca.py`).
- **`GET /roles` ahora une los roles de la DB con los de `DEFAULT_MATRIX`.**
  Antes, un rol nuevo en el código no existía en `manager.role_matrix` de prod y
  el panel no lo listaba → no había forma de asignárselo a nadie sin tocar SQL a
  mano. Los módulos de esos roles van vacíos: `get_matrix()` sigue siendo la
  verdad efectiva.
