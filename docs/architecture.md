# Kitchen Decision Service — Architecture Note

Youssef Adel Gad · Tech Lead candidate · 26 September 2026

## How it works now

```
 Imdad kitchen ──POST /kitchen (HMAC signed)──▶  Flask + gunicorn (1 worker, 4 threads)  on Render, Frankfurt
                                                 │
                                                 ├─ 1. verify signature; new run id ⇒ reset kitchen state
                                                 ├─ 2. non-order event ⇒ update state (lateness ⇒ wider buffer)
                                                 ├─ 3. ORDER_PLACED ⇒ NOTE READER (outside the lock)
                                                 │       cache by note text ─▶ Groq gpt-oss-20b (4 s, retry once on 429)
                                                 │       ─▶ qwen3.8-27b ─▶ gpt-oss-120b ─▶ keyword fallback ; 6 s total
                                                 │       returns {allergens, eater_items, key_account, cancel_risk}
                                                 └─ 4. DECIDE (under one lock, pure arithmetic, ~1 ms)
                                                         stock for every item ▸ cancel risk ▸ 4 station clocks
                                                         ▸ promise = ready − now + 1 ▸ limit 30 (26 ordinary) ▸ shift end
                ◀──{decision, promised_minutes, allergy_risk, reason, ai_used}──  always JSON, never a 500
```

**The model's job is one thing: turn free text into facts.** Everything with a number in it is code,
because the score is arithmetic on facts the code can hold exactly. The kitchen is invisible to us but
deterministic, so the service mirrors it: four station clocks, first come first served, cook time equal to
the sum of the items plus 3 minutes when an allergy is flagged. Calibrated on a real run, the mirror
reproduced actual lateness to the minute, so promises are computed, not guessed.

**Model.** `openai/gpt-oss-20b` on Groq, reasoning effort low, JSON output, temperature 0; the same
prompt on `qwen/qwen3.8-27b` and `gpt-oss-120b` as fallbacks, each with its own free-tier bucket.
About 40% of orders reach the model (30% of notes are empty, 60% repeat), ~520 input and ~100
output tokens per call. **Cost per 1,000 orders: about 0.03 USD** at list price (0.075 / 0.30 USD per
million tokens); zero on the free tier; zero for hosting on Render's free plan (7 USD/month paid).
When the model is unavailable the keyword reader answers, marked `ai_used: false`; on the practice
notes it flags every real conflict, at the price of 3 false alarms.

## What changed structurally

| Inherited | Now |
|---|---|
| Model decides accept/reject and promise, with no kitchen data | Model returns facts; code decides |
| One model call per order, no timeout, crash on any error | Cache, 3 models, 6 s deadline, fallback, catch-all |
| Keyword regex sets the allergy flag | Facts ∩ menu allergens of the items the allergic person eats, plus a keyword second opinion when the model saw nothing |
| Stock: first item only, never reset | Every item, reserved on accept, reset per run id |
| Promise = cook + 2 | Promise from four station clocks; 30/26-minute limit; shift-end cutoff |
| Key accounts and cancellations invisible | Recognised from the note; served in full / rejected |
| Key in source, no signature check, no tests | Env secrets, HMAC enforced, threads + lock, 71 tests, offline replay harness |

Kept on purpose: single file, Flask, gunicorn, Dockerfile, in-memory state (single instance is a
requirement, and state is per run). A self-ping keeps the free instance awake; a GitHub cron was tried
first and ran every 5 hours instead of 5 minutes.

## What breaks at ten times the volume

1,500 orders in six minutes is 4 orders a second, 600 model calls a shift, 100 a minute in the rush.
- **Model quota, first.** Three free buckets give about 40 calls a minute by tokens. At 10× the
  fallback answers most rush notes. Fix: a paid tier (still cents), a shorter prompt, and routing on
  Groq's remaining-quota headers instead of fixed order.
- **CPU and threads, second.** Render's free instance is 0.1 vCPU with 4 threads; at 4 orders a second
  with ~1 s model calls, requests queue and the 10 s limit is at risk. Fix: a paid instance and 8–16
  threads; the lock section is ~1 ms and is not the bottleneck.
- **State, third.** In-memory state is right while "single instance" is a rule. The day it is not, the
  station clocks and stock move to Redis with the same lock semantics, and the note cache becomes shared.
- **Not a problem at 10×:** the decision arithmetic, the request size, the signature check, memory.

**Next steps, in order:** reconcile stock from the 30-minute inventory snapshot; use the cook-started
and delivered events to correct the station clocks; route by rate-limit headers; in a real deployment,
take key accounts and allergen profiles from the CRM by customer id and treat the note as the fallback.

## Hosting alternatives considered

Hugging Face Spaces (the pack's Dockerfile target) now requires a paid plan for Docker; Koyeb closed its
free tier. Render was chosen: free, no card, fixed URL, single instance, outbound allowed. Heroku is the
natural home if Salesforce becomes the system of record (Heroku Connect). Cloud Run or Lambda would
need external state; Cloudflare Durable Objects fit the one-stateful-instance model but mean leaving Python.

## Time spent

About **4 hours**: 3 hours on the review, repairs, tests and deployment; 1 hour on this write-up and the
slides. Plus five practice shifts of six minutes each. The detail is in `TIMELOG.md` in the repository.
