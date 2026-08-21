# EvidenceLab

Asistente de razonamiento probatorio sobre resoluciones judiciales mexicanas en
español. Responde preguntas sobre un expediente citando documento, página y URL
de la fuente, y se niega a convertir testimonios o indicios en hechos probados.

Proyecto final del Módulo 3 · Diplomado en Artificial Intelligence & Large
Language Models for Financial Markets · ITAM.

> **Estado:** en construcción. El corpus, el índice híbrido y el retriever
> funcionan; la interfaz, los guardrails y la evaluación están en curso. Este
> README se actualiza conforme avanza.

## Modo de ejecución: offline

EvidenceLab corre **entero en tu máquina**. La generación la sirve Ollama y los
embeddings y el re-ranking son modelos locales. No hay API keys ni llamadas a
servicios externos: el corpus son expedientes judiciales y el diseño asume que
no deben salir del equipo.

La única vez que el proyecto toca la red es al descargar los modelos.

## Requisitos

- Python 3.11 o 3.12
- [Ollama](https://ollama.com/download) instalado
- ~5 GB de RAM libre para la configuración por defecto

## Instalación

```bash
git clone git@github.com:bargof/Evidence-Lab.git
cd Evidence-Lab

# 1. Modelo generativo
ollama pull llama3.2:3b

# 2. Dependencias
poetry install        # o: pip install -e .

# 3. Configuración (opcional: los valores por defecto funcionan)
cp .env.example .env

# 4. Índice del RAG, una sola vez
python ingest.py
```

`ingest.py` indexa 1,097 chunks de 8 resoluciones y tarda unos dos minutos en
CPU. Deja el resultado en `artifacts/rag_index/`.

### Si vas justo de memoria

En un equipo de 8 GB, edita `.env`:

```
EVIDENCELAB_OLLAMA_MODEL=llama3.2:1b
EVIDENCELAB_EMBEDDING_MODEL=intfloat/multilingual-e5-small
EVIDENCELAB_USE_RERANKER=false
```

Para saber si tu máquina aguanta la configuración completa:

```bash
python scripts/benchmark_demo.py
```

Reporta latencia de recuperación, tokens por segundo de generación y memoria
pico.

## El corpus

8 resoluciones oficiales en versión pública de la SCJN y el CJF: homicidio,
feminicidio, secuestro y coautoría, con condenas subsistentes, revocaciones y
reenvíos. 458 páginas, 1,097 chunks. Cada chunk conserva `case_id`,
`document_id`, `page_number` y `source_url`, que es lo que permite que toda
afirmación de la app se pueda rastrear hasta una página concreta.

Los documentos son versiones públicas con datos personales testados. Ver
`data/evidencelab/DATA_CARD.md`.

## Arquitectura del retrieval

```
consulta
  → BM25 (términos jurídicos exactos, números de expediente)
  → denso (multilingual-e5-base, consultas semánticas)
  → fusión RRF
  → filtro por case_id
  → re-ranker cross-encoder
  → contexto con sus fuentes
  → LLM local
```

Las cuatro capas se encienden y apagan por bandera en
`src/evidence_lab/rag/retriever.py`, para poder medir qué aporta cada una por
separado.

## Estructura

```
app.py                        entrypoint de la interfaz
ingest.py                     construye el índice del RAG
src/evidence_lab/
  config/                     configuración validada con pydantic-settings
  domain/                     modelos y contratos del dominio
  data/                       schemas de entrada y salida
  rag/                        corpus, índice híbrido y retriever
  guardrails/                 límites de responsabilidad y anti-inyección
  evaluation/                 métricas y reportes
  application/
    services/                 casos de uso, sin dependencia de framework
    app/                      interfaz (Gradio)
scripts/                      utilidades de línea de comandos
tests/                        unit e integration
data/                         corpus y datasets
notebooks/                    entrenamiento (Componente A)
reports/                      métricas y resultados
```

Los servicios de `application/services/` no saben quién los llama. Esa frontera
es deliberada: hoy los usa Gradio y mañana puede usarlos otra interfaz sin
tocar el núcleo.

## La tarea de entrenamiento

El Componente A (MLM continuado, Full SFT y LoRA/QLoRA) vive en `notebooks/` y
se corre en Colab, no en esta app. `scripts/build_sft_v3.py` regenera el
dataset derivado de instrucciones a partir del corpus congelado.

## Licencia y uso

Las resoluciones son documentos públicos de la SCJN y el CJF. El sistema no
debe usarse para atribuir culpabilidad: distingue hechos documentados,
testimonios, alegatos y resultados judiciales, y solo reporta como resultado
oficial lo que resolvió el órgano jurisdiccional.
