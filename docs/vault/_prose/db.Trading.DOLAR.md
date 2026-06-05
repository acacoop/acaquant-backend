Serie histórica del dólar oficial/mayorista (fixing diario BCRA A3500) en la base `Trading`. Es la fuente para las series macro (`dolar_oficial` / `dolar_mayorista`) cuando se necesita histórico, a diferencia del feed live.

Conecta con: la escribe `jobs/bcra.py`; la leen `api/services/macro.py`, `carry_trade.py` y los motores (`engines/curvas.py`, `futuros_dlr.py`) que necesitan el oficial histórico.
