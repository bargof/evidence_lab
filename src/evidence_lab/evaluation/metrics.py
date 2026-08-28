"""Métricas de recuperación.

Se calculan sobre el golden set: preguntas cuya página correcta se conoce de
antemano, tomada de la capa curada del corpus y **no** de lo que el retriever
devuelve. Esa dirección importa: si la verdad de referencia saliera del propio
sistema, estaríamos calificándolo con su propio examen.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GoldenItem:
    golden_id: str
    case_id: str
    question: str
    question_type: str
    expected_behavior: str
    expected_sources: list[tuple[str, int]]
    reference_answer: str = ""

    @property
    def is_answerable(self) -> bool:
        return self.expected_behavior == "answer"


def load_golden(path: Path) -> list[GoldenItem]:
    items = []
    with path.open("r", encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            fila = json.loads(linea)
            items.append(
                GoldenItem(
                    golden_id=fila["golden_id"],
                    case_id=fila["case_id"],
                    question=fila["question"],
                    question_type=fila["question_type"],
                    expected_behavior=fila["expected_behavior"],
                    expected_sources=[
                        (s["document_id"], int(s["page_number"]))
                        for s in fila.get("expected_sources", [])
                    ],
                    reference_answer=fila.get("reference_answer", ""),
                )
            )
    return items


@dataclass
class RetrievalOutcome:
    """Resultado de una consulta contra su respuesta esperada."""

    golden_id: str
    question_type: str
    hit: bool
    rank: int | None  # posición 1-indexada del primer acierto
    retrieved: list[tuple[str, int]] = field(default_factory=list)

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.rank if self.rank else 0.0


def evaluate_one(
    expected: list[tuple[str, int]], retrieved: list[tuple[str, int]]
) -> RetrievalOutcome:
    """Compara páginas esperadas contra recuperadas.

    Se compara a nivel de página, no de fragmento: la promesa del producto es
    citar documento y página, y dos fragmentos de la misma página satisfacen
    igual de bien esa promesa.
    """
    esperadas = set(expected)

    for posicion, fuente in enumerate(retrieved, start=1):
        if fuente in esperadas:
            return RetrievalOutcome("", "", True, posicion, retrieved)

    return RetrievalOutcome("", "", False, None, retrieved)


def aggregate(outcomes: list[RetrievalOutcome], k_values=(1, 3, 5, 10)) -> dict:
    """Resume una corrida completa.

    - `recall@k`: proporción de preguntas cuya página correcta aparece entre las
      primeras k. Responde "¿la encontró?".
    - `MRR`: media del inverso de la posición del primer acierto. Responde
      "¿y qué tan arriba la puso?". Distingue traerla en primer lugar de
      traerla en sexto, cosa que recall no ve.
    """
    if not outcomes:
        return {}

    total = len(outcomes)
    resumen = {
        "preguntas": total,
        "mrr": round(sum(o.reciprocal_rank for o in outcomes) / total, 4),
    }

    for k in k_values:
        aciertos = sum(1 for o in outcomes if o.rank is not None and o.rank <= k)
        resumen[f"recall@{k}"] = round(aciertos / total, 4)

    return resumen


def aggregate_by_type(outcomes: list[RetrievalOutcome]) -> dict[str, dict]:
    por_tipo: dict[str, list[RetrievalOutcome]] = {}
    for outcome in outcomes:
        por_tipo.setdefault(outcome.question_type, []).append(outcome)
    return {tipo: aggregate(items) for tipo, items in sorted(por_tipo.items())}
