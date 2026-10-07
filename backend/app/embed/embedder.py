"""The single in-process embedding model (api container). The worker reaches it via /internal/embed."""
import logging
import threading

from app.config import get_settings

log = logging.getLogger("infopoint.embed")


class EmbeddingModelMissing(RuntimeError):
    pass


class Embedder:
    def __init__(self):
        self._model = None
        self._load_lock = threading.Lock()
        self._run_lock = threading.Lock()  # one encode at a time: torch already uses all assigned threads

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        with self._load_lock:
            if self._model is not None:
                return
            s = get_settings()
            path = s.embedding_model_path
            if not (path / "config.json").exists():
                raise EmbeddingModelMissing(
                    f"Embedding model {s.embedding_model} is not installed at {path}. "
                    "Run scripts/download_models.ps1 (needs internet once).")
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(s.embed_threads)
            log.info("loading embedding model %s from %s", s.embedding_model, path)
            model = SentenceTransformer(str(path), device="cpu")
            model.max_seq_length = 1024
            self._model = model

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        with self._run_lock:
            vecs = self._model.encode(texts, batch_size=get_settings().embed_batch_size,
                                      normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return vecs.tolist()


embedder = Embedder()
