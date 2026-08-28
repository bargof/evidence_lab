"""Los guardrails codificados en el contrato de salida.

Estas pruebas son la red de seguridad de las reglas del dominio. Dos de ellas
existen porque las reglas fallaron en producción: el filtro de culpabilidad
dejaba pasar "es **el** culpable" por buscar texto literal, y el de atribución
rechazaba reconstrucciones válidas por no conocer el verbo "mencionó".
"""

import pytest
from pydantic import ValidationError

from evidence_lab.data.schemas import (
    CaseTimeline,
    Citation,
    Claim,
    GroundedAnswer,
    Modality,
    TimelineEvent,
    Verdict,
)

CITA = [Citation(document_id="CASE-MX-001-DOC-001", page_number=9)]


def claim(statement: str, modality: Modality = Modality.TESTIMONY) -> Claim:
    return Claim(
        statement=statement,
        modality=modality,
        verdict=Verdict.SUPPORTED,
        citations=CITA,
    )


class TestCitasObligatorias:
    def test_un_claim_sin_cita_no_se_puede_construir(self):
        with pytest.raises(ValidationError):
            Claim(
                statement="La víctima salió de su casa.",
                modality=Modality.DOCUMENTED_FACT,
                verdict=Verdict.SUPPORTED,
                citations=[],
            )

    def test_la_pagina_debe_ser_positiva(self):
        with pytest.raises(ValidationError):
            Citation(document_id="D", page_number=0)


class TestAtribucionDeCulpabilidad:
    """RULE-OPPORTUNITY-NOT-GUILT."""

    @pytest.mark.parametrize(
        "texto",
        [
            "El señor Q es el culpable del delito de homicidio calificado.",
            "Q es culpable.",
            "Q fue el responsable del homicidio.",
            "Q cometió el delito.",
            "Sin duda fue Q.",
        ],
    )
    def test_rechaza_culpabilidad_sin_atribuir(self, texto):
        with pytest.raises(ValidationError):
            GroundedAnswer(answer=texto)

    @pytest.mark.parametrize(
        "texto",
        [
            "El tribunal determinó que Q era penalmente responsable.",
            "La Primera Sala confirmó la condena impuesta a Q.",
        ],
    )
    def test_admite_el_resultado_oficial_atribuido(self, texto):
        assert GroundedAnswer(answer=texto).answer == texto

    def test_el_articulo_intermedio_no_burla_el_filtro(self):
        """Regresión: 'es culpable' como texto literal no veía 'es el culpable'."""
        with pytest.raises(ValidationError):
            claim("El acusado es el culpable.", Modality.JUDICIAL_FINDING)


class TestAtribucionDeTestimonio:
    """RULE-TESTIMONY-NOT-FACT."""

    @pytest.mark.parametrize(
        "texto",
        [
            "El testigo no mencionó que el recurrente intervino.",
            "El testigo señaló que vio al acusado.",
            "Luego retractó su declaración.",
            "A los denunciantes no les constaban los hechos.",
            "El recurrente alega que fue coaccionado.",
            "Según el perito, la muerte ocurrió de madrugada.",
        ],
    )
    def test_admite_afirmaciones_atribuidas(self, texto):
        """Regresión: una lista corta de verbos rechazaba reconstrucciones buenas."""
        assert claim(texto).statement == texto

    @pytest.mark.parametrize(
        "texto",
        [
            "El acusado estuvo en el lugar de los hechos.",
            "La víctima murió a las tres de la mañana.",
            "Había sangre en la habitación.",
        ],
    )
    def test_rechaza_testimonio_redactado_como_hecho(self, texto):
        with pytest.raises(ValidationError):
            claim(texto)

    def test_el_sustantivo_acusado_no_cuenta_como_atribucion(self):
        """Regresión: el patrón 'acus' también casaba con 'acusado'."""
        with pytest.raises(ValidationError):
            claim("El acusado tenía un objeto metálico.")


class TestDerivacionDeFuentes:
    def test_las_fuentes_salen_de_las_citas_de_los_claims(self):
        respuesta = GroundedAnswer(
            answer="El testigo declaró haberse retractado.",
            claims=[claim("El testigo declaró que se retractó.")],
        )
        assert [s.label() for s in respuesta.sources] == [
            "CASE-MX-001-DOC-001 p.9"
        ]

    def test_sin_claims_no_hay_fuentes_derivadas(self):
        assert GroundedAnswer(answer="No hay evidencia suficiente.").sources == []


class TestCronologia:
    def test_los_eventos_quedan_ordenados(self):
        linea = CaseTimeline(
            case_id="CASE-MX-006",
            events=[
                TimelineEvent(
                    order=3,
                    time_expression="2017",
                    description="Tercero",
                    modality=Modality.PROCEDURAL_FACT,
                    citations=CITA,
                ),
                TimelineEvent(
                    order=1,
                    time_expression="2015",
                    description="Primero",
                    modality=Modality.PROCEDURAL_FACT,
                    citations=CITA,
                ),
            ],
        )
        assert [e.order for e in linea.events] == [1, 3]
        assert linea.events[0].description == "Primero"

    def test_un_evento_sin_cita_no_se_puede_construir(self):
        with pytest.raises(ValidationError):
            TimelineEvent(
                order=1,
                time_expression="2015",
                description="Algo pasó",
                modality=Modality.PROCEDURAL_FACT,
                citations=[],
            )
