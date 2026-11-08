@echo off
setlocal EnableExtensions

if /i "%~1"=="open" goto open_entry
if /i "%~1"=="manage" goto manage_entry
if /i "%~1"=="queue" goto queue_entry
if /i "%~1"=="validate" goto validate_entry
if /i "%~1"=="--help" goto help
if /i "%~1"=="-h" goto help
if not "%~1"=="" goto help

:menu
cls
echo Workbench - ambiente local de projetos
echo.
echo 1  Abrir Workbench
echo 2  Gerenciar instalacao
echo 3  Baterias de tarefas (execucao pode usar LLM)
echo 4  Validar o codigo do repositorio (sem LLM)
echo 0  Sair
echo    A execucao da bateria pode chamar a LLM configurada e gerar custo.
echo.
choice /c 12340 /n /m "Escolha uma opcao: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto menu_validate
if errorlevel 3 goto menu_queue
if errorlevel 2 goto menu_manage
if errorlevel 1 goto menu_open
goto menu

:menu_open
call "%~f0" open
goto menu

:menu_manage
call "%~f0" manage
goto menu

:menu_queue
call "%~f0" queue
goto menu

:menu_validate
call "%~f0" validate
goto menu

:open_entry
if /i "%~2"=="--help" goto open_help
if /i "%~2"=="-h" goto open_help
set "MANAGED=%~dp0.workbench-managed\current.json"
set "LEGACY=%~dp0.workbench-installed-f1-20261022-225209"
if exist "%MANAGED%" goto open_managed_args
if not exist "%LEGACY%\runtime\Scripts\python.exe" goto open_missing

echo Instalacao gerenciada ausente; abrindo a instalacao F1 preservada.
echo Para migrar, escolha Gerenciar instalacao no menu Workbench.
set "PYTHONPATH="
set "PYTHONHOME="
pushd "%LEGACY%"
if errorlevel 1 goto legacy_open_error
"runtime\Scripts\python.exe" -I "launch_installed.py" %2 %3 %4 %5 %6 %7 %8 %9
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" goto legacy_open_error
exit /b 0

:open_missing
echo Nenhuma instalacao gerenciada ativa ou instalacao F1 recuperavel foi encontrada.
echo Use Workbench.cmd e escolha Gerenciar instalacao. Nenhum download sera iniciado.
pause
exit /b 1

:legacy_open_error
echo O Workbench F1 encontrou um erro. Verifique os logs na pasta sessions.
pause
exit /b 1

:open_managed_args
call :open_managed %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:open_managed
set "WORKSPACE="
set "PROFILE="

:parse_open_args
if "%~1"=="" goto open_managed_run
if /i "%~1"=="--help" goto open_help
if /i "%~1"=="-h" goto open_help
if /i "%~1"=="--workspace" goto parse_open_workspace
if /i "%~1"=="--profile" goto parse_open_profile
goto open_invalid_args

:parse_open_workspace
if "%~2"=="" goto open_invalid_args
set "WORKSPACE=%~2"
shift
shift
goto parse_open_args

:parse_open_profile
if "%~2"=="" goto open_invalid_args
set "PROFILE=%~2"
shift
shift
goto parse_open_args

:open_invalid_args
echo Argumentos de abertura invalidos. Use Workbench.cmd open --help.
exit /b 2

:open_managed_run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Gerenciar_Workbench.ps1" -Operation Open -Workspace "%WORKSPACE%" -Profile "%PROFILE%"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo Falha ao abrir o Workbench gerenciado. Verifique .workbench-managed\logs e maintenance.log.
    pause
)
exit /b %RESULT%

:open_help
echo Uso: Workbench.cmd open [--workspace CAMINHO] [--profile NOME]
echo Abre a instalacao gerenciada ou, se ausente, a instalacao F1 preservada.
echo --profile seleciona um perfil de modelo definido na configuracao.
exit /b 0

:manage_entry
if not "%~2"=="" goto manage_operation
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Gerenciar_Workbench.ps1"
exit /b %ERRORLEVEL%

:manage_operation
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Gerenciar_Workbench.ps1" -Operation "%~2"
exit /b %ERRORLEVEL%

:queue_entry
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python local ausente em .venv. Nenhum download sera iniciado.
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\run_test_queue.py" %*
exit /b %ERRORLEVEL%

:validate_entry
if /i "%~2"=="static" goto validate_direct
if /i "%~2"=="tests" goto validate_direct
if /i "%~2"=="installed" goto validate_direct
if /i "%~2"=="all" goto validate_direct
goto validate_menu

:validate_direct
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python local ausente em .venv. Nenhum download sera iniciado.
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\validate.py" "%~2" %3
exit /b %ERRORLEVEL%

:validate_menu
cls
echo Validar o codigo do repositorio - nao usa LLM
echo.
echo 1  Checagens estaticas (Ruff, politicas, mypy)
echo 2  Suite Python (pytest)
echo 3  Pacote instalado
echo 4  Todas as validacoes
echo 5  Mostrar comandos estaticos sem executar
echo 0  Voltar
echo.
choice /c 123450 /n /m "Escolha uma opcao: "
if errorlevel 6 exit /b 0
if errorlevel 5 goto validate_plan
if errorlevel 4 goto validate_all
if errorlevel 3 goto validate_installed
if errorlevel 2 goto validate_tests
if errorlevel 1 goto validate_static
goto validate_menu

:validate_plan
set "VALIDATION=plan"
goto validate_selected

:validate_all
set "VALIDATION=all"
goto validate_selected

:validate_installed
set "VALIDATION=installed"
goto validate_selected

:validate_tests
set "VALIDATION=tests"
goto validate_selected

:validate_static
set "VALIDATION=static"

:validate_selected
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python local ausente em .venv. Nenhum download sera iniciado.
    pause
    goto validate_menu
)
if /i "%VALIDATION%"=="plan" (
    "%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\validate.py" static --plan
) else (
    "%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\validate.py" "%VALIDATION%"
)
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo Algumas checagens falharam. Veja os resultados acima.
pause
goto validate_menu

:help
echo Workbench.cmd abre o menu integrado de projeto, gestao e validacao.
echo.
echo Uso direto:
echo   Workbench.cmd open [--workspace CAMINHO] [--profile NOME]
echo   Workbench.cmd manage [Install^|Update^|Status^|Rollback^|Uninstall]
echo   Workbench.cmd queue [comando e argumentos]
echo   Workbench.cmd validate [static^|tests^|installed^|all] [--plan]
echo.
echo Queue sem argumentos abre o menu interativo da fila.
exit /b 0
