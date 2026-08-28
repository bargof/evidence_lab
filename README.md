# EvidenceLab

Asistente de razonamiento probatorio sobre resoluciones judiciales mexicanas.
Reconstruye la cronología de un expediente y responde preguntas citando el
documento y la página exactos, distinguiendo hechos documentados de testimonios,
alegatos y decisiones del tribunal.

Proyecto final del Módulo 3 · Diplomado en Artificial Intelligence & Large
Language Models for Financial Markets · ITAM.

---

## Arranque en un paso

**Windows:** doble clic en `run.bat`
**macOS y Linux:** `./run.sh`

El script verifica que Ollama esté corriendo, instala lo que falte, construye el
índice la primera vez y abre la aplicación en el navegador.

Único requisito previo: [instalar Ollama](https://ollama.com/download) y dejarlo
abierto.

### Instalación manual

```bash
ollama pull llama3.2:3b     # el modelo generativo, una vez por máquina
poetry install              # o: pip install -e .
python ingest.py            # construye el índice, ~2 min
python app.py
```

---

## Modo de ejecución: offline

EvidenceLab corre **entero en tu máquina**. La generación la sirve Ollama y los
embeddings y el re-ranking son modelos locales. No hay API keys ni llamadas a
servicios externos: el corpus son expedientes judiciales y el diseño asume que
no deben salir del equipo.

Lo único que toca la red es la descarga inicial de los modelos. Después de eso
funciona con el wifi apagado.

### Si vas justo de memoria

En un equipo de 8 GB, copia `.env.example` a `.env` y ajusta:

```
EVIDENCELAB_OLLAMA_MODEL=llama3.2:1b
EVIDENCELAB_EMBEDDING_MODEL=intfloat/multilingual-e5-small
EVIDENCELAB_USE_RERANKER=false
```

Para saber qué aguanta tu máquina:

```bash
python scripts/benchmark_demo.py
```

Reporta latencia de recuperación, tokens por segundo y memoria pico.

---

## Qué hace

**Cronología.** Reconstruye el orden de los hechos del expediente, distinguiendo
actos del proceso, testimonios y decisiones del tribunal. Cada hecho lleva su
página.

**Consulta.** Responde preguntas sobre el expediente seleccionado. Cada
afirmación se clasifica por modalidad —hecho documentado, testimonio, alegato,
hallazgo judicial— y se liga a su fuente.

**Ordena hechos dispersos.** Dados los hechos de un expediente en desorden,
reconstruye la secuencia. Medido con Kendall tau contra el orden curado:
**+0.56**.

**Se niega cuando debe.** Si la evidencia no sostiene la respuesta, lo dice. No
atribuye culpabilidad por cuenta propia: solo puede reportar lo que resolvió el
tribunal, citándolo. Siete vectores de ataque, siete contenidos.

### Lo que NO hace

Se probaron dos capacidades más y **no funcionan con un modelo de este tamaño**,
así que no están en el producto: detectar contradicciones entre proposiciones
(recall 0.059) y evaluar teorías (34% de aciertos, por debajo de responder
siempre la clase mayoritaria). Ambas están medidas y documentadas en
[reports/capacidades.md](reports/capacidades.md); para contradicciones, unas
reglas deterministas sobre la ontología dan nueve veces más recall que el
modelo.

---

## El corpus

8 resoluciones oficiales en versión pública de la SCJN y el CJF: homicidio,
feminicidio, secuestro y coautoría, con condenas subsistentes, revocaciones y
reenvíos. 458 páginas, 1,097 fragmentos indexados.

Cada fragmento conserva `case_id`, `document_id`, `page_number` y `source_url`.
Esa cadena es lo que permite que toda afirmación se rastree hasta una página
concreta de un documento público.

Los documentos son versiones públicas con datos personales testados. Ver
[data/evidencelab/DATA_CARD.md](data/evidencelab/DATA_CARD.md).

---

## Arquitectura del retrieval

```
consulta
  → BM25 (términos jurídicos exactos, números de expediente)
  → denso (multilingual-e5-base, consultas semánticas)
  → fusión RRF
  → filtro por case_id
  → re-ranker cross-encoder
  → antecedentes del expediente como contexto base
  → LLM local
  → validación estructural + verificación de fuentes
```

Las capas se encienden y apagan por bandera en
`src/evidence_lab/rag/retriever.py`, para medir qué aporta cada una. Los
resultados están en [reports/evaluacion_rag.md](reports/evaluacion_rag.md).

---

## Evaluación

```bash
python scripts/evaluate_retrieval.py    # ablation del retriever, ~1 min
pytest                                  # 48 pruebas
```

Golden set de 40 preguntas (33 contestables, 7 que deben rechazarse) construido
sin contaminación con los splits de entrenamiento. La tabla de ablation de las
cinco iteraciones y el análisis de los peores casos están en
[reports/evaluacion_rag.md](reports/evaluacion_rag.md).

---

## Documentación

| Documento | Para qué |
|---|---|
| [reports/capacidades.md](reports/capacidades.md) | **Qué hace el sistema y qué tan bien.** Empieza por aquí si quieres saber qué esperar |
| [documentation.md](documentation.md) | Explicación técnica completa, desde qué es un LLM hasta cómo está construido cada módulo. Escrita para alguien que programa pero no ha trabajado con modelos de lenguaje |
| [docs/pruebas.md](docs/pruebas.md) | Qué se probó, qué falló y qué se aprendió |
| [reports/evaluacion_rag.md](reports/evaluacion_rag.md) | Métricas del retrieval y análisis de errores |
| [reports/deteccion_contradicciones.md](reports/deteccion_contradicciones.md) | Un resultado negativo con sus cuatro intentos: dónde el modelo no llega y dónde el código sí |
| [reports/componente_a_reporte.md](reports/componente_a_reporte.md) | La tarea de entrenamiento: MLM, SFT completo, LoRA y QLoRA |

---

## Estructura

```
app.py                        entrypoint de la interfaz
ingest.py                     construye el índice del RAG
run.bat / run.sh              arranque en un paso
prompts/                      prompts versionados
src/evidence_lab/
  config/                     configuración validada con pydantic-settings
  domain/                     conceptos del dominio
  data/                       contratos de entrada y salida
  rag/                        corpus, índice híbrido y retriever
  guardrails/                 doble validación
  evaluation/                 métricas
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
es deliberada: hoy los usa Gradio y mañana puede usarlos otra interfaz sin tocar
el núcleo.

---

## La tarea de entrenamiento

El Componente A —preentrenamiento continuo, fine-tuning supervisado completo, y
LoRA con QLoRA— vive en `notebooks/` y se corre en Colab, **no** en esta app.
Los resultados están en [reports/](reports/).
`scripts/build_sft_v3.py` regenera de forma determinista el dataset derivado de
instrucciones a partir del corpus congelado.

---

## Licencia y uso

Las resoluciones son documentos públicos de la SCJN y el CJF, consultables en
los portales oficiales; sus URLs se conservan en el corpus. El código de este
proyecto es material académico.

El sistema no debe usarse para atribuir culpabilidad. Distingue hechos
documentados, testimonios, alegatos y resultados judiciales, y solo reporta como
resultado oficial lo que resolvió el órgano jurisdiccional.
