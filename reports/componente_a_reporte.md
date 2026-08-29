# Componente A · Reporte de la tarea de entrenamiento

**Proyecto:** EvidenceLab — razonamiento probatorio sobre resoluciones judiciales
mexicanas
**Camino de datos elegido:** dominio propio (no la receta del módulo)
**Fecha:** 24 de agosto de 2026

---

## Datos

Se usó el corpus y el dataset de instrucciones construidos para la app.

| | |
|---|---|
| Corpus | 8 resoluciones oficiales de la SCJN y el CJF · 458 páginas · 1,097 chunks |
| Dataset supervisado | 600 ejemplos en español, 14 tareas |
| Split (por caso completo, sin fuga) | 387 train / 76 validation / 137 test |
| Derivado v3 (contratos + augmentation) | 412 / 76 / 137 |

El split se hizo por **caso completo**, no por ejemplos aleatorios: los casos de
train (001, 002, 004, 005, 007), validation (003) y test (006, 008) no se
mezclan. Así el modelo no puede ver hechos de un expediente en entrenamiento y
ser evaluado sobre otra pregunta del mismo expediente.

---

## Etapa 1 · Preentrenamiento continuo y adaptación de dominio

Modelo: `distilbert-base-multilingual-cased` (~135 M parámetros), masked
language modeling con `mlm_probability=0.15`, tres épocas, en GPU T4.

| Corrida | Corpus | Perplexity | Tiempo | VRAM pico |
|---|---|---|---|---|
| Modelo original | sin adaptar | 25.2223 | — | — |
| MLM base | 49 páginas | 11.2053 | 27.8 s | 5.43 GB |
| MLM extendido | 349 páginas (49 + 300) | **6.5764** | 87.4 s | 6.45 GB |

**Reducción total de perplexity: 73.9%.** La extensión del corpus con 300
páginas adicionales, que es la ampliación pedida, aportó por sí sola una caída
de 11.21 a 6.58: el lenguaje jurídico dejó de resultarle sorpresivo al modelo.

También se midieron desplazamientos de embeddings por término antes y después.
El mayor observado fue `absolución`, con un desplazamiento de ≈0.000922
*(cifra pendiente de confirmar contra los artefactos; ver Procedencia)*. **Es un
valor pequeño, y esa pequeñez es en sí un resultado:** el preentrenamiento
continuo movió mucho el ajuste global del modelo al dominio —lo dice la
perplexity— pero movió poco la representación individual de cada término. Con
349 páginas y tres épocas no hay señal suficiente para reubicar vectores
concretos; lo que se adaptó fue el modelo de lenguaje, no el significado
puntual de las palabras.

### DAPT, TAPT y si valió la pena

Gururangan et al. (2020), *Don't Stop Pretraining*, distingue dos formas de
seguir preentrenando antes de la tarea final. **DAPT** (*domain-adaptive
pretraining*) continúa el preentrenamiento sobre un corpus grande del dominio;
**TAPT** (*task-adaptive pretraining*) lo hace sobre el texto específico de la
tarea, mucho más pequeño pero mejor alineado. El hallazgo del artículo es que
ambos ayudan y que combinarlos ayuda más, incluso cuando el modelo base ya es
fuerte.

Esa es la lógica detrás de modelos como **FinBERT**, un BERT readaptado a texto
financiero, o **BloombergGPT**, entrenado desde cero con décadas de datos
propietarios de Bloomberg. La apuesta es que el vocabulario y las convenciones
de un dominio cerrado —jurídico, financiero, clínico— justifican el costo de
adaptar.

**En nuestro caso, lo honesto es responder que sí funcionó y no se usó.**

Funcionó: 73.9% de reducción de perplexity es evidencia clara de adaptación, y
lo que hicimos está más cerca de TAPT que de DAPT, porque 349 páginas es un
corpus de tarea, no de dominio.

Y no se usó: el producto final es un sistema **generativo con RAG**, no un
clasificador basado en un encoder. La factualidad la aporta la recuperación de
evidencia, no el conocimiento internalizado del modelo. Un DistilBERT adaptado
no tiene lugar en esa arquitectura. Podría haberlo tenido como modelo de
embeddings para el retriever, pero ahí un multilingüe entrenado
específicamente para recuperación (`multilingual-e5-base`) rinde mucho más que
un encoder adaptado con 349 páginas.

