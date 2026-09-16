---
paths:
  - "deploy/**"
  - "scripts/gen_sistema.py"
  - ".claude/commands/deploy.md"
---
# Plano del sistema y deploy

## El doc oficial — `docs/ACAQUANT.md`

**Es el único documento que tiene la posta de cómo funciona todo** (qué es, dónde
corre, procesos y horarios, datos, seguridad, IA, deploy, incidentes). Privado, sin
credenciales, en palabras simples. Si contradice al código, es un bug de uno de los
dos. **Si agregás / quitás / modificás un servicio systemd, un cron o un schema**
(tocás `deploy/systemd/*.service`, `deploy/crontab.txt` o `sql/schema.sql`), en el
MISMO cambio regenerá el doc:

```bash
python -m scripts.gen_sistema          # regenera las tablas ⚙️ + la línea "Última actualización"
python -m scripts.gen_sistema --check  # falla si las tablas quedaron viejas o el doc se editó sin re-estampar
```

Las tablas ⚙️ (servicios, motores, crons, schemas) salen de la fuente real → no
pueden mentir. La primera línea lleva fecha, hora y una huella del contenido: el CI
compara la huella, así «siempre tiene fecha» es un test y no un deseo. La narrativa
se mantiene a mano y tiene que caber en una pantalla: el detalle va al manual del
dominio. Un schema nuevo necesita su fila en `gen_sistema._SCHEMAS` (qué guarda,
quién escribe) o aparece marcado. Skill: `/sistema`.


## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend, **un solo comando en el Droplet**: `cd /root/TradingAV && git pull && bash deploy/deploy.sh` (pull → `apply_schema` → **restart de api.service + agente.service Y NADA MÁS** → smoke a `/api/health`, cortando al primer fallo; `--sin-schema` saltea el schema). También existe la skill `/deploy` como wrapper del procedimiento. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

> **⚠️ EL DEPLOY NO REINICIA LOS MOTORES** (regla del user, 2026-08-18: *«no puedo
> estar reiniciando todos los motores en vivo… antes era git pull y luego reinicio
> la API, con eso estamos»*). Reiniciar un motor EN RUEDA corta el feed de precios
> de la mesa, y **el 95% de los deploys tocan la API y no los motores**: pagar ese
> corte en cada entrega es puro costo. `deploy.sh` llamaba a `restart_all.sh`, que
> hacía `try-restart` de todos los motores activos — eso se sacó; la skill
> `/deploy` ya decía «no toca motores» y el script se había desviado.
>
> El script **detecta** si el código nuevo tocó `engines/`, `core/`, `quant/` o
> `config.py` y lo **avisa nombrando los motores activos con el comando exacto**,
> pero **no los reinicia**: enterarse tres días después de que un motor corre
> código viejo es peor que el aviso, y reiniciar en rueda es una decisión de la
> mesa, no un efecto secundario. Para hacerlo igual: `--con-motores`, o
> `systemctl try-restart motor_X.service` a mano **fuera de rueda** (no 13-20 UTC
> L-V). `deploy/restart_all.sh` queda para ese caso explícito.
>
> **Al entregar trabajo (REGLA #0), el bloque copy-paste NUNCA lleva
> `--con-motores`** salvo que el cambio toque un motor y el user lo pida.

