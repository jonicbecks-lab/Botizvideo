from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class AsyncJsonlWriter:
    """Single-writer buffered JSONL sink with bounded backpressure.

    Producers enqueue already-serializable rows. A dedicated task owns the file handle,
    batches writes, and flushes periodically. This avoids opening the file per market
    event while preserving ordering within one writer.
    """

    def __init__(self, path: str | Path, *, queue_maxsize: int = 50000,
                 batch_size: int = 1000, flush_interval_sec: float = 1.0) -> None:
        if queue_maxsize < 1 or batch_size < 1 or flush_interval_sec <= 0:
            raise ValueError("invalid writer configuration")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=queue_maxsize)
        self.batch_size = int(batch_size)
        self.flush_interval_sec = float(flush_interval_sec)
        self._task: asyncio.Task | None = None
        self._closed = False
        self.enqueued_count = 0
        self.written_count = 0
        self.flush_count = 0
        self.max_queue_depth = 0

    async def start(self) -> "AsyncJsonlWriter":
        if self._closed:
            raise RuntimeError("writer already closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"jsonl:{self.path.name}")
        return self

    async def write(self, row: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("writer is closed")
        if self._task is None:
            await self.start()
        await self.queue.put(row)
        self.enqueued_count += 1
        self.max_queue_depth = max(self.max_queue_depth, self.queue.qsize())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is None:
            return
        await self.queue.put(None)
        await self._task
        self._task = None

    def stats(self) -> dict[str, int | float | str]:
        return {
            "path": str(self.path),
            "enqueued_count": self.enqueued_count,
            "written_count": self.written_count,
            "flush_count": self.flush_count,
            "queue_depth": self.queue.qsize(),
            "max_queue_depth": self.max_queue_depth,
            "queue_maxsize": self.queue.maxsize,
        }

    async def _run(self) -> None:
        batch: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.flush_interval_sec
        with self.path.open("a", encoding="utf-8", buffering=1024 * 1024) as f:
            while True:
                timeout = max(0.0, deadline - loop.time())
                item: dict[str, Any] | None
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    item = None  # flush tick, distinguished below by empty queue state
                    timed_flush = True
                else:
                    timed_flush = False

                if timed_flush:
                    if batch:
                        self._flush(f, batch)
                        batch.clear()
                    deadline = loop.time() + self.flush_interval_sec
                    continue

                if item is None:  # explicit close sentinel
                    if batch:
                        self._flush(f, batch)
                        batch.clear()
                    f.flush()
                    return

                batch.append(item)
                self.queue.task_done()
                if len(batch) >= self.batch_size:
                    self._flush(f, batch)
                    batch.clear()
                    deadline = loop.time() + self.flush_interval_sec

    def _flush(self, f, batch: list[dict[str, Any]]) -> None:
        f.write("".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in batch))
        f.flush()
        self.written_count += len(batch)
        self.flush_count += 1


class AsyncJsonlDirectory:
    """Lazily manages one buffered writer per logical JSONL stream name."""

    def __init__(self, root: str | Path, **writer_kwargs: Any) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.writer_kwargs = writer_kwargs
        self._writers: dict[str, AsyncJsonlWriter] = {}
        self._lock = asyncio.Lock()

    async def write(self, name: str, row: dict[str, Any]) -> None:
        writer = self._writers.get(name)
        if writer is None:
            async with self._lock:
                writer = self._writers.get(name)
                if writer is None:
                    writer = AsyncJsonlWriter(self.root / f"{name}.jsonl", **self.writer_kwargs)
                    await writer.start()
                    self._writers[name] = writer
        await writer.write(row)

    async def close(self) -> None:
        await asyncio.gather(*(w.close() for w in self._writers.values()))

    def stats(self) -> dict[str, dict[str, int | float | str]]:
        return {name: writer.stats() for name, writer in self._writers.items()}