La conclusión práctica: **la adaptación de dominio paga cuando el conocimiento
tiene que vivir en los pesos.** Si el sistema va a consultar sus fuentes en cada
respuesta, ese mismo esfuerzo rinde más invertido en el retriever.

---

## Etapa 2 · Fine-tuning supervisado completo (Full SFT)

Modelo: `microsoft/Phi-4-mini-instruct`, **3,836,021,760 parámetros, 100%
entrenables**. GPU T4 de 14.56 GB, `packing=True`, `max_length=1280`,
`completion_only_loss=True`.

**Restricción de hardware y cómo se resolvió.** DeepSpeed falló por RAM del
host. Se migró a **AdaLomo**, un optimizador que evita los estados de momento
de AdamW y permite mantener el 100% de los parámetros entrenables en una T4. Fue
necesario desactivar el `GradScaler` de fp16 porque entraba en conflicto con
AdaLomo.

**Búsqueda de learning rate** (387 train / 76 validation a 1280 tokens):

| Learning rate | Train loss | Eval loss |
|---|---|---|
| 1e-5 | 1.8455 | 2.7344 |
| 5e-5 | 1.3271 | 1.8190 |
| **1e-4** | **0.9986** | **1.5773** |

Ganó `1e-4`. La lectura: con solo 412 ejemplos y una época, los learning rates
bajos no alcanzan a mover un modelo de 3.8 mil millones de parámetros lo
suficiente. El presupuesto de actualizaciones es tan corto que hace falta un
paso agresivo para que el entrenamiento deje huella.

**Corrida final:** eval loss **0.274236**, 761.5 s (12.7 min), VRAM pico
**14.037 GB** — prácticamente el techo de la T4.

**Comparaciones cualitativas.** Sobre cinco prompts fijos del caso de test
CASE-MX-006, comparando el modelo base contra el fine-tuneado, la versión v3
con contratos explícitos logró **5/5 JSON válido y 5/5 schema superior
correcto**, con **1/5 coincidencia exacta**.

Ese 1/5 fue el hallazgo más útil de la etapa. Al revisar los cuatro fallos
resultó que varios no eran errores del modelo sino problemas del target: un
`expected` exigía citar la página 49 cuando esa información no estaba en el
input, y otro imponía un orden cronológico discutible. **La coincidencia exacta
de JSON quedó descartada como métrica principal** y se conservó solo como
diagnóstico.

Una corrida anterior a 512 tokens dio un eval loss aparentemente mejor (0.1369),
pero **no es comparable**: filtró a 300 train / 59 validation, excluyendo los
ejemplos largos, incluidos los cinco de `case_reconstruction`.

---

## Etapa 3 · LoRA y QLoRA

Modelo: `meta-llama/Llama-3.2-3B-Instruct`. Módulos objetivo `q_proj, k_proj,
v_proj, o_proj, gate_proj, up_proj, down_proj`. QLoRA con NF4 de 4 bits, doble
cuantización y cómputo en fp16.

### Curva de calidad contra rango

Barrido con 60 pasos por rango, todo lo demás constante:

| r | Train loss | Eval loss | Mejora vs. anterior | VRAM pico | Tiempo |
|---|---|---|---|---|---|
| 4 | 0.4918 | 0.2863 | — | 5.92 GB | 870 s |
| 8 | 0.4102 | 0.1838 | −35.8% | 6.02 GB | 872 s |
| 16 | 0.3458 | 0.1314 | −28.5% | 6.20 GB | 873 s |
| 32 | 0.2888 | 0.1035 | −21.3% | 6.56 GB | 875 s |
| 64 | 0.2467 | **0.0924** | −10.7% | 7.30 GB | 881 s |

**Cada duplicación del rango produce aproximadamente la mitad de la mejora que
la anterior.** El eval loss sigue bajando hasta r=64, así que no es cierto que
"más rango sea peor". Lo que ocurre es que el beneficio marginal se desploma
mientras el costo de memoria crece, y en algún punto se cruzan:

| Salto | Mejora de eval loss | Aumento de VRAM |
|---|---|---|
| 4 → 8 | 35.8% | 1.5% |
| 8 → 16 | 28.5% | 3.0% |
| 16 → 32 | 21.3% | 5.8% |
| 32 → 64 | 10.7% | 11.3% |

