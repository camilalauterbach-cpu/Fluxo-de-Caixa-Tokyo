@echo off
setlocal
cd /d "%~dp0"
title Tokyo - Demonstracao
echo Iniciando o Tokyo de demonstracao...
echo A instalacao e o banco financeiro existentes nao serao alterados.
echo.
if not exist "%~dp0python\python.exe" (
  echo O Python incluido nao foi encontrado. Extraia TODO o ZIP antes de abrir.
  echo Nao execute este arquivo diretamente de dentro do ZIP.
  pause
  exit /b 1
)
"%~dp0python\python.exe" -I -B "%~dp0abrir_tokyo.py"
if errorlevel 1 (
  echo.
  echo Se o Windows impedir a execucao ou o Python incluido nao abrir,
  echo confirme que voce esta usando Windows 10 ou 11 de 64 bits.
  echo Alternativa: instale Python 3.12 ou posterior de https://www.python.org/downloads/windows/
  echo e execute: py -3 -I -B abrir_tokyo.py
)
echo.
pause
