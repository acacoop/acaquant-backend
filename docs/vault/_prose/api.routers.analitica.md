Router `/api/analitica`: expone como HTTP las herramientas analíticas Tier 1 + Tier 2 (listar curva, serie macro, descomposición de retorno, sensibilidad, canje, carry trade, comparar inversión, opciones). Son thin wrappers para consumo externo (acaquant-web, curl, debugging); el asistente legacy las llamaba directo por service registry sin loopback HTTP.

Conecta con: delega en los services `renta_fija`, `macro`, `descomposicion_retorno`, `sensibilidad`, `canje`, `carry_trade`, `comparar_inversion`, `opciones`, `analitica`; lo monta `api.main`.
