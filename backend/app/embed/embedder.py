"""In-process text embeddings with ONNX Runtime. Used by the worker (passages) and the api (queries)."""
import logging
import threading
from functools import lru_cache

import numpy as np

from app.config import get_settings
from app.embed.models import EmbeddingSpec, get_spec

log = logging.getLogger("infopoint.embed")


class EmbeddingModelMissing(RuntimeError):
    pass


class Embedder:
    def __init__(self, spec: EmbeddingSpec, model_dir, threads: int):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = model_dir / spec.onnx_file
        if not onnx_path.exists():
            raise EmbeddingModelMissing(
                f"Embedding model {spec.name} is not installed at {model_dir}. "
                "Run scripts\\download_models.ps1 once while online.")
        self.spec = spec
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(spec.max_tokens)
        self.tokenizer.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(onnx_path), opts, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}
        self._lock = threading.Lock()  # one inference at a time; it already uses all assigned threads
        log.info("embedding model loaded", extra={"event": "embed_ready", "model": spec.name, "threads": threads})

    def _encode(self, texts: list[str]) -> list[list[float]]:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        with self._lock:
            hidden = self.session.run(None, feed)[0]
        if self.spec.pooling == "cls":
            vecs = hidden[:, 0]
        else:
            vecs = (hidden * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
        vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        return vecs.astype(np.float32).tolist()

    def passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.spec.passage_prefix + t for t in texts]) if texts else []

    def query(self, text: str) -> list[float]:
        return self._encode([self.spec.query_prefix + text])[0]


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    spec = get_spec(s.embedding_model)
    return Embedder(spec, s.embedding_model_path, s.embed_threads)


def is_installed() -> bool:
    s = get_settings()
    spec = get_spec(s.embedding_model)
    return (s.embedding_model_path / spec.onnx_file).exists()
