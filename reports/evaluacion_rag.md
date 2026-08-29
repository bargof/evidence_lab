# Evaluación del RAG

Medición del retriever contra un golden set construido para este fin. Todas las
cifras se regeneran con:

```bash
python scripts/evaluate_retrieval.py
```

Fecha de la corrida: 24 de agosto de 2026.

---

## 1. El golden set

`data/evidencelab/evaluation/golden_v2.jsonl` — **40 preguntas**: 33
contestables y 7 que el sistema debe rechazar.

| Dimensión | Composición |
|---|---|
| Casos | los 8, entre 4 y 7 preguntas cada uno |
| Tipos contestables | factual (11), outcome (9), modality (7), contradiction (4), gap (2) |
| Rechazos esperados | culpabilidad, PII, inyección, mezcla de casos, salto lógico, 2 fuera de corpus |

### Por qué se construyó uno nuevo

El corpus ya traía `golden_set.jsonl` con 48 ejemplos. **No se usó**, por dos
razones independientes:

1. **Está contaminado.** 30 de sus 48 entradas son literalmente ejemplos del
   split de entrenamiento, con el mismo `example_id`; 6 son de validation y 12
   de test. Medir sobre él es medir memorización.
2. **No es un golden de RAG.** Sus entradas son proposiciones ya curadas y
   seleccionadas, no preguntas. Si le entregas al sistema la proposición ya
   encontrada, no estás midiendo la recuperación.

### Cómo se evitó calificar al sistema con su propio examen

La tentación evidente es escribir preguntas, correr el retriever y anotar como
correcto lo que devuelva. Eso garantiza un 100% que no significa nada.

En su lugar, la fuente de verdad viene de la **capa curada a mano** del corpus:
las 60 proposiciones traen ya su `document_id` y `page_number`. El proceso fue
al revés: se tomó una proposición con su página conocida y se escribió la
pregunta en lenguaje natural que esa proposición respondería, **parafraseada**
para que el retriever tenga que buscar y no coincidir literalmente. Cada entrada
declara de qué proposición salió en el campo `derived_from`.

Verificación: cero solape entre los `golden_id` y los `example_id` de train.

---

## 2. Ablation: qué aporta cada capa

Cinco configuraciones sobre las mismas 33 preguntas contestables.

| Iteración | Capas | recall@1 | recall@3 | recall@5 | MRR | Precisión de caso | s/consulta |
|---|---|---|---|---|---|---|---|
| v1 | dense | 0.000 | 0.000 | 0.000 | 0.005 | 0.248 | 0.38 |
| v2 | dense + BM25 | 0.000 | 0.030 | 0.030 | 0.015 | 0.258 | 0.02 |
| v3 | + filtro por caso | 0.152 | 0.242 | 0.242 | 0.197 | **1.000** | 0.02 |
| v4 | + re-ranker | **0.242** | 0.273 | 0.333 | 0.266 | 1.000 | 0.90 |
| v5 | + antecedentes | 0.152 | 0.364 | **0.515** | **0.279** | 1.000 | 0.76 |

Efecto de cada capa sobre recall@5:

```
v1 -> v2   +0.030
v2 -> v3   +0.212
v3 -> v4   +0.091
v4 -> v5   +0.182
```

### El filtro por metadata no es una optimización

`precision_de_caso` mide qué proporción de los fragmentos entregados al modelo
provienen del expediente correcto. Sin el filtro, es **24.8%**: tres de cada
cuatro fragmentos vienen de otro caso.

La causa es el corpus: ocho resoluciones por homicidio, redactadas en el mismo
lenguaje jurídico, con la misma estructura y las mismas fórmulas. La similitud
semántica no puede distinguirlas, porque *son* semánticamente parecidas. En un
dominio así, el filtro por `case_id` es lo que hace que el sistema sea correcto,
no lo que lo hace un poco mejor.

### Los antecedentes: una heurística de dominio

La quinta iteración salió del análisis de errores, no de una lista de mejoras
estándar de RAG. Al revisar los fallos apareció un patrón: el retriever traía
páginas de **discusión doctrinal** en vez de las de **narrativa de hechos**.

