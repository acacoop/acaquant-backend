# REGLA — El modelo CARO diseña, los BARATOS ejecutan

Aplica siempre que la sesión principal corra con **Fable** (o cualquier modelo de
la capa cara: Opus). El principal es el ARQUITECTO: lee, decide, especifica,
revisa y le explica al usuario. La ejecución mecánica va a sub-agentes con un
modelo más barato. Doc oficial: code.claude.com/docs/en/costs («Sonnet resuelve
la mayoría de las tareas de código; reservar el modelo grande para decisiones de
arquitectura») y code.claude.com/docs/en/sub-agents.

## Qué se delega y a quién

| Tarea | Sub-agente |
|---|---|
| Relevar, buscar, listar («dónde se lee tal campo», «qué llama a X») | `explorador` (haiku, solo lectura) |
| Implementar un cambio YA especificado | `implementador` (sonnet) |
| Correr las verificaciones (imports, ruff, perf_scan, tests) | `pre-deploy-check` (sonnet) |
| Revisar un diff con ojos frescos antes de pushear | `revisor` (sonnet) |
| El mismo cambio en N archivos | N `implementador` en paralelo |

El default de los sub-agentes SIN modelo declarado es Sonnet
(`CLAUDE_CODE_SUBAGENT_MODEL` en `settings.json` → `env`). Para subir uno a
Fable hay que pedirlo explícito: el barato es el default, el caro se justifica.

## Qué NO se delega

- **Las decisiones de diseño** y cualquier cambio que cruce los dos repos
  (REGLA #9: dos mitades coherentes cada una consigo misma y no entre sí).
- **Lo que tiene la especificación ambigua.** Un modelo menor rellena los huecos
  suponiendo, que es justo lo que prohíbe la REGLA #2. Si la spec no se puede
  escribir completa, la tarea no se delega: se hace en el principal.
- **La revisión final** del trabajo del sub-agente y la explicación ejecutiva
  (REGLA #3). El ahorro real es que el modelo caro LEE 200 líneas de diff en vez
  de escribirlas.

## El contrato de delegación

1. **La spec es completa**: archivos por ruta, función, comportamiento esperado,
   qué tests deben pasar, qué NO tocar, qué regla del repo aplica.
2. **El sub-agente devuelve tres cosas**: qué cambió, qué corrió y con qué
   resultado (output real, no «pasó»), y qué NO pudo hacer o dónde dudó. Nunca
   «listo».
3. **El principal lee el diff** antes de dar por cerrado. El informe del
   sub-agente no le llega al usuario: se relata verificado.
4. **Un sub-agente no commitea ni pushea.** Lo hace el principal, después de
   revisar. Los hooks de `git push` corren igual.
