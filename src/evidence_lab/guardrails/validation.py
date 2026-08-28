"""Doble validación de la respuesta del modelo.

Dos barreras independientes, porque protegen contra fallas distintas:

1. **Estructural** — Pydantic. ¿La respuesta cumple el contrato, tiene las
   claves correctas, cada afirmación lleva cita, y respeta las reglas
   epistemológicas del dominio?
2. **Factual de fuentes** — ¿Cada cita corresponde a un fragmento que de verdad
   se recuperó? Un JSON perfectamente formado que cita la página 49 cuando el
   contexto solo traía la 7 es una alucinación con buena presentación, y la
   primera barrera no la ve.
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from evidence_lab.data.schemas import (
    CaseTimeline,
    Citation,
    Claim,
    GroundedAnswer,
    TimelineEvent,
)
from evidence_lab.rag.retriever import RetrievedChunk


@dataclass
class ValidationReport:
    """Resultado de las dos barreras, con detalle de qué falló y por qué."""

    answer: GroundedAnswer | None = None
    structural_ok: bool = False
    sources_ok: bool = False
    errors: list[str] = field(default_factory=list)
    dropped_citations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.structural_ok and self.sources_ok and self.answer is not None


def allowed_sources(chunks: list[RetrievedChunk]) -> set[tuple[str, int]]:
    """Las únicas fuentes citables: las que el retriever puso en el contexto."""
    return {(c.chunk.document_id, c.chunk.page_number) for c in chunks}


def _mensajes(error: ValidationError) -> list[str]:
    mensajes = []
    for detalle in error.errors():
        ubicacion = ".".join(str(p) for p in detalle["loc"]) or "(raíz)"
        mensajes.append(f"{ubicacion}: {detalle['msg']}")
    return mensajes


def validate_structure(payload: dict) -> tuple[GroundedAnswer | None, list[str]]:
    """Valida el contrato, rescatando las afirmaciones que sí cumplen.

    Un solo `claim` mal redactado no debe tumbar una respuesta por lo demás
    correcta. Si la validación completa falla, se revisa afirmación por
    afirmación, se descartan las inválidas y se conserva el resto. Solo se
    rechaza todo cuando el fallo está en la respuesta misma —por ejemplo, si
    atribuye culpabilidad— o cuando no sobrevive ninguna afirmación.
    """
    try:
        return GroundedAnswer.model_validate(payload), []
    except ValidationError as error:
        errores = _mensajes(error)

    if not isinstance(payload.get("claims"), list):
        return None, errores

    sobrevivientes = []
    descartes = []

    for indice, bruto in enumerate(payload["claims"]):
        try:
            sobrevivientes.append(Claim.model_validate(bruto))
        except ValidationError as claim_error:
            motivo = "; ".join(d["msg"] for d in claim_error.errors())
            texto = (
                str(bruto.get("statement", ""))[:60]
                if isinstance(bruto, dict)
                else ""
            )
            descartes.append(f"claim {indice} descartado ({motivo}): {texto}")

    if not sobrevivientes:
        return None, errores

    rescatado = dict(payload)
    rescatado["claims"] = [c.model_dump() for c in sobrevivientes]
    rescatado.pop("sources", None)

    try:
        return GroundedAnswer.model_validate(rescatado), descartes
    except ValidationError as error:
        # El fallo no estaba en los claims sino en la respuesta misma.
        return None, _mensajes(error)


def validate_sources(
    answer: GroundedAnswer, chunks: list[RetrievedChunk]
) -> tuple[GroundedAnswer, list[str]]:
    """Elimina las citas inventadas y las afirmaciones que se quedan sin apoyo.

    No se "corrige" la cita a la más parecida: si el modelo citó algo que no
    estaba, esa afirmación no tiene respaldo y se cae. Corregirla sería
    fabricar la trazabilidad que el sistema promete.
    """
    permitidas = allowed_sources(chunks)
    por_pagina = {
        (c.chunk.document_id, c.chunk.page_number): c.chunk for c in chunks
    }

    descartadas: list[str] = []
    claims_validos = []

    for claim in answer.claims:
        citas_buenas: list[Citation] = []

        for cita in claim.citations:
            clave = (cita.document_id, cita.page_number)
            if clave in permitidas:
                chunk = por_pagina[clave]
                citas_buenas.append(
                    Citation(
                        document_id=cita.document_id,
                        page_number=cita.page_number,
                        chunk_id=chunk.chunk_id,
                        source_url=chunk.source_url,
                    )
                )
            else:
                descartadas.append(cita.label())

        if citas_buenas:
            claim.citations = citas_buenas
            claims_validos.append(claim)
        else:
            descartadas.append(f"(afirmación sin fuente válida) {claim.statement[:60]}")

    answer.claims = claims_validos
    answer.sources = []
    answer = GroundedAnswer.model_validate(answer.model_dump())

    return answer, descartadas


def validate_timeline(
    payload: dict, chunks: list[RetrievedChunk], case_id: str
) -> tuple[CaseTimeline | None, list[str], list[str]]:
    """Valida una cronología: estructura, y que cada hecho cite fuentes reales.

    Devuelve `(cronología, errores, descartes)`. Igual que con las respuestas,
    un hecho mal citado se elimina en vez de tumbar toda la reconstrucción; solo
    se rechaza del todo si no sobrevive ningún hecho.
    """
    datos = dict(payload)
    datos["case_id"] = case_id
    datos.pop("sources", None)

    permitidas = allowed_sources(chunks)
    por_pagina = {(c.chunk.document_id, c.chunk.page_number): c.chunk for c in chunks}

    descartes: list[str] = []
    eventos_validos = []

    for indice, bruto in enumerate(datos.get("events") or []):
        try:
            evento = TimelineEvent.model_validate(bruto)
        except ValidationError as error:
            motivo = "; ".join(d["msg"] for d in error.errors())
            descartes.append(f"evento {indice} descartado ({motivo})")
            continue

        citas_buenas = []
        for cita in evento.citations:
            clave = (cita.document_id, cita.page_number)
            if clave in permitidas:
                chunk = por_pagina[clave]
                citas_buenas.append(
                    Citation(
                        document_id=cita.document_id,
                        page_number=cita.page_number,
                        chunk_id=chunk.chunk_id,
                        source_url=chunk.source_url,
                    )
                )
            else:
                descartes.append(f"cita inexistente: {cita.label()}")

        if citas_buenas:
            evento.citations = citas_buenas
            eventos_validos.append(evento)
        else:
            descartes.append(
                f"(hecho sin fuente válida) {evento.description[:60]}"
            )

    if not eventos_validos:
        return None, ["Ningún hecho de la cronología pudo validarse."], descartes

    datos["events"] = [e.model_dump() for e in eventos_validos]

    try:
        return CaseTimeline.model_validate(datos), [], descartes
    except ValidationError as error:
        return None, _mensajes(error), descartes


def validate(payload: dict, chunks: list[RetrievedChunk]) -> ValidationReport:
    """Corre las dos barreras en orden y reporta el resultado."""
    report = ValidationReport()

    answer, descartes_estructurales = validate_structure(payload)
    if answer is None:
        report.errors = descartes_estructurales
        return report

    report.structural_ok = True
    report.dropped_citations.extend(descartes_estructurales)

    answer, descartadas = validate_sources(answer, chunks)
    report.answer = answer
    report.dropped_citations.extend(descartadas)

    # Se toleran citas inventadas sueltas mientras quede algo sostenido; lo que
    # no se tolera es una respuesta que afirma cosas y no sostiene ninguna.
    if descartadas and not answer.claims:
        report.errors.append(
            "Todas las afirmaciones citaban fuentes que no estaban en el contexto."
        )
        return report

    report.sources_ok = True
    return report
