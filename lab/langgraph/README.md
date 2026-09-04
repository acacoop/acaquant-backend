# `lab/langgraph` — EL INVESTIGADOR

> **⚠️ ESTO YA NO ES UN LABORATORIO. ESTÁ EN PRODUCCIÓN.**
>
> Nació como un banco de pruebas y este README decía, textual, *«no lo importa
> nadie, no corre en ningún cron, no toca la base y no tiene un solo INSERT»*.
> Las cuatro cosas dejaron de ser ciertas y el archivo siguió diciéndolas
> durante días. Un manual que describe una versión que ya no existe es **peor
> que no tener manual**: el que no existe te obliga a leer el código; éste te
> deja concluir lo contrario de la verdad. Por eso el encabezado arranca acá.
>
> Hoy: **lo importa `jobs/agente.py`** (corre en un hilo del daemon del agente),
> **lee seis tablas de producción**, **escribe en el esquema `lab`**, y sus tres
> dependencias están en `requirements.txt`. Se le habla desde el modal del AV
> AGENT por tres endpoints admin-only.

## Qué hace

Le preguntás por qué pasó algo —una reincidencia, un bono sin precio, un job que
no dejó el dato— y **el modelo decide solo qué herramientas usar** para
averiguarlo, leyendo el repo y la base. No hay un flujo fijo: si le falta
información, pide otra herramienta y vuelve a pensar. Termina en un **veredicto
con forma** (qué pasó, por qué, de quién es, qué haría, qué no sabe, de dónde lo
sacó) que queda anotado en un libro.

**No escribe nada en producción y no puede**: la base no lo deja.

## La división con el agente de producción

El AV AGENT (`agente/`) es determinista y va a seguir siéndolo. El investigador
**lo lee**, no lo reemplaza.

| | `agente/` (producción) | `lab/langgraph/` (el investigador) |
|---|---|---|
| Qué hace | detecta y verifica | investiga y propone |
| Cómo decide | catálogo + ritmo declarado | el modelo elige |
| Escribe en producción | sí, por `registro.py`, con libro | **no — la base se lo impide** |
| Quién dice si funcionó | el detector, en la próxima pasada | `scripts/eval_investigador.py` |

