"""Ordenamiento cronológico de hechos dispersos.

Responde la pregunta que dio origen al proyecto: dados hechos sueltos y
desordenados, ¿puede el sistema reconstruir la secuencia?

Los hechos se le entregan **barajados**, y el orden curado se reserva como
ground truth. Es la misma idea que en la detección de contradicciones: en vez de
mostrar el orden que ya está anotado, el sistema tiene que producirlo y se mide
qué tan cerca quedó.

La tarea no es trivial. De las 52 marcas temporales del corpus, solo 23 son
fechas completas: 16 son años sueltos y 13 son relativas (`incident`,
`days_later`, `23_days_later`). Ordenar exige razonar sobre el dominio, no
ordenar cadenas.
"""

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from evidence_lab.application.services import generation, prompting
from evidence_lab.config.settings import get_settings

_settings = get_settings()

ORDERING_NUM_PREDICT = 500


@dataclass
class OrderingResult:
    case_id: str
    predicted: list[str] = field(default_factory=list)
    truth: list[str] = field(default_factory=list)
    shuffled: list[str] = field(default_factory=list)
    reasoning: str = ""
    raw: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    with_dates: bool = True

    @property
    def is_valid(self) -> bool:
        """La predicción debe ser una permutación exacta de la entrada."""
        return bool(self.predicted) and sorted(self.predicted) == sorted(self.truth)

    @property
    def exact_match(self) -> bool:
        return self.predicted == self.truth

    @property
    def kendall_tau(self) -> float | None:
        """Concordancia de pares: 1.0 es orden perfecto, -1.0 es el inverso.

        Se usa tau y no "aciertos por posición" porque lo que importa aquí es el
        orden relativo entre hechos, no que cada uno caiga en su casilla exacta:
        un solo hecho fuera de lugar desplaza a todos los que le siguen y
        castigaría de más.
        """
        if not self.is_valid or len(self.truth) < 2:
            return None

        posicion = {eid: i for i, eid in enumerate(self.predicted)}
        concordantes = discordantes = 0

        for i in range(len(self.truth)):
            for j in range(i + 1, len(self.truth)):
                a, b = self.truth[i], self.truth[j]
                if posicion[a] < posicion[b]:
                    concordantes += 1
                else:
                    discordantes += 1

        total = concordantes + discordantes
        return round((concordantes - discordantes) / total, 4) if total else None

    @property
    def pairs_correct(self) -> float | None:
        """Proporción de pares en el orden correcto. Más legible que tau."""
        tau = self.kendall_tau
        return round((tau + 1) / 2, 4) if tau is not None else None


_LABEL = re.compile(r"H\d+")


def extract_order(texto: str, payload: dict) -> list[str]:
    """Lee la secuencia de identificadores que produjo el modelo.

    El contrato pide `{"order": [...]}`, pero el modelo a veces devuelve un
    arreglo suelto de objetos, o una lista numerada en prosa. En todos esos
    casos **la respuesta está ahí**: el orden en que aparecen los identificadores
    es el orden que propuso.

    Por eso el último recurso es leer los identificadores por orden de aparición
    en el texto crudo. No inventa nada: si el modelo no nombró un hecho, no
    aparece, y la permutación quedará incompleta y se marcará inválida.
    """
    orden = payload.get("order")
    if isinstance(orden, list):
        limpio = [str(x) for x in orden if isinstance(x, str)]
        if limpio:
            return limpio

    # Arreglo de objetos: [{"id": "..."}, ...] o [{"event_id": "..."}, ...]
    if isinstance(orden, list):
        desde_objetos = [
            str(item.get("id") or item.get("event_id"))
            for item in orden
            if isinstance(item, dict) and (item.get("id") or item.get("event_id"))
        ]
        if desde_objetos:
            return desde_objetos

    vistos: list[str] = []
    for encontrado in _LABEL.findall(texto):
        if encontrado not in vistos:
            vistos.append(encontrado)
    return vistos


def load_events(path: Path | None = None) -> list[dict]:
    ruta = path or (_settings.global_dir / "events.jsonl")
    with ruta.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class OrderingService:
    def __init__(self, prompt_version: int | None = None):
        self.prompt = prompting.load_prompt("ordering", prompt_version)
        self.events = load_events()

    def case_events(self, case_id: str) -> list[dict]:
        """Los hechos del caso en su orden curado.

        La numeración E1..En es el orden cronológico anotado, verificado contra
        las 44 relaciones BEFORE del corpus: las 44 son consistentes con ella.
        """
        propios = [e for e in self.events if e["case_id"] == case_id]
        return sorted(propios, key=lambda e: int(e["event_id"].rsplit("E", 1)[1]))

    def order(
        self, case_id: str, seed: int | None = None, with_dates: bool = True
    ) -> OrderingResult:
        """Baraja los hechos, pide al modelo ordenarlos y compara.

        `with_dates=False` oculta las marcas temporales. Es la versión difícil:
        obliga a ordenar solo por la lógica de los hechos y del proceso.
        """
        eventos = self.case_events(case_id)
        verdad = [e["event_id"] for e in eventos]

        barajados = list(eventos)
        random.Random(seed if seed is not None else _settings.random_seed).shuffle(
            barajados
        )

        # Etiquetas anónimas asignadas en el orden barajado. Los identificadores
        # reales (E1..En) codifican el orden cronológico curado: mostrárselos al
        # modelo le permitiría ordenar por el número del identificador sin leer
        # un solo hecho. Con H1..Hn asignadas tras barajar, la etiqueta no dice
        # nada sobre la posición correcta.
        etiquetas = {
            evento["event_id"]: f"H{i}" for i, evento in enumerate(barajados, start=1)
        }
        reales = {etiqueta: eid for eid, etiqueta in etiquetas.items()}

        lineas = []
        for evento in barajados:
            marca = f" · momento: {evento['time_expression']}" if with_dates else ""
            lineas.append(
                f"- {etiquetas[evento['event_id']]}{marca}\n  {evento['description']}"
            )

        mensaje = (
            "HECHOS DE UN EXPEDIENTE, EN DESORDEN:\n\n"
            + "\n".join(lineas)
            + "\n\nOrdénalos cronológicamente."
        )

        started = time.perf_counter()
        generado = generation.generate(
            [
                {"role": "system", "content": self.prompt.body},
                {"role": "user", "content": mensaje},
            ],
            num_predict=ORDERING_NUM_PREDICT,
        )
        elapsed = time.perf_counter() - started

        payload = generation.extract_json(generado.text) or {}
        # El modelo trabaja con etiquetas anónimas; se traducen de vuelta a los
        # identificadores reales para poder comparar contra el orden curado.
        predicho = [
            reales[etiqueta]
            for etiqueta in extract_order(generado.text, payload)
            if etiqueta in reales
        ]

        return OrderingResult(
            case_id=case_id,
            predicted=predicho,
            truth=verdad,
            shuffled=[e["event_id"] for e in barajados],
            reasoning=str(payload.get("reasoning", ""))[:300],
            raw=generado.text[:600],
            labels=etiquetas,
            elapsed_seconds=round(elapsed, 2),
            with_dates=with_dates,
        )
