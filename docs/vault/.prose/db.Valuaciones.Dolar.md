Serie de dólar de referencia (MEP histórico) en la base `Valuaciones`. Provee el tipo de cambio para convertir valuaciones y operaciones a USD por fecha.

Conecta con: la lee el helper `api/services/_mep.py` (MEP histórico por fecha) y services como `carry_trade.py`, `scanner.py`, `valuaciones.py`, `operaciones_informes.py`; se ingesta vía `api/routers/ingest.py`.
