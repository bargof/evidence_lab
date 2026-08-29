# EvidenceLab · Documentación técnica

Este documento explica el proyecto completo, de punta a punta, suponiendo que
quien lo lee sabe programar pero **nunca ha trabajado con modelos de lenguaje**.
Cada término técnico se explica la primera vez que aparece. Al terminarlo
deberías poder abrir cualquier archivo del repo y entender qué hace y por qué
está escrito así.

Está organizado en tres bloques:

1. **Conceptos** (secciones 1 a 4) — qué es un modelo de lenguaje, por qué
   inventa cosas, y cuáles son las formas de darle conocimiento. Si ya sabes
   esto, salta a la sección 5.
2. **El sistema** (secciones 5 a 10) — cómo está construido EvidenceLab, pieza
   por pieza, con la razón de cada decisión.
3. **Referencia** (secciones 11 a 13) — la tarea de entrenamiento, el
   rendimiento medido y un glosario.

---

## 1. El problema que resuelve el proyecto

EvidenceLab responde preguntas sobre **expedientes judiciales mexicanos**:
resoluciones públicas de la Suprema Corte de Justicia de la Nación (SCJN) y del
Consejo de la Judicatura Federal (CJF). Ocho casos, todos penales: homicidio,
feminicidio, secuestro, coautoría.

Suena a un buscador de documentos, pero el problema real es más delicado. Una
sentencia **no es una lista de hechos**. Es un documento donde conviven cosas de
peso muy distinto:

| Lo que aparece en la sentencia | Qué tan sólido es |
|---|---|
| "El testigo declaró que vio salir a X" | Alguien lo dijo. Puede ser falso. |
| "La defensa alega que la detención fue ilegal" | Una parte lo sostiene. Es su postura. |
| "El dictamen pericial indica hora de muerte a las 3 a.m." | Prueba documentada. |
| "Esta Primera Sala resuelve revocar la sentencia" | Decisión oficial. |
| "El 11 de enero se admitió a trámite la demanda" | Acto del proceso. |

Un sistema que trate esas cinco líneas como si fueran lo mismo produce una
conclusión falsa con apariencia de certeza. Y en este dominio, esa falla no es
un error cosmético: es afirmar que alguien es culpable.

Por eso el proyecto no se llama "buscador de sentencias". Su trabajo es
**responder citando la fuente exacta y sin subir de nivel la fuerza de la
evidencia**.

Tres reglas gobiernan todo el diseño. Vienen de la ontología del corpus
(`data/evidencelab/ontology/logical_rules.yaml`) y las vas a ver reaparecer
convertidas en código:

```
RULE-TESTIMONY-NOT-FACT      alguien declara X   →   X fue declarado, no probado
RULE-OPPORTUNITY-NOT-GUILT   presencia + hecho   →   no implica culpabilidad
RULE-ABSENCE-NOT-NEGATION    sin evidencia de X  →   no implica que X sea falso
```

---

## 2. Qué es un modelo de lenguaje

### 2.1 La idea básica

Un **modelo de lenguaje grande** (LLM, *large language model*) es un programa
que hace una sola cosa: dado un texto, predice qué sigue.

Eso es todo. No busca en una base de datos, no razona con reglas, no consulta
internet. Recibe una secuencia y produce la continuación más probable, una
pieza a la vez.

Que de ahí salga algo parecido a conversar es consecuencia de la escala. El
modelo que usamos, `llama3.2:3b`, tiene **3 mil millones de parámetros**.

### 2.2 Parámetros

Un **parámetro** es un número ajustable dentro del modelo. Piénsalo como el
peso de una conexión: cuánto influye una cosa sobre otra. Entrenar el modelo es
ajustar esos números para que las predicciones sean buenas.

3 mil millones de parámetros es un modelo pequeño para los estándares actuales
(los grandes andan en cientos de miles de millones). Lo elegimos justo por eso:
tiene que correr en una laptop.

### 2.3 Tokens

El modelo no ve letras ni palabras: ve **tokens**, que son pedazos de palabra.
"Reconstrucción" puede partirse en `Recon` + `struc` + `ción`. El modelo de
Llama 3.2 maneja 128,256 tokens distintos en su vocabulario.

Esto importa por tres razones prácticas que verás en el código:

- **El contexto se mide en tokens.** El modelo solo puede "ver" una cantidad
  limitada de texto a la vez. Todo lo que le mandas —instrucciones, evidencia,
  pregunta— compite por ese espacio.
- **La velocidad se mide en tokens por segundo.** En nuestra laptop el modelo
  genera 9.2 tokens/s. Una respuesta de 450 tokens tarda unos 50 segundos.
- **El español gasta más tokens que el inglés**, porque los modelos se entrenan
  mayoritariamente con inglés y su vocabulario está optimizado para ese idioma.

### 2.4 Por qué un modelo inventa cosas

A esto se le llama **alucinación**, y no es un bug: es la consecuencia directa
de cómo funciona.

El modelo predice la continuación *más probable*, no la *verdadera*. No tiene
un mecanismo para distinguir "esto lo sé" de "esto suena bien". Si le preguntas
por la página donde consta algo, va a producir un número de página, porque
después de "página" lo estadísticamente probable es un número — exista o no.

