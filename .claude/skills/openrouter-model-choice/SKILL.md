---
name: openrouter-model-choice
description: How to pick, sanity-check, and swap the OpenRouter model used for meal generation — free-tier gotchas, reasoning-token diagnosis, timeout headroom.
---

# Picking a free OpenRouter model — known gotcha

The current `DEFAULT_MODEL`/`openrouter_model` (`google/gemma-4-26b-a4b-it:free`)
was chosen after several free models failed in ways worth knowing about if you
swap it:

- **Reasoning models can hang or blow the token budget on this task.**
  Several free models (`openai/gpt-oss-20b:free`, `nvidia/nemotron-3-nano-30b-a3b:free`,
  `inclusionai/ling-3.0-flash:free`, `cohere/north-mini-code:free`) spend
  most/all of their output budget on hidden or visible step-by-step
  "reasoning" tokens — literally narrating arithmetic ("previously 150g was
  51 kcal...") instead of just writing the JSON — and either hit
  `max_tokens` (`instructor.v2.core.errors.IncompleteOutputException`) or
  appear to hang for 10+ minutes on a throttled free route. Check
  `response.usage.completion_tokens_details.reasoning_tokens` when
  diagnosing a slow/failing model — a large number there is the signature.
- **The free-tier lineup changes constantly.** A model can be `:free` one day
  and removed the next (`404 ... use this slug instead: <paid-id>`). Query
  the live list before assuming a model ID still works:
  `requests.get("https://openrouter.ai/api/v1/models").json()["data"]`,
  filter `id.endswith(":free")`.
  - Some free routes are rate-limited upstream and return 429 even on a
    trivial request (`google/gemma-4-31b-it:free` did this; the sibling
    `google/gemma-4-26b-a4b-it:free` did not).
- **How to sanity-check a candidate model before wiring it in:** send a
  minimal `client.chat.completions.create(...)` call directly (bypass
  `instructor`) with a small `max_tokens`, and inspect
  `resp.choices[0].finish_reason` and `resp.usage`. `finish_reason: "stop"`
  with `reasoning_tokens` near 0 is a good sign; `finish_reason: "length"`
  with most of the budget in `reasoning_tokens` means pick a different model.
- The system prompt in `generate_day()` explicitly says "Do not show
  your work, explain your reasoning, or narrate your process" — this was
  added after observing reasoning-heavy models ignore a softer instruction
  and helps steer well-behaved models toward direct JSON output. It doesn't
  fix a genuinely reasoning-heavy model; swap the model instead.
- **Even a "good" free model has highly variable latency, not just a binary
  hang/no-hang.** `google/gemma-4-26b-a4b-it:free` has been observed taking
  anywhere from ~2s (trivial prompt) to ~58s (full meal-plan prompt) for a
  normal, successful, non-reasoning response — this is free-tier queuing
  variance, not a code problem. The `OpenAI(..., timeout=...)` in
  `build_client()` must have real headroom above that (currently `120.0`;
  it was `60.0` and a request that legitimately took ~58s+ on a busier route
  came close to tripping it). If the client timeout fires mid-request,
  `instructor` doesn't just retry the same response — it re-runs the full
  generation (up to `max_retries=3` times), so a timeout that's set too
  tight turns a slow-but-fine call into what looks like a multi-minute hang.
  If you see this again: first re-run the raw-call diagnostic above with a
  generous hard wall-clock timeout (60-100s) to confirm the model itself
  still finishes with `finish_reason: "stop"` and low `reasoning_tokens`
  before assuming the model is broken — it may just need a longer client
  timeout.

- **A week is 7x the exposure to this.** Generating a full week on a free
  model means up to 7 sequential calls, each of which can take 30s–3min and
  may burn `max_retries` on the macro validator. Budget 10–20 minutes, and
  prefer a paid model (`anthropic/claude-sonnet-5`) when portion accuracy
  matters. `--use-cached-plan` re-renders `week_plan.json` with no API calls,
  which is the right way to iterate on shopping-list or display changes.

Note: reasoning must stay disabled on every request regardless of which model
you pick — see the "Reasoning must be disabled" section in `CLAUDE.md`.
