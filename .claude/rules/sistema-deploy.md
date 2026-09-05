---
paths:
  - "deploy/**"
  - "scripts/gen_sistema.py"
  - ".claude/commands/deploy.md"
---
# Plano del sistema y deploy

## Plano del sistema — `deploy/SISTEMA.md`

Fuente de verdad de TODO lo que corre: servicios systemd, motores, crons y
cómo se conectan. **Si agregás / quitás / modificás un servicio systemd o un
cron** (tocás `deploy/systemd/*.service` o `deploy/crontab.txt`), en el MISMO
cambio regenerá el plano:

```bash
python -m scripts.gen_sistema          # regenera las tablas (no editar a mano entre marcadores AUTOGEN)
python -m scripts.gen_sistema --check  # falla si SISTEMA.md quedó desincronizado
```

El inventario (servicios/motores/crons) es auto-generado desde la fuente
real → no puede mentir. La narrativa (topología, flujo de datos, bases) se
mantiene a mano. Si cambió cómo se conectan los servicios, actualizá esa
parte también. Skill: `/sistema`.


## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend, **un solo comando en el Droplet**: `cd /root/TradingAV && git pull && bash deploy/deploy.sh` (pull → `apply_schema` → **restart de api.service Y NADA MÁS** → smoke a `/api/health`, cortando al primer fallo; `--sin-schema` saltea el schema). También existe la skill `/deploy` como wrapper del procedimiento. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

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

