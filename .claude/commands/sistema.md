---
description: Regenera y muestra el plano único del sistema (deploy/SISTEMA.md) desde systemd + crontab
---

Mantené sincronizado el **plano único del sistema** — la fuente de verdad de
todo lo que corre (servicios systemd, motores, crons, topología).

Pasos:

1. **Regenerar** las tablas de inventario desde la fuente real:
   ```
   python -m scripts.gen_sistema
   ```
   Lee `deploy/systemd/*.service` + `deploy/crontab.txt` y actualiza los
   bloques `AUTOGEN` de `deploy/SISTEMA.md`. NO toca la narrativa (topología,
   flujo de datos, bases) — esa es a mano.

2. Si hubo cambios, mostrá el **diff** de `deploy/SISTEMA.md` (`git diff
   deploy/SISTEMA.md`) para que el user vea qué cambió en el sistema.

3. Para responder *"¿cómo está compuesto el sistema?"*, leé/resumí
   `deploy/SISTEMA.md` (topología + servicios + motores + crons).

4. Si el cambio vino de agregar/quitar un servicio o cron, recordá commitear
   `deploy/SISTEMA.md` JUNTO con el cambio de `deploy/`.

Validar sin escribir (CI / pre-deploy): `python -m scripts.gen_sistema --check`
→ falla si el plano quedó desincronizado de los systemd/crontab reales.
