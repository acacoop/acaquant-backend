# AGENTE_MAPA.md — el MAPA DEL CÓDIGO del AV AGENT

> **Qué es esto y qué NO es.** `docs/AV_AGENT.md` (10.045 líneas, §0.a–§0.dd) es
> el **doc madre del PROGRAMA**: el porqué de cada decisión, la historia, los
> incidentes, el roadmap. Sigue siendo el que se lee primero y donde se asienta
> todo avance en el mismo commit.
>
> Esto es otra cosa: **el mapa del CÓDIGO**. Dónde vive cada pieza, qué invariante
> rige en cada una, y por dónde se entra a tocar. Es el `init` del agente — lo que
> hay que tener en la cabeza antes de abrir un archivo, no el relato de cómo se
> llegó hasta acá. Si los dos se contradicen, **manda AV_AGENT.md**.
>
> Nada de acá reemplaza leer el header del módulo que vas a tocar: los headers de
> este subsistema son la documentación real y están al día.

---

## 0. El tamaño real, medido

| Pieza | Cuánto |
|---|---|
| Servicios `api/services/av_agent_*.py` | **34 archivos, 18.870 líneas** |
| El modelo | `core/ciclo.py` (650) + `api/services/salud.py` |
| Jobs | `jobs/av_agent.py` · `jobs/av_agent_live.py` · `jobs/av_agent_centinela.py` (daemon) |
| Endpoints HTTP | **44**, todos `/api/ia/av-agent/*` y todos `require_admin` |
| Tablas | **17** en el schema `agente` + 6 en `manager` (salud/controles/superficie/perfiles) |
| Tests | **47 archivos, 864 tests** — y una parte grande son LEYES, no verificaciones |
| Frontend | `src/components/av-agent/` (6.072 líneas, 9 archivos) |

**No usa IA para detectar nada.** De las 5 tareas de `core/ai._TAREAS`, tres son
del agente (`av_agent_informe`, `av_agent_accion`, `av_agent_error`) y ninguna
detecta: leen patrones, sugieren un valor que aprueba una persona, o traducen una
línea de log. La detección entera es determinista.

---

## 1. El modelo — `core/ciclo.py`

Todo lo demás cuelga de acá. **Leelo antes que nada.**

### Los seis estados

```
nuevo → visto → en_curso → resuelto → volvio
                             ignorado (reversible → nuevo)
```

`TRANSICIONES` declara qué saltos existen; `puede_pasar()` devuelve `False` ante
lo desconocido. **`RESUELTO → VOLVIO` es la única vuelta atrás y es la más
importante**: un problema que reaparece no es nuevo, y contarlo como nuevo es
cómo se pierde que algo se rompe todas las semanas.

### La identidad = `(sujeto, causa)` — y NADA más

```python
ciclo.identidad(sujeto, causa, respaldo="")   # el modelo
av_agent_items.clave_de_problema(...)         # LA única puerta de entrada
```

`tipo` y `origen` **NO son identidad: son quién lo vio.** Cuando estaban en la
clave, el mismo bono roto producía dos objetos (el del detector de rueda y el del
control nocturno) y arreglarlo movía uno dejando el otro colgado. `causa_canonica()`
normaliza los sinónimos (`patas_equivocadas` del control → `pata_equivocada` del
detector) **derivándolos de `ACCIONES`**, no de una segunda lista.

`respaldo` es para lo que no tiene sujeto (un `db_cambio` habla de la base
entera): sin él, todos los sin-sujeto de una causa colapsarían en un objeto.

### Los hitos, y por qué el reloj es hábil

`HITOS_DIAS = (1, 2, 3, 7, 14, 30)`. Cada hito que pasa sin volver suma
confianza; **volver una vez borra los anteriores** (un arreglo que falla al día 8
no es «7 días bueno»).

Dos relojes distintos y la asimetría es deliberada:
- `dias_abierto()` → **calendario**. Un problema abierto molesta también el sábado.
- `dias_de_prueba()` → **días hábiles AR** (`core/calendario`, límites en hora
  argentina). Un arreglo del viernes llegaba al hito 1 el sábado con los motores
  apagados: *evidencia que no pudo contradecirse no es evidencia*. **Volver, en
  cambio, cuenta siempre** — eso lo decide `estado`, no este reloj.

### Las bandas — qué merece atención HOY

