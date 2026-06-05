Herramienta reusable para corregir un valor puntual de cualquier nivel_1..5 en Clientes.Comitentes (ej. tipeo `INSTITUCIONAL` → `INSTITUCIONALES`) sin tocar el resto del dataset. Match exacto y case-sensitive (los segmentos viven en MAYÚSCULAS por convención). Idempotente: si no quedan docs con el valor `--de`, no escribe. Por defecto dry-run; con --apply ejecuta. Uso: `python -m scripts.rename_nivel_valor --campo nivel_1 --de X --a Y [--apply]`.

Conecta con: Clientes.Comitentes (escribe nivel_1..5), core.mongo (rw); campos usados por la segmentación comercial / tablero comercial.