Y lo hace con total fluidez y aplomo. **Un modelo alucinando se ve exactamente
igual que un modelo acertando.** Esa es la razón de que este proyecto tenga
capas de verificación: no se puede confiar en el tono.

En la sección 9 verás un caso real donde el modelo inventó las páginas 100 y
101 de un documento que solo tiene 64.

### 2.5 Temperatura

La **temperatura** controla cuánta aleatoriedad hay al elegir el siguiente
token. Con temperatura alta el modelo se arriesga con opciones menos probables
(más creativo, más variable). Con temperatura 0 siempre elige la opción más
probable, y la misma pregunta produce la misma respuesta.

Nosotros usamos **temperatura 0** (`generation_temperature` en la
configuración). En un sistema que debe ser auditable y evaluable, la
reproducibilidad vale más que la variedad.

### 2.6 Cuantización

Los parámetros del modelo son números decimales. Guardarlos con precisión
completa cuesta mucha memoria: 3 mil millones de parámetros a 16 bits cada uno
son unos 6 GB.

La **cuantización** los guarda con menos precisión. Nuestro modelo usa
`Q4_K_M`: aproximadamente 4 bits por parámetro. El archivo baja de ~6 GB a
**1.88 GB** y cabe cómodo en RAM.

¿Qué se pierde? Un poco de calidad. La analogía útil es guardar una foto en JPEG
con más compresión: sigue siendo la misma foto, con algo de degradación que casi
nunca notas. Para nuestro caso el intercambio vale la pena, porque la
factualidad no viene del modelo sino de la evidencia que le damos.

---

## 3. Las tres formas de darle conocimiento a un modelo

El modelo base no sabe nada de tus ocho expedientes. Hay tres maneras de
cambiarlo, y entender la diferencia es la decisión de arquitectura más
importante del proyecto.

### 3.1 Prompting

Le escribes instrucciones y el texto relevante en el mensaje. Es lo más
inmediato: no requiere entrenar nada, funciona al instante y se corrige
editando texto.

Su límite es el tamaño del contexto. No puedes pegarle 458 páginas en cada
pregunta: no caben, y aunque cupieran, mandar 458 páginas por cada consulta
sería absurdamente lento.

### 3.2 RAG (Retrieval-Augmented Generation)

*Generación aumentada con recuperación.* La idea: antes de preguntarle al
modelo, **busca** los fragmentos relevantes en tus documentos y **solo esos** se
los pasas en el prompt.

Es prompting con un buscador enfrente. Resuelve el problema de tamaño: de 458
páginas se seleccionan 6 fragmentos, unas 2 páginas.

Y tiene una propiedad que en este dominio es decisiva: **como tú controlas qué
texto entra, sabes exactamente de dónde salió cada respuesta.** Eso hace posible
la trazabilidad y hace posible detectar cuándo el modelo se inventó una fuente.

### 3.3 Fine-tuning

*Ajuste fino.* Sigues entrenando el modelo con tus propios ejemplos, cambiando
sus parámetros.

Aquí está el malentendido más común, y vale la pena decirlo con todas sus
letras:

> **El fine-tuning no le mete hechos al modelo, le mete forma.**

Con fine-tuning le enseñas *cómo comportarse*: qué formato usar, qué tono, qué
estructura de respuesta, qué tipo de razonamiento seguir. Lo que no logras es
que memorice de forma confiable el contenido de tus documentos. Para eso harían
falta órdenes de magnitud más datos, y aun así seguiría sin poder citarte la
página.

### 3.4 Qué eligió este proyecto y por qué

**RAG.** El corpus es pequeño (8 documentos), cambia poco, y el requisito
central es citar la fuente. RAG resuelve las tres.

El fine-tuning **sí se hizo**, pero como ejercicio aparte, en `notebooks/`, y
**no alimenta la app**. Dos razones:

1. **Es lo que el proyecto pide.** El fine-tuning es el Componente A, evaluado
   por separado; la app es el Componente B y se concentra en el sistema
   alrededor del modelo.
2. **Hay un desajuste técnico real.** El modelo se entrenó recibiendo
   *proposiciones curadas* (una capa estructurada de 60 hechos atómicos
   extraídos a mano). La app le entrega *fragmentos de texto crudo* del PDF. Es
   un formato de entrada que nunca vio. Un modelo especializado que recibe algo
   fuera de su distribución de entrenamiento suele portarse **peor** que el
   modelo base, porque fuerza lo que aprendió sobre algo que no encaja.

El principio de diseño, en una línea:

> **El RAG aporta la factualidad. El fine-tuning aportaría el comportamiento.
> Nunca al revés.**

---

## 4. Cómo se busca texto: los dos enfoques

El corazón del RAG es la búsqueda. Hay dos familias, con fortalezas opuestas, y
este proyecto usa las dos.

### 4.1 Búsqueda léxica: BM25

**BM25** es un algoritmo clásico de recuperación de información. Compara
*palabras literales* entre la consulta y los documentos. Ordena los documentos
según tres intuiciones:

