Migración one-shot que normaliza el campo VENCIMIENTO en Valuaciones.Assets: saca el sufijo ' 00:00:00' (la hora siempre es medianoche y no aporta) dejando 'YYYY-MM-DD'. No toca placeholders ('-', '', 'NO APLICA') ni valores ya limpios. Es idempotente y por defecto corre en dry-run; con --apply escribe. Tras aplicar hay que re-sincronizar la copia derivada con `python -m scripts.api_migrate assets`.

Conecta con: Valuaciones.Assets (escribe), core.mongo (rw/ro), scripts.api_migrate (resync posterior).
