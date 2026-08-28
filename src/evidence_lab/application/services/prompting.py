"""Carga de prompts versionados y armado del contexto.

Los prompts viven en `prompts/` como archivos con front-matter, uno por versión.
Que estén en disco y versionados, y no incrustados en el código, permite dos
cosas que la rúbrica pide: comparar versiones en la evaluación, y registrar en
cada respuesta con qué versión de prompt se generó.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from evidence_lab.config.settings import get_settings
from evidence_lab.rag.corpus import normalize
from evidence_lab.rag.retriever import RetrievedChunk

_settings = get_settings()

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Presupuesto de contexto. A 9 tok/s cada token del prompt también cuesta, así
# que se recorta el fragmento en vez de mandar la página entera.
MAX_CHARS_PER_CHUNK = 1200


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    body: str

    @property
    def identifier(self) -> str:
        return f"{self.name}.v{self.version}"


def prompts_dir() -> Path:
    return _settings.base_dir / "prompts"


@lru_cache(maxsize=8)
def load_prompt(name: str = "system", version: int | None = None) -> Prompt:
    """Carga `prompts/<name>.v<N>.md`. Sin versión, toma la más alta."""
    directory = prompts_dir()

    if version is None:
        candidates = sorted(directory.glob(f"{name}.v*.md"))
        if not candidates:
            raise FileNotFoundError(f"No hay prompts '{name}' en {directory}")
        path = candidates[-1]
        version = int(re.search(r"\.v(\d+)\.md$", path.name).group(1))
    else:
        path = directory / f"{name}.v{version}.md"
        if not path.exists():
            raise FileNotFoundError(f"No existe {path}")

    raw = path.read_text(encoding="utf-8")
    body = _FRONT_MATTER.sub("", raw).strip()

    return Prompt(name=name, version=version, body=body)


def format_evidence(chunks: list[RetrievedChunk]) -> str:
    """Arma el bloque de evidencia con las etiquetas que el prompt enseña a citar.

    El formato de la etiqueta es idéntico al que el system prompt describe, para
    que el modelo pueda copiarla literalmente. Cualquier cita que invente fuera
    de estas etiquetas la detecta después la validación de fuentes.
    """
    if not chunks:
        return "(sin evidencia recuperada)"

    bloques = []
    for item in chunks:
        chunk = item.chunk
        texto = normalize(chunk.text)[:MAX_CHARS_PER_CHUNK]
        bloques.append(f"[{chunk.document_id} p.{chunk.page_number}] {texto}")

    return "\n\n".join(bloques)


def build_messages(
    question: str,
    chunks: list[RetrievedChunk],
    prompt: Prompt | None = None,
) -> tuple[list[dict], Prompt]:
    """Construye los mensajes para el modelo y devuelve el prompt usado."""
    prompt = prompt or load_prompt()

    user = (
        f"EVIDENCIA DISPONIBLE:\n\n{format_evidence(chunks)}\n\n"
        f"PREGUNTA: {question}\n\n"
        "Responde con el objeto JSON descrito, citando únicamente las etiquetas "
        "de documento y página que aparecen arriba."
    )

    return (
        [
            {"role": "system", "content": prompt.body},
            {"role": "user", "content": user},
        ],
        prompt,
    )


def build_timeline_messages(
    case_id: str,
    chunks: list[RetrievedChunk],
    prompt: Prompt | None = None,
) -> tuple[list[dict], Prompt]:
    """Mensajes para la reconstrucción cronológica de un expediente."""
    prompt = prompt or load_prompt("timeline")

    user = (
        f"EVIDENCIA DISPONIBLE DEL EXPEDIENTE {case_id}:\n\n"
        f"{format_evidence(chunks)}\n\n"
        "Reconstruye la cronología de este caso con el objeto JSON descrito, "
        "citando únicamente las etiquetas de documento y página de arriba."
    )

    return (
        [
            {"role": "system", "content": prompt.body},
            {"role": "user", "content": user},
        ],
        prompt,
    )
