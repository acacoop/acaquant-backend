---
description: Regenera y estampa docs/ACAQUANT.md (el doc oficial) desde systemd + crontab + schema.sql
---

Mantené al día **`docs/ACAQUANT.md`** — EL documento oficial de cómo funciona todo:
qué es, dónde corre, qué procesos hay y cuándo, dónde están los datos, cómo se
protege, cómo se deploya, qué hacer si se rompe.

Pasos:

1. **Regenerar** las tablas ⚙️ y la estampa desde la fuente real:
   ```
   python -m scripts.gen_sistema
   ```
   Lee `deploy/systemd/*.service`, `deploy/crontab.txt` y `sql/schema.sql`, y
   actualiza los bloques `AUTOGEN` (servicios, motores, crons, schemas). Reescribe
   la línea **Última actualización** (fecha, hora de Buenos Aires, huella). NO
   toca la narrativa: esa es a mano, y es corta a propósito.

2. Si hubo cambios, mostrá el **diff** (`git diff docs/ACAQUANT.md`) para que el
   user vea qué cambió en el sistema.

3. Para responder *"¿cómo funciona / cómo está compuesto el sistema?"*, leé y
   resumí `docs/ACAQUANT.md`. Es la posta; si contradice al código, es un bug de
   uno de los dos y se arregla en el mismo commit.

4. Si editaste la narrativa a mano, corré el paso 1 igual: sin re-estampar, el
   CI falla (la huella no coincide). Commiteá el doc JUNTO con el cambio que lo motivó.

Validar sin escribir (CI / pre-deploy): `python -m scripts.gen_sistema --check`
→ falla si las tablas quedaron viejas o si el doc se editó sin re-estampar.
