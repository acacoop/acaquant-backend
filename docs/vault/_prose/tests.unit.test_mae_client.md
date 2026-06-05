Valida el cliente MAE (`core/mae.py`) sin red real, mockeando requests: que falle sin API key, que `_base_url` elija prod/uat/fallback según MAE_ENV, que `get_repo` arme la request con header `x-api-key` y paginación correcta, que la iteración de páginas corte en vacía, y el mapeo de errores (401/403→MaeAuthError, 429→MaeRateLimitError, 500/red caída/payload no-JSON→MaeError).

Conecta con: importa `core.mae`; red de seguridad del cliente del feed MAE MarketData (repo/caución, dólar mayorista).
