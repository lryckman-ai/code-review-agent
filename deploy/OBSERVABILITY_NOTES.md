# LangSmith + shadow scoring — session notes (in progress, resume here)

Started 2026-09-04, paused to tear down before finishing the live demo.
Nothing built yet — this is what we learned reading the code + checking the
live deployment, and the plan for next time.

---

## LangSmith — how it's wired

`monitoring/tracing.py`:
1. `setup_langsmith()` runs once at `pipeline.py` import time. If
   `LANGSMITH_API_KEY` is set, it registers `"langsmith"` as a LiteLLM
   `success_callback` — every LiteLLM completion call in that process
   automatically becomes a span, no per-call code needed.
2. `@trace_review` wraps `run_review()` (the whole pipeline) as the *root*
   trace via `langsmith.traceable`, so every model call nests under one trace
   per review run instead of showing up as unrelated spans.
3. No key → both no-op, degrades gracefully.

Project name: **`codereview-agent`** (LANGSMITH_PROJECT). View at
[smith.langchain.com](https://smith.langchain.com).

### Real gap found (not yet decided whether to fix)

`setup_langsmith()` is only called from `pipeline.py`, which runs inside the
**api** pod (supervisor + synthesis + remediation execute there).
`serve_one.py` — what each of the 3 reviewer pods runs — never imports
`pipeline.py` and never calls `setup_langsmith()`. **So today LangSmith only
sees 3 of the 6 agent calls per review** (supervisor's own reasoning,
synthesis, remediation) — not what `security_reviewer` / `performance_reviewer`
/ `dependency_auditor` actually said, even though those are the calls with
the real findings in them.

Two ways to close this, not yet chosen:
1. Add the same `setup_langsmith()` call (+ `load_dotenv`) to the top of
   `serve_one.py`, so each reviewer pod independently traces its own LLM
   call. Simplest, but the resulting reviewer spans would NOT nest under the
   api pod's root trace (LangSmith's `traceable` context is per-process/
   in-memory — it doesn't cross the A2A HTTP boundary between pods without
   deliberately propagating a trace/parent-run id in the request). You'd get
   4 separate traces per review (1 root in the api pod + 3 independent ones
   in the reviewer pods) rather than 1 unified waterfall.
2. Propagate a parent run id across the A2A call (headers or part of the
   request) so reviewer spans nest under the same root trace as the
   supervisor's. More correct, more work — would need to check what, if
   anything, the A2A protocol/ADK already carries for this.

Next time: open the LangSmith UI together, look at an actual trace from one
of the runs already done (run_ids: `06a30c4b87`, `dcde985c73`, `62772557cb`,
`0745cd4e84`, `5d869afde8` (webhook PR#1), `5226bea348` (webhook PR#2)),
confirm the gap is visible there, then decide whether it's worth fixing.

---

## Shadow scoring — how it's wired

`monitoring/shadow_scorer.py`: after every review, `maybe_queue_shadow_score()`
rolls the dice at `SHADOW_SCORE_PROBABILITY` (default 0.10). If selected, a
background task sends the code + review output to a judge LLM
(`OPENAI_JUDGE_MODEL`, `gpt-oss-120b`) with a rubric — specificity (30%),
correctness (35%), remediation_quality (25%), structure (10%) — weighted into
one `overall` score. Logged as a `gate_decision` row: `passed` if
`score >= SHADOW_SCORE_THRESHOLD` (default 0.70), with the per-dimension
breakdown, judge notes, and flagged key issues.

**As of 2026-09-04, 0 of 6 runs had been shadow-scored** — unsurprising at
10% odds with only 6 runs (P(none in 6 tries) ≈ 53%), not a bug.

The dashboard (`dashboard/src/components/RunDetail.vue`) already has a full
"Gate decision" section built and wired to this — pass/fail badge (green/red),
score vs threshold, judge notes, per-dimension breakdown list — it's just
been rendering "Not scored (shadow scoring is sampled at 10% of runs)" every
time so far because none have hit yet.

### Planned live demo, next time

1. Temporarily force it: `helm upgrade codereview deploy/chart -n codereview
   --reuse-values --set shadow.probability=1.0` — **careful**, `--reuse-values`
   has the exact gotcha noted in `PHASE_E_RUNBOOK.md` §9 (ignores
   `values.yaml`, only reuses the prior release's already-resolved values) —
   only safe here because we're setting a value that already has a resolved
   default from the *previous* successful install; still, safer to re-run the
   **full** `helm upgrade --install ... --set ...` command from
   `RESUME_CHECKLIST.md` with `--set shadow.probability=1.0` added, to avoid
   relying on `--reuse-values` at all.
2. Run one review (`POST /api/review` or a fresh test PR).
3. Watch `kubectl -n codereview logs -f deploy/codereview-api` for
   `[shadow] Quality check queued for run ...` then a `shadow_score` /
   `gate_decision` event.
4. Look at the result in the dashboard's RunDetail view (or `GET
   /api/runs/{run_id}` — the `gate_decision` field).
5. **Revert** `shadow.probability` back to `0.10` afterward (redeploy again)
   — don't leave it at 100%, it doubles LLM traffic per review (main pipeline
   + a judge call every time) for no reason past the demo.
