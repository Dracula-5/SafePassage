# Gate 4 report: Instance A (Brain), polish

Run on 2026-10-31, on branch `integration/gate3`.

## Done

- `docs/brain.md`: state graph in Mermaid, planning decisions, closed loop, recovery table.
- Langfuse tracing (`llm/tracing.py`): optional, 3 s limit, never affects a plan. Tested against a mock server only, because there are no keys.
- Final audit: 0 findings.

## Latency, from the live runs

| Step | Time |
|---|---|
| Plan, drill to approval gate | 9 s (rule-based; Gemini adds about 2 calls) |
| Approval to dispatched | About 1 s, plus 1 LLM call per non-English route |
| Deviation to new route dispatched | 5 s |
| Hazard published to deviation detected | Under 1 s |
| Driver silent to offline detected | 5 min 27 s (5 min grace, 30 s sweep) |

Almost all planning time is OR-Tools' fixed 5 s search. Valhalla's largest matrix (40×5) took about 2 s.

**Cost:** 2 LLM calls per plan, plus 1 per route needing translation. No tuning was done, because no Gemini usage was available to measure.

## Not done

- **Langfuse dashboards:** need keys.
- **Cost tuning:** needs a live LLM.
