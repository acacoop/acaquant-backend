# AGENT 2.0 — el rediseño del AV AGENT

> **Doc VIVO.** Acá se modela el agente nuevo. Todo lo que se decida vive
> únicamente en este archivo hasta que exista en código. `docs/AV_AGENT.md` es
> el diario del agente VIEJO: sirve para entender por qué las cosas están como
> están, **no** como especificación de lo que hay que construir.
>
> Regla de esta sesión (user, 2026-08-24): *«yo pregunto, vos respondés 100% con
> lo que ves del código, no con la documentación»*. Todo número que aparezca acá
> está medido sobre el repo, no citado de otro doc.

---

## 0. Por qué se rehace

El user, después de cuatro semanas seguidas con bugs nuevos:

> *«el agent tiene muchas cosas positivas pero en su conjunto es algo totalmente
> inútil en estos momentos»* · *«lo que hay hoy en día no sirve para nada, todas
> esas tablas no sirven de nada, ya he resuelto algunas cosas con el agent pero
> son poquísimas»*

Y el tamaño le da la razón. Medido sobre el repo:

| | Hoy |
|---|---|
| Services `av_agent_*` | **37 archivos, 24.319 líneas** (el más grande, 4.090) |
| Tablas `av_agent_*` en la base | **18** |
| Relojes independientes | **4** |
| Habilidades declaradas | 19 detectar · 9 explicar · 10 arreglar |

**El diagnóstico no es "hay bugs".** Es que el sistema no tiene una forma
de estar bien: cada funcionalidad nueva inaugura su propia tabla, su propio
criterio de "resuelto" y su propia manera de fallar. Arreglar los bugs de a uno
no lo cambia — hay que sacar los lugares donde equivocarse.

### Lo que se conserva

- **La familia ARREGLAR (10 acciones).** Funciona. Proponen, el user aprueba,
  aplican, re-chequean. No se toca.
- **Los detectores puros.** Reciben datos ya leídos y devuelven una lista. Esa
  parte está bien hecha y se hereda.
- **La puerta única de escritura.** Ya existe y es reciente. Se conserva la idea.

### Lo que sale del alcance

- **La familia EXPLICAR (9).** Ocho de las nueve son preguntas de mesa (rinde,
  breakeven, pivots, YTM) metidas adentro de un monitor de infraestructura por
  herencia. El user: *«la familia 2 la verdad es irrelevante de momento»*. No se
  borra todavía; se congela y se decide después.

### Lo que se rehace entero

**La familia DETECTAR y todo lo que la rodea**: sus reglas, sus umbrales, cómo
corre, cómo se guarda y cómo se cierra un problema.

---

## 1. El modelo de datos — tres tablas

Todo el agente se apoya en tres tablas y **ninguna otra** guarda estado de
problemas. Esa es la restricción central del rediseño.

### 1.1 `agente.hallazgos` — los EVENTOS

Un hallazgo es **algo que una habilidad vio en un momento**. Es inmutable: no se
edita, no se pisa. Si el problema sigue mañana, mañana hay otro hallazgo.

Campos que lleva sí o sí:

| Campo | Qué es |
|---|---|
| `id` | **Único por evento.** No se reusa jamás. |
| `habilidad` | **El nombre de la skill que lo detectó.** Requisito del user. |
| `regla` | La causa concreta dentro de esa skill. |
| `sujeto` | Qué: el bono, la tabla, el motor, el endpoint. |
| `detectado_at` | **Fecha y hora. Visible siempre, en toda pantalla.** |
| `severidad` | alta · media · baja |
| `problema` | Qué está mal, en castellano. |
| `que_hacer` | **Qué hay que hacer para resolverlo.** Si no se puede decir, la regla está mal pensada. |
| `arreglo` | Qué acción lo resuelve, o vacío si no hay ninguna. |
| `evidencia` | Los números congelados de ese momento (jsonb). |
| `estado` | Ver 1.4. |
| `cerrado_at` / `cerrado_como` / `cerrado_por` | Cómo terminó. |

**La identidad del PROBLEMA no es el `id`**: es el trío
`habilidad + sujeto + regla`. Ese trío es lo que permite decir "esto ya lo
vimos". El `id` identifica la ocurrencia; el trío identifica el problema.

### 1.2 `agente.habilidades` — el CATÁLOGO

Una fila por habilidad. **Es la tabla que hoy no existe y que resuelve el
problema más grave del agente viejo.**

| Campo | Guardado o derivado |
|---|---|
| `nombre` | guardado |
| `tipo` | guardado — `detector` · `consulta` · `accion` |
| `que_mira` | guardado — en castellano |
| `usa_ia` | guardado |
| `cada_cuanto` | guardado — su propio ritmo |
| `ventana` | guardado — cuándo tiene sentido mirar |
| `activa` | guardado — se puede apagar sin tocar código |
| `ultima_corrida_at` | **guardado** |
| `ultimo_resultado` | **guardado** — `ok` · `sin_datos` · `error` |
| `ultimo_error` | guardado |
| `corridas_hoy` | guardado |
| `hallazgos_total` | derivado de `hallazgos` |
| `ultimo_hallazgo_at` | derivado de `hallazgos` |
| `reincidencias` | derivado de `reincidencias` |

**Por qué `ultima_corrida_at` se guarda y no se deriva.** Una corrida que no
encontró nada **no deja rastro en la tabla de hallazgos**. Si la fecha se
derivara de ahí, "corrí y estaba todo bien" y "no corrí" se verían idénticos.

