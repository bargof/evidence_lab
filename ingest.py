"""Construye el índice del RAG una sola vez.

    python ingest.py

Descarga los modelos de embeddings (la única vez que la app toca la red) y deja
en artifacts/rag_index/ todo lo que la app necesita para correr sin conexión.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from evidence_lab.config.settings import get_settings  # noqa: E402
from evidence_lab.rag.corpus import load_chunks  # noqa: E402
from evidence_lab.rag.index import build_index  # noqa: E402


def main() -> int:
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Indexa el corpus de EvidenceLab.")
    parser.add_argument("--index-dir", type=Path, default=settings.index_dir)
    parser.add_argument("--embedding-model", default=settings.embedding_model)
    parser.add_argument("--batch-size", type=int, default=settings.embedding_batch_size)
    args = parser.parse_args()

    if not settings.chunks_path.exists():
        print(f"No encontré el corpus en {settings.chunks_path}", file=sys.stderr)
        return 1

    chunks = load_chunks()
    cases = sorted({chunk.case_id for chunk in chunks})
    print(f"Corpus: {len(chunks)} chunks de {len(cases)} casos")
    print(f"Embeddings: {args.embedding_model} (CPU)")

    started = time.perf_counter()
    index = build_index(
        chunks=chunks,
        embedding_model_name=args.embedding_model,
        batch_size=args.batch_size,
    )
    elapsed = time.perf_counter() - started

    index.save(args.index_dir)

    print(f"Indexado en {elapsed:.1f} s")
    print(f"Dimensiones: {index.embeddings.shape}")
    print(f"Guardado en: {args.index_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
