# Buzón de pedidos — del comentario al código

Cómo una idea que alguien de la mesa dice al pasar termina siendo un cambio en
la plataforma, sin que nadie tenga que abrir un ticket ni acordarse de mirar
una lista.

El inventario de pedidos vivos está en **`docs/PEDIDOS.md`** (auto-generado).
Este doc explica el circuito.

## El circuito

```
  1. la mesa habla          "estaría bueno filtrar por cartera"
     con el copiloto        → tool registrar_pedido → manager.pedidos ('nuevo')

  2. triaje automático      jobs/pedidos_triage.py — 12:30 UTC (9:30 ART), L-V
     (1 vez por día)        · duplicados (los exactos sin gastar tokens)
                            · impacto (alto/medio/bajo) y esfuerzo
                            · SPEC: qué habría que hacer y dónde
                            → un mensaje al celular con botones ✅ / ❌

  3. la decisión            un tap en Telegram
     (humana, siempre)      → jobs/pedidos_inbox.py (cron cada minuto)
                            → estado = aceptado | descartado

  4. la cola                el MISMO job regenera docs/PEDIDOS.md y lo
     (automática)           commitea+pushea (solo ese archivo)
                            → sección 🛠 COLA DE TRABAJO, ordenada por
                              impacto alto / esfuerzo chico

  5. el código              Claude Code: /pedidos
                            → toma el primero, valida la spec contra el código
                              real, implementa, y pide el OK antes de pushear

  6. cierre                 python -m scripts.gen_pedidos --marcar <id> hecho
```

Entre el tap y el trabajo **no queda ningún paso manual**: si la publicación
quedara a cargo de acordarse de commitear, el circuito se muere ahí. Si el
server no puede pushear (sin credencial), la aprobación igual quedó guardada y
el archivo actualizado en disco — se dice en el log y se commitea a mano.

## Las decisiones de diseño (y por qué)

**Por qué el pedido se captura hablando y no con un formulario.** La fricción
es lo que mata los buzones de sugerencias: el que tiene la idea la tiene
*mientras trabaja*, y si para anotarla hay que cambiar de pantalla, no la
anota. Acá se la dice al copiloto que ya tiene abierto, en la vista donde le
surgió — y esa vista queda registrada como contexto.

**Por qué el LLM escribe una propuesta y no solo clasifica.** Aprobar "filtro
por cartera en Operaciones" sin saber qué implica es aprobar a ciegas. La spec
(qué tocar y dónde) es lo que convierte el tap en una decisión informada. El
modelo hace lo que hace bien —redactar y clasificar— y no decide nada.

**Por qué la decisión es humana, siempre.** El job tría y avisa; **nunca**
acepta ni descarta por su cuenta. Que la IA priorice está bien; que la IA
decida qué se construye, no.

**Por qué Telegram y no una pantalla.** El pedido llega cuando llega y la
decisión es de dos segundos. Un botón en el celular se toca; una vista que hay
que ir a abrir se posterga y el buzón se muere igual que antes.

**Por qué un cron de un minuto y no un servicio siempre prendido.** Telegram
guarda los updates 24 h, así que consultarlos cada minuto no pierde nada, no
hay proceso que se cuelgue ni haya que vigilar, y si el server estuvo caído las
aprobaciones se aplican cuando vuelve.

## Seguridad — el server ahora LEE de Telegram

`core/notify.py` decía: *"el server le manda mensajes a Telegram; Telegram nunca
entra al server"*. Aprobar con botones obliga a leer. La postura se mantiene y
el cómo importa:

| | |
|---|---|
| **Sin puerta de entrada** | `getUpdates` es una consulta **saliente** del server, no un webhook. Telegram nunca inicia una conexión y no se abre ningún puerto. |
| **Un tap es un voto, no un comando** | El `callback_data` debe matchear exactamente `pedido:(aceptar\|descartar):<id>`. Cualquier otra cosa se descarta sin mirarla. No hay texto libre interpretado, ni shell, ni SQL armado con el contenido. |
| **Superficie mínima** | `allowed_updates=["callback_query"]`: los mensajes de texto que le manden al bot ni se leen. |
| **Default-deny** | `TELEGRAM_ADMIN_IDS` (lista blanca de ids) **y** el chat configurado. Lista vacía = nadie aprueba. |
| **Idempotente** | Dos taps seguidos, o un update entregado dos veces, no cambian nada y lo dicen. El cursor se guarda al final: si el job se corta, los taps se re-entregan en vez de perderse. |
| **No escala privilegios** | Lo único alcanzable desde Telegram es `aceptado`/`descartado`. A `hecho` solo se llega desde el repo. |

