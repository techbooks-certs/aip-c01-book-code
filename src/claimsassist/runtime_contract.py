"""Application JSON contract inside the AgentCore SDK streaming envelope."""

from .baseline import BoundaryError, _string


def validate_result(result):
    fields = {
        "status",
        "mode",
        "synthetic_data",
        "response",
        "stop_reason",
        "tool_proposals",
        "write_tools_available",
        "successful_evidence_reads",
        "quality_evaluated",
    }
    if not isinstance(result, dict) or set(result) != fields:
        raise BoundaryError("Invalid runtime result fields")
    if (
        result["status"] != "completed"
        or result["mode"] not in ("fixture", "bedrock")
        or result["synthetic_data"] is not True
        or result["write_tools_available"] is not False
        or result["quality_evaluated"] is not False
        or result["stop_reason"] != "end_turn"
    ):
        raise BoundaryError("Runtime did not return a read-only completed result")
    _string(result["response"], "runtime response", 16000)
    proposals = result["tool_proposals"]
    reads = result["successful_evidence_reads"]
    if (
        type(proposals) is not int
        or not 1 <= proposals <= 3
        or type(reads) is not int
        or not 1 <= reads <= proposals
    ):
        raise BoundaryError("Invalid runtime evidence counters")
    return result
