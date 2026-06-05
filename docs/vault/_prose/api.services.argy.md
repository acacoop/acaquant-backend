Arma el agregado del panel ARGY del frontend: las 5 métricas argentinas (MEP, CCL, canje, caución ARS, caución USD), cada una con su valor live y las variaciones %Día / %7d / %MTD / %YTD calculadas contra el cierre histórico anclado a cada fecha-target. Toma el live del snapshot y matchea cada anchor al último cierre con fecha ≤ target.

Conecta con: lee live de `Valuaciones.DolarSnapshot` y `Trading.CaucionSnapshot` (escritos por `engines.dolares` / `engines.caucion`) e histórico de `Valuaciones.Dolar` y `Trading.Caucion`; lo consume el router `/api/argy`.
