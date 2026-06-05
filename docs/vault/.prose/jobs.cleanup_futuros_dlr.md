Borra de `Trading.FuturosDLRSnapshot` los contratos DLR (Dólar A3500) ya vencidos. El motor `engines/futuros_dlr.py` hace upsert por ticker y nunca borra, así que al vencer un contrato el doc queda fantasma con su última info: este job lo limpia. Tiene `--dry` que solo lista.

Corre cada mañana antes de que arranque el motor de futuros DLR.

Conecta con: lee/borra de `Trading.FuturosDLRSnapshot`. Complementa a `engines.futuros_dlr` (que escribe esa colección) y sigue la misma cadencia que `jobs.cleanup_curvas`.
