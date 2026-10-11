---
title: "Designing Tools for a Model Reader"
linkTitle: "6. Designing Tools for a Model Reader"
weight: 6
---

**Goal:** make the restrictive tools from Units 3-5 easier for a model to use correctly,
without changing a line of what they do. You'll:
- serve the same nine tools with four different descriptions, from a few words each to a
  full rewrite;
- measure what each tool list costs, and where the bytes go;
- lint the definitions against a short set of rules;
- grade the tools' error messages, which are instructions too;
- measure, with `gpt-oss-120b`, whether the model picks the right tool with the right
  arguments, and whether it recovers from a mistake when the error tells it how.

**Where this fits:** Unit 5 showed that restrictive tools have the smallest blast radius and
the largest tool list. This unit works on that list: what the model reads, what it costs and
what it buys. Unit 7 changes *who* sees which tools; Unit 9 caches the list; Unit 11 turns
this unit's evals into regression tests.

---

## Who reads a tool definition

A tool definition has two readers. The SDK reads the Python signature and builds a JSON
schema from it. The model reads whatever the *host* passes on. The Unit 2 host turns each
tool into an OpenAI-style function:

```python
def openai_tools(self) -> list[dict]:
    return [{"type": "function", "function": {
                "name": key,
                "description": f"[{server}] {tool.description or ''}".strip(),
                "parameters": tool.input_schema}}
            for key, (server, tool) in self.tools.items()]
```

So the model sees three things per tool: the namespaced name (`siem__search_events`), the
description, and the input schema. It does **not** see the output schema, the annotations, or
the server's `instructions`. They're in `tools/list`, and other hosts may use them, but the
function-calling format this host speaks has nowhere to put them. Before you tune a
description, check what your host actually sends.

## The same tools, four ways

[`examples/06/styles.py`](../examples/06/styles.py) describes Unit 5's restrictive tools
(five CMDB, five SIEM) four ways:

| style | what the model reads |
|---|---|
| **terse** | a few words per tool (`"Search events."`), nothing per parameter: what many servers ship |
| **original** | the docstrings Units 3-5 wrote |
| **rewritten** | this unit's rules: what it answers, when to use it and what instead, the limits, an example; a description on every parameter; bounds and fixed choices in the schema; annotations |
| **lean** | rewritten, with each shared convention (the window format, the filter syntax) written once |

[`examples/06/styled_server.py`](../examples/06/styled_server.py) serves any style. It imports
the functions from Units 3, 4 and 5 and registers them again with `add_tool`, so a difference
in any measurement comes from what the model reads, never from what the tool does:

```python
for name, fn in functions.items():
    spec = styles.TOOLS[name]
    if STYLE == "original":
        mcp.add_tool(fn)
    elif STYLE == "terse":
        mcp.add_tool(fn, description=spec["terse"])
    else:
        params = styles.params_for(STYLE, name, getattr(source, "MINE", None))
        mcp.add_tool(restyle(fn, params), description=spec["rewritten"],
                     annotations=ToolAnnotations(**styles.READ_ONLY))
```

Parameter descriptions come from the signature, so `restyle` gives the function a new one:
each parameter's type wrapped in `Annotated[..., Field(description=...)]`. The SDK reads
`__signature__`, and the schema follows.

## What a tool list costs

[`examples/06/toolcost.py`](../examples/06/toolcost.py) measures each style as the host sends
it, as `l1_analyst`:

```bash
python examples/06/toolcost.py
```

```text
style         sent  ~tokens  descr  schema  titles  output*  ~tokens without titles
terse        6,184    1,546    158   4,946     880    3,042  1,326
original     8,032    2,008  1,979   4,946     880    3,042  1,788
rewritten   11,822    2,955  2,083   8,659     880    3,042  2,735
lean        11,085    2,771  2,083   7,922     880    3,042  2,551
```

Four things to notice:

- **Even terse costs 1,500 tokens.** Most of a tool list is schema, not prose: types,
  `anyOf` for every optional parameter, defaults. Ten tools with nine parameters each are a
  lot of JSON before anyone writes a word.
- **`titles` is pure repetition.** Pydantic gives every property a `"title"` made from its
  name (`"title": "Dataset"` next to `dataset`). That's 880 bytes, 11% of the original list,
  that a host could strip before sending.
- **The output schemas (3 KB) aren't sent.** They still matter: the SDK validates every
  result against them, and a host that *can* show them to the model will.
- **The rewrite costs 47% more than the original,** and the lean version saves only 6% of
  that. The extra bytes are mostly parameter descriptions and enums. Whether they're worth
  it is a measurement, not an opinion: the selection and recovery evals below.

