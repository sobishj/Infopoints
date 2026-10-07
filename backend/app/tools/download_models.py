"""Download the embedding model into MODELS_DIR (run once while online; see scripts/download_models.ps1)."""
import os
import sys

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import snapshot_download  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    target = s.embedding_model_path
    if (target / "config.json").exists() and "--force" not in sys.argv:
        print(f"{s.embedding_model} already present at {target}")
        return 0
    print(f"Downloading {s.embedding_model} to {target} (about 2.3 GB)…", flush=True)
    snapshot_download(repo_id=s.embedding_model, local_dir=str(target),
                      ignore_patterns=["onnx/*", "*.onnx", "imgs/*", "colbert_linear.pt", "sparse_linear.pt",
                                       "*.md", ".gitattributes"])
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
