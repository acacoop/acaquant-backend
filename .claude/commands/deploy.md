---
description: Push a main y pull+restart en el Droplet
---

Deploy del backend al Droplet. Flujo seguro: pushear local → pullear en el server → restart del `api.service`. **No toca motores de mercado** (corren en su propio schedule via cron, y reiniciarlos en rueda corta el feed de precios de la mesa).

En el Droplet el equivalente de un comando es `bash deploy/deploy.sh`, que además corre `apply_schema` y el smoke. **Tampoco reinicia motores**: si el cambio tocó `engines/`/`core/`/`quant/`, los nombra con el comando exacto para correr fuera de rueda. `--con-motores` solo si el user lo pide.

Pasos:

1. **Chequeo previo** — `git status` local:
   - Si hay cambios sin commitear, parar y preguntar si quiere commitearlos primero.
   - Si hay commits sin pushear, OK seguir (los pusheamos en el paso 3).
   - `ruff check .` — si falla, parar. Deploy con ruff roto = CI rojo.
2. **Confirmación explícita del usuario** antes de tocar prod. Mostrá:
   - Branch actual.
   - Commits que se van a pushear (`git log origin/main..HEAD --oneline`).
   - Qué se va a reiniciar (`api.service` solamente, no motores).
3. `git push origin main`.
4. En el Droplet (via ssh, asumiendo que el usuario tiene la key configurada):
   - `cd /root/TradingAV && git pull`
   - `systemctl restart api.service`
   - `systemctl status api.service --no-pager -n 5` para confirmar que levantó.
5. Smoke post-deploy: `curl -s https://api.acaquant.com/api/health` (o `localhost:8000` en el server).
6. Si algo falla en el restart, NO hagas rollback automático. Reportá el error y esperá decisión del usuario — puede que quiera ver logs primero (`journalctl -u api.service -n 50`).

**No pusheás ni deployás sin el "OK" del usuario.** Este comando es sólo el wrapper del procedimiento — cada paso destructivo pide confirmación.
