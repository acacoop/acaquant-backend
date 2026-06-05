Proxy live de la sección Títulos/Mercado del Back Office: reenvía el parámetro opcional `fecha` y devuelve los movimientos sin cache para que el polling vea los nuevos boletos apenas entran.

Conecta con: vista Back Office del front → este route → backend `GET /api/back-office/titulos-mercado` (service `api/services/back_office_titulos.py`, lee `CashFlow.NegocioMovimientos`).
