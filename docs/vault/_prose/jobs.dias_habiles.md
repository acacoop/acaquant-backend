Carga el calendario de días hábiles argentinos del año (excluye fines de semana y feriados vía librería `holidays`) a `Trading.DiasHabiles`. Es la fuente de verdad del calendario hábil que usan otros jobs y motores para contar plazos. Idempotente (upsert por fecha).

Se corre puntualmente (típicamente al inicio del año / cuando hace falta refrescar el calendario).

Conecta con: escribe `Trading.DiasHabiles`. Lo consumen `jobs.cleanup_curvas` y los cálculos de breakevens/anualización que necesitan contar días hábiles.
