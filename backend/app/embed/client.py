"""Worker-side client for the api's internal embedding endpoint."""
import time
from typing import Callable

import httpx

from app.config import get_settings


class EmbedServiceError(RuntimeError):
    pass


def embed_passages(texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
    s = get_settings()
    out: list[list[float]] = []
    batch = s.embed_batch_size * 2
    with httpx.Client(timeout=600) as client:
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            for attempt in range(5):
                try:
                    r = client.post(s.embed_url, json={"texts": part},
                                    headers={"X-Internal-Token": s.internal_token})
                    if r.status_code == 503:  # model still loading / missing
                        raise EmbedServiceError(r.json().get("detail", "embedding service unavailable"))
                    r.raise_for_status()
                    out.extend(r.json()["vectors"])
                    break
                except (httpx.HTTPError, EmbedServiceError) as e:
                    if attempt == 4:
                        raise EmbedServiceError(f"Embedding failed: {e}") from e
                    time.sleep(3 * (attempt + 1))
            if progress:
                progress(min(i + batch, len(texts)), len(texts))
    return out