`volvio` › `estancado` › `arrastra` › `nuevo` › `mirando`. `prioridad()` devuelve
una **tupla y no un puntaje**: un «87 puntos» no se puede discutir ni auditar y
esconde cuál criterio lo puso ahí.

### El REGISTRO — 23 tablas y cómo dice cada una su estado

`estado_de(tabla, fila)` es **el árbitro**: la pantalla pregunta acá en vez de
mirar la columna, así dos pantallas no pueden discrepar. Cada `Forma` declara
además su `clase`, y esto importa porque **no todo lo que tiene estado es un
problema**:

| clase | qué es | ¿va a `av_agent_items`? |
|---|---|---|
| `problema` | algo está mal en algo | **sí** |
| `sensor` | una lectura cruda (¿contesta Aunesa?) | no — sería el termómetro como fiebre |
| `bitacora` | que algo CORRIÓ (un run, una acción) | no — su valor es ser append-only |
| `meta` | habla DE los items (el seguimiento) | no — el modelo conteniéndose |
| `acuse` | quién LEYÓ qué, por persona | no — se perdería el por-persona |

`sin_migrar()` cuenta la deuda de vocabulario; `deuda_de_problemas()` la de
modelo; `espejan()` las que ya tienen objeto con historia aunque conserven su
columna vieja. **La deuda es un número, y ésa es la mitad del valor del módulo.**

`tablas_del_agente()` deriva del `sql/schema.sql` por regex → una tabla nueva sin
declarar **rompe un test**.

### Comunicación ≠ problema

`TIPOS_COMUNICACION = ("aviso", "aviso_fila", "pregunta")`. Son cosas que el
agente **dijo**: tienen ciclo pero no son algo roto. Mezclarlas hizo que la
pantalla dijera *«58 de 256 abiertos»* con 142 filas de aviso adentro, y peor:
sus «resueltos» entraban al seguimiento y podían llegar a **votar al eval set**
como si fueran arreglos que aguantaron.

---

## 2. El flujo, de punta a punta

```
        ┌── jobs.av_agent          (0 13,15,17,19 UTC L-V) — censa 1816, ~29 créditos
DETECTAR├── jobs.av_agent_live     (cada 5' 13:30-19:55)   — rueda, CERO créditos
        ├── jobs.db_tamano         (23:30 UTC)             — el SISTEMA de noche
        └── av_agent_centinela     (daemon systemd, 30s en rueda / 300s fuera)
                    │
                    ▼
PERSISTIR   av_agent_items.sincronizar(origen, vistos, evaluados=…)
            ├─ ver()            → nace · suma `veces` · o pasa a `volvio`
            └─ _cerrar_ausentes → lo que ya no está pasa a `resuelto`
                    │            (⚠ SOLO de los tipos que la corrida declaró evaluar)
                    ▼
MOSTRAR     av_agent_vista.vista()  → UN request, GET /api/ia/av-agent/vista
                    │
                    ▼
ACTUAR      av_agent_hacer:  PROPONER → (OK humano) → APLICAR → VERIFICAR
            Seguible:        + veredicto() cuando el efecto no es inmediato
                    │
                    ▼
MEDIR       av_agent_evals   (voto humano)  +  jobs.seguimiento (23:50 UTC)
                             → precisión por causa → la compuerta de autonomía
```

### La guarda más importante de todo el subsistema

`sincronizar(..., evaluados=…)`. Una corrida puede mirar **menos** de lo que mira
siempre: si 1816 no contesta, la lista vacía de `falta_en_base` no significa que
no falte ningún bono — significa que no se miró. Cerrar por ausencia sin saber
qué se evaluó convertiría cada caída de un proveedor en «se arreglaron 40
problemas»: **el tablero en verde justo el día que está más ciego.**

Sin `evaluados`, `_cerrar_ausentes` devuelve **0 y no cierra nada**. Es la
degradación correcta: dejar un problema resuelto en la lista molesta; borrar uno
que sigue roto no se ve nunca.

### Dos ciclos conviven en `av_agent_hallazgos`

- **La relevada** deja una CORRIDA (foto con `corrida_at`; vigente = la última).
- **Los monitores** (`ALCANCES_VIVOS = ("live", "sistema")`) **reemplazan** lo
  suyo en cada pasada — acumular dejaría 84 avisos del mismo problema.

