"""Download embedding models into MODELS_DIR (run once while online; see scripts/download_models.ps1).

    python -m app.tools.download_models            # the configured EMBEDDING_MODEL
    python -m app.tools.download_models --all       # every supported model (for offline bundles)
"""
import os
import sys

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import hf_hub_download  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.embed.models import EMBEDDING_MODELS, get_spec  # noqa: E402


def download(name: str) -> None:
    spec = get_spec(name)
    target = get_settings().models_dir / spec.name
    if (target / spec.onnx_file).exists() and (target / "tokenizer.json").exists():
        print(f"{spec.name}: already installed at {target}")
        return
    print(f"{spec.name}: downloading from {spec.repo} ...", flush=True)
    for f in (spec.onnx_file, "tokenizer.json", "config.json"):
        hf_hub_download(spec.repo, f, local_dir=str(target))
    print(f"{spec.name}: done")


def main() -> int:
    names = list(EMBEDDING_MODELS) if "--all" in sys.argv else [get_settings().embedding_model]
    for name in names:
        download(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
