@echo off
setlocal EnableExtensions DisableDelayedExpansion
if /i "%~1"=="--tokyo-console" goto tokyo_iniciar
"%ComSpec%" /D /K ""%~f0" --tokyo-console"
exit /b

:tokyo_iniciar
cd /d "%~dp0"
title Tokyo - Trabalho
if not exist "%~dp0python\python.exe" goto tokyo_python_ausente
echo Iniciando o Tokyo...
echo Na primeira abertura, uma janela pede a chave recebida na conversa.
echo.
"%~dp0python\python.exe" -I -B -u "%~dp0desbloquear_tokyo.py"
set "TOKYO_UNLOCK_RESULT=%errorlevel%"
if "%TOKYO_UNLOCK_RESULT%"=="2" goto tokyo_cancelado
if not "%TOKYO_UNLOCK_RESULT%"=="0" goto tokyo_preparo_erro
"%~dp0python\python.exe" -I -B -u "%~dp0abrir_tokyo.py"
set "TOKYO_LAUNCH_RESULT=%errorlevel%"
if not "%TOKYO_LAUNCH_RESULT%"=="0" goto tokyo_abertura_erro
echo.
echo Tokyo encerrado. Seus dados locais foram preservados.
echo Esta janela continua aberta. Voce pode fecha-la pelo X.
exit /b 0

:tokyo_cancelado
echo.
echo A abertura foi cancelada. Nenhuma chave foi salva.
echo Para tentar novamente, abra INICIAR_TOKYO.cmd.
echo Esta janela continua aberta. Voce pode fecha-la pelo X.
exit /b 2

:tokyo_preparo_erro
echo.
echo O aplicativo nao foi iniciado. A mensagem acima permanece nesta janela.
echo Se existir, consulte private\inicializacao.log.
echo Esta janela continua aberta. Voce pode fecha-la pelo X.
exit /b %TOKYO_UNLOCK_RESULT%

:tokyo_abertura_erro
echo.
echo Nao foi possivel abrir o servidor. Consulte a mensagem acima.
echo Se existirem, consulte private\inicializacao.log e private\servidor.log.
echo Este pacote usa Windows 10 ou 11 de 64 bits.
echo Esta janela continua aberta. Voce pode fecha-la pelo X.
exit /b %TOKYO_LAUNCH_RESULT%

:tokyo_python_ausente
echo.
echo O Python incluido nao foi encontrado. Extraia TODO o ZIP antes de abrir.
echo Nao execute este arquivo diretamente de dentro do ZIP.
echo Esta janela continua aberta. Voce pode fecha-la pelo X.
exit /b 1