⚠️ Por eso hay que **excluirlos del `max(corrida_at)`**: un máximo a secas
devuelve siempre el del monitor y la relevada entera desaparece de la pantalla en
silencio. Ya pasó con `live`; la constante existe para que no se repita.

### Los hallazgos de rueda VENCEN

`VENCE_RAPIDO_S` (30') y `VENCE_OBSERVACION_S` (15'). El monitor deja de correr
al cierre y su última foto de las 16:55 se quedaba toda la noche: *un detector
correcto mostrando una foto vieja*, que para el que mira es lo mismo. **Vence en
vez de borrarse con un cron**: si el monitor se muere a las 11 sus hallazgos
desaparecen también, y está bien, porque ya no sabemos si siguen pasando.

---

## 3. Los registros declarativos — la constitución

**Nada de esto se infiere.** Todos viven en `api/services/av_agent.py` (salvo
donde se indica), todos tienen un test que exige la declaración, y el default de
cada uno es siempre el lado ruidoso (mostrar de más, pedir voto de más).

| Registro | Qué decide | Entradas | Default |
|---|---|---|---|
| `ACCION_POR_TIPO` | qué puerta abre cada tipo | 19 | — (todo tipo debe estar) |
| `ACCION_POR_REGLA` | **gana sobre el tipo** | 2 | cae al tipo |
| `SIN_PUERTA` | por qué NO hay botón, y si es deuda | 13 | `afuera` = es deuda |
| `PREGUNTA_POR_TIPO` | `juicio` vs `observacion` | 19 | **`juicio`** |
| `DOMINIO_EVAL` | dónde se anota el voto | 19 | `bono` |
| `DE_QUIEN` | `nuestro` vs `mercado` | 3 | **`nuestro`** (no esconde nada) |
| `EN_AHORA_SIEMPRE` | se muestra en AHORA aunque no sea de hoy | 4 | no |
| `TIPOS_NOTICIA` | observación sin accionable → vive en AHORA | 2 | no (queda en LA LISTA) |
| `ACCIONES` (`av_agent_hacer`) | qué sabe arreglar | 10 clases | — |
| `SIN_ACCION` (`av_agent_hacer`) | por dónde se arregla lo que no tiene botón | 6 | — (test lo exige) |
| `DESTINOS` (`av_agent_acciones`) | dónde escribió cada acción | 11 + derivado | `?` |
| `_DONDE_CORRE` (`av_agent_skills`) | en qué job corre cada detector | 20 | — (test lo exige) |
| `_TAREAS` (`core/ai.py`) | modelo, tokens, thinking por tarea | 5 | flash/800/disabled |

### Los tres que más fácil se rompen

**`ACCION_POR_REGLA` gana sobre `ACCION_POR_TIPO`.** El tipo es la FAMILIA; la
regla es la causa, y la causa decide el arreglo. `precio_moneda` agrupa dos
problemas que se arreglan distinto: `cotiza_en_pesos` (buscar la pata) y
`pata_equivocada` (apuntar el master). Como la acción salía del tipo, los dos
mostraban «BUSCAR LA PATA USD» — que para el segundo pide una pata que ya cotiza
y deja el master igual. 17/17 votos con el user gritando que ya lo había
completado 40 veces. **Un botón que no arregla el problema de esa fila es peor
que no tenerlo: promete, no cumple, y no da un solo error.**

**`PREGUNTA_POR_TIPO` protege el eval set.** A una OBSERVACIÓN («el motor escribió
esta línea de ERROR») preguntarle «¿acertó?» es preguntar si el log existe: la
respuesta es siempre sí, la causa llega a 10/10, se marca `candidata_a_auto` y
**la compuerta de autonomía se abre con evidencia que no mide nada**. A una
observación se le pregunta *¿te sirve verla?* y se guarda con `origen='utilidad'`,
que los filtros de la compuerta descartan.

**`SIN_PUERTA` convierte paredes en deuda contable.** `cobertura()` agrupa por
REGLA (no por tipo — la regla es la unidad que se vuelve acción) y ordena por
volumen: *qué pared conviene romper primero*. Ese ranking habría cantado
`pata_equivocada` semanas antes; sin él, la única forma de saber cuál era la peor
era que alguien se hartara de verla.

---

## 4. Los 34 servicios, por función

