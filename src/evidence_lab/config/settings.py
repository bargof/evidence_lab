from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

# ═══════════════════════════════════════════
# Configuración con validación
# ═══════════════════════════════════════════


class Settings(BaseSettings):
    """Configuración centralizada de EvidenceLab.

    La app corre en modo offline: Ollama para generación y modelos locales de
    sentence-transformers para embeddings y re-ranking. Nada sale de la
    máquina. Todo es ajustable por .env para poder afinar la demo según el
    equipo donde corra sin tocar código.
    """

    # --- Entorno ---
    environment: str = Field(
        default="development",
        description="Entorno de ejecución: development, staging, production",
    )
    debug: bool = Field(default=False, description="Modo debug activo")
    base_dir: Path = Path(__file__).resolve().parents[3]

    # --- Datos ---
    data_dir: Path = Path("data/evidencelab")
    derived_data_dir: Path = Path("data/derived")
    artifacts_dir: Path = Path("artifacts")

    # --- Modelo generativo (Ollama, local) ---
    ollama_model: str = Field(
        default="llama3.2:3b",
        description="Modelo servido por Ollama. Bajar a 1b si la RAM aprieta.",
    )
    ollama_host: str = Field(default="http://127.0.0.1:11434")
    ollama_timeout: float = Field(default=180.0, gt=0)
    generation_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    generation_num_predict: int = Field(default=700, gt=0)

    # --- Embeddings ---
    # e5-base (278M) sobre bge-m3 (568M): el índice y las consultas corren en
    # CPU, y e5-base da el mejor balance calidad/latencia medido en este corpus.
    embedding_model: str = Field(default="intfloat/multilingual-e5-base")
    embedding_batch_size: int = Field(default=16, gt=0)

    # --- Re-ranking ---
    reranker_model: str = Field(
        default="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    )
    use_reranker: bool = Field(
        default=True,
        description="Apagable para demos en equipos con poca RAM.",
    )

    # --- Retrieval ---
    chunk_candidates: int = Field(default=20, gt=0)
    final_context_chunks: int = Field(default=6, gt=0)
    rrf_k: int = Field(
        default=60, gt=0, description="Constante de Reciprocal Rank Fusion"
    )

    # --- Reproducibilidad ---
    random_seed: int = Field(default=42)

    # --- Logging ---
    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
        "env_prefix": "EVIDENCELAB_",
    }

    # --- Rutas derivadas ---
    @property
    def global_dir(self) -> Path:
        return self.base_dir / self.data_dir / "global"

    @property
    def evaluation_dir(self) -> Path:
        return self.base_dir / self.data_dir / "evaluation"

    @property
    def chunks_path(self) -> Path:
        return self.global_dir / "chunks.jsonl"

    @property
    def documents_path(self) -> Path:
        return self.global_dir / "documents.jsonl"

    @property
    def cases_path(self) -> Path:
        return self.global_dir / "cases.jsonl"

    @property
    def index_dir(self) -> Path:
        return self.base_dir / self.artifacts_dir / "rag_index"

    @property
    def reports_dir(self) -> Path:
        return self.base_dir / "reports"

    @property
    def log_file_path(self) -> Path:
        return self.base_dir / self.log_file


# e5 exige estos prefijos; sin ellos la calidad de recuperación cae de forma
# notable. No son configurables porque dependen del modelo, no del entorno.
EMBEDDING_QUERY_PREFIX = "query: "
EMBEDDING_PASSAGE_PREFIX = "passage: "


# ═══════════════════════════════════════════
# Patrón singleton: una sola instancia de configuración
# ═══════════════════════════════════════════
def get_settings() -> Settings:
    """Retorna la configuración de la aplicación."""
    return Settings()
