from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.rootmap import Root, parse_roots


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_mode: str = "desktop"
    app_secret_key: str = "change-me"
    internal_token: str = "change-me"

    database_url: str = "postgresql+psycopg://infopoint:infopoint@localhost:5434/infopoint"
    data_dir: Path = Path("/data")
    models_dir: Path = Path("/models")
    static_dir: Path = Path(__file__).resolve().parent.parent / "static"

    allowed_doc_roots: str = ""
    roots_mount_base: str = "/roots"
    default_doc_folder: str = ""

    initial_admin_user: str = "admin"
    initial_admin_password: str = "change-me"
    auto_login: bool = False

    qwen_base_url: str = "http://host.docker.internal:1234/v1"
    qwen_model_name: str = "qwen2.5-1.5b-instruct"
    qwen_api_key: str = ""
    qwen_context_length: int = 32768
    llm_max_concurrency: int = 2
    llm_timeout_seconds: float = 180.0

    embedding_model: str = "multilingual-e5-small"   # see app/embed/models.py
    embed_threads: int = 8

    ocr_langs: str = "eng"
    folder_poll_seconds: int = 20
    rescan_interval_seconds: int = 300
    stability_seconds: int = 10
    doc_lane_slots: int = 2

    chunk_tokens: int = 450          # capped below the embedding model's input limit
    chunk_overlap_tokens: int = 50

    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    min_vector_similarity: float | None = None   # None = the embedding model's calibrated default
    answer_reserve_tokens: int = 1024

    @property
    def roots(self) -> list[Root]:
        return parse_roots(self.allowed_doc_roots)

    @property
    def embedding_model_path(self) -> Path:
        return self.models_dir / self.embedding_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