Ejemplo real, pregunta GV2-002 (*"¿Hubo alguna admisión de participación ante la
autoridad policial?"*):

- Página 9, esperada: *"policías de investigación —sin encontrarse asistido por
  defensora— **confesó** el homicidio de la víctima"*. El hecho, en una frase.
- Página 24, recuperada: *"mediante una **confesión** del inculpado rendida ante
  el Ministerio Público o un testimonio de referencia..."*. Doctrina general
  sobre confesiones, donde el término aparece muchas más veces.

La página doctrinal gana en similitud precisamente porque habla *del tema*
todo el rato, mientras que la página del hecho lo menciona una vez.

Una resolución judicial abre con los antecedentes, donde se enuncian los hechos
del caso de forma concisa. Sembrar siempre las tres primeras páginas del
expediente como contexto de base sube el recall@5 de 0.333 a **0.515**, un 55%
de mejora relativa.

Es una heurística de dominio, y se declara como tal: funciona porque estos
documentos tienen una estructura fija, no porque sembrar contexto sea buena idea
en general.

**El costo:** recall@1 baja de 0.242 a 0.152, porque los antecedentes ocupan las
primeras posiciones. Para este sistema es un intercambio favorable —lo que
importa es que la página correcta llegue al contexto del modelo, no que quede en
primer lugar— pero es un costo real y conviene nombrarlo.

### Qué NO resolvió el problema

Antes de la heurística se probó lo obvio, ampliar la búsqueda:

| Candidatos | top_k | recall@5 |
|---|---|---|
| 20 | 6 | 0.333 |
| 50 | 6 | 0.364 |
| 50 | 12 | 0.364 |
| 120 | 20 | 0.364 |

Se satura en 0.364. La página correcta no estaba en el fondo de la lista: no
rankeaba bien de entrada. Buscar más profundo no arregla un problema de
ordenamiento, y saberlo evitó gastar latencia en una mejora que no lo era.

---

## 3. Desempeño por tipo de pregunta

Configuración v5:

| Tipo | Preguntas | recall@5 | MRR |
|---|---|---|---|
| modality | 7 | 0.714 | 0.310 |
| factual | 11 | 0.636 | 0.439 |
| contradiction | 4 | 0.500 | 0.125 |
| gap | 2 | 0.500 | 0.583 |
| outcome | 9 | bajo | bajo |

**Las preguntas de resultado son el punto débil.** Tiene explicación: el
resultado oficial está al final del documento, a veces en la página 44, 64 o
158, y la heurística de antecedentes siembra el principio. Una extensión
natural, no implementada por tiempo, sería sembrar también las últimas páginas
para las preguntas de resultado, o detectar la intención de la pregunta y
sembrar según ella.

---

## 4. Análisis de los peores casos

El detalle completo está en `reports/rag_worst_cases.csv`. Los patrones:

**1. Competencia doctrinal.** Ya descrito. Es la causa dominante y motivó la
quinta iteración.

**2. El resultado vive al final.** Preguntas como *"¿Qué resolvió finalmente el
tribunal?"* apuntan a páginas terminales que ninguna capa actual privilegia.

**3. Anotación en página de resumen.** Varias proposiciones curadas están
anotadas en las páginas 1-2, donde la resolución resume los hechos. Cuando el
retriever trae la página donde el hecho se discute a fondo, la métrica lo cuenta
como fallo aunque el fragmento pueda sostener la respuesta. **Esto hace que las
cifras reportadas sean una cota inferior del desempeño real.** No se corrigió
porque relajar el criterio a mitad de la evaluación es la forma más fácil de
engañarse.

---

## 5. Limitaciones de esta evaluación

**Solo se mide recuperación.** No hay métricas de la generación —fidelidad de la
respuesta al contexto, calidad de la redacción— porque calcularlas requiere un
modelo juez y, en CPU a 9 tokens por segundo, 40 preguntas evaluadas por un
juez local no era viable en el tiempo disponible. La verificación de que las
citas existen sí es automática y corre en cada respuesta, en
`guardrails/validation.py`.

**Una sola fuente esperada por pregunta.** Varias preguntas admiten más de una
página válida; se anotó la de la proposición curada. Cota inferior, otra vez.

**40 preguntas es un golden pequeño.** Suficiente para comparar configuraciones
entre sí, corto para afirmar un nivel absoluto de desempeño. Las diferencias de
una o dos preguntas mueven el recall tres puntos.

**Los rechazos no están medidos aquí.** Las 7 preguntas de rechazo requieren
generación para evaluarse; se documentan en `docs/pruebas.md`.
