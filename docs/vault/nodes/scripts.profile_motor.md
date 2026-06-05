---
id: scripts.profile_motor
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/profile_motor.py
---

# scripts/profile_motor

> profile_motor.py — Perfilar un motor always-on en vivo con py-spy.

**Archivo:** `scripts/profile_motor.py`

## Qué hace
Herramienta reusable de profiling de motores always-on con py-spy: se cuelga del proceso por su PID y muestra en qué gasta CPU sin reiniciarlo ni instrumentar el código (ideal para engines/). El wrapper resuelve el PID del servicio systemd (ej. `curvas` → motor_curvas.service) y dispara py-spy en modo top (htop de funciones), record (flamegraph SVG a logs/) o dump (stack de cada thread). Corre EN EL DROPLET como root (ptrace). Uso: `python -m scripts.profile_motor --list | <motor> [--record 30 | --dump]`.

Conecta con: servicios systemd motor_* (vía PID), py-spy; complementa scripts.perf_sweep (services) y scripts.profile_quant (cálculo puro).

_Sin conexiones detectadas mecánicamente._
