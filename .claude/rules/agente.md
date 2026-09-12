---
paths:
  - "agente/**"
  - "jobs/agente.py"
  - "docs/AGENT.md"
  - "tests/unit/test_agente.py"
---
# EL AV AGENT — lo que hay que saber antes de tocarlo

> **⚡ EL AV AGENT → `docs/AGENT.md` (LEER al arrancar la sesión).**
> **Se rehízo ENTERO el 2026-08-24.** El agente viejo eran 37 services / 24.319
> líneas / 18 tablas / **cuatro relojes** haciendo lo mismo con distinta
> frecuencia, y el user lo resumió así: *«tiene muchas cosas positivas pero en
> su conjunto es algo totalmente inútil»*. Lo que se cambió no fueron los bugs:
> fue que **no hubiera dónde equivocarse**.
>
> **El agente vive en `agente/` (paquete raíz, al lado de `engines/` y `jobs/`).**
>
> **CUATRO tablas llevan el MODELO** del agente, y ninguna otra decide si algo
> es un problema: `habilidades` (el catálogo — y **cuándo corrió cada una**) ·
> `hallazgos` (los eventos, con `id` único) · `reincidencias` (**la que DEBE
> estar vacía**) · `acciones` (el libro: qué escribió, de qué valor a qué valor).
>
> ⚠️ El schema tiene MÁS tablas que esas cuatro (cuántas, lo dice
> `sql/schema.sql` — acá decía «nueve» y ya eran once): las demás son de
> INFRAESTRUCTURA y ninguna nace de un detector — `latido` (el pulso del daemon,
> UNA fila) · `silenciados` (lo que una persona mandó a callar; **no borra el
> hallazgo, evita crearlo de nuevo**) · `db_peso` (la serie del tamaño de la
> base) · `avisos_dirigidos` (la bandeja hacia un usuario) · `tasa_1816` (la
> lista de prioridad) · `pulso_cliente` (lo que reporta el navegador) ·
> `explicaciones` (el caché de «explicámelo»). Decir «cuatro» a secas hacía que
> `silenciados` pareciera no existir, y es la que explica por qué un problema
> real no aparece en pantalla.
>
> **Sumar una habilidad es UNA fila en `agente/catalogo.py`.** No hay que tocar
> un reloj, ni una lista de tipos, ni un mapa de dominios, ni un test que
> recuerde declararla — en el agente viejo eran CINCO listas paralelas en dos
> archivos.
>
> **UN reloj**: el daemon `agente.service` (`jobs/agente.py`). Cada habilidad
> declara su ritmo y su ventana; el motor pregunta a quién le toca. El único
> cron que queda es `agente_tasa` (cuesta créditos de 1816).
>
> **Los invariantes (`AGENT.md` §8), congelados por `tests/unit/test_agente.py`:**
> 1. Una habilidad que no corrió **no cierra nada**. Solo un resultado `ok`
>    puede cerrar por ausencia: una corrida ciega que cierra 40 problemas deja
>    el tablero en verde justo el día que menos ve. **Y de a uno**: un sujeto
>    que no se pudo mirar vuelve como `tipos.NoMirado` y ni se crea ni se
>    cierra (§0.fa). Nunca se juzga con una foto vieja.
> 2. Un hallazgo **sin `que_hacer` no se guarda** (CHECK en la base).
> 3. Todo lo que se muestra lleva **fecha y hora**.
> 4. **Solo lo cerrado POR ACCIÓN puede reincidir.** Ante la duda, por ausencia.
> 5. `agente/registro.py` es **la única puerta** que escribe hallazgos.
> 6. «¿Qué hago si no puedo mirar?» se contesta **una vez, en el motor** — los
>    detectores levantan `SinDatos` y no deciden nada.
> 7. Cada habilidad **declara su arreglo, o declara que no tiene**.
> 8. Ningún detector vive adentro de un job ajeno.
> 9. A ENCONTRÓ **solo entra lo que tiene arreglo**; lo demás es aviso y vive en AHORA.
> 10. **Un arreglo ESCRIBE.** Un botón que vuelve a mirar no es un arreglo.
> 11. Ninguna pantalla deriva nada; **ningún contador se suma en el navegador**.
> 12. **No encontrar el sujeto NO prueba que no exista.** Para CADUCAR un
>     hallazgo hace falta una fuente que AFIRME la baja, con fecha; si dos
>     fuentes se contradicen, es «no sé» y no se cierra nada.
> 13. **El agente no se autoevalúa**: sin votos, sin eval set, sin confianza.
> 14. **Lo automático entra por la misma puerta** (`arreglos.aplicar`) y firma
>     como `tipos.ACTOR_AGENTE`; una regla se aplica sola sólo si su habilidad
>     lo declara en `automatico`.
> 15. **El agente avisa de lo NUESTRO. Un hecho del mundo no es un hallazgo: es
>     el SILENCIO** (§0.eu). Un `que_hacer` que empieza diciendo que no hay nada
>     que hacer no es una regla mal escrita: no es una regla — lo rechaza
>     `tipos.Hallazgo`, no un test. ⚠️ Callar sólo vale si lo NUESTRO se
>     descartó primero: en `bono_sin_precio` el silencio significa «iliquidez»
>     porque las tres causas propias siguen cantando en `alta`.
>
> **Las pantallas** (`/api/agente`, admin-only, REGLA #8 congelada por test).
> TRES son el ciclo de trabajo: **AHORA** = hallazgos de HOY sin leer y sin
> resolver (informativo; el único botón es «leído», que **no resuelve**) ·
> **ENCONTRÓ** = lo abierto que tiene arreglo · **HISTORIAL** = el libro,
> paginado del backend. Y tres son de lectura: **PATRONES** (los crónicos —
> §6.10, lo que pasa SIEMPRE es una configuración mal puesta, no un incidente) ·
> **HABILIDADES** (qué sabe hacer y **cuándo miró cada cosa**) · **LAB** (el
> asistente conversacional, `asistente/`). Todo el modal viaja en UN request (`/vista`)
> para que se dibuje con UNA sola noción de «ahora».
>
> **`docs/AGENT.md` tiene DOS PARTES y no se confunden.** La **A manda**: es la
> especificación viva —el modelo, el motor, las pantallas, los invariantes—, y
> si el código y ella se contradicen, es un bug de una de las dos. La **B es el
> diario**: cada §0.x es un bug real y explica por qué las cosas quedaron como
> quedaron, **no cómo funciona el agente hoy**.
>
> ⚠️ Ni la parte A ni este archivo llevan el DDL ni la lista de habilidades:
> **el schema manda desde `sql/schema.sql` y el catálogo desde
> `agente/catalogo.py`.** Un conteo escrito a mano acá queda viejo sin que nada
> falle — lo congela `test_ningun_conteo_de_habilidades_quedo_viejo`.


## ⚠️ REGLA #10 — LEY DE CONEXIÓN del AV AGENT: nada nuevo queda suelto

**Irrompible** (pedido del user, 2026-08-23: *«todo lo nuevo que se desarrolle
no tiene que estar suelto como si nada — acá todo se tiene que conectar»*).

En **AGENT 2.0** esta regla dejó de depender de que alguien se acuerde: la
estructura la cumple sola. Toda funcionalidad nueva del agente es **una fila en
`agente/catalogo.py`**, y esa fila obliga a declarar las cinco cosas:

1. **QUÉ mira**, en castellano — sin eso el dataclass no se construye.
2. **CUÁNDO** — su ritmo y su ventana. El motor lee de ahí; no hay reloj aparte.
3. **QUÉ arreglo** tiene cada una de sus reglas, **o que no tiene ninguno**.
   Vacío es una declaración explícita, no un olvido.
4. **DÓNDE escribe** lo que encuentra: por `agente/registro.py` y por ningún
   otro lado (un test prohíbe el resto).
5. **QUÉ HACER** con cada hallazgo — un hallazgo sin `que_hacer` no se guarda,
   y eso lo exige un CHECK de la base.

Historia de por qué hizo falta escribirla: `docs/AGENT.md` §0.cw–§0.cx.
Cómo se cumple hoy: `docs/AGENT.md` §3 y §8.