Run it as `l3_responder` too: the rewritten `dataset` parameter lists four datasets
instead of three, because L3 may read EDR.

## Rules for a model reader

Each rule below is applied in the rewritten style, and checked by the linter in the next
section.

**1. Name the action and the object.** `search_events`, `find_assets`, `get_incident`:
snake_case, a verb and what it acts on. A bare `search` or `query` is ambiguous as soon as
a host loads a second server. The host namespaces names (`siem__search_events`), but the
model still has to choose between your `search_events` and someone else's `search`.

**2. Say what it answers, when to use it, and what to use instead.** A description is a
routing rule for the model. Compare `event_histogram` in the terse style:

```text
Event histogram.
```

with the rewritten one:

```text
Counts of log events per time bucket, plus the total and the peak. Use it FIRST for 'when'
and 'how many' questions about logs: it costs a few hundred tokens whatever the volume.
Window up to 21 days. Then top_values for 'who', and search_events (24h max) to read
events.
```

The rewrite says what comes back, when to reach for it, what it costs, its limit, and where
to go next. Naming the neighbouring tools is what makes a set of tools usable together.

**3. Describe every parameter that isn't obvious, with its format and an example.**
`hostname: "exact short hostname, e.g. jump-prod-02 (no domain)"` heads off the most common
mistake before it happens. A parameter whose name and type say everything (`newest_first:
boolean`) can do without.

**4. Put rules in the schema, not only in prose.** If `limit` is 1-50, say `"minimum": 1,
"maximum": 50`. If `interval` is one of eleven values, make it an `enum`. A model treats
the schema as a rule and the prose as a suggestion. The rewritten `dataset` goes one step
further: its enum lists only the datasets *this persona* may read, so L1 never sees `edr`
as an option.

**5. Advertise in the schema; enforce in your code.** The first version of `restyle` used
`Field(ge=1, le=50)`, and the SDK began validating those bounds before the tool ran. A
`limit` of 500 then came back as pydantic's message instead of the tool's:

```text
1 validation error for find_assetsArguments
limit
  Input should be less than or equal to 50 [type=less_than_equal, input_value=500, input_type=int]
    For further information visit https://errors.pydantic.dev/2.14/v/less_than_equal
```

It's accurate, but it's noise to a model, and it exposes the library. The fix is to
advertise the bound in the schema (`json_schema_extra={"maximum": 50}`) and leave
enforcement to the tool's own check, which says `limit must be between 1 and 50.`:

```python
extra = {k: v for k, v in (("minimum", spec.pop("ge", None)),
                           ("maximum", spec.pop("le", None)),
                           ("enum", list(spec.pop("choices", ())) or None))
         if v is not None}
field = Field(**spec, json_schema_extra=extra or None)
```

**6. Return structured results, bounded.** Every tool here declares its result type (a
`TypedDict` or `dict[str, Any]`), so the SDK publishes an output schema and validates the
result against it. Results stay bounded the way Unit 4 bounded them: a `limit`, an exact
`total`, a `next_cursor`, a `hint` when the question wanted an aggregate.

**7. Annotate, and don't trust annotations.** `readOnlyHint`, `destructiveHint`,
`idempotentHint` and `openWorldHint` tell a *host* what a tool does, for example so it can
skip asking the person before a read. They're hints, written by the server. A host should
believe them only from a server it trusts, and this host doesn't send them to the model at
all. Unit 7 enforces the rules that annotations merely describe; Unit 10 shows a server that
lies about them.

## Lint the definitions

[`examples/06/lint.py`](../examples/06/lint.py) checks every tool as `tools/list` returns it.
The rules are the ones above, as heuristics, so each finding is a question to answer, not a
verdict:

| rule | finding |
|---|---|
| N1 / N2 | the name isn't snake_case, or is a bare generic verb |
| D1 / D2 / D3 | the description is missing or too short, too long, or never says when to use the tool or what to use instead |
| P1 | a parameter has no description and the tool's description doesn't mention it |
| P2 / P3 | a range or a fixed set of choices is stated in prose but not in the schema |
| O1 | no output schema |
| A1 / A2 | no annotations, or annotated read-only with a name that says it writes |

```bash
python examples/06/lint.py --quiet --style terse --style original --style rewritten
python examples/06/lint.py --variant permissive --variant guarded
```