1. Si una palabra de la consulta aparece muchas veces en un documento, ese
   documento es más relevante.
2. Una palabra que aparece en *todos* los documentos (como "de" o "resolución")
   no distingue nada, así que pesa poco. Una palabra rara pesa mucho.
3. Un documento largo tiene más palabras por casualidad, así que se normaliza
   por longitud.

**Fortaleza:** encuentra términos exactos. Si preguntas por "amparo directo en
revisión 4919/2017", BM25 lo clava.

**Debilidad:** no entiende sinónimos. Si el documento dice "se retractó" y tú
preguntas "cambió su versión", BM25 no ve ninguna relación.

En este proyecto está en `rag/index.py`, con dos detalles adaptados al español:

- **Plegado de acentos.** Los PDFs de la SCJN a veces extraen "resolución" y a
  veces "resolucion". Sin plegar los acentos, BM25 los trataría como palabras
  distintas. La función `tokenize()` los normaliza.
- **Palabras vacías.** Se descarta una lista de artículos y preposiciones que
  aparecen en toda página y no discriminan nada.

### 4.2 Búsqueda densa: embeddings

Un **embedding** es la representación de un texto como una lista de números —un
vector— que captura su *significado*.

La idea: un modelo entrenado para eso convierte cualquier texto en, digamos, 768
números. Y lo hace de forma que **textos con significado parecido quedan cerca
en ese espacio de 768 dimensiones**, aunque no compartan una sola palabra.

Para medir "cerca" se usa la **similitud coseno**: el coseno del ángulo entre
los dos vectores. Vale 1 si apuntan en la misma dirección (muy parecidos), 0 si
son perpendiculares (sin relación).

Un truco de implementación que verás en el código: si los vectores se
**normalizan** (se les ajusta la longitud a 1) al momento de crearlos, entonces
la similitud coseno es simplemente el producto punto. Eso convierte la búsqueda
en una sola multiplicación de matrices, que es rapidísima. En `retriever.py`:

```python
# Embeddings ya normalizados, así que el producto punto es el coseno.
similarities = self.index.embeddings @ vector
```

El modelo que usamos es **`intfloat/multilingual-e5-base`**: 278 millones de
parámetros, 768 dimensiones, multilingüe.

Un detalle que parece trivial y no lo es: los modelos de la familia e5 exigen
que les antepongas un prefijo — `"query: "` a las preguntas y `"passage: "` a
los documentos. Sin esos prefijos la calidad cae de forma notable, porque el
modelo fue entrenado para distinguir los dos roles. Están en
`config/settings.py` y no son configurables por entorno, precisamente porque
dependen del modelo y no del equipo.

**Por qué e5-base y no bge-m3.** El material del curso sugiere `BAAI/bge-m3`,
que es más grande (568M parámetros) y de mejor calidad. Lo descartamos porque
esta app corre en CPU: indexar y consultar con un modelo del doble de tamaño
duplica la latencia. e5-base da el mejor balance medido en este corpus. Es una
decisión de ingeniería con un número detrás, no una preferencia.

### 4.3 Por qué se usan los dos: búsqueda híbrida

Las debilidades son complementarias. BM25 falla con sinónimos; el denso falla
con identificadores exactos y nombres propios raros. Usar ambos y combinar los
resultados es lo que se llama **búsqueda híbrida**.

### 4.4 Cómo se combinan: RRF

El problema de combinar: BM25 devuelve puntajes en una escala (pueden ser 8.3,
2.1, 0.4) y el denso en otra (0.87, 0.85, 0.83). No son comparables. Sumarlos
directamente le daría todo el peso a BM25 por accidente.

**Reciprocal Rank Fusion (RRF)** resuelve esto ignorando los puntajes y usando
solo la **posición** en cada lista. Cada documento recibe:

```
puntaje = Σ  1 / (k + posición_en_esa_lista)
```

sumando sobre todas las listas donde aparece. `k` es una constante (usamos 60)
que suaviza la diferencia entre los primeros lugares.

La consecuencia práctica: un documento que sale en el puesto 3 de ambas listas
le gana a uno que sale primero en una sola. RRF premia el **acuerdo entre
métodos**, que es justo lo que quieres cuando ninguno de los dos es confiable
por sí solo.

En `retriever.py`, la función `_rrf()` y el comentario que explica por qué se
fusiona por rango y no por puntaje.

### 4.5 Filtro por metadata

Cada fragmento del corpus lleva pegado su `case_id`. Cuando el usuario
selecciona un expediente en la interfaz, la búsqueda **solo mira los fragmentos
de ese caso**.

Es la protección más simple y más efectiva del sistema: hace estructuralmente
imposible que la respuesta sobre un caso se apoye en evidencia de otro. En un
dominio judicial, mezclar expedientes no es una imprecisión, es una falla grave.

### 4.6 Re-ranking

Después de la búsqueda tenemos 20 candidatos ordenados. El **re-ranking** los
vuelve a ordenar con un modelo más caro pero más preciso.

La diferencia técnica está en cómo se compara pregunta y documento:

