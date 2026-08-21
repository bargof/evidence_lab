"""Carga del corpus indexable y su metadata de trazabilidad.

Cada chunk conserva case_id, document_id, page_number y source_url. Esa cadena
es lo que después permite que una afirmación de la app se ligue a una página
concreta de una resolución pública.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from evidence_lab.config.settings import get_settings

_settings = get_settings()


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    case_id: str
    document_id: str
    page_number: int
    chunk_index: int
    text: str
    source_url: str

    @property
    def citation(self) -> str:
        return f"{self.document_id} p.{self.page_number}"


def _load_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_chunks(path: Path | None = None) -> list[Chunk]:
    rows = _load_jsonl(path or _settings.chunks_path)
    return [
        Chunk(
            chunk_id=row["chunk_id"],
            case_id=row["case_id"],
            document_id=row["document_id"],
            page_number=int(row["page_number"]),
            chunk_index=int(row["chunk_index"]),
            text=row["text"],
            source_url=row.get("source_url", ""),
        )
        for row in rows
    ]


def load_documents(path: Path | None = None) -> dict[str, dict]:
    rows = _load_jsonl(path or _settings.documents_path)
    return {row["document_id"]: row for row in rows}


def load_cases(path: Path | None = None) -> dict[str, dict]:
    rows = _load_jsonl(path or _settings.cases_path)
    return {row["case_id"]: row for row in rows}


def normalize(text: str) -> str:
    """Limpieza mínima para BM25 y para el contexto que ve el modelo.

    Los PDFs de la SCJN traen saltos de línea a mitad de frase y espacios
    dobles; unificarlos mejora tanto el matching léxico como la legibilidad
    del contexto sin alterar el contenido.
    """
    return " ".join(text.split())