> **Ese es el bug estructural del agente viejo.** Hoy se resuelve con una lista
> armada a mano en cada corrida que declara *qué tipos alcancé a mirar de
> verdad*, con excepciones caso por caso escritas en el código
> (`jobs/av_agent.py::persistir` le resta `tasa_sospechosa` a mano si no pudo
> leer `portafolio.assets`). Si esa lista se arma mal, el agente cierra
> problemas que nunca miró: **el tablero queda en verde justo el día que está
> más ciego.**
>
> Con una fila por corrida eso desaparece por construcción. No hace falta que
> nadie se acuerde.

### 1.3 `agente.reincidencias` — la tabla que DEBE ESTAR VACÍA

Pedido textual del user:

> *«si ese BONO vuelve a tener un problema del mismo tipo en una fecha posterior
> a la que decía que estaba resuelto, ahí volvería… y entraría en una tabla que
> por definición queda vacía»*

**No es una lista de trabajo: es una alarma.** Una lista de 107 filas se ignora;
una tabla que debería estar vacía y tiene 3 filas, no.

Entra una fila cuando aparece un hallazgo cuyo trío
`habilidad + sujeto + regla` tuvo antes un hallazgo **cerrado por acción**, y
la fecha del nuevo es posterior a la del cierre.

| Campo | Qué es |
|---|---|
| `hallazgo_id` | el nuevo |
| `hallazgo_anterior_id` | el que se dio por resuelto |
| `arreglo_aplicado` | qué acción se había apretado |
| `resuelto_at` / `volvio_at` | cuánto aguantó |
| `dias_aguanto` | derivado |

#### La regla que la mantiene útil

**Solo puede reincidir lo que se cerró POR ACCIÓN.**

Verificado en el código actual: hoy `resuelto` **significa dos cosas mezcladas**
—`core/ciclo.py` lo documenta con todas las letras—:

- **Por acción**: alguien apretó ARREGLAR y después el detector dejó de verlo.
- **Por ausencia**: el detector no lo vio en esa corrida, y nada más. *Un bono
  que no operó esa noche se auto-resuelve.*

Si las dos pudieran reincidir, la tabla se llenaría de bonos ilíquidos que
"vuelven" cada mañana y en dos semanas nadie la miraría. Sería exactamente el
mecanismo por el que se rompió todo lo demás: algo que parece señal y es ruido.

**Entonces:**

| Cómo se cerró | ¿Puede reincidir? |
|---|---|
| Por acción | **Sí.** Si vuelve, el arreglo no sirvió. Es la alarma. |
| Por ausencia | **No.** Se reabre en silencio, como un hallazgo más. |
| Sin declarar | **No.** Ante la duda, el lado que no fabrica señal. |

La distinción `accion` / `ausencia` **ya existe escrita en `core/ciclo.py`**.
Lo que no existe es que gobierne una tabla propia: hoy vive como una columna
más adentro del mismo saco que todo lo demás, y por eso es invisible.

#### El número que habilita autonomía

De cada habilidad:

> **de los problemas que dijo que se arreglaron, ¿cuántos volvieron?**

Eso mide a la skill, no al día. Una skill con 40 hallazgos y 0 reincidencias es
confiable. Una con 5 hallazgos y 3 reincidencias diagnostica mal, aunque
encuentre poco. **Sin ese número, dejar que el agente arregle solo es apostar.**

### 1.4 Los estados de un hallazgo

Cinco, y cada uno se atiende distinto:

| Estado | Significa |
|---|---|
| `nuevo` | apareció y nadie lo miró |
| `en_curso` | se apretó el arreglo y falta la respuesta (el mercado, un job) |
| `resuelto` | ya no está — **siempre con `cerrado_como`: acción o ausencia** |
| `ignorado` | una persona dijo "no me interesa" — reversible |
| `reincidio` | estaba resuelto por acción y volvió |

Se eliminó `visto` del modelo viejo: "alguien lo miró y no hizo nada" no se
atiende distinto de `nuevo`, y un estado de más es una rama de más en cada
pantalla, para siempre.

---

## 2. El motor — un agente, una agenda

### 2.1 El problema de hoy

Cuatro programas separados hacen exactamente lo mismo —despertarse, mirar,
anotar— y lo único que los diferencia es el ritmo:

| Reloj | Ritmo | Qué mira |
|---|---|---|
| `av_agent_centinela.service` (daemon) | 30s en rueda / 5 min fuera | precios, motores, latencia, proveedores |
| `jobs.av_agent_sistema` (cron) | cada 10 min | crontab, tablas, datos partidos |
| `jobs.av_agent` (cron) | 4×/día hábil | bonos contra 1816 |
| `jobs.db_tamano` (cron) | 23:30 | permisos flojos |

El cuarto es el peor: **hay un detector de seguridad viviendo adentro de un job
que no es del agente.** Alguien que toque ese job por otro motivo apaga un
chequeo sin enterarse.

Y hay dos consecuencias que se ven en pantalla todos los días:

1. **Si se cae uno, los otros tres siguen mostrando datos frescos.** La pantalla
   se ve viva con un cuarto del agente muerto. Eso es peor que estar caído entero.
2. **Cada reloj lleva su propio horario**, y la pantalla los mezcla: te muestra
   algo de hace 10 minutos al lado de algo de hace 4 horas sin decir cuál es cuál.

### 2.2 Cómo queda

**Un solo agente, siempre vivo, con una agenda.** Adentro tiene el catálogo de
habilidades; cada una declara **su propio ritmo y su propia ventana**. El agente
se despierta, pregunta *¿a quién le toca ahora?*, la corre, anota el resultado
**en la tabla de habilidades corra o no corra**, y sigue.

