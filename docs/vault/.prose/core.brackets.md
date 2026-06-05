Implementa brackets de trading: orden de entrada LIMIT + salida automática (take-profit) cuando la entrada se llena. Cuando un order report marca la entrada FILLED, el motor de órdenes dispara la salida (side opuesto, misma cantidad, LIMIT al precio definido). Sin stop-loss. Maneja el ciclo de estados PENDING_ENTRY → EXIT_SENT → COMPLETED (y casos de cancelación/rechazo).

Conecta con: escribe/lee `Operaciones.BracketsLive` (sin TTL); lo procesa `engines/motor_ordenes.py`, que observa execution reports de pyRofex y dispara la pata de salida.
