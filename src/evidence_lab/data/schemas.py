"""Contratos de entrada y salida validados con Pydantic.

Estos schemas son el guardrail estructural del sistema. No basta con pedirle al
modelo en el prompt que no invente: la respuesta se valida contra estas clases y
se rechaza si cita una fuente que no estaba en el contexto o si afirma como
probado algo que la evidencia solo sostiene como declarado.

Las reglas epistemológicas vienen de la ontología del corpus
(data/evidencelab/ontology/logical_rules.yaml):

    RULE-TESTIMONY-NOT-FACT     alguien declara X  =>  X es afirmado, no probado
    RULE-OPPORTUNITY-NOT-GUILT  presencia + hecho  =>  no implica culpabilidad
    RULE-ABSENCE-NOT-NEGATION   sin evidencia de X =>  no implica que no X
"""

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

# Afirmaciones de culpabilidad. Se usan expresiones regulares y no búsqueda de
# texto literal porque el español intercala artículos y adverbios: un filtro que
# busca "es culpable" no ve "es el culpable", y esa grieta ya dejó pasar una
# respuesta durante las pruebas (ver docs/pruebas.md).
_ATRIBUYE_CULPA = re.compile(
    r"\b(?:es|fue|era|son|fueron)\s+(?:el\s+|la\s+|los\s+|las\s+)?"
    r"(?:único\s+|principal\s+|verdadero\s+)?"
    r"(?:culpable|responsable|autor|autora|homicida|asesin[oa])\b"
    r"|\bcometi(?:ó|eron)\s+(?:el\s+|los\s+)?(?:delito|homicidio|crimen|feminicidio)\b"
    r"|\bsin\s+duda\s+(?:fue|es)\b",
    re.IGNORECASE,
)

# Una afirmación de modalidad testimonial está bien redactada si queda claro
# QUIÉN lo dice. Se acepta por dos vías, y basta con una: un verbo de decir, o
# la mención explícita de un actor procesal como sujeto.
#
# La lista es deliberadamente amplia. Un rechazo falso destruye una respuesta
# correcta y completa, que es un daño mayor que dejar pasar una frase
# imperfecta: la modalidad ya viaja en el campo `modality` y se muestra en la
# interfaz. Una versión anterior, con solo ocho verbos, rechazaba
# reconstrucciones válidas por usar "mencionó" o "señaló" (ver docs/pruebas.md).
_ATRIBUYE_A_UNA_FUENTE = re.compile(
    r"\b(?:declar|afirm|manifest|sostien|sosten|aleg|refier|refiri|menciona|"
    # Los verbos se anclan a sus formas conjugadas: "acus" a secas también
    # coincidiría con "acusado", que es el sujeto de la frase y no marca
    # atribución alguna. Lo mismo con "imputado".
    r"mencion|señal|senal|indic|express|expres|narr|relat|admiti|admit|neg[óo]|"
    r"niega|reconoci|reconoc|acus[óa]\b|acusaron\b|acusan\b|imput[óa]\b|"
    r"imputaron\b|atribuy|retract|desmint|desmiente|"
    r"testimoni|deposici|dijo|dice|asegur|precis|apunt|consta|constaba|"
    r"declaraci[óo]n)"
    r"|\bseg[úu]n\b",
    re.IGNORECASE,
)
# Nota: no basta con que la frase mencione a un actor procesal. "El acusado
# estuvo en el lugar" nombra a alguien y sigue siendo una afirmación de hecho
# sin atribuir. Lo que marca la atribución es el verbo de decir, no el sujeto.

# Marcas de que la afirmación se atribuye al órgano jurisdiccional en vez de
# afirmarse por cuenta propia.
_ATRIBUCION_JUDICIAL = re.compile(
    r"\b(?:tribunal|sala|juzgado|corte|juez|jueza|magistrad[oa]s?|sentencia|"
    r"resoluci[óo]n|resolvi[óo]|determin[óo]|conden[óo]|sentenci[óo]|"
    r"declar[óo]\s+penalmente|acredit[óo])\b",
    re.IGNORECASE,
)


class Modality(StrEnum):
    """Fuerza epistemológica de una afirmación.

    La distinción es el corazón del dominio: una resolución judicial describe
    testimonios, alegatos y hallazgos oficiales, y tratarlos como equivalentes
    es exactamente el error que el sistema debe evitar.
    """

    DOCUMENTED_FACT = "documented_fact"
    TESTIMONY = "testimony"
    ALLEGATION = "allegation"
    JUDICIAL_FINDING = "judicial_finding"
    PROCEDURAL_FACT = "procedural_fact"
    JUDICIAL_NARRATIVE = "judicial_narrative"


class Verdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Citation(BaseModel):
    """Referencia verificable a una página concreta de un documento del corpus."""

    document_id: str
    page_number: int = Field(ge=1)
    chunk_id: str | None = None
    source_url: str | None = None

    def label(self) -> str:
        return f"{self.document_id} p.{self.page_number}"


