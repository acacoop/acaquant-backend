Cron L-V a 20:25 UTC (17:25 ART) que corre la cadena de cierre: `jobs.snapshot_cierre` (materializa el cierre diario por bono leyendo `MarketSnapshot` → `Trading.SnapshotsCierre`) + `jobs.fair_value` (fit cuadrático + residuos + z-scores).

Conecta con: ejecuta `jobs/snapshot_cierre.py` + `jobs/fair_value.py`; lee `Trading.MarketSnapshot`, escribe `Trading.SnapshotsCierre`; el fair_value usa `api/services/fair_value.py`. Timeout 25m.
