"""Job handlers. Each takes (ctx, payload); raising Requeue puts the job back in the queue."""
from typing import Protocol


class Requeue(Exception):
    def __init__(self, payload: dict, delay_seconds: float):
        super().__init__(f"requeue in {delay_seconds:.0f}s")
        self.payload = payload
        self.delay_seconds = delay_seconds


class JobContext(Protocol):
    job_id: int

    def progress(self, fraction: float, message: str | None = None) -> None: ...
