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

Y el tamaño le da la razón. Medido sobre el repo **antes de rehacerlo**:

| | El agente viejo |
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
| `usa_ia` | guardado — **es «¿DETECTA con IA?», y es `no` en TODAS**. Desde §0.dn el agente sí usa el gateway, pero sólo para REDACTAR el texto de los avisos (`agente/redactar.py`): ningún hallazgo se abre ni se cierra por lo que diga un modelo, y un test lo congela. |
| `cada_cuanto` | guardado — su propio ritmo |
| `ventana` | guardado — cuándo tiene sentido mirar |
| `activa` | guardado — se puede apagar sin tocar código |
| `ultima_corrida_at` | **guardado** |
| `ultimo_resultado` | **guardado** — `ok` · `sin_datos` · `error` |
| `ultimo_error` | guardado — con la corrida en `ok` lleva lo que la habilidad **no pudo mirar** (`tipos.NoMirado`, §0.fa): «no pude mirar 1: mercado.x (relation does not exist)» |
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
| `en_curso` | se aplicó el arreglo y el aviso sigue abierto. ⚠️ **No significa «esperando»**: eso se mide con `arreglo_aplicado_at` contra `visto_ultima_vez` (§0.fe) — para un hallazgo de FAMILIA el detector ya volvió y lo sigue viendo, porque queda cola |
| `resuelto` | ya no está — **siempre con `cerrado_como`: acción, ausencia o caducidad** (§6.8) |
| `ignorado` | una persona dijo "no me interesa" — reversible |
| `reincidio` | estaba resuelto por acción y volvió — **sigue ABIERTO**: se actualiza, se cierra y se muestra igual que `nuevo` (§0.cy) |

Se eliminó `visto` del modelo viejo: "alguien lo miró y no hizo nada" no se
atiende distinto de `nuevo`, y un estado de más es una rama de más en cada
pantalla, para siempre.

---

## 2. El motor — un agente, una agenda

### 2.1 El problema que había

El agente viejo tenía CUATRO programas separados haciendo exactamente lo mismo
—despertarse, mirar, anotar— y lo único que los diferenciaba era el ritmo:

| Reloj | Ritmo | Qué mira |
|---|---|---|
| `av_agent_centinela.service` (daemon) | 30s en rueda / 5 min fuera | precios, motores, latencia, proveedores |
| `jobs.av_agent_sistema` (cron) | cada 10 min | crontab, tablas, datos partidos |
| `jobs.av_agent` (cron) | 4×/día hábil | bonos contra 1816 |
| `jobs.db_tamano` (cron) | 23:30 | permisos flojos |

