Baja series monetarias del BCRA (CER, TAMAR, DOLAR A3500, BADLAR) vía su API de Estadísticas Monetarias y las persiste una colección por serie, upsert por fecha. Crítico: pide hasta hoy+21 días corridos porque el BCRA publica el CER con ~10 hábiles de forward (las otras series simplemente devuelven vacío para fechas futuras). Modo `--today` (rango corto, para el cron diario) o histórico completo desde 2023.

Conecta con: pega a `api.bcra.gob.ar`, escribe `Trading.CER`, `Trading.TAMAR`, `Trading.DOLAR`, `Trading.BADLAR`. El CER forward alimenta a `motor_curvas`/breakevens; corre 22 UTC L-V.
