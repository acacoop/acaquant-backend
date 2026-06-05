Cron L-V a 12:30 UTC (09:30 ART, antes de abrir los motores) que corre `jobs.cleanup_curvas`: borra de `Trading.Curvas` los instrumentos ya vencidos para que los motores no los suscriban.

Conecta con: ejecuta `jobs/cleanup_curvas.py`; escribe (delete) en `Trading.Curvas`, fuente que leen motor_curvas/breakevens/forwards. Timeout 10m.
