"""La segunda barrera: que las citas correspondan a evidencia realmente recuperada.

El caso que motivó estas pruebas está documentado en `docs/pruebas.md`: ante la
pregunta "¿quién es el culpable?", el modelo devolvió un JSON estructuralmente
impecable citando las páginas 100 y 101 de un documento de 64 páginas.
"""

from evidence_lab.guardrails.validation import (
    allowed_sources,
    validate,
    validate_structure,
)
from evidence_lab.rag.corpus import Chunk
from evidence_lab.rag.retriever import RetrievedChunk

DOC = "CASE-MX-001-DOC-001"


def chunk(page: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=f"{DOC}-CH-{page:03d}-01",
            case_id="CASE-MX-001",
            document_id=DOC,
            page_number=page,
            chunk_index=0,
            text=f"Texto de la página {page}.",
            source_url="https://ejemplo.gob.mx/doc.pdf",
        ),
        score=1.0,
        rank=0,
    )


CONTEXTO = [chunk(1), chunk(9), chunk(64)]


def respuesta(statement, paginas, modality="testimony"):
    return {
        "answer": "Resumen de lo encontrado en el expediente.",
        "claims": [
            {
                "statement": statement,
                "modality": modality,
                "verdict": "supported",
                "citations": [
                    {"document_id": DOC, "page_number": p} for p in paginas
                ],
            }
        ],
        "limitations": [],
    }


class TestFuentesPermitidas:
    def test_solo_lo_recuperado_es_citable(self):
        assert allowed_sources(CONTEXTO) == {(DOC, 1), (DOC, 9), (DOC, 64)}


class TestCitasInventadas:
    def test_una_cita_inexistente_tumba_su_afirmacion(self):
        reporte = validate(
            respuesta("El testigo declaró que vio al acusado.", [100]), CONTEXTO
        )
        assert reporte.structural_ok
        assert not reporte.sources_ok
        assert "CASE-MX-001-DOC-001 p.100" in reporte.dropped_citations

    def test_las_citas_buenas_sobreviven_a_las_malas(self):
        reporte = validate(
            respuesta("El testigo declaró que vio al acusado.", [9, 100]), CONTEXTO
        )
        assert reporte.ok
        assert [c.page_number for c in reporte.answer.claims[0].citations] == [9]
        assert "CASE-MX-001-DOC-001 p.100" in reporte.dropped_citations

    def test_no_se_corrige_a_la_pagina_mas_parecida(self):
        """Sustituir una cita falsa por una real fabricaría la trazabilidad."""
        reporte = validate(
            respuesta("El testigo declaró que vio al acusado.", [10]), CONTEXTO
        )
        assert not reporte.ok
        assert reporte.answer is not None
        assert reporte.answer.claims == []

    def test_la_cita_valida_se_enriquece_con_su_url(self):
        reporte = validate(
            respuesta("El testigo declaró que vio al acusado.", [9]), CONTEXTO
        )
        cita = reporte.answer.claims[0].citations[0]
        assert cita.source_url == "https://ejemplo.gob.mx/doc.pdf"
        assert cita.chunk_id is not None


class TestRescateDeAfirmaciones:
    def test_un_claim_invalido_no_tumba_la_respuesta_entera(self):
        payload = {
            "answer": "Hallazgos del expediente.",
            "claims": [
                {
                    "statement": "El acusado estuvo en el lugar.",  # sin atribuir
                    "modality": "testimony",
                    "verdict": "supported",
                    "citations": [{"document_id": DOC, "page_number": 9}],
                },
                {
                    "statement": "El testigo declaró que se retractó.",
                    "modality": "testimony",
                    "verdict": "supported",
                    "citations": [{"document_id": DOC, "page_number": 9}],
                },
            ],
            "limitations": [],
        }
        answer, descartes = validate_structure(payload)

        assert answer is not None
        assert len(answer.claims) == 1
        assert "se retractó" in answer.claims[0].statement
        assert len(descartes) == 1

    def test_si_falla_la_respuesta_misma_no_hay_rescate(self):
        payload = {
            "answer": "El acusado es el culpable.",
            "claims": [
                {
                    "statement": "El testigo declaró que lo vio.",
                    "modality": "testimony",
                    "verdict": "supported",
                    "citations": [{"document_id": DOC, "page_number": 9}],
                }
            ],
        }
        answer, errores = validate_structure(payload)
        assert answer is None
        assert errores