### Núcleo
| Archivo | Qué es |
|---|---|
| `av_agent.py` (2.069) | los 9 detectores puros + **todos los registros declarativos** |
| `av_agent_items.py` (685) | el STORE de los objetos — `ver` · `sincronizar` · `marcar` · `que_importa` |
| `av_agent_vista.py` (1.380) | la pantalla entera en UN request |
| `av_agent_hacer.py` (1.874) | las 10 acciones, el ciclo PROPONER→APLICAR→VERIFICAR |
| `av_agent_alta.py` (4.055) | dar de alta un bono entero desde 1816: simular → aplicar |

### Detectores especializados (todos escriben hallazgos con la misma forma)
`av_agent_contexto` (la base sin listas: cadencia MEDIDA + frescura) ·
`av_agent_db` (peso de tablas, delta diario) · `av_agent_motores` (caídos y
ruidosos) · `av_agent_seguridad` (la superficie HTTP, probada de verdad) ·
`av_agent_latencia` (cada endpoint contra SU propia mediana) ·
`av_agent_proveedores` (Aunesa y compañía) · `av_agent_crontab` (repo vs máquina)
· `av_agent_sin_precio` (5 causas) · `av_agent_recuperados` (la buena noticia) ·
`av_agent_respuesta` (¿el mercado contestó?) · `av_agent_causas` (tres avisos, un
problema).

### Puertas de arreglo
`av_agent_pata` (buscar/pedir/apuntar la pata) · `av_agent_espejo` (asset
faltante) · `av_agent_rehacer` (re-correr un día) · `av_agent_salud` (SOLO
LECTURA, a propósito).

### Medición y memoria
`av_agent_evals` (la compuerta) · `av_agent_seguimiento` (el tiempo como
evidencia) · `av_agent_memoria` · `av_agent_errores` (traduce un log, **una vez
por PATRÓN** y persistido) · `av_agent_acciones` (EL LIBRO).

### Comunicación y superficie
`av_agent_preguntas` · `av_agent_mensajes` · `av_agent_agenda` (¿qué estoy
haciendo hoy?) · `av_agent_skills` (el catálogo DERIVADO) · `av_agent_explicar`
(9 explicadores) · `av_agent_control` (la parada) · `av_agent_masivo` +
`av_agent_analista` (el informe con IA) · `av_agent_relevar`.

---

## 5. Las cinco leyes de conexión (REGLA #10) — dónde se cumplen

Toda funcionalidad nueva del agente cumple **las cinco en el mismo commit**:

1. **Es un objeto con DNI** → `agente.av_agent_items` (clave `sujeto|causa`) o es
   una comunicación tipada (`ciclo.TIPOS_COMUNICACION`).
2. **Tiene UNA casa, por su naturaleza** →
   noticia → **AHORA** · accionable → **LA LISTA** · arreglo en prueba →
   **¿AGUANTAN?** · monitor en vivo → **VIGILANCIA** · pasado →
   **HISTORIAL/REGISTRO** · comunicación → **COMUNICACIONES** (solo hoy).
   Si aparece en dos, una es la casa y la otra un puntero.
3. **Lleva fecha y hora visibles.**
4. **Consulta el DNI antes de actuar** (`estados_de`) — un lote jamás trabaja
   desde una foto sin cruzar el estado, ni re-aplica lo atendido.
5. **Se declara** — skill en SKILLS, acción en `DESTINOS`, tipo en los registros
   de §3.

### Y la que las precede: REGLA #9

Dos copias del mismo criterio se desincronizan **sin dar un solo error**. En este
subsistema pasó cinco veces documentadas:

- `atendido`/`recien`/`ya_votado`/`sin_puerta`/`resuelto` — cinco derivaciones en
  cinco lugares en cinco días → nació `core/ciclo.py`.
- El contador de ENCONTRÓ decía **95** y LA LISTA **60**: el mismo predicado
  escrito dos veces en el front, y al sumar `ignorado` + `noticia` se actualizó
  una copia. Se arregló **eliminando el filtro del front**: hoy el backend manda
  `por_resolver` y las dos pantallas lo LEEN.
- `clave_caso()` en evals: `votar()` guardaba `.upper()` y los lectores comparaban
  sin upper. Los bonos matcheaban *de casualidad* (ya vienen en mayúscula) y los
  votos sobre motores/tablas **no se recordaban nunca** — hasta 9 votos duplicados
  y los botones volviendo intactos en cada recarga.

**La lección operativa: cuando dos lugares tienen que estar de acuerdo, borrá uno.**

---

## 6. El frontend — `src/components/av-agent/`

