Vista principal de Opciones GGAL: chain CALL/PUT compacta + tabla de estrategias armadas, con detalle de payoff, escenarios, post-trade lab y gráficos históricos (costo, opción, griegas). Pollea la chain cada 30 s (uso bajo, se baja la carga serverless).

Conecta con: consume el snapshot de opciones del backend (motor `engines.options` → service `api.services.opciones`); compone `web.cmp.estrategias-tabla`, `escenarios-tabla`, payoff/históricos; calcula estrategias con `lib/estrategias`.
