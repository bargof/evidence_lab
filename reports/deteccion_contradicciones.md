# Detección de contradicciones: resultado negativo

Experimento para que el sistema **encuentre** las tensiones probatorias de un
expediente, en vez de mostrar las que ya están anotadas a mano.

**Conclusión: `llama3.2:3b` no puede hacer esta tarea con la calidad necesaria
para ponerla en el producto.** Se reporta como resultado negativo, con la
evidencia de los dos intentos.

Reproducible con `python scripts/detect_contradictions.py`.

---

## 1. El diseño

Las 17 tensiones anotadas en `relations.jsonl` **nunca se le muestran al
modelo**. Se reservan como ground truth.

El modelo recibe cada par de proposiciones de un mismo expediente —196 pares en
total, 8.7% de ellos anotados como tensión— y clasifica la relación. Después se
compara contra la anotación.

Ese giro es el punto del experimento. Mostrar el campo `explanation` de la
anotación convertiría la app en un visor de base de datos con estética de IA: el
modelo no aportaría nada y el mérito sería de quien anotó. Escondiendo la
anotación, la app tiene que ganársela.

`CASE-MX-001` se usó como caso de desarrollo para ajustar los prompts. Los otros
siete son conjunto retenido y se reportan por separado.

---

## 2. Intento 1 · Taxonomía de cuatro clases

Prompt `contradiction.v2`, con las cuatro relaciones de la ontología del corpus:
`CONTRADICTS`, `SUPPORTS`, `COMPATIBLE_WITH`, `INSUFFICIENT_FOR`.

| Conjunto | Pares | Anotadas | Detectadas | VP | FP | Precisión | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Corpus completo | 196 | 17 | 6 | 1 | 5 | 0.167 | 0.059 | 0.087 |
| Retenido (sin 001) | 168 | 15 | 5 | 1 | 4 | 0.200 | 0.067 | 0.100 |

La distribución de etiquetas explica el resultado:

```
SUPPORTS           154   (79%)
COMPATIBLE_WITH     30
CONTRADICTS          6
INSUFFICIENT_FOR     6
```

**El modelo colapsó a una sola etiqueta.** Y no por falta de instrucciones: la
v1 del prompt ya advertía que la mayoría de los pares son compatibles, y la v2
endureció la definición de `SUPPORTS` precisamente para corregirlo. El resultado
empeoró: de 68% de `SUPPORTS` en el caso de desarrollo con v1, a 79% en el
corpus con v2.

### Replicación

El experimento se corrió **dos veces de forma independiente**, sin cambiar nada.
Las métricas salieron idénticas —VP=1, FP=5, FN=16, precisión 0.167, recall
0.059— y la distribución de etiquetas se movió apenas: `SUPPORTS` de 154 a 152,
`COMPATIBLE_WITH` de 30 a 31, `INSUFFICIENT_FOR` de 6 a 7. Tres pares de 196
cambiaron de clasificación.

Es decir: hay algo de varianza entre corridas, como en el resto del sistema, pero
no es la que explica el resultado. El fallo es estable.

Ejemplos de fallos que no requieren conocimiento jurídico:

| Proposición A | Proposición B | Dijo | Debía decir |
|---|---|---|---|
| "La persona acusada se encontraba en el vehículo" | "La persona acusada se encontraba durmiendo en el domicilio" | `COMPATIBLE_WITH` | contradicción |
| "El testigo implicó al acusado en su primera declaración" | "El testigo dio una declaración posterior sustancialmente distinta" | `SUPPORTS` | contradicción |
| "El tribunal de apelación condenó" | "La Suprema Corte revocó la resolución" | `SUPPORTS` | tensión |

Estar en dos lugares a la vez es incompatible sin saber nada de derecho.

---

## 3. Intento 2 · Pregunta binaria con ejemplos

Hipótesis: cuatro clases son demasiadas para un modelo de 3 mil millones de
parámetros. Se reformuló como una sola pregunta —*¿pueden ambas ser correctas al
mismo tiempo?*— con tres ejemplos resueltos en el prompt, incluido uno idéntico
en forma al caso del vehículo.

Sobre el caso de desarrollo (28 pares):

```
coexisten = true    26
coexisten = false    2
VP=0  FP=2  FN=2   precisión 0.00   recall 0.00
```

**Peor todavía.** Las dos que marcó eran ambas incorrectas, y siguió sin
detectar ninguna de las dos anotadas, pese al ejemplo casi literal en el prompt.

---

## 4. Qué se concluye

Dos formulaciones muy distintas —taxonomía de cuatro clases con reglas de
dominio, y binaria con few-shot— producen el mismo comportamiento: el modelo
responde la etiqueta mayoritaria casi siempre y no discrimina.

Eso descarta que sea un problema de ingeniería de prompt. Es un **límite de
capacidad** de `llama3.2:3b` para razonamiento contrastivo entre pares de
afirmaciones en español jurídico.

