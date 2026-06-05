Helpers de formato de montos para el módulo Comercial. `fmtMoney` devuelve el monto compacto con sufijo de magnitud en locale es-AR (k = mil, M = millón, MM = mil millones, B = billón; ej. "$12,6 B", "$50 k"), y `fmtMoneyFull` el monto completo con separador de miles para tooltips. `null`/NaN → "—".

Conecta con: utilidad pura de presentación, sin I/O; la consumen los componentes del Tablero Comercial del frontend para mostrar AuM y volúmenes que vienen de `api.services.comercial`.