Tres tabs (eran siete y era un menú, no una jerarquía), agrupadas por lo que hay
que HACER: **AHORA** (algo espera una decisión) · **ENCONTRÓ** (la lista de
trabajo) · **HISTORIAL** (lo que ya pasó). CONTROL, AGENDA y SKILLS pasan a
íconos a la derecha.

**La red se toca desde UN archivo** — `av-agent/datos.tsx` — y el
`no-restricted-imports` de `eslint.config.mjs` lo hace estructural. Tres verbos:

- `leer(url)` — GET, no cambia nada.
- `llamar(url, body)` — POST que CALCULA. No muta lo que la pantalla dibuja → no relee.
- `escribir(url, body, relee)` — POST que MUTA. **Declara qué invalida y lo relee
  al volver, también si el backend contestó `ok: false`** — la pantalla tiene que
  mostrar la verdad del servidor, salga bien o mal. Solo un fallo de red saltea la
  relectura: no hay a quién preguntarle.

Y **«no pude leer» nunca borra lo que había**: el recurso conserva el dato
anterior y expone el error aparte. `null` silencioso dibujado como «no hay nada»
es la mentira que el agente persigue en el backend; el front no la reintroduce.

**El front NO DERIVA**: acción, estado, atendido, nombre y los contadores vienen
resueltos del backend.

Es **admin-only decidido en el server** (`layout.tsx`): sin el módulo el
componente no existe en el HTML, no pollea y no puede mostrar nada. El gate real
es `require_admin` en los 44 endpoints — esto es defensa en profundidad. Y por
**REGLA #8** hay un test (`test_rbac`) que falla si un endpoint nuevo de
`/api/ia` queda sin `require_admin`: así el agente, que habla del estado interno
del sistema, no puede quedar alcanzable desde el portal invitado.

---

## 7. Cómo agregar cosas

### Un detector nuevo
1. La función pura en `av_agent.py` (o su módulo especializado). **No toca la base
   ni la red** — se testea sin Postgres.
2. Declarar el tipo en **`ACCION_POR_TIPO`** (con `None` explícito si no hay
   puerta) + **`SIN_PUERTA`** + **`PREGUNTA_POR_TIPO`** + **`DOMINIO_EVAL`**.
3. Si es observación sin accionable → **`TIPOS_NOTICIA`**. Si es infraestructura
   que hay que ver esté rota desde cuando sea → **`EN_AHORA_SIEMPRE`**.
4. Describirlo en `av_agent_skills._QUE_DETECTA` + `_DOMINIO_DETECTOR` +
   **`_DONDE_CORRE`** (y el job tiene que **escribir** hallazgos de ese tipo — hay
   un test que lo exige; el horario se lee del crontab, no se escribe).
5. El job lo suma a `evaluados` en `sincronizar()`.

**El enemigo es el FALSO POSITIVO, no el falso negativo.** Si la lista trae ruido,
a las tres semanas no la mira nadie y el agente muere aunque funcione.

### Una acción nueva
Una clase con `proponer` / `aplicar` / `verificar` y una línea en `ACCIONES`. Ni
endpoint, ni tabla, ni UI. Si su efecto no es inmediato hereda de `Seguible` y
suma `veredicto()` + `espera_s` (en **segundos de mercado abierto**).

- **La escritura va SIEMPRE por la misma puerta que usa la pantalla** (ej.
  `assets_sql.set_campos`). Un segundo camino termina con dos criterios distintos.
- `causa` es la regla que vota un humano, que **no siempre es `sobre`** (el
  control se llama `patas_equivocadas`, el detector emite `pata_equivocada`): si
  divergen, el voto humano y el derivado miden por separado y **ninguno llega
  nunca al mínimo de la compuerta**.
- Si no tiene acción posible, va a `SIN_ACCION` **diciendo por dónde se arregla**
  — un test exige una de las dos cosas.

### Regla de oro para el LLM
La regla determinista SIEMPRE primero; el modelo **solo para lo que la regla no
pudo**, y entra por el mismo `Propuesta` con `fuente="ia"`. No es desconfianza:
el 80% lo resuelve una regla de tres líneas, y gastar tokens y atención humana en
eso es tirar los dos recursos que escasean. Toda llamada pasa por `core/ai.py`,
que **nunca propaga excepción** y deja traza en `ia.trazas`.

---

## 8. Los invariantes que los tests hacen cumplir

