Calcula el fair value relativo intra-curva del cierre diario. Por cada curva (tasa_fija, cer) filtra un universo líquido, ajusta una cuadrática TEA = β₀ + β₁·d + β₂·d² (mínimos cuadrados), y para cada bono calcula el residuo en bps (observado vs teórico), su z-score estático (vs σ del universo del día) y temporal (vs últimos 30 cierres). Detecta así bonos caros/baratos relativos.

Encadenado al cron de `snapshot_cierre`. Idempotente (upsert por ts_cierre/curva/ticker).

Conecta con: lee `Trading.SnapshotsCierre`, usa `quant.curve_fit.fit_quadratic`, escribe `Trading.FitParams` (β + R²) y `Trading.FairValueResiduos`. Lo consume `api/services/fair_value.py`.