Hasta r=32 cada duplicación compra mucha más calidad de la memoria que cuesta.
En el salto a r=64 los dos porcentajes se igualan: **ese cruce es la señal
cuantitativa del punto de rendimientos decrecientes.** Para este dataset el
equilibrio está en r=16 o r=32; r=64 gana en la métrica, pero ya paga
proporcionalmente lo que obtiene.

El tiempo es casi indiferente al rango (870 → 881 s, +1.3%): el cuello de
botella es el paso hacia adelante y atrás del modelo base, no las matrices del
adapter.

### Tabla comparativa de las tres técnicas

| Técnica | Modelo base | r | Params entrenables | % | Eval loss | Tiempo | VRAM pico | JSON válido | Schema exacto |
|---|---|---|---|---|---|---|---|---|---|
| Full SFT | Phi-4-mini | — | 3,836 M | 100% | 0.2742 | 761 s | **14.04 GB** | — | — |
| LoRA | Llama-3.2-3B | 16 | 24.3 M | 0.76% | 0.1568 | 668 s | 8.43 GB | 100% | 100% |
| QLoRA | Llama-3.2-3B | 64 | 97.3 M | 3.03% | **0.0975** | 784 s | **7.30 GB** | 100% | 100% |

**El resultado central: QLoRA consumió menos memoria que LoRA (7.30 contra 8.43
GB) mientras entrenaba cuatro veces más parámetros.** Cuantizar el modelo base
a 4 bits libera memoria suficiente para pagar un adapter mucho más grande.

El costo es tiempo: QLoRA tardó **17% más** (784 s contra 668 s), porque
descuantizar los pesos en cada paso cuesta cómputo. Es un intercambio explícito
de tiempo por memoria.

Contra Full SFT, el contraste es más fuerte: entrenar el 3% de los parámetros
con QLoRA usó **la mitad de la VRAM** que entrenar el 100% con AdaLomo.

### Merge y tamaños en disco

| Artefacto | Tamaño |
|---|---|
| Adapter LoRA r16 | 109 MB |
| Adapter QLoRA r64 | 387 MB |
| Modelo fundido fp16 (`merge_and_unload`) | **6.00 GB** |

El adapter QLoRA es **15.9× más chico** que el modelo fundido; el LoRA r16, 56×.
Esa es la economía práctica de PEFT: se distribuyen megabytes de especialización
sobre un modelo base que ya está en la máquina, en vez de gigabytes por cada
variante.

---

## Síntesis y decisión

**¿Cuándo adaptar dominio en vez de fine-tunear instrucciones?** Cuando el
conocimiento tiene que residir en los pesos. La adaptación enseña *cómo se habla*
en un dominio; el fine-tuning de instrucciones enseña *cómo obedecer*. En
EvidenceLab la perplexity cayó 73.9%, demostrando que la adaptación funciona,
pero el modelo adaptado no llegó al producto: como el sistema consulta sus
fuentes en cada respuesta, ese esfuerzo rinde más invertido en el retriever.

**¿Cuándo Full SFT y cuándo QLoRA?** Con estos números, QLoRA gana en casi
todo. Full SFT exigió 14.04 GB —el límite de la T4—, obligó a abandonar AdamW y
migrar a AdaLomo, y aun así dio un eval loss peor (0.274 contra 0.098). Full SFT
se justifica cuando se quiere cambiar el comportamiento del modelo de forma
profunda y se tiene el hardware; para especializar formato y patrones de
razonamiento sobre un dataset de cientos de ejemplos, **QLoRA es la opción
correcta y no es un compromiso**.

**¿Qué costó cada técnica y qué dio a cambio?**

| | Costo en memoria | Costo en tiempo | A cambio |
|---|---|---|---|
| MLM continuado | 6.45 GB | 87 s | −73.9% perplexity; adaptación real que no llegó al producto |
| Full SFT | 14.04 GB | 761 s | control total de los pesos; forzó cambiar de optimizador |
| LoRA | 8.43 GB | 668 s | 99.2% menos parámetros entrenables; adapter de 109 MB |
| QLoRA | **7.30 GB** | 784 s | mejor eval loss, menor memoria, adapter portable de 387 MB |

La lección que resume la tarea: **el eje que más importó no fue la calidad sino
la memoria.** Todas las decisiones técnicas del proyecto —AdaLomo en vez de
DeepSpeed, migrar a otra GPU, cuantizar a 4 bits— salieron del techo de 14.56 GB
de una T4, no de perseguir un número de loss.

