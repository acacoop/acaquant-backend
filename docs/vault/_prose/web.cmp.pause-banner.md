Banner informativo que avisa al usuario cuando el sistema está en su ventana de pausa nocturna (cluster Atlas + crons de ingesta apagados para ahorrar consumo), entre 04:00 y 11:30 UTC (01:00–08:30 ART). Muestra una cuenta regresiva al próximo "resume"; fuera de la ventana no renderiza nada. Calcula la hora en el cliente y se actualiza cada 30s.

Conecta con: lógica 100% local en el navegador (no pega a la API). Refleja la ventana de pausa real de Atlas/crons definida en deploy/crontab.txt.
