Barra de navegación global de la app. Arma el menú (links sueltos HOME/OPERAR/CARTERAS/BACK OFFICE/MANAGER + dropdowns MERCADOS y NEGOCIO) y gatea cada vista por su `module`, alineado con la matriz de roles del backend (`core/roles.py::MODULES`). Un grupo solo aparece si el usuario tiene al menos una vista adentro.

Conecta con: consume los módulos permitidos del usuario (vía `/api/me` / RBAC); si modules es null (dev o backend caído) muestra todo. Se renderiza en el layout raíz de todas las páginas.