- Un **bi-encoder** (el de embeddings) convierte la pregunta en un vector y cada
  documento en otro, por separado, y compara los vectores. El documento se puede
  procesar de antemano, así que la búsqueda es instantánea. Pero pregunta y
  documento nunca se "miran" directamente.
- Un **cross-encoder** (el re-ranker) recibe **el par completo** —pregunta y
  documento juntos— y produce un puntaje de relevancia. Ve las dos cosas a la
  vez, así que es mucho más preciso. El costo: hay que correrlo una vez por cada
  par, y no se puede precalcular.

Por eso el orden es: búsqueda rápida sobre 1,097 fragmentos para quedarte con
20, y luego el modelo caro solo sobre esos 20.

Usamos `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingüe, 118M
parámetros) en vez del `bge-reranker-v2-m3` sugerido en el curso (568M), otra
vez por latencia en CPU.

### 4.7 Chunking

Un documento de 64 páginas no se puede indexar entero: el embedding de un texto
larguísimo promedia todos sus temas y no representa bien ninguno. Hay que
partirlo en **chunks** (fragmentos).

Nuestro corpus:

| | |
|---|---|
| Documentos | 8 |
| Páginas | 458 |
| Chunks | 1,097 |
| Tamaño medio | 985 caracteres |
| Máximo | 1,200 caracteres |

El chunking se hizo **respetando el límite de página**, y esa es una decisión de
diseño, no una casualidad. Un chunk nunca cruza de una página a otra, porque
cada chunk debe poder decir con exactitud de qué página salió. Si un fragmento
abarcara el final de la página 12 y el inicio de la 13, la cita sería ambigua —
y la cita verificable es el producto.

El intercambio: a veces una frase queda partida entre dos chunks. Se aceptó a
cambio de trazabilidad exacta.

---

## 5. Arquitectura del proyecto

### 5.1 La estructura de carpetas y su lógica

```
EvidenceLab/
├── app.py                    punto de entrada de la interfaz
├── ingest.py                 construye el índice (se corre una vez)
├── pyproject.toml            dependencias y configuración de herramientas
├── .env.example              variables de entorno documentadas
│
├── prompts/                  prompts versionados, uno por archivo
│   └── system.v1.md
│
├── src/evidence_lab/
│   ├── config/               configuración validada
│   ├── domain/               conceptos del dominio
│   ├── data/                 contratos de entrada y salida (Pydantic)
│   ├── rag/                  corpus, índice y recuperación
│   ├── guardrails/           validaciones de seguridad
│   ├── evaluation/           métricas
│   └── application/
│       ├── services/         casos de uso
│       └── app/              interfaz
│
├── scripts/                  utilidades de línea de comandos
├── tests/                    unit e integration
├── data/                     corpus y datasets
├── notebooks/                entrenamiento (Componente A)
├── artifacts/                índice generado (no se versiona)
└── reports/                  resultados de mediciones
```

La organización sigue una idea llamada **arquitectura por capas**: el código se
separa según qué tan cerca está del problema o de la tecnología.

- `domain` y `data` son el **centro**: describen el problema (qué es una cita,
  qué es una afirmación, qué modalidades existen). No importan nada de nadie.
- `rag`, `guardrails` y `evaluation` son **capacidades**: saben del dominio pero
  no de la interfaz.
- `application/services` son los **casos de uso**: orquestan las capacidades
  para lograr algo útil ("responder una pregunta").
- `application/app` es la **interfaz**: solo presenta. No decide nada.

La regla que hace que esto valga la pena: **las dependencias apuntan hacia
adentro**. La interfaz conoce los servicios; los servicios no conocen la
interfaz.

El beneficio concreto: `AnswerService` no sabe si lo llama Gradio, un test o un
script de evaluación. Si mañana quisieras montar la app en Django, escribes una
vista que llama al mismo servicio y no tocas ni una línea del núcleo.

Un detalle de aislamiento que vale la pena notar: **`generation.py` es el único
archivo del proyecto que importa `ollama`**. Si algún día el modelo se sirviera
de otra forma, se cambia ese archivo y nada más.

### 5.2 El recorrido completo de una pregunta

Esto es lo que pasa, en orden, cuando escribes una pregunta y presionas
"Preguntar". Sigue los archivos:

```
  Usuario escribe la pregunta y elige el expediente
                    │
                    ▼
  application/app/gradio_app.py  ──  solo recoge y muestra
                    │
                    ▼
  application/services/answer_service.py  ──  orquesta todo
                    │
     ┌──────────────┴──────────────┐
     ▼                             │
  rag/retriever.py                 │   1. BM25 sobre el caso seleccionado
     │  Retriever.search()         │   2. búsqueda densa sobre el mismo caso
     │                             │   3. fusión RRF → 20 candidatos
     │                             │   4. re-ranker → los 6 mejores
     ▼                             │
  6 fragmentos con                 │      (~0.76 s)
  documento, página y URL          │
     │                             │
     ├──── se muestran YA en pantalla ──►  el usuario ve las fuentes
     │                             │
     ▼                             │
  application/services/prompting.py│   arma el mensaje:
     │                             │   system prompt + evidencia etiquetada
     │                             │   + pregunta
     ▼                             │
  application/services/generation.py   habla con Ollama
     │                             │   emite el texto token por token
     │                             │      (~35 s)
     ▼                             │
  extract_json()                   │   rescata el objeto JSON
     │                             │
     ▼                             │
  guardrails/validation.py         │   BARRERA 1: estructura (Pydantic)
     │                             │   BARRERA 2: fuentes reales
     ▼                             │
  Respuesta validada  ──  o  ──  Negativa explicada
                    │
                    ▼
  gradio_app.py muestra respuesta, fuentes y rastro técnico