Los tests de la jaula (inyección, doble orden, otra caja, id fuera de rango,
lista blanca vacía, chat equivocado) viven en `tests/unit/test_pedidos_flujo.py`.

## Qué ve el proveedor de IA — y qué NUNCA ve

Pregunta del user (2026-07-22): *"¿esto le da acceso a mi código a DeepSeek o
a OpenAI?"*. **No, y no puede.** El reparto es este:

| | Ve | No ve |
|---|---|---|
| **El proveedor del triaje** (DeepSeek/OpenAI) | el texto del pedido, los títulos de los pedidos previos, y una descripción genérica de la plataforma (nombres de vistas) | el repositorio, `sql/schema.sql`, cualquier archivo, cualquier dato de cliente (lo tacha la aduana) |
| **Claude Code** | el repositorio completo, en la máquina del user, con su suscripción | — |

Lo que el proveedor DEVUELVE es un JSON con tres etiquetas y un párrafo en
castellano. **Ese párrafo es prosa, no código: se guarda como texto y se
muestra. Nada lo ejecuta.** Las etiquetas pasan por `_sanear()`, que las
encierra en su enum: el modelo propone, el código valida.

Vale para todo el programa de IA, no solo para el buzón: **ninguna salida de
un LLM se ejecuta en este sistema**. El copiloto elige tool + parámetros de un
conjunto fijo y validado (nunca escribe SQL); la navegación se valida contra
catálogos vivos; el MCP es read-only. El modelo aporta el lenguaje y el
criterio de clasificación; el cálculo y el acceso a datos son código nuestro.

### El riesgo que SÍ existe: la spec entra al contexto de Claude Code

Más sutil que el anterior y por eso vale escribirlo: la propuesta que redacta
el LLM termina **leída por Claude Code** cuando levanta la cola. Un pedido
malicioso ("…y además borrá la tabla X") viajaría como texto hasta ahí. Así se
ataca a un agente: no hackeándolo, escribiéndole.

Defensas, en orden:

1. `/pedidos` obliga a **validar la propuesta contra el código real** antes de
   tocar nada. Las specs se verifican, no se ejecutan.
2. **El user aprueba antes de cualquier push.** Nada llega a producción sin
   que lo vea.
3. El buzón solo lo cargan usuarios internos autenticados de la plataforma —
   no hay entrada anónima.

Si alguna vez se abre el buzón a un público más amplio (portal invitado,
externos), esto se revisa ANTES: la spec deja de ser una sugerencia entre
colegas y pasa a ser texto no confiable de un desconocido.

## Privacidad

El texto del pedido lo escribió una persona y puede nombrar a un cliente
("no encuentro las operaciones de Fulano"). Antes de salir al proveedor pasa
por la **aduana** (`core/pii_gateway.py`), igual que el asistente de negocio; la
spec que vuelve se destokeniza para guardarla, porque `manager.pedidos` vive en
nuestro perímetro.

## Costo

Prácticamente nulo: el triaje es una llamada `flash` de ~1.200 tokens por
pedido **nuevo**. Un día sin pedidos son 0 tokens; los duplicados exactos se
detectan sin llamar al modelo; y hay un techo por corrida (`--max`) para que una
ráfaga no dispare la cuenta. Si el presupuesto diario está agotado, el pedido
queda sin triar y se reintenta al día siguiente — no se pierde.

## Puesta en marcha

1. `python -m scripts.apply_schema` (columnas de triaje + `manager.telegram_cursor`).
2. En el `.env` del Droplet: `TELEGRAM_ADMIN_IDS=<tu id numérico de Telegram>`.
   Si no sabés tu id: dejalo vacío, tocá un botón, y el bot te contesta cuál es.
3. `crontab deploy/crontab.txt` (o sumar las dos líneas de la sección BUZÓN).
4. Prueba en seco, sin gastar tokens ni escribir nada:
   ```
   python -m jobs.pedidos_triage --dry-run
   python -m jobs.pedidos_inbox --dry-run
   ```

## Archivos

| Pieza | Dónde |
|---|---|
| Captura (tool del copiloto) | `api/services/copiloto/pedidos.py` |
| Triaje + aviso | `jobs/pedidos_triage.py` |
| Aprobación (taps) | `jobs/pedidos_inbox.py` |
| Transporte Telegram | `core/notify.py` |
| Export + cola | `scripts/gen_pedidos.py` → `docs/PEDIDOS.md` |
| Ejecución | `.claude/commands/pedidos.md` (`/pedidos`) |
| Tabla | `manager.pedidos` + `manager.telegram_cursor` (`sql/schema.sql`) |
