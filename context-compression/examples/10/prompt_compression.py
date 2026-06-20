"""
Unit 10 - Prompt-Level Compression.

Compress INSIDE a message: drop the least-informative tokens, keep the load-bearing ones. The real
method is perplexity-based (LLMLingua: a small LM scores how predictable each token is and drops
the predictable ones). This course runs no Hugging Face models (Section 4), so we show the
PRINCIPLE with a deterministic trimmer and -- crucially -- measure the two numbers that must always
be reported together: the compression RATIO and the retained CAPABILITY.

What this shows:
  - trim(): collapse whitespace + drop a filler/stopword list (a blunt stand-in for "least
    predictable token"); report tokens before/after and the ratio.
  - the rule of the unit: a ratio ALONE is meaningless (you can always delete more). The opt-in
    capability check asks the model a question whose answer lives in the prompt, with the full vs
    the trimmed prompt, and sees whether the answer survived.
  - a prompt_compress record (ratio + capability_ok) for the Unit 11 no-regression gate.

The trim + ratio run offline. The capability check is OPT-IN: set OPENAI_BASE_URL + OPENAI_API_KEY
(your foundations .env); offline it skips cleanly.

    python context-compression/examples/10/prompt_compression.py
    python context-compression/examples/10/prompt_compression.py 2>> run.jsonl

Want the real method? If your environment allows Hugging Face models (outside this course's rule):
    pip install llmlingua
    from llmlingua import PromptCompressor
    PromptCompressor().compress_prompt(text, rate=0.5)   # then measure capability the same way
"""

import os
import re
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event
# NB: the foundations `common` (get_client/MODEL) is imported lazily inside the endpoint branch of
# main(), so the offline trim+ratio path runs on the standard library alone -- no httpx needed.

# A blunt stand-in for "low-information token". Real LLMLingua scores perplexity with a model;
# this fixed list is cruder but needs no model. Identifiers, numbers, and content words survive.
FILLER = {
    "a", "an", "the", "this", "that", "these", "those", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "and", "or", "as", "with", "by", "it", "its", "we", "you",
    "i", "our", "your", "so", "very", "really", "just", "please", "kindly", "could", "would",
    "should", "also", "additionally", "furthermore", "moreover", "note", "basically", "actually",
    "simply", "here", "there", "then", "now", "going", "want", "make", "sure", "thing", "things",
}


def trim(text):
    """Collapse whitespace and drop filler words. Deterministic, no model."""
    text = re.sub(r"\s+", " ", text).strip()
    kept = [w for w in text.split(" ") if w and w.lower().strip(".,;:!?()") not in FILLER]
    return " ".join(kept)


# A wordy system prompt + user message. The load-bearing facts (paths, the port 5432) are content
# tokens the filter keeps; the filler around them is what goes.
VERBOSE_SYSTEM = (
    "You are a very helpful and friendly coding assistant. Please note that you should always be "
    "sure to be careful and thorough. It is really important that you basically always cite exact "
    "values, and that you simply do not ever invent any facts that are not actually present."
)
VERBOSE_USER = (
    "So I just wanted to kindly ask you, if you could, to please take a look. We are now going to "
    "need the production database port. For your reference, the service config.py basically says "
    "that the production database host is db-prod-1 and the production database port is 5432."
)
QUESTION = "What is the production database port? Answer with just the number."


def capability_check(client, model, system, user):
    """Ask QUESTION with a given (system, user) prompt; return the model's answer text."""
    r = client.chat.completions.create(
        model=model, max_tokens=16, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user + "\n\n" + QUESTION}])
    return (r.choices[0].message.content or "").strip()


def main():
    sess, trace = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    before = estimate_tokens(VERBOSE_SYSTEM) + estimate_tokens(VERBOSE_USER)
    sys_t, user_t = trim(VERBOSE_SYSTEM), trim(VERBOSE_USER)
    after = estimate_tokens(sys_t) + estimate_tokens(user_t)
    ratio = before / after if after else 0.0

    print("prompt-level compression  (deterministic trimmer; principle, not real LLMLingua)\n")
    print(f"  before: {before:4d} tokens   after: {after:4d} tokens   ratio: {ratio:.1f}x")
    print(f"  trimmed system: {sys_t[:88]}…")

    have_endpoint = os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")
    capability_ok = None
    if not have_endpoint:
        print("\n  (OPENAI_BASE_URL/OPENAI_API_KEY not set -- skipping the capability check)")
        print("  REMEMBER: a ratio without a capability result is half the story (the dishonest half).")
    else:
        from common import get_client, MODEL   # lazy: only the endpoint path needs httpx/openai
        client = get_client()
        full_ans = capability_check(client, MODEL, VERBOSE_SYSTEM, VERBOSE_USER)
        trim_ans = capability_check(client, MODEL, sys_t, user_t)
        capability_ok = ("5432" in trim_ans)
        print(f"\n  capability check: full -> {full_ans!r}   trimmed -> {trim_ans!r}")
        print(f"  retained capability: {capability_ok}  (the answer {'survived' if capability_ok else 'was LOST'} the trim)")

    log_event(sess, trace, 0, "prompt_compress", tokens_before=before, tokens_after=after,
              ratio=round(ratio, 2), capability_ok=capability_ok)
    print("\n  Report ratio AND capability together -- never the ratio alone.")


if __name__ == "__main__":
    main()
