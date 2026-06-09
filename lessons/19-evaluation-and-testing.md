# Section 19 — Evaluation & Testing

**Goal:** answer the question "is it actually any good — and did my change help or hurt?"
You'll build two complementary evaluators: **golden tests** for tasks with a checkable
answer, and an **LLM-as-judge** for open-ended ones. Together they let you change prompts
and models with evidence instead of vibes.

**Where this fits:** Sections 11–18 made the model *do* things. This section makes those
things *measurable* — the difference between "seems fine when I tried it" and "passes 47/50
cases." It's also how you'd catch a regression after swapping models or editing a prompt.

---

## Why evaluation is its own skill

LLM output is **non-deterministic** and **open-ended**, so you can't just `assert
output == expected` everywhere. But you still need to know if a change is an improvement.
The answer is to build evaluators suited to the task:

- **Checkable answer?** (a fact, a label, a number) → a **golden test**.
- **Open-ended?** (an explanation, a summary, a tone) → an **LLM-as-judge** with a rubric.

Run them on every change. That's regression testing for AI.

---

## Golden tests: when there's a right answer

Treat it like unit testing. A fixed set of inputs, an expected substring (or exact match,
or regex), run at `temperature=0` for stability, with a pass rate at the end. Create
**`work/golden.py`**:

```python
from common import get_client, MODEL

client = get_client()

CASES = [
    ("What is the capital of France? One word.", "Paris"),
    ("What is 2 + 2? Reply with just the number.", "4"),
    ("Name the largest planet in our solar system. One word.", "Jupiter"),
]

def run(question):
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": question}], temperature=0)
    return r.choices[0].message.content

passed = 0
for question, expected in CASES:
    output = run(question)
    ok = expected.lower() in output.lower()
    passed += ok
    print(f"[{'PASS' if ok else 'FAIL'}] {question!r} -> {output!r}")
print(f"\n{passed}/{len(CASES)} passed")
```

```bash
python work/golden.py
```

Now this is a regression suite: change the prompt or the model, rerun, and the pass rate
tells you instantly whether you broke something. *(Reference:
[`examples/19/golden_test.py`](../examples/19/golden_test.py).)*

---

## LLM-as-judge: when there's no single right answer

How do you "test" an explanation? Have a model **grade** it. Give a judge the question,
the answer, and a rubric, and ask for a structured score — validated with Pydantic
(Section 6) so a score is always a number in range. Create **`work/judge.py`**:

```python
from pydantic import BaseModel, ConfigDict, Field
from common import get_client, MODEL

client = get_client()

class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # -> "additionalProperties": false (strict mode)
    score: int = Field(ge=1, le=5)
    reason: str

def generate(question):
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": question}], temperature=0.7)
    return r.choices[0].message.content

def judge(question, answer):
    prompt = (f"You are grading an answer for a beginner audience.\n"
              f"Question: {question}\nAnswer: {answer}\n\n"
              "Score 1-5 for correctness, clarity, completeness; give a one-line reason.")
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema":
                         {"name": "Verdict", "schema": Verdict.model_json_schema(),
                          "strict": True}},
        temperature=0)
    return Verdict.model_validate_json(r.choices[0].message.content)

q = "Explain what an API is to a complete beginner, in 2 sentences."
a = generate(q)
print("ANSWER:", a)
print("JUDGE:", judge(q, a))
```

```bash
python work/judge.py
```

You generated an answer at `temperature=0.7` and graded it at `temperature=0`. Run a
whole dataset through this and you have an automated quality score for open-ended work.
*(Reference: [`examples/19/eval_judge.py`](../examples/19/eval_judge.py).)*

> **Judge with care.** LLM judges are useful but biased: they favor longer answers and
> their own style, and they're not perfect graders. Mitigations: judge at `temperature=0`,
> use a clear rubric, prefer a strong model as judge, validate its output (we do), and
> spot-check judge scores against human judgment now and then.

---

## Putting it to work

- **Build the dataset first.** Even 10–20 representative cases beats none. Add every bug
  you find as a new case so it can't regress silently.
- **Gate changes on it.** Run the suite before/after a prompt edit or model swap; compare
  pass rate (golden) and average score (judge).
- **Track quality *and* cost.** A change that improves scores but doubles tokens
  (Section 10) may not be worth it. Log both (Section 9).

---

## Challenges

1. **Catch a regression.** Add a case your current prompt fails, then improve the prompt
   until the suite is green. *Success:* a red→green cycle driven by the test.
2. **Compare two prompts.** Run the judge over 5 questions with prompt A vs prompt B.
   *Success:* you can say which prompt scores higher, with numbers.
3. **Eval an earlier section.** Point a golden test at your Section 16 RAG: questions your
   corpus answers (expect the fact) and questions it doesn't (expect "I don't know").
   *Success:* it scores grounding, not just correctness.

---

## Recap

- LLM output is non-deterministic and open-ended, so you build **evaluators**, not bare
  `assert`s.
- **Golden tests** (input → expected, at `temperature=0`) are regression tests for
  checkable answers.
- **LLM-as-judge** grades open-ended output against a rubric; validate the judge's score
  (Section 6) and beware judge bias.
- Gate every change on your suite, and track **quality and cost** together.

## Next

**Section 20 — Capstone:** you'll combine everything — retrieval, tools, an agent loop,
structured output, observability, cost tracking, and an eval — into one small end-to-end
application.
