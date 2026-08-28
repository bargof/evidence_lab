"""Detección de contradicciones entre proposiciones de un mismo expediente.

El modelo clasifica cada par de proposiciones sin ver nunca la anotación humana.
Las 17 tensiones anotadas en `relations.jsonl` se reservan como ground truth
para medir la detección, no se le muestran ni se le insinúan.

Esa separación es el punto: mostrar una explicación ya escrita convertiría la
app en un visor de base de datos. Aquí el sistema tiene que encontrarlas.
"""

import itertools
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from evidence_lab.application.services import generation, prompting
from evidence_lab.config.settings import get_settings

_settings = get_settings()

# Respuestas cortas: son 196 pares y cada token cuesta segundos en CPU.
PAIR_NUM_PREDICT = 120


class Relation(StrEnum):
    CONTRADICTS = "CONTRADICTS"
    SUPPORTS = "SUPPORTS"
    COMPATIBLE_WITH = "COMPATIBLE_WITH"
    INSUFFICIENT_FOR = "INSUFFICIENT_FOR"


@dataclass
class PairVerdict:
    case_id: str
    source_id: str
    target_id: str
    source_text: str
    target_text: str
    relation: Relation | None
    reason: str = ""
    raw: str = ""
    elapsed_seconds: float = 0.0

    @property
    def is_contradiction(self) -> bool:
        return self.relation == Relation.CONTRADICTS


@dataclass
class CaseContradictions:
    case_id: str
    verdicts: list[PairVerdict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def detected(self) -> list[PairVerdict]:
        return [v for v in self.verdicts if v.is_contradiction]


def load_propositions(path: Path | None = None) -> list[dict]:
    ruta = path or (_settings.global_dir / "propositions.jsonl")
    with ruta.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def annotated_contradictions(path: Path | None = None) -> set[frozenset[str]]:
    """Ground truth. Se carga solo para evaluar, jamás para el prompt."""
    ruta = path or (_settings.global_dir / "relations.jsonl")
    pares = set()

    with ruta.open("r", encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            fila = json.loads(linea)
            if fila.get("relation_type") == "TENSION_OR_CONTRADICTION":
                pares.add(frozenset({fila["source_node"], fila["target_node"]}))

    return pares


def _describe(proposicion: dict) -> str:
    return (
        f"[{proposicion['proposition_id']}] "
        f"(modalidad: {proposicion.get('modality', 'desconocida')}) "
        f"{proposicion['statement_es']}"
    )


class ContradictionService:
    def __init__(self, prompt_version: int | None = None):
        self.prompt = prompting.load_prompt("contradiction", prompt_version)
        self.propositions = load_propositions()

    def classify_pair(self, a: dict, b: dict) -> PairVerdict:
        mensaje = (
            f"PROPOSICIÓN A:\n{_describe(a)}\n\n"
            f"PROPOSICIÓN B:\n{_describe(b)}\n\n"
            "¿Qué relación evidencial hay entre A y B?"
        )

        started = time.perf_counter()
        generado = generation.generate(
            [
                {"role": "system", "content": self.prompt.body},
                {"role": "user", "content": mensaje},
            ],
            num_predict=PAIR_NUM_PREDICT,
        )
        elapsed = time.perf_counter() - started

        payload = generation.extract_json(generado.text) or {}
        etiqueta = str(payload.get("relation", "")).strip().upper()

        try:
            relacion = Relation(etiqueta)
        except ValueError:
            relacion = None

        return PairVerdict(
            case_id=a["case_id"],
            source_id=a["proposition_id"],
            target_id=b["proposition_id"],
            source_text=a["statement_es"],
            target_text=b["statement_es"],
            relation=relacion,
            reason=str(payload.get("reason", ""))[:300],
            raw=generado.text[:400],
            elapsed_seconds=round(elapsed, 2),
        )

    def analyze_case(self, case_id: str, progress=None) -> CaseContradictions:
        props = [p for p in self.propositions if p["case_id"] == case_id]
        pares = list(itertools.combinations(props, 2))

        started = time.perf_counter()
        resultado = CaseContradictions(case_id=case_id)

        for indice, (a, b) in enumerate(pares, start=1):
            resultado.verdicts.append(self.classify_pair(a, b))
            if progress:
                progress(indice, len(pares))

        resultado.elapsed_seconds = time.perf_counter() - started
        return resultado