**La línea que no se cruza:** un LLM nunca puede ser la pieza que devuelve `ok`
con lista vacía. Si el modelo no contestó, es `sin_datos` — y entonces no se
cierra nada (invariante #1 de `docs/AGENT.md`).

## Los archivos

| Archivo | Qué es | Concepto del curso |
|---|---|---|
| `grafo.py` | estado, cinco nodos, aristas, ciclo | **el agent loop** |
| `investigaciones.py` | un tipo de caso por fila, con su **piso** | **el método** — lo que hay que mirar antes de concluir |
| `herramientas.py` | 5 funciones de solo lectura sobre el repo | **tools** — el docstring ES el prompt |
| `datos.py` | 6 funciones de solo lectura sobre Postgres | **tools con riesgo** — tocan producción |
| `veredicto.py` | la forma de la respuesta, declarada una vez | **salida estructurada** |
| `diario.py` | el libro de investigaciones | **memoria persistente** |
| `base.py` | las DOS conexiones, y nadie más las conoce | **fail-closed** |
| `modelo.py` | de dónde sale el cerebro (DeepSeek o guionado) | **provider abstraction** |
| `medidor.py` | techo de gasto + traza, como callback | **observabilidad** |
| `cola.py` | los pedidos y sus pasos en vivo | **trabajo asincrónico** |
| `servicio.py` | quién atiende la cola, y en qué hilo | **el runtime** |
| `correr.py` | el CLI, que muestra cada paso | **el harness de mano** |
| `armar_lector.py` | arma las dos conexiones en el `.env` | (utilitario) |
| `probar_lector.py` | ¿los permisos son REALES? piso **y techo** | **verificación de la jaula** |

Y fuera de esta carpeta, pero parte del sistema:

| Dónde | Qué |
|---|---|
| `sql/lab.sql` | el esquema **y el alcance**: qué puede leer, revocando antes de otorgar |
| `scripts/eval_investigador.py` | lo califica contra lo que arregló el problema de verdad |
| `api/routers/agente.py` | los tres endpoints (`/lab/investigar`, `/lab/pedido/{id}`, `/lab`) |
| `jobs/agente.py` | lo llama en cada pasada del daemon, en un hilo aparte |
| `docs/AGENT.md` §L | la documentación de dominio |

## El loop, en un dibujo

```
                     ┌──────────────┐
         caso ──────►│    agente    │◄──────────────┐
                     │  (el modelo) │               │
                     └──────┬───────┘               │
                    ¿pidió herramientas?            │
                       │            │               │
                      sí            no              │
                       │            │               │
              ┌────────▼─────┐      │               │
              │ herramientas │      │               │
              └────────┬─────┘      │               │
                       │            │               │
                 ┌─────▼─────┐      │               │
                 │  anotar   │──────┼───────────────┤   ← MEMORIA DE TRABAJO
                 └───────────┘      │               │
                                    │               │
                          ┌─────────▼────────┐      │
                          │  revisar el piso │      │   ← EL MÉTODO
                          └─────┬────────┬───┘      │
                          falta │        │ completo │
                                └────────┼──────────┘
                                         │
                                 ┌───────▼──────┐
                                 │   redactar   │
                                 └──────────────┘
```

1. **El estado** es un dict tipado que viaja por el grafo. El reducer
   `add_messages` es lo que hace que los nodos *agreguen* en vez de *pisar*.
2. **Los nodos** son funciones `estado -> pedazo de estado`. Nada mágico.
3. **La arista condicional** es **la decisión**. Es literalmente lo que le falta
   al agente de producción, cuyo `motor._agenda()` es un `sorted()` por ritmo
   donde nadie elige nada.
4. **`revisar el piso`** no lo trae ningún framework: cuando el modelo deja de
   pedir herramientas, se verifica que haya mirado el mínimo que su tipo de caso
   declara — **contra las que se ejecutaron de verdad**, no contra lo que dice
   que miró.
5. **`anotar`** es la memoria de trabajo. Sin ella el modelo repetía la misma
   búsqueda con el patrón apenas cambiado, cinco veces en una corrida.

## La jaula: dos identidades, y ninguna cae a la del sistema

    lector_lab    lee seis tablas de producción y el esquema del lab.
                  **La base NO lo deja escribir.** En ningún lado.
    escritor_lab  escribe SÓLO en `lab.*`. Producción le es INVISIBLE.

Que sean dos y no una es la diferencia entre «confío en que no va a escribir» y
«no puede». **Ninguna cae a `POSTGRES_URI`**: esa es la del sistema y puede
todo; un fallback silencioso convertiría la garantía en una intención.

**El alcance vive en `sql/lab.sql`, no en la base.** Ese archivo revoca todo y
vuelve a otorgar exactamente lo declarado, así que un permiso de más otorgado a
mano se deshace en el próximo deploy. `probar_lector` verifica las dos mitades:
que pueda leer lo que tiene que leer, y que **no pueda leer nada más**.

## Correrlo

Las dependencias están en `requirements.txt`, así que el venv del proyecto
alcanza. El esquema y los permisos los aplica `deploy/deploy.sh`.

```bash
python -m lab.langgraph.correr --guionado          # prueba el cableado, sin gastar un token
python -m lab.langgraph.correr --tipos             # qué sabe investigar
python -m lab.langgraph.correr reincidencia M31G6
python -m lab.langgraph.correr job dolar_mep
python -m lab.langgraph.probar_lector              # ¿la jaula es real?
python -m scripts.eval_investigador --dias 90      # ¿acierta?
```

Lo único que sigue siendo a mano es **crear los dos roles** (`CREATE ROLE` lleva
contraseña, y una contraseña no va a un repo). Se hace una vez; desde ahí sus
permisos los manda `sql/lab.sql`. Ver `docs/AGENT.md` §L.

## Lo que falta

1. **`interrupt` antes de escribir** — el `PROPONER → OK → APLICAR` que el modal
   ya hace a mano, formalizado en el grafo.
2. **Checkpointer en Postgres** en vez de memoria. ⚠️ **Va ANTES que el punto 1,
   no después**: hoy el progreso vive en la RAM del proceso, así que un reinicio
   del daemon lo borra. Una aprobación humana puede tardar horas o quedar de un
   día para el otro, y con la memoria como está cualquier reinicio en el medio de
   esa espera pierde la investigación.
3. **Los 8 arreglos de `agente/arreglos.py` como tools.** Ya tienen `preview()`
   (dry-run), allowlist y libro de auditoría: son tools de producción esperando
   un selector.
