@echo off
setlocal
cd /d "%~dp0"
title Tokyo - Trabalho
if not exist "%~dp0python\python.exe" (
  echo O Python incluido nao foi encontrado. Extraia TODO o ZIP antes de abrir.
  echo Nao execute este arquivo diretamente de dentro do ZIP.
  pause
  exit /b 1
)
echo Iniciando o Tokyo...
echo Na primeira abertura, cole a chave do pacote recebida na conversa.
echo.
"%~dp0python\python.exe" -I -B "%~dp0desbloquear_tokyo.py"
if errorlevel 1 (
  echo.
  echo O aplicativo nao foi iniciado. Leia a mensagem acima e tente novamente.
  pause
  exit /b 1
)
"%~dp0python\python.exe" -I -B "%~dp0abrir_tokyo.py"
if errorlevel 1 (
  echo.
  echo Consulte a mensagem acima e o arquivo private\servidor.log.
  echo Este pacote usa Windows 10 ou 11 de 64 bits.
)
echo.
pause