Sumar una habilidad es **una fila en el catálogo**, no un programa nuevo.

**Lo único que hay que resolver a propósito:** que una habilidad lenta no tape a
una rápida. Si el barrido de bonos tarda 2 minutos, el monitor de 30 segundos no
puede quedarse esperando. Es una decisión, no algo que se descubre después.

### 2.3 El modo de guardado deja de existir

Hoy la puerta de escritura mira **el nombre del alcance** y decide sola si pisa
o si acumula. Son **tres nombres escritos a mano** (`live`, `sistema`,
`superficie`): un reloj nuevo que se llame distinto entra al modo equivocado y
nadie se entera.

En 2.0 **no hay modos**. Un hallazgo es un evento y siempre se inserta. Lo que
antes resolvía el modo "reemplazo" —no dejar 2.880 avisos del mismo problema por
día— lo resuelve la identidad: si el trío ya tiene un hallazgo abierto, se
actualiza su `visto_ultima_vez`; no nace otro.

### 2.4 "¿Qué hago si no puedo mirar?" — se contesta UNA vez

Es la pregunta más importante del sistema, porque **"no encontré nada" y "no
pude mirar" se ven iguales en la pantalla**, y uno significa que está todo bien
y el otro que estás ciego.

Hoy **cada uno de los 19 detectores la contesta por su cuenta**, con su propio
bloque de código, y no la contestan igual: unos devuelven vacío en silencio,
otros emiten un hallazgo que dice "no pude mirar", y uno revienta a propósito.
Los tres comportamientos son defendibles; el problema es que es **la misma
decisión tomada 19 veces por separado**, y la vigésima se va a tomar mal.

**En 2.0 no es problema del detector.** El detector mira y devuelve, o falla. El
agente decide qué significa un fallo:

- Devolvió → `ultimo_resultado = ok`. Lo que no vino, se cierra **por ausencia**.
- Falló → `ultimo_resultado = error` + el mensaje. **No se cierra nada.**
- No corrió → la fila lo dice sola. **No se cierra nada.**

Esa es la única implementación, en un solo lugar.

---

## 3. El contrato de una habilidad

Toda habilidad —detector, consulta o acción— declara lo mismo. Si no puede
declararlo, no entra.

```
nombre          soberanos_faltantes
tipo            detector
que_mira        bonos que 1816 lista y no están en nuestro master
usa_ia          no
cada_cuanto     cada 2 horas
ventana         rueda
reglas          no_esta_en_curvas
arreglo         alta_bono            ← qué acción lo resuelve
que_hacer       darlo de alta en mercado.curvas con los ejes sugeridos
```

**`arreglo` puede estar vacío**, pero eso es una declaración explícita: *esta
skill encuentra algo que hoy nadie sabe arreglar*. Hoy pasa con 5 de 19
detectores y no está dicho en ningún lado — el user los ve en la lista, no hay
botón, y la lista nunca baja.

---

## 4. El esquema SQL

Vive en el schema `agente`, al lado de las tablas viejas (que no se dropean,
§8). Prefijo **sin** `av_agent_`: las nuevas se distinguen solas de las 18 que
quedan apagadas.

### 4.1 `agente.habilidades` — el catálogo

```sql
CREATE TABLE IF NOT EXISTS agente.habilidades (
    nombre              text PRIMARY KEY,
    tipo                text NOT NULL,          -- detector | consulta | accion
    que_mira            text NOT NULL,          -- en castellano, para la pantalla
    dominio             text NOT NULL,          -- MERCADO | SISTEMA | DATOS | SEGURIDAD
    usa_ia              boolean NOT NULL DEFAULT false,

    -- SU PROPIO RITMO. El agente lee esto para armar la agenda (§2.2).
    cada_segundos       integer NOT NULL,
    ventana             text NOT NULL DEFAULT 'siempre',  -- rueda | habil | siempre
    activa              boolean NOT NULL DEFAULT true,

    -- SUS PROPIOS UMBRALES (§7). Editable sin deploy.
    umbrales            jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- EL ARREGLO. Vacío es una declaración explícita: "esto hoy nadie lo
    -- sabe arreglar" — hoy pasa en 5 de 19 y no está dicho en ningún lado.
    arreglo             text NOT NULL DEFAULT '',

    -- LA ÚLTIMA CORRIDA. Se GUARDA, no se deriva: una corrida que no encontró
    -- nada no deja rastro en `hallazgos`, y sin esto "corrí y estaba todo bien"
    -- y "no corrí" se ven idénticos. Es el bug estructural del agente viejo.
    ultima_corrida_at   timestamptz,
    ultimo_resultado    text,                   -- ok | sin_datos | error
    ultimo_error        text NOT NULL DEFAULT '',
    ultima_duracion_ms  integer,
    corridas_hoy        integer NOT NULL DEFAULT 0,
    corridas_dia        date,                   -- de qué día es el contador

    creada_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT habilidades_tipo_ok
        CHECK (tipo IN ('detector','consulta','accion')),
    CONSTRAINT habilidades_ventana_ok
        CHECK (ventana IN ('rueda','habil','siempre')),
    CONSTRAINT habilidades_resultado_ok
        CHECK (ultimo_resultado IS NULL
               OR ultimo_resultado IN ('ok','sin_datos','error'))
);
```

⚠️ **`corridas_hoy` va con `corridas_dia`.** Un contador sin la fecha del día
que cuenta miente en el primer cambio de día: se resetea en la primera corrida
cuya fecha no coincide, en el mismo UPDATE. Sin cron de limpieza que se pueda
olvidar.

