from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class StreamHealth:
    stream_id: str
    last_event_ts_ms: int | None = None
    last_recv_ts_ms: int | None = None
    reconnect_count: int = 0
    duplicate_count: int = 0
    sequence_gap_count: int = 0
    event_count: int = 0

    def observe(self, event_ts_ms: int, recv_ts_ms: int) -> None:
        self.last_event_ts_ms = int(event_ts_ms)
        self.last_recv_ts_ms = int(recv_ts_ms)
        self.event_count += 1

    def reconnect(self) -> None:
        self.reconnect_count += 1

    def duplicate(self) -> None:
        self.duplicate_count += 1

    def sequence_gap(self) -> None:
        self.sequence_gap_count += 1

    def to_dict(self) -> dict:
        return asdict(self)


def quality_gate(streams: dict[str, StreamHealth], required_streams: tuple[str, ...], now_ms: int,
                 max_stale_ms: int = 5000, max_event_lag_ms: int = 5000,
                 reject_any_sequence_gap: bool = True) -> dict:
    """Point-in-time gate using only information known at `now_ms`.

    A research signal is eligible only when every required stream exists, has emitted
    data, is fresh by receive time, and its latest exchange event timestamp is not too
    far behind. Sequence gaps can invalidate a stream until the caller explicitly
    resets/rebuilds its health after a clean snapshot/reconnect.
    """
    reasons: list[str] = []
    diagnostics: dict[str, dict] = {}

    for stream_id in required_streams:
        h = streams.get(stream_id)
        if h is None:
            reasons.append(f"missing:{stream_id}")
            continue
        if h.last_recv_ts_ms is None or h.last_event_ts_ms is None:
            reasons.append(f"uninitialized:{stream_id}")
            diagnostics[stream_id] = h.to_dict()
            continue
        stale_ms = max(0, int(now_ms) - h.last_recv_ts_ms)
        event_lag_ms = max(0, h.last_recv_ts_ms - h.last_event_ts_ms)
        diagnostics[stream_id] = {
            **h.to_dict(),
            "stale_ms": stale_ms,
            "event_lag_ms": event_lag_ms,
        }
        if stale_ms > max_stale_ms:
            reasons.append(f"stale:{stream_id}")
        if event_lag_ms > max_event_lag_ms:
            reasons.append(f"lagged:{stream_id}")
        if reject_any_sequence_gap and h.sequence_gap_count > 0:
            reasons.append(f"sequence_gap:{stream_id}")

    return {
        "quality_pass": not reasons,
        "required_stream_count": len(required_streams),
        "healthy_stream_count": sum(
            1 for stream_id in required_streams
            if stream_id in diagnostics
            and f"stale:{stream_id}" not in reasons
            and f"lagged:{stream_id}" not in reasons
            and f"sequence_gap:{stream_id}" not in reasons
        ),
        "reasons": reasons,
        "streams": diagnostics,
        "evaluated_at_ms": int(now_ms),
        "max_stale_ms": int(max_stale_ms),
        "max_event_lag_ms": int(max_event_lag_ms),
    }
