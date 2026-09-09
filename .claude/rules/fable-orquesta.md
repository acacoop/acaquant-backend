# REGLA — Con FABLE: se delega SOLO la verificación, nunca la implementación

⚠️ **APLICA SÓLO SI LA SESIÓN PRINCIPAL CORRE CON FABLE** (confirmado con
`get_session` del servidor claude-code-remote, o porque el user lo dijo). Con
Opus o cualquier otro modelo, no aplica.

## Por qué se acotó

La versión anterior mandaba a delegar también la implementación a sub-agentes
con Sonnet. Medido en la sesión del simulador de descuento por LOTE: tres
implementadores (5, 10 y 17 minutos) más dos revisores (4 y 5), en cadena, y el
turno tardó 40 minutos donde antes tardaba 10. Cada sub-agente arranca sin
contexto y relee todo; el ahorro de tokens no compensa el tiempo de pared.
Decisión del user: **el principal escribe el código**.

## Qué SÍ se delega (y solo esto)

| Tarea | Sub-agente |
|---|---|
| Correr las verificaciones previas al push | `pre-deploy-check` (backend) / `pre-push-check` (front) |
| Revisar el diff con ojos frescos antes de commitear | `revisor` |
| Relevar de solo lectura cuando el principal no sabe por dónde empezar | `explorador` / `Explore` |

Los tres son de lectura o de ejecución de comandos: no escriben código. Van en
paralelo cuando se puede (revisor + pre-*-check a la vez), nunca en cadena.

## Qué NO se delega

- **La implementación**, siempre. Ni «el mismo cambio en N archivos».
- **Las decisiones de diseño** y todo cambio que cruce los dos repos.
- **La explicación ejecutiva** (REGLA #3).

## El contrato (vale para los tres de arriba)

1. El sub-agente devuelve **output real** (el comando y lo que imprimió), no
   «pasó».
2. El principal lee el informe y decide; el informe no le llega al user, se
   relata verificado.
3. Un sub-agente no commitea ni pushea, ni pisa `user.name`/`user.email`.