⚠️ **`hallazgos_total`, `ultimo_hallazgo_at` y `reincidencias` NO son columnas.**
Se derivan en la lectura de las otras dos tablas. Guardarlos sería una segunda
verdad que se desincroniza sola — la REGLA #9 del repo.

### 4.2 `agente.hallazgos` — los eventos

```sql
CREATE TABLE IF NOT EXISTS agente.hallazgos (
    id                  bigserial PRIMARY KEY,  -- ÚNICO POR EVENTO, nunca se reusa

    -- LA IDENTIDAD DEL PROBLEMA es el trío, no el id (§1.1).
    habilidad           text NOT NULL REFERENCES agente.habilidades(nombre),
    sujeto              text NOT NULL,          -- el bono, la tabla, el motor
    regla               text NOT NULL,          -- la causa concreta

    nombre              text NOT NULL DEFAULT '',  -- legible: "AL30", "motor_curvas"
    severidad           text NOT NULL,          -- alta | media | baja

    problema            text NOT NULL,          -- qué está mal
    -- SIN ESTO NO SE GUARDA (invariante 2). Si no se puede decir qué hacer,
    -- la regla está mal pensada.
    que_hacer           text NOT NULL,
    evidencia           jsonb NOT NULL DEFAULT '{}'::jsonb,

    detectado_at        timestamptz NOT NULL DEFAULT now(),
    visto_ultima_vez    timestamptz NOT NULL DEFAULT now(),
    veces               integer NOT NULL DEFAULT 1,

    -- LEÍDO ≠ RESUELTO (§6.1). `leido_at` lo saca de AHORA y NADA MÁS: el
    -- hallazgo sigue abierto, sigue en LA LISTA y sigue con su botón. Son dos
    -- ejes independientes y por eso son dos columnas y no un estado.
    leido_at            timestamptz,
    leido_por           text NOT NULL DEFAULT '',

    estado              text NOT NULL DEFAULT 'nuevo',
    cerrado_at          timestamptz,
    -- accion | ausencia. SOLO `accion` habilita reincidencia (§1.3).
    cerrado_como        text,
    cerrado_por         text NOT NULL DEFAULT '',
    arreglo_aplicado    text NOT NULL DEFAULT '',

    CONSTRAINT hallazgos_severidad_ok
        CHECK (severidad IN ('alta','media','baja')),
    CONSTRAINT hallazgos_estado_ok
        CHECK (estado IN ('nuevo','en_curso','resuelto','ignorado','reincidio')),
    CONSTRAINT hallazgos_cierre_ok
        CHECK (cerrado_como IS NULL OR cerrado_como IN ('accion','ausencia')),
    -- Un cerrado sin fecha, o una fecha sin cierre, no significan nada.
    CONSTRAINT hallazgos_cierre_completo
        CHECK ((estado = 'resuelto') = (cerrado_at IS NOT NULL)),
    CONSTRAINT hallazgos_que_hacer
        CHECK (btrim(que_hacer) <> '')
);

-- UN SOLO hallazgo ABIERTO por problema. Es lo que reemplaza al "modo
-- reemplazo" del agente viejo (§1.3): si el trío ya está abierto se actualiza
-- `visto_ultima_vez` y `veces`; no nace otro. Sin esto, el monitor de 30
-- segundos deja 2.880 filas del mismo problema por día.
CREATE UNIQUE INDEX IF NOT EXISTS hallazgos_abierto_unico
    ON agente.hallazgos (habilidad, sujeto, regla)
    WHERE estado IN ('nuevo','en_curso');

CREATE INDEX IF NOT EXISTS hallazgos_problema
    ON agente.hallazgos (habilidad, sujeto, regla, detectado_at DESC);
CREATE INDEX IF NOT EXISTS hallazgos_abiertos
    ON agente.hallazgos (estado, severidad, detectado_at DESC);
CREATE INDEX IF NOT EXISTS hallazgos_por_habilidad
    ON agente.hallazgos (habilidad, detectado_at DESC);
-- AHORA: lo de hoy sin leer. Parcial, así el índice pesa lo que la tab muestra
-- y no lo que la tabla acumula.
CREATE INDEX IF NOT EXISTS hallazgos_ahora
    ON agente.hallazgos (detectado_at DESC)
    WHERE leido_at IS NULL;
```

### 4.3 `agente.reincidencias` — la que debe estar vacía

```sql
CREATE TABLE IF NOT EXISTS agente.reincidencias (
    id                  bigserial PRIMARY KEY,
    hallazgo_id         bigint NOT NULL REFERENCES agente.hallazgos(id),
    hallazgo_previo_id  bigint NOT NULL REFERENCES agente.hallazgos(id),

    -- Copiados del par para poder leer la tabla sin joins: es la que se mira
    -- primero cuando algo salió mal.
    habilidad           text NOT NULL,
    sujeto              text NOT NULL,
    regla               text NOT NULL,
    arreglo_aplicado    text NOT NULL,

    resuelto_at         timestamptz NOT NULL,   -- cuándo se dio por arreglado
    volvio_at           timestamptz NOT NULL,   -- cuándo reapareció
    dias_aguanto        numeric GENERATED ALWAYS AS
                        (EXTRACT(epoch FROM volvio_at - resuelto_at) / 86400) STORED,

    visto_por           text NOT NULL DEFAULT '',
    visto_at            timestamptz,

    -- El mismo par no entra dos veces por el mismo regreso.
    CONSTRAINT reincidencias_par_unico UNIQUE (hallazgo_id, hallazgo_previo_id),
    -- Volver ANTES de haberse resuelto no es reincidir.
    CONSTRAINT reincidencias_orden_ok CHECK (volvio_at > resuelto_at)
);

CREATE INDEX IF NOT EXISTS reincidencias_recientes
    ON agente.reincidencias (volvio_at DESC);
```

