"""Construcción y carga del índice híbrido: BM25 léxico + denso.

BM25 carga los términos jurídicos exactos ("amparo directo", "in dubio pro
reo", números de expediente); el índice denso carga las consultas vagas. Se
construyen una sola vez con `python ingest.py` y se guardan en artifacts/.
"""

import json
import pickle
import re
import unicodedata
from pathlib import Path

import numpy as np

from evidence_lab.config.settings import EMBEDDING_PASSAGE_PREFIX, get_settings
from evidence_lab.rag.corpus import Chunk, load_chunks, normalize
from evidence_lab.rag.devices import pick_device

_settings = get_settings()

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Palabras vacías del español más el ruido procesal que aparece en casi todas
# las páginas y por lo tanto no discrimina entre chunks.
_STOPWORDS = {
    "a", "al", "ante", "con", "contra", "de", "del", "desde", "e", "el", "en",
    "entre", "es", "esa", "ese", "esta", "este", "fue", "ha", "han", "hasta",
    "la", "las", "lo", "los", "mas", "me", "mi", "no", "o", "para", "pero",
    "por", "que", "se", "si", "sin", "sobre", "su", "sus", "también", "tras",
    "un", "una", "uno", "y", "ya",
}


def tokenize(text: str) -> list[str]:
    """Tokeniza sin acentos y en minúsculas.

    Los PDFs mezclan "resolución" y "resolucion" según cómo se extrajo el
    texto; plegar los acentos evita que BM25 los trate como términos distintos.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [t for t in _TOKEN_RE.findall(folded) if t not in _STOPWORDS]


class HybridIndex:
    """Índice sobre los chunks: matriz densa normalizada + BM25 + metadata."""

    def __init__(
        self,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        bm25,
        embedding_model_name: str,
    ):
        self.chunks = chunks
        self.embeddings = embeddings
        self.bm25 = bm25
        self.embedding_model_name = embedding_model_name
        self.position_by_case: dict[str, list[int]] = {}
        for position, chunk in enumerate(chunks):
            self.position_by_case.setdefault(chunk.case_id, []).append(position)

    def __len__(self) -> int:
        return len(self.chunks)

    # --- persistencia ------------------------------------------------------
    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / "embeddings.npy", self.embeddings)

        with (index_dir / "bm25.pkl").open("wb") as f:
            pickle.dump(self.bm25, f)

        with (index_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
            for chunk in self.chunks:
                f.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")

        meta = {
            "embedding_model": self.embedding_model_name,
            "chunks": len(self.chunks),
            "dimensions": int(self.embeddings.shape[1]),
            "cases": sorted(self.position_by_case),
        }
        with (index_dir / "meta.json").open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, index_dir: Path | None = None) -> "HybridIndex":
        index_dir = index_dir or _settings.index_dir
        if not (index_dir / "meta.json").exists():
            raise FileNotFoundError(
                f"No hay índice en {index_dir}. Corre primero: python ingest.py"
            )

        with (index_dir / "meta.json").open("r", encoding="utf-8") as f:
            meta = json.load(f)

        embeddings = np.load(index_dir / "embeddings.npy")

        with (index_dir / "bm25.pkl").open("rb") as f:
            bm25 = pickle.load(f)

        chunks = []
        with (index_dir / "chunks.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    chunks.append(Chunk(**json.loads(line)))

        return cls(chunks, embeddings, bm25, meta["embedding_model"])


def _encode_passages(texts: list[str], model_name: str, batch_size: int):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device=pick_device())
    prefixed = [EMBEDDING_PASSAGE_PREFIX + t for t in texts]
    return model.encode(
        prefixed,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)


def build_index(
    chunks: list[Chunk] | None = None,
    embedding_model_name: str | None = None,
    batch_size: int = 16,
) -> HybridIndex:
    from rank_bm25 import BM25Okapi

    chunks = chunks if chunks is not None else load_chunks()
    embedding_model_name = embedding_model_name or _settings.embedding_model

    texts = [normalize(chunk.text) for chunk in chunks]

    bm25 = BM25Okapi([tokenize(text) for text in texts])
    embeddings = _encode_passages(texts, embedding_model_name, batch_size)

    return HybridIndex(chunks, embeddings, bm25, embedding_model_name)
