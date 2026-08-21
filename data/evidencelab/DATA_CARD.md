# Data Card - EvidenceLab Criminal ES v0.3

## Propósito
Entrenar y evaluar un modelo bilingüe/español para reconstrucción documental de casos, extracción de proposiciones, cronologías, contradicciones y evaluación de teorías.

## Fuentes
Resoluciones judiciales oficiales en versión pública de la SCJN y el Poder Judicial de la Federación. Las URLs se conservan en cada manifest y registro documental.

## Cobertura
8 casos: homicidio, feminicidio, privación de libertad, robo y coautoría. Incluye condenas subsistentes, revocaciones y reenvíos.

## Idioma
100% español en los ejemplos de entrenamiento.

## PII
Las resoluciones son versiones públicas con datos personales testados. No se intentó reidentificar a las personas.

## Riesgos
- Sesgo de selección judicial.
- Las sentencias resumen pruebas originales.
- Un resultado judicial no debe extrapolarse a casos nuevos.
- El modelo no debe declarar culpabilidad fuera de una resolución oficial o sin evidencia suficiente.

## Anotación
La capa de proposiciones, eventos, relaciones y teorías es curada para el MVP, pero no exhaustiva. Los 600 ejemplos se generan de manera controlada a partir de esa capa. Se recomienda revisión humana estratificada antes del entrenamiento final.