⚠️ **No hay constraint que impida insertar una reincidencia de un cierre por
ausencia** — la base no puede expresar "el previo tiene que estar cerrado como
accion" sin un trigger. **La guarda vive en la única función que inserta**, y un
test la sostiene. Es el mismo criterio que la puerta única de escritura.

⚠️ **`dias_aguanto` es una columna generada**: se calcula sola de sus dos
insumos y no puede contradecirlos. Es lo contrario del acumulado de ACA, que se
deriva en la lectura porque ahí los insumos cambian.

### 4.4 Las dos vistas que se derivan

```sql
-- La tabla de habilidades como la pide el user: nombre, cuántos hallazgos,
-- cuándo fue el último, cuándo corrió por última vez.
CREATE OR REPLACE VIEW agente.v_habilidades AS
SELECT h.nombre, h.tipo, h.dominio, h.que_mira, h.usa_ia,
       h.cada_segundos, h.ventana, h.activa, h.arreglo,
       h.ultima_corrida_at, h.ultimo_resultado, h.ultimo_error,
       CASE WHEN h.corridas_dia = current_date THEN h.corridas_hoy ELSE 0 END
           AS corridas_hoy,
       coalesce(f.total, 0)      AS hallazgos_total,
       coalesce(f.abiertos, 0)   AS hallazgos_abiertos,
       f.ultimo_hallazgo_at,
       coalesce(r.n, 0)          AS reincidencias,
       -- EL NÚMERO QUE HABILITA AUTONOMÍA (§1.3): de lo que dijo que arregló,
       -- cuánto volvió.
       CASE WHEN coalesce(f.cerrados_por_accion, 0) = 0 THEN NULL
            ELSE round(coalesce(r.n, 0)::numeric
                       / f.cerrados_por_accion * 100, 1) END AS pct_volvio
  FROM agente.habilidades h
  LEFT JOIN LATERAL (
        SELECT count(*) AS total,
               count(*) FILTER (WHERE estado IN ('nuevo','en_curso')) AS abiertos,
               count(*) FILTER (WHERE cerrado_como = 'accion') AS cerrados_por_accion,
               max(detectado_at) AS ultimo_hallazgo_at
          FROM agente.hallazgos WHERE habilidad = h.nombre) f ON true
  LEFT JOIN LATERAL (
        SELECT count(*) AS n
          FROM agente.reincidencias WHERE habilidad = h.nombre) r ON true;

-- AHORA (§6.1): lo de HOY sin leer. Una condición, un COUNT, sin sumas en el
-- navegador. El día es ART, no UTC: el día UTC arranca a las 21:00 de acá y
-- mezclaría dos días bajo el mismo rótulo.
CREATE OR REPLACE VIEW agente.v_ahora AS
SELECT f.id, f.habilidad, f.sujeto, f.regla, f.nombre, f.severidad,
       f.problema, f.que_hacer, f.detectado_at, hab.dominio
  FROM agente.hallazgos f
  JOIN agente.habilidades hab ON hab.nombre = f.habilidad
 WHERE f.leido_at IS NULL
   AND (f.detectado_at AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
       = (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
 ORDER BY f.detectado_at DESC;

-- LO QUE PIDE TRABAJO. Un hallazgo ignorado o resuelto no está acá.
CREATE OR REPLACE VIEW agente.v_abiertos AS
SELECT f.*, hab.dominio, hab.tipo, hab.que_mira
  FROM agente.hallazgos f
  JOIN agente.habilidades hab ON hab.nombre = f.habilidad
 WHERE f.estado IN ('nuevo','en_curso')
 ORDER BY CASE f.severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
          f.detectado_at DESC;
```

### 4.5 Lo que NO tiene tabla

- **La foto por corrida.** El agente viejo guarda 60 corridas de fotos
  (`TTL_CORRIDAS = 60`). Con un hallazgo abierto que lleva `veces` y
  `visto_ultima_vez`, la foto no agrega nada que no esté.
- **Los objetos aparte de los eventos.** Hoy son dos tablas (`hallazgos` +
  `items`) que se escriben juntas y "no pueden divergir". Acá el hallazgo
  abierto **es** el objeto: una tabla, una verdad.
- **El estado de las acciones.** Vive donde vive hoy (familia 3 no se toca); el
  hallazgo solo guarda `arreglo_aplicado`.

---

---

## 5. Las habilidades de familia 1, una por una

### 5.1 Renombres y rediseños

#### `soberanos_faltantes` (era `falta_en_base`)

Bonos que 1816 lista y no están en `mercado.curvas`.

**Cambio:** el cruce va por `mercado.curvas.ticker` **directo**. Hoy el detector
le saca la letra D/C final "por las dudas" — una defensa contra un caso que
después del renombre de columnas ya no debería existir (`ticker` es la PK
`AL30`; el símbolo de mercado vive en `instrumento`).

**La clasificación por curva se conserva** tal cual: tabla explícita de 28
nombres de 1816, y una curva desconocida se reporta como tal en vez de
clasificarse mal en silencio.

**La pregunta "¿cotiza en Primary?" sale de acá** → pasa a `deteccion_primary`.

#### `deteccion_primary` — habilidad NUEVA, tipo `consulta`

Contesta *"¿este símbolo cotiza en Primary?"* para quien la necesite.

Hoy esa pregunta se hace **adentro** de `falta_en_base`, pero la necesitan
también `bono_sin_precio`, `precio_moneda` y las tres acciones de patas — y cada
una la resuelve a su manera.

