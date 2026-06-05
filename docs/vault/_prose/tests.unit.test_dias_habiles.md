Valida la generación del calendario hábil argentino (`jobs/dias_habiles.py::generar_dias_habiles`): que excluya fines de semana y feriados clave (Navidad, Año Nuevo, 25 de Mayo), devuelva la cantidad razonable (240-255 días/año), en orden ascendente y formato ISO. El calendario hábil es la base del settlement T-10 del CER y de toda la navegación de fechas de la renta fija.

Conecta con: importa `jobs.dias_habiles`; red de seguridad del job que pueble el calendario hábil consumido por `engines/curvas.py`.
