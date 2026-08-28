@echo off
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
REM Arranca EvidenceLab desde cero en Windows: verifica requisitos, instala,
REM construye el indice si falta, y levanta la aplicacion.
setlocal

echo.
echo   EvidenceLab
echo   ===========
echo.

REM --- 1. Python ------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [X] No encuentro Python en el PATH.
    echo     Instala Python 3.11 o 3.12 desde https://www.python.org/downloads/
    pause
    exit /b 1
)

REM --- 2. Ollama ------------------------------------------------------------
curl -s -m 5 http://127.0.0.1:11434/api/version >nul 2>&1
if errorlevel 1 (
    echo [X] Ollama no responde en el puerto 11434.
    echo.
    echo     1. Instalalo desde https://ollama.com/download
    echo     2. Abrelo para que quede corriendo
    echo     3. Ejecuta:  ollama pull llama3.2:3b
    echo.
    pause
    exit /b 1
)
echo [ok] Ollama respondiendo

REM --- 3. Entorno virtual ---------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [..] Creando entorno virtual, esto tarda un momento
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

.venv\Scripts\python.exe -c "import gradio, sentence_transformers, ollama" >nul 2>&1
if errorlevel 1 (
    echo [..] Instalando dependencias, la primera vez tarda varios minutos
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
    .venv\Scripts\python.exe -m pip install --quiet -e .
    if errorlevel 1 (
        echo [X] Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
)
echo [ok] Dependencias listas

REM --- 4. Indice del RAG ----------------------------------------------------
if not exist "artifacts\rag_index\meta.json" (
    echo [..] Construyendo el indice del corpus, unos dos minutos
    .venv\Scripts\python.exe ingest.py
    if errorlevel 1 (
        echo [X] Fallo la construccion del indice.
        pause
        exit /b 1
    )
)
echo [ok] Indice listo

REM --- 5. Arranque ----------------------------------------------------------
echo.
echo [..] Levantando la aplicacion, se abrira en el navegador
echo      Para cerrarla: Ctrl+C en esta ventana
echo.
.venv\Scripts\python.exe app.py

endlocal