**Por eso el catálogo necesita la columna `tipo`.** Una consulta no produce
hallazgos: si figurara como detector, aparecería con 0 hallazgos para siempre y
parecería rota.

#### `bono_sin_flujo` (era `sin_flujo`)

Bonos cargados sin cronograma de pagos. **El predicado se conserva** (mira la
definición de flujo, no si el array está vacío — una LECAP es cupón cero y no le
falta nada).

**El cambio no es el detector: es el cableado.** Verificado — las dos piezas del
arreglo ya existen y están desconectadas:

- `core/mercado_1816.cashflow(ticker)` trae el cronograma completo.
- La acción `mercado.alta_flujos` hace todo el trabajo: ticker → catálogo 1816
  ya persistido (0 créditos) → baja el cuadro → simula la TEA → coteja → **solo
  escribe si la cadena cierra**. Y si 1816 no lo tiene, lo dice.

**El problema:** esa acción cuelga de **otro** control (`titulos_sin_flujo`, de
Manager, que habla en unidades de Aunesa) y no del detector (que habla en
tickers del master). Son dos mundos puenteados a mano.

En 2.0 la habilidad **declara su arreglo** y eso deja de poder pasar.

#### `bono_sin_tasa` (reemplaza a `tasa_sospechosa`)

> *«tasa_sospechosa NO FUNCIONA HOY EN DÍA»* — user.

**Lo que se elimina:** las 6 reglas bajo un mismo nombre y sus umbrales a dedo
(paridad 40–160, TEA −30%/+60%). Ahí se genera la mayor parte del ruido.

**Lo que queda: una sola regla**, el cruce de dos condiciones:

| ¿Tiene precio? | ¿Tiene TEA? | Veredicto |
|---|---|---|
| No | No | **Ilíquido.** No cotiza a ninguna tasa. **No es un hallazgo.** |
| Sí | Sí | Todo bien. |
| **Sí** | **No** | **Hallazgo** → el ticker entra a la lista de prioridad. |

**La lista de prioridad.** Acumula tickers durante el día. Se purga al día
siguiente a las **9:00**. Cada **15 minutos** se le piden a 1816 la **TEA, la
duration y el precio** de todo lo que tenga adentro.

La máquina ya existe: `core/mercado_1816.indicadores_vigentes(tickers, campos)`
recibe una **lista** y trae los campos de la última rueda con datos, retrocediendo
día hábil por hábil si hoy todavía no hay. Es lo que usa `jobs/tamar_1816`.

**Qué se hace con esa tasa — patrón TAMAR, decidido por el user:**

- **NO se escribe en `mercado.market_snapshot`.** Esa tabla es del motor:
  Primary, live, 5 segundos. Esto es 1816, con delay.
- Va a **tabla propia**.
- **Se juntan en la LECTURA**, y cada fila viaja diciendo **de dónde salió su
  tasa** y **de cuándo es**.

Con eso la habilidad deja de ser "te aviso que falta algo" y pasa a **tapar el
agujero**: la mesa deja de ver `--` y ve la tasa de 1816, marcada como tal.

#### `motor_caido` — hay que rehacer cómo sabe el horario

> *«yo no sé si hoy hay una regla que SEPA detectar exactamente el horario de
> cada uno»* — user. **No la hay.**

Verificado. Hoy son dos mecanismos y los dos son flojos:

1. **La ventana es una categoría de 4 valores** (`rueda`, `rueda_agro`,
   `always`, `diario`). **Todas las piezas de `rueda` comparten una única
   ventana hardcodeada: 10:00–17:05 ART.** Las de `always` y `diario` **no
   tienen ventana**: están "en ventana" las 24 horas.
2. **La hora de arranque sale de un regex sobre prosa.** Cada pieza declara su
   cadencia como texto libre (`"cada 30m :05,:35 · 15-22 UTC L-V"`) y el código
   le busca las horas con una expresión regular. Si la prosa está escrita
   distinto, no encuentra nada y **decide avisar igual**.

**El horario real —`deploy/crontab.txt` y los units de systemd— no se lee.**

**Lo que hay que hacer:** derivar el horario de cada pieza de esa fuente. Ya está
al alcance: el agente **ya lee el crontab** para la habilidad `cron_desalineado`.
Es la misma fuente, usada dos veces.

**Y la segunda mitad, que es la más importante:** hoy *"caído"* significa **una
sola cosa para las 53 piezas** — que su tabla no recibió escrituras en X
segundos, con X puesto a mano. Eso no es entender qué es que se cayó. Un motor
de precios sobre un papel ilíquido no escribe y no está caído.

**Cada pieza declara su propia prueba de vida:** qué escribe, con qué ritmo, y
qué es normal *para ella*.

Las tres guardas actuales se conservan porque son correctas — gracia de arranque
de 30 minutos, no juzgar una pieza fuera de su ventana, no juzgar un job al que
todavía no le tocó — **pero apoyadas en el horario real y no en un regex.**

#### `db_cambio` → peso en vivo + alarma

> *«esto tiene que ser más realtime y mostrar el peso que va dando de cada
> tabla, no con la foto de ayer»* — user.

El tamaño de una tabla es **una query barata contra el catálogo de Postgres**.
No hace falta una foto diaria.

Cambia la pregunta: si es en vivo, **¿contra qué compara?** Se guarda una serie
por hora (barata, se purga sola) y salen **dos cosas de la misma fuente**:

- **El peso actual de cada tabla** — información, siempre visible.
- **Lo que creció fuera de lo suyo en 24h** — eso sí es hallazgo.

### 5.2 Se elimina

