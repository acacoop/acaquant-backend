Selector buscable de cuentas que reemplaza al `<select>` nativo en la vista OPERAR. Permite tipear varios dígitos seguidos y filtra por prefijo (escribir "6" muestra las cuentas que arrancan con 6), con navegación por teclado (Enter selecciona, Escape restaura, click-fuera cierra). Las cuentas vacías se ofrecen al final.

Conecta con: recibe la lista de `CuentaDescubierta[]` desde el shell de OPERAR (tipos en `dolar-mep-shared`); no hace fetch propio, solo emite el cambio de cuenta vía `onChange`.
