"""
Section 19 - Evaluation: LLM-as-judge for open-ended answers.

Some outputs have no single right answer (explanations, summaries). Grade them
with a second model call: give the judge the question, the answer, and a rubric,
and have it return a structured score. We validate the judge's output with
Pydantic (Section 6) so a score is always a number in range.

    python examples/19/eval_judge.py
"""

import sys
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()


class Verdict(BaseModel):
    score: int = Field(ge=1, le=5)      # 1 = poor, 5 = excellent
    reason: str


def generate(question: str) -> str:
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": question}], temperature=0.7
    )
    return r.choices[0].message.content


def judge(question: str, answer: str) -> Verdict:
    prompt = (
        f"You are grading an answer for a beginner audience.\n"
        f"Question: {question}\nAnswer: {answer}\n\n"
        "Score 1-5 for correctness, clarity, and completeness, and give a one-line reason."
    )
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema":
                         {"name": "Verdict", "schema": Verdict.model_json_schema(),
                          "strict": True}},
        temperature=0,                  # judging should be stable
    )
    return Verdict.model_validate_json(r.choices[0].message.content)


question = "Explain what an API is to a complete beginner, in 2 sentences."
answer = generate(question)
verdict = judge(question, answer)

print("ANSWER:", answer)
print(f"\nJUDGE: score={verdict.score}/5 -- {verdict.reason}")
