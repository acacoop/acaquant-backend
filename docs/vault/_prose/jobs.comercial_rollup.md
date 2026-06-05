Pre-agrega la data del Tablero Comercial al grano {fecha, id_cuenta} → {volumen pesificado ARS, n_ops, arancel}, combinando volumen de `CashFlow.NegocioMovimientos` con aranceles de `CashFlow.Operaciones`. Resuelve el problema de que el informe comercial escaneaba toda la historia sin filtro (~338k+488k docs, 2.4s, desalojaba el cache del M10): pasa a leer ~40k filas indexadas.

Default incremental (últimos 7 días, para boletos que llegan tarde); `--full` hace rebuild completo con swap atómico.

Conecta con: lee `CashFlow.NegocioMovimientos` + `CashFlow.Operaciones`, escribe `Clientes.ComercialCache`. Reusa `api.services.comercial` y `_negocio_futuros`. Lo consumen las vistas comerciales (`api/services/comercial.py`).