```

Fíjate en la bifurcación después del retriever: **las fuentes se muestran antes
de que empiece la generación**. El retrieval tarda menos de un segundo y la
generación 35. Mostrar las citas de inmediato hace que la app se sienta viva y,
de paso, pone la trazabilidad en primer plano.

---

## 6. El prompt como contrato

El **prompt** es el texto de instrucciones que recibe el modelo. El nuestro está
en `prompts/system.v1.md`, y hay dos decisiones de diseño en esa ruta.

**Está en un archivo, no en el código.** Un prompt es contenido que se itera
constantemente. Tenerlo aparte permite compararlo entre versiones y registrar
con qué versión se generó cada respuesta.

**Está versionado por nombre de archivo.** `system.v1.md`, `system.v2.md`. El
cargador (`prompting.py`) toma la versión más alta si no le pides una
específica, y cada respuesta guarda su `prompt_version`. Cuando la evaluación
diga que algo mejoró, vas a saber contra qué versión de prompt fue.

El prompt hace cuatro cosas:

1. **Delimita la fuente.** "Respondes exclusivamente con la EVIDENCIA que se te
   entrega." Contra la tendencia natural del modelo a completar con lo que sabe.
2. **Enseña el vocabulario del dominio.** Las seis modalidades, con su
   significado.
3. **Enuncia las tres reglas epistemológicas.**
4. **Fija el formato exacto de salida**, con un ejemplo del JSON.

Y un detalle que conecta el prompt con la verificación: la evidencia se le
entrega con etiquetas del mismo formato que debe citar.

```
[CASE-MX-006-DOC-001 p.12] texto del fragmento...
```

El modelo tiene la etiqueta enfrente y solo tiene que copiarla. Eso hace dos
cosas: le facilita citar bien, y hace **fácil de detectar** cuando se inventa
una, porque cualquier cita que no coincida con una etiqueta entregada es
necesariamente falsa.

---

## 7. Salidas estructuradas

El modelo no responde con texto libre: responde con un **objeto JSON** que sigue
un esquema fijo.

```json
{
  "answer": "Respuesta en español, clara y breve.",
  "claims": [
    {
      "statement": "Una afirmación concreta.",
      "modality": "testimony",
      "verdict": "supported",
      "citations": [{"document_id": "CASE-MX-006-DOC-001", "page_number": 12}]
    }
  ],
  "limitations": ["Lo que la evidencia no permite determinar."]
}
```

Tres motivos:

1. **Se puede verificar.** De un párrafo en prosa no puedes comprobar
   automáticamente que cada afirmación tenga fuente. De esta estructura sí.
2. **Obliga a separar.** Al pedirle `modality` por cada afirmación, se le fuerza
   a clasificar la fuerza de cada cosa que dice.
3. **Se puede evaluar.** Los datos estructurados se comparan con métricas.

### 7.1 Pydantic

**Pydantic** es una librería de Python que valida datos contra un esquema
declarado con tipos. Defines una clase, y al construirla comprueba que todo
cumpla; si no, lanza un error detallado.

El esquema está en `data/schemas.py`. Lo importante no es que valide tipos —eso
es lo mínimo— sino que **las reglas del dominio están codificadas como
validadores**. Un ejemplo:

```python
citations: list[Citation] = Field(min_length=1)
```

Esa línea significa: **un `Claim` sin al menos una cita no se puede construir**.
No es una recomendación que el modelo pueda ignorar; es una imposibilidad del
tipo de dato.

Esta es la idea central del enfoque:

> Pedirle a un modelo de 3 mil millones de parámetros que "no invente" es una
> sugerencia. Rechazar la respuesta si un `Claim` viene sin cita es una
> garantía.

Hay otra sutileza en `GroundedAnswer`: la lista de `sources` **se deriva** de
las citas de los claims, nunca se acepta tal cual. Así no existe forma de listar
como fuente algo que ninguna afirmación usó.

---

## 8. Los guardrails: dos barreras independientes

Un **guardrail** es una barrera que impide que el sistema haga algo que no debe.
EvidenceLab tiene dos, y son independientes a propósito: protegen contra fallas
de naturaleza distinta.

### Barrera 1 — Validación estructural (`data/schemas.py`)

Pregunta: *¿la respuesta cumple el contrato?*

- ¿Tiene las claves correctas, con los tipos correctos?
- ¿Cada afirmación lleva al menos una cita?
- ¿Los `verdict` y `modality` son valores válidos?
- ¿Una afirmación con modalidad `testimony` está redactada como declaración
  atribuida, y no como hecho probado?
- ¿La respuesta atribuye culpabilidad sin decir qué órgano la resolvió?

Las dos últimas son reglas del dominio convertidas en código. Por ejemplo, esta
afirmación se **rechaza**:

> "El acusado estuvo en el lugar." *(modalidad: testimony)*

porque está redactada como hecho, cuando la modalidad dice que es un testimonio.
Y esta se **admite**:

> "El testigo declaró que vio al acusado en el lugar."

Lo mismo con la culpabilidad: `"Q es el culpable"` se rechaza; `"El tribunal
determinó que Q era penalmente responsable"` se admite, porque reporta una
decisión oficial en vez de emitir un juicio propio.

