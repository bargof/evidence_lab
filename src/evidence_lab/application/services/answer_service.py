"""Caso de uso principal: responder una pregunta con evidencia trazable.

Orquesta recuperación, generación y validación. No sabe nada de interfaces: lo
mismo lo llama Gradio que un test o un script de evaluación. Esa frontera es
deliberada.

    pregunta → retriever → prompt con evidencia → modelo local
             → parseo → validación estructural → validación de fuentes
             → respuesta con citas, o negativa explicada
"""

import time
from dataclasses import dataclass, field

from evidence_lab.application.services import generation, prompting
from evidence_lab.config.settings import get_settings
from evidence_lab.data.schemas import (
    CaseTimeline,
    Citation,
    GroundedAnswer,
    Refusal,
    RetrievalDebug,
)
from evidence_lab.guardrails.validation import (
    ValidationReport,
    validate,
    validate_timeline,
)
from evidence_lab.rag.index import HybridIndex
from evidence_lab.rag.retriever import ITERATIONS, RetrievedChunk, Retriever

_settings = get_settings()

# Consultas semilla para la reconstrucción. Cubren las tres capas que toda
# resolución mezcla: los hechos, lo que se declaró, y lo que se resolvió.
TIMELINE_QUERIES = (
    "hechos ocurridos fecha lugar del incidente",
    "declaraciones de testigos y versiones de las partes",
    "resolución del tribunal sentencia efectos",
    "actuaciones del proceso demanda audiencia plazos",
)
TIMELINE_MAX_CHUNKS = 10
# Una cronología de 8 hechos con sus citas no cabe en el presupuesto normal
# de tokens; se le da el doble y aun así se repara si se corta.
TIMELINE_NUM_PREDICT = 1600


@dataclass
class Timeline:
    """Resultado de una reconstrucción cronológica."""

    case_id: str
    timeline: CaseTimeline | None = None
    refusal: Refusal | None = None
    retrieval: RetrievalDebug | None = None
    prompt_version: str = ""
    model: str = ""
    raw_text: str = ""
    errors: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.timeline is not None


