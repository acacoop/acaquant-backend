---
description: Estado de los motores de mercado en el Droplet (systemctl) + última actividad en Mongo
---

Diagnóstico rápido del estado de los motores de mercado. Responde "¿está todo corriendo?" en una sola vista sin que el usuario tenga que hacer 8 `systemctl status`.

Pasos:

1. **Horario**. Primero chequear hora UTC. Si está fuera de la ventana 13:00–20:05 UTC (L-V), los motores están stopped por cron → NO es un bug. Avisar y detenerse.
2. **Estado systemd** (vía ssh al Droplet):
   ```
   ssh root@droplet 'for s in motor_rofex motor_options motor_curvas motor_forwards motor_breakevens motor_caucion motor_futuros_dlr motor_dolares; do echo "=== $s ==="; systemctl is-active $s.service; done'
   ```
3. **Actividad reciente en Mongo** — para cada motor, query a la colección destino:
   - `motor_rofex` → último `Trading.TimeSales` (cualquier ticker).
   - `motor_curvas` → último doc con `duration` seteada.
   - `motor_options` → último `Opciones.OptionsSnapshot.updated_at`.
   - `motor_forwards` → `Trading.ForwardsLive.updated_at`.
   - `motor_breakevens` → `Trading.BreakevensLive.updated_at`.
   - `motor_caucion` → `Trading.CaucionSnapshot.updated_at`.
   - `motor_futuros_dlr` → `Trading.FuturosDLRSnapshot.updated_at`.
   - `motor_dolares` → `Valuaciones.DolarSnapshot.updated_at`.

   Si el timestamp tiene más de 30s (o 1 min para los más lentos), el motor está vivo en systemd pero sin producir — suele ser WS caído. Bandera amarilla.
4. **Resumen**:
   ```
   MOTOR              SYSTEMD   ÚLTIMA ACTIVIDAD   ESTADO
   motor_rofex        active    14:03:22 (2s)      ✓
   motor_curvas       active    14:03:18 (6s)      ✓
   motor_options      failed    —                  ✗
   motor_forwards     active    13:45:01 (18min)   ⚠  WS stale?
   ...
   ```
5. Si alguno está `failed`: sugerir `journalctl -u <motor>.service -n 100 --no-pager` y aplicar el skill `debug-motor` para el playbook completo. **No reiniciar sin confirmación del usuario.**

Consideraciones:
- Atlas pausado 04:00–11:20 UTC → las queries a Mongo van a fallar. Reportar "Atlas pausado" y omitir la parte de actividad; el systemd sí se puede checkear siempre.
- No asumir ssh key configurada — si falla, pedirle al usuario que corra los comandos manualmente y pegue el output.
