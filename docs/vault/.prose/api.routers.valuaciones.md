Router de performance e historia por cuenta. `GET /consolidado` da una fila por cuenta (valor, base-100, PnL acum, TEM, TEA en ARS y USD) para comparar carteras; `/{id}/serie` la curva diaria del portfolio; `/{id}/mensual` el cierre mensual con flujos externos; `/{id}/posiciones` el ledger cost-basis (PnL realizado/no-realizado, drill-down per-ticker).

Conecta con: delega en `api.services.valuaciones` (lee `Valuaciones.AuM`, `ConsolidadoCuentas`, `NegocioMovimientos`); scope de grupos por cuenta (`verificar_id_cuenta`/`scope_cuentas`); lo consume la vista /aum del frontend.