### Barrera 2 — Validación de fuentes (`guardrails/validation.py`)

Pregunta: *¿las citas existen de verdad?*

Se construye el conjunto de fuentes que el retriever puso en el contexto:

```python
def allowed_sources(chunks):
    return {(c.chunk.document_id, c.chunk.page_number) for c in chunks}
```

y se compara cada cita contra ese conjunto. Las que no están, se eliminan. Si
una afirmación se queda sin ninguna cita válida, se elimina la afirmación
entera. Si no queda ninguna afirmación en pie, el sistema **se niega a
responder**.

Una decisión importante: cuando una cita no existe, **no se corrige a la más
parecida**. Sería tentador —el modelo citó la página 100, la más cercana
disponible es la 57, la sustituimos— pero eso sería fabricar la trazabilidad que
el sistema promete. Si el modelo citó algo que no estaba, esa afirmación no
tiene respaldo, punto.

### Por qué dos y no una

Porque una respuesta puede pasar la primera y fallar la segunda, y viceversa.

Un JSON perfectamente formado, con todas sus claves, cada afirmación con su cita
bien tipada... citando páginas que no existen. Estructuralmente impecable,
factualmente inventado. La barrera 1 no lo ve, porque el número 100 es un entero
positivo perfectamente válido.

Eso no es hipotético: pasó, y está documentado paso a paso en
[docs/pruebas.md](docs/pruebas.md).

### La negativa como funcionalidad

Cuando nada se sostiene, la app responde con un `Refusal` que dice por qué. Hay
cuatro categorías: `fuera_de_alcance`, `evidencia_insuficiente`,
`peticion_indebida`, `mezcla_de_casos`.

Negarse bien es parte del producto. Un sistema que siempre contesta algo es un
sistema en el que no puedes confiar, porque no distingue entre saber y no saber.

---

## 9. Trazabilidad

Cada fragmento del corpus arrastra cuatro datos desde la ingesta hasta la
pantalla:

| Campo | Para qué |
|---|---|
| `case_id` | filtrar y no mezclar expedientes |
| `document_id` | identificar la resolución |
| `page_number` | señalar la página exacta |
| `source_url` | enlace al PDF oficial de la SCJN |

En la interfaz, cada fuente aparece como enlace directo al documento oficial.
Quien lea la respuesta puede abrir el PDF y verificar. Eso es lo que separa un
sistema auditable de uno que hay que creerle.

---

## 10. La configuración

`config/settings.py` usa **pydantic-settings**: la configuración es una clase
con tipos y validaciones, que se llena desde variables de entorno o un archivo
`.env`.

Por qué así y no constantes sueltas:

- **Se valida.** `chunk_candidates: int = Field(default=20, gt=0)` garantiza que
  nadie ponga cero.
- **Se documenta sola.** Cada campo lleva su descripción.
- **Se ajusta sin tocar código.** Y eso importa mucho aquí: la app tiene que
  correr en máquinas distintas, y en una de 8 GB conviene bajar a un modelo más
  chico. Eso es editar `.env`, no reprogramar.

Las variables llevan el prefijo `EVIDENCELAB_`, para no chocar con otras del
sistema.

---

## 11. El Componente A: la tarea de entrenamiento

Esta parte **no alimenta la app**, pero es la mitad del proyecto académico y
explica varios archivos del repo. Son tres etapas.

### 11.1 Preentrenamiento continuo (MLM)

Se toma un modelo pequeño ya entrenado (`distilbert-base-multilingual-cased`) y
se le sigue entrenando con el texto del dominio, sin etiquetas. La tarea se
llama **masked language modeling**: se tapan palabras al azar y el modelo
aprende a adivinarlas. Adivinar bien las palabras tapadas de un texto jurídico
exige haber aprendido cómo se habla en ese dominio.

Se mide con **perplexity**: qué tan "sorprendido" está el modelo ante un texto.
Más bajo es mejor. Los resultados:

| Corrida | Corpus | Perplexity |
|---|---|---|
| Modelo original | sin adaptar | 25.22 |
| MLM base | 49 páginas | 11.21 |
| MLM extendido | 349 páginas | **6.58** |

Una reducción del 74%: el lenguaje jurídico dejó de sorprenderle.

### 11.2 Fine-tuning supervisado completo (SFT)

Se entrena con pares de instrucción y respuesta para que el modelo aprenda a
obedecer un formato. "Completo" significa que se ajustan **todos** los
parámetros, que es caro en memoria.

Se usó `microsoft/Phi-4-mini-instruct` (3.8 mil millones de parámetros) con el
dataset propio de 412 ejemplos.

