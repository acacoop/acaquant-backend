Panel exploratorio de MANAGER → AUNESA: trae los movimientos crudos del custodio Aunesa para una cuenta/fecha y los muestra parseados a boletos (op, ticker, cantidad, precio, importe, moneda, plazo) con sus líneas de detalle y la categoría/captura inferida. Sirve para diagnosticar cómo el backend interpreta la data del custodio.

Conecta con: pollea el endpoint exploratorio en vivo de `GET /api/manager/aunesa/...` (router `manager/aunesa.py`, cliente `core/aunesa.py`). Solo manager.
