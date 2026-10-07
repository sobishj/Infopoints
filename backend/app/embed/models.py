"""Supported embedding models (ONNX, int8-quantized, run with ONNX Runtime on the CPU).

Measured on the target laptop (Core 7 150U, 10 threads, ~280-token passages):
    multilingual-e5-small   3.9 passages/s   (default: fast, 100 languages incl. Malayalam)
    bge-m3                  0.7 passages/s   (stronger retrieval, 5x slower)
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingSpec:
    name: str
    repo: str                 # Hugging Face repo with an ONNX export
    onnx_file: str
    dim: int
    pooling: str              # "mean" or "cls"
    max_tokens: int
    # Below this best-match similarity a question counts as "not in the documents". Calibrated on real data:
    # e5-small scored answerable questions >= 0.845 and unrelated ones <= 0.827.
    min_similarity: float
    query_prefix: str = ""
    passage_prefix: str = ""


EMBEDDING_MODELS = {
    "multilingual-e5-small": EmbeddingSpec(
        name="multilingual-e5-small", repo="Xenova/multilingual-e5-small", onnx_file="onnx/model_quantized.onnx",
        dim=384, pooling="mean", max_tokens=512, min_similarity=0.835, query_prefix="query: ", passage_prefix="passage: "),
    "bge-m3": EmbeddingSpec(
        name="bge-m3", repo="Xenova/bge-m3", onnx_file="onnx/model_quantized.onnx",
        dim=1024, pooling="cls", max_tokens=1024, min_similarity=0.45),
}


def get_spec(name: str) -> EmbeddingSpec:
    try:
        return EMBEDDING_MODELS[name]
    except KeyError:
        raise ValueError(f"Unknown EMBEDDING_MODEL {name!r}. Choose one of: {', '.join(EMBEDDING_MODELS)}") from None