### 11.3 LoRA y QLoRA

Aquí están las dos técnicas que hacen viable entrenar sin un centro de datos.

**LoRA** (*Low-Rank Adaptation*). En vez de modificar los 3 mil millones de
parámetros, se congelan todos y se añaden unas matrices chiquitas que aprenden
el ajuste. La intuición: el cambio que necesitas es mucho más simple que el
modelo completo, así que se puede representar con muchos menos números.

El **rango** (`r`) controla el tamaño de esas matrices. Más rango, más capacidad
de aprender y más memoria. Se probaron r = 4, 8, 16, 32 y 64 para ver dónde deja
de mejorar. La lección esperada es que más rango no siempre es mejor: hay un
punto de rendimientos decrecientes.

El resultado práctico: el **adapter** pesa megabytes, contra los gigabytes del
modelo completo. Puedes tener muchos adapters especializados sobre un mismo
modelo base.

**QLoRA** añade cuantización: el modelo base se carga a **4 bits** (formato NF4)
y los adapters se entrenan encima. Baja el consumo de memoria a una fracción,
con calidad casi igual.

---

## 11b. Qué puede y qué no puede el modelo

Esta sección es el resultado más importante del proyecto, y conviene leerla
antes de asumir qué es capaz de hacer un modelo de lenguaje pequeño.

Se probaron cinco capacidades, cada una con su propia medición y su propio
ground truth. **Tres funcionan, dos no**, y la línea que las separa es nítida.

### Funcionan

**Responder con evidencia enfrente.** JSON válido, citas verificadas contra el
contexto, clasificación correcta de modalidad. Es la operación central de la app.

**Reconstruir una cronología desde el texto.** Siete hechos ordenados con su
página, resultado oficial y limitaciones.

**Ordenar hechos dispersos.** Se barajan los 52 hechos curados del corpus y se
le pide reordenarlos. Kendall tau medio de **+0.563** con las marcas temporales
visibles: muy por encima del azar, aunque nunca acierta el orden completo.

### No funcionan

**Detectar contradicciones entre proposiciones.** Con las 17 tensiones anotadas
escondidas como ground truth, sobre 196 pares: precisión 0.167, **recall
0.059**. Una reformulación binaria con ejemplos resueltos dio 0.000 en ambas
métricas.

**Evaluar teorías.** 34.4% de aciertos sobre 32 teorías, **por debajo del 40.6%**
que daría responder siempre la clase mayoritaria. Nunca predijo `SUPPORTED`, ni
una vez, aunque 12 de las 32 lo son.

### El patrón

Las dos que fallan lo hacen igual: **el modelo colapsa a una sola etiqueta, y
cuál etiqueta depende del encuadre de la pregunta.** Preguntando de forma
abierta por la relación entre dos proposiciones, respondió `SUPPORTS` al 79%.
Preguntando "¿hay tensión aquí?" sobre pares preseleccionados, dijo que sí al
98%. No está evaluando el contenido: está siguiendo la forma de la pregunta.

Falla casos que no requieren saber derecho. *"La persona acusada se encontraba
en el vehículo"* contra *"La persona acusada se encontraba durmiendo en el
domicilio"* lo clasificó `COMPATIBLE_WITH`. No se puede estar en dos lugares a
la vez.

### Por qué esa línea, y no otra

Responder con evidencia enfrente es **recuperar y reformular**: el material
está en el contexto y la tarea es seleccionarlo y expresarlo. Ordenar hechos es
parecido: cada hecho trae su marca temporal y la tarea es acomodarlos.

Comparar dos proposiciones es otra cosa. Exige sostener ambas al mismo tiempo,
contrastarlas en varias dimensiones —tiempo, lugar, modalidad, valor probatorio—
y emitir un juicio sobre la relación entre ellas, que no está escrita en ningún
lado. Es razonamiento relacional, y ahí está el techo de un modelo de 3 mil
millones de parámetros.

Que dos formulaciones muy distintas de la misma tarea fallen igual descarta que
sea un problema de ingeniería de prompt.

### Dónde el código gana

Para contradicciones se probó una alternativa determinista: reglas sobre la
ontología del corpus, usando la modalidad de cada proposición.

| Método | Precisión | Recall | F1 |
|---|---|---|---|
| Modelo, 4 clases | 0.167 | 0.059 | 0.087 |
| Modelo, binaria | 0.000 | 0.000 | 0.000 |
| **Reglas de ontología** | 0.205 | **0.529** | **0.295** |
| Reglas + modelo como filtro | 0.209 | 0.529 | 0.300 |

**Nueve veces más recall que el modelo**, y además instantáneo, determinista y
explicable. El híbrido no aporta nada: el modelo confirmó 43 de los 44
candidatos que le propuso la regla.

La lección general: **cuando una regla de decisión se puede expresar en código,
exprésala en código.** Deja al modelo lo que exige leer lenguaje. Y usa las
reglas para verificar al modelo, no al revés: si el modelo ordena A antes de B y
B antes de C, una regla de transitividad puede comprobar que también ponga A
antes de C.

