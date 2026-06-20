"""Unit 11 - Meeting the Standard: OpenTelemetry at the Boundary

What this shows:
- the by-hand trace you built in Unit 1 IS an OpenTelemetry trace, once you map it onto the
  standard's shape: a 32-hex trace id, a 16-hex span id, a parent span id, a name, and
  attributes following the OTel GenAI semantic conventions (gen_ai.*);
- so "adopt OpenTelemetry" is not "throw away your work" — it is "speak the standard at the
  boundary," where signal crosses processes, services, and substrates;
- the decision the harness actually faced: adopt the SDK, or stay compatible without it.

Run (pure Python, no endpoint or OTel SDK needed):
    python examples/11/to_otel.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace  # noqa: E402


def _hex(n_bytes: int) -> str:
    return uuid.uuid4().hex[: n_bytes * 2]


def to_otel_span(trace: Trace, name: str, gen_ai: dict) -> dict:
    """Map our hand-rolled Trace + a model call onto an OpenTelemetry span.

    trace_id / span_id / parent_span_id are exactly OTel's trace context; the gen_ai.* keys are
    the OTel GenAI semantic conventions, so any OTel backend understands the call without bespoke
    parsing. (We build the dict by hand — no SDK dependency — which is the whole point below.)
    """
    return {
        "name": name,
        "trace_id": trace.trace_id.replace("-", "")[:32].ljust(32, "0"),  # 16-byte hex
        "span_id": _hex(8),  # 8-byte hex
        "parent_span_id": None,
        "attributes": {
            # OTel GenAI semantic conventions — the standard names every backend indexes.
            "gen_ai.system": gen_ai["system"],
            "gen_ai.request.model": gen_ai["model"],
            "gen_ai.usage.input_tokens": gen_ai["input_tokens"],
            "gen_ai.usage.output_tokens": gen_ai["output_tokens"],
            # Our own join key rides along as a custom attribute — still joinable.
            "session.id": trace.session_id,
        },
    }


def main() -> None:
    import json

    trace = Trace.new()
    span = to_otel_span(
        trace,
        name="chat.completion",
        gen_ai={"system": "openai", "model": "gpt-oss-120b", "input_tokens": 1840, "output_tokens": 210},
    )
    print("our hand-rolled Trace, mapped to an OpenTelemetry span:")
    print(json.dumps(span, indent=2))

    print("\nadopt the OTel SDK, or stay compatible without it? — the decision, not a default:")
    print("  reach for the SDK when:")
    print("    - signal must cross process / service / substrate boundaries (the exigence)")
    print("    - you want a collector + standard backends (Langfuse / Phoenix / Arize / Tempo)")
    print("    - multiple teams/services must share one trace contract")
    print("  stay hand-rolled + compatible when:")
    print("    - one process, thin-dependency budget, full control of the shape")
    print("    - the GenAI conventions are still moving and you don't want churn")
    print("\npersonal_agent chose the second: an OTel-SHAPED layer, no SDK — and that is a")
    print("defensible call, not a failure. You meet the standard when the boundary forces it.")


if __name__ == "__main__":
    main()
