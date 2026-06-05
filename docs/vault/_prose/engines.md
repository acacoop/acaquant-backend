Paquete `engines/` — marcador de paquete Python (`__init__.py`), sin lógica propia. Agrupa los motores de mercado always-on (WS pyRofex → Mongo) que corren en rueda L-V 13-20 UTC controlados por cron/systemd.

Conecta con: define el namespace `engines.*` que importan los servicios (`core/`, `quant/`) y permite ejecutar cada motor con `python -m engines.<motor>` desde la raíz.