Y tiene sentido: la tarea exige mantener dos proposiciones en memoria,
compararlas en varias dimensiones —tiempo, lugar, modalidad, valor probatorio— y
emitir un juicio relacional. Es un tipo de razonamiento distinto al de responder
una pregunta con evidencia enfrente, que es donde el mismo modelo sí se
desempeña bien.

### Por qué esto no se ships

La función no entra al producto. Un detector con recall de 0.06 encontraría una
de cada diecisiete tensiones y, de lo que reporta, cinco de cada seis serían
falsas. En un dominio donde el usuario busca precisamente lo que se le pudo
haber pasado, eso es peor que no tener la función.

La alternativa —mostrar las tensiones anotadas— se descartó por honestidad: sería
presentar anotación humana como hallazgo del sistema.

---

## 5. Dónde sí valdría la pena seguir

Este es el punto del proyecto donde el fine-tuning tendría un caso claro, y
donde el Componente A dejaría de ser un ejercicio separado.

El dataset de entrenamiento contiene **17 ejemplos de `contradiction_analysis`,
17 de `contradiction_impact` y 17 de `contradiction_resolution`**: fueron
construidos exactamente para esta tarea. El adapter QLoRA r64 entrenado sobre
Llama-3.2-3B existe y está guardado.

La comparación natural es: mismo modelo base, mismos 196 pares, con y sin
adapter. Si el fine-tuning mueve el recall de 0.06 a algo utilizable, sería la
evidencia más fuerte del proyecto entero —justificaría el entrenamiento con un
número de producto, no con un eval loss— y si no lo mueve, también es un
resultado que vale reportar.

No se ejecutó por tiempo y porque servir el modelo fusionado de 6 GB en CPU
requiere convertirlo a GGUF para Ollama, que es trabajo aparte.

### Otras vías, en orden de costo

1. **Un modelo más grande.** La misma prueba con un 8B o con una API mediría si
   el techo es del tamaño del modelo o de la tarea.
2. **Fine-tuning**, con los datos que ya existen. Ver sección 5.

---

## 6. Intento 3 · Reglas sobre la ontología

Si el modelo no puede, ¿puede el código?

La regla se definió desde el dominio, sin mirar cuáles pares están anotados,
usando tres patrones:

1. Una proposición marcada como disputada por su propia modalidad
   (`contested_fact`, `contested_legal_issue`, `contested_legal_finding`).
2. Estructura adversarial: `defense_claim` o `defense_argument_summary` frente a
   una modalidad oficial (`official_outcome`, `official_finding`,
   `judicially_proven_fact`, `officially_upheld_finding`, `official_summary`).
3. Dos `documented_procedural_fact` del mismo expediente, donde puede haber un
   vicio en la cadena procesal.

| Método | Precisión | Recall | F1 |
|---|---|---|---|
| `llama3.2:3b`, 4 clases | 0.167 | 0.059 | 0.087 |
| `llama3.2:3b`, binaria | 0.000 | 0.000 | 0.000 |
| **Reglas de ontología** | 0.205 | **0.529** | **0.295** |

**El código le gana al modelo por nueve veces en recall**, y además es
instantáneo, determinista y explicable: cada marca dice qué patrón la disparó.

## 7. Intento 4 · Híbrido: la regla propone, el modelo dispone

El diseño neuro-simbólico evidente: la regla genera candidatos —44 de 196
pares—, y el modelo confirma o descarta cada uno con una pregunta más fácil que
la clasificación abierta.

```
la regla propuso 44 candidatos de 196 pares
el modelo confirmó 43, descartó 1
HÍBRIDO: precisión 0.209  recall 0.529  F1 0.300
```

Prácticamente idéntico a la regla sola (F1 0.295). **El modelo no aporta
discriminación en ninguna dirección.**

Y ahí está la observación más útil de todo el experimento: preguntando de forma
abierta, el modelo responde `SUPPORTS` al 79% de los pares; preguntando "¿hay
tensión aquí?", responde que sí al 98% de los que se le presentan. **La
dirección de su respuesta la marca el encuadre de la pregunta, no el contenido
de las proposiciones.** Es una debilidad conocida de los modelos pequeños, y este
experimento la mide en un dominio concreto.

## 8. Qué se ships al final

La regla de ontología, **como triaje y no como veredicto**.

Reduce los 196 pares a 44 que merecen revisión humana y captura el 53% de las
tensiones anotadas. Presentada como *"estos pares podrían estar en tensión,
revísalos"*, es genuinamente útil en una herramienta de revisión: recorta el
trabajo a la cuarta parte.

Y es honesta en las dos direcciones: no finge que un modelo las descubrió, y no
recita la explicación que un humano ya había escrito.

---

## 6. Qué se preserva de este experimento

Aunque la función no entre al producto, el ejercicio deja tres cosas:

- **Un banco de pruebas reutilizable**: 196 pares con ground truth, y un script
  que mide precisión, recall y F1 en unos doce minutos.
- **Una medición de dónde está el techo** del modelo local, útil para decidir si
  conviene subir de tamaño.
- **La confirmación de que la anotación humana sirve mejor como examen que como
  contenido.** Ese cambio de rol es lo que separa un visor de base de datos de un
  sistema evaluado.