El cuarto era el peor: **un detector de seguridad viviendo adentro de un job que
no era del agente.** Alguien que tocara ese job por otro motivo apagaba un
chequeo sin enterarse. (Es el invariante #8, que nació de acá.)

Y tenía dos consecuencias que se veían en pantalla todos los días:

1. **Si se caía uno, los otros tres seguían mostrando datos frescos.** La
   pantalla se veía viva con un cuarto del agente muerto — peor que estar caído
   entero.
2. **Cada reloj llevaba su propio horario**, y la pantalla los mezclaba:
   mostraba algo de hace 10 minutos al lado de algo de hace 4 horas sin decir
   cuál era cuál.

### 2.2 Cómo queda

**Un solo agente, siempre vivo, con una agenda.** Adentro tiene el catálogo de
habilidades; cada una declara **su propio ritmo y su propia ventana**. El agente
se despierta, pregunta *¿a quién le toca ahora?*, la corre, anota el resultado
**en la tabla de habilidades corra o no corra**, y sigue.

Sumar una habilidad es **una fila en el catálogo**, no un programa nuevo.

**Lo único que hay que resolver a propósito:** que una habilidad lenta no tape a
una rápida. Si el barrido de bonos tarda 2 minutos, el monitor de 30 segundos no
puede quedarse esperando. Es una decisión, no algo que se descubre después.

### 2.3 El modo de guardado no existe

En el agente viejo la puerta de escritura miraba **el nombre del alcance** y
decidía sola si pisaba o si acumulaba. Eran **tres nombres escritos a mano**
(`live`, `sistema`, `superficie`): un reloj nuevo que se llamara distinto entraba
al modo equivocado y nadie se enteraba.

Acá **no hay modos**. Un hallazgo es un evento y siempre se inserta. Lo que
antes resolvía el modo "reemplazo" —no dejar 2.880 avisos del mismo problema por
día— lo resuelve la identidad: si el trío ya tiene un hallazgo abierto, se
actualiza su `visto_ultima_vez`; no nace otro.

### 2.4 "¿Qué hago si no puedo mirar?" — se contesta UNA vez

Es la pregunta más importante del sistema, porque **"no encontré nada" y "no
pude mirar" se ven iguales en la pantalla**, y uno significa que está todo bien
y el otro que estás ciego.

En el agente viejo **la contestaba cada detector por su cuenta**, con su propio
bloque de código, y no la contestaban igual: unos devolvían vacío en silencio,
otros emitían un hallazgo que lo decía, y uno reventaba a propósito. Los tres
comportamientos son defendibles; el problema es que era **la misma decisión
tomada diecinueve veces por separado**, y la vigésima se iba a tomar mal.

**Acá no es problema del detector.** El detector mira y devuelve, o falla. El
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
habilidad encuentra algo que hoy nadie sabe arreglar*. En el agente viejo pasaba
y no estaba dicho en ningún lado — el user los veía en la lista, no había botón,
y la lista nunca bajaba.

⚠️ **Y se declara por REGLA, no por habilidad** (§6.3): una habilidad puede
tener reglas de las dos clases.

---

## 4. El esquema SQL

> ⚠️⚠️ **EL DDL NO SE COPIA ACÁ. Vive en `sql/schema.sql` y ahí manda.**
>
> Hasta el 2026-09-05 esta sección traía el `CREATE TABLE` de las tres tablas
> pegado. **Quedó viejo sin que nada fallara**, que es exactamente el modo de
> falla que el agente persigue en los datos (REGLA #9): dos copias del mismo
> dato, cada una coherente consigo misma, ninguna arbitrando. Lo que la copia
> decía y la base desmiente: no tenía `cerrado_como = 'caducidad'` (§6.8), ni
> la columna `sujeto_es`, ni `reincidio` adentro del índice único de abiertos
> (§0.cz), ni las columnas `ia_*` (§0.dn), ni `detalle`, y colgaba `arreglo` de
> la HABILIDAD cuando hoy cuelga de la REGLA (§6.3).
>
> Un doc que se declara la especificación viva no puede tener una copia del
> schema: o la genera un script, o se la pide a la fuente. Acá se la pide.

**Dónde vive cada cosa:**

| Qué | Fuente única |
|---|---|
| Las tablas del schema `agente`, sus índices y sus CHECK | `sql/schema.sql`, bloque «EL AV AGENT» |
| Las vistas que dibujan las pantallas (`v_ahora`, `v_encontro`, `v_habilidades`) | ídem, al final de ese bloque |
| Qué habilidades hay, con su ritmo, su ventana y sus umbrales | `agente/catalogo.py` |
| Qué significa cada estado, cada cierre y cada severidad | `agente/tipos.py` |
| Cuántas tablas y cuántas habilidades hay | las dos fuentes de arriba, nunca este doc |

Lo que **sí** vive acá son las decisiones que ese DDL codifica y que un
`CREATE TABLE` no explica. Son las que hay que respetar al tocarlo.

### 4.1 Cuatro tablas llevan el MODELO; el resto es infraestructura

`habilidades` (el catálogo, y cuándo corrió cada una) · `hallazgos` (los
eventos) · `reincidencias` (la que debe estar vacía) · `acciones` (el libro).
**Ninguna otra decide si algo es un problema** — ese es el invariante #5.

Las demás son infraestructura y ninguna nace de un detector: `latido` (una
fila) · `silenciados` (lo que una persona mandó a callar) · `db_peso` (la serie
del tamaño de la base) · `avisos_dirigidos` (la bandeja hacia una persona) ·
`tasa_1816` (la lista de prioridad) · `pulso_cliente` (lo que reporta el
navegador, §0.dg) · `explicaciones` (el caché de «explicámelo», §0.dh).

⚠️ Decir «cuatro» a secas hace que `silenciados` parezca no existir, y es la
que explica por qué un problema real puede no aparecer en pantalla.

### 4.2 Las decisiones que el DDL codifica

**`corridas_hoy` va con `corridas_dia`.** Un contador sin la fecha del día que
cuenta miente en el primer cambio de día. Se resetea en el mismo `UPDATE` que lo
incrementa: sin cron de limpieza que se pueda olvidar.

**`hallazgos_total`, `ultimo_hallazgo_at` y `reincidencias` NO son columnas.**
Se derivan en la lectura, en `v_habilidades`. Guardarlos sería una segunda
verdad que se desincroniza sola (REGLA #9).

**El índice único parcial de `hallazgos` es lo que reemplaza al «modo
reemplazo»** del agente viejo: `UNIQUE (habilidad, sujeto, regla) WHERE estado`
abierto. Si el trío ya está abierto se actualiza `veces` y `visto_ultima_vez`;
no nace otro. Sin él, un monitor de 30 segundos deja 2.880 filas del mismo
problema por día. ⚠️ Y `reincidio` tiene que estar adentro de ese `WHERE`: sin
eso, cada corrida que volvía a ver un trío reincidente insertaba otra fila
(§0.cz).

**Dos CHECK sostienen invariantes, no formatos.** `btrim(que_hacer) <> ''` es el
invariante #2 puesto en la base; `(estado = 'resuelto') = (cerrado_at IS NOT
NULL)` impide un cerrado sin fecha o una fecha sin cierre, que no significan
nada ninguno de los dos.

**No hay constraint que impida una reincidencia sobre un cierre por ausencia.**
La base no puede expresar «el previo tiene que estar cerrado como `accion`» sin
un trigger, así que **la guarda vive en la única función que inserta**
(`registro._ver`) y la sostiene un test. Es el mismo criterio que la puerta
única de escritura: donde la base no puede, el código tiene UN solo lugar.

**`arreglo_aplicado_at` existe porque un ESTADO no puede decir «esperando».**
`en_curso` es el mismo valor para «se aplicó hace diez segundos y el detector no
volvió» y para «se aplicó hace dos semanas, el detector vuelve cada hora y lo
sigue viendo porque la cola no llegó a cero». Son dos cosas opuestas —una es
paciencia, la otra es trabajo— y sin la hora la pantalla tiene que suponer: decía
«esperando que el detector confirme» al lado de un «confirmado» de hace diez
minutos (§0.fe). La compara `v_encontro` en `espera_al_detector`, una sola vez,
porque el navegador no deriva (invariante 11).

**`dias_aguanto` es una columna generada**: se calcula de sus dos insumos y no
puede contradecirlos. Es lo contrario de un acumulado que se deriva en la
lectura — acá los insumos no cambian nunca.

**Las columnas nuevas van PEGADAS a su tabla, no al final del archivo.**
`apply_schema` ejecuta en orden y las vistas del final seleccionan esas
columnas: un `ALTER` después de la vista corta el deploy con «column no
exists». Y las vistas se **dropean antes de recrearse**: `CREATE OR REPLACE
VIEW` solo sabe agregar columnas al final, así que una columna nueva en el
medio se lee como un renombre y también corta el deploy. Los dos casos están
congelados por test.

### 4.3 Lo que NO tiene tabla, a propósito

- **La foto por corrida.** El agente viejo guardaba 60 corridas de fotos. Con un
  hallazgo abierto que lleva `veces` y `visto_ultima_vez`, la foto no agrega
  nada que no esté.
- **Los objetos aparte de los eventos.** En el agente viejo eran dos tablas
  (`hallazgos` + `items`) que se escribían juntas y «no podían divergir». Acá el
  hallazgo abierto **es** el objeto: una tabla, una verdad.
- **El estado de un arreglo.** El hallazgo guarda `arreglo_aplicado` y el LIBRO
  guarda qué escribió; no hay una máquina de estados aparte.
- **Una tabla de propuestas.** `preview` se recalcula al mirar y `aplicar`
  vuelve a calcular antes de escribir: lo que se aplica es lo cierto AHORA, no
  lo que era cierto cuando alguien abrió la pantalla.

---

## 5. Las habilidades — la decisión de cada una

> ⚠️ **LA LISTA NO ESTÁ ACÁ: está en `agente/catalogo.py`.** Una fila por
> habilidad, con su ritmo, su ventana, sus umbrales y sus arreglos. Copiarla acá
> sería la REGLA #9 otra vez, y ya pasó: esta sección describía habilidades que
> nunca existieron (`deteccion_primary`) y otras que se borraron hace meses
> (`respuesta`, `recuperado`, `motor_ruidoso`), con el mismo tono de spec que el
> resto.
>
> Lo que vive acá es **el POR QUÉ de las que no son obvias**: la que reemplazó a
> otra, la que se partió en dos, la que decide algo raro. Una habilidad que hace
> lo que dice su `que_mira` no necesita párrafo.

### 5.1 Las que reemplazaron a otra, y por qué

#### `soberanos_faltantes` (reemplazó a `falta_en_base`)

Bonos que 1816 lista y no están en `mercado.curvas`.

**El cruce va por `mercado.curvas.ticker` DIRECTO.** El detector viejo le sacaba
la letra D/C final «por las dudas», y eso era una defensa contra un caso que el
renombre de columnas de 2026-08-15 ya cerró: `ticker` es la PK (`AL30`) y el
símbolo de mercado vive en `instrumento`. Sacar un sufijo para adivinar el
ticker base es exactamente la REGLA #9(A) — `TXAD` se convierte en `TXA`.

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

#### `tasa_vs_1816` — habilidad NUEVA (2026-09-04)

Corporativos HARD DÓLAR: la tabla de nuestra TNA/TEA al lado de la de 1816
(ticker · TNA mía · TNA 1816 · TEA mía · TEA 1816), en **UN hallazgo** que el
registro refresca cada corrida. **Sin umbral, sin filtro, sin decir cuál está
mal**: compara, no concluye. Es `scripts/diag_tea_corp_hd` hecho habilidad.
Aviso (vive en AHORA), rueda, cada 2 h. Historia en §0.do.

#### `on_faltante` — habilidad NUEVA (2026-09-04)

ONs hard dólar que 1816 lista, **Primary cotiza**, y no están en
`mercado.curvas`. Es `soberanos_faltantes` para el otro lado del mostrador
(`ALCANCE = {soberano, bcra}` las dejaba afuera a propósito). Historia en §0.dp.

Dos diferencias con su hermano, ninguna de gusto: **Primary es condición, no
filtro** (sin foto → `SinDatos`, no se ofrece nada), y el arreglo es por REGLA:
`no_esta_en_curvas` (la mesa lo TIENE y no valúa) → `alta_bono`, y la fila de
familia `no_estan_en_curvas` (lo que 1816 publica y no tenemos) → `alta_on`, que
despliega la lista para tildar (§0.du, §0.dv). Nació sin arreglo mientras la
rama `on` no estaba en `alta.RAMAS_AUTOMATICAS`.

**La oferta se descarta por ticker, no entera** (§0.eh): `fuentes.ons_no_interesan`
(la lista del conciliador de ONs) se descuenta de la familia, y la fila renace
sola cuando 1816 publica una ON que no está en la lista.

**Y es la primera regla que el agente aplica SOLO** (§0.ef): `no_esta_en_curvas`
está declarada en `automatico`. El ejecutor (`agente/autonomo.py`) la aplica en
cada pasada por la misma puerta que el botón, y el pre-flight sigue siendo el
juez: lo que no cierra queda como botón para una persona.

#### «¿Este símbolo cotiza en Primary?» — NO es una habilidad

Se planeó como una habilidad tipo `consulta` (`deteccion_primary`) y **fue la
decisión equivocada**: una consulta no produce hallazgos, así que habría
figurado en el catálogo con 0 para siempre y se habría leído como rota.

La pregunta la hacen cuatro detectores y tres arreglos, y la respuesta tiene que
ser UNA. Vive en `agente/fuentes.primary()`, que a su vez **delega en
`core/instrumentos_validos`** — el mismo criterio que `core/websocket.py` aplica
a todas las suscripciones de todos los motores. Dos lectores de «¿esto cotiza?»
terminan siempre igual: uno se queda viejo y nadie sabe cuál manda.

⚠️ Y la degradación vive allá también: `None` cuando el catálogo no se puede
leer o tiene menos de 100 símbolos. **Ante la duda no se filtra** — filtrar de
más deja papeles sin precio.

#### `bono_sin_flujo` (era `sin_flujo`)

Bonos cargados sin cronograma de pagos. **El predicado se conserva** (mira la
definición de flujo, no si el array está vacío — una LECAP es cupón cero y no le
falta nada).

**Lo que se arregló no fue el detector: fue el CABLEADO.** Las dos piezas del
arreglo ya existían y estaban desconectadas:

- `core/mercado_1816.cashflow(ticker)` trae el cronograma completo.
- La cadena de alta (`agente/alta.py`) hace todo el trabajo: ticker → catálogo
  1816 ya persistido (0 créditos) → baja el cuadro → simula la TEA → coteja →
  **solo escribe si la cadena cierra**. Y si 1816 no lo tiene, lo dice.

El problema era que esa acción colgaba de **otro** control (`titulos_sin_flujo`,
de Manager, que habla en unidades de Aunesa) y no del detector (que habla en
tickers del master): dos mundos puenteados a mano.

Hoy la habilidad **declara su arreglo en su fila** (`arreglos={"sin_flujo":
"alta_flujos"}`) y eso no puede volver a pasar — un test exige que todo arreglo
declarado exista.

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

#### `motor_caido` y `motor_latido` — se partió en DOS, y por eso funciona

Al principio `motor_caido` juzgaba las 53 piezas con **una sola definición de
«caído»**: su tabla no recibió escrituras en X segundos, con X puesto a mano. Y
la ventana horaria salía de una **expresión regular sobre prosa** (la cadencia
declarada como texto libre, `"cada 30m :05,:35 · 15-22 UTC L-V"`); si la prosa
estaba escrita distinto no encontraba nada y **avisaba igual**.

Las dos mitades se corrigieron por separado, y la lección es la misma:

**(a) Un PROCESO no se juzga por su tabla** (§0.da). Un motor de precios sobre un
papel ilíquido no escribe y no está caído — «mercado quieto» y «muerto» se veían
iguales. Los procesos pasaron a `motor_latido`, que los juzga por **su propio
latido** (`operaciones.latidos`, que cada motor escribe solo) con cuatro
veredictos que se atienden distinto: `apagado` · `sin_latido` · `colgado` ·
`sin_feed`/`feed_mudo`. Y el universo no es una lista: sale de
`deploy/systemd/*.service` + `deploy/crontab.txt`, así que **un motor nuevo se
espera solo**. `motor_caido` quedó para los JOBS, que sí se juzgan por resultado.

**(b) El horario sale del CRON, no de un regex** (§0.db). Lo evalúa
`salud.ultima_ejecucion_esperada`, el único evaluador cron del repo, sobre los
`schedules` que `rehacer.rehacibles()` deriva de `deploy/crontab.txt`. La misma
fuente que ya leía `cron_desalineado`, usada una vez.

Las tres guardas originales quedaron porque eran correctas: gracia de arranque,
no juzgar una pieza fuera de su ventana, y no juzgar un job al que todavía no le
tocó.

⚠️ Y la corrección más cara vino después: **el día que falta no depende de que
alguien haya notado el fallo.** El veredicto del árbol PARPADEA —el 28/08 el job
del AuM falló a las 08:00, la tarjeta apareció, y al mediodía `motor_caido` tenía
cero abiertos con el día todavía sin escribirse—. La pregunta que decide («¿está
el día en la tabla?») no se apaga nunca, así que ahora se hace igual, la haya
visto el árbol o no.

#### `db_peso` (reemplazó a `db_cambio`) — en vivo, y dos preguntas distintas

> *«esto tiene que ser más realtime y mostrar el peso que va dando de cada
> tabla, no con la foto de ayer»* — user.

El tamaño de una tabla es **una query barata contra el catálogo de Postgres**: no
hace falta una foto diaria. Lo que cambia al medir en vivo es contra qué se
compara — se guarda una serie horaria (se purga sola) y de la misma fuente salen
dos cosas que **no son lo mismo**:

- **el peso de cada tabla** → información, no hallazgo. Llenar la pantalla con
  200 tamaños es el ruido que hace que nadie mire;
- **lo que creció fuera de lo suyo en 24 h** → eso sí.

⚠️ Y una tercera, que es la que enseñó algo: **«estaba hace 24 h y ahora no»
sirve UN día.** La referencia se mueve, así que una tabla borrada el martes deja
de verse el miércoles a la noche — y sigue sin estar. La pregunta que no caduca
(«¿está la que `sql/schema.sql` dice que tiene que existir?») se contesta
siempre. Las dos conviven: la primera atrapa lo que no está declarado, la
segunda no vence nunca.

### 5.2 Las que se descartaron, y no volver sobre esto

| | Por qué |
|---|---|
| `motor_ruidoso` | *«no sé si sirve sinceramente, con que esté bien el motor_caido alcanza»*. Se conservó UNA cosa, como evidencia y no como habilidad: cuando algo se cae, que el hallazgo traiga lo último que dijo antes de morir. |
| `respuesta` · `recuperado` | Eran del agente viejo. Lo que hacían lo hace la estructura: un arreglo deja el hallazgo `en_curso` y **el detector confirma** en su próxima pasada; lo que vuelve, vuelve por `reincidencias`. |
| Vigilar el presupuesto de créditos de 1816 | Descartado por el user. |
| Cualquier cosa alrededor del discovery de instrumentos | Descartado por el user: `jobs.validar_instrumentos` y el catálogo de especies contra Primary **no son terreno del agente**. |

### 5.3 El hueco que quedó cerrado

**Nadie miraba que un arreglo aplicado hubiera quedado.** Lo resuelve
`reincidencias` (§1.3): si algo que se dio por arreglado vuelve, hay una fila. No
es una lista de trabajo — es una alarma, y su valor es que **debe estar vacía**.

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

#### Cuánto achicó ENCONTRÓ esta regla — la medición que la justificó

⚠️ **Es una foto con fecha (2026-08-24), no una descripción de hoy.** Se
conserva porque es la evidencia de por qué la regla vale.

Medido sobre el agente viejo, cruzando sus 19 detectores contra sus 10 acciones
por la causa que cada acción declaraba:

| | |
|---|---|
| Detectores con arreglo REAL | **3** — `sin_flujo`, `sin_precio`, `precio_moneda` |
| Detectores con arreglo PARCIAL | **1** — `tasa_sospechosa`, y solo 1 de sus 6 reglas |
| Detectores **declarados como accionables sin serlo** | **2** — `salud`, `falta_en_base` |
| Detectores sin ninguna puerta | **13** |
| **Acciones que NO colgaban de ningún detector** | **5 de 10** |

O sea: ENCONTRÓ mostraba 19 tipos y solo 3 o 4 tenían algo que apretar. Los
otros 15 **simulaban** que sí — y por eso la lista no se podía apretar casi
nunca y se dejó de leer. Sacarlos no fue perder cobertura: fue mandarlos al
noticiero, que es su lugar.

Las cinco acciones huérfanas —`assets.cartera`, `assets.fci`,
`contrapartes.alta`, `avisar.responsable`, `sistema.rehacer_dia`— colgaban de
controles de Manager y no de habilidades: funcionaban, pero **el agente no las
conocía como suyas**. Hoy eso no puede volver a pasar: un arreglo se declara en
la fila de la habilidad (`arreglos={regla: id}`) y **un test exige que todo
arreglo declarado exista y que todo arreglo escriba**.

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

⚠️ **Hoy hay UN tipo: `bono`** (las que lo declaran salen de `catalogo.py`, no
de acá). No es recorte de ambición, es la
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

### 6.10 AGUDO vs CRÓNICO — la pregunta que decide qué hacer (2026-09-04)

El agente miraba **cada hallazgo aislado**. Por eso un job que no escribió HOY y
uno que no escribe TODOS LOS DÍAS se veían idénticos, y los dos terminaban en la
misma frase: *«Relanzar `jobs.interbanking_sync`»*.

Para el primero está bien. **Para el segundo, relanzar ES el parche.**

> *«capaz está mal que el cron diga 14hs — el agente debe poder buscar mejoras y
> potenciar lo que puede llegar a haber mal, no dejar todo como está y
> parchear»* — el user, 2026-09-04.

#### Un EPISODIO no es una VISTA

| | Qué cuenta |
|---|---|
| `veces` | cuántas veces el detector VIO este problema (sube en cada pasada) |
| `episodios` | cuántas veces el problema **NACIÓ** en 30 días |

La diferencia importa: un problema que persiste **no crea fila nueva** (sube
`veces`). Así que tres episodios son tres veces que **apareció, se fue y
volvió** — que es exactamente lo que un incidente aislado NO hace. Confundirlos
daría «crónico» a cualquier cosa que lleve un rato abierta.

**Tres, y no dos**: dos veces en un mes puede ser casualidad; tres ya es un
ritmo. El umbral vive en `agente/tipos.py` y en ningún otro lado — un test
prohíbe que se copie al schema o al diag (REGLA #9).

#### Qué cambia con esto

```
        aparece un hallazgo
                ↓
      ¿AGUDO o CRÓNICO?
        │            │
     AGUDO        CRÓNICO
   es un          NO es un incidente: es una configuración mal puesta.
   incidente      Relanzarlo lo TAPA. Lo que hay que revisar es el
   → arreglar     umbral, el cron, o si el job sigue haciendo falta
```

#### La pregunta que decide: ¿el sujeto es UNA COSA o un GRUPO? (§0.ep)

⚠️⚠️ **Los episodios se cuentan sobre EL MISMO SUJETO, y eso sólo significa algo
si el sujeto es una cosa fija** — un job, un motor, una tabla, un ticker. Ahí
«nació tres veces» es un patrón: se rompe, se arregla y se vuelve a romper.

Si el sujeto es un **GRUPO** —`ONs HARD DÓLAR`, `CEDEAR`, `CARTERA`, el peso de
la base—, la fila nace, se vacía y **vuelve a nacer cada vez que el mundo
crece**. Sus episodios miden cuánto creció el mercado, no una falla. Y nunca
llegan a cero, porque siempre hay un título más.

Por eso cada regla declara su **naturaleza** en el catálogo, con vocabulario
cerrado (`agente/tipos.NATURALEZAS`):

| | Cuándo | Cuenta episodios |
|---|---|---|
| `INCIDENTE` | se rompió algo que no debería | **sí** — puede ser crónico |
| `INFORME` | nace por CALENDARIO (el peso de la base, la tabla de 1816) | no |
| `RECURRENTE` | nace porque el UNIVERSO CRECIÓ (títulos, cuentas, clientes) | no |

`INCIDENTE` es el default de toda regla no declarada. **Toda regla CON ARREGLO
está obligada a declararse**: lo exige `Habilidad.__post_init__`, así que la
habilidad no se puede ni construir sin contestar la pregunta — falla al
importar, no dentro de un mes cuando alguien mire la pantalla.

Hoy sólo se MUESTRA (`⚠ crónico · 27× en 30d` en AHORA y ENCONTRÓ, y el ranking
completo en `scripts/diag_agente`). **No cambia ningún comportamiento todavía** —
y eso es a propósito: no se puede decidir si dejar que el agente arregle solo
hasta saber si arreglar es lo correcto para ese problema.

#### La columna que decide es la DURACIÓN, no la cuenta

El ranking trae la **mediana** de cuánto duró cada episodio (mediana y no
promedio: un episodio de cuatro horas entre cuarenta de tres minutos mueve el
promedio a doce y cuenta una historia que no pasó). Sin ella, dos casos opuestos
se leen igual:

| | Qué significa | Qué se hace |
|---|---|---|
| 41 episodios de **3 min** | el umbral es demasiado sensible | cambiar un número |
| 41 episodios de **2 horas** | se cae de verdad | hablar con quien lo rompe |

Y el ranking separa **CRÓNICO ACTIVO** (pasó en los últimos 7 días) de **CRÓNICO
HISTÓRICO**. Medido la primera vez que se listó (2026-09-04): de 25 crónicos,
**once ya no pasaban** —seis `soberanos_faltantes` silenciados el 26/08 y cuatro
`precio_viejo` cortados el 28/08— y competían por atención con los que rompen
hoy. Es el mismo error que el agente persigue en los datos, cometido por la
herramienta que lo mide.

⚠️ **Y los umbrales se editan EN CALIENTE** (`agente.habilidades.umbrales`), sin
deploy: el código trae el default y la base lo pisa (§7). Por eso «revisá el
umbral» no es una tarea de programación.

#### La guarda

⚠️ **«No pude contar» NO es «es la primera vez».** Si la consulta del historial
falla, `episodios` queda en `None`, `cronico` en `False`, y el front **no dibuja
nada** — no un «1ª vez». Poner un número porque no se pudo contar sería afirmar
lo contrario de la verdad justo cuando el sistema está más ciego: el invariante
1, aplicado a la pantalla.

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

1. **Una habilidad que no corrió no cierra nada.** Nunca. **Y de a uno**
   (§0.fa): un detector que no pudo mirar UN sujeto lo devuelve como
   `tipos.NoMirado`, y ese sujeto no se crea ni se cierra — la corrida sigue
   `ok` para los demás. Nunca se juzga con una foto vieja «diciéndolo».
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
14. **Lo automático entra por la misma puerta y firma como agente** (§0.ef).
    Lo que el agente aplica solo pasa por `arreglos.aplicar` —mismo pre-flight,
    mismo libro, mismo estado— con el actor `tipos.ACTOR_AGENTE`, nunca por un
    camino propio. Una regla se aplica sola únicamente si su habilidad lo
    declara en `automatico`, y el ejecutor no reintenta lo que ya intentó.
    Un arreglo que PIDE DATOS solo puede ser automático si sabe decir qué
    escribiría (`Arreglo.solo`, §0.ei), y eso es únicamente lo determinístico:
    lo que propone el modelo nunca se escribe sin una persona.
15. **El agente avisa de lo NUESTRO. Un hecho del mundo no es un hallazgo: es
    el SILENCIO** (§0.eu). Si el `que_hacer` de una regla empieza diciendo que
    no hay nada que hacer, esa regla no está mal escrita — no es una regla.
    Es el invariante #2 llevado hasta donde debía llegar: no alcanza con que
    haya un texto en `que_hacer`, tiene que NOMBRAR una acción, y lo exige
    `tipos.Hallazgo` (`tipos.NADA_QUE_HACER`), no un test.
    ⚠️ **Callar sólo es legítimo si lo NUESTRO se descartó primero.** En
    `bono_sin_precio` el silencio significa «iliquidez» únicamente porque las
    tres causas propias —`sin_simbolo`, `simbolo_rechazado`, `no_suscripto`—
    siguen cantando en `alta`. Sacar una de ellas convierte el silencio en un
    error escondido.

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
- **`ia.llamadas`** — toda llamada al modelo queda registrada con tokens, latencia
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
       ej: ia.llamadas, manager.role_audit, manager.salud_eventos

**Y las tres de ejemplo no tienen ningún job atrás.**

    ia.llamadas             ← `core/ai.py`, una fila por CADA llamada al LLM
    manager.role_audit    ← `core/roles.py`, cuando alguien CAMBIA un rol
    manager.salud_eventos ← cuando un chequeo TRANSICIONA

Están quietas **porque no pasó nada**, no porque algo esté roto. Y no hay nada
que relanzar: el job no existe.

> **Casi construyo un botón «relanzar» para esa pared.** Habría sido una puerta a
> ninguna parte — peor que no tener puerta, porque encima promete. La medición
> sirvió para lo contrario de lo que uno espera: no me dijo qué construir, me
> dijo **que la pared más grande no era una pared**.

Es la otra mitad de §0.u. Allá una RÁFAGA se leía como ritmo; acá **un ritmo REAL
se lee como una obligación**: `ia.llamadas` escribe casi todos los días porque se
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

---

### 0.dm SE TILDA Y CON F5 ANDA — el pedido colgado que congelaba la pantalla para siempre (2026-09-04)

> *«estoy detectando algo nuevo en la app que no pasaba… como que se tilda,
> esto no pasaba, empezó a pasar desde hace 1 o 2 días… hay que actualizar la
> página y ahí funciona lo más bien»* (user).

**El defecto, y por qué era invisible.** El 2026-08-31 las 30 vistas que se
refrescan solas pasaron al hook `usePoll`, que **comparte el pedido en vuelo
por URL**: si dos componentes piden lo mismo en el mismo instante, sale un solo
request. La idea es correcta y ahorra la mitad del tráfico del endpoint más
caro. Lo que faltaba es que **el navegador no le pone timeout a `fetch`**: un
request puede quedar pendiente minutos —o no volver nunca— cuando la función de
Vercel no contesta, la notebook durmió o cambió la red.

Las dos cosas juntas dejan de ser inocentes. La promesa colgada quedaba en el
mapa de «en vuelo» y **cada tick siguiente se colgaba de ella**: ese endpoint no
se volvía a pedir en toda la vida de la pestaña. Y como una promesa que no
resuelve tampoco rechaza, no entraba al `catch`, no marcaba ciego y **la barra
ni siquiera decía SIN ACTUALIZAR** (§0.dg). La pantalla se quedaba quieta,
mostrando datos viejos, sin un solo error. El único arreglo era F5 — que es
exactamente lo que hacía la mesa.

Es el mismo modo de falla que persigue la REGLA #9: **cuando esto se rompe no
falla nada**. Por eso costó dos días notarlo y ninguna pantalla lo explicaba.

**Lo que se arregló (frontend).**

- **Techo de 20 s a cada poll** (`lib/use-poll.ts`): el pedido se aborta, el
  tick lo cuenta como fallo con motivo «sin respuesta en 20 s», marca ciego →
  SIN ACTUALIZAR en la barra → pulso al agente, y el **siguiente** tick
  reintenta de verdad. Se recupera solo y, si no, se ve.
- La limpieza del mapa de «en vuelo» cuelga ahora de la **propia promesa**, no
  del `await` de quien llamó: se borra igual si el componente se desmontó en el
  medio.
- **El poll del propio AV AGENT no se superpone consigo mismo** (`agente/
  datos.tsx`): era un `setInterval` pelado, y con el modal abierto (20 s) un
  `/vista` más lento que eso apilaba pedidos — cada uno arma seis consultas y
  ocupa otra conexión del pool web, justo cuando la API ya venía lenta. Ahora el
  timer se re-arma al terminar, con techo de 25 s a las lecturas, y **con la
  pestaña de fondo no pide** (al volver, pide en el acto).

**Y lo que faltaba para no volver a adivinar: EL TILDE.** «Se me colgó la app»
son dos problemas distintos que llegan como la misma frase y se arreglan en
lugares opuestos:

| | qué pasa | quién se entera hoy |
|---|---|---|
| **ciega** (§0.dg) | los pedidos fallan, el navegador anda bien | `usePoll` → pulso |
| **tilde** (esto) | el navegador **no responde**, nada falla | nadie |

Un hilo principal bloqueado no deja request, no tira excepción y no llega nunca
al servidor. `lib/tilde.ts` lo mide desde adentro: un latido cada 500 ms, y si
entre dos latidos pasó mucho más que eso, ese tiempo el navegador no pudo hacer
**nada** —tampoco atender un click—. En paralelo un `PerformanceObserver
('longtask')` dice si el hueco lo explica una tarea de JS:

    hueco grande + longtask parecida  → JS de esta app (render, parse, tabla)
    hueco grande + ningún longtask    → no fue JS: memoria, GC o la máquina

Tres guardas contra el falso positivo, y las tres hacen falta: **pestaña de
fondo no cuenta** (el navegador estrangula sus timers hasta 1 por minuto: ahí un
hueco de 60 s es lo normal), **un hueco enorme es la máquina suspendida** y se
descarta, y **como mucho un aviso por minuto** — si la app se traba en loop, el
reporte no puede ser parte del problema.

Llega por el **mismo** `POST /api/pulso` con `tipo: "tilde"` y a la misma tabla
(`agente.pulso_cliente` gana `tipo`, `ms` y `datos`), y por lo tanto hereda su
retención. Una ruta nueva habría partido en dos lugares la única pregunta que
importa: «¿por qué esta pantalla no anda?». La habilidad **`pantalla_tildada`**
agrupa por vista, dice cuántas veces y la peor, y en el `que_hacer` **manda a
mirar el navegador y no la API**, que es donde el bug no está. Sin arreglo, y
declarado: el agente no puede tocar la pestaña de nadie.

**Y por qué es una HABILIDAD PROPIA y no una regla más de `latencia`** — nació
ahí, estuvo mal media hora y lo cantó el primer `pytest`. `latencia` mira TRES
cosas de tres fuentes distintas; con el tilde adentro, no poder leer **una**
tabla nueva la dejaba entera en «no pude mirar», y con eso se apagaban la
degradación de endpoints y los 5xx, que son lo importante y se leen de otro
lado. (En el test la tabla ni siquiera existía todavía: el `apply_schema` no
había corrido.) Es el invariante 1 leído al revés: **si «no pude mirar» no puede
cerrar nada, tampoco puede ser CONTAGIOSO**. Una habilidad = una fuente que
puede faltar. Va con el ritmo de `latencia` (10') pero con ventana de 60': una
ceguera es AHORA, un tilde es un episodio de segundos que hay que juntar para
que el patrón se vea — uno solo no dice nada, seis en una hora sí.

**Lo que NO se afirma.** Que este sea el bug que vio el user está **sin
verificar** (REGLA #2): no hay acceso al navegador de la mesa. Lo que sí está
verificado es que el defecto existe leyendo el código, y que producía
exactamente ese síntoma. Para decidirlo con un dato y no con una hipótesis está
`scripts/diag_congelamiento.py`, que lee lo que ya se viene registrando: los
pulsos de las pantallas, los tildes y `manager.latencia_endpoints` (qué endpoint
se degradó, desde cuándo y cuánto lo piden).
### 0.dn EL TEXTO DEL AVISO — el único entregable que estaba escrito a mano (2026-09-04)

**Qué había.** De las 24 habilidades, **16 no tienen ningún arreglo**. Todo lo
que producen es un aviso, y en un aviso el TEXTO es el entregable entero: no hay
botón, no hay nada que apretar, lo único que le queda al que lee es la frase.
Esa frase se escribía a mano en el detector, así que era **la misma para todos
los casos de esa regla**:

```python
que_hacer=("Nada: es el número del día. Si el salto de la semana no se "
           "explica con las cinco de arriba, mirar qué creció.")
```

Y le devuelve el trabajo al que lee. *«Mirar qué creció»* es exactamente la
pregunta que la evidencia YA contesta —`top`, `bytes`, `bytes_hace_7d`— y que
nadie mira porque vive adentro de un JSON. Es el mismo defecto que el user marcó
sobre SALUD (*«es todo muy mecánico, no va»*, `api/services/salud.py`) y sobre el
`que_hacer` de los proveedores (`agente/tipos.py`: *«un texto que no cambia con
el caso no informa: entrena a saltearlo»*). Tercera vez, mismo modo de falla.

**Qué se pidió** (user, 2026-09-04): *«me cansé de cosas hardcodeadas, quiero LLM
en este tipo de avisos. El tema es cómo lo hacemos para que tampoco sea una
respuesta o algo berreta»* — y, sobre la forma: *«hacelo para todo pero hacelo
con un patrón de diseño que sea escalable, no puede haber cosas que sirvan sólo
para algo»*.

**La respuesta a «que no sea berreta» no es el prompt.** Berreta lo hace pedirle
al modelo que escriba SOBRE algo que no le diste. Acá no describe el problema:
**contesta la pregunta que el texto fijo deja abierta, con la evidencia
adelante**, y todo lo que dice se verifica antes de mostrarse. Cinco decisiones,
en orden de importancia:

1. **NO DECIDE NADA.** El detector ya dijo que hay un problema, cuál es la
   severidad y cuál es la evidencia. El modelo sólo REDACTA. Ningún hallazgo se
   abre, se cierra ni se reabre por lo que diga: el invariante #1 sigue siendo
   verificable justamente porque «no pude mirar» nunca pasa a ser una opinión.
   `usa_ia` sigue en **False en las 24**, y `test_ninguna_habilidad_detecta_con_ia`
   lo congela — lo que cambió no es que el agente decida con IA, es que explica
   con IA.
2. **EL PISO NO SE PISA.** `que_hacer` —el texto determinista— sigue en su
   columna, intacto; lo del modelo vive en `ia_texto`, al lado. Si el gateway no
   contesta, si no hay presupuesto o si la validación rechaza, el aviso muestra
   lo de siempre. **Meter un modelo acá no puede agregar un modo de falla
   nuevo**, sólo puede mejorar el texto — y apagarlo es `AGENTE_REDACTA=0`, no
   un revert. `test_el_piso_nunca_se_pisa` prohíbe que el UPDATE toque una sola
   columna que no sea `ia_*`.
3. **SE VALIDA MECÁNICAMENTE, no «a ojo».** `redactar.VALIDADORES` es una cadena
   de funciones y la que importa es `_v_numeros`: **todo número del texto tiene
   que estar copiado de los hechos que se le dieron**. Si inventó uno, se
   descarta la respuesta entera. Un texto lindo con un número inventado es
   estrictamente peor que la frase de molde: la frase de molde no informa, el
   número inventado **desinforma con la autoridad de un dato**. Las otras cuatro
   frenan lo que hace berreta a un texto de IA: largo, markdown, muletillas
   («se recomienda revisar», «monitorear», «parece que») y el calco del
   `problema` que ya está dibujado dos renglones arriba.
4. **EL ALCANCE SE DERIVA, no se lista.** Se redacta lo que NO tiene arreglo, y
   el filtro vive en la query de `registro.pendientes_de_texto` (`arreglo = ''`).
   Una habilidad nueva sin botón entra sola; una con botón queda afuera sola —
   ahí el texto ES el botón y el modelo sólo podría contradecirlo. **No hay una
   lista de 16 nombres** que alguien se olvide de actualizar: es la REGLA #10, y
   `test_solo_los_avisos_se_redactan` la recorre entera.
5. **UN SOLO PROMPT PARA LAS 24, Y PARA LA 25.** Lo que varía lo aportan el
   hallazgo (`problema`, `detalle`, `evidencia`) y el catálogo (`que_mira`, ya
   declarado en castellano por la REGLA #10). Un prompt por habilidad sería la
   misma frase de molde de vuelta, escrita en otro archivo y encima pagándola;
   `test_el_redactor_no_escribe_ni_conoce_detectores` prohíbe que el módulo
   nombre una sola habilidad.

**Dónde vive cada mitad**:

| Pieza | Qué hace | Qué NO sabe |
|---|---|---|
| `agente/redactar.py` | arma el pedido, llama al gateway, **valida** | que existe una base y que existe un daemon (es puro: sin SQL) |
| `agente/registro.py` | lee los pendientes y escribe `ia_*` | que existe un modelo |
| `jobs/agente.py::_redactar_avisos` | cuándo corre y cuánto se gasta | cómo se redacta |

`registro.py` escribe porque es **la puerta única** (invariante #5) y hay un
test que prohíbe el resto; que el redactor sea puro es lo que hace que se lo
pueda testear entero sin base, que es donde vive la mitad del valor.

**Las tres guardas de plata**, todas declaradas: el alcance en la query,
`TOPE_POR_PASADA = 4` llamadas por vuelta del daemon, y `MAX_INTENTOS = 2` por
hallazgo — sin este último, un aviso cuya evidencia no alcanza para escribir
nada se pagaría en cada pasada para siempre.

**`ia_rechazo` es la mitad que se olvida.** Guarda POR QUÉ se descartó lo que
escribió (número inventado, muletilla, calco). Sin eso, «el proveedor no
contestó» y «contestó una macana y la tiré» se ven idénticos en la tabla — que
es el invariante #1 aplicado al propio redactor. `ia_traza` apunta a
`ia.llamadas` (modelo, tokens, latencia): el costo no se copia, se referencia
(REGLA #9).

**Cómo se juzga si sirve.** Un test puede congelar que el mecanismo no haga
daño; que el texto informe o no lo tiene que decir una persona leyéndolo. Para
eso está `python -m scripts.diag_redaccion`: pone el piso y el texto del modelo
uno debajo del otro, y agrupa lo rechazado **por motivo**. Si un motivo se
repite, no es ruido: o el prompt pide algo que la evidencia de esa habilidad no
tiene, o esa regla necesita más evidencia antes de poder explicarse.

**En la pantalla**: la tab AHORA dibuja el texto del modelo con una marca `ia` y
la hora; el determinista queda en el `title` para poder comparar los dos sin
gastar pixeles. Ninguna tab nueva.

#### LA PRIMERA CORRIDA REAL — 4 de 66, y el único texto que salió estaba mal

El diag hizo lo suyo el primer día. De 4 intentos: 1 texto, 3 rechazos.

```
▸ db_peso/peso_total_16 · la base
  PISO   : Nada: es el número del día. Si el salto de la semana no se explica
           con las cinco de arriba, mirar qué creció.
  MODELO : Purgá o archivá las cinco tablas que figuran en el error crudo; si
           el peso no baja a 1.3 GB, compará contra el corte de las 16:00 de
           mañana.
```

**Tres fallas, y dos son del pedido, no del modelo:**

1. **Convirtió un «no hay nada que hacer» en «borrá datos»** — y las cinco
   tablas eran las cinco más GRANDES del sistema, no basura. Los cuatro
   validadores lo dejaron pasar porque miraban la FORMA (largo, markdown,
   muletillas, números) y esto está mal **por lo que dice**. Arreglo:
   `_v_manda_destruir`, que es estructural y no prudencia genérica — acá sólo
   llegan hallazgos SIN arreglo, así que un texto que ordena una acción es, por
   construcción, algo que este subsistema no puede afirmar. Y la regla «NO
   ESCALES» en el prompt: *tu texto reemplaza al de hoy y tiene que decir lo
   mismo que él sobre si hay algo que hacer*. Lo anterior decía «el piso,
   **mejoralo**», que se lee como «pedí más».
2. **«el error crudo» es el nombre de una sección de MI pedido** — y encima era
   mentira: `detalle` es el error crudo en `proveedor_caido` y el DESGLOSE en
   `db_peso`. Rotularlo igual para todos hizo que el modelo repitiera un nombre
   que el lector nunca ve. Arreglo: rótulo neutro + prohibido nombrar las
   secciones del pedido.
3. **No nombró una sola tabla — y no podía.** Esta es la lección que vale más
   que las dos anteriores: **la evidencia no traía la respuesta**. `db_peso`
   mandaba `top` = las cinco más grandes (casi siempre las mismas, casi nunca
   las que se movieron) y del pasado sólo el TOTAL, teniendo `peso.referencia()`
   la foto vieja **tabla por tabla**. La pregunta que su propio `que_hacer`
   dejaba abierta no se podía contestar ni a mano. Arreglo: `crecio` en la
   evidencia, aparte de `top`, porque son dos preguntas distintas.

> **Cuando el texto no sirve, mirar primero la EVIDENCIA.** Un prompt no puede
> inventar un dato que no está, y si se lo pide igual, lo inventa — que es
> exactamente lo que hay que evitar. De las tres fallas, la única que importaba
> se arregló en el detector.

**Los dos rechazos cosméticos** (backticks ×2, 269 caracteres contra un tope de
260) eran plata tirada: se pagó la llamada para mostrar el piso por una comilla.
Ahora `limpiar()` **borra** el formato y **corta por oración entera** — sólo se
rechaza lo que cambia el sentido. ⚠️ El `_` no se toca: sin él
`mercado.market_snapshot` queda en `marketsnapshot` y el nombre de la tabla,
que es lo único que sirve del texto, deja de existir.

**`python -m jobs.agente --reredactar`** borra lo escrito de los avisos abiertos
para que se vuelva a redactar. No es una limpieza de una vez: **es la contracara
de tocar el prompt o un validador**, porque el texto se escribe UNA vez por
hallazgo y una corrección no alcanza sola a lo que ya está en pantalla. Acepta
`--skill` para una sola habilidad.

---

### 0.do LA TASA CONTRA 1816 — la primera habilidad que compara un valor (2026-09-04)

**Qué había.** El user pidió *«validar si hay alguna habilidad que valide si
las TASAS están raras o compare contra 1816, en especial corporativos»*. La
respuesta, medida en el código: en las 25 habilidades la palabra `tea` aparece
en DOS lugares, y los dos son `is not None`. El agente sabía contestar «¿hay
tasa?» y no «¿coincide con alguien?». Contra 1816 comparaba sólo para TAPAR el
agujero (`agente/tasa_1816`, el fallback cuando la TEA está vacía).

**Qué se hizo, y qué NO.** Primero un script —`scripts/diag_tea_corp_hd`—
con la tabla que pidió el user: ticker · TNA mía · TNA 1816 · TEA mía · TEA
1816, y nada más. Después, la MISMA tabla como habilidad: UN hallazgo por
corrida (sujeto `CORPORATIVOS HD`, como la familia de `cedear_faltante`), con
la tabla entera en `detalle` —que AHORA dibuja monoespaciado— y los cuatro
números de cada bono en la evidencia. El registro le refresca la tabla cada
vez que la vuelve a ver. **Sin umbral y sin filtro**: la primera versión sumó
un segundo cotejo por el endpoint de input manual, un tope de llamadas y una
ventana al cierre; la segunda, un umbral en bps que decidía «cuál se aparta».
El user frenó las dos: *«no quiero NADA de análisis, solamente COMPARAR, uno
vs otro y listo»*. Se sacó todo. La habilidad compara; concluir es de la
persona que la lee.

**Dos decisiones que sí hacen falta para que la comparación sea comparación**,
las dos ya medidas antes: se pide con `moneda='mep'` (el default de 1816 divide
por CCL y el motor por MEP — los 202 bps de GD46), y la TNA nuestra se deriva
con TEM×12, que es lo que la pantalla muestra en esta pill. Se troza de a 50
porque `/indicadores` trunca ahí sin avisar.

---

### 0.dp LA ON QUE 1816 TIENE Y NOSOTROS NO — y por qué Primary es condición (2026-09-04)

**Qué había.** `soberanos_faltantes` cruza el censo de 1816 contra el master y
ofrece el alta. Su `ALCANCE` es `{soberano, bcra}`, a propósito: los ~140
corporativos entran al sistema por Manager → TÍTULOS → BONOS → CARGAR →
Corporativo (ON), a mano, y nadie avisaba cuando 1816 publicaba una ON nueva.
El user (2026-09-04): *«así como hoy agregamos de saber de los CEDEARs, el
agente tiene que poder detectar ONs hard dólar que estén en 1816 y no en
nuestro modelo, y antes de ofrecer agregarlas que también valide que se
encuentra en Primary»*.

**Lo que se comparte, se comparte de verdad.** Mismo censo
(`fuentes.universo_1816`), misma foto de Primary (`fuentes.tickers_en_primary`),
misma regla de «se está yendo» (`curvas_sql.sale_del_master`, la lección de
M31G6) y misma excepción de cartera. El corte es estructural sobre los EJES de
la curva de 1816 —corporativo, USD, fija—, no una lista de nombres: «Corporativos
USD Linked» es corporativo y USD y NO entra.

**Primary como condición.** En los soberanos, «no pude leer la foto» deja pasar
el hallazgo y se descarta sólo lo que la foto NIEGA. Acá se invierte: sin foto,
`SinDatos`. Es lo que pidió el user y es lo correcto para un alta manual —
mandar a alguien a cargar un cronograma de una ON que después no cotiza es peor
que avisarle un día tarde. La excepción sigue siendo la cartera: si la tenemos
y no valúa, se canta aunque Primary no la liste, y el hallazgo lo dice.

**Por qué no tiene botón.** La rama `on` no está en `alta.RAMAS_AUTOMATICAS`.
No es olvido: 1816 manda algunos cuadros de ONs en NOMINALES y no en base 100
(medido, RESEARCH.md §A.4.9), y esa conversión no está verificada. Declarar
`alta_bono` haría que cada preview dijera «no aplicable» — un botón que
siempre bloquea enseña a no apretar. Hasta que la conversión se mida, el
`que_hacer` trae lo que hay que tipear.


### 0.dq EL EMISOR PROPUESTO — dónde el modelo opina y dónde no (2026-09-05)

**Qué había.** `ficha_incompleta · sin_emisor` listaba los títulos con el emisor
vacío y ofrecía un campo de texto. Nada proponía nada: había que ir título por
título a tipearlo. El user (2026-09-05): *«esto tiene que empezar a hacerse solo,
yo lo voy a confirmar»* — no autonomía, **trabajo hecho y una persona que
destilda lo que esté mal**.

**Qué se hizo.** `agente/emisor.py`: una cadena de cuatro eslabones, y cada
propuesta viaja con su FUENTE hasta la pantalla.

1. `regla` — las mismas reglas determinísticas del cron. Se **importan** de
   `jobs/assets_autofill` (un test prohíbe la palabra `FINANCIAMIENTO` dentro de
   `emisor.py`): la pantalla no puede saber menos que el job de la noche, y una
   regla copiada son dos copias sin árbitro (REGLA #9).
2. `nombre` — el emisor está adentro del nombre del FCI («Allaria Ahorro Plus»).
   Puro, `\b`, el más largo primero: con menos de 5 caracteres un match es
   casualidad.
3. `finnhub` — si el ticker tiene subyacente en `mercado.cedears`, el perfil de
   la empresa. **7 de 7 correctos, medido.**
4. `modelo` — sólo lo que sobró, **eligiendo de la lista cerrada de emisores que
   ya existen**. Lo que conteste fuera de la lista se descarta, y devolver vacío
   es una respuesta válida.

**Las tres guardas.** El modelo no puede inventar un emisor ni una variante de
grafía (así nacieron `CREDICUOTAS` y `Credicuotas Consumo`, el mismo emisor
partido en dos que hace que todo lo que agrupe por emisor cuente mal) · «no sé»
no completa nada · un tope de filas por pantalla, declarado. Y si el gateway no
contesta, las filas vuelven como estaban: **meter IA acá no puede agregar un
modo de falla nuevo, sólo puede ahorrar tipeo.**

**Lo que salió mal, y el corte que dejó.** La primera versión pedía a la vez
«elegí de la lista» y «si no sabés, vacío». El modelo resolvió la contradicción
eligiendo *el más parecido*, y escribió cuatro emisores plausibles y falsos:
`TXAR → YPF`, `TRAN → Transportadora de Gas del Norte`, `OEST → BBVA`,
`AGRO → Banco de Valores`. Se arregló el prompt, y además se midió: en RENTA
VARIABLE **sin ficha** el modelo acierta 2 de 6. Ahí ya no opina
(`SIN_FICHA_NO_OPINA`). El corte NO es la cartera: es **la ficha** — un ETF tiene
subyacente cargado y Finnhub simplemente no lo cubre, y ese sí va al modelo, que
contesta `OTROS` 8 de 8. Lo que queda vacío tiene su camino propio y ya existe:
`cedear_faltante` → `alta_cedear`, que los da de alta CON subyacente, y desde ahí
contesta Finnhub.

**Nada de esto escribe.** La escritura sigue siendo la de siempre —
`arreglos.CompletarFicha` → `assets_sql.set_campos(crear=False)`, contra la lista
viva de faltantes y con una línea de libro por título — y **sólo completa
vacíos**: un emisor ya cargado no se toca nunca (congelado por test).

**Y la tarjeta.** En el camino se descubrió que la de `ficha_incompleta` decía
«EMISOR» cuatro veces, repetía tres timestamps y mostraba una muestra truncada de
lo que el listado ya lista. Quedó un campo, un número y un botón. Además decía 44
cuando quedaban 10: nada re-corría el detector después de aplicar un arreglo.
`Arreglo.confirma_ya` lo corre en el acto, **después** de marcar `en_curso`, para
que el cierre sea POR ACCIÓN (invariante 4).

### 0.dr AHORA NO TENÍA CÓMO CALLAR NADA, Y LAS ONs NO TENÍAN BOTÓN (2026-09-06)

**El síntoma.** 214 avisos en AHORA, la mayoría `on_faltante`, cada uno con un
instructivo de tipeo de cuatro renglones. El user: *«cómo hago para ignorar
algunos y que ya no vuelvan a aparecer»* y *«ese texto no me interesa porque no
es algo que quiero hacer manual»*. Las dos cosas son la misma: **el texto es
largo porque no hay botón**, y el aviso vuelve porque no hay forma de callarlo.

**Por qué está en AHORA y no en ENCONTRÓ.** Invariante 9: a ENCONTRÓ solo entra
lo que tiene arreglo. `on_faltante` declara que no tiene, y no por olvido — la
rama `on` no está en `alta.RAMAS_AUTOMATICAS`, así que el paso `rama` del
pre-flight queda en BLOQUEA y el botón siempre bloquearía. Un botón que siempre
bloquea enseña a no apretar.

**El botón que faltaba (✕).** `POST /api/agente/ignorar` y la tabla
`agente.silenciados` existían desde el principio, pero el botón vivía **solo en
ENCONTRÓ** — o sea, justo donde las filas SÍ tienen arreglo. Los avisos, que son
la mayoría de AHORA, no tenían forma de callarse: «leído» los saca de la pantalla
y el detector los vuelve a crear en la pasada siguiente. Se agregó ✕ a AHORA, con
el mismo endpoint: silencia el TRÍO (habilidad + sujeto + regla) para que **no
vuelva a nacer**, y es reversible.

**La medición que decide si el botón se puede prender** (`scripts/diag_on_alta`).
La ambigüedad de la rama `on` es real —1816 manda algunos cuadros de ONs en
NOMINALES y otros en base 100— pero **se puede medir por bono en vez de
asumirla**: `alta.convertir_flujos` ya calcula `escala` mirando la Σ de
amortizaciones, y la rama `dolar_linked` ya normaliza dividiendo por esa Σ (una
operación que es la identidad cuando el cuadro ya viene en base 100, así que es
correcta en los dos casos). Y el pre-flight ya tiene el control que no depende de
creerle a nadie: compara **la PARIDAD contra la de 1816 al mismo precio**. Un
cuadro mal escalado no da error — da un número plausible y equivocado, y eso es
exactamente lo que ese cotejo caza.

El diag corre `alta.simular()` (la MISMA función del botón, no una copia) sobre
las ONs abiertas y reporta escala, Σ, paridad nuestra vs 1816 y el veredicto.
**Sale a la red y 1816 cobra por cupón**, así que el tope por default es chico.
Si el cotejo cierra, la rama se prende y el instructivo se reemplaza por «tildar
y dar de alta». Si no cierra, dice en cuál y con qué número.


### 0.ds 212 AVISOS DE ONs — el corte no es «cuáles importan» (2026-09-06)

**El síntoma.** `on_faltante` abría **una fila por ON**: 212 en AHORA. User:
*«esto ensucia el AHORA, muchas ON no son relevantes, ya de por sí hay muchas
ON»*. Y no alcanzaba con el ✕ nuevo (§0.dr): silenciar 212 de a uno no es una
salida.

**El corte que se eligió, y por qué ése.** «Cuáles son relevantes» el sistema no
lo sabe y no lo puede inventar. Pero hay un corte que **sí se puede afirmar**:

    la TENEMOS en cartera   → hoy NO VALÚA. Es un problema concreto de un bono
                              concreto, y merece su propia fila (severidad alta).
    NO la tenemos           → no hay nada roto. Es una OFERTA de catálogo: 1816
                              publica y nosotros no seguimos. Va en UNA fila.

Mismo patrón que `cedear_faltante` (§0.dl) y por la misma razón medida: Primary
lista muchos más de los que la mesa mira, y 300 filas de «falta X» no se leen. La
fila de familia (`FAMILIA_ON`) lleva la lista en `evidencia._items` —dato de
máquina, no se dibuja— y su `que_hacer` dice explícitamente que se puede callar
con ✕, porque nada está roto.

Las 212 filas viejas se cierran solas **por ausencia** en la primera corrida `ok`
del detector (invariante 1): no hay que borrar nada a mano.

**Lo que la medición del alta dijo, y lo que NO dijo** (`scripts/diag_on_alta`,
muestra de 8 el 2026-09-06). El miedo original —que 1816 mandara los cuadros de
ONs en nominales— **no apareció**: las 8 dieron `escala = vn100` con
Σ amortizaciones = 100,0000 exacto. Lo que sí apareció es que nuestra paridad da
consistentemente 4,5–7,2% **más alta** que la de 1816, siempre en el mismo
sentido — que es el sentido del interés corrido (nosotros dividimos por el
residual, ellos por el valor técnico), pero por encima de la cota de un cupón.

⚠️ **Y la primera versión del diag no dejaba decidir**: truncaba el detalle del
cotejo en 150 caracteres, justo antes de la duration. La duration es el testigo
que no depende del precio, ni del tipo de cambio, ni del devengado: si coincide,
el cronograma ES el de ellos. Sin ese número, «5% de diferencia de paridad» no
distingue «otro cuadro» de «otra definición». El diag ahora la imprime como
columna y no trunca nada.


### 0.dt EL ALTA DE UNA ON: TRES PUERTAS CERRADAS, Y UNA MENTÍA (2026-09-06)

**La medición.** `scripts/diag_on_alta`, muestra de 8 el 2026-09-06, con la
duration a la vista (§0.ds):

```
        Δduration      cotejo        lectura
AEC3O     0,12%        no_se         el cronograma ES el de 1816
CP37O     0,08%        no_se         idem
DNC3O     0,00%        no_se         idem
DNC5O     0,18%        no_se         idem
EAC3O     0,24%        no_se         idem
ARC1O     1,17%        bloquea       bajamos otro cuadro
DHSOO     1,68%        bloquea       idem
LMS8O     2,06%        bloquea       idem
```

Las 8 dieron `escala = vn100` con Σ = 100,0000 exacto: **el miedo original —que
1816 mandara las ONs en nominales— no aparece en esta muestra.** Lo que decide es
la duration, y parte la muestra en dos: **5 de 8 con el cronograma verificado al
tercer decimal**, 3 que difieren entre 1,2% y 2,1%. En esas 5 la paridad de 1816
es más baja que la nuestra por definición (ellos dividen por el valor técnico,
nosotros por el residual) y su indicador no sirve de juez.

**Las tres puertas que bloquean hoy, y no son la misma.**

1. `rama` — la rama `on` no está en `RAMAS_AUTOMATICAS`. **Correcto por ahora**:
   la conversión está verificada en 5 de 8, no en 8 de 8.
2. `curva_destino` — «on» no está en `bonos_admin.CURVAS_BONO`, y **eso no es un
   olvido**: las ONs tienen su PROPIA puerta de escritura
   (`api/services/ons.upsert_on`, curva `on_<sector>`) y su propio panel. O sea
   que `alta.aplicar`, que escribe por `bonos_admin.upsert_bono`, hoy no puede
   dar de alta una ON aunque el cuadro estuviera perfecto — le falta el riel, y
   además el SECTOR, que 1816 no publica.
3. `tea_motor` — **decía algo FALSO.** Afirmaba «el ajuste cae en el `else` del
   motor: solo computa duration» y BLOQUEABA, cuando `calcular_campos` tiene un
   `elif curva == "on"` con la matemática hard-dólar completa desde siempre. La
   causa: el paso preguntaba «¿va a haber TEA?» mirando `RAMAS_AUTOMATICAS`, que
   contesta «¿el cuadro se convierte sin ambigüedad?». Dos preguntas distintas
   con una sola respuesta (REGLA #9(B)); coincidían para cuatro ramas y por eso
   nadie lo notó. Ahora el motor declara `RAMAS_CON_FORMULA` y el pre-flight se
   lo pregunta a él. Un test verifica que cada rama declarada tenga su `elif` de
   verdad.

**Lo que queda para que el botón exista**: rutear el alta a `ons.upsert_on`,
resolver el sector, y decidir qué hacer con las que difieren en duration (no se
aplican solas: el cotejo ya las bloquea una por una, que es para lo que está).


### 0.du LA ON YA TIENE BOTÓN — la rama se prendió CON la medición delante (2026-09-06)

**Lo medido** (`scripts/diag_on_alta`, 8 ONs): las 8 con `escala = vn100` y
Σ amortizaciones = 100,0000 exacto. **El miedo que apagó la rama —que 1816
mandara las ONs en nominales— no existe en esta muestra.** Y la conversión de
esta rama es la más simple que hay: montos absolutos, tal cual los manda 1816,
que es exactamente lo que consume el `elif curva == "on"` del motor. El `else`
de `convertir_flujos` no los toca.

Duration nuestra contra la de 1816: **5 de 8 idénticas al tercer decimal**
(0,00% – 0,24%), 3 que difieren 1,17% – 2,06%.

**Qué significa prender la rama, y qué NO.** `on` entró en `RAMAS_AUTOMATICAS`:
el paso `rama` deja de bloquear a TODAS por adelantado. **No es «se aplica sin
mirar»** — es que cada bono pasa a juzgarlo su propio cotejo contra 1816, que es
para lo que el pre-flight existe. Las 3 que difieren siguen bloqueadas, una por
una y con el número puesto. Los umbrales no se tocaron: bajar una guarda para
que dé el resultado que uno quiere es exactamente lo que este diseño previene.

**La segunda puerta cerrada, que no era la rama.** `curva_destino` le preguntaba
a `bonos_admin.curvas_validas()` y «on» no está ahí — **y no es un olvido**: las
ONs viven en `on_<sector>` y se escriben por `api/services/ons.upsert_on`, con su
propio panel en Manager. O sea que el paso «la escritura va a ser aceptada»
bloqueaba CUALQUIER ON aunque el cuadro fuera perfecto. Ahora `aplicar` rutea:
rama `on` → la puerta de las ONs, el resto → `upsert_bono`. **Se usa la puerta de
la mesa y no una escritura propia**, que es lo que garantiza que un alta del
agente no pueda tener otra shape que un alta humana.

El **sector** 1816 no lo publica: la ON nace en `on_otros` —el default del propio
`slug_sector`— y se reclasifica desde el panel de ONs, en vivo. Un sector
provisorio no rompe la valuación: el motor despacha por `curva.startswith("on_")`.

**Dónde queda el botón.** Sobre la regla `no_esta_en_curvas`, que después de
§0.ds son solo **las que la mesa TIENE y hoy no valúan**. La fila de familia
—las que 1816 publica y nosotros no seguimos— sigue sin botón a propósito: ahí no
hay nada roto, y a ENCONTRÓ solo entra lo que tiene arreglo (invariante 9).


### 0.dv LA FILA DE CATÁLOGO TAMBIÉN LLEVA BOTÓN — tildar, no tipear (2026-09-06)

**Lo que faltaba, y era el pedido original.** §0.ds juntó las ONs que la mesa no
tiene en una sola fila, lo cual arregló el ruido de AHORA — pero la dejó **sin
botón**, con el argumento de que «no hay nada roto ahí». Y el user estaba
mirando justamente esas: *«estábamos con las ONs que estaban en AHORA que yo no
las tenía y me las sugería por 1816, siguen en AHORA, no entiendo cómo se da de
alta»*. Tenía razón: una fila sin botón no le daba ninguna forma de sumar las que
sí le interesan.

**El arreglo es `alta_on`, y es el gemelo de `alta_cedear`.** Mismo problema —el
sistema SABE darlas de alta a todas y **no puede decidir cuáles**— así que misma
forma: la fila despliega la lista, una persona tilda, y se dan de alta las
tildadas. Con eso la fila pasa a ENCONTRÓ, que es donde el user la quería
(invariante 9: a ENCONTRÓ entra lo que tiene arreglo).

**Lo que la lista NO es.** No es una autorización: cada ticker tildado vuelve a
pasar por `alta.aplicar` entero —baja el cuadro de 1816, lo convierte, calcula la
TEA y **coteja el cronograma contra el de ellos**—. La que no cierra no se
escribe y **no frena a las demás**. Y la curva de 1816 sale de lo que guardó el
DETECTOR, no de lo que manda el navegador: es la clasificación con la que se
decidió que faltaba.

Quedan entonces las dos filas, cada una con lo suyo: la ON **en cartera** (hoy no
valúa, problema real) con su botón individual, y la de **catálogo** con la lista
para tildar. Y el ✕ sigue estando para el que no quiera seguir ONs.


### 0.dw EL CUADRO DE UNA ON SALÍA EN GUIONES (2026-09-06)

Primera ON con botón, y su cronograma se dibujó con las tres columnas vacías:
fecha sí, amortiza/cupón/residual «—».

**La causa.** `convertir_flujos` devuelve **tres shapes distintas a propósito**,
porque el motor consume cosas distintas:

```
cer · soberanos · dolar_linked   →  amortizacion_pct · cupon_sobre_residual
tasa_fija · ON                   →  amortizacion · interes   (montos absolutos)
```

El mapeo de la preview (`arreglos._preview_de_simulacion`) conocía solo la
primera. **Y no fallaba nada**: `.get("amortizacion_pct")` sobre un dict que no
la tiene devuelve `None`, y `None` se dibuja como un guión. La fila existía, la
fecha estaba, los números no — el modo de falla de siempre.

⚠️ Es un bug **preexistente**, no de las ONs: una LECAP (rama `tasa_fija`) salía
igual de vacía y nadie lo había mirado.

El RESIDUAL, que la shape de montos absolutos no trae, se deriva en el backend y
**solo para la vista**, con la MISMA cuenta que hace el motor en su rama `on`: el
residual vivo es la suma de las amortizaciones que faltan, y no el campo
`valor_residual` —que puede venir en otra escala—. El cuadro que se escribe no se
toca.


### 0.dx EL BOTÓN SE PONÍA GRIS — ver lo que el agente VA HACIENDO (2026-09-06)

**El pedido** (user, 2026-09-06, mirando el alta de una ON): *«estaría buenísimo
que cuando tocás dar de alta no quede así como grisado, y se vea lo que va
haciendo el paso a paso… así de esa manera es más confiable»*.

Y es exacto: un alta hace **siete cosas** —simular, verificar, escribir, ficha,
tasa, especies, libro— y devolvía **una frase al final**. Peor que aburrido: si
algo salía a medias (el bono escrito pero la especie no), esa frase lo decía en
una subordinada y se leía como éxito.

**Lo que se hizo.** `alta.aplicar` arma un RASTRO: una línea por paso, con
`ok`/`falló`/`info` y lo que de verdad pasó —no lo que se esperaba—. Viaja por
`Resultado.pasos` hasta la pantalla, que lo dibuja como lista con ✔/✖ debajo de
la fila. En `alta_on`, que da de alta varias, cada ON es un paso y adentro lleva
los suyos: «3 sí y 1 no» dice cuál y por qué.

⚠️ **La pantalla no inventa ningún paso.** Todos vienen del backend, y los que
fallan también. Es la contracara del pre-flight: uno muestra lo que VA a hacer,
el otro lo que HIZO, y los dos salen del mismo lugar.

**Lo que esto NO es.** No es streaming: los pasos llegan juntos cuando la
llamada termina (mientras tanto la fila dice «trabajando…»). Un rastro en vivo
—cada paso apareciendo mientras corre— es otra cosa: necesita que la escritura
emita eventos y que el proxy los deje pasar. Queda como pendiente, y la primera
mitad —que el rastro EXISTA y salga del backend— es la que había que resolver
primero.


### 0.dy «APLICADO · ESPERANDO QUE EL DETECTOR CONFIRME» — dos horas de más (2026-09-06)

El user dio de alta PFC4O, el rastro mostró los seis pasos en verde, el bono
quedó escrito… y la fila siguió ahí, con el cartel de espera. *«¿Qué sería lo
que falta para que desaparezca?»*

**Nada del alta: faltaba que el detector volviera a mirar.** El hallazgo pasa a
`en_curso` y lo cierra `registro._cerrar_ausentes` cuando `on_faltante` deja de
encontrarlo — y esa habilidad corre **cada 2 horas**. Es el mismo bug que §0.cy
para `ficha_incompleta`, que se arregló con `Arreglo.confirma_ya` (correr el
detector en el acto, DESPUÉS del `en_curso`, para que el cierre sea POR ACCIÓN).

**Por qué las altas no lo tenían, y por qué el argumento era flojo.** El comentario
decía que volver a correr no es gratis: `soberanos_faltantes` / `on_faltante`
piden el censo a 1816 y eso cuesta créditos. Es cierto y no alcanza: **el alta ya
gastó un crédito POR CUPÓN** para bajar el cuadro —de 7 a 39 según el bono—, así
que un censo más es marginal contra lo que la misma acción acaba de pagar. Del
otro lado de la balanza estaba la tarjeta mintiendo dos horas con el bono ya
escrito, que es exactamente lo que este subsistema existe para no hacer.

Ahora `alta_bono`, `alta_on` y `alta_cedear` confirman en el acto. `alta_bono`
cierra el hallazgo (el sujeto ES el bono); las otras dos recuentan y la fila
queda con las que SIGUEN faltando — no cierra, pero deja de mentir el número.

**Lo que sigue sin confirmarse, y con razón**: `rehacer_job`. Su job tarda ocho
minutos, así que preguntar apenas se lanza no es caro: es **medir antes de
tiempo**, y el «no» que devolvería sería falso. Ese es el criterio de verdad —
*¿la respuesta ya existe?*—, no el costo.


### 0.dz «MIRAR AHORA» NO MIRABA NADA (2026-09-06)

User: *«¿«correr ahora» le decís a eso? porque ese botón NO HACE NADA»*. Era
literal.

**La causa.** El botón llamaba a `motor.tick()`, la MISMA función del daemon, que
corre solo las habilidades a las que **les toca** (venció su ritmo y están en su
ventana). O sea que apretarlo hacía exactamente lo que el agente iba a hacer un
segundo después. A las 22:50 no le tocaba a ninguna: no corría nada, y **no
decía nada**.

**Los dos frenos no son lo mismo, y ahí estaba el error de diseño.**

```
RITMO   «cada 2 h alcanza»   decisión de FRECUENCIA nuestra. Una persona que
                             aprieta un botón la está anulando A PROPÓSITO.
VENTANA «solo en rueda»      condición del MUNDO. Fuera de rueda
                             `bono_sin_precio` vería todos los precios viejos y
                             cantaría cien problemas que no existen.
```

El ritmo se puede forzar; la ventana no. `tick(forzar=True)` —que usa el
endpoint, nunca el daemon— anula el primero y jamás la segunda.

**Y lo que la ventana frena, se dice.** `_agenda` devuelve ahora también quién
quedó afuera y por qué, y la cabecera lo muestra: «miró 12 · 6 esperan a que abra
la rueda». Sin eso, **«no pasó nada» y «no había nada que hacer» se veían
idénticos** — que es la misma familia de bug que el agente viejo tenía en todas
partes: una corrida ciega y una corrida limpia dibujadas igual.

La condición de ventana quedó en UN solo lugar (`_fuera_de_ventana`), que
`_le_toca` consulta. Antes estaba inline en `_le_toca`, y por eso no se podía
distinguir un freno del otro sin reescribir la función.


### 0.ea EL DAEMON DEL AGENTE CORRÍA CÓDIGO VIEJO — desde siempre (2026-09-07)

Tres días después de §0.ds–§0.dz, la pantalla seguía mostrando **una fila por
ON en AHORA**, con el `que_hacer` viejo («No hay botón: la rama `on` todavía no
convierte el cuadro sola») — un texto que ya no existe en el repo. Y las filas
estaban **confirmadas ese mismo día a las 14:03**, o sea reescritas por una
corrida reciente.

**La causa: `agente.service` no lo reiniciaba NADIE.** `deploy.sh` reiniciaba
sólo `api.service`, y `restart_all.sh` itera `deploy/systemd/motor_*.service` —
un glob que `agente.service` no matchea. Así que después de CUALQUIER deploy la
API servía el código nuevo y **el daemon seguía detectando con el viejo,
indefinidamente**.

⚠️ **El síntoma es cruel porque cada mitad es coherente consigo misma.** Los
botones nuevos aparecían (los sirve la API, que sí se reinicia) y las filas
seguían saliendo con el texto viejo (las escribe el daemon). Eso no se lee como
«falta un restart»: se lee como **«el cambio no funcionó»**. Es exactamente la
REGLA #9 —dos copias del código y ningún árbitro— aplicada al deploy.

**Por qué no aplica la regla de no reiniciar.** Un motor reiniciado en rueda
corta el feed de precios de la mesa, y por eso el deploy no los toca. El agente
no le sirve precio a nadie: mira y escribe hallazgos. Y su unit está escrita
para el restart —atiende SIGTERM, termina la pasada en curso y sale limpio—, así
que lo peor que pasa es perder una pasada de detección.

**Lo que se congeló** (`tests/unit/test_deploy_units.py`): cada unit de
`deploy/systemd/` que no sea un motor tiene que estar clasificada — o la
reinicia el deploy, o está declarada como que no **con su motivo escrito**.
Sumar una unit nueva sin decidir de qué lado va rompe el test. Sin eso, una unit
suelta corre código viejo para siempre y **nada falla**: el proceso está vivo,
el deploy dice OK y el commit está en `main`.

`control_saldos` y `tenencia_live` quedaron declaradas del lado de «no se
reinician»: son feeds vivos como un motor, aunque no se llamen `motor_*`.


### 0.eb EL BOTÓN ARREGLADO, Y ROTO DE OTRA FORMA (2026-09-07)

§0.dz hizo que «mirar ahora» **forzara el ritmo**, porque antes corría lo mismo
que el daemon y por eso casi nunca hacía nada. Efecto no previsto: pasó de correr
cero o una habilidad a correr **las ~27 de una**, se fue muy arriba de los 30 s
que aguanta el proxy de Vercel (`maxDuration = 30`), y el botón empezó a
contestar **«no pude correr la pasada»**.

El cambio era correcto y el resultado quedó peor que antes. **Arreglar un botón
mudo y dejarlo roto no es un arreglo.**

**Los dos presupuestos son dos números porque los limita otra cosa.** Al daemon
lo limita que la pasada VUELVA (`PRESUPUESTO_S = 240`: corta y sigue en la
siguiente). A la pantalla la limita un TRANSPORTE que corta a los 30 s
(`PRESUPUESTO_PEDIDO_S = 20`, con margen para el viaje). Confundirlos es lo que
rompió el botón.

**Y lo que no entra no se pierde**: la pasada devuelve `faltaron` —las que SÍ les
tocaba y quedaron por tiempo, distinto de `fuera_de_ventana`— y la pantalla dice
«miró 9 · faltan 14, apretá de nuevo». Sin ese número, una pasada cortada y una
completa se ven idénticas, que es el mismo bug que §0.dz vino a arreglar.

⚠️ El `30` vive en el frontend y el `20` acá: **dos copias de un mismo límite**
(REGLA #9). Se declaran una en función de la otra, con el path de la otra
escrito al lado, y un test verifica que el de acá sea menor.


### 0.ec CON QUÉ DÓLAR VALUAMOS — 4,2% que se come la tasa entera (2026-09-07)

El user, mirando las cuatro ONs que el agente ofrece dar de alta: *«todas mal la
TEA… no puede dar siempre todo distinto a 1816, que justamente es una empresa de
excelencia en valuar. Hay que detectar en qué estamos valuando mal estos bonos»*.

**No es la fórmula ni el cronograma.** La duration coincide con la de 1816 al
tercer decimal en los cuatro (0,16% a 0,30%), y la duration depende SOLO del
cronograma y las fechas. El cuadro que bajamos es el de ellos.

**Es el DÓLAR.** Un hard dólar que cotiza en pesos hay que pasarlo a dólares
antes de calcular la tasa, y con qué se divide **ES** la tasa:

```
                 tipo de cambio implícito
nosotros              1.525,4      ← idéntico en los cuatro (un dólar de la casa)
1816                  1.589,5      ← +4,20%
```

`1.589,5` sale de un ancla dura: la TEA que 1816 publica para LMS8O es −0,0085%,
o sea cero, y a tasa cero el precio ES la suma de los flujos que faltan
(77,3828). `123.000 / 77,3828 = 1.589,5`. Con ESE tipo de cambio, el interés
corrido implícito de los otros tres cierra solo: YFCPO +0,25 contra 0,28 teórico
(20 días de un cupón de 2,5), EAC3O +1,84 contra 1,82 (77 días de 4,26), YM43O
+2,18 contra 2,20 (146 días desde su emisión). Cuatro bonos, un solo número.

**Y estaba escrito en nuestro código desde el 2026-08-17.** `core/mercado_1816.py`,
textual: *«el spec dice "para instrumentos pagaderos en moneda distinta a ARS,
para calcular indicadores las cotizaciones se dividen por CCL"… NUESTRO motor
divide por MEP»*. Lo dejó anotado el episodio de GD46 (202 bps) y **nunca se
actuó**. De ahí salen los 136 / 158 / 215 bps del cotejo.

⚠️ **El caso que lo grita: LMS8O.** Nosotros −13,44%, ellos −0,01%. Un bono que
rinde cero mostrado como si perdiera 13% al año. No falla nada: el número es
plausible y está bien calculado — sobre el dólar equivocado.

**Ninguno de los dos está «mal»: son dos preguntas.** La TEA en MEP es lo que
rinde para quien liquida contra MEP; la TEA en CCL es la que cotiza el mercado y
publican los brokers, y la única comparable hacia afuera. Cuál usa la mesa es una
decisión de la mesa, no del código — y mueve la tasa de TODO el hard dólar de la
casa, así que se mide antes: `scripts/diag_dolar_valuacion` corre el motor de
verdad dos veces por bono (una con MEP, otra con CCL) y tabula la diferencia en
bps, marcando los que **cambian de signo**.

**Lo que sí se arregló ya, porque no depende de esa decisión**: la tarjeta decía
«TEA 8,59%» a secas. Ahora dice también la **TNA** —el número que la mesa mira,
sacado de `quant/tasas` y no de una cuenta nueva— y **con qué dólar se calculó,
con su valor**. Una tasa en dólares sin decir cuál no es un número.


### 0.ed EL MEP ES EL CORRECTO — y el atajo que sí puede doler (2026-09-07)

§0.ec cerró diciendo que la diferencia contra 1816 es el dólar, y dejó abierto
cuál usar. El user lo cerró en una línea: *«los bonos pagan en USD, o sea vos
recibís MEP no CCL»*. **Tiene razón, y el argumento es de flujos:**

```
pagás PESOS por la pata O  →  el bono te paga DÓLARES en tu cuenta local
tu alternativa con esos pesos era comprar dólar MEP
⇒ el rendimiento en dólares se mide contra el MEP
```

El CCL son dólares AFUERA: de este bono no salen, salvo que además hagas el
canje. Que 1816 divida por CCL es una **convención** —homogeneiza todo el
mercado bajo un solo tipo de cambio— y no una afirmación sobre los flujos de este
papel. Así que los 136-215 bps **no son un error nuestro que haya que corregir:
son una diferencia de convención que hay que ESCRIBIR** (por eso la tarjeta ahora
dice «dólar usado: MEP 1.524,05», §0.ec).

**Lo que sí puede estar mal es más fino, y es un ATAJO.** Si la pata D del bono
cotiza, su precio YA está en dólares y los flujos también: la tasa sale **sin
ningún tipo de cambio** y es la que un trader puede ejecutar. Hoy el motor divide
la pata en pesos por un **MEP genérico** —el del AL30— aunque la D del propio
bono esté operando, y eso le mete a cada bono **el canje de otro papel**.
`precio_soberano_a_usd` ya sabe no convertir cuando el símbolo termina en D o C;
el problema es que en `mercado.curvas` el `ticker` guardado es la pata en pesos,
así que nunca toma ese camino.

`scripts/diag_dolar_valuacion` mide exactamente eso: para cada hard dólar busca su
pata D **por FICHA** (`especies.hermanas_por_ficha` → `core/pareo`, REGLA #9: los
tickers están topeados en 5 caracteres y emparejar por string anda por
casualidad), corre el motor sobre las dos, y tabula el FX implícito de cada bono
contra el MEP genérico. Si el rango es angosto, el atajo es inofensivo; si es
ancho, hay que cambiar de pata. **Eso se mide, no se supone.**

⚠️ **Y apareció un agujero propio: el CCL vino en `None`.** Sale de UNA sola
cuenta (`engines/dolares.py`: AL30 offer ÷ AL30C bid). El AL30C es mucho menos
líquido que el AL30D, así que cuando su punta compradora se vacía **la casa se
queda sin CCL y sin canje, en silencio y sin que nada avise**. El diag lo reporta
en vez de abortar, que es lo que hacía la primera versión.


### 0.ee 1816 CON EL MISMO PRECIO Y TRES DÓLARES — la prueba que separa dólar de fórmula (2026-09-07)

§0.ec dijo «es el dólar» y §0.ed «el MEP es el correcto». Faltaba una cosa que
ninguno de los dos midió: **qué dólar usa 1816, en número**, y si con el mismo
dólar nuestra cuenta y la de ellos son la misma.

**Lo que se verificó antes de escribir el diag**, con los 7 ejemplos de la
pantalla (EAC3O YM43O LMS8O YFCPO AEC3O CP37O DNC3O) y el XIRR del repo:

```
                       FX que reproduce la TEA
nuestra (MEP casa)          1.523     ← las 7
1816 (moneda=ars)           1.588     ← las 7, +4,25%
```

Mismo cronograma, misma liquidación (T+1) y misma convención (act/365): la TEA
de 1816 se reproduce EXACTA con nuestro motor cambiando solo el dólar. Correr la
fecha no la reproduce con ningún corrimiento razonable. Y su paridad cierra al
centésimo con ese mismo 1.588 más el interés corrido por días reales del cupón
en curso — o sea que **su paridad es sobre valor técnico y la nuestra sobre el
residual pelado**, sin devengado (hasta un cupón entero de más, 2,9 puntos en
DNC3O).

**Pero 1.588 no es el CCL de la casa** (~1.576 ese día, dato del user): queda un
0,76% de dólar sin nombre, que en un bono corto son 200-460 bps. Tres
hipótesis, sin medir: es SU CCL (otra medida que la nuestra), es timing de
feriado, o es un dólar propio de la curva.

**`scripts/diag_1816_dolar_on` es la prueba que las separa**: le pide a la
calculadora de 1816 (`/indicadores/{ticker}`, input manual) N ONs AL MISMO
PRECIO en `ars`, `mep` y `ccl`, y para cada TEA despeja el FX implícito con
nuestro propio motor. Si el FX de `mep` cae a ≤0,5% del MEP de la casa, con el
mismo dólar las dos cuentas coinciden y **lo que difiere es SOLO el dólar**. Si
`ars` y `ccl` dan el mismo FX, su `ars` es su CCL, y ese FX ES su CCL en número.
Es una calculadora, no un precio: se puede correr en feriado.

**Dos cosas más que salieron de leer el código**, pendientes de arreglo:
`agente/alta.py::moneda_cotejo_1816` devuelve `mep` solo para `soberanos`, así
que el cotejo «al mismo precio» de una ON en USD compara nuestro MEP contra su
dólar por construcción y no puede cerrar nunca; y el mensaje del cotejo culpa al
«180-360» de `convencionTna`, que es la convención de la **TNA**, no de la TEA —
la TEA de ellos es act/365 igual que la nuestra, medido arriba.

**Primera corrida (2026-09-07, 7 ONs).** La fila `ars` cerró la pregunta 1: un
solo dólar de **1.590** reproduce los 5 bonos largos (duration 1,8 a 3,2) con
0,04% de rango, y con la bisección corregida también los 3 cortos. Fórmula,
cronograma y liquidación son los de ellos; contra el MEP de la casa (1.528) son
+4,0%. Su valor técnico de EAC3O dio 101,86 = residual 100 + 1,86 devengado.
**Error de la primera versión**: la calculadora espera el precio YA en la
moneda pedida; mandarle pesos con `moneda=mep` los tomó como dólares (TEA −95%).
Ahora `mep` recibe `precio / MEP casa` (prueba directa de fórmula) y el dólar
de ellos se mide con el endpoint de PRECIOS en `mep` y `ccl` (pregunta 2).

**Lo que se arregló con eso (mismo día).** (1) `engines/curvas.py`, rama `on`:
la paridad pasa a ser sobre **valor técnico** (`interes_corrido`: residual +
devengado lineal por días reales del cupón en curso, desde el último pago o la
emisión). Verificado contra 1816 al mismo dólar: EAC3O 99,83 contra 99,79, DNC3O
100,82 contra 100,82, YM43O 98,28 contra 98,28. Sin `fecha_emision` y sin pago
previo el devengado es 0, que es lo que hacía antes. (2) `agente/alta.py`:
`moneda_cotejo_1816` contesta `mep` también para una ON en USD, así que el
cotejo «al mismo precio» le manda a 1816 el precio ya en dólares —el mismo número
que consume el motor— y cierra a 0 bps; y el mensaje deja de culpar al «180-360»,
que es la convención de la TNA. Las demás ramas (soberanos, CER) siguen con la
paridad sobre el residual: no se midieron, no se tocan.


### 0.ef EL EJECUTOR — lo que el agente aplica SOLO, y cómo se distingue (2026-09-07)

El user, con las ONs de cartera dándose de alta a mano una por una: *«me
gustaría que esto se ejecute solo en vez de yo hacerlo manual… pero que quede en
historial y todo, en especial con una marca de que se hizo solo, para ir
diferenciando»*.

**Lo que ya estaba, y por eso el diseño es chico.** El veredicto del pre-flight
contestaba dos preguntas desde el 2026-08-22 —`puede_aplicar` (¿puede apretar
un humano?) y `puede_auto` (¿puede aplicarse SOLO?, que además frena con
`revisar` y `no_se`)— y nadie leía la segunda. `arreglos.aplicar` ya era la
única puerta: pre-flight, escritura, libro, `en_curso`, y el detector que
confirma. Y el libro ya guardaba `por`. Faltaba quién apriete y cómo firmar.

**Las cuatro piezas:**

1. **La declaración**, hermana de `investigar`: `Habilidad.automatico =
   {regla: motivo}`. Vacío es «nada de esto va solo». Un test exige que la
   regla tenga arreglo y que el arreglo no pida datos (un robot no tilda listas).
2. **El ejecutor**, `agente/autonomo.py`: no escribe, ELIGE, y llama a
   `arreglos.aplicar(hallazgo, por=ACTOR_AGENTE)`. Corre en cada pasada del
   daemon (`jobs/agente.py::_aplicar_solo`), después del tick. Tres guardas: **tope por pasada** (5: cada alta gasta créditos de 1816
   y segundos, y un bug no puede escribir 200 bonos), **no reintentar 24 h** lo
   que ya intentó (bien o mal: queda en `agente.acciones` con su motivo y el
   hallazgo sigue como botón), y **solo reglas declaradas**.
3. **La marca**: el actor reservado `tipos.ACTOR_AGENTE = "av-agent"`, que viaja
   por el `por` que ya existía —`agente.acciones`, `hallazgos.cerrado_por`, y el
   bono mismo en `mercado.curvas.actualizado_por`—. El backend lo resuelve en
   cada fila del historial (`automatico`, invariante 11) y el front pinta SOLO.
   El badge de HISTORIAL cuenta lo hecho solo hoy (`vista.solo`).
4. **El switch es la declaración**: prender una regla es una línea en el
   catálogo. Se eligió no agregar un switch en base para no tener dos lugares
   que contesten «¿esto va solo?» (REGLA #9).

**Decisiones que se tomaron y por qué.** Actúa al primer avistaje: acá el juez
es el pre-flight, no el tiempo (en `investigar` los segundos existen porque una
caída se recupera sola; una ON en cartera sin master no). Arranca con UNA regla,
`on_faltante / no_esta_en_curvas`; `soberanos_faltantes` y el CEDEAR tienen la
misma forma y se prenden cuando el historial de esta muestre una semana limpia.
Los motores NO se reinician: el bono queda escrito y valúa desde el próximo
arranque, que sigue siendo decisión de la mesa.


### 0.eg UN INFORME NO ES UN PROBLEMA — y el peso de la base se lee POR VISTA (2026-09-07)

El user, mirando AHORA: *«esto no tiene que ser crónico… y el aviso de la BASE
hay que profundizarlo. Debería consolidarse por VISTA de la página, agrupar todo
lo que hay adentro y ver cuál más creció y por cuánto, en formato listado»*.
Y sobre la tabla de `tasa_vs_1816`: *«debería poder minimizarse, tipo lista
desplegable»*.

**Lo crónico.** La regla de §6.10 cuenta cuántas veces NACIÓ un trío en 30 días,
y tres es crónico. Está bien para un problema: aparece, se va, vuelve. Pero
`db_peso / peso_total_11` nace todos los días a las 11 **por diseño**, y
`tasa_vs_1816 / tabla` cada dos horas: son INFORMES, tienen ediciones, no
episodios. A los tres días la pantalla los pintaba «⚠ CRÓNICO · 11× en 30 d» y
mandaba a «revisar el umbral o el cron». La solución no es subir el umbral —eso
esconde los crónicos de verdad— sino DECLARAR: `Habilidad.informes` lista las
reglas que son informes, `vista._con_historial` no les cuenta episodios y
`vista.cronicos` las excluye con el mismo `unnest` de tres listas paralelas
(nunca habilidad+regla pegados en un string).

**El peso de la base.** El aviso decía «1,4 GB en 249 tablas, +40,8 MB en 7
días» y cinco tablas sueltas. Ahora el detalle es un LISTADO por vista de la
app: `peso.VISTAS_POR_SCHEMA` traduce cada schema de Postgres a la(s)
pantalla(s) que alimenta (los schemas se diseñaron por dominio, `docs/SQL.md`
§2, así que la traducción es una tabla y no una adivinanza), `peso.por_vista`
agrupa, ordena por lo que más creció y mete adentro de cada vista sus tres
tablas que más crecieron. Un test exige que todo schema declarado en
`sql/schema.sql` tenga vista: uno nuevo sin etiqueta no pasa. Lo que no es
nuestro (`auth`, `storage`, `realtime`, `vault` de Supabase) cae en OTROS.

**La tabla larga.** `Detalle` (front, `components/agente/evidencia.tsx`): más de
seis líneas se pliega mostrando la primera —en una tabla, la cabecera— y un
botón «ver las N líneas». Es presentación, no dato: el backend manda el texto
entero.


### 0.eh «NO ME INTERESAN ESTAS» — descartar por ticker, y que el aviso vuelva con las nuevas (2026-09-07)

El user, con las 147 ONs de la oferta de catálogo: *«si no está la ON en el
aviso es porque ya la agregué… ahora el aviso tiene que volver a aparecer
nuevamente, ¿se entiende? Si mañana aparecen nuevas ONs yo quiero saberlo. Ahí
es donde estoy confundido»*.

Y la confusión era del diseño, no de él. El botón «no me interesa» silencia el
TRÍO (habilidad + sujeto + regla) y la fila de familia tiene un solo sujeto,
`ONs HARD DÓLAR`: apretarlo callaba la oferta ENTERA para siempre, incluida la
ON que 1816 publique mañana. Lo que hacía falta es lo opuesto: descartar POR
TICKER, que el aviso desaparezca cuando no queda nada, y que **renazca solo con
lo nuevo**.

**La lista ya existía.** `mercado.ons_ignoradas` es la que ignora el
conciliador del panel de ONs de Manager, con su alta (`ons.ignorar_concil`),
su baja y su listado para restaurar. Se REUSA en vez de crear otra: dos listas
de «ONs que la mesa no sigue» sin árbitro no fallan, muestran cosas distintas
(REGLA #9). `fuentes.ons_no_interesan()` la lee una vez por pasada; `None` = no
pude leer → no se filtra nada, porque ofrecer de más es mejor que callar una ON
nueva.

**Cómo cierra el ciclo sin código nuevo.** El detector descuenta las
descartadas de la oferta (las de CARTERA nunca: si la casa la tiene y no valúa,
es un problema aunque alguien la haya descartado). Cuando no queda ninguna, no
emite la fila, y `registro` la cierra por AUSENCIA en la misma pasada. El día
que 1816 publica una ON que no está en la lista, la fila NACE de nuevo —`nuevo`,
no `reincidió`, porque el cierre fue por ausencia— con esa sola adentro. El
`problema` dice cuántas se descartaron para que «3 ONs» no se lea como «solo
hay 3».

**La puerta.** `POST /api/agente/ons/no-interesan` con `tickers` o `todas`.
`vista.no_interesan_ons` recorta lo que manda el navegador a los `_items` que el
detector ofreció (nunca se escribe lo que llega), escribe por `ons.ignorar_concil`
ticker por ticker, deja UNA línea en el libro (`no_interesa_on`, con cuántas
quedaban y cuántas se descartaron) y vuelve a correr `on_faltante` para que la
tarjeta muestre lo que sigue. En el listado: «no me interesan las N tildadas» y
«ninguna me interesa · avisar solo las nuevas» (dos clics, sin `confirm`).


### 0.ei CLASE_ACTIVO SE COMPLETA SOLA, EN EL ACTO — determinista primero, el modelo después (2026-09-08)

El user, con 263 títulos sin `clase_activo` en la tarjeta: *«quiero seguir con el
AGENT, que puede llegar a ser determinista así como también usar LLM… apenas se
reconoce un activo nuevo ahí no se podría disparar? ¿Por qué esperar a un job
nocturno?»*. Dos reglas que él mismo dictó:

```
DERIVADOS   unidad/ticker termina en « C» o « P» (SOJ.ROS/MAY27 364 C, con o sin
            «OTC - »)  →  CALL OPCIONES / PUT OPCIONES.  Solo esos dos: los demás
            derivados no son opciones y no se tocan.
FCI         el fondo está en la lista de instrumentos de Primary con su tipo y su
            moneda: Mercado de Dinero → MM ARS / MM USD · Renta Fija → ARS T1 /
            HD T1 · Renta Variable → RENTA VARIABLE.  Renta Mixta: después.
```

**Lo que se verificó antes.** La «tabla de instrumentos» que tiene el tipo del
fondo y la moneda no es una tabla propia: es `manager.pyrofex_instruments`, lo
que Primary publica, y el agente ya la leía (`fuentes.fichas_primary`,
`core.instrumentos_validos.fichas`: `subyacente` = «Mercado de Dinero»…,
`moneda`). El ticker de un FCI en `assets` es el nombre del fondo, que es lo que
Primary usa como símbolo: se cruza por nombre normalizado. Y el vocabulario de
clases ya está fijado por datos (`aca.clase_destacada`, `aca.moneda_regla`).

> ⚠️ **Ese «se cruza por nombre normalizado» era la parte NO verificada, y era
> falsa: superseded por §0.fg (2026-09-11).** El símbolo de Primary puede ser
> ` IEB Retorno Total - Clase D` (con espacio inicial) o un código
> (`ADBAICA AR`), y el asset dice `FCI IAM Ahorro Pesos - Clase B`: medido, 73
> de 84 no matchearon. Hoy la clase de un FCI sale del LINK
> `mercado.fci.unidad`, y el nombre quedó como respaldo con el normalizador del
> dominio (`core/fci_match.normalizar`).

**Por qué NO es el job nocturno.** `jobs/assets_autofill` sabe hacer esto y lo
haría a las 3 de la mañana. Pero el ejecutor (§0.ef) ya corre en cada pasada:
lo que faltaba era que `completar_ficha` —un arreglo que PIDE DATOS, o sea que
hasta hoy solo lo apretaba una persona— supiera decir **qué escribiría solo**.
Eso es `Arreglo.solo(sujeto, ev)`: los `datos` listos para `aplicar`, o `None`.
`CompletarFicha.solo` devuelve únicamente lo que las dos reglas resuelven, con
la lista viva de faltantes. El ejecutor, para un arreglo que pide datos, pregunta
`solo` primero: si no hay nada determinístico, no intenta y no ensucia el libro.

**Tres cosas nuevas del ejecutor por esto.** (1) `Arreglo.repetible`: el sujeto
de `completar_ficha` es un CAMPO entero —una familia— y hay que poder aplicarlo
cada vez que aparece un título nuevo, así que para los repetibles entra también
`en_curso` y solo los intentos FALLIDOS frenan 24 h. (2) `ficha_incompleta` pasa
de cada 6 h a cada hora: son cinco consultas SQL, y con seis horas un título
nuevo esperaba media jornada. (3) La regla de las reglas: un arreglo con
`pide_datos` es automático solo si sobreescribe `solo` (test).

**La lista cerrada.** Una regla propone un valor solo si ya existe en
`clase_activo`. Si dice «PUT OPCIONES» y ese valor no está cargado todavía, la
fila viaja con `nota` en vez de `propuesto`: se carga una vez a mano y desde ahí
sale sola. Es el invariante de `agente/emisor.py`: ninguna regla inventa una
grafía, que es cómo un catálogo se parte en dos.

**Las siguientes tres reglas, el mismo día.** Copiar la cartera cuando es
`RENTA VARIABLE`, `HD` o `DL` (la clase es la misma palabra), y para la cartera
`ARS` leer la CURVA del bono en `mercado.curvas`: `ajuste_alt` no vacío → `DUAL`
(un dual es la consecuencia de tener dos ajustes), `cer` → `CER`, `fija` →
`FIJA`, `tamar` → `TAMAR`; `badlar`/`tpm`/`caucion` o bono que no está en el
master → nada. Fuente nueva: `curva`. Solo si la curva dice `moneda_eje = ARS`:
un HD mal carteado como ARS no se clasifica por la cartera equivocada.

**Y los derivados que no son opciones, el mismo día.** El producto sale del
prefijo del contrato —`SOJ`/`SOY` → SOJA, `MAI`/`CRN` → MAIZ, `TRI` → TRIGO,
`DLR` → DOLAR— y la forma de si dice `OTC`: `[SOJ.ROS.P/DIS26]` → FUTUROS DE
SOJA, `[OTC - CRN.CME/NOV26]` → OTC MAIZ, `[OTC - DLR012027]` → OTC DOLAR. El
dólar solo bajo OTC. Las opciones C/P siguen yendo primero. Y la lista cerrada
pasa a comparar SIN acentos y a escribir la grafía que ya existe en la base
(`core.clase_activo.en_lista_cerrada`): «MAIZ» contra «MAÍZ» no bloquea ni crea
una segunda grafía.

**Determinista primero, el modelo después.** `agente/clase.py` propone con tres
fuentes, `regla`, `primary` y `curva`, y la tarjeta las muestra igual que EMISOR. Lo que
ninguna regla resuelve queda para una persona; el paso «el modelo elige de la
lista cerrada» (como en el emisor) se suma cuando haga falta, y **nunca** entra
por `solo`.


### 0.ej LA CARTERA DE UN BONO NUNCA TUVO REGLA — y ahora se completa sola (2026-09-08)

El user, con tres ONs sin cartera en la tarjeta (EAC3O, PFC4O, ARC1O): *«¿por
qué no sugiere qué cartera? ¿O mismo lo hace solo? ¿Está fallando algo? Estos
tres fácilmente se podría saber a qué cartera pertenecen»*.

**No fallaba nada: para `cartera` nunca se escribió una regla que sepa qué es un
bono.** Los assets nacen vacíos (el escritor de `assets_sql` inserta la unidad
y nada más, por diseño). `jobs/assets_autofill` sabe tres carteras y solo esas:
pagarés → FINANCIAMIENTO, fondos → FCI, OTC/agro → DERIVADOS. Un `[58364] EAC3O`
no matchea ninguna. Y `CompletarFicha` no proponía nada para la cartera porque
un comentario decía que era «criterio de la mesa». El «crónico 7× en 30 d» era
la medida honesta de cuántas veces por mes entra un título nuevo sin cartera.

**El dato ya estaba en dos lugares.** Los EJES del bono son la clasificación
única del sistema (`core/curvas_ejes`), y el agente ya los lee: en el master
(`fuentes.master()`, si el bono está dado de alta) y en el catálogo local de
1816 (`fuentes.universo_1816()`, la curva traducida con `desde_1816`, la misma
función que usa el alta). De los ejes a la cartera es una tabla de tres líneas
(`core/cartera.py::de_ejes`): `dolar_linked` → DL; USD/EUR → HD; ARS → ARS.

**La cadena** (`agente/cartera.py`, misma forma que `clase.py`): primero las
reglas del job —importadas, no copiadas, como `emisor.por_regla`—, después el
master (`curva`), después 1816 (`1816`). Lista cerrada contra lo que ya existe
en `cartera`. `CompletarFicha` propone y sabe `solo()` también para este campo,
y `ficha_incompleta` declara `sin_cartera` automático.

**Efecto dominó, a favor.** Las reglas de `clase_activo` (§0.ei) arrancan de la
cartera. Sin cartera, un bono tampoco recibía clase. Ahora una pasada escribe
la cartera y la siguiente la clase, sin código extra. Lo que sigue para una
persona: acciones y CEDEARs (RENTA VARIABLE, derivable del cficode de Primary
como paso siguiente) y cualquier ticker que no esté ni en el master ni en 1816.


### 0.ek LA FICHA DE UN TÍTULO NUEVO NO ES CRÓNICA — lo crónico es del sistema (2026-09-08)

El user: *«clase activo, emisor, cartera no entran en lo de crónico. Son
justamente el tipo de cosas que van a pasar constantemente porque siempre
aparecen nuevos activos. Lo importante es que se segmenten bien. Lo de crónico
va para cosas de sistema, monitor, salud»*.

Tiene razón, y es la segunda vez que la regla de §6.10 marca como patrón algo
que es ritmo: primero los INFORMES (§0.eg, nacen por calendario), ahora el
TRABAJO RECURRENTE. Que `sin_cartera` nazca siete veces por mes mide cuántos
títulos nuevos entraron, no algo mal configurado. `Habilidad.recurrentes`
declara esas reglas —las cuatro de `ficha_incompleta`— y `catalogo.sin_episodios()`
junta informes y recurrentes para `vista._con_historial` y `vista.cronicos`. Un
test exige que toda regla de la ficha esté declarada recurrente. Lo crónico
queda para lo que sí es un patrón: motores, feeds, jobs, proveedores.


### 0.el CEDEARs: el subyacente a la vista, y «no me interesan» por ticker (2026-09-08)

El user, sobre la tarjeta de 303 CEDEARs que Primary lista y no tenemos: *«acá
me gustaría agregar (a) el underlying y (b) poder tener el patrón de marcar los
que no me interesan y que aparezcan solo nuevos, como ya hicimos en lo otro»*.

**(a) El dato ya viajaba.** `alta_cedear.candidatos` pone en cada fila
`subyacente_primary` —lo que Primary declara como subyacente— y la pantalla no
lo mostraba. Ahora hay una columna «subyacente Primary», solo lectura. **No se
usa de default** para el subyacente US que se escribe: no está medido qué pone
Primary ahí para un CEDEAR (REGLA #2), así que se muestra para que quien tilda
decida, y el default sigue siendo el ticker.

**(b) Dónde vive el descarte, sin tabla nueva.** El detector considera «en el
sistema» todo lo que está en `mercado.cedears`, activo o no. Entonces descartar
es escribir la fila con `activo = false` y la marca `data.descartado` (quién y
cuándo), por `cedears_sql.descartar`, que **nunca apaga uno activo**. Deja de
ofrecerse sin tocar el detector, el aviso se cierra por ausencia cuando no queda
ninguno y renace con el primero que Primary liste fuera del master; y Manager →
RENTA VARIABLE lo lista y lo reactiva. Una lista, no dos (REGLA #9).

**La puerta.** `POST /api/agente/cedears/no-interesan` → `vista.no_interesan_cedears`,
copia de la de las ONs (§0.eh) con una diferencia obligada: la evidencia del
hallazgo trae solo una MUESTRA, así que los permitidos se recalculan EN VIVO
con `alta_cedear.candidatos`, igual que hace `preview`. Nunca se escribe lo
que manda el navegador. El `problema` dice cuántos están descartados para que
«3 CEDEARs» no se lea como «solo hay 3».


### 0.em «NO ESCRIBE HACE 3,5 DÍAS» SOBRE UNA TABLA QUE ESCRIBE CADA 15 MINUTOS (2026-09-08)

El aviso, textual: `bancos.mayor_movimientos · tabla_quieta` — *«no escribe hace
3,5 días y su cron dice cada 21,0 h como mucho»*, crónico, 6× en 30 días.
`mayor_sync` corre **20 veces por día**. Nada estaba roto.

El user, sin ver el código: *«esto corre siempre en T-1; un martes a esta hora,
si todavía no arrancó el job, no va a detectar datos desde el viernes, y saltaría
el aviso — pero eso es porque está mal configurado, se tiraron números»*. La
intuición apuntaba al lugar correcto. Los números no se tiraron: los dedujo el
código, y los dedujo mal. **Dos bugs independientes, y ninguno de los dos solo
alcanzaba para el aviso.**

**(1) UN JOB EN VARIAS LÍNEAS DEL CRONTAB ES UN SOLO RELOJ.** `mayor_sync` está
declarado en tres líneas —`*/15 13-15`, `0,30 16-17`, `0 18-21`, todas L-V— que
juntas son una corrida continua de 13:00 a 21:00 UTC. `core/crontab.py` resolvía
cada línea por separado, como si fueran horarios alternativos, y se quedaba con
el hueco más chico: el de `0 18-21`, que **aislada** admite 21 h. No son
alternativas: se COMPONEN, y el hueco real es 21:00 → 13:00 = **16 h**. Medido:
**7 de los 58 jobs del crontab tienen más de una línea** y en los 7 el número era
falso (`sync_comitentes` decía 24 h contra 17 reales). Ahora `hueco_maximo_union`
une TODAS las líneas de un job sobre una grilla **semanal** —no diaria: el hueco
viernes→lunes no existe en una grilla de un día— y `hueco_maximo` quedó como su
caso de una línea, con los mismos valores de siempre.

**(2) EL QUE DE VERDAD DISPARÓ EL AVISO — una fecha de negocio no contesta «hace
cuánto que no escribe».** El detector medía contra `fecha_conciliacion`, que dice
**de qué día son los datos**. `mayor_sync` pide SIEMPRE el día hábil anterior
(está en el comentario del crontab y es el punto: contabilidad sigue cargando
asientos toda la mañana siguiente), así que esa columna **nunca puede estar más
fresca que T-1 hábil**. Un martes 08:54 UTC su valor correcto ES el viernes. El
agente comparó eso contra el reloj y le dio 3,5 días.

Es §0.u de nuevo, un nivel más abajo. Allá se aprendió que **un valor a medianoche
exacta es un día, no un instante**, y se le suma 24 h. La guarda funciona y no
alcanzaba: tapa el cierre del día, no el desfase de diseño de la fuente.

**LA CAUSA ERA LA ELECCIÓN DE COLUMNA, Y ESTABA A LA VISTA.** La tabla tiene
`actualizado_at timestamptz NOT NULL DEFAULT now()`, que cada corrida pisa
(DELETE + INSERT del día entero). `max(actualizado_at)` ES la hora de la última
corrida: contesta la pregunta que el detector hace, literalmente. Pero
`COLS_FECHA` —la lista de convenciones de nombre del repo— **estaba escrita sólo
en inglés**. Medido sobre `sql/schema.sql`: `actualizado_at` aparece **38 veces,
más que `updated_at` (31)**, y no estaba en la lista. Sin ningún match, la tabla
caía al fallback «la primera columna temporal», que es el orden del `CREATE
TABLE` — no una elección, el orden en que alguien tipeó. Y en este repo ese orden
no es neutro: las fechas de negocio se declaran arriba y el sello de auditoría al
final. **El fallback apuntaba sistemáticamente a la respuesta equivocada.**

**LA REGLA NUEVA ES DE TIPO, NO UNA LISTA.** `_elegir_col()`: una columna `date`
no tiene hora, así que **por construcción no puede ser un instante de escritura**.
Entre los sellos (`timestamptz`) gana la convención de nombre; a una `date` sólo
se cae si la tabla no tiene NINGÚN sello, y ahí se marca `es_fecha_negocio` —
porque eso hay que poder decirlo, no esconderlo. Es la misma doctrina que el resto
del módulo: se deriva de la base, no se declara tabla por tabla.

De yapa cayó un tercero, del mismo tipo: **un sello de ALTA no sirve para medir
frescura porque no se mueve.** `creado_at` se escribe una vez; en una tabla que se
upsertea, `max(creado_at)` queda congelado aunque el job la reescriba entera cada
quince minutos. Por eso los sellos de alta pierden incluso contra un sello que no
está en ninguna lista. Lo cazó `agente.habilidades`, que elegía `creada_at` en
vez de `ultima_corrida_at`.

**Y ARREGLAR `date` VS `timestamptz` DEJABA EL MISMO DEFECTO UN NIVEL MÁS
ABAJO.** Lo cazó la revisión adversarial del diff, corriendo la regla nueva
contra el schema real: entre dos `timestamptz` que no estaban en ninguna lista,
seguía decidiendo el orden del `CREATE TABLE`. Tres modos de falla distintos:

| tabla | elegía | debía | cómo falla |
|---|---|---|---|
| `operaciones.latidos` | `arrancado_at` (una vez) | `latido_at` (cada 15 s) | ruidoso: la canta caída |
| `mercado.simbolos_cuarentena` | `first_seen` | `last_seen` | ruidoso |
| `manager.tabla_perfil` | `ultimo_dato` (copiado de OTRA tabla) | `medido_at` | ruidoso |
| `manager.tokens_externos` | **`expira_at`** | `llamadas_at` | **callado** |

El último es el peor y es de otra clase: `expira_at` guarda un instante
**futuro**, así que `max()` da una fecha que todavía no llegó, el atraso sale
NEGATIVO y la tabla queda en verde **para siempre**. Un aviso falso se ve y se
vota; una tabla que no avisa nunca no se ve nunca. Por eso un vencimiento no se
elige jamás, y si es lo ÚNICO que hay la tabla queda **sin columna** —fuera del
detector— antes que sana por error.

La lista sola no podía arreglarlo: sólo cubre lo que alguien se acordó de anotar.
Se agrega una regla sobre el IDIOMA, que sí escala a un nombre nuevo — los
morfemas que dicen «último» (`ultim*`, `last_*`) mandan, los que dicen
«primero/alta» (`primer*`, `first`, `creado`) pierden, los que dicen
«vencimiento» se descartan, y los que marcan un estado excepcional (`anulado_en`,
`revocada_at`: NULL en casi todas las filas, así que `max()` describe la última
anulación y no la última escritura) pierden hasta contra un sello de alta —
porque en una tabla que sólo appendea, `ingestado_en` **es** el instante de
escritura.

**Medido sobre `sql/schema.sql` (207 tablas): 46 cambian de columna** — 26 salían
de una `date` (`portafolio.tenencia_live`, `bancos.saldos`,
`operaciones.tesoreria_saldos`, …) y 20 de un sello que no servía.

**LO QUE QUEDA ABIERTO, Y POR QUÉ NO SE CODEÓ.** Quedan **17 tablas que no tienen
ningún sello**: su única columna temporal es un `date` (`portafolio.tenencia`,
`mercado.snapshots_cierre`, `macro.uva`, …). Ahí «hace cuánto que no escribe» **no
se puede contestar**, y la pregunta correcta es la que hizo el user en §0.r: *«qué
día es hoy, cuándo es T-1, ¿hay datos? sí o no»*. Para escribir eso hay que saber
el T-N de cada tabla, y **eso no se puede derivar del código ni adivinar**
(REGLA #2). Por eso se entrega `scripts/diag_frescura_columna.py`, que lo mide:
por tabla, con qué columna se la juzga, si es un sello o una fecha de negocio, y
para las 17 el **desfase en días hábiles** contra hoy. Con esos números se declara
el T-N; sin ellos, declararlo sería tirar un 21 otra vez.

**LO QUE MIDIÓ EL DIAG EN PROD (2026-09-08), y lo que cambió por eso.** 249
tablas, 224 con columna temporal: **207 pasan a juzgarse por un sello**, 57
cambiaron de columna y quedan **17 sin ningún sello** — el grupo B. Y ahí el T-N
casi no hace falta declararlo, porque los datos lo dicen solos: **10 de las 17
dan T-1 hábil y las 10 son tablas de cierre** (`bonos_ohlc_daily`,
`snapshots_cierre`, `portafolio.tenencia`, …). El resto se separa en tres, y las
tres enseñan algo distinto:

| tabla | último dato | qué significa |
|---|---|---|
| `macro.uva` | 28/05 · **T-69 hábiles** | el UVA es diario y **ningún módulo del repo la escribe** (`core.escribe` da escritor vacío) |
| `mercado.precios_acciones` | 04/09 · T-2 | **una rueda atrás que sus nueve pares de cierre**, que están en T-1 |
| `mercado.dias_habiles` · `macro.series_macro` | 31/12 y 15/09 | **adelante del reloj** |

Las dos últimas son un descubrimiento y no un atraso: con el último valor en el
FUTURO, el atraso sale negativo y la tabla pasa todas las tolerancias **para
siempre**. No estaban al día — la pregunta no se podía hacer contra esa columna,
y nadie lo sabía. Es el mismo veneno que `expira_at`, y donde la columna es la
única que hay se contesta lo único cierto: `no_se_puede_saber`. **Un «no sé»
declarado se ve en el tablero; un verde falso no se ve nunca.**

**Y EL DIAG AGARRÓ EL BUG DEL DÍA COMETIDO DENTRO DEL ARREGLO DEL DÍA.**
`_FUTURO` estaba escrita sólo en castellano (`expira|vence|caduca`), así que
`auth.oauth_authorizations` eligió **`expires_at`** — el mismo veneno, en
inglés. Idéntico a `COLS_FECHA` sin `actualizado_at`, treinta líneas más abajo y
media hora después. Este repo nombra en los dos idiomas: **toda regla de nombre
tiene que cubrir los dos**, y esa frase vale más que las dos regexes que la
provocaron.

**El patrón, que es lo que importa más que el caso.** Las tres fallas son la misma
familia: **el sistema tenía el dato correcto al lado y usó el parecido.** La hora
real del cron estaba en el crontab (en tres líneas en vez de una); el sello de
escritura estaba en la misma tabla (con el nombre en el otro idioma); la última
corrida estaba en la misma fila (al lado de la fecha de nacimiento). Ninguna
falló ruidosa: las tres contestaron seguras y con la cara de siempre. Es REGLA #9
en su versión más barata de cometer — no dos copias del dato, **dos datos
distintos con nombres parecidos**, y el código eligiendo por nombre.

### 0.en CI ESTABA EN ROJO HACE CUATRO COMMITS Y EL MOTIVO ERA UNA VERSIÓN (2026-09-08)

Al pushear §0.em apareció, de costado, que **CI venía en `failure` desde la
corrida 1136** — cuatro commits seguidos, todos por el mismo paso: `gen_mapa_app
--check`. Ruff, import-linter y pytest pasaban en las cuatro.

Un semáforo que está siempre en rojo deja de ser un semáforo: el día que se rompa
un test de verdad, la corrida va a decir `failure` igual que ayer y nadie va a
mirar. Es la MISMA patología que §0.em un piso más arriba — una señal que no
distingue.

**No era un doc viejo.** `gen_mapa_app --check` pasaba en el sandbox y fallaba en
CI con el mismo commit. La diferencia: el sandbox tenía **FastAPI 0.141.1** y
`requirements.txt` pinea **0.136.1**, que es lo que instalan CI y el Droplet.

El conteo de «rutas con gate extra» sale de caminar
`route.dependant.dependencies`, y 0.141 baja ahí también las `dependencies=` del
`include_router` que 0.136 deja afuera. O sea que quien regeneró el doc desde un
entorno sin pinear **contó el gate del módulo como si fuera un extra por ruta**.

    /api/back-office/interbanking   decía «23 rutas con gate extra»   → son 0
    /api/portfolio                  decía 14                          → son 12
    /api/ap5                        decía 3                           → es 1

Verificado en el código, no deducido: `api/routers/interbanking.py` no tiene
**ningún** `dependencies=` por ruta; su único gate es `_BACK_OFFICE` en el
`include_router` de `api/main.py:300`, que el doc ya reporta en la columna
«módulo». Las 23 eran esa misma dependencia contada dos veces.

**La seguridad real no cambió** —los gates corren igual, esto es introspección
para un doc—, pero el doc **afirmaba más protección de la que hay**, que en un
índice de permisos es la dirección peligrosa (REGLA #8: default-deny también en
lo que se afirma). Regenerado con la versión pineada.

**El comentario de `ci.yml` decía que este check «da lo mismo acá, en el Droplet
y en el sandbox».** Era cierto, y le faltaba la mitad: *a igual versión de
FastAPI*. Esa media verdad es la que dejó el rojo cuatro días. La advertencia
quedó escrita en los dos lugares donde se comete el error — el paso de CI y la
línea de `gen_mapa_app` que produce el número.

Misma familia que §0.em otra vez: **el sistema tenía el dato al lado y usó el
parecido.** La versión que manda estaba en `requirements.txt`.

### 0.eo SE BORRÓ MANAGER → TÍTULOS → BONOS — y tres cosas quedaron sin pantalla (2026-09-08)

El user: *«el objetivo de esta sesión es borrar de todos lados la parte de BONOS
dentro de MANAGER - TÍTULOS. Esto ya es algo que hace el agent, buscá solamente
que no toque nada que haga ya [el agente], para no dejarlo ahí sin poder hacer
algo»*.

**Lo que se fue.** El sub-tab entero (`CARGAR` / `BONOS` / `REVISAR`, ~1.100
líneas de `manager-titulos-panel.tsx`), el router `api/routers/manager/ons.py`
COMPLETO, y de `bonos.py` todo menos `GET /bonos/sin-flujo` (lo consume el check
homónimo de VALIDACIONES, que no estaba en el pedido). Detrás cayeron las
funciones que no llamaba nadie más: `list_bonos`, `delete_bono`, `bonos_sin_tasa`,
`parse_flujos_bono`, `list_ons`, `ons_values`, `delete_on`, `set_sector`,
`conciliar`, `quitar_ignorar`, `listar_ignoradas` y el parser de flujos pegados
(`parse_flujos_texto`). **516 endpoints** (eran 531).

**Lo que NO se tocó, y era la pregunta.** Las dos PUERTAS DE ESCRITURA:
`bonos_admin.upsert_bono` y `ons.upsert_on`. `agente/alta.py` escribe por ahí
—«la MISMA puerta que usaba la mesa»— así que borrarlas habría dejado a
`alta_bono` / `alta_flujos` / `alta_on` sin poder aplicar nada. También quedan
`curvas_validas` (paso `rama` del pre-flight), `slug_sector` e `ignorar_concil`
(`vista.no_interesan_ons`). **Se fue la pantalla, no la función.**

**Las tres cosas que quedaron sin pantalla, y son deliberadas.** El relevamiento
encontró que el agente NO cubre tres salidas que él mismo nombraba, y el user
eligió perderlas antes que mudarlas:

1. **Cargar a mano un bono que 1816 no publica.** Los arreglos copian el cuadro
   de 1816 y solo escriben si el cotejo cierra; lo que 1816 no publica el
   detector lo derivaba a esta pantalla. Hoy es un script sobre `upsert_bono`.
2. **Restaurar una ON descartada.** El agente escribe `mercado.ons_ignoradas`
   desde «no me interesan», pero NUNCA borró de esa lista: des-descartar vivía
   en el conciliador del panel. Hoy es un `DELETE` a mano. **Descartar pasó a
   ser sin vuelta desde la app**, y el `que_hacer` de `on_faltante` lo dice.
3. **Reclasificar el sector de una ON.** 1816 no publica el sector, así que toda
   ON que da de alta el agente nace en `on_otros` (default de `slug_sector`) y
   ahí se queda: el `PATCH /ons/sector` se fue con el router. No rompe la
   valuación (el motor despacha por `curva.startswith("on_")`), solo agrupa mal
   en la vista.

**El cambio de código que esto obliga, y es el punto.** Un detector que manda al
operador a una puerta que ya no existe es peor que uno que dice que no hay
salida automática: se corrigieron los `que_hacer` y los comentarios de
`detectores/mercado.py`, `vista.py`, `fuentes.py` y `alta.py` que decían «Manager
→ TÍTULOS», «Manager → ONs → ignoradas» y «se reclasifica desde el panel de ONs».


### 0.ep «ONs HARD DÓLAR» SALÍA CRÓNICA Y NO ES UN ERROR — la definición que faltaba (2026-09-08)

La tarjeta, textual: `ONs HARD DÓLAR · on_faltante · ⚠ crónico · 3× en 30d ·
tiene arreglo → ENCONTRÓ`. El user: *«este aviso NO tiene que pasar por lo de
crónico, no va por este lado. Está bien que aparezca constantemente esto, pero
no es algo crónico, ya que no son errores. Acá o falta alguna definición de qué
es crónico o no, o esto está mal hecho si ya la hay»*.

**Eran las dos cosas.** La definición existía —§0.eg para los INFORMES, §0.ek
para el TRABAJO RECURRENTE— pero estaba escrita como **dos listas opcionales de
causas** («nace por calendario», «entran activos nuevos») en vez de como un
criterio. Y `on_faltante` no figuraba en ninguna de las dos.

#### El criterio, que ahora está escrito UNA vez

Un episodio es una vez que el problema **nació sobre el mismo sujeto**. Ese
número dice algo si el sujeto es **UNA COSA FIJA**: un job, un motor, una tabla,
un ticker. Tres veces = se rompió, se arregló y se volvió a romper.

Si el sujeto es un **GRUPO**, no dice nada. `no_estan_en_curvas` tiene por
sujeto la constante `FAMILIA_ON = "ONs HARD DÓLAR"`, y el propio detector ya lo
decía en un comentario: *«lo que no tenemos en cartera no es un problema de un
bono: es una OFERTA de catálogo»*. Esa fila nace, se tilda, se vacía y vuelve a
nacer **cada vez que 1816 publica una ON nueva**. Tres episodios en 30 días
miden cuántas tandas de ONs salieron al mercado. Nunca va a llegar a cero.

**Un comentario no es una declaración**, y ese es el punto de toda la entrada.

#### Qué se cambió

`Habilidad.informes` y `Habilidad.recurrentes` —dos tuplas opcionales— se
reemplazaron por **una sola declaración por regla**, con vocabulario cerrado:

```python
naturaleza={"no_esta_en_curvas": INCIDENTE,      # UN ticker en cartera que hoy no valúa
            "no_estan_en_curvas": RECURRENTE}    # la FAMILIA: la oferta de catálogo
```

Y `Habilidad.__post_init__` **no deja construir una habilidad cuya regla tenga
arreglo y no diga qué es**. Una regla con botón es trabajo que hace una persona:
ahí «¿esto es una falla o es el ritmo del negocio?» siempre tiene respuesta. Las
reglas sin arreglo (los avisos) caen en `INCIDENTE`, que es el comportamiento de
siempre — no cambió ni una.

#### El gemelo que también estaba mal, y las que NO se tocaron

`cedear_faltante/no_esta_en_master` tiene exactamente la misma forma (sujeto
`alta_cedear.FAMILIA = "CEDEAR"`, la misma oferta de tildar) y estaba igual de
sin declarar: iba a dar el mismo cartel en cuanto juntara tres tandas.

Lo que **sigue contando episodios**, y es a propósito: `on_faltante` y
`cedear_faltante` tienen cada una una hermana **por ítem** —`no_esta_en_curvas`
sobre un ticker que la mesa TIENE en cartera, `no_cotiza_en_primary` sobre un
símbolo—, y ahí el mismo papel volviendo tres veces sí es un patrón (el alta no
pegó, o el proveedor lo publica a los saltos). Silenciar la habilidad entera
habría apagado justo lo que hay que ver.

#### Dos cosas que hacen que esto valga

**Es retroactivo.** La exclusión se aplica en la consulta (`vista._con_historial`
y `vista.cronicos` leen `catalogo.sin_episodios()`), no al guardar: el cartel
desaparece de las filas que YA están en la base, sin backfill y sin tocar un
dato.

**Y no se puede volver a olvidar.** Dos tests lo sostienen: uno nombra las tres
familias **por su constante de módulo** (renombrar la familia no lo evade) y
exige que ninguna cuente episodios; el otro exige que toda regla con botón
declare naturaleza y que las tres listas que leen las pantallas salgan de la
misma función (REGLA #9).


### 0.eq LO QUE EL CONCILIADOR NO PUEDE VER — el diseño de la habilidad de contrapartes (2026-09-08)

Pedido del user: *«relacionado a lo de MANAGER → CONTRAPARTES, la idea es lo del
conciliador: que constantemente el AGENT sepa avisarme con cuentas nuevas si hay
contrapartes y cuáles serían. Que se dispare con nuevas cuentas, no una vez cada
tanto, y la idea es ir mejorándolo para que no falle en segmentar»*.

Esta entrada es el DISEÑO y el relevamiento. La habilidad todavía no existe: la
frena la REGLA #2 y los tres números están al final.

#### Cómo funciona hoy (verificado, no supuesto)

```
Aunesa  cuentas/listadoCuentas
   ├─ jobs/sync_comitentes        cron 14, 17 y 21 UTC L-V, tipoCuenta=Comitente + Activa
   │      → clientes.cuentas (id_cuenta, denominacion)     UPSERT
   │      → clientes.comitentes (tipo_cliente, estado, …)  UPSERT
   └─ contrapartes_seg.reconciliar()   ← el BOTÓN «Solicitar cuentas», EN VIVO
          candidata = Activa + no persona física + NO está en `contrapartes`
                      + su denominación contiene el NOMBRE de una contraparte YA CONOCIDA
          sugiere    contraparte = el keyword que matcheó
                     segmento    = FCI→Fondos · ALYC(en el nombre)→ALYC · BANCO→Bancos · si no, nada
```

#### Los tres defectos, en orden

**1. Sólo encuentra MÁS cuentas de contrapartes que YA conocemos.** El criterio
de búsqueda es `SELECT DISTINCT contraparte FROM contrapartes` convertido en un
regex de palabras. Una contraparte **nueva** —un banco, una ALYC, una gerente
que nunca registramos— no tiene keyword y **es invisible**. Es exactamente la
pregunta que el user quiere contestada, y es la única que el conciliador no
puede contestar.

**2. Es un botón.** Existe mientras alguien esté mirando esa tab.

**3. La segmentación tira la mejor señal que tiene.** `reconciliar()` LEE
`tipoCliente` de Aunesa (`Fondo Común de Inversión`, `Compañía de seguros`,
`Empresa`, `Fideicomiso`, `Institucional`) y lo usa **sólo para descartar
personas físicas**. El segmento sale de tres `if` sobre el nombre, y uno de
ellos (`"ALYC" in cp`) mira la contraparte SUGERIDA, no la cuenta.

#### Por qué importa, en plata

Registrar una cuenta como contraparte tiene dos efectos automáticos: **sale del
AuM** (`jobs/_aum_filters` reglas 3 y 4) y su `nivel_3` pasa a **PJ GRANDE**
(`segmentacion.clasificar_nivel_3`). Entonces una contraparte sin registrar
**infla el AuM** con cuotapartes que no son plata de clientes; y una cuenta
registrada de más **borra plata real** del AuM. Los dos casos **no fallan**:
cada mitad es coherente consigo misma y el número sale con confianza. Es la
REGLA #9 aplicada al negocio, no a los datos.

#### La arquitectura, y por qué cada pieza

**(A) El disparador ya existe: es el hallazgo.** Un hallazgo nace UNA vez por
`(habilidad, sujeto, regla)` y no vuelve a nacer mientras siga abierto; cuando
el trabajo se termina se cierra por ausencia. «Cuenta nueva sin decidir» ya
tiene esa forma exacta. No hace falta un bus de eventos: hace falta que el
detector corra seguido.

**(B) Correr seguido obliga a NO llamar a Aunesa.** Un detector cada 15 minutos
no puede pegar una API externa. Lee SQL: `cuentas` ⨝ `comitentes` ⨝
`contrapartes`, todo por PK. Las cuentas nuevas ya las trae el sync tres veces
por día; el agente las ve dentro de los 15 minutos.

**(C) El agujero de (B), y cómo se tapa.** El sync pide `tipoCuenta=Comitente`;
el conciliador no filtra por tipo. Puede haber cuentas que el conciliador ve y
SQL no. Si las hay, **se resuelve con el patrón FOTO que el repo ya tiene**
(`foto_primary`, `foto_1816`): un job baja la lista completa a una tabla, el
detector lee la tabla y una habilidad vigila que no quede vieja. NO se resuelve
haciendo que el detector pegue la API.

**(D) La pieza que falta en el modelo es el NO.** `clientes.contrapartes` guarda
sólo los SÍ. Que una cuenta no esté ahí significa dos cosas opuestas —«no es
contraparte» y «nadie la miró»— y sin distinguirlas **la lista de pendientes no
puede llegar a cero nunca**. Es la lección de `ficha_incompleta`, que se acotó a
las carteras de clientes justo por esto.
⚠️ **Y no se puede modelar como una fila con el nombre vacío**: `_aum_filters`
regla 3 excluye del AuM por la SOLA PRESENCIA del `id_cuenta` en esa tabla, así
que marcar «no es contraparte» ahí **le sacaría del AuM la plata a un cliente
real**. Si hace falta, va en una tabla aparte que no lee nadie más.

**(E) La habilidad.** Sujeto de GRUPO y `_items` para la identidad (el patrón de
`ficha_incompleta`): una fila que sube y baja de número, no 300 filas. Y por
§0.ep está **obligada a declarar** que sus reglas son `RECURRENTE` — entran
cuentas nuevas todo el tiempo; sin esa declaración, en tres semanas daba «⚠
crónico» igual que las ONs.

**(F) Mejorar la segmentación es MEDIR, no proponer.** Ya hay ~100 filas
clasificadas a mano: es un set de validación gratis. Toda señal candidata se
cruza contra él —cuántas reproduce, cuántas contradice, cuántas no opina— y sólo
se prende lo que reproduce. Y **una sola definición** (REGLA #9): la función
vive en `api/services/contrapartes_seg.py` y la usan el conciliador Y el
detector, nunca una copia en el agente.

**(G) Nada `automatico`, y es a propósito.** Dar de alta una contraparte mueve
plata del AuM. El agente ordena, propone y muestra la evidencia; el botón lo
aprieta una persona. Se prende cuando el HISTORIAL muestre una semana limpia,
que es el criterio del repo.

#### Los tres números que faltan, y por eso esto no se codeó todavía

`scripts/diag_contrapartes.py` (read-only) los junta:

1. **De qué tamaño es la lista de pendientes**, por tramo de señal. Si son 600,
   la habilidad nace inútil y hay que cambiarle la forma; si son 12, sirve el
   primer día.
2. **Cuál es el vocabulario real de `segmento`** y qué señal lo predice, medido
   contra lo ya clasificado a mano — con la línea de base de cuánto acierta hoy
   `inferir_segmento`, y sobre todo cuánto **contradice** (sugerir mal es más
   caro que no sugerir).
3. **`sql/schema.sql` ya no describe `clientes.comitentes`.** Al `CREATE TABLE`
   del repo le faltan **ocho** columnas que `jobs/sync_comitentes` nombra en su
   INSERT —`tipo_cliente`, `tipo`, `tipo_titular`, `clase`, `perfil_inversion`,
   `provincia`, `created_at`, `updated_at`— más
   `clientes.contrapartes.denominacion`, que `contrapartes_seg._DEN` lee en toda
   consulta del panel. O la base está adelante del schema —y entonces
   `apply_schema` **no puede reconstruirla**, y una base restaurada rompe el sync
   en la primera corrida— o algo viene fallando callado. El diag lo cruza contra
   las últimas corridas del job, que es la prueba que lo decide.
   ⚠️ Y no es un detalle de higiene para esta habilidad: `tipo_cliente` es la
   señal más fuerte para segmentar, y es una de las que faltan.


### 0.er EL CONCILIADOR ENCONTRABA CERO DE 615 — la habilidad de contrapartes (2026-09-08)

Pedido del user: *«que constantemente el AGENT sepa avisarme con cuentas nuevas
si hay contrapartes y cuáles serían»*, y después: *«no quiero usar más MANAGER →
CONTRAPARTES, la idea es canalizar todo desde acá»*.

El diseño estaba en §0.eq y la REGLA #2 lo frenaba hasta tener tres números.
`scripts/diag_contrapartes` los trajo, y **decidieron todo**.

#### Lo que midió el diag (2026-09-08)

| | |
|---|---|
| cuentas activas no persona física SIN decidir | **615** |
| de esas, cuántas encuentra el conciliador de hoy | **0** |
| contrapartes ya cargadas | 398 (Fondos 329 · ALYC 43 · Bancos 22 · vacío 4) |
| `tipo_cliente = Fondo Común de Inversión` → segmento | **Fondos en 295 de 295 (100%)** |
| qué acierta hoy `inferir_segmento` | 312 de 394 · **contradice 0** · se calla en 82 |

**Tres conclusiones, y ninguna era la que esperábamos.**

**1. El conciliador no encuentra poco: encuentra CERO.** Su criterio es un regex
armado con `SELECT DISTINCT contraparte`, o sea que sólo puede encontrar MÁS
cuentas de contrapartes que ya conocemos. Ninguna de las 615 comparte nombre con
las 398 cargadas. No es que esté mal calibrado — no puede, por construcción.

**2. Avisar de las 615 habría sido inútil.** 332 Empresa, 176 PyMES, 226
cooperativas: eso no es una lista de pendientes, es la cartera de clientes. La
señal que sí sirve es el `tipo_cliente` institucional —el dato que
`reconciliar()` LEE y usa sólo para descartar personas físicas—, y son **53**
cuentas: 43 FCI, 8 seguros, 1 institucional, 1 fideicomiso. Una lista de 53 que
llega a cero.

**3. «Falla en segmentar» era la pregunta equivocada.** `inferir_segmento`
**contradice 0 veces**: no se equivoca, se calla en 82. Y esos 82 son casi
exactamente las **87 contrapartes que no están en `clientes.comitentes`** — el
sync sólo pide `tipoCuenta=Comitente`, así que las ALYC y los bancos llegan sin
`tipo_cliente`. **No le falta criterio: le falta el dato.** Una regla más
inteligente no habría movido el número; la FOTO completa de Aunesa sí. Queda
pendiente y está dicho en el detector.

#### La habilidad

`contraparte_faltante` (DATOS, cada 30 min, `ventana="siempre"`). Lee SQL y
nunca Aunesa: las cuentas nuevas ya las trae `sync_comitentes` (14, 17 y 21 UTC)
y acá se ven dentro de la media hora. UNA fila de familia —sujeto
`CONTRAPARTES NUEVAS`— con la lista en `_items`, que sube y baja de número; el
`problema` dice además cuántas quedaron afuera del recorte, para que «53» no se
lea como «hay 53 sin decidir» cuando sin filtrar son 615.

Es **RECURRENTE** por §0.ep, y esa declaración es obligatoria: sin ella, en tres
semanas daba «⚠ crónico» exactamente igual que las ONs.

#### El botón, y las tres guardas que necesita

`alta_contraparte` despliega la lista para tildar y completar (nombre y
segmento). **Equivocarse acá cuesta plata**: cada alta saca esa cuenta del AuM
(`_aum_filters` reglas 3 y 4) y le pone `nivel_3 = PJ GRANDE`. Por eso:

1. **No se escribe lo que manda el navegador.** Cada cuenta se verifica contra
   la lista VIVA que recalcula `preview`. Sin eso, esta puerta aceptaría sacar
   del AuM cualquier cuenta del padrón — y no fallaría nada.
2. **Escribe por la puerta única** (`contrapartes_seg.add_contraparte`), la
   misma que usa Manager. Un INSERT propio sería una segunda definición de «dar
   de alta una contraparte» (REGLA #9).
3. **Nunca automático.** `automatico` vacío es una declaración con su test: un
   robot no decide de qué cuenta deja de contarse la plata.

#### Lo que NO se hizo, a propósito

- **La FOTO de Aunesa.** Sin ella el agente no ve ALYC ni bancos nuevos. Es el
  próximo paso y está escrito en el docstring del detector, no supuesto.
- **Poder decir que NO.** Una cuenta institucional que no es contraparte vuelve
  a aparecer para siempre. Necesita una tabla aparte (§0.eq (D)) y con 53 filas
  todavía no duele.
- **`codigo_mae`.** 135 de 398 lo tienen vacío y no es derivable: lo asigna el
  MAE. Meterlo en este aviso habría sido ruido sobre un tercio de la tabla.


### 0.es LA CONTRAPARTE NO SE LEE DEL NOMBRE: SE APRENDE DE LAS QUE YA ESTÁN (2026-09-08)

Cuatro pedidos del user sobre la tarjeta de `contraparte_faltante`, ya
funcionando en pantalla. Tres son de forma; el cuarto cambia lo que la
habilidad sabe hacer.

#### El caso que lo explica todo

> *«2010 FCI Consultatio Estrategia I · 2014 FCI Consultatio Estrategia II ·
> 1925 FCI Consultatio Estrategia III · 2011 FCI Consultatio Estrategia IV — si
> acá leés uno creerías que es Consultatio, pero si revisás el historial que ya
> hay, Consultatio es **ONE618**. La mayoría de los fondos, viendo similitudes,
> se podrían ir sugiriendo.»*

**Y ahí está el peligro, no la oportunidad.** «Consultatio» es la respuesta que
suena bien, y por eso el que tilda la acepta. Un sugeridor que mire el NOMBRE se
equivoca con seguridad; uno que mire la TABLA acierta por el mismo motivo por el
que el user acierta: porque hay historia.

#### Cómo funciona el sugeridor

De cada contraparte YA cargada se sacan las palabras **distintivas** de sus
cuentas —descartando las que están en media tabla (`FCI`, `FONDO`, `SEGUROS`,
`SOCIEDAD`…)— y se arma `{palabra: {contraparte: en cuántas cuentas}}`. Una
cuenta nueva hereda la contraparte de la palabra que comparte.

Dos guardas, y las dos son sobre cuándo **no** opinar:

1. **Sólo si la palabra apunta a UNA sola contraparte.** Si «CONSULTATIO»
   estuviera bajo dos nombres, no se propone nada. No es que la sugerencia sea
   peor: sería una moneda al aire con cara de dato.
2. **La propuesta viaja con su evidencia** — «CONSULTATIO está en 3 cuenta(s) de
   ONE618» — para poder rechazarla sin abrir otra pantalla.

Vive en `api/services/contrapartes_seg.py`, al lado de `inferir_segmento`, así
lo usan el agente y el conciliador de Manager (REGLA #9).

**Y se mide dejando cada fila afuera.** `diag_contrapartes` §5b arma el índice
SIN la fila que está evaluando: medirlo contra un índice que ya contiene la
respuesta daría 100% siempre y no diría nada. El número que importa es
CONTRADICE — proponer mal y que alguien lo tilde es el único error caro.

#### Y la primera versión salió MAL — lo que enseñó medirla

`diag_contrapartes` §5b sobre las 405 cargadas: **acertaba 42% y CONTRADECÍA 27
veces**. Los fallos, textuales, mostraron tres defectos distintos:

```
FCI SBS MULTIACTIVOS          → decía SCHRODER, es SBS
FCI ADCAP BALANCE MULTIACTIVO → decía ONE618,   es ADCAP
FCI MAX MONEY MARKET          → decía TORONTO,  es MAX
FCI ALLARIA DÓLAR PERFORMANCE → decía SCHRODER, es ALLARIA
```

1. **Elegía la palabra con MÁS cuentas detrás**, que es exactamente al revés: la
   que aparece mucho es de PRODUCTO (`PERFORMANCE`, `BALANCE`, `MONEY`,
   `ACTIVOS`) y la que identifica es la marca. El docstring decía «gana la más
   rara» y el código hacía `max()` por cantidad — **el comentario y el código no
   decían lo mismo**, que es el bug que ningún test agarra.
2. **Tiraba las palabras de menos de cuatro letras**, que es justo donde viven
   las marcas: `SBS`, `MAX`, `BM`, `IAM`. Y los números, donde vive el `1810` de
   Credicoop.
3. **Exigía unicidad absoluta**: `ALLARIA` quedaba descartada por existir
   `ALLARIA - ALYC` con UNA cuenta, y ganaba una palabra cualquiera.

La v2 usa la estructura que estos nombres tienen de verdad —**la marca va
adelante, el producto atrás**—: lee las tres primeras palabras de izquierda a
derecha y se queda con la primera que el índice conozca, con una contraparte que
se lleve el 80% de sus cuentas y tenga más de una. Y desempata por el nombre de
la propia contraparte, que es lo que distingue `ALLARIA` de `ALLARIA - ALYC`.

Sobre los diez fallos medidos: **9 aciertos, 0 contradicciones, 1 sin opinar**.
Los diez están congelados como test — no un porcentaje de acierto, sino **cero
contradicciones**: callarse deja la fila para escribir a mano, proponer mal hace
que alguien tilde una cuenta que mueve el AuM.

#### El «no me interesa», que es lo que deja la lista llegar a cero

Mismo patrón que las ONs y los CEDEARs (§0.eh): se silencia **por clave**, no
por evento, y el aviso vuelve solo con las cuentas nuevas.

⚠️ **Va en `clientes.contrapartes_descartadas`, una tabla propia.** No se puede
anotar el NO como una fila vacía en `contrapartes`: `jobs/_aum_filters` regla 3
excluye del AuM por la **sola presencia** del `id_cuenta` ahí, así que marcar
«esta no es contraparte» en esa tabla le sacaría del AuM la plata de un cliente
real, y no fallaría nada. El detector las resta EN LA MISMA consulta que arma la
lista: dos filtros serían dos números que se desincronizan.

#### Lo que se sacó de la pantalla

*«Demasiado texto, no me interesa nada acá»*, y tenía razón. La tarjeta traía el
desglose por tipo, el denominador del recorte, ocho denominaciones truncadas y
una frase sobre el AuM — **con la lista entera a un click**. Quedó el número y el
botón, igual que `ficha_incompleta` (§0.dz): acá no hay un problema que
explicar. También se fue el cartel del AuM del listado.

**Corto no es perder el dato**: el desglose y el denominador siguen viajando en
la evidencia, que es donde se miran cuando se quieren mirar. Un test lo exige en
las dos direcciones — que el texto no vuelva a crecer y que el dato no se pierda.


### 0.et EL TRABAJO RECURRENTE NO ES UNA NOVEDAD DEL DÍA — sale de AHORA (2026-09-08)

El user, viendo `CONTRAPARTES NUEVAS` en AHORA: *«esto no es para AHORA, es para
ENCONTRÓ»*.

Tiene razón, y es la misma distinción de §0.ep leída del otro lado. **AHORA
contesta «¿qué pasó hoy?»**. Una COLA DE TRABAJO no pasó hoy: está desde siempre
y baja cuando alguien la trabaja. Mezclarla con las novedades del día hace las
dos cosas peor — el que busca qué se rompió tiene que saltearla, y el que quiere
trabajarla la busca en la pantalla equivocada.

**El criterio no es una lista nueva**: son las reglas ya declaradas `RECURRENTE`
(§0.ep). `vista.ahora()` las saca leyendo `catalogo.recurrentes()`, y el badge
sale de la misma lista, así que no pueden decir cosas distintas.

⚠️ **La guarda que evita que esto haga desaparecer un aviso.** ENCONTRÓ sólo
muestra lo que tiene botón: una regla recurrente SIN arreglo, sacada de AHORA,
no quedaría en ninguna pantalla. Un test lo exige — toda regla recurrente tiene
arreglo. Y por eso el criterio son las RECURRENTES y no los INFORMES: los
informes son avisos sin botón y su única pantalla es AHORA.

---

### 0.eu LA ILIQUIDEZ NO ES UN ERROR — el silencio como respuesta (2026-09-08)

El user, mirando AHORA:

> *«8/9 10:36 · confirmado 12:51 · BYZ2O · bono_sin_precio · ×26 · sin punta hoy
> · lo estamos pidiendo, así que es iliquidez»* — **«Tengo muchos casos así. Si
> es por iliquidez NO LO QUIERO VER. La lógica sería: ver solamente algo que ES
> un error. Iliquidez no es un error, por lo que no me interesa el aviso: SIN
> AVISO DOY POR SENTADO LA ILIQUIDEZ.»**

#### Qué estaba pasando, exactamente

`bono_sin_precio` corre cada 5 min en rueda, cruza `mercado.curvas` contra
`mercado.market_snapshot` y separaba CINCO estados. Cuando el símbolo estaba en
el snapshot —o sea, el motor lo suscribió y **estamos escuchando**— con
`last_price = 0`, emitía un hallazgo `sin_punta` severidad `baja`:

    problema:   «sin punta hoy · lo estamos pidiendo, así que es iliquidez»
    que_hacer:  «Nada que apretar: el papel no operó.»

Sin arreglo declarado, su clase es **aviso**: no entra a ENCONTRÓ (que exige
botón) y vive sólo en AHORA. El `×26` no son 26 hallazgos: es UNA fila que el
detector volvió a ver 26 veces (`veces`), y el «confirmado 12:51» es su
`visto_ultima_vez`. Nace a las 10:36 y ocupa un renglón de AHORA todo el día.

#### La contradicción que ya estaba escrita en el modelo

El invariante #2 dice **«un hallazgo sin `que_hacer` no se guarda»**, y lo
sostiene un CHECK de la base. `sin_punta` lo cumplía **de forma nominal**: el
campo tenía texto, y el texto decía que no había nada que hacer.

Medido —leyendo el AST, no a ojo— sobre los **83 textos `que_hacer`** que
declaraba el agente (**52** llamadas a `Hallazgo()` en `agente/detectores/` +
**31** filas de `agente/reportes.py`): **CUATRO** empezaban diciendo que no había
nada que hacer, y `sin_punta` era **el único de los detectores**. Los otros 79
nombran una acción: «Relanzar», «Revisar», «Mirar el log», «Cargarle el
símbolo», «Cerrarlo en el router». O sea: **el modelo ya sabía que esto no era
un hallazgo, y la frase lo esquivó.** Un CHECK que se contesta «no hay nada que
hacer» no es un CHECK.

#### Por qué el silencio SÍ alcanza, y de qué depende

Un bono sin precio tiene cuatro causas y **tres son nuestras**, las tres `alta`:

    sin_simbolo        nadie le cargó el símbolo      → NUESTRO   → alta
    simbolo_rechazado  lo pedimos y no existe allá    → NUESTRO   → alta
    no_suscripto       está y nadie lo pidió          → NUESTRO   → alta + botón
    (sin punta)        lo pedimos y no vino           → EL PAPEL  → SILENCIO

Si ninguna de las tres cantó, la única causa que queda es el mercado. Por eso
«sin aviso doy por sentado la iliquidez» es una inferencia **válida** y no una
apuesta: el silencio no significa «no miramos», significa «miramos las tres
cosas que pueden ser culpa nuestra y ninguna pasa».

⚠️ **Y ahí está la dependencia que hay que cuidar** (invariante #15): el día que
se saque una de esas tres ramas, callar el cuarto caso deja de ser honesto y
pasa a esconder un error. Lo congela
`test_la_iliquidez_no_genera_hallazgo_y_las_tres_causas_nuestras_si`, que
verifica las cuatro ramas juntas y no la que se sacó.

#### `precio_viejo` NO se tocó, y es a propósito

Es la otra regla ruidosa de la misma habilidad (195 de los 225 hallazgos del
2026-08-28, §0 de `agente/reloj.py`), pero **no es el mismo caso**: ahí hubo
precio y dejó de llegar, y eso puede ser el papel o puede ser nuestro feed. Su
`que_hacer` nombra una verificación real. Hay algo que mirar → hay hallazgo.

#### Lo que la guarda destapó sola: tres casos más

La prohibición no quedó en un test: vive en `tipos.Hallazgo.__post_init__`
(`tipos.NADA_QUE_HACER`), porque *un test que existe para recordarte algo es la
señal de que el diseño no lo garantiza solo*. Al ponerla, la suite reventó en
tres avisos de jobs (`agente/reportes.py`) que nadie había mirado con esta luz:

| Aviso | Qué decía | Qué se hizo |
|---|---|---|
| `tamar_1816 · sin_dato` | «Nada que apretar: 1816 no cubre esos tickers… si es uno que importa, pedirle la cobertura» | **Reordenado**: la acción existía y estaba enterrada detrás de la frase |
| `validar_instrumentos · tickers_no_vigentes` | «Nada que apretar: son papeles que amortizaron. Si uno sigue vivo, destildar VIGENTE» | **Reordenado**, ídem |
| `cleanup_curvas · borrados` | «Nada que hacer: **es lo esperado**» | **Se sacó la fila.** Declaraba por escrito que no era un problema |

El tercero es el mismo defecto que `sin_punta`: un bono que el cleanup borra
porque vence es el sistema funcionando. **No se pierde el dato** — el job sigue
guardando `borrados` y `borrados_lista` en `manager.job_runs`, que es donde
vivían antes de que existiera la tabla de reportes. Lo que se sacó es el AVISO,
no el registro.

#### Nada que borrar a mano

Los `sin_punta` abiertos **se cierran solos**: la habilidad deja de verlos y
`registro._cerrar_ausentes` los cierra POR AUSENCIA en la primera corrida `ok`
—que además es el cierre que NO habilita reincidencia (invariante 4)—. No hay
backfill, no hay `UPDATE` masivo, no aplica la REGLA #4.

### 0.ev EL MAPA — la misma tabla, leída como estructura (2026-09-08)

**De dónde salió.** Mirando el grafo que Google ADK dibuja de un agente —un
árbol con el agente arriba y sus herramientas colgando—, el pedido fue directo:
*«no hay nada de lo que tengo que yo pueda ver así»*. Es cierto, y el hueco es
real: el catálogo declara 27 habilidades con dominio, ritmo, ventana y arreglo,
y la única forma de leerlo era una lista de 27 filas.

**Lo primero que se decidió fue lo que NO se hace: un grafo.** El dibujo de ADK
tiene flechas porque ahí hay FLUJO — un agente le pasa la posta al siguiente por
un estado compartido. Las habilidades del AV AGENT **no se hablan entre sí**: no
comparten estado, no se llaman, no hay orden entre ellas. Dibujarles flechas
sería copiar la forma sin la estructura, que es la manera más rápida de tener un
diagrama que parece decir algo y no dice nada. Lo que hay es un catálogo con
atributos categóricos y una dimensión temporal, y eso se dibuja como grilla.

**Qué pregunta contesta, y es otra que la de la lista.** La lista contesta «¿qué
pasó con cada habilidad?». El mapa contesta **«¿qué NO se está mirando?»**. Con
27 filas los huecos de cobertura son invisibles; en la grilla dominio × ventana
una celda vacía se ve de un golpe. La primera lectura ya dejó tres a la vista:
SISTEMA está entero en `siempre` (nada mira distinto dentro y fuera de rueda),
`rueda` y `cierre` son exclusivas de MERCADO, y `habil` la usa **una** habilidad
en todo el sistema. Ninguna de las tres es necesariamente un error — son
decisiones que hasta hoy estaban implícitas en 27 filas y ahora están dibujadas.

**Tres lecturas, un control.** No es un toggle adentro de otro toggle: la tab
HABILIDADES tiene un selector de tres posiciones —LISTA · MAPA·DOMINIO ·
MAPA·RITMO— porque son tres vistas hermanas del mismo payload, no una principal
con variantes. En DOMINIO las columnas van ordenadas **por tamaño**, así que la
altura de cada una es el dato: se ve que SISTEMA pesa el doble que MERCADO sin
leer un número. En RITMO son carriles de 2 minutos a 12 horas.

**El color significa UNA cosa: salud.** El dominio se codifica por posición (en
DOMINIO) o por etiqueta de texto (en RITMO), nunca por color — dos escalas de
color en el mismo dibujo no se leen. La clase va en la FORMA: cuadrado relleno =
tiene arreglo, contorno = solo avisa. Así salud y clase se leen a la vez sin
competir.

**Cero backend.** `GET /api/agente/habilidades` ya devolvía todo lo que el mapa
necesita —y de yapa `ultima_corrida_at`, `ultimo_resultado`, `corridas_hoy` y
`hallazgos_abiertos`, que es lo que convierte el dibujo en un tablero de salud y
no un diagrama muerto. No se agregó ningún endpoint, ninguna query y ninguna
columna. Agregar un endpoint que contara lo mismo habría sido servir la REGLA #9
en bandeja.

**Los cuatro estados se mudaron a `tipos.ts`.** `ESTADO_HABILIDAD` y
`saludDe()` vivían dentro del panel; ahora los dibujan **dos** pantallas. Con
una copia en cada archivo, el día que cambie un color el mapa y la lista
pintarían la misma habilidad de distinto y ninguno de los dos fallaría.

**La grilla de cobertura se calcula en el navegador, y es la excepción que
confirma la regla.** El front no deriva contadores: la razón es que un número
calculado acá puede discrepar con el backend. Acá no hay segunda fuente — es un
PIVOT del mismo array que se está dibujando dos centímetros más arriba, en el
mismo render. No puede decir algo distinto de la lista porque **es** la lista.

**Los dominios y las ventanas salen de los datos, no de una lista escrita a
mano.** Es la condición para que el mapa sea la prueba visual de la REGLA #10:
si una habilidad está declarada, aparece; si aparece algo que no reconocés, está
declarado. Con las columnas hardcodeadas, una ventana nueva no se dibujaría y el
mapa mentiría en silencio — que es exactamente el modo de falla que el agente
existe para evitar.

**Lo que quedó pendiente.** La tab mostraba **29** y el catálogo tiene **27**.
No se tocó: hay que ver si el front cuenta algo que el catálogo no tiene o si es
otra cosa. Es, otra vez, dos conteos de lo mismo que no coinciden.

---

### 0.ew EL DETECTOR NO VEÍA A LOS ESCRITORES — «es tiempo real (cada 8 s)» sobre un job sano (2026-09-08)

El user, mirando once tarjetas de AHORA:

> *«No tengo claro estos que pasan todo el tiempo cuál es el problema… ahí es
> donde no entiendo de dónde sale lo de "su cron dice cada X". ¿Siempre tiene
> que escribir? ¿Falló realmente?»*

#### De dónde sale «su cron dice cada X»

La cadena entera, y ninguna parte es nueva:

```
escribe.que_relanzar(tabla)   → qué job la escribe (grep de INSERT / pg_mirror)
crontab.ritmo_declarado(job)  → hueco_maximo_union de SUS líneas de cron
tope = hueco × 1.5            (tablas.MARGEN_DECLARADO)
```

`hueco` **no es «cada cuánto corre»**: es el agujero MÁS LARGO de su grilla
semanal, casi siempre el de la noche. `bcra_research` (`0 12,16,20,23 * * 1-6`)
tiene su hueco entre las 23 y las 12 → 13 h → tope 19,5 h. La tarjeta apareció a
las 20,1 h. Los cuatro casos del día cayeron **apenas** pasado el tope: un
detector calibrado al filo dispara todas las mañanas.

#### Pero la pregunta de fondo era otra: ¿siempre tiene que escribir?

**No.** Y ahí estaban los bugs. Medido corriendo `core/escribe.py` sobre el repo:

```
agente.acciones                  dispara=no_se  relanzar=''  escriben=[]
operaciones.negocio_movimientos  dispara=no_se  relanzar=''  escriben=[]
valuaciones.portfolio_snapshot   dispara=no_se  relanzar=''  escriben=[]
```

**Tres agujeros distintos, un mismo final.** Sin escritor no hay ritmo
declarado; sin ritmo declarado `tablas.frescura` cae al MEDIDO; y un job que
appendea un lote tiene sus filas separadas por milisegundos, así que se mide
«tiempo real, cada 8 s» y se le exige el ritmo de un feed live: tolerancia de 30
minutos de rueda, tarjeta todos los días, con el job perfecto. Es exactamente el
bug que §0.u dio por resuelto — pero sólo para las tablas **con** escritor
detectado. Estas se colaban por el agujero del detector, que es el peor lugar
para tener uno.

Los tres agujeros:

| Tabla | Por qué no se veía |
|---|---|
| `operaciones.negocio_movimientos` | `jobs/negocio_movimientos.py:198` hace `INSERT INTO negocio_movimientos` **sin schema** (lo resuelve el `search_path`) y el regex exigía el punto |
| `valuaciones.portfolio_snapshot` | `engines/portfolio_snapshot.py:167` escribe con `write_snapshot(...)`, que **no estaba** en el patrón de puertas de `pg_mirror` |
| `agente.acciones` | la escribe `agente/`, que no está entre las carpetas que se escanean — y encima es una tabla **de ocasión** |

#### La ironía que hay que mirar de frente

`agente.acciones` es el LIBRO del agente: escribe cuando alguien aprieta un
arreglo. O sea que la tarjeta *«agente.acciones no escribe hace 6,1 h de rueda ·
es tiempo real»* era **el agente avisando de que el agente no arregló nada** —
un día sin arreglos aplicados, que es un día normal. Se declararon las cuatro
tablas del agente que escriben por OCASIÓN en `escribe.POR_OCASION`, y una de
ellas es `agente.reincidencias`: **la que DEBE estar vacía**. Exigirle frescura
es exigir que algo se rompa.

#### Lo que el arreglo destapó solo — cuatro cosas más

1. **La PROSA escribía.** Este repo cita el código entre backticks en sus
   comentarios, y el regex no distingue una cita de una ejecución: al ampliar el
   patrón, **la docstring que explica el arreglo quedó anotada como escritora de
   la tabla que nombra**. No es cosmético — `quien_escribe()` viaja en la
   evidencia del hallazgo y manda a mirar el archivo equivocado. Se borran los
   spans entre backticks antes de buscar: el SQL de verdad nunca va entre
   backticks.
2. **`write_hist` indexaba la COLECCIÓN.** Su primer argumento es `'FuturosDLR'`,
   no una tabla; siempre escribe en `mercado_hist`. Estaba en el patrón general,
   así que dejaba claves fantasma (`breakevenshistorico`, `forwardshistorico`) y
   a `mercado.mercado_hist` **sin escritor**.
3. **Un nombre corto AMBIGUO se le adjudicaba a la tabla equivocada.** Medido
   sobre `sql/schema.sql`: de 208 tablas, TRES nombres viven en dos schemas
   (`cuentas`, `movimientos`, `presencia`). `quien_escribe` busca por el nombre
   completo Y por el corto, así que guardar `cuentas` le habría dado a
   `clientes.cuentas` el escritor de `bancos.cuentas`: mandar a relanzar, con
   seguridad, el job equivocado. Se descartan → quedan en `no_se`, que **sigue
   exigiendo** frescura (el default seguro).
4. **`que_relanzar` decidía por el ORDEN DE ESCANEO de las carpetas.**
   `mercado.market_snapshot` —la tabla de precios— la escriben `engines.valores`
   (cada tick del WS) y tres jobs que le parchean unas filas. Contestaba
   `jobs.tamar_1816`. Ahora **un motor le gana a un job**: si la tabla está
   quieta, el que dejó de escribir es el motor. Es el mismo defecto que
   `agente/tablas.py` describe para elegir la columna de fecha — dejar que
   decida el orden en que alguien escribió el archivo.

#### El resultado, medido

`core/escribe.py` pasó de **139 a 153** claves. **30 tablas ganaron escritor**
(entre ellas `mercado.market_snapshot`, `mercado.options_*`, `valuaciones.dolar`,
`clientes.comitentes`, `home.news_headlines`) y **16 claves salieron**: nombres
cortos que ahora se resuelven a su nombre completo, más las claves fantasma de
`write_hist` y de la prosa. **Ninguna tabla cambió de `que_relanzar`** salvo
`market_snapshot` — o sea que la cobertura subió sin re-atribuir nada.

Un solo job cambió de prueba: `negocio_chain` pasó de `PRUEBA_CORRIDA` a
`PRUEBA_TABLA` porque la cadena corre `jobs.fci_bilateral`, que escribe
`operaciones.operaciones` — una tabla que **ya tenía contrato en SALUD**. No
inventa un juicio nuevo (`_al_dia_por_contrato` llama a `salud._chequeo_dato`
con ese mismo contrato y su tolerancia de hábiles): lo que gana es el BOTÓN sobre
un veredicto que hasta hoy no tenía dónde apretarse. Es la lección de
`motor_caido` otra vez: **mirar el proceso no es mirar el resultado**.

#### El parser de `sql/schema.sql` se mudó a `core/`

Resolver un nombre corto necesita saber en qué schema vive cada tabla, y eso lo
dice `sql/schema.sql`. El parseo estaba en `agente/peso.py` y `core/` no puede
importar de `agente/`. Se mudó a **`core/schema_sql.py`** y `peso` delega: dos
parsers del mismo archivo son dos verdades sobre qué tablas tenemos, y la que se
desincronice no falla — contesta distinto (REGLA #9 B).

#### LO QUE ESTO **NO** ARREGLA, y es la mitad más grande

Quedan en pie los dos casos que el user marcó y que **no son un problema de
cobertura sino del modelo de la habilidad**:

1. **El sello de ALTA leído como sello de escritura.** `research.bcra_series` y
   `research.fred_observations` tienen como única columna temporal
   `ingestado_en`; `ia.research`, `created_at`. Las tres están en
   `tablas._SELLO_ALTA`, y los tres jobs son **incrementales por watermark**: si
   el BCRA/FRED no publicó un día nuevo, mandan CERO filas y el sello no se
   mueve. La tarjeta mide **cuándo apareció dato nuevo por última vez**, no
   cuándo corrió el job. El user lo dijo exacto: *«hay días como los martes que
   no va a tener datos desde el viernes»*.

2. **«El job no corrió» y «el job corrió y no escribió» son la misma tarjeta**, y
   son problemas opuestos: uno es del scheduler, el otro es del código. Es el
   caso de `bancos.sync_log`.

**El dato que contesta las dos ya existe y el detector no lo mira:
`manager.job_runs`.** Verificado: `bcra_research`, `fred_research`,
`interbanking_sync` y `research_mail` usan los cuatro `JobRunLogger`. Es la misma
fuente de la que ya leen `salud` y `trajo_poco`.

El orden correcto de preguntas, que hoy `tabla_quieta` no hace:

```
1. ¿corrió el job?           → manager.job_runs
       no corrió             → el problema es ese, y lo canta `salud`
2. corrió: ¿trajo algo?      → `trajo_poco` ya lo mide contra su normal
       trajo 0 y su normal admite 0  → SILENCIO (§0.eu: no es un error)
3. corrió y trajo: ¿escribió? → recién ahí, tabla quieta = escritura rota
```

> Resuelto en §0.fb (2026-09-11): el detector hace el cruce solo, para toda tabla
> con job de reloj.

`scripts/diag_tabla_quieta.py` hace ese cruce a mano —para cada tabla con
hallazgo abierto: la clase de su sello, el ritmo declarado, el veredicto, y las
últimas corridas reales del job con su `status` y sus `stats`— y es lo que hay
que correr **antes** de meter el paso 1 adentro del detector. Es la REGLA #2: el
cambio de modelo se decide con el número de prod delante, no con la lectura del
código.

---

### 0.ex «NO PUDE MIRAR» NO PUEDE SER CONTAGIOSO — `latencia` se apagaba por la tabla del navegador (2026-09-09)

Relevando el subsistema entero apareció el mismo bug que ya se había encontrado
y corregido **a medias**.

`latencia` miraba TRES cosas de una sola vez, y el resultado era uno solo:

| | Qué vigila | De dónde lo lee |
|---|---|---|
| 1 | endpoints degradados contra su propia normalidad | `manager.latencia_endpoints` |
| 2 | endpoints que devuelven **5xx** | la misma tabla |
| 3 | **vistas ciegas**: pantallas que no pueden refrescar | `agente.pulso_cliente` |

`_vistas_ciegas` levanta `SinDatos` cuando no puede leer su tabla — que es lo
correcto: sin pulsos no se afirma nada y no se cierra nada. El problema es que
vivía **adentro** de `latencia`, así que ese `SinDatos` dejaba a la habilidad
entera en «no pude mirar»:

```python
out = _vistas_ciegas(u)   # ← levanta, y se lleva puesto todo lo de abajo
for c in casos:           #   ...aunque `casos` YA estaba calculado
```

Tres costos, y el segundo es el peor:

1. **Los 5xx no se reportan.** Son lo más grave que vigila esta habilidad y se
   leen de una tabla que no tiene nada que ver con la que falló.
2. **El trabajo ya estaba hecho y se tiraba.** `maq.comparar()` corre ANTES:
   los endpoints rotos estaban contados y se descartaban.
3. **El cartel manda al lugar equivocado.** En HABILIDADES queda «no pude leer
   `agente.pulso_cliente`» — te manda a mirar el pulso del navegador mientras
   lo que se cae es un endpoint.

#### Lo que hace que esto valga una entrada: ya lo sabíamos

Es **el mismo bug** que hizo salir a `pantalla_tildada` de este lugar (§0.dm), y
su docstring lo dejó escrito con todas las letras:

> *«Nació ahí y estuvo mal media hora: `latencia` mira TRES cosas y, con el
> tilde adentro, no poder leer una tabla nueva la dejaba entera en "no pude
> mirar" — o sea, la degradación de endpoints y los 5xx, que son lo importante
> y se leen de otro lado, se apagaban por una fuente secundaria.»*

Se separó **la mitad** —los tildes— y se dejó la otra adentro. El diagnóstico
estaba escrito y completo; la corrección se aplicó a un caso en vez de a la
forma.

#### La regla, que es el invariante #1 puesto al revés

> **Si «no pude mirar» no puede cerrar nada, tampoco puede ser CONTAGIOSO.**
> Una habilidad, una fuente que puede faltar.

`vista_ciega` pasa a ser habilidad propia (SISTEMA, cada 10 min, ventana
`siempre`, sin arreglo — el agente no reinicia la API). `latencia` queda con
una sola tabla y sin umbrales. Ninguna regla cambia de nombre: el hallazgo
sigue siendo `vista_ciega` sobre el mismo sujeto, así que lo que estaba abierto
no nace de nuevo ni se duplica.

Lo congela `test_no_poder_mirar_una_fuente_no_apaga_las_otras`, que prueba la
FORMA y no el caso: con el pulso muerto, un 5xx tiene que salir igual, y ninguna
de las dos puede volver a leer la fuente de la otra.

⚠️ El test mira el CÓDIGO (`_codigo()`), no el archivo: los dos docstrings se
nombran entre sí a propósito —explican el bug que evitan— y un test que grepea
la prosa castiga justo la documentación que hace falta. Es la trampa que ese
helper ya documentaba, y en la que caí al escribir este test.

---

### 0.ey UNA HABILIDAD FANTASMA CON LA LUZ VERDE — y los 43 hallazgos que no se ven en ninguna pantalla (2026-09-09)

Dos cosas distintas que aparecieron en la misma pantalla y comparten el modo de
falla de la REGLA #9: **cuando se rompe, no falla nada**.

#### (A) `licitacion_anunciada` — verde, con fecha, y no existe

El `--estado` del daemon imprimió esto:

```
agente: catálogo sincronizado — 28 habilidades      ← lo dice el CÓDIGO
...
licitacion_anunciada   aviso   ok   0 0 0 0   2026-09-02 03:24:15
```

**29 filas impresas para 28 habilidades declaradas.** `licitacion_anunciada` se
había borrado de `agente/catalogo.py`; `sincronizar()` la marcó `activa = false`
y ahí terminaba todo, porque **nadie leía `activa`**: ni el panel del front, ni
el mapa, ni `_imprimir_estado()`. Verificado: en todo el frontend la palabra
aparecía UNA vez, en la declaración del tipo.

Sin ese campo, el color sale de `ultimo_resultado`, que quedó congelado en el
`ok` de la última corrida real. Resultado: un **punto verde** con el texto «miró
y guardó lo que vio» y un «02/09» en una lista donde todo lo demás decía hoy.
No es cosmético: el agente entero existe para que «miré y estaba bien» y «no
miré» no se dibujen igual, y acá se estaba dibujando «miré y estaba bien» sobre
algo que **no corre**.

**La fila `activa = false` tiene un motivo real, y se puede comprobar.** Se
conserva porque los hallazgos históricos la referencian por nombre (`hallazgos.habilidad`
es `text`, sin FK) y sin ella el HISTORIAL no puede decir de qué habilidad
hablaba. Pero eso vale **si dejó historia**. `licitacion_anunciada` tenía 0
hallazgos y 0 acciones: no guardaba historia, inventaba una habilidad.

Entonces, dos comportamientos y ninguno a criterio de nadie:

> **Fuera del código y SIN historia → se BORRA** (`sincronizar()`, con el doble
> `NOT EXISTS` como condición, no como formalidad).
> **Fuera del código y CON historia → sobrevive `activa = false` y se dibuja
> «DADA DE BAJA»**, gris, antes de mirar cualquier resultado.

La baja gana sobre el último resultado en los dos lugares que pintan: `saludDe()`
en el front (la única función que da color, compartida por la lista y el mapa) y
`_imprimir_estado()` en `jobs/agente.py`, que además la saca del conteo de «no
pudieron mirar» — una habilidad que no corre no es una que falló.

#### (B) Lo abierto que no se ve en ninguna pantalla

Del mismo listado salió el otro número. De **50 hallazgos abiertos**, a lo sumo
7 tienen botón. Los otros **43 son avisos**, y un aviso vive en AHORA, que
muestra lo de HOY: al día siguiente se va solo de la pantalla sin haberse
resuelto ni cerrado. Sigue abierto, el detector lo vuelve a confirmar en cada
pasada, y no lo mira nadie.

⚠️ **Eso todavía es una hipótesis, no una medición.** «43 sin botón» está
verificado del listado; «cuántos de ésos nacieron hoy» —lo único que decide si
se ven o no— NO se midió. Por eso lo primero no es una pantalla nueva sino
`scripts/diag_invisibles.py`, read-only, que contesta cuántos hay, de qué
habilidades, hace cuántos días y con qué severidad.

**El diag no define «visible»: se lo pregunta a las pantallas.** Llama a
`vista.ahora()`, `vista.encontro()` y `vista.cronicos()` —las mismas funciones
que dibujan el modal— y compara por `id`. Un `WHERE` propio sería una CUARTA
definición de «lo que está abierto» conviviendo con las tres reales, y eso ya
pasó adentro de este mismo subsistema: `cola.investigables()` ofrecía 60 casos
mientras la pantalla mostraba 3 y 3, porque tenía su propio criterio.

La decisión de diseño —¿dónde vive un problema real que nadie puede apretar?—
queda ABIERTA hasta tener el número.

### 0.ez EL DÓLAR QUE NADIE DECLARABA — 481 bps al CCL, en tres pipelines (2026-09-09)

El user, mirando RESEARCH → RENTA FIJA ARGENTINA → SPREAD y COMPARAR: *«creo que
los datos están viniendo mal, vienen con moneda denominación CCL y no es ese, es
MEP. ¿Podemos validar?»*.

**Era cierto, y el mecanismo es el peor de todos: no falla nada.** La API de
1816 lleva un parámetro `moneda` cuyo default es `ars`, y su spec dice que con
`ars` *«para instrumentos pagaderos en moneda distinta a ARS, para calcular
indicadores las cotizaciones se dividen por CCL»*. Toda esta plataforma divide
por MEP (`engines/curvas.py::precio_soberano_a_usd`). Omitir el parámetro no
devuelve un error ni un vacío: devuelve **un número plausible al dólar
equivocado**.

**Medido** (`scripts/diag_1816_moneda_series --pedir`, misma rueda, mismos
campos, mismos tickers):

| | TEA `ars` (=CCL) | TEA `mep` | Δ | Paridad `ars` | Paridad `mep` |
|---|---|---|---|---|---|
| BPOA7 | 6,898% | 2,498% | **−440 bps** | 98,51% | 102,16% |
| BPOA8 | 9,186% | 7,059% | **−213 bps** | 88,67% | 92,35% |
| BPOB7 | 7,255% | 2,441% | **−481 bps** | 98,22% | 102,20% |

Y `precioClean` en `ars` viene en PESOS (BPOA7: 157.089,89 contra 102,19), o sea
que el cuadrante COMPARAR sobre "Precio" mezclaba escalas de tres órdenes de
magnitud sin que se entendiera por qué.

**Lo que convierte esto en una entrada del diario y no en un fix.** El bug ya
había aparecido antes, en otro pipeline: los **202 bps de GD46** del simulador de
alta (§0.y) eran esto mismo, y ahí se arregló *en ese pipeline*
(`alta.moneda_cotejo_1816`). Nadie fue a mirar los demás. Al buscarlo esta vez
aparecieron **tres lugares distintos** pidiéndole a 1816 sin declarar la moneda:

| Dónde | Qué alimenta | Se veía en |
|---|---|---|
| `jobs/mercado_1816_series` | `research.mkt_1816_series` | los gráficos de Research |
| `agente/tasa_1816` | `agente.tasa_1816` | **la TEA que ve la mesa en Renta Fija** |
| `core/tamar_1816_sql.sembrar_desde_1816` | `mercado.tamar_1816` | la TEA y el margen de cada pata |

El segundo es el que más duele: esas tasas van **derecho a la vista sin que
nadie las convierta** (`curvas_vista`), o sea que un bono en dólares mostraba su
tasa al CCL al lado de las que el motor calcula al MEP.

**La corrección tiene tres capas, y la del medio es la que evita repetir.**

1. **La regla, en un solo lugar**: `core.mercado_1816.moneda_series(moneda_pago)`
   → `mep` si paga en dólares, `ars` si no; y `monedas_de()` / `por_moneda()`
   que la aplican sobre el catálogo. Todos los consumidores la usan. ⚠️ El
   predicado es **`monedaPago`, nunca `monedaDenom`**: un dólar-linked está
   denominado en USD y paga en pesos, y 1816 ni publica `mep` para ellos (D30O6
   devolvió todo `None`).
2. **Cada dato guardado DICE a qué dólar se pidió.** Se agregó la columna
   `moneda` a `mercado.tamar_1816` y a `agente.tasa_1816` (`research.
   mkt_1816_series` ya la tenía, en la PK). Sin eso la pregunta «¿esto está al
   CCL?» no se podía hacer sobre el dato: había que ir a leer el job.
3. **La habilidad `hd_1816_al_ccl`**, que mira las TRES tablas juntas. Pedido
   textual del user: *«esto abarca para TODO lo que venga de 1816 y sea HARD
   DOLAR»*. Por eso el detector no pregunta por una vista: pregunta por todo lo
   que 1816 dejó escrito, sea cual sea el job que lo trajo — **un pipeline nuevo
   que se olvide de la moneda cae ahí solo**, sin que nadie se acuerde de
   agregarlo a una lista.

**Sujeto = la TABLA, no el bono.** Se rompen los 21 hard dollar juntos o
ninguno; 21 filas en AHORA diciendo lo mismo es ruido. Los tickers viajan en
`evidencia["items"]` para que la reincidencia se cuente por bono (§0.cz).
`naturaleza = RECURRENTE`: si aparece dos meses seguidos no es un incidente, es
que el escritor sigue sin derivar la moneda (§6.10).

**Sin arreglo, a propósito.** Rehacer una serie cuesta créditos de 1816 y es un
backfill — REGLA #4, eso no sale de un botón del tablero. Y no hace falta: se
cierra solo, porque corregido el pedido el cron reescribe las filas.

**La trampa del detector, que me comí escribiéndolo.** La primera versión
juzgaba FILA POR FILA: «hay una fila en `ars` para un bono que paga en dólares →
hallazgo». Pero un hard dollar ya rebajado tiene **las dos** series, y la vieja
en `ars` se conserva a propósito. O sea que el detector iba a cantar todos los
días, para siempre, sobre algo que está bien — la forma más rápida de que se
deje de mirar el tablero. Lo que está mal no es que SOBRE una moneda: es que
FALTE la que corresponde. Se junta por `(tabla, ticker)` antes de juzgar y el
predicado es `"mep" not in monedas`. Congelado por test con AE38 en las dos.

**La segunda regla, la que casi no escribo.** El detector también canta los
tickers que **no están en el catálogo de 1816**: de esos no se puede DERIVAR en
qué moneda pagan, se piden con el default `ars`, y si alguno paga en dólares
queda al CCL sin que se note. Es exactamente la puerta por la que el problema
volvería.

**Alcance del rebajado**: 21 de 82 bonos del watch pagan en dólares (6
BOPREALes, 9 Bonares, 6 Globales). El backfill se corrió el 2026-09-09 con
`--moneda mep --desde 2025-09-10` y el diag confirmó **21 de 21 en MEP**. Las
filas viejas en `ars` no se borran: son el único registro del tramo que la API
ya no deja rebajar (topea en 1 año) y la lectura las ignora. Detalle del lado de
Research en `docs/RESEARCH.md` §A.4.7b.

---

### 0.fa DIEZ TABLAS «NO ESCRIBEN HACE 1,5 DÍAS» Y TODAS ESCRIBÍAN — la memoria que no olvidaba y el viaje todo-o-nada (2026-09-11)

El user, con AHORA abierto un viernes a las 09:19: diez tablas de cierre
(`cedears_ohlc_daily`, `eikon_cierres`, `fair_value_residuos`, `bonos_ohlc_daily`,
`snapshots_cierre_hist`, …) marcadas `tabla_quieta` con «no escribe hace 1,5
días y su cron dice cada 1,0 días», todas «crónico, 4-5× en 30 d». *«El mercado
cierra a las 17. No entiendo qué toma para decir que no escribe hace X días si
en realidad sí escribe. Es el mismo patrón que pasa con casi todas.»*

**Medido con `scripts/diag_tabla_quieta --tabla mercado.cedears_ohlc_daily --vivo`:**

- El job corrió las ocho últimas veces, `ok`, 17:15 ART, con cedears escritos.
  A las 10:01 `max(fecha)` decía **10/09**. El agente a las 09:19 decía 09/09.
- **La lectura en vivo fallaba en TODAS las pasadas, en 0,03 s**: `relation
  "estrategia.resultados" does not exist`. Una tabla borrada de la base que
  `manager.tabla_perfil` seguía recordando — el barrido hace upsert y **nunca
  borraba** — y como la lectura viva era UNA query `UNION ALL` para las 72,
  un nombre muerto la tumbaba entera. `journalctl` mostró el warning ocho
  veces entre las 08:52 y las 12:49.
- Sin dato vivo, el detector caía a la FOTO «y lo decía» (`ultimo_vivo: false`
  en la evidencia). La foto se saca al cerrar la rueda, **17:15, el mismo
  minuto en que corren los jobs de cierre**: siempre dice «ayer». Ayer +
  36 h de tope = **hoy a las 09:00**. Por eso las cuatro veces cayeron a las
  09:19/09:20: la primera pasada después de las 09:00.
- Y el «crónico 4×» eran **tres bugs distintos del detector**, ninguno del
  job: 28/08 la fecha de negocio (§0.em), 31/08 el finde a medias
  (`_segundos_de_finde`), 05/09 y 11/09 esto.

**Qué se cambió (código, no IA — el user lo discutió y tiene razón: «no sería
mejor que no pase nunca el NO PUDE MIRAR?». Lo nuestro se previene; lo del
mundo se declara):**

1. **El barrido olvida** (`tablas.barrer`): lo que ya no está en el catálogo se
   borra del perfil. Una memoria que nunca olvida es un bug.
2. **La lectura viva no es todo o nada** (`tablas._ultimo_dato_vivo`): si el
   viaje único falla, se lee tabla por tabla y devuelve `(leídas, fallidas)`.
   72 viajes sólo en las pasadas en que algo está roto.
3. **Sin dato vivo no se juzga** (`sistema.tabla_quieta`): la foto dice qué
   tablas hay y cada cuánto escriben; el atraso se mide contra la tabla ahora
   o no se mide. Si no se pudo leer ninguna → `SinDatos`. Si no se pudo leer
   una → **`tipos.NoMirado`**, que es nuevo: viaja mezclado con los
   `Hallazgo`, `registro.guardar` lo separa, cuenta como visto para el cierre
   por ausencia (lo que esa tabla tuviera abierto sigue abierto) y como nada
   para el resto. La corrida queda `ok` y `ultimo_error` dice «no pude mirar
   1: …», que es lo que HABILIDADES muestra. Es el invariante 1 de a uno.

`ultimo_vivo` desaparece de la evidencia: ya no hay veredicto sin dato vivo.
El desfase de la hora del barrido contra los jobs de cierre queda como está:
con la foto fuera del veredicto, no decide nada.

---

### 0.fb LA PLANILLA ANTES QUE LA TARJETA — `tabla_quieta` mira las corridas del job, para todas (2026-09-11)

Cerrado §0.fa, el user: *«no quiero soluciones pensadas en tablas puntuales,
quiero soluciones generales que sirvan para toda tabla_quieta»*. La lista de
crónicos seguía llena de `tabla_quieta` con otra causa: `research.bcra_series`
(8 episodios), `research.fred_observations` (11), `bancos.sync_log` (12) — el
caso de §0.ew: sello de ALTA, job incremental, la fuente no publicó, el job
corrió perfecto y la tarjeta manda «relanzar».

**Lo que se descartó, y por qué.** La primera idea fue un CONTRATO: que todo
job anote «filas escritas» con un nombre único, contado en la puerta de
escritura (`core/pg_mirror`) sin tocar cada job. **Medido**: 27 de los jobs
escriben con `cursor.execute` directo, por fuera de `pg_mirror` —justo los de
estas tablas (`eikon_cierres`, `mercado_1816_series`, `fred_research`,
`bcra_research`, `mayor_sync`)—, así que un contador en la puerta no sería
general, y uno por job es lo que el user no quiere. Los `stats` de hoy son
25 nombres distintos para lo mismo (`escritos`, `filas`, `upserted`, `docs`…).

**Lo que sí es general y ya existe**: `manager.job_runs` tiene, para todo job
con `JobRunLogger` (todos menos 7), CUÁNDO corrió y CÓMO terminó. Con eso se
separan las tres causas sin declarar nada por tabla:

| La planilla dice | `tabla_quieta` hace |
|---|---|
| no hay corridas (motor, o job sin logger) | canta como siempre (`sin_escribir`) |
| la última corrida no es `ok` | **calla**: «no corrió» / «falló» lo canta `salud` para todo el crontab (REGLA #9: un hecho, un lugar) |
| corrió `ok` después del último dato, menos de N veces | **calla**: la fuente no publicó; relanzar no cambia nada |
| corrió N o más veces después del último dato (ok o no) y la tabla no avanzó | `corre_ok_sin_avanzar`: o la fuente lleva días muda, o escribe y la columna no se mueve; el `que_hacer` dice cómo separarlas. Se cuentan todas las posteriores, no sólo las ok seguidas: un job que falla de a ratos nunca juntaría N ok, y si `salud` no confirma cada falla suelta nadie la cantaría |

N = `corridas_ok_sin_avanzar` (4, en el catálogo; editable por base). «Después
del último dato» se mide con `started_at` y contra el día CERRADO cuando la
columna es fecha de negocio: la corrida que escribió el dato termina segundos
después del sello que dejó y contaría como posterior sin serlo.

**Lo que queda afuera, dicho**: un job que corre `ok` y escribe cero para
siempre (watermark roto) se ve recién a las N corridas, como
`corre_ok_sin_avanzar`, no antes; y una tabla sin job de reloj sigue igual que
ayer. El «sello de escritura universal» —leer `pg_stat_user_tables.n_tup_ins/upd`
por pasada para saber cuándo se ESCRIBIÓ cada tabla, sin depender de columnas—
resolvería también la cota de fecha de negocio (§0.em) y queda propuesto, no
hecho.

### 0.fc EL ESCRITOR QUE ESTABA A LA VISTA — un `[a-z_]+` y una constante dejaban 12 tablas exigidas a ciegas (2026-09-11)

Seguido de §0.fb, el user, sobre los mismos avisos de `tabla_quieta`: *«este
tipo de tablas quietas en realidad está MAL DEFINIDO. La gracia es para las que
se llenan con procesos automáticos, no las de inputs manuales por usuarios»*.

Tenía razón, y la distinción **ya existía**: `core/escribe.la_dispara()` separa
`reloj` (cron o motor → se le exige) de `evento` (una persona, una request → se
saltea), y el detector ya hace `continue` sobre las de evento. El problema no era
el diseño: era el **tercer** valor. `no_se` sigue exigiendo a propósito («dejar de
mirar algo que no entendimos es cómo se pierde una señal de verdad»), y nadie
había medido cuántas caían ahí.

**Medido** (`scripts/diag_quien_escribe`, sobre el universo real del detector):
**12 de 65**. Y las 12 se explican por dos fallas mecánicas del escaneo, las dos
en silencio — el `INSERT` estaba a la vista en todos los casos:

| | tablas | la falla |
|---|---|---|
| `([a-z_]+)` como schema | 4 (`ap5.*`) | `ap5` tiene un dígito: el patrón leía `ap`, pedía un punto, encontraba un `5` y se rendía. `ap5` es el único de los 16 schemas declarados con un dígito |
| el nombre vive en una constante | 8 | `_TABLA_CHEQUES = "operaciones.tesoreria_cheques"` arriba y `f"INSERT INTO {_TABLA_CHEQUES} (…)"` abajo — el literal que el patrón busca está partido en dos lugares y no existe en ninguno. También con las puertas de `pg_mirror`: `write_native(TABLE, …)`. Y con el nombre **sin schema** (`_UI_JOBS_TABLE = "aranceles_job_runs"`) |

**El arreglo son dos cosas y NO tienen el mismo riesgo**, por eso se dicen por
separado: sumar `0-9` al schema **no silencia nada** (las cuatro de `ap5` son de
RELOJ: lo que estaba roto era el `que_hacer`, que mandaba a «relanzar el job que
la escribe» sin poder nombrarlo, teniendo `jobs.ap5_portfolio` a la vista).
Resolver las constantes **sí silencia**, y es lo correcto: siete de las ocho se
escriben desde `api/` o `core/` —las seis de tesorería y `mercado.eikon_snapshot`—
o sea que son de EVENTO, y a **tablas de carga manual se les venía exigiendo el
ritmo de un feed live**. Las dos que más cantaban (`tesoreria_cheques`,
`tesoreria_banco_a_banco`, «es tiempo real (cada 10 s)») eran de ahí.

**La guarda de la parte que silencia**: una constante sólo se resuelve si su
valor es una tabla que `sql/schema.sql` declara (`schema_sql.donde_vive`, que ya
descarta además los nombres cortos ambiguos). Sin eso, un `f"… {_COLS_CHEQUE}"`
—una lista de columnas— le adjudicaría un escritor a una tabla inventada, y un
escritor FALSO es peor que ninguno: manda a relanzar, con seguridad, algo que no
existe. Es el mismo default seguro de `_resolver`.

**El efecto, medido antes y después sobre las 216 tablas de `sql/schema.sql`**:
`no_se` baja de 55 a 25, y **ninguna tabla pasó de `reloj` a otra cosa** — que
era el único cambio peligroso posible.

**Lo que NO se tocó, dicho**: las dos de `agente/` que quedan en `no_se`
(`agente.hallazgos`, `manager.tabla_perfil`). Su `INSERT` es literal y está a la
vista, pero `_mapa()` sólo escanea `jobs/ engines/ core/ api/`, y la clase de
`agente/` es MIXTA (el daemon escribe `hallazgos` en cada pasada; `vista.py` y
`arreglos.py` escriben cuando alguien aprieta). Hoy se las exige y **está bien
que se las exija**; lo único pobre es el texto. Declararlas es una decisión, no
un arreglo, y no se tomó acá.

**De yapa, sin arreglar**: `mercado.agro_pizarra_audit` está en el mapa de
escritores y no en `sql/schema.sql` — la crea en runtime
`api/services/derivados_agro.py` con un `CREATE TABLE IF NOT EXISTS`. Es anterior
a esto y no lo rompe nada; queda anotado porque un test que exija «toda tabla del
mapa está en el schema» fallaría por ella, y el que se escribió prueba la guarda
en vez de esa afirmación más fuerte y falsa.
### 0.fd LA TABLA QUE NO SABÍA CUÁNDO LA ESCRIBIERON — `mkt_1816_watch` y el sello que faltaba (2026-09-11)

La tarjeta, con el cruce de §0.fb ya puesto: *«`jobs.mercado_1816_discovery`
corrió 6 veces desde el último dato (6 ok) y `agregado_en` no avanzó · no escribe
hace 8,2 días y su cron dice cada 1,0 días como mucho»*. El job estaba perfecto.

`research.mkt_1816_watch` es una lista de **MEMBRESÍA** —qué 82 tickers se le
piden a 1816 todos los días—, y su única columna temporal era `agregado_en`, que
el upsert diario **no toca**: su `DEFAULT now()` sólo corre en el `INSERT`, así
que guarda **cuándo entró ESE ticker al universo**. Ocho días sin moverse
significa «hace ocho días que no se licita un bono nuevo», que es un dato normal.
El detector la elegía por descarte —no había otra columna— y leía una fecha de
alta como si fuera un sello de escritura.

**La prueba de que era un olvido y no un diseño**: la tabla hermana
`mkt_1816_instrumentos`, escrita **en la misma función tres líneas más arriba**,
ya cierra su `DO UPDATE` con `actualizado_en=now()`. Al watch le faltaba la
columna, no la idea.

**El arreglo NO es clasificar `agregado_en` como sello de ALTA**, y esto es lo
que hay que entender antes de repetirlo en otra tabla: `tablas._SELLO_ALTA` sólo
baja la PRIORIDAD al elegir columna (`_elegir_col`, paso 4). Siendo la única
temporal, se elegía igual — y peor: dejar la tabla sin ninguna columna medible la
volvería invisible, que es el cambio malo de siempre (un aviso falso se ve; una
tabla que nunca avisa, no). Se agrega `actualizado_en` al DDL (con su `ALTER …
ADD COLUMN IF NOT EXISTS`, porque `apply_schema` no recrea lo que existe) y el
upsert lo pisa. **No silencia: enciende** — desde ahora la tabla se monitorea de
verdad, y si el job muere el aviso sale con motivo.

#### Medido contra las otras tres de §0.ew, porque el arreglo NO se copia

| tabla | cómo escribe | ¿el job deja sello al correr ok? | ¿aplica esto? |
|---|---|---|---|
| `research.mkt_1816_watch` | upsert de las 82 filas, todos los días | **no** — no había columna | ✅ exacto |
| `research.bcra_series` | upsert con ventana de 7 días hacia atrás (`_VENTANA_INCREMENTAL_D`) | ya hace `ingestado_en = now()` en su `DO UPDATE` | ❌ ya lo tiene |
| `research.fred_observations` | upsert con colchón por frecuencia | ídem | ❌ ya lo tiene |
| `ia.research` | append puro, `ON CONFLICT (message_id) DO NOTHING` | **no, y no se puede**: sin mail nuevo no se escribe NINGUNA fila | ❌ no hay qué sellar |

⚠️ **Y queda una contradicción abierta, dicha y no resuelta.** §0.ew afirma que
en `bcra_series` y `fred_observations` *«si el BCRA/FRED no publicó un día nuevo,
mandan CERO filas y el sello no se mueve»*. **El código de hoy dice lo
contrario**: los dos re-piden una ventana hacia atrás, esos puntos existen, el
upsert corre y `ingestado_en = now()` se mueve en cada corrida ok. Y la ventana
no es posterior a la entrada: `_VENTANA_INCREMENTAL_D` entró a las 15:19 del
2026-09-08 y §0.ew a las 20:08 del mismo día. O la afirmación del diario quedó
imprecisa, o la API devuelve vacío para la ventana re-pedida. **Es un hecho de
prod y no se resuelve leyendo**: se mide con `scripts/diag_tabla_quieta.py`
—`max(ingestado_en)` contra las últimas corridas del job— antes de tocar nada
de esas dos.

---

### 0.fe «ESPERANDO QUE EL DETECTOR CONFIRME» SOBRE ALGO QUE EL DETECTOR YA CONTESTÓ (2026-09-11)

El user, mirando ENCONTRÓ: *«grisado lo de completar ficha… no entiendo
realmente cómo funciona lo de APLICADO · ESPERANDO QUE EL DETECTOR CONFIRME,
qué es lo que tiene que esperar, cuánto hay que esperar»*. Tres filas de
`ficha_incompleta`, las tres con «confirmado 11/9 02:22 p.m.»:

| Fila | Estado | Botón |
|---|---|---|
| CARTERA · 4 títulos · desde 9/9 | `en_curso` → «esperando que el detector confirme» | **gris** |
| EMISOR · 2 títulos · desde 11/9 | `nuevo` | apretable |
| CLASE_ACTIVO · 170 títulos · desde 27/8 | `en_curso` → «esperando que el detector confirme» | **gris** |

**No había nada que esperar, y el cartel no era el único problema: el botón
gris era el que hacía falta apretar.**

**Qué pasaba, en orden.** `ficha_incompleta` declara `automatico` para
`sin_cartera` y `sin_clase_activo` (§0.ei, §0.ej) y NO para `sin_emisor`. El
ejecutor (§0.ef) aplica lo determinístico solo, entra por la misma puerta
(`arreglos.aplicar`), y esa puerta deja el hallazgo **`en_curso`** — que es
correcto: la escritura no prueba que el problema se fue, eso lo dice el
detector. Pero el sujeto de `completar_ficha` es un CAMPO ENTERO: el detector
vuelve (cada hora, y encima en el acto por `confirma_ya`, §0.ee), ve que
**quedan otros** títulos, y `registro._ver` lo mantiene abierto. `en_curso` no
se mueve nunca. Por eso las dos filas con regla automática estaban en gris y la
del emisor no: **el que trabaja solo es el que se bloqueaba la mano.**

Y el ciclo no era «esperar N minutos»: es una CONDICIÓN —que el campo llegue a
cero faltantes— que para una cola recurrente (§0.ek) puede no cumplirse nunca.

**La causa de fondo: un ESTADO no puede contestar una pregunta sobre el
TIEMPO.** `en_curso` vale lo mismo para «se aplicó hace diez segundos y el
detector no volvió» que para «se aplicó hace dos semanas y el detector contestó
340 veces». Son opuestos: uno es paciencia, el otro es trabajo. Es el mismo
malentendido que HISTORIAL ya había arreglado el 2026-08-28 (*«¿confirmación de
qué?? si yo ya lo apliqué»*) con `estado_hoy`, en la única pantalla que nadie
había mirado.

**Qué se cambió.**

1. **`agente.hallazgos.arreglo_aplicado_at`** — la hora, en el MISMO `UPDATE`
   que pone `en_curso`. En dos UPDATEs habría un instante con estado y sin
   hora, que se lee como «no sé».
2. **`v_encontro` resuelve `espera_al_detector`**: `en_curso` Y hay hora Y
   `visto_ultima_vez <= arreglo_aplicado_at`. Una sola definición, en la misma
   vista que dibuja la tarjeta (el navegador no compara fechas — invariante
   11). NULL no es espera: esas filas se aplicaron antes de la columna, o sea
   hace mucho.
3. **El cartel dice cuál de las dos cosas es**: «esperando que el detector
   confirme» solo mientras la espera exista; si no, «aplicado {hora} · el
   detector ya volvió y sigue abierto», en gris apagado.
4. **El botón dejó de ser decorativo.** Un arreglo con `pide_datos` NO escribe
   desde ese botón —la escritura sale del listado— así que apretarlo devolvía
   «no se cargó ningún valor»: era un botón que no podía funcionar nunca. Ahora
   el click ABRE EL LISTADO (`arreglo_pide_datos` viaja resuelto del backend) y
   el gris se reserva para lo único que lo justifica: que el efecto todavía no
   se pueda medir (relanzar dos veces un job de ocho minutos).
5. **El aviso de `aplicar` no promete una pasada que ya ocurrió.** La pantalla
   muestra `aviso` antes que `detalle`, así que «aplicado — el efecto lo
   confirma el detector en su próxima pasada» tapaba «5 completado(s) · quedan
   374», que es la única frase que contesta «¿y ahora qué queda?». Con
   `confirma_ya` el aviso ya no viaja.
6. **`diag_agente` tiene sección «LO QUE ESTÁ EN CURSO»**: por hallazgo, quién
   aplicó (persona o el ejecutor), cuántas escrituras dejó, cuándo se aplicó,
   cuándo miró el detector, y el veredicto leído de la vista — no recalculado.

**Lo que NO se cambió, y por qué.** El estado sigue siendo `en_curso` y el
cierre sigue siendo del detector: eso es el invariante 4 (solo lo cerrado POR
ACCIÓN puede reincidir) y no se toca. `repetible` tampoco cambió de dueño —
`alta_on` y `alta_cedear` son familias y lo declaran `False`, lo que solo
afecta la reinsistencia del ejecutor a 24 h; queda anotado como pregunta, no
como arreglo, porque cambiarlo cambia lo que el agente hace solo.

### 0.ff EL INVESTIGADOR: SE APAGÓ, Y UNA SEMANA DESPUÉS SE BORRÓ ENTERO (2026-09-11)

No es un bug: es una decisión del user, en dos pasos, y el motivo es de método.

**Paso 1 — dejó de dispararse solo.** Repasando qué consume tokens sin que nadie
apriete, el conteo dio esto: de las **30 habilidades del catálogo, UNA**
declaraba `investigar` (`proveedor_caido · no_responde`, espera 20'). Y de los
**cuatro** tipos de investigación del lab, uno era `libre` — *«una pregunta
suelta, **sin método declarado**»*. El user: *«todo esto de agentes lo hice por
hacer, no pensé (…) con casos de uso que la verdad tampoco eran reales»*.

O sea: **el investigador se conectó a producción antes de que existiera un
trabajo esperándolo del otro lado.** Es la regla que ya está escrita arriba de
`core/ai.py` —*«¿QUIÉN MIRA SU SALIDA?»*, aprendida cuando murieron el copiloto,
el MCP y el destilado del research— aplicada a una feature que había entrado por
otra puerta y se la salteó.

**Paso 2 — se borró.** Apagado el disparo, quedó una máquina de 2.520 líneas que
sólo se movía si alguien apretaba un botón que nadie apretaba. El user: *«ya ni
me acuerdo para qué se usaba (…) borremos todo, claramente no sirvió»*. Se fueron
`lab/`, `sql/lab.sql`, `agente/triage.py`, `scripts/eval_investigador.py`, la
tarea `investigador` y `registrar()` de `core/ai.py`, el campo `investigar` de
`agente/tipos.py`, los tres endpoints `/api/agente/lab/*` y las tres
dependencias de LangChain/LangGraph — la única deuda de framework que tenía el
repo del lado de LLM.

**Qué ocupa su lugar**: la tab LAB pasa a ser el **asistente conversacional**
(`asistente/`), escrito a mano sobre `core/ai.py`, con herramientas que leen
datos del negocio y un alcance de cuentas declarado fuera del código. La
diferencia no es de tecnología: el asistente tiene un usuario y una pregunta
real del otro lado antes de existir, que es lo que al investigador le faltaba.

**Lo que NO se hizo, y se dice**: nunca se corrió `scripts/eval_investigador.py`,
así que **no hay número sobre si el investigador acertaba**. Se borró sin esa
medición, a sabiendas: el costo de mantener apagado algo que nadie mira es mayor
que el valor de un dato que nadie iba a ir a buscar.

---

### 0.fg LOS 170 SIN CLASE: DOS COSAS MECÁNICAS TAPABAN EL 84%, Y EL GUION NO DECÍA CUÁL (2026-09-11)

El user, mirando el listado de `clase_activo`: *«no entiendo por qué CLASE
ACTIVO no está sugiriendo… hay un montón de FCI y bonos que no entiendo por qué
no sugiere»*. Se midió con `scripts/diag_clase_activo` sobre los 170 reales:

| | N | Qué era |
|---|---:|---|
| se proponían | **0** | — |
| la regla SABE y el valor no existe en `clase_activo` | 69 | 7 valores de agro (`OTC SOJA`, `FUTUROS DE MAIZ`, …) sin cargar |
| FCI · el nombre no matchea ninguna ficha de Primary | 73 | **el bug** |
| FCI · `Renta Mixta` / `Retorno Total` | 11 | no hay clase para eso: lo decide la mesa |
| sin CARTERA | 4 | las cinco reglas arrancan por la cartera |
| bonos ARS | 9 | cinco razones, y **dos no son de este campo** |
| cartera `OTROS` | 2 | uno está mal carteado (`OTC - MAI.ROS/JUL26`) |
| DERIVADOS sin regla | 2 | una opción de acción local y `WTI092026` |

**El bug de los FCI: el nombre como IDENTIDAD.** La regla emparejaba el
`ticker` del asset con el `simbolo` de la ficha de Primary usando
`normalizar_nombre` (upper + sin acentos + espacios). Un fondo llega con TRES
grafías del mismo nombre —`FCI IAM Ahorro Pesos - Clase B` en el asset,
` IAM Ahorro Pesos - Clase B` (con espacio inicial) o `ADBAICA AR` en Primary—
y por eso el dominio FCI tiene su propio normalizador desde el principio
(`core/fci_match.normalizar`: saca «FCI»/«F.C.I.», el `[1047]` de Aunesa, el
código `CAFCI643-1024` y la puntuación). **Dos normalizadores para la misma
pregunta** (REGLA #9), y el débil dejaba la columna vacía sin fallar.

**Y la respuesta ya estaba en la base.** De los 84, **77 estaban linkeados en
`mercado.fci` por `unidad`** —la misma clave con la que escribe
`CompletarFicha`, y un link que la mesa confirmó en Manager—, 42 con el
`tipo_renta` de Primary y 23 con la `categoria` **ya calculada** por
`jobs/fci_universo` con `de_fci`, la misma función. `core/fci_match.py` lo
decía en su encabezado: *«el link es por símbolo, no por texto»*.

**Qué se cambió.**

1. **Fuente nueva `FCI`, y va PRIMERO**: `agente.fuentes.fci_por_unidad()` trae
   `{unidad: fila de mercado.fci}` y la clase sale de `tipo_renta` + `moneda`
   por `de_fci`. El match por nombre queda como respaldo para los que no están
   linkeados, ahora con `fci_match.normalizar` — o sea que el normalizador
   débil desapareció de esta pregunta. Entra a `deterministas`: no es una
   inferencia, es un link confirmado leído con la función del job.
2. **NO se toma prestada `mercado.fci.categoria`.** Es el estante del INFORME y
   la mesa manda ahí: puede decir «T+0 MONEY MARKET», que no es vocabulario de
   `clase_activo`. Tomarla haría que la lista cerrada la rechace y la nota mande
   a «cargar ese valor a mano» — un consejo equivocado sobre otro campo.
3. **Una clave de fondo ambigua no propone nada.** El índice de fichas usaba
   «el primero gana», estable pero ARBITRARIO; acá el resultado se ESCRIBE en la
   ficha de un título, así que con dos fichas de distinta clase bajo la misma
   clave se descarta la clave. Un campo vacío se ve; una clase ajena se suma.
4. **TODA fila sin propuesta dice POR QUÉ** (`nota`, congelado por test). Era el
   defecto transversal: la nota se llenaba en UN caso —la lista cerrada rechazó
   el valor— y en los otros 101 la fila viajaba con los tres campos vacíos. En
   pantalla es un guion, y abajo del mismo guion convivían «no hay nada que
   proponer» (correcto, por diseño) y «la regla no encontró su fuente» (un bug).
   El front ya dibujaba `nota`: no hizo falta tocarlo.
5. **`core/clase_activo` expone `contrato_de` y `es_opcion`** (y `CARTERA_*`
   dejan de ser privadas): `de_futuro` DECIDE con ellas y la explicación EXPLICA
   con ellas. Con un parseo propio del lado de la nota, «el prefijo WTI no tiene
   producto» y «esto no parsea» podían mentir por separado.
6. **`diag_clase_activo` dejó de clasificar**: agrupa por `nota`, o sea por lo
   que la mesa va a leer en pantalla. Su primera versión tenía su propio
   clasificador de causas — funcionaba, y era la segunda definición del mismo
   criterio esperando a divergir.

**Dos hallazgos que aparecieron de costado y NO son de `clase_activo`.** Los
bonos ARS los destapó la nota, y ninguno tenía quién lo reportara:

- **`NZC30` y `DHSOO` están en `mercado.curvas` con `moneda_eje` vacío.** Un
  bono sin ejes **no cae en ninguna curva** (`core/curvas_sql`: *«desaparece de
  todo lo que llame acá»*): son títulos en cartera de cliente invisibles en las
  vistas de renta fija, no sólo sin clase.
- **`TZVD8` y `D15E7`: la ficha dice cartera `ARS` y su curva dice
  `moneda_eje = USD`.** Dos fuentes afirmando cosas distintas del mismo bono, sin
  árbitro (REGLA #9, el caso de `core/duplicados`).
- Y un efecto cruzado del silencio: `TO26` y `TY30P` están en
  `agente.silenciados` por `no_esta_en_curvas`, así que **nunca van a entrar al
  master y por lo tanto nunca van a poder recibir clase por curva**. Silenciar
  un aviso apagó, sin que nadie lo decidiera, la única regla que podía
  completarles la ficha.

**Medido después del deploy: 170 → 101** (los 69 del agro los completó la mesa
cargando los 7 valores; la lista cerrada pasó de 18 a 25) **y 23 FCI quedaron
propuestos por el link**, que el ejecutor escribe solo. El dato que cierra el
caso: **el match por nombre aportó CERO** — 12 filas matchean una ficha y
ninguna produjo propuesta, así que el nombre no sólo fallaba: nunca hizo falta.

De los 61 FCI que siguen, **35 estaban «linkeados pero sin `tipo_renta`»**, que
se lee como un bug del link y no lo es: `fci_universo._assets` linkea la fila a
la de Primary sólo si el asset tiene `instrumento` y Primary lo lista; si no, le
crea una **fila propia** (`origen = 'asset'`, `simbolo_primary = NULL`) con la
moneda y nada más. Sin tipo de renta no hay clase que derivar, y el arreglo no
es del agente: es cargar `instrumento` en Manager (el job ya intenta el match
por nombre solo y cuenta los ambiguos en sus stats) o, si el fondo es bilateral,
que la clase la ponga la mesa. La nota ahora dice cuál de las dos es. Los otros
26: 19 son `Renta Mixta`/`Retorno Total` —no hay clase declarada para eso y el
propio job borró «RENTA MIXTA» de su vocabulario por ser inventado, así que es
una decisión de la mesa— y 7 no tienen link.

Los tres quedan ANOTADOS y sin arreglo: cada uno es un aviso que hoy no existe,
y decidir si son habilidades nuevas es una decisión de la mesa, no un
refactor. Lo mismo el vocabulario de `WTI` y de las opciones de acciones
locales (`GFGV6600OC`), que tienen otra convención de nombre y ninguna regla.

---

### 0.fh EL BOTÓN DE CARTERA NO PODÍA CONTESTAR NUNCA — 72 segundos de censo contra 30 de proxy (2026-09-11)

El user: *«el tema es que CARTERA no sé por qué no funciona: queda en
«trabajando…» desde hoy, nunca lo pude abrir, a diferencia de los otros»*.

**No era lentitud ni un flake: era aritmética.** `CompletarFicha.preview` es el
único preview que le pedía el universo a 1816, y ese universo es un CENSO:

```
mercado_1816.censar()      recorre /curvas y pide /instrumentos por cada una
                           → 1 + 28 = ~29 requests SECUENCIALES
_MIN_INTERVALO_S = 2.5     throttle OBLIGATORIO entre llamadas, con lock
                           → 29 × 2,5 = 72,5 s de PISO, sin contar red
maxDuration = 30           el proxy de Next corta acá
```

**72,5 > 30.** No hay corrida con suerte. Los otros dos campos andaban porque no
tocan 1816: `clase_activo` es todo SQL local y `emisor` hace a lo sumo una
llamada a Finnhub/modelo con tope declarado.

**Por qué «trabajando…» y no un error.** `datos.tsx` le pone `conTecho(25 s)` a
`leer` y a `releer`, y no a `calcular`. La excepción estaba escrita para las
ESCRITURAS —*«abortar un POST no deshace lo que el backend ya escribió»*, que es
cierto— y se le aplicó a un POST que **no muta**, que ese mismo archivo define
como «POST que CALCULA (preview)». Sin techo, el navegador no le pone timeout a
`fetch`: la promesa **no se resuelve ni se rechaza**, así que el
`finally { setOcupado(null) }` nunca corre y los tres botones de la fila quedan
`disabled` hasta recargar la pestaña. Es la tercera vez que este repo aprende lo
mismo (§0.dm fue en los polls), ahora en el único verbo que había quedado afuera.

**Y cada click lo empeoraba**: `fuentes._una_vez` no es thread-safe, así que cada
intento lanzaba OTRO censo completo, y `_throttle` los serializa a todos detrás
del mismo lock — el intento N esperaba a los N−1 anteriores.

**Las tres capas del arreglo, en orden de cuánto importan:**

1. **Lo interactivo lee el CATÁLOGO, no el censo** (`fuentes.universo_1816_local`):
   `research.mkt_1816_instrumentos`, una consulta, sin créditos y sin red. El
   censo vivo queda para los detectores, que corren en el daemon. El preview
   pasó de imposible a milisegundos. **La misma fuente la usa el ejecutor**
   (`CompletarFicha.solo`): con dos universos distintos, el agente podría
   escribir solo una cartera que la pantalla nunca ofreció (REGLA #9).
2. **La puerta declara su paciencia**: `arreglos.preview` abre un
   `mercado_1816.presupuesto(20 s)`. Es el mecanismo que `agente/alta.py::_interactivo`
   ya usaba para sus puertas, aplicado a la que faltaba — si algún preview
   futuro pide algo caro, contesta **«se agotó el tiempo disponible para
   consultar a 1816»** en vez de morir callado.
3. **`calcular` lleva techo** (25 s): un cálculo que se cuelga tiene que decirlo.

**Dos cosas que aparecieron en el camino y no eran esto:**

- ⚠️ **La «degradación honesta al catálogo» nunca ocurrió una sola vez.**
  `universo_1816` prometía caerse al catálogo persistido cuando la API no
  contesta, y esa lectura pedía `SELECT ticker, curva, **data**` — y `data` **no
  es una columna** de `research.mkt_1816_instrumentos` (el discovery escribe
  columnas tipadas). La única salida posible era `column "data" does not exist`,
  y no fallaba nada porque `_una_vez` captura y cachea `None`: el fallback vivía
  en el docstring y no en la realidad. Congelado por test (la query se compara
  contra el DDL).
- **`alta.py::_PRESUPUESTO_S = 45` está calibrado contra el techo equivocado**:
  su comentario dice *«45 s deja margen de sobra dentro de los 100 s del proxy»*
  y esos 100 s son Cloudflare — el corte que llega PRIMERO es el `maxDuration`
  de Vercel, 30. No se tocó: bajarlo sin medir cuánto tarda de verdad la cadena
  de alta (que gasta un crédito por cupón) sería romper un botón que hoy
  funciona. Queda anotado para medirlo con `diag_*`.

**La moral, que es la misma que el módulo de 1816 ya tenía escrita:** *la
paciencia depende de quién espera*. El mismo código censa perfecto en el daemon
—por eso `sin_cartera` tiene 24 escrituras de `av-agent`— y es inalcanzable para
una persona. **El agente podía hacer el arreglo y una persona no, y la única
diferencia eran 30 segundos.**

---

### 0.fi `CRN` ERA MAÍZ EN UN ARCHIVO Y NADA EN EL OTRO — y el guion no decía nada (2026-09-11)

Con el botón de CARTERA ya abriendo (§0.fh), el user vio sus cuatro filas con la
columna vacía: *«no entiendo por qué no sugiere una cartera»*. Y no podía
entenderlo: la fila mostraba **un guion y nada más**.

**El bug: TRES listas de prefijos de agro para la misma pregunta.**

| Dónde | Cuántos | Qué contesta de verdad |
|---|---:|---|
| `api/services/derivados_agro.DISPO_LABELS` | 3 | qué contratos LOCALES tienen pizarra DISPO (`TRI.ROS.P/DISPO`…) |
| `jobs/assets_autofill._PREFIJOS_AGRO` | 3 | *(copiada de la de arriba, y congelada así por un test)* |
| `core.clase_activo.PRODUCTO` | 6 | qué producto es cada prefijo — **incluye los de CME: `CRN` → MAIZ, `SOY` → SOJA** |

Resultado en pantalla: `[CRN.CME/ABR27]` era **«FUTUROS DE MAIZ» para la regla de
la CLASE y no era un derivado para la regla de la CARTERA**. Se quedaba sin
cartera; y sin cartera tampoco podía recibir clase, porque las cinco reglas de
la clase arrancan mirando la cartera. Un futuro de maíz invisible para las dos
mitades del mismo sistema, cada una coherente consigo misma, sin que nada falle.

**El test que lo congelaba era el problema.** Exigía
`_PREFIJOS_AGRO == DISPO_LABELS`, o sea ataba «qué es un contrato de agro» a
«qué contrato tiene pizarra local». Chicago no tiene pizarra local y sigue
siendo agro. Se corrigió el test, no se rompió: ahora la lista sale de
`PRODUCTO` (una sola, `PREFIJOS_AGRO`, sin el dólar — que no es agro y cuya
forma OTC ya la reconoce la otra mitad de la regla) y lo que se exige es la
**contención**: todo contrato con pizarra DISPO tiene que ser agro. La lista
puede ser más ancha, nunca más angosta.

**Y la nota, ahora también en `cartera.py`.** Es el mismo invariante de §0.fg en
el otro campo: una fila sin propuesta dice por qué, y las razones se atienden
distinto —

- «está en `mercado.curvas` pero sus EJES no dan cartera (moneda «», ajuste «»):
  completá los ejes del bono y sale sola»
- «1816 lo pone en la curva «X», que no traduce a ejes conocidos»
- «**es un CEDEAR** (está en `mercado.cedears`): ninguna regla dice con qué
  cartera los sigue la mesa» ← el master de CEDEARs entra como EVIDENCIA de la
  nota y no propone nada: saber que un título es un CEDEAR no dice con qué
  cartera lo sigue la mesa (eso es criterio), pero convierte «no sé qué es esto»
  en «sé qué es y no sé cómo lo llaman ustedes», que es una pregunta que alguien
  puede contestar.
- «ninguna regla del job lo reconoce y no está ni en `mercado.curvas` ni en el
  catálogo de 1816»

**Lo que queda abierto y es criterio de la mesa**: si un CEDEAR va a cartera
`RENTA VARIABLE`, eso es una regla de una línea (`mercado.cedears` ya está
leído) y completa `CAT`, `GEV` y lo que venga. Nadie lo declaró todavía, así que
no se inventó.

---

### 0.fj EL ASISTENTE SE OLVIDABA DE LA CUENTA — lo que se SABE, aparte de lo que se DIJO (2026-09-14)

Clase 4 del curso de arquitectura de IA: la anatomía de una sesión en ADK
(*Identification · Event History · State*). Se midió contra el asistente: de
las tres cajas teníamos una y a medias. `usuario` y `tarea` hacen de identidad
(sin id de conversación —hubo un `conv_id` y se borró el 28/08 porque nadie lo
leía—), el historial está partido en `mensajes` y `eventos` y vive en el
navegador, y **estado no había**: la cuenta de la que se hablaba vivía
implícita en el texto del historial.

**La premisa que se escribió primero era falsa, y el revisor la cazó.** La
primera versión de este cambio decía «`_achicar` borra la cuenta del
historial». No: achica el RESULTADO de la herramienta, y la cuenta queda en los
argumentos del `tool_call`, que no se tocan. Lo que el estado cambia de verdad
es que el dato pasa de **inferido** (un número adentro de un JSON de argumentos,
varias preguntas atrás) a **explícito** (un renglón del SYSTEM), deja de
depender del historial (el router lo topea en 60 mensajes; «esa cuenta» sin
número), y es lo que una persistencia futura guardaría de una conversación.
Que eso ahorre la re-pregunta «¿de qué cuenta?» (la vuelta de 2.110 tokens del
13/09, documentada en el comentario del SYSTEM de `ciclo.py`) es **hipótesis,
sin medir**: se va a ver en `ia.llamadas` como menos vueltas por conversación.

**Lo que se hizo: `asistente/estado.py`, sin framework.** Un dict con lo que
queda en foco (hoy `cuenta`), que viaja ida y vuelta con el historial pero
aparte de él. Cinco decisiones, todas congeladas por test:

| Decisión | Por qué así |
|---|---|
| lo escribe el código desde los **argumentos** de una herramienta que contestó | mismo criterio que la puerta: nunca nombra una herramienta; una cuenta que la puerta cortó no queda en foco |
| se lee en un renglón al **final del SYSTEM**, y es el estado de ANTES de la pregunta | lo variable atrás para no romper el caché; rearmar el SYSTEM por vuelta rompería el de toda la conversación |
| el renglón es **dato, no regla** («en foco: cuenta = 805») | la regla («si ya nombró una cuenta, usala») vive en el SYSTEM, una vez, y ahora contempla el foco; repetirla con otras palabras es lo que hizo re-preguntar el 13/09 |
| **informa al modelo, no completa `cuenta`** a la herramienta | la pared «cuenta sin default» sigue: el código nunca adivina sobre la plata de otro |
| `EN_FOCO` declara cada clave **con su lista cerrada de valores** (`cuenta` → las habilitadas) | lo que llega del navegador va derecho al SYSTEM; con «string corto» entraba `805\nIGNORÁ TODO LO ANTERIOR`. Lo que no es exactamente un id habilitado no existe |

Sin prefijos `user:`/`app:`/`temp:` de ADK: los dos primeros necesitan una base
atrás y la conversación vive en el navegador por decisión; el `app:` que hay
es `ASISTENTE_CUENTAS`, y el `temp:` son los resultados del turno que
`_achicar` descarta. El día que exista la pregunta «qué le contestó ayer» el
módulo no cambia de forma, cambia de dónde carga.

**Cuesta** ~25 tokens por pregunta a partir de la segunda. La tab muestra «📌 en
foco» y lo cuenta como evento del ciclo cuando cambia — que en diez preguntas
sobre la misma cuenta es una vez.

---

### 0.fk LA CHARLA MORÍA A LA PREGUNTA 15 — poda por turnos, no por mensajes (2026-09-14)

Encontrado revisando el cambio de §0.fj: `Preguntar.historial` tenía
`max_length=60` en el router y la tab mandaba de vuelta TODOS los mensajes.
Cada pregunta con herramienta deja cuatro (user, pedido, resultado, respuesta):
a la pregunta 15 el body superaba los 60, Pydantic contestaba 422, la tab
mostraba un error genérico y la única salida era «empezar de nuevo». Funcionaba
perfecto catorce veces y se rompía a la quince sin decir por qué.

**Lo que se hizo: `ciclo._podar`**, al lado de `_achicar`. Antes de empezar se
conservan los últimos `TURNOS_QUE_QUEDAN` (8) turnos completos y se tira el
resto; después se achica lo que quedó. Tres decisiones:

| Decisión | Por qué así |
|---|---|
| **por turnos, nunca por mensajes sueltos** — con un techo de mensajes además del de turnos, porque un turno no tiene tamaño fijo (varios `tool_calls` en una vuelta) | el proveedor exige que cada `tool` conteste a un `assistant` con `tool_calls` presente; cortar entre los dos rechaza la llamada entera con un error de formato. Un pedido y su resultado viven o mueren juntos (mismo invariante que el `tool_call_id` del achicado) |
| la respuesta devuelve el historial **ya podado** | el navegador manda lo que recibió: nunca acumula más de N turnos. El tope del router sube a 600 y pasa a ser de sanidad contra un cliente roto, no el límite de la charla |
| primero se poda, después se achica | achicar lo que se va a tirar es trabajo perdido y el ahorro reportado mentiría |

**Por qué recién ahora.** Podar pierde lo que se DIJO hace más de N preguntas.
Hasta §0.fj lo que se SABÍA (la cuenta) vivía sólo ahí adentro; con el foco en
`estado`, aparte del historial, la poda no le saca nada al modelo que no pueda
volver a pedir. Es la otra mitad de la compactación de ADK: el historial se
recorta, el estado no.

La tab lo cuenta como paso del ciclo (`✂️ podado: N turnos`), al lado del
`achicado` que ya existía y que hasta hoy la pantalla no dibujaba.

---

### 0.fl LA CONVERSACIÓN TIENE NOMBRE — vuelve el `conv_id`, esta vez con lector (2026-09-14)

Tercera caja de la anatomía de sesión de ADK (§0.fj es *State*, el historial es
*Event History*): **Identification**. Hasta hoy dos preguntas de la misma charla
no estaban ligadas por nada: `ia.llamadas` tenía una fila por vuelta, sueltas.
Para saber cuánto costó una conversación había que adivinar por hora y usuario.

**Lo que se hizo.** Un uuid por charla (`ciclo._sesion`) que nace en el backend
en la primera pregunta y el navegador devuelve en las siguientes, con el
historial y el estado. Cada `ai.conversar` lo lleva a la fila de
`ia.llamadas.sesion`. El lector es `panel.conversacion()`, que el router llama
en cada respuesta: la tab muestra «esta conversación: N llamadas · tokens ·
caché · USD» al lado del foco, con las mismas reglas de plata que el panel (lo
cacheado a precio de caché; si a un modelo le falta la tarifa, `usd` es null y
se dice cuál).

**Por qué vuelve algo que se borró.** El 28/08 se sacó `conv_id` de esta misma
tabla con el argumento correcto: cero referencias en el repo, una columna que
nadie escribe ni lee es una pregunta abierta. La columna vuelve porque **nace
con su escritor y su lector en el mismo commit**, y un test congela los dos: si
el ciclo dejara de pasarla al gateway, o el gateway de escribirla, falla.

**Lo que NO se hizo.** Persistir la charla. `sesion` identifica, no guarda:
los mensajes siguen en el navegador y «empezar de nuevo» los borra. Reabrir una
conversación de ayer sigue sin existir hasta que exista la pregunta que lo pida.
Cuando exista, el id ya está, y con él se agrupan las llamadas que ya quedaron
anotadas desde hoy.

---

### 0.fm EL ASISTENTE MIRA EL MERCADO — dos mundos declarados, listas cerradas como pared, y un `int` que viajaba como `string` (2026-09-14)

Hasta hoy las dos herramientas miraban UNA cuenta. Se suman `curva` (qué hay en
una curva y cuánto rinde) y `ficha_bono` (qué es un bono), sobre lo que ya
existía: `curvas_vista.get_curvas_vista` (la vista de CURVAS → LIVE) y
`bono_detalle.get_bono` (el del modal de la ficha). Cero cálculo nuevo, y el
vencimiento sale del mismo master que `cobros_futuros.vence` (REGLA #9).

**Lo que el revisor cazó antes del push, y es la lección del día.** El primer
borrador de `curva` usaba `renta_fija_sql.listar_curva`, que (a) ordena `tea`
ASCENDENTE —«los que más rinden» salían al revés— y (b) no es la fuente de la
pantalla: CURVAS → LIVE dejó de leerla justamente porque «dos fuentes para el
mismo panel siempre terminan discrepando». El test no lo vio porque mockeaba el
service con TEAs iguales: **un mock con valores iguales no prueba una
dirección.** Ahora la herramienta lee la misma vista que la pantalla (con
`tasa_ruido`: la TEA de un bono que vence en días no es comparable y va
última), ordena ella misma, y el test entra con valores crecientes y exige
que salga el mayor. De paso: `ficha_bono` leía `price` donde la vista dice
`last_price` (mismo mock, mismo silencio), y tomaba `patas[0]` como pata
principal confiando en el orden de una lista; ahora `get_bono` declara
`pata_principal` y la herramienta la busca por nombre.

**Tres piezas de estructura, porque cuatro herramientas ya no son dos:**

| Pieza | Qué es | Qué compra |
|---|---|---|
| **el mundo se declara** (`DE_LA_CUENTA` / `DEL_MERCADO`) | las de la cuenta reciben `cuenta` y pasan por el permiso; las del mercado no la reciben | un test exige las dos cosas: una de mercado con `cuenta` sería un alcance que se filtra por la puerta de atrás. Y la lista del portal invitado, si algún día conversa, ya está separada |
| **listas cerradas en la firma** (`Literal` → `enum` en la ficha) | `curva`, `ordenar_por` y ahora `horizonte` | el proveedor rechaza otro valor ANTES de que cueste una vuelta. Las listas de la firma se atan por test a las del service |
| **techo por ficha** (`MAX_FICHA_CHARS`, test) | cada docstring viaja en todas las llamadas | con cuatro son ~1.700 tokens fijos por vuelta; la #8 con dos páginas rompe la suite, no la factura |

**El bug que apareció de paso.** `ficha()` arma el esquema desde la firma, y
con `from __future__ import annotations` las anotaciones llegan como TEXTO:
`_TIPOS.get("int")` no encontraba nada y `dias: int` viajó al modelo como
`string` desde el primer día. No falló nunca — el proveedor manda `"60"` y
Python lo convierte. Se arregló con `eval_str=True` y un test lo congela: los
tipos que ve el modelo son los de verdad.

**Las tasas van en porcentaje.** El motor da TEA y TEM como decimal (0,35) y la
pantalla multiplica al dibujar. El modelo tiene prohibido convertir, así que
la herramienta le da `tea_pct` hecho; paridad ya venía en porcentaje.

**Lo que se dejó para después, a propósito:** `ticker` y `curva` como claves de
foco («¿y su duration?», «¿y a 12 meses?»). Entran en `EN_FOCO` sin cambiarlo
—las dos tienen lista cerrada—, pero primero hay que ver si el modelo los pide.
