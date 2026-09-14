@echo off
REM Tenencia de la Caja de Valores -> AcaQuant.
REM Requiere AppGate/Okta CONECTADO: las APIs de BYMA no se alcanzan sin el tunel.
cd /d "%~dp0"
python byma_feed.py %*