#### `motor_ruidoso`

> *«no sé si sirve sinceramente, con que esté bien el motor_caido alcanza»* —
> user. De acuerdo.

**Se conserva una sola cosa, como evidencia y no como skill:** cuando
`motor_caido` dispara, que el hallazgo traiga *lo último que dijo ese motor
antes de morir*. Eso vale. Una skill propia que cuenta que un motor sano loguea
warnings, no.

### 5.3 Se conservan con retoques menores

| Habilidad | Retoque |
|---|---|
| `hueco_de_curva` | Sin cambios. Reporta por AJUSTE, no por bono, y declara que no tiene arreglo automático (es desarrollo, no dato). |
| `salud` | Es un traductor de otro sistema y está bien que lo sea. Solo: el hallazgo tiene que traer **el horario real del job**, no el schedule como texto. |
| `bono_sin_precio` | Las 4 causas se conservan (sin símbolo · no suscripto · sin punta · precio viejo). El umbral de 20 minutos pasa al catálogo. |
| `precio_moneda` | Se conserva. La prueba es aritmética (paridad cruda vs paridad ÷ MEP) y es sólida. |
| `actividad` | Se conserva. Es de los mejores: razonamiento invertido, y en día hábil no toca la base. |
| `cron_desalineado` | Se conserva. **Además pasa a ser la fuente de horarios de `motor_caido`.** |
| `tabla_quieta` | Se conserva. La cadencia se mide observando, no se declara. |
| `latencia` | Se conserva. Compara cada endpoint contra su propia mediana, con tres guardas. Los umbrales pasan al catálogo. |
| `proveedor_caido` | Se conserva. Lee el rastro de llamadas reales y solo llama cuando ya hay falla. |
| `dato_partido` | Se conserva. Es de los mejores: no necesita mercado abierto ni precio. |
| `permiso_flojo` | Se conserva, **pero se muda**: hoy vive adentro de `jobs/db_tamano`, que no es del agente. |
| `respuesta` | Se conserva. Cierra el círculo de una acción cuyo efecto lo contesta el mercado. |
| `recuperado` | Se conserva. Avisa cuando algo vuelve, por el mismo canal que avisó la caída. |

### 5.4 Huecos identificados

1. **Nadie mira que un arreglo aplicado haya quedado** → lo resuelve
   `reincidencias` (§1.3). **Cerrado.**

**Descartado por el user, no volver sobre esto:**

- Vigilar el presupuesto de créditos de 1816.
- Cualquier cosa alrededor del discovery de instrumentos
  (`jobs.validar_instrumentos`, catálogo de especies contra Primary). El agente
  no toca ese terreno.

---

## 6. Las pantallas

### 6.1 AHORA — el noticiero del día

> *«Tiene que leer HALLAZGOS = fecha IGUAL A HOY. Y es simple: es la sumatoria
> de hallazgos con fecha == HOY. Y tiene que tener la posibilidad de marcar como
> leídos los mensajes. Si los marcás como leídos, desaparecen. Es solo
> informativo.»* — user, 2026-08-24.

**La regla, entera:**

```
AHORA = hallazgos WHERE detectado_at::date = HOY (ART) AND leido_at IS NULL
```

Nada más. **Un COUNT sobre una condición.** El badge y la lista salen de la
misma query, así que no pueden decir cosas distintas.

#### Qué cambia respecto de hoy

Medido sobre el código actual, el badge «AHORA 92» **lo suma el navegador**
juntando cuatro cosas de **dos endpoints con frescuras distintas**:

```
nAhora = cent.hoy.novedades            ← /centinela, se refresca cada 20 s
       + preguntas + decisiones        ← /vista, se carga UNA VEZ al abrir
       + avisos abiertos               ← /vista
       + hallazgos marcados "noticia"  ← /vista
```

Y `novedades` es a su vez tres cosas (`roto` + `apareció` + `volvió`) contadas
sobre una lista **cortada en 200 filas**, leída de `av_agent_items` —una tabla
distinta de la que dibuja LA LISTA—, con un filtro extra por día hábil.

De ahí salen cinco defectos que la regla nueva elimina de una:

| Defecto de hoy | Por qué desaparece |
|---|---|
| AHORA y LA LISTA leen tablas distintas | hay una sola tabla (§1) |
| El badge mezcla dos frescuras (20 s y "cuando abriste") | un solo endpoint, una sola query |
| Los `LIMIT 200 / 40` truncan el conteo **para abajo**, en silencio | el conteo es un `COUNT(*)`, no un `len()` sobre una lista paginada |
| El «92» no se puede descomponer | cada fila trae su habilidad: el número se abre solo |
| El corte del día se peleó tres veces en el código | una condición, escrita una vez, en el backend |

#### LEÍDO no es RESUELTO

Son **dos ejes independientes**, y por eso son dos columnas y no un estado más:

- **`leido_at`** — *"ya me enteré"*. Lo saca de AHORA y **de ningún otro lado**.
- **`estado`** — *"el problema sigue o no sigue"*. Vive su ciclo aparte.

Un hallazgo marcado como leído **sigue abierto, sigue en LA LISTA y sigue con su
botón de arreglo**. Marcar leído es bajar el ruido del día, no cerrar nada.

Es la distinción que hoy no existe: el agente viejo tiene `marcar_visto`, y su
propio comentario aclara que *«no lo resuelve ni lo esconde»* — pero está
mezclado adentro del mismo campo de estado que todo lo demás.

#### AHORA se vacía sola

Un hallazgo nace con la fecha del día en que se detectó. **Al día siguiente sale
de AHORA aunque nadie lo haya leído**, porque su fecha ya no es hoy.

