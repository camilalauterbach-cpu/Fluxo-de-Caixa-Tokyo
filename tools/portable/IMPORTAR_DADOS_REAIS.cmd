@echo off
setlocal
cd /d "%~dp0"
title Tokyo - Importar dados da instalacao original
if not exist "%~dp0python\python.exe" (
  echo O Python incluido nao foi encontrado. Extraia TODO o ZIP antes de abrir.
  pause
  exit /b 1
)
echo Feche a demonstracao com Ctrl+C antes de continuar.
echo Escolha o banco ATUAL do Tokyo original ou uma copia de seguranca recente.
echo A operacao acontece somente neste computador e nao envia seus dados ao GitHub.
echo.
"%~dp0python\python.exe" -I -B "%~dp0importar_dados.py"
echo.
pause