No son verificaciones: son leyes que bloquean el merge. Los nombres de los tests
son la mejor documentación del subsistema — leelos como índice.

- **`test_ciclo`** — toda tabla del agente declara cómo dice su estado; ninguna se
  declara dos veces; no se declaran tablas fantasma; una desconocida cae en
  `NUEVO` y **no en `RESUELTO`**; una fila rota no hace explotar al árbitro.
- **`test_av_agent_skills`** — todo explicador/acción/detector/control está en el
  catálogo; toda skill declara si usa IA y **para qué**; **ningún detector usa el
  modelo**; el job de cada detector escribe hallazgos y está en el crontab; el
  horario no está escrito a mano.
- **`test_av_agent_circulo`** — toda acción declara una causa resoluble; la de
  `apuntar_pata` es **la que vota un humano**; el voto del seguimiento pesa más
  que el derivado; «todavía no volvió» **no es** «aguantó»; una causa 17/17 queda
  probada y **un solo error la saca**.
- **`test_av_agent_dni`** — el sujeto no se stripea; `resolver_sujeto` marca
  `en_curso` **y no `resuelto`**; el masivo saltea lo ya atendido; todo control
  tiene acción o motivo declarado.
- **`test_av_agent_seguridad`** — se ven **todas** las rutas (no las de primer
  nivel), los gates heredados del `include` se ven, ninguna escritura queda sin
  gate, y **jamás se prueba una escritura**.
- **`test_av_agent_sistema`** — lo LENTO no es un hallazgo, lo ANORMAL sí; cada
  endpoint se compara contra sí mismo; sin historia no hay normal; una query sola.

### La trampa de FastAPI, ya pagada
`app.routes` **no** trae las rutas de los `include_router` y las `dependencies=`
del include no bajan a cada ruta. `audit_rbac` y `test_rbac_superficie` veían
**37 de 541 rutas** y pasaban en verde. La técnica vive UNA vez en
`api/superficie.py` y las tres herramientas delegan ahí — **no la reimplementes**.

---

## 9. Comandos

```bash
# la relevada (censa 1816, ~29 créditos)
python -m jobs.av_agent --dry-run              # imprime todo, no escribe una fila
python -m jobs.av_agent --alcance todo         # los 887 de 1816, no solo soberanos
python -m jobs.av_agent --detalle
python -m jobs.av_agent --preguntas
python -m jobs.av_agent --responder "3=alta,5-9=ignorar" --por vos@acaquant.com

# el monitor de rueda (cero créditos) y el daemon
python -m jobs.av_agent_live
python -m jobs.av_agent_centinela              # en prod es systemd, Restart=always

# medición y diagnóstico
python -m jobs.seguimiento                     # ¿qué aguantó y qué volvió?
python -m scripts.diag_ciclo                   # la deuda de vocabulario, contada
python -m scripts.diag_agente_conexion         # ¿quedó algo suelto? (REGLA #10)
python -m scripts.diag_agente_veredicto
python -m scripts.diag_av_agent_flujos

pytest tests/unit/test_av_agent*.py tests/unit/test_ciclo.py -q
```

---

## 10. Errores que ya se cometieron (no los repitas)

| El error | Qué produjo |
|---|---|
| Meter `tipo`/`origen` en la identidad | el mismo bono roto como DOS objetos; arreglarlo movía uno |
| Derivar el estado en la pantalla | cinco derivaciones contradiciéndose en cinco días |
| Cerrar por ausencia sin `evaluados` | «se arreglaron 40 problemas» el día que el proveedor está caído |
| Un `None` por olvido en vez de explícito | 38 filas sin botón que se leen como que el agente no sabe qué hacer |
| Preguntar «¿acertó?» a una observación | la compuerta de autonomía abriéndose con 10/10 que no mide nada |
| Contar el mismo trabajo en dos lugares | la tab decía 95 y la sub-tab 60 |
| Verificar un efecto que tarda 5s, 0s después | un chequeo con una sola respuesta posible: un cartel, no un chequeo |
| Dejar la foto del monitor sin vencimiento | avisos de «sin precio» todo el fin de semana con el mercado cerrado |
| No excluir los alcances vivos del `max(corrida_at)` | la relevada entera desaparece de la pantalla, en silencio |
| Normalizar la clave en el que escribe y no en los que leen | votos que no se recuerdan nunca, cero errores, cero logs |