Eso funciona gracias al índice único de §4.2: un problema que persiste **no crea
una fila nueva** —sube `veces` y `visto_ultima_vez`— así que su `detectado_at`
sigue siendo el del día que apareció. AHORA no puede acumular.

#### La consecuencia que hay que aceptar a propósito

**Un motor caído hace tres días NO está en AHORA.** Apareció el lunes, hoy es
jueves.

Hoy el código hace lo contrario: mete lo `roto` **sin corte de fecha**, después
de que el user reclamara *«¿cómo no me va a avisar justo de los motores en el
AHORA?»*. Con la regla nueva eso se cae — y está bien que se caiga, porque
**AHORA es informativo**: un motor caído hace tres días no es una novedad, es
trabajo pendiente, y su casa es LA LISTA, donde tiene botón.

Si más adelante hace falta que lo viejo y roto grite, **no se arregla
ensuciando AHORA**: se arregla con severidad en LA LISTA.

#### Lo único que se escribe desde AHORA

```
POST /api/ia/av-agent/ahora/leidos   { ids: [...] }
    → UPDATE agente.hallazgos
         SET leido_at = now(), leido_por = <email>
       WHERE id = ANY(%s) AND leido_at IS NULL
    → releer AHORA
```

Idempotente (`AND leido_at IS NULL`: marcar dos veces no pisa quién fue el
primero) y reversible desde la misma pantalla.

**Y nada más.** AHORA no aprueba, no arregla, no ignora, no vota. Un botón de
acción acá volvería a mezclar el noticiero con la lista de trabajo, que es de lo
que el user viene escapando.

---

## 7. Los umbrales salen del código

Hoy están desparramados en 6 archivos y ninguno se puede tocar sin deploy:

| Umbral | Valor | Dónde vive |
|---|---|---|
| Paridad sana | 40 – 160 | `av_agent.py` |
| TEA sana | −30% a +60% | `av_agent.py` |
| Precio viejo | 20 min | `av_agent.py` |
| Rueda | 13–20 UTC | `av_agent.py` |
| Latencia: ventana / base / factor / delta | 2h / 72h / 2,5× / 300ms | `av_agent_latencia.py` |
| Latencia: mínimos | 20 req / 6h / 3 errores | `av_agent_latencia.py` |
| Proveedor caído | 20 min / 1 fallo | `av_agent_proveedores.py` |
| Gracia de arranque | 30 min | `av_agent_motores.py` |
| Apertura / cierre | 10:00 / 17:05 ART | `diagnostico.py` |

En 2.0 **cada umbral es del dueño que lo usa** y vive en el catálogo de
habilidades, editable sin deploy.

---

## 8. Invariantes — lo que no se puede romper

1. **Una habilidad que no corrió no cierra nada.** Nunca.
2. **Un hallazgo sin `que_hacer` no se guarda.** Si no se puede decir qué hacer,
   la regla está mal pensada.
3. **Todo lo que se muestra lleva fecha y hora.**
4. **Solo lo cerrado POR ACCIÓN puede reincidir.** Ante la duda, por ausencia.
5. **Ninguna otra tabla guarda estado de problemas.** Tres, y nada más.
6. **La respuesta a "no pude mirar" está escrita una sola vez**, en el agente,
   no en cada habilidad.
7. **Cada habilidad declara su arreglo, o declara que no tiene.**
8. **Nada del agente vive fuera del agente.** Ningún detector adentro de un job
   ajeno.

---

## 9. Migración

Las 18 tablas `av_agent_*` **no se dropean**. Borrar código se revierte; borrar
datos no. Se dejan de escribir y se decide después.

Lo que sí se apaga desde el primer día: los cuatro relojes, reemplazados por el
agente único.

---

## 10. Estado

| Etapa | Estado |
|---|---|
| Diagnóstico de lo que hay | ✅ hecho, medido sobre el repo |
| Modelo de las 3 tablas | ✅ acordado |
| Motor único con agenda | ✅ acordado |
| Familia 1 redefinida | ✅ acordado |
| Familia 3 (arreglar) | conservada sin cambios |
| Familia 2 (explicar) | congelada, fuera de alcance |
| Esquema SQL de las 3 tablas | ✅ §4 |
| Pantalla AHORA | ✅ §6.1 |
| Resto de las pantallas | ⬜ pendiente |
| Implementación | ⬜ pendiente |

---

## Changelog

- **2026-08-24** — AHORA queda definida (§6.1): sumatoria de hallazgos con
  fecha de HOY sin leer, un solo COUNT sobre una condición, con `leido_at`
  separado de `estado` — leer no resuelve. Se documenta cómo funciona hoy (el
  badge lo suma el navegador de dos endpoints con frescuras distintas) y qué
  defecto elimina cada cambio.
- **2026-08-24** — Se suma el esquema SQL (§4): las tres tablas, sus
  constraints, el índice único que reemplaza al «modo reemplazo», las dos
  vistas derivadas y lo que a propósito NO tiene tabla. Discovery de
  instrumentos queda descartado del alcance.
- **2026-08-24** — Nace el doc. Diagnóstico medido sobre el repo, modelo de tres
  tablas (hallazgos / habilidades / reincidencias), motor único con agenda, y
  las 19 habilidades de familia 1 redefinidas: 4 renombradas o rediseñadas
  (`soberanos_faltantes`, `bono_sin_flujo`, `bono_sin_tasa`, `db_cambio`), 1
  nueva (`deteccion_primary`), 1 eliminada (`motor_ruidoso`), 1 con el horario
  rehecho (`motor_caido`), el resto conservadas.