| tools | errors | warnings |
|---|---|---|
| terse | 10 | 56 |
| original | 0 | 33 |
| rewritten, lean | 0 | 0 |
| Unit 5 guarded (`query_sql`, `ppl_query`) | 0 | 5 |
| Unit 5 permissive (`run_sql` + OpenSearch's server) | 9 | 34 |

The original style's findings are the useful ones to read: `find_assets` states "1-50" in
prose only (P2); `event_histogram` lists its intervals in prose, not as an enum (P3); six
tools, every CMDB one among them, never say when to use them or what to use instead (D3). OpenSearch's own
server fails N1 on every tool (`SearchIndexTool`, `GenericOpenSearchApiTool`): CamelCase
names written for a Python class list, not a model.

## Errors are instructions

A refusal is the only thing the model reads between a failed call and its next try. So it
has to be written for that reader. [`examples/06/errors.py`](../examples/06/errors.py) sends
the tools seventeen mistakes models really make, and grades each message on what it does:

- **problem:** it names the argument or quotes the bad value;
- **allowed:** it says what's allowed (a limit, a format, an example, the valid choices);
- **next:** it says what to do next (use, pass, narrow, try, ...);

and on what it must not do:

- **dangling:** point at a tool, parameter or field the caller doesn't have;
- **leaks:** expose internals (a stack trace, a driver name, SQL, a library's help URL).

```bash
python examples/06/errors.py
python examples/06/errors.py --errors generic
```

```text
tool                     mistake                            score  P A N  flags
get_asset                hostname with its domain               3  ✓ ✓ ✓
                         no asset named 'jump-prod-02.corp.example'. Hostnames look like jump-prod-02; use find_assets to list hosts by
find_assets              a service by its description           0  ✓ · ·  dangling: list_datasets
                         unknown service 'customer database'; list_datasets / the catalog lists services in cmdb.services.
...
search_events            a window too wide for a search         3  ✓ ✓ ✓
                         the window is 3d; search_events allows at most 24h. Narrow it, for example to the spike an event_histogram sho
search_events            a cursor it made up                    1  ✓ · ·
                         that cursor isn't one this server issued (or the server restarted). Repeat the search without a cursor.
```

The first run found a real bug, in Unit 3: `find_assets` told the model to use
`list_datasets`, a tool on Unit 3's *catalog* server, not on any server in this variant. A
model would go looking for a tool that isn't there. Unit 3's `find_assets` now lists the
services instead, as `service_dependencies` already did. With that fixed, the shipped
messages average **2.35 of 3**. Replacing every one with "invalid request" (styled_server's
`ERROR_STYLE=generic`) drops the score to **1.00**: "invalid" says there's a problem, and
nothing else.

The pattern behind the best ones is one sentence of each: *what was wrong, what's allowed,
what to do*. For example: "the window is 3d; search_events allows at most 24h. Narrow it,
for example to the spike an event_histogram shows." Three more habits:

- **List the valid values when there are few,** as `count_assets` does with services. When
  there are many, give the format and point at the tool that lists them.
- **Refuse the silent failure.** A filter on a masked field would match nothing and look
  like an answer. `search_events` refuses it and says why.
- **Unexpected errors stay unexpected.** When Postgres is down, the SDK turns the exception
  into "Error executing tool get_asset", with no driver name, no host, no stack. That's
  right: the model can't fix it, and the details belong in your log, not in its context.

## Measure it with the model

Two evals measure what the styles buy. Neither records scores in this lesson: they depend
on your model and endpoint, so run them and keep your numbers.

**Selection.** [`examples/06/selection.py`](../examples/06/selection.py) has twenty
requests, each labelled with the tool that should answer it first and the arguments that
matter. Four should get *no* tool call: restarting a service and isolating a host (nothing
on the menu does that), the weather, and EDR events for L1 (the persona can't read them).
Each request gets one model call per style; the first tool call is graded, then run:

```bash
python examples/06/selection.py --check           # the golden set matches every style
python examples/06/selection.py --repeats 3
```

It reports `right_tool`, `right_args` and `call_ok` (the first call ran without an error),
each with its range over the repeats, plus prompt tokens per request.

**Recovery.** [`examples/06/recovery.py`](../examples/06/recovery.py) asks six questions the
way people ask them, so that the first call usually fails: a hostname with its domain
(`jump-prod-02.corp.example`), a service by its description ("the customer database"), a
CVE id with spaces, an incident by its number ("incident 42"), a field by a synonym ("source
address"), and three days of events for a tool that searches 24 hours at most. Each runs as
a whole conversation, scored on its final answer, under three conditions:

```bash
python examples/06/recovery.py --repeats 3
# original:instructive   original:generic   lean:instructive
```

What to look for:

- **Selection: terse vs the rest.** The terse names are good, so the gap may be smaller
  than you expect on the easy requests. Look at `right_args` (formats, enums) and at the
  four requests that need *no* call.
- **Selection: rewritten vs lean.** If lean isn't measurably worse, the 700 bytes it saves
  are free.
- **Recovery: instructive vs generic.** The number of calls and errors per task tells you
  as much as the success rate: with generic errors, the model either guesses, retries the
  same thing, or gives up.
- **Read the ranges.** Two conditions whose ranges overlap after three runs are not shown to
  differ. Run five.

> **Security:** a description is an instruction the model follows. That makes it an attack
> surface when it comes from a server you don't control: Unit 10 shows a description that
> tells the model to send data elsewhere. Narrowing an enum to the persona's datasets is a
> usability fix, not a control: the tool's code still checks the role, and the backend
> still enforces it. Annotations are claims, not guarantees. Error messages must help the
> model without helping an attacker map your internals: list valid values the caller may
> use, never the ones it may not, and let unexpected errors stay generic.

> **Observe:** the joinable log records `outcome=tool_error` for every refusal, joined to
> the step that caused it. The ratio of refusals to calls, per tool, is the best running
> measure of whether descriptions are working; a tool whose first calls keep failing has a
> description problem. `selection.py` and `recovery.py` write JSONL (`--out`) with every
> call, so a change in a description can be compared before and after.

> **Limits:**
> - **Context.** The tool list is paid on every model call: about 1,500 tokens for these
>   ten tools when terse, 2,000 as shipped and 2,800-2,950 rewritten. Per-property
>   titles are 11% of it. Output schemas (3 KB here) cost nothing with this host and
>   something with hosts that send them.
> - **Calls.** A good error message saves a round trip, which is a model call that carries
>   the whole tool list again. Recovery's `tool_calls` and `prompt_tokens` columns measure
>   exactly that.

## Lab: write tools for a model reader

Open the notebook ([`labs/06_tools_for_a_model.py`](../labs/06_tools_for_a_model.py)) with
marimo:

```bash
marimo edit labs/06_tools_for_a_model.py
```

It walks through the unit in seven steps:

1. Compare what each style's tool list costs.
2. Read one tool in all four styles, side by side.
3. Lint a style.
4. Grade the error messages, instructive or generic.
5. Rewrite one tool's description yourself, with its cost and its lint findings updating as
   you type.
6. With a model endpoint, run the selection eval on the styles you choose.
7. With a model endpoint, run the recovery eval.

## Challenges

1. **Strip the titles.** Change the Unit 2 host's `openai_tools` so that it removes the
   per-property `title` entries before sending (`toolcost.strip_titles` does it).
   *Success:* `toolcost.py` reports the "without titles" figure as what's sent, and
   `selection.py --check` and the Unit 5 grid still run.
2. **Beat lean.** Write a fifth style that costs less than `original` and lints clean.
   *Success:* `toolcost.py` shows it under 8,032 bytes, `lint.py` reports 0 findings, and
   `selection.py` over 5 runs shows `right_args` no worse than `lean` (overlapping ranges
   count as no worse).
3. **Fix the cursor message.** The made-up-cursor refusal scores 1: it never says what a
   cursor looks like or where one comes from. Rewrite it in Unit 4's server. *Success:*
   `errors.py` scores it 3, and the Unit 4 CI step that checks for "isn't one this server
   issued" still passes.

## Recap

- The model reads what the **host** sends: here, the name, the description and the input
  schema. Not the output schema, not the annotations, not the server's instructions.
- A tool list is mostly **schema**: even a terse one costs ~1,500 tokens for ten tools.
  Per-property titles are 11% of it; output schemas aren't sent by this host.
- The rules: name the action and the object; say what it answers, when to use it and what
  to use instead; describe parameters with a format and an example; put bounds and choices
  in the schema, narrowed to the caller; **advertise in the schema, enforce in your code**,
  so violations get your message and not a library's; return bounded, structured results;
  annotate, but treat annotations as hints.
- A **linter** turns the rules into findings. The original tools had 33 warnings, and
  OpenSearch's own server fails the naming rule on every tool.
- **Error messages are instructions:** what was wrong, what's allowed, what to do. The audit
  found one pointing at a tool that wasn't there. Generic errors score 1.00 of 3; the
  shipped ones 2.35.
- Whether a rewrite is worth its tokens is measured: tool selection on a golden set, and
  recovery from mistakes end to end, over repeated runs, reading the ranges.

## Next

**[Unit 7: Identity, Least Privilege and Enterprise Rules](07-identity-least-privilege-and-enterprise-rules.md)**: who is calling.
Every server so far acts as one fixed persona from an environment variable. Unit 7 carries
the caller's identity from the host to Postgres and OpenSearch, gives each caller its own
tool list, and adds a policy layer that enforces what this unit's annotations only describe.
