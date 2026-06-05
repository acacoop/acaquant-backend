Pestaña "Mejoras Precio Dispo" del shell Agro: muestra, por commodity (SOJA, MAIZ, TRIGO), las filas de tasa implícita (TNA, tasa diaria/directa, interés ganado, valor final) y el descalce contra el futuro DLR asociado, valuado también en USD. Pollea cada 5s y permite editar el precio ARS de referencia por commodity.

Conecta con: pollea `GET /api/derivados-agro/mejoras-dispo` (service `mejoras_dispo.py`), que cruza precios spot Agro con futuros DLR.
