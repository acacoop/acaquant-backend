# `lab/langgraph` — laboratorio: un agente de verdad sobre este repo

> **Es un LABORATORIO, no producción.** No lo importa nadie, no corre en ningún
> cron, no toca la base y no tiene un solo `INSERT`. Vive fuera de los contratos
> de capas (`.importlinter`) y de `requirements.txt` a propósito: se instala en
> **su propio venv**, así una dependencia del lab no puede romper el Droplet.

## Qué hace

Le preguntás algo sobre el AV AGENT en castellano y **el modelo decide solo qué
herramientas usar** para contestarte, leyendo tu repo. No hay un flujo fijo: si
le falta información, pide otra herramienta y vuelve a pensar.

## Correrlo (dos minutos)

```bash
cd /root/TradingAV                       # o tu checkout local
python -m venv venv-lab
venv-lab/bin/pip install langgraph langchain-openai
PYTHONPATH=. venv-lab/bin/python -m lab.langgraph.correr --guionado
```

Eso último **no necesita clave**: usa un modelo de mentira y prueba que el
cableado funciona (pide una herramienta, se ejecuta de verdad, el resultado
vuelve). Para que piense en serio:

```bash
set -a && source .env && set +a       # carga DEEPSEEK_API_KEY
PYTHONPATH=. venv-lab/bin/python -m lab.langgraph.correr "¿por qué bono_sin_precio separa cuatro causas?"
PYTHONPATH=. venv-lab/bin/python -m lab.langgraph.correr    # modo conversación
```

## Los cuatro archivos

| Archivo | Qué es | Concepto del curso |
|---|---|---|
| `herramientas.py` | 4 funciones de solo lectura sobre el repo | **tools** — el docstring ES el prompt |
| `modelo.py` | de dónde sale el cerebro (Gemini o guionado) | **provider abstraction** |
| `grafo.py` | 40 líneas: estado, nodos, aristas, ciclo | **el agent loop** |
| `correr.py` | el CLI, que muestra cada paso | **observabilidad** |

## El concepto, en cuatro ideas

```
                    ┌──────────────┐
        entrada ──► │    agente    │ ◄─────────────┐
                    │  (el modelo) │               │
                    └──────┬───────┘               │
                  ¿pidió herramientas?             │
                     sí ──┴── no ──► FIN           │
                     │                             │
              ┌──────▼──────┐                      │
              │ herramientas│──────────────────────┘
              └─────────────┘
```

1. **El estado** (`Estado`) es un dict tipado que viaja por el grafo. El reducer
   `add_messages` es lo que hace que los nodos *agreguen* en vez de *pisar*.
2. **Los nodos** son funciones `estado -> pedazo de estado`. Nada mágico.
3. **La arista condicional** (`tools_condition`) es **la decisión**. Es
   literalmente lo que le falta al agente de producción, cuyo `motor._agenda()`
   es un `sorted()` por ritmo donde nadie elige nada.
4. **El checkpointer** guarda el estado después de cada paso. Eso es la memoria
   de trabajo, y es lo que después permite meter un humano en el medio
   (`interrupt`) sin perder el hilo.

## Cómo se relaciona con el agente de producción

El AV AGENT (`agente/`) es determinista y va a seguir siéndolo. Este lab **lo
lee**, no lo reemplaza. La división que queremos probar:

| | `agente/` (producción) | `lab/langgraph/` |
|---|---|---|
| Qué hace | detecta y verifica | investiga y propone |
| Cómo decide | catálogo + ritmo declarado | el modelo elige |
| Escribe | sí, por `registro.py`, con libro | **no** |
| Quién dice si funcionó | el detector, en la próxima pasada | nadie todavía |

**La línea que no se cruza:** un LLM nunca puede ser la pieza que devuelve `ok`
con lista vacía. Si el modelo no contestó, es `sin_datos` — y entonces no se
cierra nada (invariante #1 de `docs/AGENT.md`).

## Los pasos siguientes, en orden

1. **Tools de SQL de solo lectura** (`mercado.curvas`, `market_snapshot`,
   `agente.hallazgos`). Ahí empieza a contestar cosas que hoy no contesta nadie:
   *«¿por qué este bono muestra `--`?»*.
2. **`interrupt` antes de escribir** — el `PROPONER → OK → APLICAR` que el modal
   ya hace a mano, formalizado en el grafo.
3. **Los 8 arreglos de `agente/arreglos.py` como tools.** Ya tienen `preview()`
   (dry-run), allowlist y libro de auditoría: son tools de producción esperando
   un selector.
4. **Checkpointer en Postgres** en vez de memoria, para que una investigación
   sobreviva a un reinicio.
