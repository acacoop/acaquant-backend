Compara el AuM de dos fechas (actual vs anterior) por cuenta: arma el delta de saldo, marca cuentas nuevas/cerradas y soporta filtro por cuenta, operador y moneda (ARS/USD con MEP). Valida que vengan ambas fechas (400 si no) y proxea sin cache.

Conecta con: vista de evolución de AuM del front → este route → backend `GET /api/portfolio/diff` (service `api/services/portfolio.py`).