@dataclass
class Answer:
    """Todo lo que produjo una consulta, incluido el rastro para auditarla."""

    question: str
    case_id: str | None = None
    grounded: GroundedAnswer | None = None
    refusal: Refusal | None = None
    retrieval: RetrievalDebug | None = None
    validation: ValidationReport | None = None
    prompt_version: str = ""
    model: str = ""
    raw_text: str = ""
    attempts: int = 0
    elapsed_seconds: float = 0.0
    sources: list[Citation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.grounded is not None


class AnswerService:
    def __init__(
        self,
        index: HybridIndex | None = None,
        retriever: Retriever | None = None,
        prompt_version: int | None = None,
    ):
        self.index = index or HybridIndex.load()
        self.retriever = retriever or Retriever(
            self.index,
            ITERATIONS["v5_con_antecedentes"],
        )
        self.prompt = prompting.load_prompt("system", prompt_version)
        self.timeline_prompt = prompting.load_prompt("timeline")

    def warmup(self) -> float:
        """Carga los modelos de embeddings y re-ranking antes de la primera pregunta.

        Sin esto, la primera consulta paga ~16 s de carga y parece que la app se
        colgó. Es puro costo de arranque, pero en una demo en vivo la primera
        impresión la da precisamente esa consulta.
        """
        started = time.perf_counter()
        self.retriever.search("consulta de calentamiento", case_id=None)
        return time.perf_counter() - started

    # --- pasos ------------------------------------------------------------
    def retrieve(self, question: str, case_id: str | None) -> tuple[list, float]:
        started = time.perf_counter()
        chunks = self.retriever.search(question, case_id=case_id)
        return chunks, time.perf_counter() - started

    def _debug(
        self,
        question: str,
        case_id: str | None,
        chunks: list[RetrievedChunk],
        secs: float,
    ) -> RetrievalDebug:
        return RetrievalDebug(
            query=question,
            case_id=case_id,
            retriever=self.retriever.config.name,
            chunks=[
                Citation(
                    document_id=c.chunk.document_id,
                    page_number=c.chunk.page_number,
                    chunk_id=c.chunk.chunk_id,
                    source_url=c.chunk.source_url,
                )
                for c in chunks
            ],
            elapsed_seconds=round(secs, 3),
        )

    # --- caso de uso ------------------------------------------------------
    def answer(self, question: str, case_id: str | None = None) -> Answer:
        started = time.perf_counter()

        result = Answer(
            question=question,
            case_id=case_id,
            model=_settings.ollama_model,
            prompt_version=self.prompt.identifier,
        )

        chunks, retrieval_secs = self.retrieve(question, case_id)
        result.retrieval = self._debug(question, case_id, chunks, retrieval_secs)

        if not chunks:
            result.refusal = Refusal(
                reason=(
                    "No encontré evidencia en el corpus para esa pregunta. "
                    "Puede estar fuera de los ocho casos disponibles."
                ),
                category="fuera_de_alcance",
                suggestion="Prueba con una pregunta sobre el expediente seleccionado.",
            )
            result.elapsed_seconds = time.perf_counter() - started
            return result

        messages, _ = prompting.build_messages(question, chunks, self.prompt)

        # Un reintento: si el 3B rompe el contrato, se le devuelve el error
        # concreto. Si vuelve a fallar, el sistema se niega en vez de inventar.
        for intento in (1, 2):
            result.attempts = intento

            generated = generation.generate(messages)
            result.raw_text = generated.text

            payload = generation.extract_json(generated.text)

            if payload is not None:
                report = validate(payload, chunks)
                result.validation = report

                if report.ok:
                    result.grounded = report.answer
                    result.sources = report.answer.sources
                    result.elapsed_seconds = time.perf_counter() - started
                    return result

                errores = report.errors
            else:
                errores = ["La respuesta no contenía un objeto JSON válido."]
                result.validation = ValidationReport(errors=errores)

            if intento == 1:
                messages = messages + [
                    {"role": "assistant", "content": generated.text},
                    {
                        "role": "user",
                        "content": (
                            "Tu respuesta no cumplió el contrato:\n"
                            + "\n".join(f"- {e}" for e in errores)
                            + "\n\nCorrígela. Devuelve solo el objeto JSON, con "
                            "cada afirmación citando una etiqueta de documento y "
                            "página que aparezca en la evidencia entregada."
                        ),
                    },
                ]

        result.refusal = Refusal(
            reason=(
                "El modelo no produjo una respuesta que pudiera validarse contra "
                "las fuentes recuperadas. Prefiero no responder a arriesgar una "
                "afirmación sin respaldo."
            ),
            category="evidencia_insuficiente",
            suggestion="Reformula la pregunta o revisa las fuentes recuperadas.",
        )
        result.elapsed_seconds = time.perf_counter() - started
        return result

    # --- reconstrucción cronológica ---------------------------------------
    def reconstruct(self, case_id: str) -> Timeline:
        """Reconstruye la cronología de un expediente.

        Recupera con varias consultas semilla en vez de una: una cronología
        necesita cubrir el expediente completo —los hechos, lo que declararon
        los testigos y lo que resolvió el tribunal— y una sola consulta tiende
        a traer seis fragmentos de la misma sección.
        """
        started = time.perf_counter()

        result = Timeline(
            case_id=case_id,
            model=_settings.ollama_model,
            prompt_version=self.timeline_prompt.identifier,
        )

        vistos: dict[str, RetrievedChunk] = {}
        for consulta in TIMELINE_QUERIES:
            for item in self.retriever.search(consulta, case_id=case_id):
                vistos.setdefault(item.chunk.chunk_id, item)

        chunks = sorted(vistos.values(), key=lambda c: c.chunk.page_number)
        chunks = chunks[:TIMELINE_MAX_CHUNKS]

        result.retrieval = self._debug(
            "reconstrucción cronológica",
            case_id,
            chunks,
            time.perf_counter() - started,
        )

        if not chunks:
            result.refusal = Refusal(
                reason="No hay evidencia recuperable para ese expediente.",
                category="fuera_de_alcance",
            )
            result.elapsed_seconds = time.perf_counter() - started
            return result

        messages, _ = prompting.build_timeline_messages(
            case_id, chunks, self.timeline_prompt
        )

        generated = generation.generate(messages, num_predict=TIMELINE_NUM_PREDICT)
        result.raw_text = generated.text

        payload = generation.extract_json(generated.text)
        if payload is None:
            result.refusal = Refusal(
                reason="El modelo no devolvió una cronología en formato válido.",
                category="evidencia_insuficiente",
                suggestion=(
                    "Vuelve a intentarlo; el modelo local a veces rompe el formato."
                ),
            )
            result.elapsed_seconds = time.perf_counter() - started
            return result

        timeline, errores, descartes = validate_timeline(payload, chunks, case_id)
        result.errors = errores
        result.dropped = descartes

        if timeline is None:
            result.refusal = Refusal(
                reason=(
                    "Ningún hecho de la cronología pudo verificarse contra las "
                    "fuentes recuperadas."
                ),
                category="evidencia_insuficiente",
            )
        else:
            result.timeline = timeline

        result.elapsed_seconds = time.perf_counter() - started
        return result

    def answer_streaming(self, question: str, case_id: str | None = None):
        """Igual que `answer`, pero emitiendo el avance para la interfaz.

        Va produciendo tuplas `(etapa, dato)`:

            ("retrieval", RetrievalDebug)  las fuentes, en cuanto se recuperan
            ("token", str)                 fragmentos del texto según se generan
            ("final", Answer)              la respuesta ya validada

        La interfaz puede mostrar las citas en el primer segundo y dejar que el
        texto llegue después, en vez de tener al usuario mirando una pantalla
        vacía mientras el modelo trabaja.
        """
        started = time.perf_counter()

        result = Answer(
            question=question,
            case_id=case_id,
            model=_settings.ollama_model,
            prompt_version=self.prompt.identifier,
        )

        chunks, retrieval_secs = self.retrieve(question, case_id)
        result.retrieval = self._debug(question, case_id, chunks, retrieval_secs)
        yield "retrieval", result.retrieval

        if not chunks:
            result.refusal = Refusal(
                reason=(
                    "No encontré evidencia en el corpus para esa pregunta. "
                    "Puede estar fuera de los ocho casos disponibles."
                ),
                category="fuera_de_alcance",
                suggestion="Prueba con una pregunta sobre el expediente seleccionado.",
            )
            result.elapsed_seconds = time.perf_counter() - started
            yield "final", result
            return

        messages, _ = prompting.build_messages(question, chunks, self.prompt)

        partes: list[str] = []
        for pieza in generation.generate_stream(messages):
            partes.append(pieza)
            yield "token", pieza

        result.attempts = 1
        result.raw_text = "".join(partes)

        payload = generation.extract_json(result.raw_text)

        if payload is None:
            result.validation = ValidationReport(
                errors=["La respuesta no contenía un objeto JSON válido."]
            )
        else:
            report = validate(payload, chunks)
            result.validation = report
            if report.ok:
                result.grounded = report.answer
                result.sources = report.answer.sources

        if result.grounded is None:
            # En streaming no se reintenta: reescribir la pantalla a media
            # respuesta confunde más de lo que ayuda. Se explica la negativa.
            result.refusal = Refusal(
                reason=(
                    "La respuesta del modelo no pudo validarse contra las fuentes "
                    "recuperadas, así que no la doy por buena."
                ),
                category="evidencia_insuficiente",
                suggestion=(
                    "Vuelve a preguntar; el modelo local a veces rompe el formato."
                ),
            )

        result.elapsed_seconds = time.perf_counter() - started
        yield "final", result