class Claim(BaseModel):
    """Una afirmación de la respuesta, con su modalidad y su fuente."""

    statement: str = Field(min_length=1)
    modality: Modality
    verdict: Verdict
    citations: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def guilt_must_be_attributed(self) -> "Claim":
        """RULE-OPPORTUNITY-NOT-GUILT, a nivel de afirmación.

        Una afirmación de culpabilidad solo es admisible si se atribuye al
        órgano que la resolvió. "X es el culpable" se rechaza; "el tribunal
        determinó que X era penalmente responsable" se admite, porque reporta
        una decisión oficial en vez de emitir un juicio propio.
        """
        if _ATRIBUYE_CULPA.search(self.statement) and not _ATRIBUCION_JUDICIAL.search(
            self.statement
        ):
            raise ValueError(
                "La afirmación atribuye culpabilidad sin decir qué órgano la "
                "resolvió. Debe reportarse como decisión judicial y citarse."
            )
        return self

    @model_validator(mode="after")
    def testimony_is_not_proof(self) -> "Claim":
        """RULE-TESTIMONY-NOT-FACT.

        Un testimonio o un alegato pueden estar respaldados como *dichos*, pero
        el sistema no puede reportarlos con el mismo peso que un hallazgo
        oficial. Se fuerza a que el texto lo explicite.
        """
        if self.modality in (Modality.TESTIMONY, Modality.ALLEGATION):
            if not _ATRIBUYE_A_UNA_FUENTE.search(self.statement):
                raise ValueError(
                    f"La afirmación tiene modalidad '{self.modality}' pero está "
                    "redactada como hecho probado. Debe atribuirse a quien lo "
                    "declara o alega."
                )
        return self


class GroundedAnswer(BaseModel):
    """Respuesta del asistente. Todo lo relevante va ligado a una fuente."""

    answer: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[Citation] = Field(default_factory=list)

    @field_validator("answer")
    @classmethod
    def no_guilt_attribution(cls, value: str) -> str:
        """RULE-OPPORTUNITY-NOT-GUILT.

        El sistema nunca declara culpabilidad por su cuenta. Solo puede
        reportar lo que resolvió el órgano jurisdiccional, y para eso existe
        la modalidad judicial_finding con su cita.
        """
        encontrado = _ATRIBUYE_CULPA.search(value)
        if encontrado and not _ATRIBUCION_JUDICIAL.search(value):
            raise ValueError(
                f"La respuesta atribuye culpabilidad ('{encontrado.group(0)}') sin "
                "decir qué órgano lo resolvió. Solo se puede reportar el "
                "resultado oficial, citando la resolución."
            )
        return value

    @model_validator(mode="after")
    def sources_match_claims(self) -> "GroundedAnswer":
        """La lista de fuentes se deriva de las citas, nunca al revés."""
        if self.claims and not self.sources:
            vistas: dict[str, Citation] = {}
            for claim in self.claims:
                for cita in claim.citations:
                    vistas.setdefault(cita.label(), cita)
            self.sources = list(vistas.values())
        return self


class TimelineEvent(BaseModel):
    """Un hecho ubicado en la línea de tiempo del expediente."""

    order: int = Field(ge=1, description="Posición cronológica, empezando en 1")
    time_expression: str = Field(
        min_length=1,
        description="Fecha, o marca relativa como 'antes del incidente'",
    )
    description: str = Field(min_length=1)
    modality: Modality
    citations: list[Citation] = Field(min_length=1)


class CaseTimeline(BaseModel):
    """Reconstrucción cronológica de un caso.

    Es una salida distinta de `GroundedAnswer` porque responde a una pregunta
    distinta: no "qué dice la evidencia sobre X", sino "en qué orden ocurrió
    todo". Ordenar es la capacidad central del dominio y merece su propio
    contrato en vez de salir disfrazada de párrafo.
    """

    case_id: str
    events: list[TimelineEvent] = Field(default_factory=list)
    outcome: str | None = Field(
        default=None,
        description="Resultado oficial, solo si consta en la evidencia",
    )
    limitations: list[str] = Field(default_factory=list)
    sources: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordenar_y_derivar_fuentes(self) -> "CaseTimeline":
        self.events.sort(key=lambda e: e.order)

        if self.events and not self.sources:
            vistas: dict[str, Citation] = {}
            for evento in self.events:
                for cita in evento.citations:
                    vistas.setdefault(cita.label(), cita)
            self.sources = list(vistas.values())
        return self


class Refusal(BaseModel):
    """Respuesta cuando el sistema no debe o no puede contestar.

    Negarse bien es parte del producto: si el contexto no sostiene la
    respuesta, decirlo vale más que improvisar.
    """

    reason: str = Field(min_length=1)
    category: str = Field(
        description=(
            "fuera_de_alcance | evidencia_insuficiente | peticion_indebida | "
            "mezcla_de_casos"
        )
    )
    suggestion: str | None = None


class RetrievalDebug(BaseModel):
    """Lo que recuperó el retriever, para mostrarlo en la UI y auditarlo."""

    query: str
    case_id: str | None = None
    retriever: str
    chunks: list[Citation] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
