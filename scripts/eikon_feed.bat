@echo off
rem Lanzador del feed Eikon — doble click ACA (no en el .py).
rem La ventana la sostiene este .bat: NO PUEDE cerrarse sola, pase lo que pase
rem (error de sintaxis, falta Python, falta libreria, lo que sea: queda escrito).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py eikon_feed_simple.py
) else (
  python eikon_feed_simple.py
)
echo.
echo ============================================
echo  El script termino. Lo de arriba es el motivo.
echo ============================================
pause
