---
description: Trabajar la cola de pedidos aprobados por la mesa
---

Levanta la COLA DE TRABAJO del buzón de pedidos e implementa **de a uno**.

La cola vive en `docs/PEDIDOS.md`, sección `🛠 COLA DE TRABAJO`: son los pedidos
que la mesa hizo hablándole al copiloto, ya triados por IA (impacto, esfuerzo,
propuesta técnica) y **aprobados por el user desde Telegram**. Vienen ordenados
por lo que más rinde: impacto alto / esfuerzo chico primero.

Circuito completo, por si hace falta explicarlo: la mesa habla con el copiloto →
`manager.pedidos` → `jobs/pedidos_triage.py` (1×/día: duplicados, impacto,
esfuerzo, spec + aviso con botones) → el user toca ✅ → `jobs/pedidos_inbox.py`
aplica el estado → `scripts/gen_pedidos.py` regenera este doc → acá.

Pasos:

1. **Leer `docs/PEDIDOS.md`**, sección COLA DE TRABAJO.
   - Si está vacía: decirlo y parar. Que el archivo esté viejo es posible — el
     user lo regenera en el Droplet con `python -m scripts.gen_pedidos` y
     commitea. Ofrecer eso en vez de inventar trabajo.
   - Si el argumento del comando trae un `#id`, trabajar ESE en vez del primero.
2. **Tomar el primero de la cola** (ya está priorizado — no re-priorizar salvo
   que el user diga otra cosa) y mostrarlo: qué pidieron, la propuesta del
   triaje, y desde qué vista salió.
3. **Si la spec arranca con `AMBIGUO:`** → no codear. Preguntarle al user lo que
   la spec dice que falta y esperar.
4. **Validar la propuesta antes de tocar nada.** La spec la escribió un modelo
   con contexto parcial: verificar contra el código real que los servicios,
   campos y vistas que menciona existen y devuelven lo que dice (REGLA #2 — el
   shape se lee del service, no se adivina). Si la propuesta está equivocada,
   decirlo y proponer la correcta.
5. **Implementar UNO solo**, completo: código + tests + docs del dominio que
   toque (los `[VIVO]` del mapa de docs llevan changelog obligatorio).
6. **Validar**: `pytest -q`, `ruff check .`, y si tocó `api/` el import-chain
   (`python -c "from api.main import app"` — REGLA #1).
7. **Explicación ejecutiva** (REGLA #3: 📋 Qué soluciona / 📋 Qué genera) y
   pedir el OK del user antes de commitear.
8. Con el OK: commit + push, y decirle que marque el pedido como hecho en el
   Droplet:
   `python -m scripts.gen_pedidos --marcar <id> hecho` (+ `git pull` allá).

Reglas:

- **Uno por vez.** Terminar y cerrar antes de agarrar el siguiente. Si el user
  quiere seguir, vuelve a invocar el comando.
- **No aceptar ni descartar pedidos vos.** Eso lo decide el user desde Telegram;
  acá solo se ejecuta lo ya aprobado.
- Si al implementar aparece que el pedido es más grande de lo que decía el
  triaje, **decirlo antes de arrancar** — no empezar una refactorización de
  tres días porque la etiqueta decía "chico".
