"""Validate this application's bounded SSE contract, not arbitrary SSE protocols."""

from .baseline import BoundaryError, strict_json


def consume_sse(lines, sink):
    """Consume SDK iter_lines bytes; completion requires a fully delimited frame."""
    total = 0
    event = None
    data = []
    state = "new"
    text_size = 0
    for raw in lines:
        if not isinstance(raw, bytes):
            raise BoundaryError("Invalid SSE byte stream")
        total += len(raw) + 1
        if total > 100000:
            raise BoundaryError("SSE byte bound exceeded")
        try:
            line = raw.decode("utf-8").removesuffix("\r")
        except UnicodeDecodeError:
            raise BoundaryError("Invalid SSE encoding") from None
        sink.write(line + "\n")
        if line:
            if state == "complete":
                raise BoundaryError("Content follows terminal SSE frame")
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if not separator:
                raise BoundaryError("Invalid SSE field")
            value = value.removeprefix(" ")
            if field == "event" and event is None:
                event = value
            elif field == "data":
                data.append(value)
            else:
                raise BoundaryError("Unexpected or duplicate SSE field")
            continue
        if event is None and not data:
            continue
        if event is None or not data:
            raise BoundaryError("Incomplete SSE frame")
        payload = strict_json("\n".join(data))
        if not isinstance(payload, dict):
            raise BoundaryError("SSE data must be an object")
        if event == "error":
            raise BoundaryError("Runtime reported SSE failure")
        if event == "start":
            if state != "new" or payload != {"status": "running"}:
                raise BoundaryError("Invalid SSE start")
            state = "started"
        elif event == "delta":
            if (
                state not in {"started", "streaming"}
                or set(payload) != {"text"}
                or not isinstance(payload["text"], str)
                or not payload["text"]
            ):
                raise BoundaryError("Invalid SSE delta")
            text_size += len(payload["text"])
            if text_size > 16000:
                raise BoundaryError("SSE text bound exceeded")
            state = "streaming"
        elif event == "complete":
            expected = {"status", "mode", "write_tools_available", "quality_evaluated"}
            if (
                state != "streaming"
                or set(payload) != expected
                or payload["status"] != "completed"
                or payload["mode"] not in {"fixture", "bedrock"}
                or payload["write_tools_available"] is not False
                or payload["quality_evaluated"] is not False
            ):
                raise BoundaryError("Invalid SSE completion")
            state = "complete"
        else:
            raise BoundaryError("Unknown SSE event")
        event = None
        data = []
    if state != "complete" or event is not None or data:
        raise BoundaryError("SSE ended without a complete terminal frame")
    return {
        "status": "completed",
        "validated_bytes": total,
        "text_characters": text_size,
        "quality_evaluated": False,
    }
