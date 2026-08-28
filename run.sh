#!/usr/bin/env bash
# Arranca EvidenceLab desde cero en macOS o Linux: verifica requisitos,
# instala, construye el índice si falta, y levanta la aplicación.
set -euo pipefail

cd "$(dirname "$0")"

echo
echo "  EvidenceLab"
echo "  ==========="
echo

MODELO="${EVIDENCELAB_OLLAMA_MODEL:-llama3.2:3b}"

# --- 1. Python --------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[X] No encuentro python3."
    echo "    Instala Python 3.11 o 3.12: https://www.python.org/downloads/"
    exit 1
fi

# --- 2. Ollama --------------------------------------------------------------
if ! curl -s -m 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "[X] Ollama no responde en el puerto 11434."
    echo
    echo "    1. Instálalo desde https://ollama.com/download"
    echo "    2. Ábrelo para que quede corriendo"
    echo "    3. Ejecuta:  ollama pull $MODELO"
    echo
    exit 1
fi
echo "[ok] Ollama respondiendo"

if ! curl -s -m 5 http://127.0.0.1:11434/api/tags | grep -q "$MODELO"; then
    echo "[..] Descargando $MODELO (unos 2 GB, una sola vez)"
    ollama pull "$MODELO"
fi
echo "[ok] Modelo $MODELO disponible"

# --- 3. Entorno virtual -----------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
    echo "[..] Creando entorno virtual"
    python3 -m venv .venv
fi

if ! .venv/bin/python -c "import gradio, sentence_transformers, ollama" >/dev/null 2>&1; then
    echo "[..] Instalando dependencias, la primera vez tarda varios minutos"
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e .
fi
echo "[ok] Dependencias listas"

# --- 4. Índice del RAG ------------------------------------------------------
if [ ! -f "artifacts/rag_index/meta.json" ]; then
    echo "[..] Construyendo el índice del corpus, unos dos minutos"
    .venv/bin/python ingest.py
fi
echo "[ok] Índice listo"

# --- 5. Arranque ------------------------------------------------------------
echo
echo "[..] Levantando la aplicación, se abrirá en el navegador"
echo "     Para cerrarla: Ctrl+C"
echo
exec .venv/bin/python app.py
