# AGENT — EL AV AGENT, doc único

> **⚠️ ESTA ES LA ÚNICA VERDAD DEL AGENTE.** El 2026-08-31 se fusionaron los dos
> docs que había —`AGENT_2.0.md` (la especificación) y `AV_AGENT.md` (el diario)—
> porque tener dos era exactamente el problema que el agente persigue en los
> datos: **dos copias del mismo tema sin un árbitro declarado** (REGLA #9). El que
> abría `AV_AGENT.md` creía estar leyendo cómo funciona; el que abría
> `AGENT_2.0.md` creía estar leyendo un plan a futuro. Los dos se equivocaban.
>
> El archivo tiene **DOS PARTES y no se confunden**:
>
> | Parte | Qué es | Cómo leerla |
> |---|---|---|
> | **A — CÓMO FUNCIONA** | La especificación viva: el modelo, el motor, las habilidades, las pantallas, los invariantes | **Esto manda.** Si el código y esta parte se contradicen, es un bug de una de las dos |
> | **B — EL DIARIO** | 26 entradas `§0.x`: cada una es **un bug real y la decisión que lo cerró** | **Es historia.** Habla del agente VIEJO y de código que ya no existe. Sirve para no repetir, nunca como spec |
>
> **El número manda desde el código, no desde acá.** Cuántas habilidades hay lo
> dice `agente/catalogo.py`; cuántas tablas, `sql/schema.sql`. Si acá aparece un
> conteo, es una foto con fecha.

---

# PARTE A — CÓMO FUNCIONA

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

> ⚠️ **Y por eso el `sujeto` NO puede ser una constante** (2026-08-28). Si un
> detector emite «N cosas están mal» con un sujeto fijo, esas N comparten
> identidad y el agente las trata como UN problema: «no me interesa» las calla a
> todas —y a las que aparezcan después—, `veces` cuenta vueltas de la bolsa, y
> arreglar una no cierra nada, solo baja un número.
>
> El síntoma se ve en la pantalla y es fácil de confundir con un problema de
> redacción: **la habilidad, el `nombre` y el `problema` dicen los tres lo
> mismo**. Es la firma de que el sujeto no tiene el dato, así que hubo que
> escribir a mano de qué es el problema mientras el dato real —cuál cron, cuál
> título— se iba a `evidencia`, que ninguna pantalla dibuja.
>
> La regla, entonces, es mecánica: **si tenés que redactar a mano de qué es el
> problema, el sujeto está mal elegido.** Lo congela
> `test_de_que_es_el_problema_nunca_se_escribe_a_mano`, que prohíbe un `nombre=`
> con string literal en cualquier detector.
>
> El sujeto correcto es **lo que se atiende de a uno**. Para `ficha_incompleta`
> es el CAMPO (379 títulos sin clase son un trabajo de carga, no 379 problemas);
> para `cron_desalineado` es cada CRON, porque uno puede ser legítimo y el otro
> basura. Y tiene que ser único: el nombre del job solo no alcanza —23 se repiten
> en `deploy/crontab.txt`— así que `crontab.sujeto()` le mete el horario.

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

Verificado contra el agente VIEJO: ahí `resuelto` **significaba dos cosas
mezcladas** —su `core/ciclo.py`, ya borrado, lo documentaba con todas las letras—:

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

La distinción `accion` / `ausencia` ya estaba escrita en el agente viejo
(`core/ciclo.py`), pero no gobernaba nada: vivía como una columna más adentro
del mismo saco que todo lo demás, y por eso era invisible. **Acá gobierna una
tabla propia** (`agente.reincidencias`) y la congela el invariante #4.

#### Es un HECHO, no un puntaje

La tabla registra que algo volvió. Nada más: no hay confianza acumulada, ni
hitos, ni escalera de días, ni voto de nadie. Todo eso existió en el agente
viejo (§9) y es de lo que hay que salir.

Que la fila exista ya dice lo único que importa: **el arreglo que aplicamos no
sirvió.**

#### Y se APAGA con su hallazgo (2026-09-04)

⚠️ `agente.reincidencias` **sólo recibe `INSERT`**: no hay, ni tiene que haber,
una línea que cierre una fila — que `alta_bono` aguantó 3,6 días sobre M31G6 es
un hecho, y es la evidencia con la que después se decide qué arreglo es
confiable.

Pero la ALARMA no es la tabla: es **lo que sigue vivo de la tabla**. Una
reincidencia está activa mientras su hallazgo siga abierto; cuando el hallazgo
se cierra —por la vía que sea— la alarma ya fue atendida.

Sin eso, la tabla que DEBE estar vacía era **matemáticamente imposible de
vaciar**. M31G6 volvió el 28/08, el detector se corrigió ese mismo día, el
hallazgo se cerró, y el cartel rojo siguió arriba de la pantalla una semana
describiendo un bono que ya venció. Es el mismo defecto que el agente evita en
su propio latido —*«un círculo que está en rojo cuando todo está bien enseña a
ignorar el círculo»*— y termina igual: la fila número 16, la que importaba, no
la mira nadie.

**El criterio es UNO y se cuenta en TRES lugares** (el cartel del modal, el ⚠
del panel de HABILIDADES y los casos del lab). Los tres hacen el mismo `JOIN`
contra `agente.hallazgos`; si se separan, cada pantalla muestra un número
distinto y no falla nada — la REGLA #9 adentro del agente. Lo congela
`test_la_reincidencia_se_apaga_con_su_hallazgo`.

### 1.4 Los estados de un hallazgo

Cinco, y cada uno se atiende distinto:

| Estado | Significa |
|---|---|
| `nuevo` | apareció y nadie lo miró |
| `en_curso` | se apretó el arreglo y falta la respuesta (el mercado, un job) |
| `resuelto` | ya no está — **siempre con `cerrado_como`: acción, ausencia o caducidad** (§6.8) |
| `ignorado` | una persona dijo "no me interesa" — reversible |
| `reincidio` | estaba resuelto por acción y volvió — **sigue ABIERTO**: se actualiza, se cierra y se muestra igual que `nuevo` (§0.cy) |

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
    WHERE leido_at IS NULL AND estado NOT IN ('resuelto','ignorado');
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
       -- Cuántas veces algo que esta habilidad dio por arreglado volvió. Es
       -- un CONTEO, no un puntaje (§1.3).
       coalesce(r.n, 0)          AS reincidencias
  FROM agente.habilidades h
  LEFT JOIN LATERAL (
        SELECT count(*) AS total,
               count(*) FILTER (WHERE estado IN ('nuevo','en_curso','reincidio')) AS abiertos,
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
   AND f.estado NOT IN ('resuelto','ignorado')
   AND (f.detectado_at AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
       = (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
 ORDER BY f.detectado_at DESC;

-- LO QUE PIDE TRABAJO. Un hallazgo ignorado o resuelto no está acá.
CREATE OR REPLACE VIEW agente.v_abiertos AS
SELECT f.*, hab.dominio, hab.tipo, hab.que_mira
  FROM agente.hallazgos f
  JOIN agente.habilidades hab ON hab.nombre = f.habilidad
 WHERE f.estado IN ('nuevo','en_curso','reincidio')
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

#### `cedear_faltante` — habilidad NUEVA (2026-09-04)

CEDEARs que Primary lista y no están en `mercado.cedears`, y los activos del
master que Primary NO lista. Arreglo: `alta_cedear` (pide datos: se **tilda**
cuáles). Historia y decisiones en §0.dl.

**La identidad es la FICHA, no el nombre (REGLA #9).** No se busca «símbolos
con forma `MERV - XMEV - X - 24hs`» (GGAL la tiene): se lee el `cficode` que
Primary les pone a los CEDEARs que YA tenemos y se buscan los que faltan con
esa misma ficha, plazo y moneda. Esa calibración no está escrita en ninguna
constante — sale de nuestro propio master cada vez, y con menos de
`min_propios` (3) coincidencias el detector dice «no pude mirar».

**UN hallazgo por familia**, como `ficha_incompleta`: Primary lista muchos más
CEDEARs de los que la mesa mira. La lista viva la recalcula `preview`, y el
alta recorre la cadena ENTERA por cada uno tildado: Primary (foto y en vivo) →
ficha → `mercado.cedears` → historia EOD (Yahoo) → ADR (Finnhub) → el motor,
que **relee el master cada 60 s** y lo suscribe sin reiniciar.

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

**Y el horario, que es lo de fondo** (user, 2026-08-24: *«es fundamental que
todo tenga claro el horario de mercado para saber cuándo frenar»*):

| Cuándo | Quién | Qué hace |
|---|---|---|
| 10:00–17:00 ART | `bono_sin_tasa` (ventana `rueda`) | detecta, cada 15 min |
| 10:00–16:45 ART | el cron `agente_tasa` | le pide la tasa a 1816 |
| **17:30 ART** | **`tasas_al_cierre`** (ventana `cierre`) | barre lo que quedó, UNA vez, y canta lo que ni 1816 tiene |
| después | nadie | **no se le pide más nada al mercado** |

⚠️ El cron decía `13-20` y en cron eso incluye **la hora 20 entera** (20:00 a
20:59): le seguía pidiendo precios a 1816 hasta las 17:59 ART, con el mercado
cerrado desde las 17. Es el error de rango que no se ve leyendo.

⚠️ **`cierre` es una VENTANA del catálogo, no un cron.** Un cron a las 17:30
sería el quinto reloj, y salir de los cuatro relojes fue todo el punto (§2). El
horario vive en `agente/reloj.py` —`RUEDA_UTC`, `CIERRE_UTC`— y un test prohíbe
que nadie más lo redefina.

**Qué se hace con esa tasa — patrón TAMAR, decidido por el user:**

- **NO se escribe en `mercado.market_snapshot`.** Esa tabla es del motor:
  Primary, live, 5 segundos. Esto es 1816, con delay.
- Va a **tabla propia**.
- **Se juntan en la LECTURA** (`api/services/curvas_vista.py`), y cada fila
  viaja diciendo **de dónde salió su tasa** y **de cuándo es**.
- **El fallback va ÚLTIMO y solo si la TEA quedó vacía.** No le puede ganar al
  motor: donde el motor calcula, su número es live y el de 1816 tiene atraso.

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
AHORA = hallazgos WHERE detectado_at::date = HOY (ART)
                   AND leido_at IS NULL
                   AND estado NOT IN ('resuelto', 'ignorado')
```

Nada más. **Un COUNT sobre tres condiciones**, las tres sobre la misma tabla.

Lo resuelto no está: es la misma regla que hoy, y es la correcta — un problema
que se arregló en la mañana no es una novedad de la tarde. El badge y la lista salen de la
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
POST /api/agente/leidos              { ids: [...] }
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

### 6.2 ENCONTRÓ — solo lo que tiene arreglo

> *«En ENCONTRÓ deben estar solamente los que tengan un arreglo. Hoy por ejemplo
> figuraban SALUD JOB, que en sí son avisos: se entiende que tiene que estar en
> AHORA, no en ENCONTRÓ.»* — user, 2026-08-24.

```
ENCONTRÓ = hallazgos WHERE estado IN ('nuevo','en_curso','reincidio')
                       AND arreglo <> ''
```

**AHORA y ENCONTRÓ no son dos cajas: son dos EJES.**

| | Pregunta que hace | Filtro |
|---|---|---|
| **AHORA** | ¿pasó **hoy** y ya me enteré? | tiempo + leído |
| **ENCONTRÓ** | ¿puedo **hacer algo**? | tiene arreglo |

Un hallazgo accionable aparece en los dos, y eso **no es duplicarlo**: hoy es una
novedad, y hasta que se arregle es trabajo. La diferencia es que de AHORA se va
solo mañana, y de ENCONTRÓ se va únicamente cuando se arregla.

Eso reemplaza la regla vieja de «cada cosa tiene UNA casa», que se peleó cuatro
veces en el código y nunca cerró — porque no era un problema de casas: eran dos
preguntas distintas obligadas a compartir un tabique.

### 6.3 El vocabulario

La palabra que faltaba es **CLASE**, y se DERIVA — nadie la escribe:

| Palabra | Qué es |
|---|---|
| **HABILIDAD** | lo que el agente sabe hacer (§3) |
| **REGLA** | una causa concreta que una habilidad sabe distinguir |
| **HALLAZGO** | un evento: una habilidad vio algo, en un momento (§1.1) |
| **ARREGLO** | la acción que resuelve ese hallazgo |
| **DOMINIO** | de qué habla: MERCADO · SISTEMA · DATOS · SEGURIDAD |
| **CLASE** | **derivada**: `aviso` si no tiene arreglo · `trabajo` si lo tiene |

```
clase = 'trabajo'  si arreglo <> ''  →  AHORA (hoy) + ENCONTRÓ (hasta arreglarse)
clase = 'aviso'    si arreglo  = ''  →  AHORA y nada más
```

⚠️ **El arreglo se declara por REGLA, no por habilidad.** Una habilidad puede
tener reglas de las dos clases: en `precio_moneda`, `pata_equivocada` se arregla
con un botón y `cotiza_en_pesos` es contexto. Colgar el arreglo de la habilidad
obligaría a elegir mal para una de las dos.

La habilidad muestra el resumen (*todas · algunas · ninguna de mis reglas tienen
arreglo*), pero **quien manda es la regla**, y por eso el hallazgo la lleva.

### 6.4 Qué es un ARREGLO — y qué no

**Un arreglo ESCRIBE en algún lado.** Cambia el sistema. Después de apretarlo,
el mundo es distinto.

**No son arreglos**, y hoy están mezclados como si lo fueran:

| No es arreglo | Por qué |
|---|---|
| «↻ chequear ahora» | vuelve a mirar. **Mirar no arregla.** |
| «✔ entendido» | es marcar leído — eje AHORA (§6.1) |
| «✖ es ruido» | es un voto sobre el agente, no sobre el problema |
| «ignorar» | esconde, no resuelve |
| explicar / simular | calcula, no muta |

#### El caso que lo motivó, medido

`salud` **declara una acción** en el catálogo, así que sus hallazgos caen en
ENCONTRÓ. Pero su puerta es **de solo lectura**: en el front, el botón APLICAR
está deshabilitado por diseño para ese modo, y lo único que ofrece es
«↻ chequear ahora», que re-corre el control.

**O sea: un aviso con forma de trabajo.** El user lo detectó desde la pantalla,
sin ver el código, y tenía razón.

#### Cuánto se achica ENCONTRÓ con esta regla

Medido sobre el repo, cruzando los 19 detectores contra las 10 acciones por la
causa que cada acción declara:

| | |
|---|---|
| Detectores con arreglo REAL | **3** — `sin_flujo`, `sin_precio`, `precio_moneda` |
| Detectores con arreglo PARCIAL | **1** — `tasa_sospechosa`, y solo 1 de sus 6 reglas |
| Detectores declarados como accionables sin serlo | **2** — `salud`, `falta_en_base` |
| Detectores sin ninguna puerta | **13** |
| **Acciones que NO cuelgan de ningún detector** | **5 de 10** |

Las cinco huérfanas —`assets.cartera`, `assets.fci`, `contrapartes.alta`,
`avisar.responsable`, `sistema.rehacer_dia`— cuelgan de controles de Manager,
no de habilidades del agente. Funcionan; simplemente **el agente no las conoce
como suyas**.

**ENCONTRÓ pasa de 19 tipos a 3 o 4.** Eso no es perder cobertura: los otros 15
nunca tuvieron nada que apretar. Lo único que cambia es que dejan de simular que
sí, y se van al noticiero, que es su lugar.

Y deja a la vista la lista de trabajo real del programa: **las 5 acciones
huérfanas** hay que colgarlas de una habilidad, y **los 13 sin puerta** hay que
decidir uno por uno si merecen una.

### 6.5 Dónde vive cada cosa — el mapa completo

| | AHORA | ENCONTRÓ | HISTORIAL |
|---|---|---|---|
| **Qué muestra** | lo de hoy, sin leer, sin resolver | lo abierto que tiene arreglo | el historial y las reincidencias |
| **Se vacía** | sola, cada día | solo arreglando | nunca |
| **Botones** | «leído» y nada más | el arreglo de cada fila | ninguno |
| **Ordena por** | hora, la última arriba | severidad | fecha |
| **Si está vacía** | hoy no pasó nada | no hay nada que apretar | — |

**Ninguna pantalla deriva nada.** Las tres leen una vista de la base y dibujan.
Acción, estado, clase y nombre vienen resueltos del backend — que es la regla
que el front ya tiene escrita y que el agente viejo rompió por otro lado.

### 6.6 HISTORIAL — el libro de auditoría

**Se conserva. Es la única pantalla del agente viejo que no tiene el vicio de
las otras**, porque no opina: dice quién tocó qué, cuándo, de qué valor a qué
valor, y en qué tabla escribió. Eso es lo que hace confiable a algo que escribe
en la base.

```
REGISTRO = una línea de tiempo de EVENTOS, paginada, del backend
```

Un evento es **una acción del agente**: qué se aplicó, sobre qué sujeto, qué
campo se movió, de qué a qué, en qué tabla, quién lo apretó, y **la regla que la
motivó**.

#### Los tres defectos de hoy, y qué los arregla

**1. Tres topes distintos mezclados en una línea de tiempo.**
Hoy REGISTRO se arma en el navegador juntando acciones (tope 100), respuestas a
preguntas (40) y votos (80). Cuando el más chico se agota, **la línea de tiempo
pierde un tipo de evento y no los otros, sin decirlo**: un día aparece como que
"solo tuvo acciones" cuando en realidad hubo respuestas que se cayeron del tope.
→ **Una sola fuente, un solo tope, paginado.**

**2. Se arma en el navegador.** No se puede paginar ni buscar hacia atrás: el
buscador solo mira lo que ya bajó. Un libro de auditoría que no llega más allá
de las últimas 100 líneas no es un libro.
→ **La query es del backend y el filtro también.**

**3. La columna HOY —la única que contesta «¿quedó arreglado?»— miente por
omisión**, y el código lo admite: *«la mayoría de las acciones no guardan la
regla que las motivó»*, así que no puede decir cuál problema arregló y muestra
todos los del sujeto.
→ **Cada acción guarda `habilidad + sujeto + regla`**, el mismo trío del
hallazgo (§1.1). Es la razón por la que ese trío existe.

#### Lo VIVO se va de HISTORIAL

Hoy arriba del registro conviven dos bloques que no son historial:

- **CONTESTADAS SIN EJECUTAR** — respuestas guardadas que el agente todavía no
  sabe ejecutar. **Eso es trabajo pendiente** → su casa es ENCONTRÓ.
- **NO TE INTERESAN** — los descartados. **Eso es un filtro**, no un pasado →
  vive en el filtro de ENCONTRÓ, con su deshacer.

#### COMUNICACIONES — se conserva tal cual

Lo que el agente mandó **HOY** y a quién. **No acumula**: si el destinatario no
lo atendió, sigue abierto en SU bandeja, no acá. Está bien pensada y no se toca.

### 6.7 Lo que se BORRA del modal

| Pantalla | Por qué se va |
|---|---|
| **VIGILANCIA** | Es un **segundo depósito** de los mismos problemas, con otro reloj y otra tabla. La propia pantalla se lo explica al usuario: *«es OTRA fuente que LA LISTA (…) por eso los números no coinciden ni tienen por qué»*. No es una pantalla: es la cicatriz de tener dos relojes. Con una sola tabla, no existe. Su «92 sin ver» es un contador que **nunca baja**. |
| **¿AGUANTAN?** | Tablero de vigilancia de arreglos que todavía no volvieron. Su número suma dos cosas que no se tocan (causas en prueba + hallazgos atendidos hoy) y descansa sobre un `resuelto` que significa dos cosas. Lo único que había que saber —**si algo volvió**— lo dice `reincidencias` (§1.3), que está vacía cuando todo va bien. |
| **Votos ✔/✖ y el eval set** | *«No entra nada de votos y eso: todo eso generó demasiada complejidad en algo que no funcionaba»* — user. Se va **entero**: los botones, la tabla de evals, `ya_votados`, `es_ruido`, la confianza, los hitos 1·2·3·7·14·30 y la escalera de autonomía que colgaba de ahí. |

**Lo que se pierde con eso, dicho de frente:** el agente deja de tener una
métrica de qué tan bien diagnostica. Se acepta. Un número que nadie usa y que
cuesta tres tablas y seis botones no es una métrica — es lastre. Si algún día
hace falta, se reconstruye de `reincidencias`, que es un hecho y no una opinión.

---

### 6.8 La CADUCIDAD — cuando el hallazgo deja de aplicar (2026-09-04)

Un hallazgo es **una afirmación sobre el mundo en un momento**: «M31G6 falta en
el master». Debajo hay una premisa que nadie escribió: «M31G6 debería estar en
el master». Cuando el bono vence, la premisa se vuelve falsa: el hallazgo no
está resuelto ni sin resolver — **dejó de aplicar**.

Hasta acá no había forma de decir eso. Un hallazgo moría de dos formas —el
detector no lo vio más, o una persona apretó «no me interesa»— y la segunda es
**falsa** (nadie está desinteresado) y además pierde el motivo para siempre, con
lo cual el sistema quedaba igual de tonto para el próximo bono que venciera.

#### Es un CIERRE, no un sexto estado

| | |
|---|---|
| Estado | sigue siendo `resuelto` — que es verdad: ya no está |
| `cerrado_como` | `caducidad`, junto a `accion` y `ausencia` |
| Pantallas | **ninguna suma una rama**: todas filtran por `ABIERTOS` |
| ¿Reincide? | **no**, y ese es el punto |

Un sexto estado habría costado una rama en cada pantalla para siempre. El eje
`CIERRES` ya existía justamente para registrar CÓMO se cerró, y la caducidad
hereda de ahí la única regla que importa.

**Le gana a la ACCIÓN, no sólo a la ausencia.** Si el arreglo se aplicó y
DESPUÉS el sujeto se murió, el cierre no se le puede adjudicar a la acción; y
sobre todo, cerrar por acción lo habilita a REINCIDIR. Así nació la primera fila
de `agente.reincidencias`: alta de M31G6 el 24/08 → `cleanup_curvas` lo borró
del master porque vencía → el detector lo vio faltar de nuevo el 28/08 →
reincidencia. **El arreglo no había fallado: dos subsistemas estaban peleando.**
Nada se pierde: `arreglo_aplicado` sigue en la fila y el libro tiene su línea.

#### La regla que hace que esto no sea peligroso

> ⚠️⚠️ **NO ENCONTRAR EL SUJETO NO ES PRUEBA DE QUE NO EXISTA.**

Para afirmar que algo murió hace falta una **partida de defunción**: una fuente
que lo diga, con fecha. Que el ticker no aparezca en ninguna tabla es *no sé*, y
no sé **no cierra nada**. Es el invariante 1 un nivel más abajo, y sin él esto
sería el peor bug posible en una herramienta de integridad: el día que una
fuente devuelva vacío, el agente caduca todo lo abierto de golpe y deja el
tablero en verde justo cuando está ciego.

Por eso `vigencia.Veredicto.existe` es de **TRES valores** (`True` / `False` /
`None`) y se pregunta con `.muerto`: `not v.existe` daría `True` para «no sé» y
convertiría la guarda en su contrario.

#### Y «uno dice muerto, otro dice vivo» TAMPOCO es muerte (2026-09-04)

La primera versión se cuidaba de que «no lo encontré» no fuera muerte, y sin
embargo tomaba el primer «está muerto» que encontraba **sin mirar si otra fuente
afirmaba lo contrario**. Es el mismo error un nivel más arriba, y lo destapó la
primera corrida real:

| Fuente | Dice | Afirma |
|---|---|---|
| `mercado.curvas.fecha_vencimiento` | 2028-01-28 | está **VIVO** |
| `portafolio.assets` (`vigente=false`, venc. 2026-06-28) | vencido | está **MUERTO** |

**GMCGO**: las dos copias cargadas, diecinueve meses de diferencia, nadie
arbitrando. El código saltaba la primera rama (2028 no es pasado), entraba por la
segunda, y daba por muerto un título que el master declara vivo.

**Una fecha de vencimiento FUTURA es una afirmación tan válida como la que dice
que murió.** Ahora se juntan las afirmaciones de las dos direcciones y recién
después se concluye: alcanza con que UNA firme la defunción **sólo mientras
ninguna afirme lo contrario**. Si se contradicen es «no sé», no se cierra nada, y
la divergencia se logea — porque el problema es la divergencia, no la caducidad
que no ocurrió. Lo congela `test_dos_fuentes_que_se_contradicen_no_caducan_nada`.

⚠️ Y la divergencia sigue abierta como problema de DATOS: cuál de las dos fechas
manda no lo puede decidir el código. Se declara en `core/duplicados.DUPLICADOS`,
que es el lugar que la REGLA #9 tiene para esto. Los dos jobs, mientras tanto,
actúan cada uno con su copia sin enterarse: `validar_instrumentos` se conforma
con cualquiera de las dos y apagó el asset; `cleanup_curvas` usa sólo la del
master, que dice 2028, y no lo va a soltar.

#### Las cinco guardas

1. **No borra nada, nunca.** Cambia un estado y escribe en el libro.
2. **Sólo caduca con un «no existe» afirmativo y con fuente.** Si la
   verificación falla, `vigencia.muertos` devuelve **ninguno** (va en un
   SAVEPOINT: no puede tirar abajo la escritura de la corrida).
3. **Deja el fundamento Y la fuente**: *«caducó: venció el 2026-08-26 ·
   `mercado.curvas.fecha_vencimiento`»*. «Caducó» a secas no es trazabilidad.
4. **Es reversible.** Si el sujeto vuelve a existir, el detector lo ve y nace un
   hallazgo nuevo.
5. **Tope de 10 por corrida.** Caducar 40 de una es más probablemente una fuente
   rota que 40 bonos venciendo el mismo día. Los que sobran cierran por ausencia
   —que dice menos pero no miente— y queda en el log.

#### Cómo se declara: una palabra en la fila del catálogo

```python
Habilidad(nombre="soberanos_faltantes", ..., sujeto_es="bono", ...)
```

El motor **no sabe qué es un bono**: lee `sujeto_es` y le pregunta al
verificador de ese tipo (`agente/vigencia.VERIFICADORES`). Vacío es una
declaración explícita —«mi sujeto no es una cosa que pueda dejar de existir»— y
es el default seguro: sin declaración, no se caduca nada.

⚠️ **Hoy hay UN tipo: `bono`** (6 habilidades). No es recorte de ambición, es la
regla de arriba: es el único cuyo certificado de defunción existe y es
verificable — `fecha_vencimiento` en `mercado.curvas` **y** en
`research.mkt_1816_instrumentos` (la que resuelve el caso de un bono que
reportamos *porque no está en nuestro master*: la única fuente que sabe de él es
la que lo nombró), más la marca `portafolio.assets.vigente` que escribe
`jobs.validar_instrumentos`. `job`, `tabla`, `endpoint` y `motor` son candidatos
reales y **ninguno entra hasta tener una fuente que afirme la baja**: un
verificador que caduque «porque no lo encontré» es exactamente lo que este
mecanismo existe para impedir.

Sumar un tipo = una función en `agente/vigencia.py` + una palabra en
`agente/tipos.SUJETOS`. Un test exige que los dos digan lo mismo: un tipo
declarado sin verificador no caducaría nada, callado.

---

### 6.9 EL TRIAGE — a qué vale la pena ir a investigar (2026-09-04)

*Triage* es lo de la guardia de un hospital: cuando entran diez juntos y no se
puede atender a todos, **alguien decide a quién primero**. No es curar, es
elegir.

Hace falta porque **investigar cuesta**: entre 8 y 18 llamadas al modelo, uno o
dos minutos y plata por caso, contra ~40 hallazgos abiertos en cualquier momento.
Investigarlos todos sería pagar cuarenta veces para encontrar dos.

#### La regla, y la dijo el user

> *«el agente tranquilamente puede ver 5 minutos después si eso ya funciona y
> listo — esto tiene que ser cuando se termina de caer del todo»*

**No se investiga lo que se acaba de caer: se investiga lo que SIGUE caído.**

`proveedor_caido` corre cada 5 minutos y le alcanza UN fallo para cantar. El
04/09 Aunesa se cayó 12:35, el hallazgo nació 12:41, y a la tarde ya no existía:
se había recuperado solo. Disparar en el momento del hallazgo habría pagado una
investigación entera de algo que se arregló sin que nadie hiciera nada.

Y no hace falta un reloj nuevo para medirlo: **el detector ya vuelve a mirar
solo**, y si el problema se fue, el hallazgo se cierra. Lo que sobrevive es lo
real.

#### Cómo se declara: una fila, dos datos

```python
Habilidad(
    nombre="proveedor_caido", ...,
    investigar={"no_responde": 20 * _M},   # regla → cuánto tiene que AGUANTAR
)
```

Los 20 minutos salen del propio detector: corre cada 5', así que un hallazgo vivo
a los 20' lo vieron **cuatro pasadas seguidas**. Eso ya no es un parpadeo.

Vacío es el default y es una declaración: esa habilidad no dispara nada. El
permiso se da **caso por caso** — *«no con todo, con casos que vayamos
eligiendo»*. Hoy hay UNO.

⚠️ **El TIPO de investigación no se declara acá.** Ya vive en
`lab.langgraph.investigaciones.DE_LA_HABILIDAD`, que es quien sabe qué sabe
investigar. Repetirlo sería la REGLA #9: dos mapas del mismo hecho sin árbitro, y
el que se desincronice no falla — manda a investigar con el método equivocado.

#### Tres piezas, y ninguna conoce a las otras dos

| Pieza | Qué hace | Qué NO sabe |
|---|---|---|
| `agente/triage.py` | **elige** los que sobrevivieron su espera | que existe un investigador |
| `lab/langgraph/` | **investiga** | que existe un agente |
| `jobs/agente.py` | los junta y encola | — |

Esa separación es la garantía que no se negocia: **si el laboratorio no está
instalado, el agente detecta exactamente igual.** Un test prohíbe que `agente/`
importe `lab/`, y en el daemon el import va adentro del `try`.

#### Las guardas

| | |
|---|---|
| **Sobrevivir** | el hallazgo tiene que seguir abierto después de su espera |
| **Tope diario** | `TOPE_DIARIO = 6`. Es un techo de PLATA, declarado y no en un `while` |
| **No repetir** | 24 h por caso: el problema puede seguir abierto una semana, la respuesta a «por qué pasó» no cambia todos los días |
| **Si no puedo contar, no gasto** | `gastadas_hoy()` devuelve `None` si no pudo leer → no se dispara. Un tope que no se puede contar no es un tope |

Y `lab.pedidos.hallazgo_id` guarda **qué problema lo disparó**, para poder mostrar
el veredicto al lado del hallazgo: uno que vive en otra tab no lo lee nadie.

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
   La CADUCIDAD (§6.8) le gana a las dos: un sujeto que dejó de existir no
   reincide, porque «volver» no significa nada sobre algo que no está.
5. **Ninguna otra tabla guarda estado de problemas.** Tres, y nada más.
6. **La respuesta a "no pude mirar" está escrita una sola vez**, en el agente,
   no en cada habilidad.
7. **Cada habilidad declara su arreglo, o declara que no tiene.**
8. **Nada del agente vive fuera del agente.** Ningún detector adentro de un job
   ajeno.
9. **A ENCONTRÓ solo entra lo que tiene arreglo** (§6.2). Un botón que vuelve a
   mirar no es un arreglo.
10. **Un arreglo ESCRIBE.** Si después de apretarlo el sistema quedó igual, no
    era un arreglo.
11. **Ninguna pantalla deriva nada.** Clase, estado, arreglo y nombre vienen
    resueltos del backend — ningún contador se suma en el navegador.
12. **No encontrar el sujeto NO es prueba de que no exista** (§6.8). Para
    caducar hace falta una fuente que AFIRME la baja, con fecha. «No lo
    encontré» es «no sé», y no sé no cierra nada — es el invariante 1 un nivel
    más abajo.
13. **El agente no se autoevalúa** (§9.1). Nada de votos, puntajes ni
    confianza acumulada. Lo único que se registra sobre su desempeño es un
    hecho: si algo que dio por arreglado volvió.

---

## L. EL INVESTIGADOR — el agente que SÍ decide (`lab/langgraph/`)

> **Por qué existe esta sección.** El investigador entró a producción, corre
> adentro del daemon del agente, lee producción, gasta tokens todos los días y
> tiene dos roles propios de Postgres — y durante días **ningún doc de `docs/`
> lo nombraba**. Cero menciones. Su único manual era el README de su carpeta,
> que además describía la versión anterior. Por la REGLA #10 (nada nuevo queda
> suelto) eso era trabajo a medio terminar: corre adentro de este agente, así
> que se documenta en el doc de este agente.

### L.1 Qué es, y en qué se diferencia del agente

El AV AGENT **detecta**: un catálogo de reglas deterministas, un ritmo
declarado, y un motor que pregunta a quién le toca. Nadie elige nada — su
`motor._agenda()` es un `sorted()`.

El INVESTIGADOR **averigua por qué**: se le da un caso y **el modelo decide solo
qué herramientas usar** hasta poder concluir. Es un agente en el sentido
estricto (loop + tools elegidas + memoria), construido sobre LangGraph.

| | `agente/` | `lab/langgraph/` |
|---|---|---|
| Qué hace | detecta y verifica | investiga y propone |
| Cómo decide | catálogo + ritmo declarado | el modelo elige |
| Escribe en producción | sí, por `registro.py`, con libro | **no — la base se lo impide** |
| Quién lo califica | el detector, en la próxima pasada | `scripts/eval_investigador.py` |

**La línea que no se cruza:** un LLM nunca puede ser la pieza que devuelve `ok`
con lista vacía. Si el modelo no contestó es `sin_datos`, y entonces no se
cierra nada — el invariante #1, intacto.

**Y el #12 tampoco se toca:** el eval NO es un LLM-juez. Compara, de forma
determinista, lo que el veredicto propuso contra el `arreglo_aplicado` que
cerró ese hallazgo. La verdad de campo la escribió el agente determinista, no
el modelo opinando de sí mismo.

### L.2 Dónde corre

En un **hilo aparte del daemon `agente.service`**, después del `tick()`. No en
la pasada: una investigación tarda uno o dos minutos y la pasada es de treinta
segundos — colgarla ahí apagaría el monitoreo justo cuando alguien está mirando
un problema. **Uno por vez**: dos en paralelo duplican el gasto sin que nadie
las haya pedido a la vez.

Nada del investigador puede tirar abajo al agente: el import va adentro de un
`try` (`jobs/agente.py::_atender_investigaciones`) y hay un test que lo congela.

La pantalla no espera: pide, recibe un número y pregunta cómo va — el proxy de
Next corta a los 30 s y ese corte se ve **idéntico** a un backend caído.

### L.3 La jaula — dos identidades, y el alcance vive en el repo

    lector_lab    lee seis tablas de producción y el esquema `lab`.
                  **La base NO lo deja escribir.** En ningún lado.
    escritor_lab  escribe SÓLO en `lab.*`. Producción le es INVISIBLE.

Ninguna cae a `POSTGRES_URI` — esa es la del sistema y puede todo; un fallback
silencioso convertiría la garantía en una intención.

⚠️ **El alcance se declara en `sql/lab.sql`, y ese archivo REVOCA antes de
otorgar.** No describe lo que el lab puede leer: lo impone. Antes de existir,
los `GRANT` sobre producción se habían dado a mano en el editor de Supabase y no
estaban escritos en ninguna parte del repo — no se podían auditar leyendo el
proyecto, no se reproducían en una base nueva, y no tenían techo: un
`GRANT ... ON ALL TABLES` otorgado un martes apurado pasaba todos los chequeos
en verde para siempre.

`probar_lector` verifica **las dos mitades**: que pueda leer lo declarado, y que
no pueda leer nada más (le pregunta al catálogo, no adivina).

**Lo único a mano, una vez:** crear los dos roles. `CREATE ROLE` lleva
contraseña y una contraseña no va a un repo. Desde ahí sus permisos los manda
`sql/lab.sql`, que aplica `deploy/deploy.sh` en cada deploy — y sus fallos
**avisan sin cortar**: el lab es opcional, la mesa no.

### L.4 Las dos piezas que no trae ningún framework

**`revisar_piso` — el método.** Cuando el modelo deja de pedir herramientas NO
concluye: se verifica que haya mirado el mínimo que su tipo de caso declara
(`investigaciones.py`), y si falta algo vuelve **con el faltante nombrado**. Se
mide contra las herramientas que se ejecutaron de verdad, **no contra lo que el
modelo dice que miró**. Es el invariante #1 aplicado a una investigación.

**`anotar` — la memoria de trabajo.** Una línea por herramienta ejecutada, que
viaja aparte de la conversación. Sin ella el modelo repetía la misma búsqueda
con el patrón apenas cambiado: cinco veces en una sola corrida. Y la lista sola
no alcanzó — **el nodo `herramientas` DEDUPLICA de verdad** y le devuelve el
resultado que ya tenía. Pedir por favor no obliga; es la misma diferencia que
entre «confío en que no va a escribir» y «la base no lo deja».

### L.5 Lo que falta, en orden

1. **Checkpointer en Postgres.** Hoy es `InMemorySaver`: el progreso vive en la
   RAM del proceso y un reinicio del daemon lo borra (`recuperar_colgados()` lo
   cierra con el motivo y **no lo reencola** — un reintento automático es cómo
   se hace un bucle que gasta tokens toda la noche).
2. **`interrupt` antes de escribir** — el `PROPONER → OK → APLICAR` que el modal
   ya hace a mano, formalizado en el grafo. ⚠️ **Va DESPUÉS del punto 1, no
   antes**: una aprobación humana puede tardar horas o quedar de un día para el
   otro, y con la memoria en RAM cualquier reinicio en el medio de esa espera
   pierde la investigación.
3. **Los 8 arreglos de `agente/arreglos.py` como tools.** Ya tienen `preview()`,
   allowlist y libro de auditoría: son tools de producción esperando un selector.

Detalle de implementación: `lab/langgraph/README.md`.

---

---

# PARTE B — EL DIARIO ⟨HISTÓRICO⟩

> **Nada de esta parte describe el código actual.** El agente se rehizo entero el
> 2026-08-24: los 34 services `av_agent_*`, `core/ciclo.py`, las 17 tablas viejas,
> el eval set, los votos y las siete tabs **no existen**. Cada `§0.x` se conserva
> porque es un bug real con su decisión.
>
> ## La regla que impide que esto vuelva a crecer
>
> **Una entrada vive acá si y solo si el CÓDIGO la cita por número.** Nada de
> «me parece que sirve»: si ningún `.py`, `.sql`, la CI o el `CLAUDE.md` dice
> `§0.x`, la entrada se va. Lo congela `tests/unit/test_doc_agente.py`, que falla
> en las dos direcciones — una cita a una entrada que no está, y una entrada que
> no cita nadie.
>
> El diario tenía **120 entradas y 611 kB**. Quedan **26**. Las 13
> podadas están en git (`git log -- docs/AV_AGENT.md`).

### 0.j LO QUE EL AGENTE SABE HACER — las ACCIONES (2026-08-19)

Pedido del user, con los ejemplos puestos por él: *«ya sabés exacto qué campo de
qué función hay que modificar… que el mismo agent aprenda a sugerir y que, si le
das OK, actualice en el momento y luego controle que lo hizo bien en el mismo
proceso. Ej.: OTC TRI DLR son siempre derivados; el X29E7 tiene la palabra CER en
el nombre… que ya tenga la feature armada pero que se haga con mi permiso, o que
yo escriba lo que tiene que hacer y él lo haga»*.

Hasta acá el agente **diagnosticaba y mandaba a otra pantalla**. Con 68 casos eso
no es ayuda: es una lista de tareas con un link al lado. La diferencia entre
detectar y resolver es esta capa.

**EL CICLO, Y ES UNO SOLO PARA TODO:**

    PROPONER  →  (el humano da OK)  →  APLICAR  →  VERIFICAR

Es el MISMO ciclo del alta de un bono (simular → aplicar → verificar). Que sea
uno solo no es prolijidad: el que aprueba un cambio de cartera no tiene que
aprender otro modelo mental que el que aprueba un alta, y el día que se le dé
autonomía a una acción se le da con el mismo interruptor.

**Las cuatro decisiones que sostienen el diseño:**

1. **La regla determinista SIEMPRE primero; el modelo solo para lo que quedó
   afuera.** El 80% de estos casos los resuelve una regla de tres líneas
   (`OTC`/`TRI.`/`DLR`+fecha → DERIVADOS, `CER` → ARS, código CAFCI → FCI), y
   gastar tokens *y atención humana* en eso es tirar los dos recursos que
   escasean. Cada propuesta viaja con su `fuente` (`regla` ∣ `ia`) porque
   **cambia cuánto hay que mirarla**: una regla se audita leyendo el código una
   vez; una sugerencia del modelo hay que mirarla caso por caso.

2. **El modelo elige de una LISTA CERRADA, nunca escribe libre.** «Derivados» es
   plausible y está mal escrito, y eso rompe los filtros que comparan exacto —el
   divisor del AuM, Tenencia Valorizada— **sin dar ningún error**. Lo que no está
   en la lista se descarta antes de llegar a la base, y el modelo tampoco puede
   contestar sobre un sujeto que nadie le pasó.

3. **La escritura va por la MISMA puerta que usa la pantalla**
   (`assets_sql.set_campos`, `contrapartes_seg.add_contraparte`). Un segundo
   camino de escritura termina con dos criterios distintos para el mismo dato:
   es así como se llega a que la mitad de las carteras tengan un espacio al
   final. Por eso `_write_sql` se **movió del router al service** en este mismo
   cambio — un service importando de un router era la señal de que la puerta
   estaba en el lugar equivocado.

4. **Aplicar y verificar son UN paso, y `verificado` decide el estado.** Después
   de escribir se **relee de la base**; si la lectura no confirma, la propuesta
   queda **`fallida`**, jamás `aplicada`. Marcar hecho algo que no se puede
   comprobar es exactamente cómo un tablero termina en verde con el dato roto —
   el incidente que dio origen a SALUD. Y cada propuesta se aplica **sola**: una
   que falla no arrastra a las otras, porque aprobar diez y que se caigan las
   diez por la séptima es la forma más rápida de que nadie vuelva a apretar el
   botón.

**Proponer NO trabaja sobre la foto del cron: vuelve a correr el control.**
*«Todo lo que figura en encontró tiene que ser porque realmente está y sigue
pasando»* (user). Sugerir un arreglo para un caso que ya se resolvió es el
cementerio de avisos viejos que este rediseño vino a eliminar.

**El humano puede CORREGIR el valor antes de aplicar.** Sin eso, ante una
sugerencia casi buena solo queda descartarla e ir a Manager a mano —todo el
trabajo del agente a la basura por una letra— y las propuestas que nacen sin
valor (el ping: a quién avisarle) serían inaplicables.

**Rechazar también se registra.** Una propuesta descartada mide tanto como una
aplicada: es lo que dice que el agente sugirió algo que un humano no compró. Sin
eso parecería tener 100% de acierto para siempre. **Cada propuesta con su
resultado ES el eval set de las acciones** (§0.f), y el `acierto` se calcula
sobre lo DECIDIDO —no sobre lo pendiente, que todavía no dijo nada—; sin nada
decidido vale `None` y no `0`, porque «cero acierto» y «todavía no sabemos» son
cosas distintas y la primera frenaría la autonomía por una medición que nunca se
hizo.

**Las cuatro acciones de arranque** (`api/services/av_agent_hacer.py`):

| acción | control | qué hace | escribe por |
|---|---|---|---|
| `assets.cartera` | `assets_sin_cartera` | la CARTERA por patrón del nombre; lo que la regla no sabe va al modelo con la lista de 8 carteras | `assets_sql.set_campos` |
| `assets.fci` | `fci_incompletos` | copia el EMISOR de **otra clase del mismo fondo** (mismo código CAFCI) | `assets_sql.set_campos` |
| `contrapartes.alta` | `contrapartes_pendientes` | da de alta la contraparte **que el conciliador ya sugiere** | `contrapartes_seg.add_contraparte` |
| `avisar.responsable` | `comitentes_sin_nivel1` | **le avisa a una persona** de la plataforma | `av_agent_vista.avisar_a` |

- **`assets.fci` NO adivina: copia de un hermano.** El código CAFCI es la
  identidad del fondo y la clase es la variante, así que copiar no es una
  inferencia, es un hecho (la misma idea de la regla `herencia` de
  `assets_autofill`). **Si dos hermanos no coinciden, no se propone**: dos
  emisores distintos para el mismo CAFCI es un dato ROTO, no una ambigüedad que
  se resuelva eligiendo uno.
- **`contrapartes.alta` no parsea el texto del control.** El detalle guardado es
  de la última corrida del cron; se vuelve a llamar a `reconciliar()` —la MISMA
  función que usa el control y el botón de Manager— y se propone sobre lo que
  dice HOY. Parsear una frase habría atado el alta al formato de un string.
- **`avisar.responsable` NO crea una tabla de notificaciones nueva.** Ya existe
  la lista de pendientes del agente (`agente.av_agent_avisos`): tiene alta,
  cierre por una persona y pantalla. Solo le faltaba **a quién** → columna
  `para` (NULL = de todos, que es como venía funcionando). Un segundo buzón daría
  dos lugares donde mirar lo que hay para hacer — el problema exacto que SALUD
  vino a resolver cuando la observabilidad estaba en seis pantallas. Es **UN
  aviso por control y no uno por caso**: 200 pings de «esta cuenta no tiene
  nivel_1» no son 200 avisos, son un aviso ignorado. Y el destinatario **lo
  elige el humano**: a quién le toca un tema interno no es algo que el nombre del
  caso pueda decir.

**Agregar una acción nueva es una clase con tres métodos y una línea en
`ACCIONES`.** Ni endpoint, ni tabla, ni UI: la acción es un *parámetro* de los
tres endpoints, y `POR_CONTROL` se deriva del registro, así que la lente de SALUD
la ofrece sola. Eso es lo que pidió el user cuando dijo *«la arquitectura tiene
que ser escalable porque va a ser un montón de cosas»*.

**El interruptor del modelo vive en `_proponer_con_ia` y no adentro de cada
acción** (un `ContextVar`, para que dos requests en paralelo no se pisen). Si
cada acción tuviera que acordarse de mirar el flag, la que se olvide gasta tokens
con el modelo apagado y nadie se entera hasta ver la factura.

**Dónde se ve.** Es una lente más del diagnóstico de SALUD —«Esto lo sé hacer · N
casos»— y **no se dibuja si el control no tiene acción**: un renglón que dice
«para esto todavía no sé hacer nada» aparece nueve veces y entierra las dos que
sí. La lente **declara la capacidad, no propone**: proponer cuesta (una corrida
del control, a veces una llamada al modelo) y no tiene sentido pagarlo cada vez
que alguien abre un diagnóstico a mirar.

- Tablas: `agente.av_agent_propuestas` (UNIQUE `accion+sujeto+campo` → re-proponer
  ACTUALIZA en vez de acumular diez para el mismo asset) y la columna `para` de
  `agente.av_agent_avisos`.
- Endpoints (admin-only, `api/routers/ia.py`): `GET /av-agent/hacer`,
  `POST /av-agent/hacer/proponer`, `/aplicar`, `/rechazar`, y
  `GET /av-agent/mis-avisos` (SIN `require_admin` a propósito: el destinatario de
  un ping no necesariamente administra nada, y filtra por su propio email — no
  hay forma de pedir los de otro).
- Tarea de IA: `av_agent_accion` (tier pro, thinking **disabled** — no es
  análisis de patrones, es clasificar contra una lista cerrada).
- 31 tests en `tests/unit/test_av_agent_hacer.py`.

**Lo que queda pendiente de esta capa**: deshacer (el `antes` ya se guarda, falta
el botón), instrucción en texto libre (*«que yo escriba lo que tiene que hacer y
él lo haga»*), y la lane automática por acción cuando el acierto medido lo
habilite — nunca antes, y siempre prendida a mano.

---

---

### 0.k EL COPILOTO SE DIO DE BAJA — y qué dejó (2026-08-19)

**Decisión del user:** *«desactivar el CONSULTALE A LA IA de todas las vistas,
eliminarlo del backend y toda documentación de la misma… este proyecto sirvió
como inicial pero no cumplió con la necesidad, por lo que de momento se elimina
dando paso a AV AGENT, que va a ser una versión superior»*.

**Qué era.** Un botón ✧ CONSULTALE A LA IA en cada vista de mercado (home, renta
fija, renta variable, agro, derivados, trading, research, reuters) más el
ASISTENTE DE NEGOCIO para los jefes y el GUÍA de la plataforma. Le pasabas los
datos de la vista y contestaba preguntas sobre ellos. También el VIGÍA de
/trading (toasts cuando una tarjeta tocaba un nivel) y la NAVEGACIÓN ASISTIDA
(el guía te dejaba los filtros puestos).

**Por qué no alcanzó, y esto es lo que importa que quede escrito:**

Un copiloto **contesta lo que le preguntás**. Eso tiene dos techos que no se
arreglan con mejores prompts:

1. **Requiere que ya sepas qué preguntar.** El backfill de tenencias falló dos
   días y nadie se enteró — nadie iba a preguntarle al copiloto «¿corrió el
   backfill?», porque el problema es justamente que no sabías que había un
   problema. El valor no estaba en responder: estaba en **avisar**.
2. **No deja nada.** Cada conversación empezaba de cero y terminaba en la
   pantalla. No producía un cambio en el sistema, ni un registro de si acertó, ni
   una decisión que alguien pudiera aprobar. Se gastaban tokens en producir
   texto, y el texto se cerraba con la pestaña.

El AV AGENT invierte las dos cosas: **empieza él** (detecta, releva, vigila) y
**termina en un cambio verificado** (propone → das OK → escribe → relee). Por eso
no es «el copiloto mejorado»: es otra forma de usar el modelo, donde el LLM ocupa
el lugar chico —desambiguar, clasificar, leer patrones— y el grueso lo hacen
funciones deterministas que se pueden auditar leyendo el código una vez.

**Lo que se rescata y sigue vivo** (no todo fue a la basura):

- **El gateway `core/ai.py`** — registro de tareas, presupuestos, trazas,
  ruteo por proveedor y la invariante de privacidad. Lo usa el agente entero.
- **Las lecciones de tokens**, pagadas ahí: el razonamiento cuenta como output
  (un `max_tokens` corto devuelve respuesta VACÍA), y `thinking` va apagado
  cuando la tarea es clasificar y no razonar.
- **`ia.trazas`** — toda llamada al modelo queda registrada con tokens, latencia
  y resultado. Es lo que permitió medir esto en vez de opinarlo.
- **La postura de privacidad**: una tarea que ve datos del negocio no puede
  correr en un proveedor que entrena con ellos, y el gateway **se niega** en vez
  de confiar en un string.

**Lo que se eliminó junto con él** (regla nueva del user: *«si lo usa AV Agent
perfecto, si no se elimina»* — nada de IA corriendo por atrás sin lector):

| qué | por qué se fue |
|---|---|
| `copiloto_vista` · `copiloto_vista_pro` · `asistente_negocio` | eran el copiloto |
| `critico_calidad` (`jobs/ia_calidad`, 21:30 diario) | evaluaba conversaciones del copiloto: sin copiloto, sin objeto |
| `triage_incidente` (`jobs/triage`, **cada 10 minutos**) | escribía en `ia.triage_incidentes` y **NADIE la leía** — ni un endpoint ni una pantalla |
| `controles_resumen` (1 llamada/día) | su texto terminaba en un `print()` del log del job |
| `salud_diagnostico` | lo generaba el panel de SALUD; sin panel nadie lo genera |
| `core/pii_gateway.py` (la aduana PII) | existía solo para el asistente de negocio |
| el VIGÍA de /trading y la NAVEGACIÓN ASISTIDA | colgaban de endpoints del copiloto |

De **11 tareas de IA registradas quedan 4**: `av_agent_informe`,
`av_agent_accion`, `research_destilar` (ingesta del mail de 1816, se lee todos
los días en pantalla) y `smoke`.

⚠️ **La regla que queda, y que hay que aplicar antes de sumar una tarea nueva:
¿QUIÉN MIRA SU SALIDA?** Si la respuesta es «queda en una tabla», la tarea no va.
`triage_incidente` corrió cada diez minutos durante semanas contra una tabla que
nadie abrió nunca, y no se notó porque **funcionaba**: no fallaba, no daba error,
solo gastaba. Eso es más difícil de detectar que un bug.

**Las tablas NO se dropearon** (`ia.calidad_flags`, `ia.triage_incidentes`,
`manager.asistente_chats`, `manager.asistente_mappings`,
`manager.salud_diagnosticos`): borrar código es reversible con un `git revert`,
borrar datos no. Quedan huérfanas y se limpian cuando el user lo decida.

---

---

### 0.q EL AGENTE ENTIENDE LA BASE Y LA LATENCIA (2026-08-19)

#### LA BASE DE DATOS — la información es el DELTA, no el tamaño

Pedido del user: *«que entienda qué tablas hay, cuánto pesa cada una (al menos
una vez por día) y sepa distinguir al día siguiente si se agregó algo nuevo, y
cuáles aumentaron su tamaño y por cuánto… esto tendrá que persistir para tener
contexto, pero a su vez no crecer todo el tiempo: con que tenga registro de hoy y
ayer constantemente alcanza»*.

`pg_total_relation_size` ya dice cuánto pesa cada tabla, y mirarlo una vez no
sirve de nada: son doscientos números sin escala. **Lo que informa es el
cambio**, y son tres cosas:

- una tabla **NUEVA** → alguien creó algo, o un job está escribiendo donde no
  debería. Es lo que uno se entera último y lo que explica el resto;
- una tabla que **CRECIÓ de golpe** → un job en loop, un backfill que se fue de
  mano, una tabla sin purga. Eso es lo que se come el plan;
- una tabla que **DESAPARECIÓ** → alguien dropeó algo.

Por eso hay **foto diaria** (`jobs/db_tamano.py`, 23:30 UTC) y no una consulta en
vivo: sin el de ayer no hay comparación.

**Dos fechas y nada más**, con la purga **en el mismo INSERT** (mismo patrón que
`tesoreria_snapshots`). No depende de que alguien se acuerde de correr una
limpieza: una tabla que vigila el tamaño de la base y crece sin techo es un
chiste que se cuenta solo. Son ~200 filas × 2 días.

**El corte se AUTO-CALIBRA.** ⚠️ No tengo acceso a prod, así que cualquier umbral
en MB que ponga a mano es una adivinanza (REGLA #2). Entonces no se pone: un
crecimiento entra si supera **los dos** filtros —**+25%** sobre su propio tamaño
**y** el máximo entre **10 MB y el 0,5% de la base**—. Así el aviso significa lo
mismo con 500 MB que con 50 GB, y nadie recalibra nada cuando la base crezca. Un
filtro solo no alcanza: el relativo dispara con tablas de 8 KB (crecer 50% son
4 KB) y el absoluto solo, con tablas grandes que crecen lo normal todos los días.

**La primera foto NO reporta 200 tablas nuevas.** Sin foto previa todo parecería
nuevo, y arrancar con 200 falsos positivos es la forma más rápida de que nadie
vuelva a mirar esto. Dice «es la primera, mañana te cuento» y listo.

Tablas: `manager.db_tamano` (por tabla) y `manager.db_tamano_dia` (el total de la
base, que **no** es la suma de las tablas — incluye catálogo, TOAST y espacio
libre; se guardan aparte para que las dos cifras no se puedan confundir).

#### LA LATENCIA — por qué la pantalla no servía, y qué la reemplaza

*«La verdad tengo eso en observabilidad, jamás lo usé, me está usando espacio
innecesario… ni siquiera se actualiza, puede haber cosas nuevas y no se entera.
Me interesa que el agent pueda detectar en tiempo real endpoints que estén
lentos, pero tiene que ser algo fiable y verdadero, no tirar por tirar»* (user).

**El diagnóstico es exacto y es el error de diseño que se corrige: un ranking
muestra lo LENTO, no lo ANORMAL.** Arriba de esa tabla estaban:

    /api/manager/salud/diagnostico   11.724 ms   →  es una llamada al LLM
    /api/back-office/tesoreria/dia      726 ms   →  son 1,6 s de Aunesa medidos

Los dos **están bien**. Son lentos porque hacen algo caro, y van a seguir siendo
los más lentos mañana y pasado. Una lista de cosas inherentemente lentas **no
cambia nunca** — por eso se deja de mirar, y por eso «no se entera» de nada: no
tiene con qué comparar.

**Lo que sí es información es la DEGRADACIÓN**: un endpoint que hoy tarda mucho
más que él mismo ayer. Eso no aparece en un ranking (puede seguir estando décimo)
y es lo único que amerita interrumpir a alguien. Cada endpoint se compara
**contra sí mismo**, jamás contra otros.

**Las cuatro guardas contra «tirar por tirar»** — la parte difícil, porque un
detector de latencia que grita seguido no genera desconfianza: genera que se lo
ignore, que es peor:

1. **Volumen mínimo** (20 requests). La media de 3 es ruido, no una medición.
2. **Historia mínima** (6 horas). Comparar contra dos puntos es adivinar.
3. **Relativo Y absoluto, los dos** (2,5× **y** +300 ms). Triplicarse de 10 ms a
   30 ms no le importa a nadie; empeorar 400 ms sobre 5.000 tampoco.
4. **MEDIANA y no promedio** en la línea base: un pico previo subiría la vara y
   taparía justo el problema que se repite. Hay un test que lo demuestra.

Los **5xx no pasan por la comparación**: un endpoint que rompe está roto tarde lo
que tarde, así que van con su propio umbral y sin línea base.

Corre con los detectores live **cada 5 minutos en rueda** (lo que el user llamó
«tiempo real»), y cuesta **una sola query** sobre un agregado que ya existía. La
ventana es de 2 horas y no de minutos porque el agregado es **por hora**: pedirle
a ese dato una resolución que no tiene sería inventar precisión.

⚠️ **Los cuatro números son hipótesis sin medir** (REGLA #2). Están como
constantes con nombre para moverlas con un dato real: el primer día que esto
cante algo obvio o se quede mudo, se ajustan.

#### Qué se eliminó y qué quedó

- **La tab LATENCIA de Manager se eliminó**: el ranking es justamente lo que
  falló, y dejarlo mantiene lo que enseñó a no mirar. `manager.latencia_endpoints`
  y el middleware **no se tocaron** — se eliminó la pantalla, no el dato.
- **La tab BASE queda**: el user no se quejó de esa y es el inventario. Lo que se
  suma es que el agente ahora la entienda y avise solo.
- Las dos capacidades entraron a **SKILLS solas, por la ley de §0.o** — no hubo
  que anotarlas en ningún lado. Y las dos son **`función`, sin IA**: lo que el
  agente encuentra lo encuentra una función determinista.
- Además hay **dos explicadores nuevos** (§0.m): *«¿Cuánto pesa la base y qué
  creció desde ayer?»* y *«¿Hay algún endpoint más lento que lo normal?»* — el
  segundo separa a propósito **lo degradado** (que es un problema) de **lo que
  más tiempo consume** (que es el ranking y **no** es una lista de problemas).

---

---

### 0.r EL CONTEXTO: el agente conoce la base sin que nadie se lo escriba (2026-08-19)

Pedido del user, y son dos cosas que van juntas:

> *«Que el agente sepa exactamente cada tabla que hay, y exactamente cómo funciona
> esa tabla en cuanto a los datos. Es decir: si portfolio en AuM actualiza con
> fecha T-1, ok, que el agent diga qué día es hoy, cuándo es T-1, y vaya a buscar:
> ¿hay datos? sí, no. Bueno, pasa algo o no pasa nada… Este agente me tiene que
> ayudar (y a futuro hacer solo) a controlar el sistema.»*
>
> *«Que no dependa de un git pull, que no dependa de cosas estáticas. Que siempre
> sepa qué hay en las bases, de schema y eso, o de tablas posta. Quiero modelarlo
> así al agente, sus skills con sus features, todo modularizado, que tenga un
> contexto. Hay que armarlo pro.»*

#### POR QUÉ NO HAY NINGUNA LISTA — y es la decisión que hace que esto escale

La respuesta obvia sería escribir el contrato de cada tabla: *«`portafolio.tenencia`
es diaria, `market_snapshot` es live, …»*. **Es la respuesta equivocada, por dos
razones distintas:**

1. **Nadie mantiene 200 contratos.** La lista quedaría vieja el primer mes — y una
   lista vieja es PEOR que ninguna, porque afirma cosas falsas con la misma cara
   que las verdaderas. Este proyecto ya lo pagó: por eso `MAPA_APP.md` §0,
   `SISTEMA.md` y el catálogo de SKILLS se autogeneran.
2. **Depender de un `git pull` para que el agente sepa qué existe es lo contrario
   de un agente.** Una tabla creada el martes tiene que estar en su cabeza el
   martes, no cuando alguien se acuerde de anotarla.

Así que **todo se DERIVA** (`api/services/av_agent_contexto.py`):

| qué | de dónde | mantenimiento |
|---|---|---|
| qué tablas hay | `pg_catalog` | **cero** — una tabla nueva aparece sola |
| cuál es su columna de fecha | `information_schema` + las convenciones del repo | cero |
| **cada cuánto se escribe** | **se MIDE** mirando la distribución de esa columna | cero |

Lo tercero es la parte no obvia y es la que hace que esto cubra 200 tablas: **la
cadencia no hay que declararla, la tabla la dice**. Si el intervalo típico entre
escrituras es de segundos, es `tiempo_real`; si es de un día hábil, es
`diaria_habil`. Siete clases: `tiempo_real` · `intradiaria` · `diaria` ·
`diaria_habil` · `semanal` · `mensual` · `eventual` / `estatica` / `vacia`.

**Se mide con la MEDIANA, no el promedio.** Un fin de semana, un feriado o un
backfill viejo desplazan la media y dejarían a una tabla diaria pareciendo
semanal — con lo cual se le dejaría de exigir frescura justo a la que importa.
Y se mide sobre las **últimas** escrituras: una tabla que hace un año era diaria y
hoy es live tiene que decir live.

#### Dónde SÍ manda un contrato declarado, y por qué

⚠️ **La cadencia aprendida tiene un punto ciego que hay que entender:** si el job
de tenencias lleva tres días roto, la «normalidad observada» de la tabla **se
corre sola** y el detector deja de avisar — se acostumbra al problema.

Por eso los 8 `CONTRATOS` declarados de `salud.py` **siguen mandando donde
existen**: ahí el «debería» es una decisión de negocio (`portafolio.tenencia` DEBE
tener el último día hábil), no un promedio. La derivación cubre las otras ~190,
que hoy eran un **punto ciego total**. Y el detector nuevo **excluye** las que ya
tienen contrato, para que el mismo problema no aparezca dos veces con dos textos.

Es la misma lógica que en la latencia (§0.q): compararse con uno mismo detecta un
CAMBIO, y no detecta algo que está mal desde siempre.

#### Tres cosas que evitan el falso positivo

- **El fin de semana no cuenta.** Sin eso, toda tabla de días hábiles aparece
  atrasada cada lunes a la mañana.
- **A una tabla sin ritmo no se le exige frescura.** `eventual`, `estatica` y
  `vacia` devuelven `no_se_puede_saber`, no `atrasada`: a una de carga manual no
  se le puede pedir que escriba, y marcarla en rojo todos los días es cómo se
  entrena a alguien para ignorar una pantalla.
- **Los topes son generosos** (3-4× el intervalo típico). Un detector que avisa al
  primer atraso avisa todos los días, y de uno así no se desconfía: se lo ignora.

`manager.tabla_perfil` es solo la MEMORIA de la medición (una fila por tabla,
upsert, no crece), para no re-medir 200 tablas cada vez que alguien pregunta —
sin eso el agente nunca preguntaría. Se refresca en el job diario, junto con la
foto de tamaño: las dos contestan «¿cómo está la base?» y separarlas daría dos
horarios y dos cosas que pueden fallar.

#### LOS MOTORES — todo estaba hecho y el agente no lo miraba

*«Con los logs de los motores lo mismo: quiero que si hay alguno caído enterarme
rápido (y a futuro que pueda hacer algo)»*.

`api/services/diagnostico_registry.py` ya tiene **53 piezas** —15 motores, 33 jobs,
5 APIs— **cada una con su cadencia, su ventana horaria, su umbral y de dónde se
lee la frescura**, y `diagnostico.arbol()` las evalúa. Pero eso vivía SOLO en la
pantalla de Manager → DIAGNÓSTICO, o sea que había que ir a mirarla.

`av_agent_motores.py` **no reimplementa nada**: lee el mismo árbol y convierte lo
que está roto en un hallazgo. Reescribirlo habría dado dos verdades sobre si un
motor anda. Corre en el monitor de rueda, cada 5 minutos.

⚠️ **La VENTANA es lo que hace que esto no mienta.** Un motor fuera de rueda **no
está caído: está apagado** — los prende y los apaga el cron de lunes a viernes.
Sin mirar la ventana, este detector cantaría quince motores muertos todos los
sábados, y en dos fines de semana nadie volvería a leerlo. Y `lento` **no** se
reporta: un motor que tarda el doble sigue produciendo, y mezclarlo con uno muerto
pierde la diferencia entre las dos cosas.

#### El árbol tenía NUEVE agujeros, y el anti-drift lo decía hace meses

Al revisar el primer deploy apareció que `tests/test_diagnostico_registry.py`
—el test anti-drift que cruza el registro contra `deploy/crontab.txt`— **estaba en
rojo**: nueve crons corrían sin estar en el árbol. Como el agente ahora LEE ese
árbol, cada agujero es una pieza que no vigila nadie.

Se clasificaron uno por uno, y la clasificación importa más que el número:

- **TRES eran piezas de dato de verdad** y entraron al árbol: `tamar_1816` (la
  TEA/margen de la pata TAMAR — si el feed se corta la celda queda vacía **y la
  otra pata sigue viva**, así que todo «parece bien»), `tesoreria_echeq_recibidos`
  (el espejo de los depósitos; si muere, el equipo los carga a mano sin enterarse)
  e `interbanking_sync` (los extractos: es la única fuente de esos saldos, y al
  cortarse la tab **no miente, se queda quieta** — que es peor de detectar).
- **TRES son el agente mirando al sistema** (`av_agent`, `av_agent_live`,
  `db_tamano`): quedan fuera a propósito. Meterlos al árbol sería pedirle al árbol
  que se vigile a sí mismo.
- **TRES son mantenimiento de catálogo** (`assets_autofill`,
  `validar_instrumentos`, `ficha_1816`): escriben metadata (ticker, emisor,
  vigencia, símbolos), no el dato que la vista muestra. Si un día no corren, la
  pantalla sigue mostrando lo mismo.

**Lo que dejó de lección:** el test existía y avisaba; lo que faltaba era que
alguien pagara el costo de clasificar. Un cron nuevo entra a `_CRONS_IGNORADOS` en
diez segundos y ahí queda invisible para siempre — por eso ahora cada entrada de
esa lista lleva **por qué** no es una pieza de dato, no solo el nombre.

#### La gracia del arranque — el falso positivo que se cazó ANTES de que pasara

⚠️ **La ventana del árbol abre 20 minutos ANTES de que los motores arranquen:**

    10:00 ART   `_APERTURA["rueda"]` — la ventana del árbol abre
    10:03-10:06 las piezas empiezan a dar CRÍTICO (umbral × 3, son 60-120 s)
    10:20 ART   los motores ARRANCAN de verdad (`20 13 * * 1-5` del crontab)

Son **~17 minutos de falsos positivos todos los días**. En la pantalla de
DIAGNÓSTICO eso ya pasaba y no molestaba —había que ir a mirarla—; como hallazgo
del agente sería **un aviso en ALTA cada mañana a la misma hora**, que es la
forma más rápida de que se deje de leer. Se detectó al revisar el primer deploy,
antes de que el detector llegara a su primera rueda.

`GRACIA_ARRANQUE_MIN = 30` **no es un umbral de tolerancia**: pasado ese rato, un
motor que no produce SÍ está caído y se canta. Y la gracia vive en el detector, no
en `_APERTURA`: esa ventana la comparte la pantalla de DIAGNÓSTICO, y moverla para
arreglar el detector cambiaría el estado de una vista que nadie pidió tocar.

#### La pregunta que nadie contestaba de una

*«No quiero que me muestre todos los endpoints; yo quiero saber que en horario de
mercado la aplicación funciona bien y no hay nada colapsando.»*

Explicador **«¿Está todo funcionando bien ahora?»**: junta las TRES patas —los
motores, la frescura de las tablas y la velocidad— y arranca con el veredicto en
una línea. Las tres existían, en tres pantallas distintas, y por eso había que
saber de antemano dónde mirar para poder preguntarse si el sistema andaba.

**Qué NO se muestra**: el conteo, no las 53 piezas; solo se detallan las rotas. El
user fue explícito con que no hace falta mostrar todo en el modal, **pero que por
dentro se haga**.

#### Lo que queda para «que a futuro haga algo solo»

El agente ya **ve** un motor caído y una tabla quieta. Poder **relanzarlos** es la
acción que sigue, y entra por la arquitectura de §0.j (proponer → tu OK → aplicar
→ verificar) — con la diferencia de que ahí el «verificar» es esperar a que el
motor vuelva a escribir, no releer una fila.

---

---

### 0.s ¿LOS PERMISOS SON REALES O ESTÁN EN LOS PAPELES? (2026-08-19)

> *«Que sea capaz de detectar si algún endpoint está mal hecho y se puede
> consultar a la fuerza por tener solo permisos "en los papeles". Una vez me
> había pasado que Vercel me dejaba todo sin protección y no me daba cuenta.
> Todos los endpoints debería ser capaz de controlar que solo los ves si tenés el
> permiso.»* (user)

#### PRIMERO: el agujero que había, medido

`CLAUDE.md` ya advertía que el tooling de seguridad estaba ciego por la trampa de
FastAPI. **Al medirlo resultó peor de lo que decía:**

    gen_mapa_app (que la resolvía)              541 rutas
    audit_rbac · test_rbac_superficie            37 rutas

O sea que el test llamado «RBAC de superficie» auditaba el **7%** de la superficie
**y pasaba en verde**. Para una herramienta de seguridad ese es el peor modo de
falla posible: no avisa que no sabe — **afirma que está todo bien**.

Y `gen_mapa_app`, el que se creía correcto, tenía su propio bug: **duplicaba el
prefijo en 395 de 541 paths** (`/api/ia/api/ia/observabilidad`), porque el
`prefix` de un `APIRouter` ya viene aplicado a sus propias `APIRoute` y se lo
volvía a sumar. Los gates estaban bien; los paths publicados en `MAPA_APP.md` §0,
no. Un path que no existe es **peor** que uno faltante: cualquier herramienta que
intente PROBARLO recibe un 404 y concluye que está protegido.

**El arreglo no fue copiar la técnica tres veces: fue que haya una sola.**
`api/superficie.py` es ahora la única forma de recorrer la superficie, y las tres
herramientas delegan ahí. Mientras la técnica viva en tres archivos, dos van a
quedar viejos — ya pasó.

**Lo que apareció al destapar el test:** 13 escrituras que figuraban «sin gate».
Verificadas una por una, **12 no eran agujeros**: ACA y Mesa de Dinero se gatean
con allowlists per-usuario (`require_escritura_*`), a propósito y documentado, y
el test solo sabía reconocer `require_module`. Ahora las reconoce, **enumeradas
explícitas** — no con un `startswith("require_")` que trague cualquier cosa.

**La 13ª sí era real y era nueva**: `/api/avisos` (de §0.p) no pedía bearer. Su
única protección era CF Access en el borde — justo lo que no se puede dar por
sentado. Se le agregó, y de paso **el rechazo del invitado dejó de ser un `if`
adentro del handler y pasó a ser una dependency del router**: un `if` en el
cuerpo de una función **no lo puede ver ni el test ni el propio agente**, así que
ese router figuraba como escritura sin ningún gate. *Un permiso que existe pero no
se puede auditar es, para cualquier herramienta, un permiso que no existe.*

#### SON DOS PREGUNTAS DISTINTAS, y separarlas es todo el diseño

| | qué contesta | cómo |
|---|---|---|
| **Lo DECLARADO** | ¿cada endpoint tiene el gate que le corresponde? | se **LEE** del árbol de rutas |
| **Lo EFECTIVO** | ¿el borde realmente lo aplica? | se **PRUEBA** |

La segunda **no se puede leer de ninguna manera**: el backend puede estar
impecable y el borde abierto, que es exactamente lo que le pasó al user con
Vercel. La única forma de saberlo es **pedir sin credenciales y ver qué
contesta**.

Es la primera vez que el agente hace una **prueba activa** en vez de observar. Y
es la idea de **recompensa verificable** de §0.j aplicada a seguridad: no *«creo
que está protegido»* sino *«pedí y me dio 401»*.

**El invariante**, binario y sin listas que mantener:

> **Ningún endpoint puede contestar algo distinto de 401/403 sin credencial.**

#### TRES LÍMITES, y hay que decirlos

1. **Un agente que prueba endpoints sin credenciales es, técnicamente, un
   scanner.** Por eso: **solo `GET`**, solo rutas del inventario propio (no
   adivina URLs), con throttle, y **jamás una escritura** — probar un `POST` «a
   ver si me deja» puede escribir de verdad. Congelado por test.
2. **Desde dónde se prueba cambia qué se prueba.** Desde el Droplet, pegarle a la
   URL pública sale a internet y vuelve por Cloudflare → verifica CF Access y el
   backend. **NO verifica Vercel**, que es donde el user tuvo el problema. Por eso
   el resultado **siempre** declara su alcance: en seguridad una media verdad se
   lee como un sí, y un verde sin alcance da falsa tranquilidad justo en la capa
   del incidente.
3. **Solo rutas sin parámetros de path.** Con `{id}` no se puede armar una URL
   real y un valor inventado devuelve 404, que no dice nada sobre permisos. Esas
   quedan cubiertas por la lectura.

**Y no se inventa la URL**: sin `AV_AGENT_URL_PUBLICA` el chequeo **no corre**.
Probar contra el host equivocado y salir en verde es peor que no probar.

Un **404 no es un hallazgo** (no dice nada: puede ser una ruta con parámetros o un
deploy a medias) y un **405 cuenta como rechazo** (la ruta existe, el método no
aplica: tampoco filtró nada). Reportar los mudos sería el ruido que enseña a
ignorar el aviso.

#### Dónde corre

La parte que **lee** es gratis y corre en el job nocturno junto con la foto de la
base. La que **prueba** hace tráfico real contra producción, así que va ahí
también y **no** en el monitor de rueda: 400 requests cada cinco minutos molestan,
y una superficie mal gateada no se arregla sola en ese rato.

Explicador **«¿Están bien protegidos los endpoints?»**, que muestra las dos capas
por separado — porque una se arregla en el router y la otra en el borde.

#### LO QUE ENCONTRÓ LA PRIMERA CORRIDA (2026-08-19, en prod)

Seis avisos, todos del handshake OAuth del MCP. **Verificados uno por uno contra
`docs/MCP.md` y `CLAUDE.md`, cinco no eran agujeros y el sexto sí valía la pena
—pero por otro motivo del que parecía.**

Los cinco son **públicos por protocolo**: el cliente los lee ANTES de tener
credencial, así que pedirle credencial para averiguar cómo sacar una credencial
no cierra nunca. Son los tres `.well-known/*` (discovery, RFC 8414 / RFC 9728),
`/oauth/register` (DCR, RFC 7591 — devuelve credenciales NUEVAS, no ajenas) y
`/oauth/token` (su **input** ES la credencial: code + PKCE verifier).

**El sexto es `/oauth/authorize`, y es una categoría distinta:** ahí logea el
usuario, o sea que NO es público — lo que pasa es que **su candado vive en
Cloudflare Access, no en el repo**. Declararlo como «abierto a propósito» habría
sido mentir, y peor: lo habría sacado del radar justo donde el user ya se quemó
(*el código impecable, el borde abierto, cero errores visibles*).

Por eso son **DOS listas y no una**, y la diferencia no es cosmética — cambia qué
hay que verificar:

| lista | qué significa | qué se prueba |
|---|---|---|
| `ABIERTOS_OK` | público de verdad: no devuelve nada de nadie | nada que probar |
| `PROTEGIDOS_EN_EL_BORDE` | el candado existe, pero vive afuera del repo | **siempre**: es lo único que solo la prueba puede juzgar |

La segunda lista **fuerza** su prueba activa: el filtro general descarta las rutas
sin gate (`not r.sin_gate`) y justamente estas no tienen gate en el código a
propósito. Si `/oauth/authorize` contesta **200 sin credencial** en vez de desviar
al login, la app de CF Access que lo cubre no está, se despublicó o le cambiaron
el path → hallazgo `borde_sin_candado`, severidad alta.

De paso, un **3xx al login cuenta como RECHAZO**, no como fuga: así contesta CF
Access sin sesión, y se reconoce por el host del `Location`
(`cloudflareaccess.com`), no por el código. Contarlo como fuga habría llenado el
aviso de falsos positivos, que es exactamente como se aprende a ignorarlo.

#### EL SILENCIO NO ES UN VERDE

La primera corrida también mostró un hueco **del reporte, no del chequeo**: el job
imprimió los seis avisos y nada más, así que un lector razonable concluye que el
borde se probó y salió bien. **No se probó** — falta `AV_AGENT_URL_PUBLICA`.

Un job que solo habla cuando encuentra algo se lee igual esté sano o esté ciego.
Ahora el detector emite un hallazgo propio (`prueba_no_corrio`, severidad media) y
el job **siempre** cierra diciendo qué cubrió: *«N rutas leídas + el borde probado
(no cubre Vercel)»* o *«N rutas leídas — **el borde NO se probó**, falta X»*.
Congelado por test, porque es el mismo principio que el `alcance`: **en seguridad
una media verdad se lee como un sí.**

**PENDIENTE (REGLA #6, se pide una vez):** `AV_AGENT_URL_PUBLICA` en el `.env` del
Droplet (`https://api.acaquant.com`). Sin eso, la mitad que PRUEBA queda apagada y
solo corre la que lee.

---

---

### 0.u EL RELOJ DEL MERCADO — la calibración que hizo honesto al monitor (2026-08-19)

> *«El agente tiene que entender el horario de mercado, los motores, los horarios.
> No puede decir solo desde cuándo no actualiza algo, porque eso es mentiroso. En
> AVISOS tenía un montón de avisos de precios sin precio, pero eso era porque el
> MERCADO ESTABA CERRADO: es obvio que no va a actualizar si el precio cierra a
> las 17 y abre a las 10:30. Ahora es relevante entender por qué uno de los bonos
> que aparece en la tabla no tiene precio DURANTE LA RUEDA — si es por liquidez o
> porque no suscribe porque hay algo mal, como es el caso del AO29, que claramente
> hay algo mal y ni lo está detectando.»* (user)

La primera corrida del monitor del sistema cantó **58 hallazgos**. El diag los
agrupó y el resultado fue el que hacía falta para no creerle: **47 de 57 tablas
«quietas» eran de la misma cadencia**, con atrasos de 47 minutos a 43 días. Eso
no son 47 problemas — **son dos bugs del detector**, y ninguno se arregla subiendo
una tolerancia.

#### Bug 1 — una RÁFAGA no es un ritmo

La cadencia se medía como la mediana del intervalo entre escrituras consecutivas.
Una tabla de **auditoría o un catálogo** se escribe a los saltos: alguien edita y
entran 15 filas con dos segundos de diferencia, y después nada por tres semanas.
La mediana mira **adentro** de la ráfaga y dice *«tiempo real»*.

Por eso `clientes.aca_valores` —sin escribir hace **43 días**— aparecía
clasificada como live y por lo tanto atrasada. Lo mismo `senebis_agentes`,
`contrapartes`, `role_audit`, `ons_ignoradas`: **todas tablas de evento**.

El arreglo no es tocar el umbral: es **preguntar otra cosa**. Una tabla con ritmo
rápido escribe **casi todos los días**; una a ráfagas, no. Se mide igual que todo
acá —observando, sin declarar nada— contando en **cuántos días distintos** escribió
en el último mes; la que no llega se degrada a `eventual`, que ya significa *«no se
le puede exigir frescura»*. Y si la medición falla, **no se degrada nada**: «no pude
mirar» jamás puede convertirse en un veredicto.

#### Bug 2 — el atraso se medía en tiempo de RELOJ

`mercado.timesales` a las 20:30 ART lleva 3½ horas sin escribir. Eso **no es un
atraso**: el mercado cerró a las 17. Con tiempo de reloj, el job de las 23:30
marcaría todas las tablas de rueda **todas las noches, para siempre** — y un aviso
que aparece siempre a la misma hora se deja de leer en una semana.

Ahora el atraso de una cadencia intradía se mide en **segundos de mercado
abierto**. Es la misma idea de `_segundos_de_finde` (que ya existía para las
diarias hábiles) llevada a su forma general, y el mismo principio que el detector
de motores: *fuera de rueda no está caído, está apagado*.

    último dato          ahora            atraso de RELOJ    atraso de RUEDA
    ─────────────────────────────────────────────────────────────────────────
    ayer 16:58 ART       hoy 20:30 ART         27,5 h              7 h  ← real
    hoy  16:58 ART       hoy 20:30 ART          3,5 h              0    ← cerrado
    viernes al cierre    lunes 10:30 ART          65 h             30 min

**Y no es indulgencia**: una tabla de tiempo real que se pasó la rueda ENTERA sin
escribir sigue saliendo atrasada. De yapa resuelve el arranque — a las 10:10 ART
hace diez minutos que abrió, así que lo de ayer al cierre acumula diez minutos, no
diecisiete horas.

#### Lo mismo, del otro lado: los avisos que sobrevivían al cierre

El monitor de rueda corre 10:30-17 y **reemplaza** lo suyo en cada pasada. Pero al
cerrar deja de correr, y su última foto —la de las 16:55— **se quedaba en la
pantalla toda la noche y todo el fin de semana**. Eso es lo que el user veía como
«avisos de sin precio con el mercado cerrado»: *el detector estaba bien, la foto
estaba vieja*, que para el que mira es lo mismo.

Ahora un hallazgo de rueda **vence** (`av_agent.VENCEN_EN_S`, 15 min). Vence en
vez de borrarse con un cron al cierre, y eso es a propósito: **si el monitor se
muere a las 11, sus hallazgos también desaparecen** — y está bien, porque ya no
sabemos si siguen pasando. Un dato que nadie refresca no puede seguir afirmándose.
Se cura solo y no depende de que ningún job corra a la hora justa.

#### El AO29, resuelto: no era el precio, era la PATA

El diag desmintió mi hipótesis —y por eso existe—. El AO29 pasa la cadena
entera: master, símbolo, seis patas validadas, Primary lo lista, y el snapshot
lo actualiza cada pocos segundos con **métricas completas y sanas** (TEA 9,73%,
paridad 91,4, duration 2,88). Los contadores del barrido dieron **0 sin símbolo,
0 sin snapshot, 0 con precio y sin métricas** — la regla que iba a escribir no
habría cazado nada.

Lo que sí estaba mal se ve al comparar el bono con su familia:

    AO27   102,00      paridad 102,00     cotiza en DÓLARES
    AO28    94,80      paridad  94,80     cotiza en DÓLARES
    AN29    92,65      paridad  92,65     cotiza en DÓLARES
    AO29   139.300     paridad  91,41     cotiza en PESOS   ← el raro

**Hay DOS fuentes de símbolos y nadie las cruzaba.** `curvas.instrumento` —lo que
el motor suscribe— se carga **a mano**; `mercado.especies` sabe cuál es la pata
correcta (`es_default`) y se deriva de Primary. Medido: de **229 bonos, 3** no
coinciden — **AO29, GD46 y CO32** — y son exactamente los tres que muestran pesos
en una curva en dólares. El GD46 es el que el user ya había cazado a ojo el
2026-08-18; los otros dos son el mismo bug sin descubrir.

Esto **cambia el veredicto anterior**. El 2026-08-18 la regla `cotiza_en_pesos`
se había bajado a `baja` con el texto *«la valuación está bien, es contexto»* —
correcto sobre la valuación y equivocado sobre la causa: no es que el bono cotice
así y no haya nada que hacer, es que **la pata correcta ya existe, está validada,
y el arreglo es un campo**. Ahora, cuando `especies` marca otra default, el
hallazgo es `pata_equivocada` con severidad **media** y nombra el símbolo exacto.
No `alta`: la valuación sigue estando bien, no hay plata mal contada.

⚠️ **Y el hallazgo avisa que hace falta reiniciar el motor.** El universo se arma
al arrancar, así que cambiar el campo **no surte efecto hasta el próximo
reinicio** — y reiniciar en rueda corta el feed de la mesa. Por eso mismo esto
**no** se convirtió en una ACCIÓN automática todavía: el ciclo de §0.j verifica
releyendo la fila, diría «aplicada» y la pantalla seguiría igual hasta la noche.
*Una acción que se aplica y no se ve destruye la confianza en todas las demás.*

#### Y el final: el motor y la pantalla no se estaban hablando

Nada de lo anterior explicaba **por qué la fila salía en `--`** teniendo el precio
y las métricas cargadas. El agente decía «está todo bien» y tenía razón sobre lo
que él miraba; la pantalla mostraba vacío y también tenía razón. **Las dos cosas
pueden ser ciertas si cada uno está mirando un símbolo distinto** — y era eso:

    el MOTOR escribe el precio leyendo el BLOB   `engines/curvas.py`
    la VISTA lo busca por la COLUMNA             `… ON s.ticker = c.instrumento`

Viene del renombre del 2026-08-15, que migró las columnas y dejó el blob intacto
a propósito (lo leen ~500 lugares) **con el significado invertido**:

    COLUMNA   ticker = «AL30»              instrumento  = «MERV - XMEV - AL30 - 24hs»
    BLOB      ticker = «MERV - XMEV - …»   ticker_corto = «AL30»

Mientras los dos digan lo mismo no pasa nada, y por eso durante cuatro días no
pasó. **El problema es que nadie los estaba manteniendo iguales.** Existía el
mecanismo —`_COLS_FUERA_DEL_BLOB`, donde *la columna gana*— y cubría el emisor y
los tres ejes, pero **no los dos campos de símbolo**: eran los únicos sin árbitro.

Medido: **2 de 229** — AO29 y CO32. En los dos la columna ya tenía la pata
correcta (la D, en dólares) y el blob la vieja en pesos. O sea que el dato estaba
bien; lo que estaba mal era **quién lo leía**.

Se arregló con la misma regla, sumando los dos campos al merge **con ALIAS**
(sin él, `doc["ticker"]` pasaría a valer `AO29` y los ~500 lugares que lo usan
como símbolo de mercado se romperían todos juntos — peor que el bug original).

⚠️ **Y el efecto no es inmediato**: el motor arma su universo al arrancar, así que
hasta reiniciarlo **fuera de rueda** esos dos bonos quedan sin suscribir. Ahora el
agente los canta como `no_suscripto` — que es exactamente la señal que faltaba, y
la prueba de que la mitad que él mira y la que mira la pantalla por fin coinciden.

**La lección, que vale más que el bug:** dos representaciones del mismo dato sin
un árbitro declarado no conviven — se separan. Y cuando se separan no falla nada:
cada mitad sigue siendo internamente coherente y el sistema miente en silencio.

#### Y un error del diag, que es el mismo pecado que este módulo persigue

La primera versión corría **un solo detector** (`sin_precio`) y después imprimía
*«el agente NO reporta nada de este bono»*. Falso: `precio_moneda` **sí** lo
estaba cantando. Una herramienta que exagera su propio alcance es exactamente lo
que se encontró en la superficie HTTP (el test que auditaba el 7% y decía que
estaba todo bien). Ahora corre los dos y dice «los detectores de rueda».

#### El AO29: el bono peor cargado era el único invisible

El detector de precios abría su loop así:

    if not simbolo or not tk:
        continue

O sea que **un bono del master sin símbolo de mercado se salteaba en silencio**.
No es «no aplica»: es el peor caso posible —no puede tener precio nunca, y el
motor no falla porque ni siquiera lo intenta— y encima es **el más accionable de
todos**, porque el símbolo sale de `mercado.especies`. El detector decía distinguir
las causas del «sin precio» y justo la primera la tiraba a la basura.

Ahora es un hallazgo propio (`sin_simbolo`, alta). Y `scripts/diag_bono_sin_precio`
recorre la cadena entera de un ticker —master → símbolo → especies → universo de
Primary → snapshot → precio— y dice **en qué eslabón se corta**, que es la
diferencia entre *«no opera por liquidez»* y *«hay algo mal cargado»*.

---

---

### 0.v LA PRIMERA ACCIÓN QUE SE VE EN EL ACTO (2026-08-19)

> *«Esto que acabamos de hacer es un tipo de actitud que debe tener el agente:
> estar monitoreando y saber la solución.»* (user)

#### El error que la origina, y hay que dejarlo escrito

Buscando por qué la fila del AO29 salía vacía, se midió *«¿esta pata opera?»*
contra `mercado.timesales` y dio **0 trades en 30 días**. Se concluyó que la pata
en dólares no cotizaba. **Era falso, y de la peor forma posible.**

`mercado.timesales` la escribe el motor **solo para los símbolos que suscribe**.
Como a `AO29D` no la suscribía nadie, tenía 0 filas **por construcción**.
Preguntarle a esa tabla si un símbolo opera es preguntarle al que no estaba
escuchando si sonó el teléfono. Dos pistas lo delataban y no se miraron: **todas**
las patas no suscritas daban 0 (la firma de «solo tenemos lo que pedimos»), y la
tabla **se purga a 7 días**, así que la ventana de 30 no podía existir.

`snapshots_cierre_hist` y `market_snapshot` tienen el mismo origen. **Las tres
fuentes eran la misma fuente.**

Se lo pidió, y `AO29D` cotizaba a **USD 90,76** — que coincide al centavo con lo
que el motor venía calculando dividiendo el precio en pesos por el MEP (138.220 /
1.521,89 = 90,82). El cálculo siempre estuvo bien; lo que faltaba era escuchar.

> **La regla que queda:** el agente **no puede concluir «no existe» desde una
> tabla que solo contiene lo que él mismo pidió.** La ausencia de dato prueba que
> no estamos mirando, no que no haya nada.

#### Y de paso: se reinició el motor equivocado

`motor_curvas` **no suscribe nada** — su propio log lo dice: *«Escuchando N
tickers vía MarketSnapshot»*. Es un consumidor que lee `market_snapshot` y calcula
TEA, paridad y duration. Se lo reinició en plena rueda, cortando el feed, y no
podía cambiar ninguna suscripción. El que pide los precios es **`motor_rofex`**
(`engines/valores.py`), que arma su universo AL ARRANCAR.

#### La acción: `mercado.pedir_pata`

**No hace falta reiniciar nada.** `motor_rofex` tiene un `adhoc_watcher` que cada
5 segundos lee `mercado.adhoc_subscriptions` y suscribe lo que falte — y las
suscripciones de pyRofex son **aditivas**: no rompen las existentes. Se puede
pedir un símbolo **en plena rueda, sin cortarle el feed a la mesa**.

Eso la vuelve **la primera acción del agente cuyo efecto se puede ver en el
acto**, y por eso es la que se automatiza. La comparación con su hermana es el
criterio que hay que reusar:

| | efecto | ¿se automatiza? |
|---|---|---|
| cambiar el símbolo del master | no se ve hasta el próximo arranque del motor | **no** |
| pedir la pata (adhoc) | el motor la levanta en 5 s, en rueda | **sí** |

*Una acción que se aplica, se verifica en verde y no cambia nada en la pantalla
destruye la confianza en todas las demás.*

**Qué significa «verificada» acá**, que no es obvio: que el precio LLEGUE no
siempre se puede saber en el acto —la pata puede no operar hasta las 15—, así que
se verifica lo que la acción **sí controla** (que quedó pedida) y el detalle dice
si el precio ya entró o todavía no. Y eso no es una excusa: si nunca llega, el
hallazgo sigue a la vista. Lo que cambió es que **ahora la ausencia significa
algo**, porque estamos escuchando.

La alimenta el control `patas_sin_precio`, cuyo docstring dice explícitamente que
**no concluye nada sobre liquidez**: señala que hay un símbolo del master que
nadie está pidiendo, nada más.

---

---

### 0.x LA LISTA DE TRABAJO DEJA DE TENER TRABAJO QUE NO ES (2026-08-19)

> *«En AHORA marqué como leído un montón y siguen apareciendo grisados. Además es
> raro: dice REVISANDO cada 30 seg y arriba dice revisado hace 8 min.»*
> *«Los que ya el sistema detecta que no tienen punta son porque no tienen
> liquidez. No es un problema. Está bien que los marque como ilíquidos pero por
> defecto mostremos otra cosa.»*
> *«Los de cotiza en pesos… no ofrece una solución o algo, nada. Le falta ahí una
> feature que sepa ir a buscar y agregar.»* (user)

**Las tres son la misma queja**: la pantalla mostraba datos ciertos y ninguno de
los tres se podía trabajar. Y la tercera además escondía un error de método.

#### El botón que no cambiaba nada

Marcar visto dejaba el hallazgo **en la misma lista, en gris**. El diseño lo
había decidido a propósito y el razonamiento era bueno —*si marcar visto
escondiera el hallazgo, nadie lo marcaría por miedo a perderlo de vista*— pero de
ahí se sacó una conclusión de más. Con veinte abiertos, marcar los veinte no
cambia **nada** en pantalla, y un botón que no cambia nada se lee como roto.

La tab se llama AHORA y su contrato es *«lo que espera una decisión tuya»*. Un
hallazgo ya visto **sigue abierto pero ya no espera nada**. Así que no se
esconde: se **pliega**, contado y a un clic. Esconder y plegar se parecen y no
son lo mismo — la diferencia es si el número sigue a la vista.

#### Dos relojes con el mismo verbo

    CENSO       contra 1816 · ~29 créditos · de noche o a mano  → llena ENCONTRÓ
    VIGILANCIA  local · cada 30s en rueda · cero créditos       → llena AHORA

Los dos decían «revisado». No era un bug: la pantalla se contradecía sola porque
usaba una sola palabra para dos ritmos distintos. Ahora cada uno tiene su verbo y
**se muestran juntos** en el header — verlos al lado es lo que hace innecesario
explicarlos.

#### La iliquidez no es trabajo, pero tampoco se borra

29 `sin_punta` encabezaban ENCONTRÓ. La diferencia con su hermano es toda la
cuestión:

    no_suscripto   nadie pidió el precio  → la ausencia NO prueba nada  → NUESTRO
    sin_punta      lo pedimos y no vino   → la ausencia SÍ significa    → MERCADO

Es la **otra cara de la regla del AO29** (§0.v). Aquélla decía que no se puede
concluir «no existe» desde una tabla que solo tiene lo que pedimos; ésta dice que
cuando **sí** lo pedimos, la ausencia por fin significa algo — y lo que significa
es iliquidez, que no se arregla de este lado.

Nuevo eje `av_agent.DE_QUIEN`, **declarado y no inferido** (misma lección que el
dominio de las skills). Una regla que nadie clasificó cae en `nuestro`, que es el
lado que **no** esconde: el default nunca puede ser el que hace desaparecer cosas
sin que nadie lo decida. Y el corte no es silencioso — el contador «N del
mercado» está siempre a la vista, y buscar un ticker los encuentra igual.

#### Y el que sí era un error: «no encontré una pata en dólares»

Los 43 `cotiza_en_pesos` cerraban con esa frase después de mirar **una sola
tabla**, `mercado.especies`. No es tan circular como `timesales` —sale de
Primary— pero **se siembra a mano** con `scripts.sembrar_especies` y
`jobs.validar_instrumentos` le borra filas. O sea que puede estar incompleta, y
cuando lo está el hallazgo afirmaba de más. Es la misma forma del error del AO29,
cuatro días después y en otro módulo.

Ahora se pregunta en orden, y recién con las tres contestadas se puede afirmar:

    1. ¿la tenemos sembrada?   `mercado.especies`
    2. ¿existe en el mercado?  `manager.pyrofex_instruments` (el catálogo de
                               Primary: la única fuente que no depende de
                               ninguna decisión nuestra)
    3. ¿la estamos pidiendo?   `adhoc_subscriptions` + `market_snapshot`

Cuatro desenlaces, y se nombran distinto porque se atienden distinto: `sembrada`
(falta pedirla) · `solo_en_primary` (falta sembrarla y pedirla) · `sin_pata` (es
el instrumento, no un dato mal cargado) · `no_pude_mirar` — que **jamás** puede
leerse como los otros tres.

#### La puerta, y qué NO arregla

`api/services/av_agent_pata.py` + `/api/ia/av-agent/pata` (leer) y `/pata/pedir`
(sembrar y pedir). Es la única puerta de rueda que además **escribe**, y se
automatiza por el criterio de §0.v: el `adhoc_watcher` la levanta en 5s, sin
reiniciar y en plena rueda.

**Pedir la pata no cambia lo que muestra la grilla** — y eso hay que decirlo
antes de que alguien lo descubra solo. La grilla dibuja
`mercado.curvas.instrumento`, y cambiar ese campo es la acción hermana que sigue
**sin automatizarse** porque el motor arma su universo al arrancar. Lo que esta
puerta consigue es el paso ANTERIOR, que es el que faltaba:

    hoy      no sabemos si esa pata cotiza — nadie la escucha
    después  la escuchamos, y en 5s sabemos si tiene precio y cuál es

Sin ese dato el reinicio sería a ciegas, y **apuntar el master a una pata que
tampoco opera es cambiar un problema por otro**. Registrada en SKILLS vía control
(`patas_dolar_sin_pedir`) + acción (`mercado.pata_dolar`), derivada y no a mano,
como manda la LEY de §0.o.

#### De yapa: dos relojes adentro de SALUD, y un guard protegiendo el aire

`salud._chequeo_job` **recibía `ahora` y después leía otro reloj** (el del
sistema) para calcular la corrida esperada. En prod los dos coinciden, así que el
bug estaba dormido; lo que sí rompía era el test fundacional del módulo —el job
en `ok` sin correr hace dos días—, que congela el reloj. Es literalmente la forma
del bug del blob y la columna: dos representaciones del mismo dato sin árbitro,
que mientras coinciden no fallan. Acá el árbitro es el parámetro.

Y `test_salud_admin_only` recorría los `/api/manager/salud*` **borrados en
§0.l**: 31 casos devolviendo 404 donde esperaban 403. Un guard de seguridad
apuntando a rutas que no existen no protege nada —desde afuera un 404 y un 403 se
ven igual de cerrados— y encima bloqueaba el CI. Repuntado a
`/api/ia/av-agent/salud*`, y ahora **un 404 es FALLA y no un aprobado**: la
existencia se verifica contra la tabla de rutas (`api.superficie`) y no pegando
por HTTP, porque pegar ejecuta el handler y sin base diría «no existe» — el mismo
falso veredicto que este módulo persigue.

> La suite pasó de **42 fallas a 10**. Las 10 que quedan son
> `tests/unit/test_titulos_negativos.py` (`KeyError: 'latido'`), de la otra
> sesión (`control-saldos`).

#### LO QUE DIJO LA MEDICIÓN, incluida la parte que me deja mal parado

Corrido `scripts.diag_pata_dolar` contra prod, 44 bonos de curva USD cotizando en
pesos:

    sembrada           28   63,6%
    solo_en_primary     0    0,0%   ← el caso que este fix venía a destapar
    sin_pata           16   36,4%

**`solo_en_primary` dio CERO.** O sea que el fix de las dos fuentes **no destapó
un solo caso**: hoy `mercado.especies` no le falta ninguna pata que Primary sí
liste. La hipótesis de que la tabla estaba incompleta era razonable y resultó
falsa, y queda escrito porque medir para confirmar lo que uno ya creía no es
medir.

Lo que el cambio sí compra, y no es poco: **las 16 `sin_pata` pasaron de ser una
afirmación sin respaldo a una verificada contra las dos fuentes**, y el día que
el catálogo se mueva —que se mueve— el detector no vuelve a mentir solo. Un
detector correcto por casualidad deja de serlo sin avisar.

#### Y lo que la medición SÍ encontró, que era otra cosa

**Las 28 con pata sembrada tenían las tres columnas iguales: sin pedir y sin
precio.** El AO29 replicado 28 veces — la pata existe, está validada, y nadie la
escucha, así que su falta de precio no prueba nada.

Pero al escribirlo apareció que **el propio diag no podía sostener esa frase**:
filtraba el snapshot por `last_price > 0`, con lo cual «nadie la suscribe» y «la
suscribimos y el mercado no dio punta» salían idénticos. Es el mismo pecado, un
nivel más abajo. Ahora son **TRES** estados y no dos, en el diag y en la puerta:

    no_escucha   no está en `market_snapshot` → nadie la pide → no prueba NADA
    sin_punta    está y sin precio → la escuchamos y no vino → ESO es iliquidez
    con_precio   cotiza, y sabemos a cuánto

Y `pedible` ahora mira **estar en el snapshot**, no el adhoc: el motor puede
suscribir una pata desde el master o desde el universo de portfolio sin ningún
adhoc, así que lo que prueba que la escuchamos es que la fila exista, no cómo se
pidió. Ofrecer «pedirla» sobre algo que ya se está escuchando sería un botón que
no cambia nada — justo lo que rompe la confianza en todos los demás.

#### 8 de los 44 no eran casos: los DÓLAR LINKED

D15E7, D30O6, D30S6, D31G6, D31M7, TZV27, TZV28 y TZVD8 salían en la lista y **no
tienen nada de malo**: un dólar linked se denomina en USD y **paga en pesos**, así
que cotizar en pesos es su definición, no un síntoma. No tiene pata en dólares ni
la va a tener.

Es exactamente el error que este mismo detector ya había cometido cuando marcaba
`alta` a 46 bonos sanos: *un detector que canta casos correctos enseña a ignorar
la lista*. Se excluyen — no se les baja la severidad, porque no hay nada que
mirar. Se reconocen por `ajuste`/`ajuste_alt` y, de respaldo, por `curva`: los
ejes son nullable a propósito y exigir solo el eje dejaría pasar a los que
todavía no se clasificaron.

**Queda la lista real en 36 casos, de los cuales 28 son accionables en el acto.**

Corrido de nuevo con el filtro puesto: **31 dólar linked excluidos** y la lista
en **20**. Son muchos más que los 8 que se veían en la primera tabla, y el motivo
importa: 8 tenían `curva = 'dolar_linked'` y los otros ~23 estaban escondidos
bajo `curva = 'soberanos'` / `'on_energia'` con el EJE en dólar linked — ONs
corporativas dólar linked, que son un montón. Mirar solo el nombre de la curva
habría dejado el 74% del ruido adentro.

Y de los 12 que tienen pata: **`no_escucha` 12, `sin_punta` 0**. Ni una sola está
siendo escuchada, así que de ninguna se puede afirmar hoy que no cotiza.

---

---

### 0.y EL NOMBRE NO ES LA IDENTIDAD — las patas por FICHA (2026-08-19)

> *«Justo los BOPREAL no es que cambia la D al final, cambian al principio. Pero
> también puede intentar buscar por maturity… o sea hay maneras de decir bueno a
> ver, el ticker en ARS cuál es el underlying acá, y después decir che ¿este
> underlying está en otro CCY? Y también reforzar con underlying más igual
> maturity.»* (user)

El user lo cazó abriendo Manager → Títulos · Instrumentos, que es el discovery
crudo de Primary:

    MERV - XMEV - BPOA7 - CI     ARS   BOPREAL S. 1 A VTO31/10/27 U$S CG
    MERV - XMEV - BPA7D - 24hs   USD   BOPREAL S. 1 A VTO31/10/27 U$S CG
    MERV - XMEV - BPA7C - CI     USD   BOPREAL S. 1 A VTO31/10/27 U$S CG

**La pata en pesos se llama `BPOA7` y la de dólares `BPA7D`: se cae la O del
medio.** Las dos convenciones que `core/especies` conocía fallan las dos —el
sufijo D/C y el par O/D de las ONs— y `RE_ESPECIE` encima *acierta a medias*, que
es lo peor: clasifica `BPA7D` como base `BPA7`, especie MEP, y lo cuelga de un
bono que no existe. El par nunca se arma y los 6 BOPREALes salen como «no tiene
pata en dólares» teniéndola.

#### Por qué NO se le agrega un caso a la regex

Sería la tercera convención escrita a mano, y la cuarta la vamos a descubrir
igual que ésta: tarde, y porque un bono se veía raro en la pantalla. El problema
de fondo es que **estábamos usando el nombre como identidad**, y el nombre es una
convención del emisor, no un dato.

**Primary ya dice de qué bono es cada símbolo.** El discovery guarda `underlying`
y `maturity` desde siempre y nadie los estaba usando:

    el ticker en pesos → su ficha (underlying, maturity)
    esa misma ficha    → ¿qué otros símbolos la tienen, en otra moneda?

Eso es un **JOIN EXACTO sobre dos campos**, no una heurística: o la ficha coincide
o no. Anda para BOPREAL, para AL30 y para la convención que se les ocurra
mañana, porque no mira el nombre. Vive en `core/especies.hermanas_por_ficha` y lo
usan la puerta del agente y el sembrador — una sola implementación.

⚠️ **SOLO los símbolos `MERV - XMEV - …`, y no es un detalle de formato.** Primary
publica los mismos papeles dos veces y la forma corta trae un `underlying`
GENÉRICO:

    MERV - XMEV - BPOA7 - CI   →  "BOPREAL S. 1 A VTO31/10/27 U$S CG"   ← sirve
    BPOA7/CI                   →  "Bopreales - Bonos BCRA"              ← NO

Con la genérica, los 6 BOPREALes comparten ficha y cada uno hereda las patas de
los otros cinco. **Un emparejamiento silencioso y equivocado es peor que
ninguno**: el motor pediría el precio de otro bono y la fila se llenaría con un
número perfectamente creíble. Por lo mismo hay un tope (`MAX_POR_FICHA`): una
ficha que agrupa más de 12 símbolos no se usa — un bono tiene a lo sumo 3
especies × 2 plazos.

#### Y el eslabón que faltaba: que el sembrador pueda escribirlo

Encontrar la pata y no poder sembrarla habría dejado un diagnóstico sin
consecuencia. `patas_de` agrupa por nombre —que es justo lo que no coincide— así
que ahora acepta `extra`: símbolos que YA sabemos de este bono por otra vía.
Entran forzados al grupo, pero **la moneda y la especie se siguen sacando del
sufijo**: `BPA7D` termina en D y el clasificador de siempre acierta. Lo único que
estaba roto era *a qué bono pertenece*, no *qué es*.

#### ¿Y ACÁ SÍ VA UN LLM? — no, y el motivo es la parte que sirve

La pregunta del user es la correcta y la respuesta es **no**. Esto es una
igualdad exacta entre dos campos: tiene UNA respuesta y la sabe el mercado. Un
modelo acá sería más lento, costaría plata, daría distinto entre corridas y —lo
peor— **sonaría igual de convencido cuando el dato no alcanza**. El proyecto ya
tiene la regla escrita al revés: las skills declaran si usan IA justamente porque
la mayoría son funciones y hay que poder verlo.

**Dónde SÍ tendría trabajo**: cuando las dos fichas no son idénticas —el mismo
bono escrito distinto en la pata de pesos y en la de dólares—. Ahí una igualdad
exacta no empareja y un modelo podría decir «son el mismo». Pero eso **hay que
medirlo antes** (REGLA #2): si el join exacto cubre todo, meter un modelo es puro
costo y un riesgo nuevo. Por eso el diag cuenta `por_ficha` aparte — el número
decide, no la intuición.

#### Y el número decidió: **`sin_pata` pasó de 8 a CERO**

    sembrada          12   60,0%
    solo_en_primary    0    0,0%
    por_ficha          8   40,0%   ← los que ninguna regla de nombre encuentra
    sin_pata           0    0,0%

**El join exacto cubrió el 100%.** Los 8 que el sistema daba por «no tiene pata en
dólares» la tenían: los 6 BOPREAL más NDT25 (→ `NDT5C`) y SFD34 (→ `SFD4C`), que
tampoco siguen ninguna convención de sufijo.

Y con eso queda contestada la pregunta del LLM **con un número y no con una
opinión**: no hay una sola ficha que no empareje exacto, así que un modelo no
tendría ningún caso que resolver. Si algún día aparece uno, el diag lo va a
mostrar como `sin_pata` — y recién ahí se discute.

#### El bug de esa misma corrida: emparejar bien y elegir mal

Los 8 emparejaron perfecto y **los 8 devolvieron la pata en CABLE** (`BPA7C` en
vez de `BPA7D`). `hermanas_por_ficha` ordenaba por plazo y después alfabético, y
`BPA7C` < `BPA7D`. El abecedario no es un criterio de mercado.

Peor: el criterio bueno ya existía —`preferencia`, MEP antes que cable porque es
la que mira la mesa— y estaba escrito **tres veces**: ahí, una copia en la puerta
del agente y otra en el diag, estas dos con un `sorted()` alfabético. O sea que
el diag mostraba una pata y el agente iba a pedir otra.

Ahora hay UNA: `core.especies.mejor`, y las tres la usan. *Emparejar bien y
elegir mal no se ve distinto de emparejar mal* — y cable y MEP son cosas
distintas, cosa que `CLAUDE.md` ya advertía.

---

---

### 0.aa LOS DOS PATRONES, EN LA ARQUITECTURA (2026-08-19)

> *«Hacelo bien hecho, que quede en la arquitectura, que este patrón sea general
> y no solo para esta feature y después le pase a otra cosa. Que refuerce la
> inteligencia de este modelo.»* (user)

Tenía razón: en cuatro días el MISMO error apareció tres veces, en tres módulos
distintos, y las tres se arregló el caso y no la clase. Acá quedan los dos
patrones como código reusable, no como prosa en un doc.

#### PATRÓN A — la identidad no es el nombre → `core/pareo.py`

Cada tanto hay que decir «este registro y aquel son la misma cosa» sin tener una
clave que los una. Ya pasó **cuatro** veces y las cuatro se resolvieron por
separado: las patas de un bono, el rebautizo de Aunesa (`herencia`), el emisor de
1816, y los tres lugares donde vive un símbolo.

La tentación siempre es mirar el string, y siempre falla igual, porque **el
nombre es una convención de quien lo emitió, no un dato**:

    BPOA7  →  BP[O]A7 + D  →  BPA7D      ← se cae una letra del MEDIO
    NDT25  →  NDT[2]5 + D  →  NDT5D
    AL30   →  AL30   + D  →  AL30D       ← este anda, y por casualidad

El ticker está topeado en **5 caracteres**: `AL30` tiene 4, le entra la D y la
regla de sufijo funciona; cualquier base de 5 la rompe y la va a romper siempre.
*El nombre literalmente no tiene lugar para ser la identidad.*

`core/pareo.hermanas()` empareja por FICHA —los atributos que da la fuente
autoritativa— con **las cuatro guardas adentro**, que son la mitad del módulo
porque *emparejar mal es peor que no emparejar*:

    1. solo la fuente AUTORITATIVA   la forma corta de Primary trae un underlying
                                     genérico; con ella los 6 BOPREALes comparten
                                     ficha y cada uno hereda las patas de los otros
    2. ficha COMPLETA                media ficha matchea contra todas las demás
                                     fichas incompletas
    3. tope de grupo                 una ficha que agrupa de más es genérica
    4. «no pude» ≠ «no existe»       la regla del AO29 (§0.v)

`core/especies` pasó a ser **un caso de uso**: declara qué campos forman la ficha
de un instrumento y con qué criterio se ordenan las patas, y nada más. Los 15
tests que ya existían siguen pasando sin tocar uno — la conducta es idéntica, lo
que cambió es que las guardas ya no dependen de que el próximo se acuerde.

#### PATRÓN B — dos copias sin árbitro se separan → `core/duplicados.py`

    2026-08-19  el símbolo del bono vivía en la COLUMNA y en el BLOB. El motor
                escribía leyendo el blob, la vista buscaba por la columna.
                Divergieron en 2 de 229 y esos bonos salían enteros en `--` **con
                el precio existiendo**.
    2026-08-19  `preferencia` (MEP antes que cable) estaba escrita TRES veces. Las
                tres eligieron distinto: el diag mostraba una pata y el agente iba
                a pedir otra.
    2026-08-15  el renombre dejó el blob con el significado invertido. Cuatro días
                sin que pasara nada — hasta que pasó.

El problema **no** es tener el dato dos veces: a veces hace falta (un blob que
leen 500 lugares no se migra de un día para el otro). El problema es **no
declarar quién manda y que nadie mire si siguen diciendo lo mismo**.

`core/duplicados.DUPLICADOS` es el registro **declarado** —del esquema no se puede
deducir que dos columnas guardan «lo mismo»— y cada entrada dice qué dato es,
dónde vive cada copia, **quién es el árbitro** y **qué se rompe** si divergen.
Hoy son cuatro; sumar uno son cinco líneas y una query.

#### Y lo que lo vuelve inteligencia y no documentación: el agente lo mira

`detectar_dato_partido` corre en el job nocturno (no depende del mercado: a las
23:30 es tan cierto como a las 11) y lo canta en ENCONTRÓ como cualquier
hallazgo. **`alta` sin dudar**: si dos copias difieren, algo está leyendo el valor
incorrecto ahora mismo — lo único que no sabemos es quién.

Lo que lo hace imposible de ver a mano es justo lo que lo hace peligroso: **cuando
dos copias se separan no falla nada.** No hay excepción, no hay log, cada mitad
sigue siendo coherente, y el sistema contesta con seguridad usando la equivocada.

⚠️ **NO se automatiza, y es una decisión.** Elegir la del árbitro y pisar la otra
parece obvio y no lo es: puede que la equivocada sea la del árbitro, y pisar
**borra la evidencia de que hubo una divergencia**. El agente muestra los dos
valores y decide una persona.

Y lo que no se pudo chequear **se canta** (`no_pude_chequear`, media): un
duplicado sin mirar se leería igual que uno sano, que es exactamente la forma de
mentir que estos dos módulos persiguen.

#### LA PRIMERA CORRIDA: encontró dos, y el AO29 seguía partido

    ⚠ 2 dato(s) partido(s)
       simbolo_columna_vs_blob      2 casos
       simbolo_master_vs_especies   1 caso

**El bug que tardó cuatro días en descubrirse apareció en la primera pasada.** Y
con un matiz que importa: `_ALIAS_DEL_BLOB` (§0.u) hizo que los LECTORES se
pongan de acuerdo —la columna gana al leer— pero **el dato sigue partido en la
base**. El parche es una capa de traducción, no un arreglo: cualquier cosa que
lea `data` sin pasar por `curvas_sql` todavía se lleva el valor viejo.

O sea que la divergencia estaba viva, nadie la veía, y el sistema andaba bien por
un parche que había que recordar. Exactamente la clase de deuda que este detector
existe para no dejar acumular.

#### Y NO TODOS SE ARREGLAN IGUAL — eso también se declara

Sincronizar dos copias parece siempre lo mismo y no lo es. Cada duplicado declara
si tiene arreglo **mecánico** y, si no, por qué:

    simbolo_columna_vs_blob        UPDATE  → se le escribe al blob el valor de la
                                            columna. NO es un renombre: la clave
                                            sigue llamándose `ticker` (la leen
                                            ~500 lugares), solo cambia el valor.
    ticker_corto_columna_vs_blob   UPDATE  → ídem.
    simbolo_master_vs_especies     NO      → el motor arma su universo AL
                                            ARRANCAR: el UPDATE se verifica en
                                            verde y la pantalla no cambia hasta la
                                            noche (§0.v).
    emisor_curvas_vs_assets        NO      → el árbitro es 1816 y quien escribe
                                            las dos tablas es `jobs.ficha_1816`.
                                            Escribir a mano dejaría las copias
                                            coincidiendo en un valor que ninguna
                                            fuente respalda — peor que la
                                            divergencia, porque además la esconde.

`scripts/fix_dato_partido` (dry-run por default, scopeado al WHERE de la
detección, idempotente) aplica solo los mecánicos y **releé al final**: «apliqué»
no es «pasó». Los otros los nombra y no los toca.

---

---

### 0.ab EL AGENTE MANDA MENSAJES — y después chequea si sirvió (2026-08-19/20)

Dos pedidos del user que resultaron ser el mismo: *«necesito que el agente mejore
lo de enviar mensajes al resto de usuarios… debería ser una feature de
ENVIAR_MENSAJE»* y *«necesito que este agente entienda cuándo hizo algo bien no
solamente porque yo le puse acertó, si no porque al otro día puede detectar que
los cambios realmente tuvieron consistencia»*.

Son la misma cosa porque las dos preguntan lo mismo: **¿llegó de verdad?** Un
mensaje que nadie ve y un arreglo que se deshace solo fallan igual de callados.

#### (1) MANDAR es una capacidad, no un pedazo de otra cosa

Estaba metido adentro del control de carteras sin nivel 1: para avisar de otra
cosa había que copiar el código. Ahora vive solo en
**`api/services/av_agent_mensajes.py`** y cualquier detector o job lo usa:

    directorio()   quién puede recibir            enviar()        uno
    de_quien_es()  a qué operador le toca         enviar_tabla()  con grilla
    existe()       ¿ese mail está dado de alta?   enviar_muchos() en lote

`MAX_DESTINATARIOS = 60` es un freno a propósito: un lote más grande no es un
aviso, es un incidente.

**Y no llegaba por TRES bugs encadenados**, que es la parte que vale la pena
recordar:

  1. el índice único ignoraba a **quién** iba dirigido → el segundo destinatario
     pisaba al primero en silencio;
  2. la escritura devolvía «0 filas» y nadie miraba ese 0;
  3. **la ruta `/api/avisos` no existía en el frontend.** Ese era el de verdad:
     `MisAvisos` nunca había funcionado y el `catch` se comía el 404.

La lección no es «había bugs». Es que **los tres eran mudos**: cada capa daba OK
por su cuenta. Por eso ahora la escritura mira lo que devuelve y hay un test que
exige que la ruta exista.

#### (2) El aviso de SALDOS — la primera cosa que sabe contar

Todos los días hábiles 16:45, cada operador recibe los saldos de SUS comitentes
(`jobs/saldos_a_operadores.py`). Cuatro decisiones:

  · **La fuente es la MISMA que la pantalla.** El job no tiene una sola query
    propia: llama a `titulos_negativos.saldos_del_dia()`, que es lo que sirve la
    vista SALDOS DE CUENTAS — con sus cuentas ocultas, sus excluidas y su último
    día real. La primera versión sí tenía query propia y ya divergía en tres
    cosas (usaba `current_date` en vez del último día cargado, no sacaba CDC/OTC,
    tenía su propio mínimo). *Un aviso que contradice a la pantalla no avisa: abre
    una discusión sobre cuál de los dos miente.* Hay un test que falla si al job
    le vuelve a aparecer un `cur.execute`.
  · **Cuatro cuadrantes, top 5 cada uno**: ARS a la izquierda, USD a la derecha,
    positivos arriba y descubiertos abajo. Es un aviso para actuar, no un reporte:
    lo que importa son las puntas.
  · **ARS y USD, nada más.** USDL (link) y USDC (cable) son otra cosa y sumarlos
    adentro de la columna USD mezclaría peras con manzanas. **No desaparecen**: se
    cuentan aparte y el aviso lo dice, igual que dice cuántas quedaron fuera del
    top. Truncar en silencio se lee como «esto es todo lo que hay».
  · **El título cuenta lo que la tabla MUESTRA.** Decía «46 en descubierto» arriba
    de cinco filas porque contaba todo lo que había llegado. Un título que no
    cierra con lo de abajo hace dudar de los dos.

El operador **tilda fila por fila** y eso persiste con su hora. Vale un día: al
abrir el mercado siguiente los saldos son otros y el tilde de ayer no significa
nada.

**El modal se abre para ACTUAR, no para leer** (user, 2026-08-20). Arriba van dos
líneas y nada más —quién manda y qué pasa— y abajo, directo, las tablas:

    AV AGENT — TENÉS UN MENSAJE NUEVO!
    Tenés estos saldos y el mercado ya cierra.

Antes había tres bloques de texto antes de la grilla: un título que contaba («46
cuenta(s) tuyas EN DESCUBIERTO»), el contador de pendientes y un párrafo
explicando cómo usar la tabla. **Los números no se borraron: bajaron al pie**,
que es donde se miran cuando ya se decidió algo. Lo que se sacó es el texto de
arriba, no la información — el detalle sigue diciendo cuántas quedaron afuera y
por cuál de los dos motivos.

#### Y TIENE QUE APARECER SOLO — el reloj no alcanza (2026-08-20)

*«Hay que actualizar la página, es decir inviable… hay gente que deja esto de
fondo.»* Había un poll de 5 minutos y aun así el aviso no salía hasta recargar.

**El motivo no es la app: el navegador frena los timers de una pestaña que está
en segundo plano.** Chrome los baja a uno por minuto y, pasado un rato sin
mirarla, puede congelarlos del todo. O sea que **justo en el caso que importa**
—la app abierta atrás toda la tarde— el reloj es lo primero que deja de andar.

Regla que queda, y no es solo de este componente: **para algo que tiene que
llegar, el reloj es el respaldo, no el mecanismo.** Se despierta por EVENTO —
volver a la pestaña, volver a la ventana, recuperar internet, el atrás del
navegador— y mientras está oculta no pide nada: el timer no iba a correr igual,
así que en vez de pelearle al navegador se apaga y se recupera al volver. Sale
más barato en requests que el poll de antes **y llega antes**.

Y con la app de fondo lo único que se ve de una pestaña es su TÍTULO: ahí va una
marca `(!)` mientras haya algo esperando, que se saca sola al volver.

#### (3) SEGUIMIENTO — el tiempo como evidencia

`api/services/av_agent_seguimiento.py`. Cuando algo se marca como hecho, se
**anota**; y durante `DIAS_DE_PRUEBA = 5` se vuelve a mirar si el hallazgo
reapareció. Si no volvió, el voto se emite solo con origen `verificado`.

Eso agrega un tercer origen al eval set, y **el origen importa**:

    humano      alguien miró y dijo si la causa era la correcta
    derivado    alguien aplicó la acción propuesta (aprobación, no verificación)
    verificado  pasaron los días y el problema no volvió

La compuerta de autonomía cuenta **`humano` + `verificado`** y deja afuera a
`derivado`: aprobar una acción es decir «probemos», no «funcionó». Meterlos en la
misma bolsa haría subir el número justo cuando menos evidencia hay.

Dos guardas: `revisar(None)` —«no pude mirar»— devuelve error y **nunca** cambia
un veredicto (§0.v: no se concluye «no existe» desde una consulta que no corrió),
y la recurrencia queda anotada, así que si el mismo error vuelve el agente lo
sabe en vez de descubrirlo de nuevo.

---

---

### 0.ac LOS LOGS DE LOS MOTORES (2026-08-20)

*«Es fundamental que el agente tenga presente los logs de los motores
constantemente»* (user, 2026-08-19).

**Lo que el agente ya sabía y lo que no.** `motor_caido` (§0.r) mira si el motor
PRODUCE: si su tabla de salida dejó de escribir dentro de su ventana, lo canta.
Eso deja afuera al motor que **produce y a la vez se está rompiendo** —
reconexiones, suscripciones rechazadas, excepciones que alguien atrapó y siguió.
Nada de eso llega a la base: vive en el log y nadie lo lee.

#### La lectura estaba escrita, en el lugar donde nadie podía usarla

`journalctl` ya se leía… **adentro de `api/routers/manager/logs.py`**, o sea que
la única forma de mirar era que una persona abriera la pantalla. Un service no
importa un router (regla de capas), así que para el agente la única salida
habría sido copiar el `subprocess` — dos formas de leer lo mismo, que se separan
solas (REGLA #9).

Ahora la lectura vive en **`api/services/logs_sistema.py`** y el router es un
cliente más. Lo que el router devuelve **no cambió un nombre de campo**: la
pantalla de LOGS lee `ts_epoch`/`priority`/`servicio`/`message` y renombrarlos la
habría dejado en blanco sin que fallara nada. Hay un test que los congela.

#### Los últimos N renglones no sirven para vigilar

Un motor que se reconecta mil veces deja mil líneas casi iguales; las últimas 20
son la misma. La pregunta útil no es «¿qué dijo recién?» sino **«¿qué viene
diciendo, y cuántas veces?»**.

Por eso se **normaliza**: se le sacan al mensaje la fecha, la hora, el símbolo,
el id y los números — lo que cambia en cada repetición y no ayuda a identificar
el problema — y queda la FORMA de la frase, que es su identidad real. Mil líneas
colapsan en un patrón con su cuenta. Un traceback se agrupa por su **última**
línea (la que nombra la excepción): agrupar por la primera daría un grupo por
traceback y el resumen tendría el mismo largo que el log.

Y cada grupo guarda **la ventana**, no solo la cuenta: *300 repeticiones en dos
minutos es un motor peleando contra algo; las mismas 300 repartidas en un día son
ruido de fondo* — y no se responden igual.

#### Todavía NO avisa solo, y eso es la decisión, no un pendiente

Para que el agente cante un problema hay que fijar umbrales: cuántas veces, de
qué nivel, en cuánto tiempo. **Ninguno de esos números se puede elegir sin haber
mirado nunca los logs de producción** (REGLA #2), y un detector mal calibrado
grita todos los días hasta que alguien lo silencia — peor que no tenerlo, porque
además enseña a ignorar la pantalla donde vive.

Así que primero se midió (el diag ya cumplió y se borró): el reparto era
por motor, lo grave aparte (aunque haya salido una sola vez) y los patrones más
repetidos con su ventana. Con esos números se elige el umbral, y recién ahí el
detector.

**Y si no se puede leer, se dice.** En un host sin systemd, o sin permiso sobre
el journal, devolver lista vacía se lee EXACTAMENTE igual que «no hay errores».
Es el mismo modo de falla de §0.s y el que más caro sale: la vigilancia diría
verde para siempre. `disponible: False` con el motivo, y el diag lo imprime como
lo que es — «no sé», no «está todo bien».

#### LA PRIMERA CORRIDA DIO CERO — y el cero era el detector (2026-08-20)

14 motores, 24 horas, **ninguna línea de warn o peor**. Sospechoso, y al medirlo
—en el repo, sin tocar el Droplet— eran **tres capas tapando lo mismo**:

  1. **Ninguna unit de systemd declara nivel** (`SyslogLevelPrefix`), así que
     **journald marca TODAS las líneas como `info`**, también las de
     `logger.error`. Pedirle `-p warning` devuelve vacío *siempre*.
  2. **9 de 13 motores formateaban con `"%(asctime)s %(message)s"`** — sin el
     nombre del nivel. El texto tampoco lo decía.
  3. **`engines/valores.py` (motor_rofex) no configuraba logging en absoluto.**
     Sin `basicConfig` el logger raíz queda sin handlers y en WARNING: sus
     `logger.info` se **descartaban** y sus `logger.error` salían por el handler
     de último recurso, a stderr y sin fecha. Es el feed de precios de la mesa.

Juntando las tres, **un error de un motor era indistinguible de una línea
normal** — para el agente y para una persona leyendo `journalctl`. No es que
faltara un detector: *no había forma de encontrar un error aunque lo buscaras a
mano*. Y como un log ilegible no falla, esto podía durar para siempre.

**Las dos mitades del arreglo:**

  · **El nivel viaja EN EL TEXTO** (`core/logs.py`, formato único con
    `%(levelname)s` y `force=True`). Es lo único que sobrevive a journald, a
    `tail`, a un `grep` y a un copiar-pegar en un chat.
  · **El lector no le cree a journald**: saca el nivel del texto y se queda con
    **lo peor** entre ese y el de journald — hay servicios que sí mandan el nivel
    de verdad, y creerle solo al texto sería cambiar un punto ciego por otro. El
    filtro va como `--grep` del lado del servidor: sin eso habría que traerse 24 h
    de logs de 14 motores para descartar el 99%.

Dos detalles que hacen que esto no vuelva a esconderse:

  · Las palabras se buscan **en MAYÚSCULAS**, que es lo que imprime
    `%(levelname)s`. Sin eso, «0 errores» y «sin warnings» —las líneas que dicen
    que todo salió bien— entrarían como problemas y el detector nacería gritando.
  · El diag arranca con una **muestra de las últimas líneas de cualquier nivel** y
    dice cuánto tiempo cubren y cuántas declaran su nivel. Eso separa de una las
    dos lecturas posibles de un cero: *«están tranquilos»* de *«no se puede
    encontrar nada»*. **Un cero sin esa muestra no prueba nada.**

⚠️ **El formato nuevo NO se ve hasta reiniciar los motores**, y el deploy no los
toca a propósito. El cron los prende y apaga de lunes a viernes, así que entra
solo en el próximo arranque. Hasta entonces el diag lo canta: *«ninguna línea
dice su nivel: este cero no prueba que no haya errores, prueba que no se pueden
encontrar»*.

#### EL DETECTOR, CALIBRADO CON LA MEDICIÓN (2026-08-20)

Con el lector arreglado, 24 h sobre 14 motores dieron **171 líneas en 6 patrones**:

    ×76 en 3 min    motor_cedears     REST exception JSONDecodeError
    ×91 en 6.7 h    motor_options     Expiries configuradas ya vencidas
    ×1              motor_portfolio   ERROR símbolo inexistente, purgo y sigo
    ×1 ×1 ×1        varios            warn sueltos, todos auto-resueltos

Y ahí se ve lo que no se podía saber antes de medir: **la cuenta sola no
alcanza.** 76 y 91 son números parecidos y son dos problemas distintos — *76 en
tres minutos es algo rompiéndose ahora en loop; 91 repartidas en siete horas es
una configuración rota desde hace días que nadie mira*. Por eso son **dos reglas
con nombres propios** y no un umbral con dos valores:

| regla | cuándo | severidad |
|---|---|---|
| `rafaga` | ≥30 veces en ≤15 min | alta |
| `machaca` | ≥20 veces en la ventana | alta si es error, media si no |
| `error_de_motor` | nivel error o peor, aunque sea una vez | media |
| `no_pude_leer` | el journal no se pudo leer | media |

**Los warn sueltos se descartan a propósito.** En la medición eran tres y los
tres se anunciaban resolviéndose solos («reconectando (intento 1)», «purgo y
resuscribo sin ellos»). Reportar eso enseña a cerrar la pantalla sin leerla, y
con ella se van los avisos que sí importan. El diag los sigue mostrando cuando
alguien va a buscarlos.

Resultado sobre esos mismos datos: **6 patrones → 3 hallazgos.**

Dos cosas que salieron de escribir los tests con los casos reales, y que no se
habrían visto de otro modo:

  · **`WARNING · … REST status=ERROR` se clasificaba como ERROR.** La palabra
    estaba en el *cuerpo* del mensaje, no en el nivel. El nivel es un PREFIJO —lo
    pone `%(levelname)s` después del timestamp— y buscarlo suelto confunde *el
    nivel del mensaje* con *el tema del mensaje*. Ahora va anclado. Misma familia
    que «0 errores», encontrada con datos de producción.
  · **La forma clasifica, el nivel pesa.** La primera versión probaba
    `rafaga`/`machaca` antes que el nivel, así que un ERROR repetido 40 veces
    caía en `machaca` con severidad **media**: el que más repetía era el que
    menos se veía.

Y de yapa, al reescribir el guardián de reglas (pasó de un regex sobre
`_hallazgo(...)` a recorrer el AST) apareció que **`tabla_quieta` emitía
`sin_escribir` sin declararla**: sus votos se contaban sin poder decir por qué
causa acertó. El regex solo veía UNA de las formas de escribir un detector, así
que los cinco que arman el dict inline pasaban en verde sin haber sido mirados.

---

---

### 0.ad LOS DE AFUERA SE CAEN (2026-08-20)

*«Esto es una funcionalidad que la vi de milagro… sí o sí el agente tiene que
detectar cuándo esto está caído, avisar y dar el motivo exacto»* (user, con
Aunesa devolviendo HTTP 500 en su login mientras lo escribía).

**Lo que fallaba no era la detección.** La vista de Tesorería ya captura el error
de Aunesa, degrada bien —arma la vista con lo que hay y dice qué falta— y muestra
el mensaje exacto en un cartel rojo. Está bien hecho. Lo que falla es **cuándo**:
ese cartel existe *solo mientras alguien tiene la pantalla abierta*. Si nadie
entra, el back office puede pasar la mañana entera creyendo que el saldo del día
está completo cuando le falta la mitad.

Es el mismo patrón que ya se corrigió con SALUD (§0.l), con la latencia (§0.q) y
con los detectores que solo imprimían en el log (§0.t): **una señal que te espera
no es un aviso**.

#### No se pregunta: se deja rastro

La tentación es pegarle cada 5 minutos a cada proveedor. No, por dos razones:

  · **1816 cobra por llamada.** Un health check cada 5 minutos se come la cuota
    del día antes del mediodía.
  · **Un health check puede mentir.** Un proveedor que contesta el ping y
    devuelve 500 en el endpoint que usamos de verdad sale VERDE.

Así que al revés: **cada llamada real deja su rastro** (`core/proveedores.anotar`,
llamado desde adentro del cliente HTTP). Los daemons ya le pegan a Aunesa todo el
tiempo, así que una caída queda registrada en segundos, sin una sola llamada
extra y con el error del endpoint que importa.

Se escribe **solo cuando falla**, y como mucho una vez por minuto por proceso: un
proveedor sano no cuesta ni una escritura, y una caída de una hora no son miles
de filas diciendo lo mismo. La recuperación sí se escribe — es lo que apaga el
aviso rápido en vez de esperar a que venza.

⚠️ Y **el hallazgo VENCE** (20 min). Nadie apaga el registro cuando el proveedor
se recupera: simplemente dejan de anotarse fallos. Sin ventana, un 500 de la
semana pasada seguiría en pantalla para siempre (la lección de §0.u).

#### El aviso dice TRES cosas, y las tres hacen falta

    1. QUIÉN se cayó       Aunesa (el custodio)
    2. QUÉ deja de andar   Tesorería sin los movimientos del día…
    3. EL MOTIVO EXACTO    HTTPError: 500 Server Error for url: …/login

La 3 es la que lo hace accionable: sin el error textual no se distingue *«se cayó
el proveedor»* de *«se nos vencieron las credenciales»*, que se resuelven en
lugares distintos y por personas distintas. La 2 es la que evita que el que lo
lee tenga que averiguar si eso le arruina el día — por eso cada proveedor declara
su `rompe` en el catálogo, y hay un test que lo exige.

#### DESDE Y HASTA QUÉ HORA el problema es real

Pedido del user en la misma corrida: *«es fundamental entender desde qué hora
hasta qué hora el error es real para cada motor»*. Y al mirarlo apareció un
hueco: **`diagnostico._estado` devuelve `sin_datos` ANTES de mirar la ventana**,
así que un motor de mercado que nunca escribió salía en ALTA a las 3 de la mañana
y los sábados. `fuera_rueda` ya estaba cubierto; éste no, porque nunca llegaba a
compararse contra un umbral.

Ahora cada pieza viaja con su `ventana` y **el hallazgo lo dice en palabras**:

    CUÁNDO ES REAL: corre de 10:00 a 17:05 ART, de lunes a viernes;
                    ahora son las 14:32 ART y está DENTRO de su ventana:
                    no es que esté apagado.

El horario se **lee** de `diagnostico._APERTURA`/`_CIERRE`, no se escribe a mano:
un «10 a 17:05» tipeado en el texto es una segunda verdad que se desactualiza
sola el día que muevan el horario del mercado (REGLA #9), y hay un test que lo
impide.

#### Y otro reloj doble, encontrado por los tests

`_recien_abrio` **decía en su docstring** que la hora sale del árbol y el código
llamaba a `ahora_ar()`. Dos relojes para juzgar UNA foto. Se descubrió porque
tres tests fallaban **solo entre las 10:00 y las 10:30 ART** — media hora por día
de rojo intermitente sin causa aparente. Es el mismo bug que tenía
`salud._chequeo_job` y por el mismo motivo, así que la regla ya se puede escribir
sola: **cuando algo se evalúa contra una foto, el tiempo tiene que salir de la
foto**.

---

---

### 0.ai UN AVISO QUE NO SE PUEDE VOTAR NO SIRVE (2026-08-20)

*«No le pone hora ni nada… si vas a decir eso, para acertar me tenés que mostrar
que falló en horarios donde debería funcionar; si no, no tiene validez. Si tenés
los logs de todo, o sea, es clarito»* (user).

Antes, la fila de ENCONTRÓ decía:

    motor_rofex (trades)   SIN PRODUCIR   motor de MERCADOS: hace rato que no produce
                                          ¿ACERTÓ?  ✔ SÍ   ✖ NO

Ahora:

    motor_rofex (trades)   SIN PRODUCIR   sin producir hace 40 min · esperado live
                                          · 14:22, en ventana

**El detalle de pantalla que lo explica todo, y que yo no había mirado: el botón
¿ACERTÓ? está en la FILA, y la fila muestra solo el `motivo`.** Toda la evidencia
—la ventana, la hora, la cadencia esperada— estaba en la evidencia, una pantalla
más abajo. O sea que se pedía un voto sobre una frase sin un solo número.

> **Un eval set alimentado así no mide la puntería del agente: mide la paciencia
> del que vota.** Y como el eval set es lo que habilita cada paso de autonomía
> (§0.f), una medición sucia acá contamina todo el roadmap.

Lo que entra en el motivo, y por qué cada cosa:

| | por qué |
|---|---|
| **cuánto hace** | sin eso no se distingue un tropiezo de algo roto desde ayer |
| **qué se esperaba** | sin el «debía», el voto lo emite solo quien ya se sabe la cadencia de memoria — al revés de para qué existe el aviso |
| **la hora + en ventana** | es literalmente lo que el user pidió: *mostrame que falló cuando debería estar funcionando* |

Y la VISTA (`MERCADOS`, `PORTFOLIOS`) salió del motivo: el nombre de la pieza ya
está en su columna y repetirlo gastaba los caracteres que necesita la prueba.

#### La prueba del log, adentro del aviso

*«Si tenés los logs de todo, es clarito.»* Un motor caído ahora trae **su última
línea de log** en la evidencia: `Último log 13:48: WebSocket desconectado`. Con
eso el voto se emite mirando, sin ir a `journalctl`. Solo para motores (un job no
tiene unidad de systemd propia) y **con freno de 10 minutos por unidad**: es un
subprocess por motor caído y el monitor corre cada 5 minutos.

#### La regla, congelada

`test_avisos_cortos.py` exige que **todo motivo de un hallazgo del sistema traiga
un número y una hora**. Es la versión chequeable de «si se pide un voto, en la
misma línea tiene que estar la evidencia». También se arregló `_humano`, que
mostraba «cada 0 min» para una tabla que escribe cada 30 s — justo el número que
hacía votable el hallazgo.

---

---

### 0.al ¿EL CRON DEL REPO ES EL QUE CORRE? (2026-08-20)

Salió de una pregunta del user que parecía trivial: *«¿ya está ok el aviso de
saldos a operadores de las 16:30, el automático?»*. Buscando la respuesta
aparecieron dos cosas.

**La chica**: está a las **16:45 ART** (`45 19 * * 1-5` UTC), no a las 16:30.

**La grande**: no había forma de contestar si corrió — y hay una razón concreta
por la que podría no haber corrido nunca.

#### El agujero

`deploy/crontab.txt` dice en su encabezado que es la **fuente de verdad**, y todo
el sistema le cree:

| quién | qué hace con el archivo |
|---|---|
| `jobs_catalogo` | lo parsea: es el catálogo de jobs |
| `salud` | arma un chequeo por línea (¿corrió cuando debía?) |
| `diagnostico_registry` | valida el inventario contra él |
| tab SKILLS | de ahí saca el horario de cada detector |

**Y nadie lo compara nunca con el crontab real de la máquina.** `deploy.sh` hace
`git pull`, `apply_schema` y reinicia la API — **no instala el crontab**. O sea
que agregar un cron al repo no lo pone a correr: hay que instalarlo a mano, y si
alguien se olvida, el job no existe.

> Es **REGLA #9(B) textual**: el mismo dato en dos lugares, sin árbitro y sin
> chequeo. Y falla del modo que este proyecto ya conoce de memoria: **no falla
> nada**. El archivo está bien, el código está bien, los tests pasan, el catálogo
> muestra el job, la pantalla del agente lo lista con su horario… y no corrió.
> Se descubre cuando alguien pregunta «¿esto funcionó?», que es literalmente
> cómo apareció.

#### Qué mira, y en las dos direcciones

    en el archivo y NO en la máquina  → el job NO CORRE y todos creen que sí
    en la máquina y NO en el archivo  → corre algo que el repo no declara: nadie
                                        lo revisa, y la próxima instalación del
                                        archivo se lo lleva puesto sin avisar

Corre con los detectores del sistema (`jobs/db_tamano`, de noche) y emite
`cron_desalineado`. **Si no puede leer el crontab lo DICE** en vez de callarse:
sin eso, un `crontab` que no está en el PATH devolvería «ninguno instalado» y el
detector cantaría los 40 jobs como caídos — o peor, se quedaría mudo. Es la regla
de §0.v otra vez: *no se concluye «no existe» desde una lectura que falló*.

Se compara la ORDEN, no el archivo: comentarios, `MAILTO=` y `PATH=` quedan
afuera, y los espacios se colapsan — pero **cambiar el horario sí es una
diferencia** y hay un test que lo exige, porque colapsar de más taparía justo lo
que hay que ver.

#### Y para contestarlo hoy: `scripts/diag_crontab`

Dos preguntas distintas, las dos en una pasada: **¿está instalado?** (repo vs
máquina) y **¿corrió?** (`manager.job_runs`, con las últimas corridas y su
error). Estar instalado y haber corrido no son lo mismo: puede fallar el lock del
`run_job.sh`, el venv o el propio job.

> ⚠️ **La primera versión del diag se inventó las columnas** (`job`,
> `duration_ms`, `error`). La tabla real es `tipo`/`status`/`started_at` + un
> `data` jsonb — REGLA #2 en vivo: lo cazó mirar `sql/schema.sql` antes de
> pushear, no un test.

---

---

### 0.an EL AuM NO SE ESCRIBIÓ Y EL AGENTE NO SUPO DECIR POR QUÉ (2026-08-20)

El diag del crontab (§0.al), en su segunda corrida, trajo esto:

    aum   ✖ 20/08 11:00 error · HTTPError: 500 Server Error for url: https://aca.aunesa.co…

**`aum` es `jobs/portafolio_backfill --diario`: el writer de `portafolio.tenencia`,
la fuente única del AuM.** Ese día no escribió. Es el incidente del 2026-08-07
otra vez —el backfill falla y nadie se entera— salvo que ahora se vio.

Y lo que importa es **por qué el agente no lo cantó con su causa**, porque las
dos piezas para hacerlo ya existían. Había DOS eslabones rotos, cada uno
suficiente para romper la cadena solo.

#### 1) El fallo no dejaba rastro

`jobs/aum.py` le pega a Aunesa con **su propio `requests.Session()`** — nunca
pasa por `core/aunesa`, que es donde vive el `proveedores.anotar()`. O sea que
para el detector de caídas ese 500 **no ocurrió**: sin rastro no hay
`proveedor_caido`, y sin eso no hay nada que correlacionar.

Son cuatro los clientes sueltos y estaban declarados en el propio comentario de
`core/dependencias` como deuda conocida. Migrarlos a `core/aunesa` sigue siendo
lo correcto y es otro trabajo; lo que se hizo hoy es que **dejen el rastro**:

  · **`jobs/aum.py`** engancha `proveedores.rastrear(_SESSION)` — el hook va en
    la SESSION, no en cada llamada, así una función nueva en ese módulo queda
    cubierta sin que nadie se acuerde. El throttle de `anotar` ya evita que las
    ~1800 requests del backfill escriban 1800 filas.
  · Los otros tres usan `requests` suelto → una línea `proveedores.mirar(resp)`
    en el login y en la llamada principal.

⚠️ **Y la guarda, que es lo que hace que no vuelva**: un test escanea el repo
buscando quién menciona `aca.aunesa.com` y **exige** que ese módulo pase por
`core/aunesa` o llame a `mirar`. Un cliente suelto más, mañana, deja de ser un
punto ciego silencioso y pasa a ser un test rojo. (Con su propio test de que el
escaneo sigue encontrando los cuatro — un parametrizado sobre una lista vacía
pasa en verde sin mirar nada.)

#### 2) La correlación no reconocía el nombre

SALUD nombra sus chequeos **`job:<label del crontab>`**, y ese label es libre:
`portafolio_diario` corre `jobs.portafolio_backfill`. El grafo de dependencias
se arma con nombres de MÓDULO, así que `de_quien_depende("job:portafolio_diario")`
devolvía **vacío** — y la pantalla seguía mostrando exactamente lo que §0.af vino
a arreglar:

    proveedor_caido   Aunesa no responde
    salud_job         portafolio_diario: la última corrida falló

La traducción label → módulos ya existía en `jobs_catalogo` (parsea el crontab).
Se **delega**, no se copia. Ahora resuelve también las cadenas: `negocio_chain`
es una línea con varios `-m jobs.x`, y si cualquiera le pega a un proveedor, la
corrida entera depende de ese proveedor.

> Las dos mitades del arreglo tienen la misma forma: **el dato ya estaba y nadie
> lo cruzaba.** El 500 lo vio `requests`, el mapeo lo tenía `jobs_catalogo`. Lo
> que faltaba era que cada uno se lo contara al otro.

#### Y `controles_datos`, que moría todas las noches

Importaba `core/ai_resumen`, borrado el 2026-08-19 con el copiloto. Corría los 20
controles y explotaba con `ModuleNotFoundError` **al final**: calculaba todo y no
persistía ni avisaba nada. No se reemplaza por otra IA — rige §0.k: *una tarea de
IA existe solo si alguien lee su salida*.

---

---

### 0.ap LAS PUERTAS: cuánto de lo que ve, puede resolver (2026-08-20)

**El paso de arquitectura que dictó el caso BOPREAL** (§0.am). Un hallazgo sin
arreglo posible no es un aviso: es una **pared**, y una pared que aparece todas
las ruedas enseña a ignorar la lista entera.

Pero para atacar paredes hay que poder **contarlas**, y no se podía:
`ACCION_POR_TIPO` decía `None` para **12 de 18 tipos**, mezclando cuatro cosas
que no se parecen en nada.

| se veía igual | pero es | ¿deuda? |
|---|---|---|
| `hueco_de_curva` | se acciona por OTRA vía (ME PREGUNTA) | no |
| `recuperado` · `respuesta` | una BUENA NOTICIA: no hay qué arreglar | no |
| `dato_partido` · `permiso_flojo` | se decidió NO automatizar, a propósito | no |
| `motor_caido` · `tabla_quieta` · `cron_desalineado` | se arregla AFUERA y **se podría cerrar** | **SÍ** |

Los cuatro eran la misma fila sin botón. Por eso **la única forma de saber que
`pata_equivocada` era la pared más cara fue que alguien se hartara de verla** —
después de 17 votos.

#### Qué cambia al declarar el motivo

  1. **La pantalla dice por qué no hay botón.** Una fila muda se lee como que el
     agente no sabe qué hacer con lo que él mismo encontró; una que dice «se
     arregla relanzando el job» es información.
  2. **El agente mide su propia cobertura** y ordena la deuda **por volumen** —
     cuánto ruido hace cada pared. Arreglar la que sale 48 veces vale más que la
     que sale una, y eso es un número, no una corazonada.

`SIN_PUERTA` se DECLARA (igual que `DE_QUIEN` y `DOMINIO_EVAL`) y **un test exige
que todo tipo sin acción esté ahí**: un tipo nuevo no puede volverse otra fila
muerta en silencio. Un tipo desconocido cae **del lado de la deuda** a propósito
— asumir «no se puede» escondería el hueco justo cuando nadie lo declaró.

⚠️ **La deuda NO es «todo lo sin puerta»**, y mezclarlos daba una cobertura
falsamente mala: nadie sabría cuál de los dos números mirar. Se agrupa por
**REGLA** y no por tipo, porque la regla es la unidad que se convierte en acción
(fue `pata_equivocada`, no `precio_moneda`).

    python -m scripts.diag_puertas

#### Lo que la medición ya sugiere

Con una mezcla parecida a la de la pantalla de hoy (**simulada, no medida contra
prod**): ~89% con puerta, y **toda la deuda restante es la misma capacidad** —
`sin_escribir`, `machaca` y `sin_producir` piden las tres *relanzar algo*.

> O sea que la próxima puerta no es de bonos: es **una sola capacidad —relanzar
> un job o un motor— que cierra la deuda entera de una**. Eso es exactamente lo
> que esta medición existe para decir, y es lo contrario de lo que uno elegiría
> mirando la pantalla, donde lo que abunda son los bonos.

El número real sale de correr el diag contra la corrida de prod. Y esa puerta
tiene su propia condición, ya escrita en `ACCION_POR_TIPO`: relanzar tiene
efectos afuera de `mercado.curvas`, así que **se habilita cuando el eval set diga
que el diagnóstico acierta** — primero ver, después simular, después escribir.

---

---

### 0.aq UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES (2026-08-20)

La medición de §0.ap contra prod dio **105 hallazgos · 84 con puerta (80%) · 20 de
deuda**, y la pared #1 fue clarísima:

    1. sin_escribir  ×8   [tabla_quieta]   falta: relanzar el job de esa tabla
       ej: ia.trazas, manager.role_audit, manager.salud_eventos

**Y las tres de ejemplo no tienen ningún job atrás.**

    ia.trazas             ← `core/ai.py`, una fila por CADA llamada al LLM
    manager.role_audit    ← `core/roles.py`, cuando alguien CAMBIA un rol
    manager.salud_eventos ← cuando un chequeo TRANSICIONA

Están quietas **porque no pasó nada**, no porque algo esté roto. Y no hay nada
que relanzar: el job no existe.

> **Casi construyo un botón «relanzar» para esa pared.** Habría sido una puerta a
> ninguna parte — peor que no tener puerta, porque encima promete. La medición
> sirvió para lo contrario de lo que uno espera: no me dijo qué construir, me
> dijo **que la pared más grande no era una pared**.

Es la otra mitad de §0.u. Allá una RÁFAGA se leía como ritmo; acá **un ritmo REAL
se lee como una obligación**: `ia.trazas` escribe casi todos los días porque se
usa IA casi todos los días — hasta el día que no, y ese día no hay nada roto.

#### Cómo se sabe, sin ninguna lista

Igual que `core/dependencias`: **la respuesta ya está en el código**. Quién le
hace el `INSERT` vive en un archivo, y la carpeta dice quién lo dispara.

| escritor | lo dispara | ¿se le exige? |
|---|---|---|
| `jobs/` · `engines/` | un RELOJ (cron, loop de motor) | **sí** |
| `core/` · `api/` | un EVENTO (una request, una acción) | no |

⚠️ **`no sé` NO es `evento`**: si no se encuentra el escritor, la tabla se sigue
exigiendo. Dejar de mirar algo porque no lo entendimos es cómo se pierde una
señal de verdad — sería el bug contrario, y peor. Y `scripts/` no cuenta: un
one-shot corrido a mano no es el escritor habitual de nada.

De yapa el hallazgo ahora trae **`relanzar`**: el módulo exacto, derivado y no
adivinado. Es justo lo que la puerta va a necesitar el día que exista.

> ⚠️ **El bug que casi lo deja midiendo la mitad, y lo cazó su test.** El mapa
> saltaba los archivos con `if "INSERT" not in texto` — y `motor_cedears`, que
> escribe solo por `pg_mirror`, no tiene esa palabra en ninguna parte: quedaba
> afuera **en silencio**. Un filtro de performance que achica lo medido sin
> avisar es el mismo bug que el `for r in app.routes` que veía 5 de 428 (§0.s).
> Con las dos formas: **de 47 a 158 tablas mapeadas**.

---

---

### 0.ar REHACER EL DÍA — con la prueba mirada, no con el error (2026-08-20)

El AuM del 2026-08-20 **no se escribió**: Aunesa devolvió HTTP 500 a las 11:00 y
`jobs/portafolio_backfill --diario` murió. El AuM, la Tenencia Valorizada y
Títulos en Alquiler mostraron el día anterior **sin ningún cartel**. Lo vimos de
casualidad, mirando otra cosa.

El user pidió las dos mitades, y la segunda es el diseño entero:

> *«que mismo tenga la skill o que lo pueda hacer (o sea, ejecutar fecha de hoy
> por haber detectado un error **y haber verificado 100% en la base que no hay
> fecha realmente** con lo que iba de hoy)»*

    el job falló     → una señal del PROCESO. Puede fallar y haber escrito.
    el dato no está  → un hecho sobre el RESULTADO. Es lo único que importa.

Un job que revienta al final después de escribir todo no necesita relanzarse;
uno que sale en verde sin escribir una fila, sí. Por eso **la precondición se
consulta contra la tabla y manda sobre el estado del job** — la misma ley que los
CONTRATOS de SALUD: se chequea el resultado, no el proceso.

**Las cuatro guardas** (`api/services/av_agent_rehacer.py`):

1. **Sin evidencia no corre.** Si la fecha ya está, no se ejecuta nada.
2. **Por `run_job.sh`**: lock + timeout, el mismo que usa el cron (REGLA #4). Si
   la corrida anterior sigue viva, esta se saltea sola en vez de apilarse — el
   incidente de CPU del 2026-06-03.
3. **Solo jobs declarados** (`REHACIBLES`), cada uno con su tabla y su columna
   de fecha. Un `subprocess` con el comando abierto sería una consola remota.
4. **Se verifica releyendo la tabla.** Que el proceso salga 0 no prueba nada.

⚠️ **NO relanza motores.** Un motor en rueda le corta el feed de precios a la
mesa (regla del user, 2026-08-18) y eso no se decide desde un botón.

⚠️ **Y EL DÍA QUE LE TOCA NO ES HOY.** `--diario` snapshotea el hábil ANTERIOR.
Exigirle el día de hoy lo daría por faltante **todas las noches**, y un detector
que grita siempre enseña a ignorar la lista entera — la enfermedad que el agente
vino a curar. El día lo declara el job (`"dia": "habil_anterior"`) y lo resuelve
`fecha_objetivo()` **con el mismo reloj que usa el job**: si el que pregunta
calculara su propia fecha, entre las 00 y las 03 UTC diferirían un día y las dos
mitades seguirían siendo coherentes consigo mismas (REGLA #9).

Enchufado: control diario **`dia_sin_dato`** (16:30 UTC, cinco horas y media
después del job) → acción **`sistema.rehacer_dia`**. El control lee la MISMA
lista que el arreglo, así no puede cantar un faltante que la acción no sabe
rehacer. A mano: `python -m scripts.diag_rehacer` (solo mira) y `--rehacer`.

#### Y LO QUE LO HIZO INVISIBLE: cuatro módulos mudos

`jobs/aum`, `jobs/cashflow`, `jobs/sync_comitentes` y `api/services/aunesa_negocio`
le pegan a Aunesa con su propio `requests`, por fuera de `core/aunesa`. El
detector de caídas (§0.ad) mira `manager.proveedor_estado`, que se llena desde
adentro del cliente: **un módulo que no pasa por el cliente es invisible**, aunque
sea el que rompe el dato más importante del sistema.

Migrarlos enteros es otro trabajo y toca cuatro flujos. Lo que cierra la ceguera
hoy con **una línea por módulo** es `proveedores.sesion_vigilada()`: una
`requests.Session` con un hook de respuesta, que se dispara en CADA llamada de
ese archivo — así una función nueva ahí queda cubierta sin que nadie se acuerde.
De yapa reusa la conexión TCP, que en un job de cientos de llamadas no es poco.

> ⚠️⚠️ **Y ESO MISMO CASI INVENTA UNA CAÍDA.** El hook quedó al lado de `mirar()`,
> que ya existía y hacía lo mismo, **con otro umbral**: `mirar` marcaba caída
> desde 400 y el hook desde 500. Con los dos enganchados a la misma llamada, un
> **400 escribía «AUNESA CAÍDO» y enseguida «recuperado»** — una caída inventada,
> prendiéndose y apagándose sola. Y `jobs/cashflow` maneja el 400 de Aunesa
> explícitamente, o sea que no era hipotético. Es REGLA #9(B) en vivo: dos copias
> del mismo criterio, sin árbitro, cada una coherente consigo misma.
>
> **RESUELTO**: el umbral vive UNA vez en `proveedores.es_caida()` (solo 5xx — un
> 4xx es problema NUESTRO, y el 401 es el token vencido que los jobs resuelven
> re-autenticando), `rastrear` pasó a ser `vigilar`, y `vigilar` es idempotente
> (`jobs/aum` tenía los dos hooks sobre la MISMA sesión). Tres tests lo congelan,
> incluido uno que exige que los dos caminos deriven del mismo `es_caida`.

---

---

### 0.br HORA ARGENTINA, ORDEN Y UNA COLUMNA DE CUÁNDO (2026-08-22)

*«Basta de UTC y esas cosas… horario argentino mostrar.»* Tenía razón en dos
lugares, y uno era peor que el otro.

**El mensaje que decía la hora equivocada.** `salud` imprimía
`ultimo_t.strftime(...)` sobre un datetime en UTC **y le pegaba la etiqueta
«UTC»**: «la corrida de 20/08 16:30 UTC falló», cuando acá eran las 13:30. No
es solo la etiqueta — pedirle a alguien que reste tres horas mentalmente para
ubicar un hecho es garantizar que lo ubique mal. El formateo pasa a vivir UNA
vez, en **`core.tz.hora_ar`**, que ya existía y esa capa no usaba.

**Y la pantalla heredaba la zona del navegador.** `hora()` llamaba a
`toLocaleTimeString("es-AR")` **sin `timeZone`**: en la oficina coincide con ART
*por casualidad*, y desde un teléfono en otra zona la pantalla miente sin
avisar. La zona se declara, siempre.

#### La columna de CUÁNDO

El motivo ya traía un `· 12:25` pegado al final del texto. Ahí no se puede
barrer ni ordenar: hay que leer la frase entera de cada fila para ubicarla.
Como columna, el ojo la recorre de una.

De HOY muestra la hora; de otro día, la fecha — repetir «22/08» ciento treinta
veces gasta ancho sin informar. El `title` siempre trae las dos.

Para eso `abierto_at` **deja de descartarse** en la vista (se leía del JOIN,
se usaba para `dias_abierto` y se tiraba). Va en ISO y no formateado: mandarlo
ya escrito parece más simple y es peor — el mismo dato no se podría ordenar sin
volver a parsear el texto.

#### Orden y densidad

Las filas venían **en el orden que devolvía la query**, o sea ninguno: lo que
apareció recién quedaba enterrado entre lo de la semana pasada. Ahora es más
reciente primero, y lo que no tiene `abierto_at` va al final — no se le inventa
una fecha para poder ordenarlo.

El alto de fila baja de `py-1` a `py-0.5`: con 130 filas, cada 4px son media
pantalla.

---

---

### 0.ci EL COTEJO GENERAL, y una acción que creó 6 anomalías (2026-08-22)

Se aplicaron los 6 `pata_equivocada` (BPOA7/8, BPOB7/8, BPOC7, GD46), el control
`patas_equivocadas` quedó en **0** y ENCONTRÓ siguió mostrando **17**. Tercera
vez en la misma sesión con la misma forma: **control verde, hallazgo vivo**.

Las dos anteriores se taparon de a una regla por vez. **Así se llega a la
cuarta.** Ahora hay un cotejo general: *un hallazgo cuya regla espeja un control
caduca cuando ese control queda en cero*. Y la relación regla → control **ya
existía y no hubo que escribirla** — cada `Accion` declara `causa` (la regla) y
`sobre` (el control), así que una acción nueva trae su cotejo puesto.

⚠️ **Solo cuenta el control en CERO, a propósito.** Con casos activos se podría
matchear sujeto por sujeto, pero las claves no son el mismo string
(`assets_ticker_partido` guarda la UNIDAD y el hallazgo habla del TICKER) y un
match fallido se leería como «resuelto». Cero activos no tiene esa ambigüedad.
`pata_equivocada` sale de la lista de deuda del test; quedan dos.

#### Y la acción creó 6 anomalías nuevas: faltó reiniciar el OTRO motor

Junto al verde apareció `Renta fija cotizando sin TEA/TNA: 16 (▲6 nuevos)` — y
los 6 nuevos son **exactamente** los 6 que se acababan de apuntar.

La causa es la trampa que este mismo doc describe: **son DOS motores.**
`motor_rofex` PIDE el precio y `motor_curvas` CALCULA la TEA, y **los dos arman
su universo al arrancar**. Se reinició solo el primero, así que el bono quedó con
precio de la pata D y la TEA escrita bajo el símbolo viejo. El bono no está roto:
está partido entre un motor nuevo y uno viejo.

**Regla que queda: una acción que cambia `mercado.curvas.instrumento` toca a los
DOS motores, y el que la aplica tiene que decir los dos reinicios.** Decir uno es
peor que no decir ninguno — deja el sistema en un estado mixto que ningún
detector distingue de un bono realmente sin tasa.

---

---

### 0.ci CI ROJA EN `main`: dos contratos de capas rotos (2026-08-22)

Las últimas 6 corridas de CI en `main` fallaban en `lint-imports` — y con ese
paso rojo, **Pytest ni corría**: cualquier regresión nueva entraba sin red. Dos
contratos rotos, y cada uno pedía un arreglo distinto porque las causas eran
distintas:

- **`core.dependencias → api.services.jobs_catalogo`.** La correlación
  label→módulos (§0.af) delegaba en el parser del crontab, que vivía en
  `api/services` — y core no puede importar del proyecto. El parser es parsing
  PURO de un archivo que viaja con el deploy (ni base ni red), así que su lugar
  era `core/`: ahora vive en **`core/crontab.py`** y `jobs_catalogo` importa de
  ahí con el nombre que ya usaban sus llamadores (`_parse_crontab`). Un solo
  parser (REGLA #9), capa correcta, cero llamadores tocados.
- **`av_agent_seguridad → api.superficie → fastapi`.** Acá el arreglo NO es
  mover código: `api/superficie.py` es la ÚNICA implementación de la
  enumeración de rutas (la regla «no la reimplementes» existe porque la copia
  veía 37 de 541) y vive pegada a FastAPI por naturaleza — inspecciona la app
  montada. Se declara la excepción en `.importlinter` con su porqué, igual que
  la que ya tenía `_grupos_scope`.

La lección es de proceso: los dos imports entraron en commits que CI ya no
podía frenar porque el paso anterior ya estaba rojo. **Una CI roja no es un
estado: es una puerta abierta** — todo lo que se pushea mientras tanto entra
sin que nadie lo mire.

#### Y detrás de la puerta había DOS tests rotos más

Con `lint-imports` verde, Pytest volvió a correr — y cazó dos fallas que
entraron durante la ventana ciega. Las dos son la misma enfermedad ya
bautizada en §0.ay y §0.ce: *un test que congela la implementación, no la
intención*.

- **`test_lo_que_ARRASTRA_no_entra` era flaky por HORA DEL DÍA.** Usaba
  `AHORA − 21 h` (las 21 horas del incidente real) esperando que cayera
  «ayer», pero el corte del día es la medianoche ART: entre las 21:00 y las
  24:00 ART esas 21 horas caen adentro de HOY y el test fallaba — tres horas
  por día, todos los días. CI de las 20:54 ART lo pasó por seis minutos. Ahora
  el timestamp se arma contra `_arranco_el_dia()`, el corte real.
- **`test_se_ven_TODAS_las_rutas` congelaba un detalle de la VERSIÓN de
  FastAPI.** Afirmaba `rutas() > app.routes × 5`, que presume los envoltorios
  `_IncludedRouter`; con la 0.136.x pineada `app.routes` viene PLANO (las 562
  directas) y el test fallaba justo cuando no hay nada escondido. Ahora
  verifica la intención en los dos mundos: con envoltorios, superficie ve
  mucho más que el primer nivel; plano, no puede ver ni una APIRoute menos.

#### ⚠️ Y el hallazgo de fondo: EL ENTORNO REAL Y EL PIN NO CORREN LA MISMA FASTAPI

Ese segundo test destapó algo más grande, verificado acá y no supuesto: la
clase `_IncludedRouter` **no existe** en la FastAPI que pinea
`requirements.txt` (0.136.x aplana `app.routes` — medido con una app mínima).
Pero el 37/541 de §0.s se midió EN PROD con envoltorios: **el entorno donde
corre el sistema y el que instala CI no son la misma FastAPI.** REGLA #9(B) a
nivel entorno: dos mundos sin árbitro, cada uno coherente consigo mismo.

Consecuencia concreta: `gen_mapa_app` genera un mapa DISTINTO según dónde
corra — regenerado en el entorno plano, la tabla de 31 routers colapsa a 1
(`(raíz)`) porque la atribución por router viaja en los envoltorios. Por eso
el `--check` del mapa no puede dar verde en CI mientras el doc se genere en el
entorno real (y el mapa regenerado en CI sería PEOR, así que no se regeneró
acá a ciegas). Tres cosas quedaron hechas:

  1. **CI reordenada: Pytest corre ANTES del check del mapa.** Un doc
     desincronizado no puede volver a tapar el resultado de los tests — que es
     literalmente lo que pasó estos dos días.
  2. **`scripts/diag_entorno`** (read-only): compara instalado contra pineado
     en los paquetes clave y dice en qué mundo cae `app.routes`. Correrlo en
     el Droplet contesta qué versión manda. De paso ya midió algo acá:
     `psycopg` está **sin pinear** en requirements.
  3. ~~La decisión queda abierta~~ → **RESUELTA CON LA MEDICIÓN** (mismo día,
     abajo).

#### La medición REFUTÓ la hipótesis: el Droplet es PLANO

`diag_entorno` corrido en prod: **FastAPI 0.136.1 == pin, `app.routes`
PLANO.** O sea que prod, CI y el sandbox son el MISMO mundo — el entorno
anómalo con envoltorios es la **máquina local de Windows** (donde se generó el
mapa de 31 routers y donde se midió el 37/541 de §0.s). La hipótesis «prod
tiene los envoltorios» era razonable y estaba equivocada, y decidir el pin
sobre ella habría alineado CI contra el entorno equivocado. REGLA #2: la
medición antes que la decisión.

Con eso el arreglo cambió de forma:

- **`superficie` aprendió el mundo plano** (`_bajar_plano`): la atribución de
  routers se reconstruye por **IDENTIDAD DE ENDPOINT** — la función declarada
  es el mismo objeto en la ruta copiada al tope y en el router que la declaró
  (REGLA #9A: identidad por ficha, no por string) — y la etiqueta sale de
  restarle al path completo el tramo declarado. Verificado: reproduce los
  **31 routers exactos** de la tabla. Los gates ya eran world-independientes
  (FastAPI plano fusiona las dependencies del include en cada ruta, así que
  `_gates_propios` ve la misma unión que el otro mundo arma a mano).
- **El mapa PLANO pasa a ser el canónico** (regenerado: 4 filas cambiaron solo
  en el conteo de «gates extra» — cada mundo expande sub-dependencias con
  distinta profundidad). CI y prod lo reproducen; el `--check` de CI queda
  verde.
- **`psycopg` quedó pineado a 3.3.4** — la versión medida en el Droplet; era
  el único drift real que mostró el diag. `psycopg-pool` entra a la lista del
  diag para medirse en la próxima corrida antes de pinearse (REGLA #2).
- **Pendiente para el user, una sola vez (REGLA #6)**: reinstalar el venv
  LOCAL de Windows desde `requirements.txt`, así el mapa regenerado ahí vuelve
  a coincidir con el canónico. Mientras tanto, regenerarlo desde la máquina
  local va a mover esas 4 filas — no está roto, es el mundo viejo.

---

---

### 0.cq LOS DOS DIAGNÓSTICOS HABLARON — un árbitro para la pata, y el contrato real del control (2026-08-22)

La ronda anterior dejó dos preguntas medibles; los diags las contestaron y las
dos respuestas eran DISTINTAS de lo que parecía en pantalla:

**1. Los 11 «resueltos solos» NO estaban resueltos.** `diag_patas_master`:
columna Y blob siguen apuntando a la pata en PESOS, y no hay ninguna acción de
lote sobre ellos (el lote de las 00:26 cubrió los 6 BOPREALes + GD46, que sí
caducan bien). El «ya no aparece en el control: se resolvió solo» era el
control mintiendo por su criterio: usaba `es_default` — que es una **copia del
master** (`sembrar_especies`: `es_default = simbolo == curvas.instrumento`) —
y para estos 11 Primary tiene la default en ARS, así que el JOIN no devolvía
fila y eran INVISIBLES, no resueltos. El detector (§0.bu) ya usaba el árbitro
correcto: **la moneda del EJE** vía `core.especies.pata_para_el_eje`.
RESUELTO: `_chk_patas_equivocadas` usa el MISMO árbitro (congelado por test:
`es_default` no puede volver al predicado). Al deployar, los 11 reaparecen en
el control —que es la verdad— y la acción `mercado.apuntar_pata` se
desbloquea: se aplican desde la pantalla y ahí sí caducan de LA LISTA.
De paso el control cubre TODOS los ejes declarados (una curva ARS suscribiendo
la pata D también es un campo mal cargado).

**2. La alerta del sábado por las tenencias era FALSA, y el user tenía razón
en el porqué.** `diag_tenencia_fechas`: todos los viernes históricos están
(14/08, 07/08) y el máximo un sábado es el JUEVES — el job corre L-V a las 11
UTC y **escribe el hábil anterior a su corrida** (T-1). `fecha_objetivo`
calculaba «hábil anterior a HOY», que un sábado exige el viernes… que recién
se escribe el lunes. RESUELTO en dos pasos con el calendario: (1) ¿cuál fue la
última corrida esperada? (hoy solo si es hábil y su `corre_utc` ya pasó, con
una hora de gracia; si no, el hábil anterior); (2) esa corrida escribe el
hábil anterior a sí misma. La hora del cron se declara en `REHACIBLES`
(`corre_utc`), al lado del job — el dato vive con el job, no con el que
pregunta. Bonus del diag: **06/08 también falta** (un hueco histórico real,
nadie lo rehizo) y `job_runs` registra este job como tipo `aum`.

La lección que se repite y ya tiene nombre: **las dos pantallas del mismo
hecho tienen que leer EL MISMO predicado** — es REGLA #9 y es la tercera vez
esta semana (voto, símbolo, pata).

---

---

### 0.cv IGNORAR es un SNOOZE del día, «viejo» cierra solo, y el diagnóstico da UNA respuesta (2026-08-22)

El round de correcciones más grande de la semana, todo del mismo mensaje del
user. Cinco decisiones que quedan:

**1. El modelo mental canónico de las pantallas.** AHORA es el **noticiero**:
el primer lugar donde aparece lo que se detectó, sin accionables. ENCONTRÓ →
LA LISTA es el **banco de trabajo**: el mismo aviso pero con el toolkit, y
SOLO para lo que tiene algo que hacer de nuestro lado. Lo que no se arregla
desde acá (AUNESA caído, un motor ajeno) NO ensucia LA LISTA: es un objeto con
estado en VIGILANCIA que se cierra solo cuando el monitor deja de verlo — y su
aparición/cierre se cuenta en AHORA. Esto YA era así (verificado: el centinela
escribe en `av_agent_centinela`, no en la foto de la relevada) y queda escrito
como contrato.

**2. IGNORAR = «sacalo de la lista por HOY», no una blacklist.** El user:
*«si lo ignoro quiero que salga de ENCONTRAR — NO que entre en una blacklist
de cosas que nunca más me van a interesar. Si el agente funciona bien, mañana
lo vuelve a detectar y TIENE que volver a aparecer»*. El botón de un hallazgo
ya NO escribe `agente.av_agent_ignorados`: mueve los objetos del sujeto a
`ignorado` (acción `ignorar_hoy`) y **el snooze vence solo** —
`av_agent_items.ver()` reabre como `nuevo` (no `volvio`: nadie lo dio por
arreglado) la primera vez que un detector lo re-ve en un día ART posterior.
La vista lo marca (`ignorado`, contador `ignorados_hoy`) y la pantalla lo
esconde contándolo — nunca en silencio. **La tabla durable queda SOLO para la
respuesta «no nos interesa» de las preguntas de alta**: ese sí es un juicio
sobre el PAPEL, sigue siendo reversible en DECIDIDO, y son dos gestos
distintos a propósito. Congelado por `test_av_agent_ignorar_hoy`.
⚠️ Lo ignorado ANTES de este cambio quedó en la blacklist vieja: se ve y se
deshace en DECIDIDO → NO TE INTERESAN.

**3. El voto «¿te sirve verlo?» se eliminó de la UI.** El user: *«no le
encuentro el sentido — todo me sirve ver, el agente ya muestra en función de
lo que le pido»*. Tenía razón: en una observación no hay diagnóstico que
juzgar y el voto de utilidad generaba métricas que no miden nada. Para sacar
una fila está IGNORAR (por hoy); los votos viejos siguen valiendo como dato.
**El «¿acertó el diagnóstico?» se queda**: ese sí entrena la compuerta.

**4. Un desenlace «viejo» CIERRA el hallazgo en el acto** (caso GD46: «dice
arreglado hoy pero sigue figurando»). Dos fixes encadenados: (a) la conclusión
de las ocho lentes («causa: sano») viaja en capa `veredicto` y `_desenlace`
solo buscaba `nada_que_hacer` entre las `prueba` — por eso un cotejo ámbar por
diferencia de DEFINICIÓN (paridad clean vs precio operado) le ganaba al «no se
detecta nada roto» y el título decía «LEÉ LA TRABA» sobre un bono sano; ahora
el flag se busca en todas las capas y viejo gana. (b) `_cerrar_si_viejo` corre
tras CADA `out["veredicto"]` (4 puntos, contados por test): marca los objetos
del sujeto `resuelto` (motivo `diagnostico_probo_sano`) — las lentes re-corren
la MISMA detección que el detector nocturno, así que esperar a la noche para
cerrar era burocracia. El front esconde ARREGLAR y dice «quedó VIEJO — se
cerró solo». Si el diagnóstico se equivocó, el detector lo re-ve → VOLVIÓ.

**5. El diagnóstico da UNA respuesta; el razonamiento es del agente.** El
user: *«¿para qué LOS PASOS, CONTEXTO, PARA APRENDER? Eso es interno del
agente. Yo quiero: ¿está ok? → arreglar; ¿no está ok? → el motivo exacto»*.
La pantalla queda: ORDEN imperativa → la traba UNA sola vez (el título del
desenlace ya no se repite arriba del bloque que dice lo mismo) → y TODO lo
demás (pasos, contexto, lecciones, contadores, cómo se calculó) detrás de
«▸ detalle interno del agente», cerrado por default. No hizo falta el LLM que
el user ofreció: los textos ya eran prosa razonable — el problema era la
repetición y la jerarquía, que se arreglan en la estructura y no gastan un
token por diagnóstico. Si con esto todavía no alcanza, el paso siguiente es
una redacción LLM de la conclusión (tarea nueva → pasa por la ley de §0.o).

**Y DECIDIDO se rediseñó** (§0.cr aplicado acá): una sola columna en el orden
en que uno pregunta — CONTESTADAS SIN EJECUTAR TODAVÍA (dice explícito que se
mueve sola cuando el agente gane la habilidad), NO TE INTERESAN (lo único con
botón: deshacer), REGISTRO DE RESPUESTAS (ex «HISTORIAL», que era un historial
adentro del historial), VOTASTE. Cada bloque dice qué es y si se actualiza.

---

---

### 0.cw EL DNI SE RESPETA DE PUNTA A PUNTA — tres flujos lo ignoraban (2026-08-22)

> *«Cada cosa que pasa no es un objeto con un ID… no hay un DNI: existe un
> problema y puede aparecer infinitamente en el día por más que ya lo
> soluciones. A nivel base y a nivel desarrollo claramente está todo mal.»*

El diagnóstico del user es correcto con una precisión: el DNI **existe**
(`agente.av_agent_items`, clave = sujeto|causa, §0.bd) — lo roto era que tres
flujos actuaban sobre fotos o strings sin consultarlo. Los tres, con su caso:

**1. La identidad se stripeaba, y el upsert fabricaba FANTASMAS (caso OTC).**
La cadena completa del bug, leída en el código: el control `assets_sin_cartera`
canta la `unidad` EXACTA (`'[OTC - MAI.ROS/ENE27] '` — con espacio final; el
doble espacio en pantalla era la pista) → `av_agent_hacer._sujeto()` hacía
`.strip()` → la propuesta nacía con OTRA identidad → `assets_sql.set_campos`
es un **UPSERT**, así que aplicar no falló: **creó una fila nueva** trimmeada
con `CARTERA=DERIVADOS` → la verificación releyó esa fila («✔ CARTERA =
DERIVADOS») → el control siguió cantando la fila real, y QUÉ PROPONÉS volvía a
proponer lo mismo, infinito. Tres pantallas coherentes, ninguna diciendo la
verdad — REGLA #9 en su forma más pura. **Arreglos**: `_sujeto()` ya no
stripea (la identidad es el string exacto; para mostrar está `_nombre()`), y
las escrituras del agente van con `set_campos(..., crear=False)` — sobre una
unidad que no existe EXACTA se levanta error en vez de inventar un asset.
**Lo que quedó en la base se mide y repara con
`scripts/diag_unidades_fantasma`** (dry-run; `--reparar` copia la cartera a la
fila real y borra la fantasma, con tres guardas: btrim-colisión, escrita por
av-agent, sin tenencia).

**2. «Probado sano» marcaba `resuelto` y generaba VOLVIÓ espurio (caso GD46).**
El arreglo se escribe en `mercado.curvas`, pero el detector lee la TEA de
`market_snapshot`, que recién cambia cuando `motor_curvas` se reinicia. Cerrar
como `resuelto` hacía que el siguiente avistaje legítimo lo marque «volvió» —
ensuciando la señal más valiosa del modelo con falsas reincidencias. Ahora
`resolver_sujeto` y el `_cerrar_viejo` del masivo marcan **`en_curso`**: la
fila sale de la lista igual (atendida), un re-avistaje NO la mueve (suma
`veces`), y la cierra el DETECTOR vía `_cerrar_ausentes` cuando deja de verla
— el agente no califica su propio trabajo, que es la filosofía que
`_mover_item` ya tenía escrita.

**3. Los lotes trabajaban sobre la FOTO sin mirar el objeto.** El masivo
tomaba los casos de la foto de la última corrida y re-diagnosticaba (y el lote
re-aplicaba: dos `arreglar_bono` GD46 en el libro, 19:10 y 20:05). Ahora
`arrancar()` consulta `estados_de()` (una query para todo el lote) y saltea
en_curso/resuelto/ignorado **diciendo cuántos** (`saltados_atendidos`). Y la
cola de DECIDIDO se concilia contra la BASE: una `alta` contestada cuyo ticker
ya existe en `mercado.curvas` se sella `aplicada` sola con nota «ya estaba en
la base» (read-repair en `pendientes_de_aplicar`) — la cola muestra lo que de
verdad falta, no promesas que la realidad ya cumplió.

**Y el error de UX del round anterior se revierte**: los pasos con panel de
acción (`hacer`) se muestran SIEMPRE fuera del pliegue «detalle interno» — lo
que se pliega es el razonamiento, jamás la acción. Congelado por
`test_av_agent_dni`.

**Medido en prod (diag del user, 2026-08-23): la hipótesis dio exacta.** Dos
fantasmas — `'[OTC - DLR052027]'` y `'[OTC - MAI.ROS/ENE27]'` trimmeadas,
escritas por `av-agent` el 22/08 con cartera y cero tenencias — al lado de las
reales con espacio final (de `job:assets_autofill`, con 6 y 16 tenencias, sin
cartera). Reparado con `--reparar`. Y quedó la PREVENCIÓN: el control nocturno
**`unidades_gemelas`** canta cualquier colisión futura de unidades por btrim,
venga de donde venga — la próxima dura horas, no días.

---

---

### 0.cx LA LEY DE CONEXIÓN — y el HISTORIAL que la estrena (2026-08-23)

> *«Si hay que hacer una nueva regla general del proyecto que sea una ley
> irrompible: todo lo nuevo que se desarrolle en el AV AGENT no tiene que
> estar suelto como si nada — acá todo se tiene que conectar.»*

Quedó como **REGLA #10 en CLAUDE.md** (las cinco: objeto con DNI · una casa
por naturaleza · fecha y hora visibles · consultar el DNI antes de actuar ·
declararse en los registros). Lo que la estrena, todo del mismo mensaje:

**Las NOTICIAS salen de ENCONTRÓ.** «LA BASE CAMBIÓ: 34» ocupaba LA LISTA sin
un solo botón y sin hora — y ENCONTRÓ es accionable por definición. Nace
`av_agent.TIPOS_NOTICIA` (`db_cambio`, `tabla_quieta` — observaciones de la
base sin accionable, DECLARADO como `EN_AHORA_SIEMPRE`, nunca inferido): la
vista las marca `noticia`, LA LISTA las esconde contándolas (la búsqueda las
encuentra), y su casa es **AHORA → NOTICIAS DE LA BASE**, cada una con fecha y
hora, sumando al contador de la tab.

**HISTORIAL queda en DOS cosas con el menú horizontal de ENCONTRÓ:**

- **REGISTRO** — «¿por qué DECIDIDO no está en YA HIZO?» No había razón: lo
  que escribió el agente y lo que decidiste vos (respuestas, votos) son
  eventos del MISMO sistema, y separarlos obligaba a mirar dos tablas para
  reconstruir una historia. Ahora es UNA línea de tiempo ordenada, cada
  evento con fecha+hora, quién y sobre qué. Arriba, lo único vivo:
  CONTESTADAS SIN EJECUTAR (que se concilia contra la base, §0.cw) y NO TE
  INTERESAN (el único botón: deshacer). Y **sin grillas de columnas fijas**
  — la `grid-cols-[110px_170px_90px_1fr]` que montaba «JOB:CIERRE_CANJE»
  sobre la columna de al lado se fue: dos renglones que envuelven.
- **COMUNICACIONES** — ex «MANDÓ», que no es una palabra de nadie. **Solo las
  de HOY** (día ART), con fecha y hora por fila: una comunicación es del día;
  el efecto pendiente vive en la bandeja del destinatario (/api/avisos), no
  acumulándose acá. El pasado no se pierde: las escrituras que dispararon
  esos mensajes están en el REGISTRO.

---

---

### 0.cy LA FOTO DE PRIMARY TENÍA 17 DÍAS — y «no cotiza» era «no está en la foto» (2026-09-01)

> *«Es mentira esto... sí que está el ticker. Y encima hay una habilidad que
> corrió 4× hoy, que NO detectó esto ni tampoco detectó otros bonos nuevos que
> se licitaron. Y encima dice que sí chequeó.»*

**Lo que se vio.** El arreglo de `S29E7` decía «⚠ Primary NO lista este
símbolo» y bloqueaba el alta; el buscador de OPERAR lo encontraba en el acto.
`soberanos_faltantes` mostraba 4 corridas `ok` y 1 hallazgo, y los licitados
nuevos no aparecían por ningún lado.

**Lo que era.** El agente no le pregunta a Primary: le pregunta a una FOTO,
`manager.pyrofex_instruments`, que escribe `scripts/discovery_pyrofex` — un
script **manual**, sin cron. Medido con `scripts/diag_primary_catalogo` (borrado al cerrar el tema, REGLA #5; git lo conserva): la foto
era del **15/08 19:23 UTC**, 17 días; S29E7 cotizaba en vivo en 24hs y CI y no
estaba en ella; **966 símbolos** en vivo no estaban en la foto y **1.070** de la
foto ya no existían. OPERAR pregunta en vivo (`get_detailed_instruments`), así
que las dos mitades se contradecían sin que nada fallara: la REGLA #9 (B) del
repo, dos copias sin árbitro.

Y la misma foto la usan **el WS de todos los motores**
(`core/instrumentos_validos`, aplicado en `agregar_suscripciones`) y
`jobs/validar_instrumentos`, que borra especies que no estén en ella: un bono
nuevo dado de alta no recibía precio, y su especie sembrada se borraba esa noche.

**El segundo agujero.** El detector descartaba «lo que no cotiza» con un
`continue` y un `log.info`. Con la foto vieja, TODO lo licitado después caía
ahí, y la habilidad sellaba `ok`. Un descarte que no deja rastro es
indistinguible de un detector que dejó de mirar — el invariante #1, violado por
dentro.

**Lo que queda, cinco cosas:**

1. **La foto se refresca sola**: cron `discovery_pyrofex` a las 12:15 UTC L-V,
   antes del cleanup (12:30) y de los motores (13:20). El script se niega a
   pisar la foto si Primary devuelve menos de la mitad que la vez anterior, y
   sale con error para que `run_job.sh` lo registre.
2. **Alguien vigila que corra**: habilidad `foto_primary` (SISTEMA, cada hora).
   Lee el horario **del crontab del repo**, no de una copia, calcula la última
   corrida esperada (L-V) y canta `foto_vieja` o `nunca_corrio`. Sin arreglo a
   propósito: sacar la foto necesita sesión pyRofex y el daemon no la tiene.
3. ~~El descarte se canta como aviso por bono~~ — **duró un día.** El user
   (2026-09-02): *«si Primary no lo lista es porque no está, eso mata todo; no
   hay que insistir»*. Primary ES el mercado. Lo que 1816 publica y Primary no
   lista se descarta (y se cuenta en el log); lo que hacía falta no era avisar
   sino que la foto fuera fresca y vigilada, que son los puntos 1 y 2. Lo que
   está en cartera sigue pidiendo el alta.
4. **El pre-flight distingue foto de vivo**: `_estado_simbolo` pregunta a
   Primary en vivo (`ordenes.simbolos_live`, la misma llamada que OPERAR) antes
   de afirmar que no lo lista. En vivo sí / foto no → INFO con la fecha de la
   foto, no BLOQUEA: el alta se aplica y el precio llega cuando el discovery la
   refresque y el motor rearme. Ni en vivo → BLOQUEA, ahora con razón. No pude
   preguntar → NO_SE.
5. **`reincidio` es ABIERTO** (`tipos.ABIERTOS`, y las tres vistas). Apareció
   en el mismo diag: M31G6 en `reincidio` desde el 28/08, «visto 1×», sin
   cerrar. `_cerrar_ausentes` y `_ver` solo miran ABIERTOS, así que un hallazgo
   que volvió no lo cerraba nadie, no lo actualizaba nadie (verlo de nuevo
   insertaba OTRA fila en `reincidencias`) y no lo mostraba ninguna pantalla.
   El índice único `hallazgos_abierto_unico` no se amplió a propósito: fallaría
   si ya hubiera dos filas `reincidio` del mismo trío, y la puerta ya actualiza
   en vez de insertar.

**Pendiente, medido y no resuelto acá**: `research.mkt_1816_instrumentos` (el
catálogo persistido de 1816, que llena `mercado_1816_discovery --apply
--catalogo`) también es manual y tampoco tiene a S29E7 — `ficha_1816` y
`tamar_1816` leen de ahí. El detector no lo sufre porque censa 1816 en vivo
(~29 créditos por corrida, 4 corridas por día en rueda).

---

### 0.cz REINCIDE EL ITEM, NO EL GRUPO — y las seis copias de «CARTERA» (2026-09-01)

> *«Mucho son lo mismo, solo que se repite. No tiene en cuenta que no es que
> reincidió: el aviso en sí sigue. Emisores sin cargar va a haber siempre, eso
> no es reincidencia. Reincidencia sería que si yo agrego un emisor de un bono,
> ese bono vuelva a estar sin emisor.»*

Dos bugs encadenados, y §0.cy destapó el segundo al hacer visible `reincidio`.

**1 · La identidad era demasiado gruesa.** En `ficha_incompleta` el sujeto es
el CAMPO («CARTERA»), a propósito: 224 títulos sin clase son UN trabajo de
carga, no 224 problemas. Pero la reincidencia se decide por el trío, así que
completar 4 títulos cerraba «CARTERA sin_cartera» por acción, y el 5.º que
entraba a cartera al día siguiente «reincidía» — sobre un arreglo que nunca lo
tocó. Lo que el user describe es la definición correcta: reincide un ITEM que
la acción escribió y volvió a estar mal. Así que el hallazgo de grupo lleva
sus `items` (las unidades, en la evidencia — no para la pantalla, para la
identidad), y `registro._ver` sólo declara reincidencia cuando alguno de esos
items figura como `sujeto` de una acción `ok` del hallazgo previo. Sin
intersección, es un problema nuevo con el mismo nombre. Un hallazgo sin
`items` (el sujeto ES el item, como un bono) sigue igual que antes.

**2 · Y cada corrida hacía una copia.** Hasta §0.cy `reincidio` no estaba en
`ABIERTOS`, así que `_ver` no encontraba nada abierto, volvía a encontrar el
previo cerrado por acción, e INSERTABA otra fila `reincidio` — y otra en
`reincidencias`. Nadie lo vio porque ninguna vista mostraba `reincidio`. Al
mostrarlo, ENCONTRÓ tenía seis «CARTERA sin_cartera» de distintas horas.
`sql/schema.sql` lo limpia en un bloque idempotente antes de crear el índice:
de cada trío con más de una fila abierta queda la más nueva, las
«reincidencias» de `ficha_incompleta` se borran y sus hallazgos vuelven a
`nuevo`. Con eso hecho, `hallazgos_abierto_unico` pasa a cubrir `reincidio`:
la base ya no permite la copia que el código dejó de hacer.

---

### 0.da EL LATIDO — cada proceso dice que está vivo, solo (2026-09-02)

> *«Es el que más me interesa. Tiene que estar hecho de manera excelente y
> eficiente, y actualizarse solo, no depender de intervención humana: si
> mañana meto otro motor se tiene que detectar solo.»*

**Lo que había.** De diecisiete procesos de systemd, dos escribían un latido
(`motor_ordenes`, `control_saldos`), cada uno con su hilo y su formato, en un
singleton. Los otros quince se juzgaban por la frescura de la tabla que
escriben (`motor_caido` sobre el árbol de diagnóstico), y eso no distingue
«vivo y sin operaciones» de «muerto». El WS trunca a diez los símbolos que
Primary no lista y, cuando agota seis reconexiones, se mata con un mensaje
que no llega a ninguna tabla.

**Lo que queda, y por qué nadie tiene que acordarse de nada:**

- **`core/latido.py`**: un hilo daemon escribe `operaciones.latidos` cada 15 s
  (proceso, pid, host, arrancado, último latido, `data`). Nunca levanta: si
  Postgres no responde, loguea una vez cada diez minutos y sigue.
- **Arranca solo en `engines/__init__.py`** cuando el proceso es `python -m
  engines.<motor>`, leído de `sys.orig_argv` (`sys.argv[0]` vale `-m` mientras
  se importa el paquete). Un motor nuevo late desde su primer arranque sin
  saber que esto existe. Los tres daemons de `jobs/` lo llaman en su `main`.
- **`core/websocket.py` anota el feed en el latido**: `ws` (conectado ·
  reconectando · agotado · error_inicio), mensajes recibidos y cuándo fue el
  último, reconexiones, y las listas COMPLETAS de rechazados por Primary, por
  ROFEX y en cuarentena. Es el único punto por el que pasan todos los motores.
- **`agente/unidades.py`**: el universo de procesos sale de `deploy/systemd`
  (qué unit corre qué módulo) y de `deploy/crontab.txt` (a qué hora la prende
  y la apaga el cron). Y le pregunta a systemd en la máquina con
  `systemctl is-active`, como `cron_desalineado` le pregunta al crontab.
- **Habilidad `motor_latido`** (SISTEMA, cada 2 min), cuatro veredictos porque
  el que_hacer es otro en cada uno: `apagado` (systemd inactive en ventana),
  `sin_latido` (active y nunca latió: código anterior al latido, o colgado
  antes de latir), `colgado`/`muerto` (dejó de latir), `sin_feed` (WS no
  conectado) y `feed_mudo` (conectado y sin mensajes en rueda caliente, media:
  puede ser mercado quieto). Sin arreglo a propósito: reiniciar en rueda lo
  decide la mesa; el que_hacer trae el comando exacto.
- **`motor_caido` deja los procesos y se queda con los jobs**, que se juzgan
  por su resultado. Dos habilidades sobre lo mismo son dos relojes.
- **`bono_sin_precio` dice por qué**: antes de ofrecer «pedir la pata» mira si
  `motor_rofex` ya lo pidió y lo rechazaron; ahí es `simbolo_rechazado`, aviso
  con el motivo, sin botón, porque no hay precio posible.

**El día del deploy**: los motores corren código sin latido hasta su próximo
arranque (cron 13:20 UTC). Mientras `operaciones.latidos` esté vacía, la
habilidad levanta `SinDatos` y lo dice, en vez de cantar quince alarmas.

---

### 0.db LOS RELANZABLES SE DERIVAN — y el horario sale del cron (2026-09-02)

`rehacer.REHACIBLES` tuvo una sola entrada, `portafolio_diario`, desde el
20/08. Todo lo demás caía en `pieza_<estado>` con la frase «todavía no tiene
botón: hay que declarar en qué tabla se ve su resultado». Nadie declara
cuarenta jobs a mano, y no hacía falta: lo que hace relanzable a un job ya
estaba escrito en otro lado.

- **El comando, el label y el timeout** salen de `deploy/crontab.txt`
  (`core.crontab.parse_crontab`, que ahora también devuelve el comando
  interno): es exactamente lo que `run_job.sh` recibe cada día.
- **La prueba de que el dato está** sale de los contratos de frescura de
  `api/services/salud.CONTRATOS`, unidos al job por `core.escribe.que_relanzar`
  (quién escribe esa tabla). Para esos jobs el chequeo es **el mismo que corre
  SALUD** (`_chequeo_dato`), no una segunda query parecida.
- **Los que no tienen contrato** se prueban por la **corrida**: una fila que
  no falló en `manager.job_runs`, empezada después de la última hora de cron.
  Es una prueba sobre el proceso y no sobre el resultado, y el preview lo dice
  con esas palabras.
- **Lo declarado gana**: `portafolio_diario` sigue con su regla del hábil
  anterior, y ahora toma el horario del cron en vez de una copia.
- Un job con varias líneas de cron (`sync_comitentes`, tres) tiene todos sus
  horarios; la última esperada es la más reciente de las tres.

`rehacibles()` es la única lista y `cual_job` resuelve módulo, label y tipo
de `job_runs`. El segundo barrido de `motor_caido` («el día que falta no
depende de que el árbol lo note») sigue limitado a los jobs con prueba sobre
el DATO: consultar los cuarenta de prueba «corrida» cada dos minutos era justo
el barrido que ese comentario prohíbe, y a esos ya los canta el árbol.

**Y la deuda de §5.1 se paga**: `_todavia_no_le_toco` dejaba de avisar según
una expresión regular sobre la prosa de la cadencia (`"cada 30m · 15-22 UTC
L-V"`). Ahora lee el horario del crontab por `rehacer.rehacibles()` y lo
evalúa **el único evaluador cron del repo**, `salud.ultima_ejecucion_esperada`.
`foto_primary` (§0.cy) también: nació con un evaluador propio y se lo sacó el
mismo día, porque dos evaluadores de la misma expresión son la REGLA #9 (B).

---

### 0.dc ARBITRAR DOS COPIAS — el SQL declarado al lado del chequeo, con botón (2026-09-02)

`core/duplicados.DUPLICADOS` declaraba `arreglo_sql` en dos de sus cinco
entradas desde el 2026-08-19 y ningún `Arreglo` lo consumía: «dato partido»
salía sin botón, y el símbolo columna-vs-blob que dejó dos bonos en `--`
durante cuatro días se seguía corrigiendo a mano.

- **`arbitrar_copia`** es UNA clase para todos: el preview relee con
  `duplicados.una()` las filas que difieren y las muestra una por una (la que
  pierde → la que manda); aplicar relee de nuevo, corre el `arreglo_sql`
  declarado (scopeado por su propio WHERE) y anota **una línea de libro por
  fila** con antes y después. Lo que se arregla es exactamente lo que el
  detector midió, porque el SQL vive al lado del chequeo.
- `Duplicado.gana` (`"a"`/`"b"`) dice cuál copia manda cuando hay SQL: es lo
  que permite escribir el libro sin adivinar leyendo la prosa de `arbitro`.
- **Dos reglas en `dato_partido`**: `copias_que_no_coinciden` (tiene SQL →
  ENCONTRÓ con botón) y `copias_a_mano` (declara `arreglo_manual` → aviso con
  la instrucción, en AHORA). Un botón que siempre contesta «esto se hace a
  mano» es un aviso con forma de trabajo, la misma lección que `salud`.

---

### 0.dd LO QUE LOS JOBS REPORTAN Y NO ESCRIBEN — de un log a un aviso (2026-09-02)

`ficha_1816` encuentra bonos donde 1816 dice otra moneda y no la corrige,
porque la moneda decide la valuación; `tamar_1816` cuenta las patas sin tasa;
`assets_autofill` capa sus conflictos a diez en cron; `snapshot_cierre`
saltea una curva con un warning; `cleanup_curvas` borraba sin dejar qué.
Todo eso terminaba en un log que nadie abre y en un contador de
`manager.job_runs` que dice que hay algo y no qué.

- **`agente/reportes.py`**: una fila por stat (`job`, `stat`, qué significa
  que sea mayor que cero, qué hacer, severidad). Es la REGLA #10 aplicada a
  los jobs: cada uno inventaba su forma de quejarse.
- **Cada job persiste la lista al lado del número** (`<stat>_lista`, hasta
  200): `ficha_1816`, `tamar_1816`, `assets_autofill` (por regla, y las
  divergencias de herencia), `ops_tasa_mav` (las muestras del formato
  desconocido), `validar_instrumentos` (borrados y no vigentes),
  `snapshot_cierre` (curvas salteadas, que antes ni se contaban) y
  `cleanup_curvas` (qué borró; `run()` ahora lo devuelve).
- **Habilidad `job_reporto`** (DATOS, cada hora): lee la última corrida de
  cada job y convierte cada contador en un aviso con la lista adentro. Un job
  que todavía corre código sin la lista sale igual, con el número, y lo dice.
  No juzga si el job corrió cuando debía: eso es de `salud`.
- Un test cruza cada fila del registro contra el código de su job: una fila
  que promete un stat que el job no guarda es un aviso que jamás aparece.

---

### 0.de LA BANDEJA QUE NADIE PODÍA LEER — y el job que no la escribía (2026-09-02)

> *«Es exclusivo del día: con saber que se informó alcanza, no hay que
> guardar historial. Es solo avisar a las 16:45 y listo.»*

Dos roturas encadenadas, y ninguna fallaba a la vista:

1. **El router no existía.** `api/main.py` documentaba `/api/avisos` «sin
   gate de módulo» y remitía a `api/routers/avisos.py` desde el 2026-08-19. El
   archivo no estaba. La pantalla «PARA VOS» pedía la bandeja, el proxy de
   Next reenviaba, y el backend contestaba 404, que el front traga.
2. **El job moría al mandar.** `jobs/saldos_a_operadores` llamaba a
   `mensajes.enviar_tabla(..., donde=, por=, interrumpe=True)` y la firma no
   aceptaba esos tres: `TypeError` a las 16:45, todos los días, con
   `manager.job_runs` en `error`. La bandeja se escribía para nadie y además
   no se escribía.

**Lo que queda:**

- `api/routers/avisos.py`: `GET /api/avisos` (los de HOY del que pregunta,
  `[]` para el invitado) y `POST /api/avisos/visto`. Montado con
  `verify_api_key` y sin módulo, como `/api/me`.
- `mensajes.enviar` acepta `donde`, `por` e `interrumpe`; la tabla los
  guarda (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`), y un reenvío del mismo
  tema vuelve a poner `visto_at` en NULL: el aviso de hoy es nuevo aunque el
  tema sea el de ayer.
- El front (`mis-avisos.tsx`) se reescribió al modelo 2.0: asunto, detalle,
  la tabla que venga en `filas`, y un solo botón, «visto». Sin historial, sin
  ítems que tildar, sin posponer: el que lleva `interrumpe` se abre solo, como
  el de briefing, y con «visto» desaparece.

Y en el mismo cambio, **Postrade entra a `core/proveedores.PROVEEDORES`**:
era la única API externa sin vigilar. `core/postrade._request` deja rastro
con el mismo criterio que Aunesa (`es_caida`: solo 5xx), así que
`proveedor_caido` lo cubre sin una línea más. Se lo usa para leer, no para
operar desde ahí.

Y **la evidencia se ve**: `v_ahora` viaja con `visto_ultima_vez`, y las filas
de AHORA y ENCONTRÓ muestran «confirmado hace N» al lado de «desde», más un
desplegable con la evidencia del detector. Sin lo primero, un hallazgo de
hace tres días y uno confirmado hace veinte minutos se veían iguales.

---

### 0.df EL CATÁLOGO DE 1816 TAMPOCO PUEDE SER ESTÁTICO (2026-09-02)

> *«El del catálogo persistido es grave. Jamás algo así puede ser estático.»*

Es la misma foto que Primary, en otra tabla. `research.mkt_1816_instrumentos`
la escribía `jobs/mercado_1816_discovery --apply --catalogo`, a mano, y de ahí
leen tres cosas: `ficha_1816` (el emisor estándar), `tamar_1816` (la grafía
con la que 1816 publica cada pata) y el alta del agente. El 2026-09-02 no tenía
a S29E7, licitado hacía días: un bono nuevo nacía sin emisor y sin tasa TAMAR
aunque 1816 lo tuviera, y nada lo decía.

- **Cron** a las 12:00 UTC L-V, antes de `tamar_1816` (13:00) y de
  `ficha_1816` (22:30). Con `--catalogo` las curvas de cruce salen del catálogo
  ya relevado: una pasada por 1816, ~29 créditos por día, no dos.
- **El job deja rastro** (`JobRunLogger`, stats `relevados`, `catalogo`,
  `catalogo_guardados`, `watch`) y se niega a tocar la tabla si 1816 no
  devuelve nada.
- **Habilidad `foto_1816`** (SISTEMA, cada hora): lee `max(actualizado_en)` y
  lo compara con la última corrida esperada del cron, leído del repo.
- **Un solo detector para las dos fotos**: `foto_primary` y `foto_1816` son
  dos filas del catálogo sobre `_foto(...)`. La tercera foto que aparezca es
  otra fila, no otra copia de «¿esto quedó viejo?».

Pendiente de decidir: `soberanos_faltantes` censa 1816 en vivo cuatro veces
por día (~116 créditos). Con el catálogo fresco a diario podría leer de ahí y
ahorrar eso; la diferencia sería enterarse de un bono nuevo a las 9 y no a las
10. Se deja para el user.

---

### 0.dg EL PULSO DEL CLIENTE — lo que la mesa tiene enfrente y el servidor no ve (2026-09-02)

> *«No tiene que estar sesgado por un rol. Tiene que ser algo verdaderamente
> útil. ¿Cómo lo vería en el agente y cómo funcionaría?»*

**Lo que había.** De las 37 pantallas que se refrescan solas, 3 le muestran a
la persona que el refresco falló; las otras 34 conservan lo último que tenían
(correcto) y no lo dicen (no). Y el backend no se entera de ninguna: el fallo
ocurre en el navegador. Un 502 del proxy de Next, un timeout de Vercel, una
vista que pide un endpoint que ya no existe, el minuto de reinicio de un
deploy: nada de eso pasa por el servidor. La tab ESTRATEGIA se perdió una
semana exactamente así.

**Lo que queda, en tres piezas conectadas:**

- **El navegador dice cuándo está ciego, una sola vez para todas las vistas.**
  `usePoll` registra cada endpoint que lleva más de un minuto fallando en un
  registro compartido; el componente `<Pulso />` del layout lo lee. Dibuja la
  marca «sin actualizar hace N min» en la barra (para cualquier rol, en
  cualquier vista) y manda `POST /api/pulso` una vez por minuto por endpoint
  mientras dure. Nada mientras todo anda.
- **`agente.pulso_cliente`** guarda vista, endpoint, motivo, desde cuándo y
  quién. Retención 30 días (`cleanup_retencion`). Sin módulo y sin invitado.
- **`latencia` gana la regla `vista_ciega`**: agrupa por vista, cuenta
  personas, mide desde cuándo, y **cruza con el latido de la API**: `api/main`
  ahora late como cualquier proceso (`uvicorn api.main:app` → `api.main` en
  `unidades`), así que «ciega 4 min» puede decir «coincide con el reinicio de
  la API de las 11:20». Es la única regla del agente que mira el navegador, y
  por eso vive en `latencia`, que ya es la habilidad de «¿la app responde?».

Lo que NO cubre todavía: el fallback vacío del SSR (`safeFetch` en cinco
páginas). Ahí el navegador nunca pidió nada, así que no hay poll que falle:
es el siguiente paso de esta misma pieza.

---

### 0.dh «EXPLICÁMELO» — el error crudo, contado con el repo en la mano (2026-09-02)

> *«¿Y cómo sabe la IA cómo explicar esto? ¿Y qué haría para no solo
> explicarlo sino algo más?»*

**Cómo sabe.** El botón del panel de HABILIDADES (`POST /api/agente/explicar`)
no le manda a la IA el error solo. `agente/explicar.contexto()` arma el
paquete con lo que ya está en el repo: el traceback entero —que ahora se
guarda en `habilidades.ultimo_traceback`, porque «KeyError: 'x'» a secas no
dice dónde—, el código del detector, qué fuentes lee, las entradas del diario
que ese código cita (cada `§0.x` cuenta por qué las cosas quedaron así) y la
última corrida del job que aparezca en el error. Con eso el modelo lee, no
adivina, y **cada respuesta viaja con la lista de lo que se le dio**
(`fuentes`), igual que la evidencia de un hallazgo.

**Qué hace además de explicar**, en el mismo JSON: `de_quien` (nuestro código,
un dato roto en origen, el proveedor, o no sé), `afecta` (qué deja de andar y
qué sigue), `que_hacer` (concreto: un comando, un campo, un job), `test` (el
pytest que congelaría el caso, para revisar y sumar) y `tarea` (título y
prompt listos para una sesión de Claude Code). **Lo que no hace**: tocar
código ni decidir nada. El invariante #12 sigue: es una lectura a pedido de
una persona, con su firma.

**Mecánica.** Tarea `explicar_error` en `core/ai.py` (flash, a pedido, nunca
en una pasada). Cacheada por hash del error en `agente.explicaciones`: el
mismo error no se paga dos veces, y queda con fecha y quién. Si la IA no está
configurada o el presupuesto se agotó, el botón lo dice con esas palabras.

### 0.di LOS CONTADORES DE NEGOCIO QUE MORÍAN EN EL LOG (2026-09-02)

**Qué se midió.** Al relevar las 116 tablas de negocio, back office y
clientes contra lo que el agente lee, salieron cuatro jobs que **cuentan
anomalías de plata todos los días y las dejan en `manager.job_runs` sin que
nadie las lea**: `operaciones_informes` y `negocio_movimientos` abortan la
anulación de boletos cuando el tope salta y escriben «NO se marcó nada —
revisar a mano» en el log; `interbanking_sync` cuenta `dias_incoherentes` y
`cuentas_error`; `mayor_sync` cuenta `duplicados`, `cuentas_sin_mapear` y, la
peor, **no aplica el día** si Aunesa devuelve 0 sobre un día que tenía
movimientos (guarda correcta, pero el día queda con el mayor de ayer y la
conciliación de hoy compara contra un mayor viejo); `ap5_portfolio` cuenta
`claves_divergentes`, filas de futuros distintas bajo la misma clave donde el
UPSERT elige una y pierde la otra.

**Por qué es §0.dd otra vez, y no una habilidad nueva.** El mecanismo ya
existía: una fila en `agente/reportes.py`, el job guarda `<stat>_lista`, y
`job_reporto` lo convierte en aviso con la lista. Lo que faltaba era medir
cuáles contadores eran de negocio. Se sumaron doce filas, y cinco jobs pasaron
a guardar la lista al lado del número (`negocio_movimientos._reconciliar`
ahora devuelve también cuántos candidatos dejó sin marcar; `interbanking_sync.
_incoherencias` devuelve las fechas y no un conteo).

**Lo que sigue sin lista, y se dice.** `aranceles.sin_match`,
`ops_tasa_mav.sin_texto` y `ambiguos` solo tienen el número: el aviso lo
aclara (`con_lista=False`) en vez de prometer una lista que no llega.

### 0.dj GUARDRAILS SE BORRA, Y NO SE RECICLA (2026-09-02)

`jobs/guardrails.py` corría todas las noches desde el 2026-07-18 con cinco
umbrales que nacieron en `None` («sin calibrar») y nunca se calibraron: no
podía marcar nada. El user lo dio por muerto y se borró entero: job, test,
`config.GUARDRAILS_UMBRALES`, cron y su fila en el registro de diagnóstico.

La primera versión de este cambio lo reciclaba como una habilidad nueva, y
el user la rechazó con razón: *«son 5 que dijiste que no sirven para nada y
me estás haciendo sobre eso, esto no es una mejora»*. Vale dejarlo escrito
porque es un modo de falla del que construye: **portar un control muerto a la
arquitectura nueva no lo revive, le da forma de vivo.** Lo que el cierre
necesite mirar se decide desde el negocio, no desde lo que ya estaba escrito.

### 0.dk TRAJO POCO — el job que trae la mitad sale en verde (2026-09-02)

**Qué se midió.** Con `scripts/diag_ingesta` (12 corridas por job): **ningún
job de ingesta compara su volumen contra ayer.** Las defensas que existen son
otras (no anular de más, no purgar si vino vacío, error si no escribió nada),
y un día que Aunesa devuelve la mitad de las cuentas pasa en verde. Y ya
había un caso: `interbanking_sync` el 01/09 a las 17:00 trajo **0
movimientos, 0 días, 0 cuentas, estado `ok`**, después de 382 a las 15:01.

**Dos formas de traer, y no se miden igual.** Salió de los números, no de la
teoría. Los jobs **diarios** (`aum` 1.044 a 1.062 cuentas, `sync_comitentes`
1.970, `ap5` 460, `snapshot_cierre` 193 a 198) son estables: se comparan
contra la mediana de sus últimas corridas ok. Los **acumulados**
(`operaciones_informes` va de 1.583 a las 15:03 a 3.032 a las 11:31 del día
siguiente, por su ventana de dos días) crecen durante el día: compararlos
contra una mediana de corridas mezcladas es comparar la mañana con la tarde.
Se comparan contra la corrida anterior **del mismo día**, y la primera del
día no opina: un lunes trae menos que un viernes a la tarde.

**Cómo quedó.** `reportes.VOLUMENES` declara, por job, cuál es su contador y
de qué forma crece; `trajo_poco` (`agente/detectores/datos.py`) los compara
cada hora en día hábil. Corte 50%, con tres guardas de umbral: menos de 5
corridas de historia no opina, una referencia menor a 20 no opina (los
números chicos son ruido), y una corrida en seco o solo-maestro (los jobs la
marcan con `modo`) no cuenta ni como hoy ni como referencia. Una segunda regla,
`volumen_sin_dato`, canta cuando la última corrida no trae el contador
declarado: es lo que impide que la lista se pudra en silencio.

**Lo que se dejó afuera, y por qué.** `research_mail` (2 y 3 mails: cualquier
corte es ruido), `precios_acciones_daily` (las velas van de 616 a 930 según
el día; no es volumen de ingesta) y `argentina_datos` (no guarda ningún
número). Y `aranceles.sin_match` **se sacó de REPORTES** el mismo día: medido,
son 2.530 de 3.187 informes (80%) todos los días — la base, no una anomalía.
Se había declarado sin medir; REGLA #2.

**De yapa.** `precios_acciones_daily` estaba `partial` **todos los días desde
el 17/08** por un ticker que falla siempre. Ahora guarda `errores_lista` y
entra por `job_reporto`: un color permanente pasa a ser un aviso con el ticker.

### 0.dl EL ALTA DE UN CEDEAR — la cadena entera, por FICHA, sin reiniciar el motor (2026-09-04)

**Qué había.** Sumar un CEDEAR era `scripts/add_cedear`: escribía una fila en
`mercado.cedears` y terminaba con «⚠️ reiniciá `motor_cedears.service`». Nadie
verificaba que el símbolo existiera en Primary (`add_cedears_bulk` sí probaba,
pero por REST y a mano), nadie traía la historia del subyacente hasta la
corrida nocturna, y la fila quedaba sin precio hasta el próximo arranque del
motor — que en rueda significa cortarle el feed a la mesa para sumar un papel.
Y el editor de Manager → TÍTULOS → RENTA VARIABLE sólo edita lo que ya existe:
no había ninguna pantalla desde la que dar de alta.

**Qué se pidió** (user, 2026-09-04): *«una skill que permita agregar de manera
integral cualquier CEDEAR que yo elija: que busque si existe en Primary y, si
existe, que lo agregue para que cumpla con todo — renta variable, títulos,
suscripción, que se guarden los datos»*.

**Cómo se decide qué ES un CEDEAR.** Es la parte que no se puede resolver con
un string. `MERV - XMEV - NVDA - 24hs` y `MERV - XMEV - GGAL - 24hs` tienen la
misma forma y uno es CEDEAR y el otro una acción local; `NVDAD` y `NVDA - CI`
son el mismo CEDEAR por otra pata. Lo que sí los distingue es la ficha que les
pone Primary (`cficode`, moneda, plazo) — y esa ficha **no se escribe en una
constante**: el detector la CALIBRA leyendo la de los CEDEARs que ya tenemos
(`agente/alta_cedear.candidatos`, pura). Con menos de tres propios compartiendo
ficha no afirma nada y levanta `SinDatos`. Así el día que Primary recodifique,
se recalibra sola en vez de comparar contra un número viejo en silencio. Para
leer la ficha se sumó `core/instrumentos_validos.fichas()`, al lado del lector
único de la foto: el agente no abre `manager.pyrofex_instruments` por su cuenta.

**Por qué un hallazgo por familia y una persona que tilda.** Primary lista
muchos más CEDEARs que los que la mesa mira. Un hallazgo por cada uno sería la
lista que nadie lee (§0.x); un botón que los sume a todos convertiría el
scanner en la guía telefónica. El sujeto es `CEDEAR`, el número sube y baja, y
`alta_cedear` es el segundo arreglo que **pide datos** — por la razón opuesta a
`completar_ficha`: acá el sistema sabe escribirlo todo, lo que no puede decidir
es cuáles. La segunda regla, `no_cotiza_en_primary`, es al revés y sí va por
título: un activo nuestro que Primary no lista nunca va a tener precio (el WS
lo filtra por la misma foto), y no tiene botón porque corregir un símbolo o
apagar un papel lo decide la mesa en Manager.

**La cadena, y por qué el master va primero.** `alta_cedear.aplicar` recorre:
Primary (foto **y en vivo**, reusando `alta.estado_simbolo` — la lección de
S29E7, §0.cy) → ficha → `core/cedears_sql.alta`, la **puerta única** por la que
ahora también entra el script (REGLA #9 B: había dos `INSERT` copiados) →
historia EOD por `precios_acciones_daily.backfill_ticker` → ADR por
`adr_live.upsert_uno`. El master se escribe primero porque es lo que hace que
el CEDEAR EXISTA para el motor y los jobs; Yahoo y Finnhub son mejoras, y si no
contestan se anota en el libro (una línea por eslabón) y el nocturno completa.
Sin poder afirmar que Primary lo lista **no se escribe**: «no pude preguntar»
no habilita un alta.

**El motor relee el master.** `engines/motor_cedears` tiene ahora un
`_master_watcher` (hilo vital, cada 60 s) que suscribe lo que apareció en
`mercado.cedears`, con el mismo patrón que el `adhoc_watcher` de `valores.py`:
el master ES el pedido. Anota `relee_master=True` en su latido, y eso es lo
que el pre-flight del alta mira para prometer «lo suscribe en ≤ 60 s» en vez
de «reiniciá el motor». Sólo SUMA: dar de baja o cambiar un símbolo sigue
pidiendo reinicio fuera de rueda. ⚠️ Hasta que el motor en el Droplet corra
esta versión, el pre-flight lo dice («corre código SIN relector») y el primer
reinicio se hace fuera de rueda; `deploy.sh` no reinicia motores.

**Lo que NO hace.** No decide `rubro`, `es_ia`, `ric` ni `ratio`: eso sigue
cargándose en Manager, y el alta nunca los pisa. No siembra `mercado.especies`
ni `portafolio.assets`: un CEDEAR entra al AuM sólo cuando una cuenta lo
tiene, y de eso se ocupa `assets_autofill` como con cualquier título.