---

## Limitaciones metodológicas

Se declaran explícitamente porque afectan cómo deben leerse las tablas.

**La fila de Full SFT usa otro modelo base.** Phi-4-mini contra
Llama-3.2-3B. La comparación de tres técnicas no es limpia y no debe leerse
como tal. LoRA y QLoRA sí son comparables entre sí: comparten modelo base,
dataset, longitud de secuencia y configuración de entrenamiento.

**LoRA r16 contra QLoRA r64 mezcla dos variables.** La diferencia de eval loss
no puede atribuirse a la cuantización, porque el rango también cambió cuatro
veces. El efecto aislado del rango está en el barrido, donde todo lo demás se
mantuvo fijo.

**El eval loss se calcula sobre un solo caso.** El split de validation es
CASE-MX-003 completo, 76 ejemplos. Un eval loss bajo indica ajuste a ese
expediente, no generalización a expedientes nuevos.

**El dataset es pequeño y poco diverso.** 412 ejemplos derivados de 5 casos
independientes, con instrucciones muy plantilladas. Las 25 variantes de
`case_reconstruction` son permutaciones de 5 casos, no 25 casos nuevos.

**El golden set de 48 ejemplos está contaminado.** 30 de ellos son ejemplos del
split de entrenamiento. No se usó para ninguna cifra de este reporte y hay que
reconstruirlo antes de sostener cualquier afirmación de calidad final.

**`exact_json` no es una métrica de calidad.** Ambos adapters dieron 20% (1/5),
igual que Phi. La revisión manual mostró que varios targets exigían información
no deducible del input. Se conserva como diagnóstico.

---

## Procedencia de las cifras

No todas las cifras de este reporte tienen el mismo respaldo, y conviene
declararlo antes que presentarlas como equivalentes.

| Etapa | Fuente | Estado |
|---|---|---|
| 3 · LoRA y QLoRA | `artifacts/03_llama32_lora_qlora/*.csv` y medición directa de los adapters en disco | **Verificado**. Regenerable con `scripts/build_training_report.py` |
| 1 · MLM | Documento de handoff del proyecto (20-ago-2026) | Pendiente de confirmar contra los artefactos de la corrida |
| 2 · Full SFT | Documento de handoff del proyecto (20-ago-2026) | Pendiente de confirmar contra los artefactos de la corrida |

Los notebooks ejecutados de las etapas 1 y 2, con sus salidas, están en Drive;
las copias locales disponibles no conservan outputs. Antes de la entrega final
deben recuperarse `artifacts/01_mlm/` y
`artifacts/02_phi_full_sft_v3_contract_augmented/`, o los `mlruns`
correspondientes, y contrastar cada valor. Cualquier cifra que no aparezca en un
artefacto debe marcarse como no registrada, no reconstruirse por estimación.

## Reproducibilidad

La etapa 3 se registró en **MLflow** (`report_to=["mlflow"]`, tracking en
Drive), verificado en el notebook. El registro de las etapas 1 y 2 está
declarado en la documentación del proyecto pero no se ha confirmado contra
`mlruns`.

Los datasets están congelados con manifiesto y hashes
(`RELEASE_MANIFEST.json`); el derivado v3 se regenera de forma determinista con
`scripts/build_sft_v3.py` a partir del corpus congelado, con semilla fija.

Todas las tablas de este reporte se regeneran sin GPU con:

```bash
python scripts/build_training_report.py
```

que lee los CSV de `artifacts/03_llama32_lora_qlora/` y escribe
`reports/componente_a_*.csv` más la gráfica `qlora_rank_curve.png`.

**Notebooks:** `01_domain_adaptation_mlm_evidencelab.ipynb`,
`02_Full_SFT_Phi4_Colab_T4_FINAL_v3.ipynb`,
`03_LoRA_QLoRA_Llama32_Colab_T4_FINAL.ipynb`.

### Nota sobre un error corregido

La celda que mide tamaños en disco reportó el adapter QLoRA como 6.38 GB. El
modelo fundido se guarda **anidado dentro** de la carpeta del adapter, y la
función de medición recorre recursivamente, así que sumaba los 6 GB del modelo
completo. El tamaño real del adapter es 387 MB. La corrección está implementada
en `scripts/build_training_report.py`, que excluye explícitamente el
subdirectorio del merge. El error invertía justamente la conclusión que esta
etapa demuestra.