El detalle completo está en [reports/capacidades.md](reports/capacidades.md).

---

## 12. Rendimiento medido

Todo esto está medido en la laptop de desarrollo (Intel i7, 32 GB, **sin GPU**)
con `scripts/benchmark_demo.py`.

| Operación | Tiempo |
|---|---|
| Construir el índice completo (una vez) | 133 s |
| Recuperación completa, en frío | ~16 s (carga de modelos) |
| Recuperación completa, en caliente | **0.74 s** |
| Generación | **9.2 tokens/s** |
| Respuesta típica (~450 tokens) | ~50 s |
| Memoria del stack de recuperación | 1.7 GB |
| Modelo en Ollama | 1.88 GB |

Tres decisiones de diseño salen directamente de estos números:

**Streaming.** A 9 tokens/s, esperar 50 segundos en blanco se siente roto. Ver
el texto aparecer se siente lento pero vivo. La espera es la misma; la
percepción, no.

**Fuentes primero.** El retrieval tarda 0.76 s y la generación 35. La interfaz
muestra las citas de inmediato.

**Respuestas cortas.** `num_predict` está en 450 y el prompt pide brevedad
explícitamente.

Sobre dónde correr la demo: una Mac con chip M1 usa la GPU integrada vía Metal y
sería considerablemente más rápida generando, a costa de menos memoria
disponible. La decisión se toma corriendo `scripts/benchmark_demo.py` en ambas.

---

## 13. Cómo correrlo

```bash
# 1. El modelo generativo (una vez por máquina)
ollama pull llama3.2:3b

# 2. Dependencias
poetry install

# 3. El índice (una vez por corpus o por modelo de embeddings)
python ingest.py

# 4. La app
python app.py
```

`ingest.py` es el único paso que toca la red, para descargar los modelos de
embeddings. Después de eso todo corre sin conexión.

El índice **no se versiona en git**: se regenera en dos minutos y depende del
modelo de embeddings elegido. Si cambias `EVIDENCELAB_EMBEDDING_MODEL`, hay que
volver a correr `ingest.py`.

---

## 14. Glosario

**Alucinación** — Cuando el modelo produce información falsa con total fluidez.
No es un error corregible: es consecuencia de que predice lo probable, no lo
verdadero.

**BM25** — Algoritmo de búsqueda por palabras literales. Pesa según frecuencia
del término, rareza en el corpus y longitud del documento.

**Bi-encoder** — Modelo que convierte pregunta y documento en vectores por
separado. Rápido, permite precalcular.

**Chunk** — Fragmento de documento, unidad de indexación. Aquí, ~1,000
caracteres sin cruzar de página.

**Cross-encoder** — Modelo que evalúa pregunta y documento juntos. Más preciso,
más lento. Se usa para re-ranking.

**Cuantización** — Guardar los parámetros con menos precisión para ahorrar
memoria. `Q4_K_M` ≈ 4 bits por parámetro.

**Embedding** — Representación de un texto como vector de números que captura su
significado.

**Fine-tuning** — Seguir entrenando un modelo con datos propios. Enseña forma y
comportamiento, no hechos.

**Guardrail** — Barrera que impide que el sistema haga algo indebido.

**LoRA / QLoRA** — Técnicas de fine-tuning eficiente. LoRA entrena matrices
pequeñas con el modelo congelado; QLoRA además cuantiza el modelo base a 4 bits.

**LLM** — Modelo de lenguaje grande. Predice el siguiente token.

**Ollama** — Programa que sirve modelos de lenguaje localmente. Corre como
demonio en el puerto 11434.

**Parámetro** — Número ajustable dentro del modelo.

**Perplexity** — Medida de qué tan sorprendido está el modelo ante un texto.
Menor es mejor.

**Prompt** — El texto de instrucciones y contexto que recibe el modelo.

**Pydantic** — Librería de validación de datos con tipos en Python.

**RAG** — Recuperar los fragmentos relevantes y pasárselos al modelo en el
prompt.

**Rango (LoRA)** — Tamaño de las matrices del adapter. Más rango, más capacidad
y más memoria.

**Re-ranking** — Reordenar los candidatos con un modelo más preciso.

**RRF** — Reciprocal Rank Fusion. Combina listas usando posiciones, no puntajes.

**Similitud coseno** — Coseno del ángulo entre dos vectores. 1 = misma
dirección.

**Temperatura** — Aleatoriedad al elegir el siguiente token. 0 = determinista.

**Token** — Pedazo de palabra. La unidad que el modelo procesa.

---

## 15. Para seguir leyendo el repo

Un orden sugerido si quieres entender el código:

1. `src/evidence_lab/data/schemas.py` — el contrato. Define de qué habla el
   sistema.
2. `prompts/system.v1.md` — lo que se le pide al modelo, en español.
3. `src/evidence_lab/rag/retriever.py` — la búsqueda, con sus cuatro capas.
4. `src/evidence_lab/application/services/answer_service.py` — el recorrido
   completo de una pregunta.
5. `src/evidence_lab/guardrails/validation.py` — las dos barreras.
6. [`docs/pruebas.md`](docs/pruebas.md) — qué se probó y qué falló.
