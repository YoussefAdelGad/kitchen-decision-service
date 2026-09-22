# Kitchen Decision Service — design for the corrected service

Date: 2026-09-22. Status: proposed, awaiting approval.
Baseline: −1,964 AED (`BASELINE.md`). Grader rules derived from the run table: `GRADER_RULES.md`.

## 1. Decision: where the model sits

The model reads the customer note and nothing else. It returns structured facts. Code owns every
number: stock, the four-station queue, the promise, the shift clock, and the accept/reject call.

Why: every cost line in the score is arithmetic on facts the code can hold exactly, except one —
understanding a free-text note. The model is the only tool for that, and it is the only place it
adds information. Putting it anywhere else adds latency, quota use and run-to-run noise without
adding knowledge. This departs from the brief's "one model call per order, no hand-written rules";
the Guide itself says a per-order model design runs out of quota, and the baseline proved it
(53 crashes from rate limiting).

## 2. What changes, file by file

Working folder: `kitchen-service/`. Flask, gunicorn and the Dockerfile stay. `service.py` shrinks
to an HTTP shell; the logic moves into a small package.

| File | Status | Responsibility |
|---|---|---|
| `service.py` | rewritten | Flask routes, signature check, run-id reset, timing budget, JSON response shaping. ~90 lines. |
| `kitchen/world.py` | new | Loads `world.json` once: menu, recipes, cook times, allergens, opening stock, constants. Replaces the hand-copied dicts. |
| `kitchen/state.py` | new | `KitchenState`: run id, stock, in-flight orders, adaptive lateness buffer. `reset()`, `reserve()`, `release()`, `apply_event()`. Thread-safe. |
| `kitchen/queue.py` | new | Pure function: given in-flight orders and the current minute, estimate when a new order would start cooking on one of 4 stations. |
| `kitchen/notes.py` | new | `NoteReader`: normalise note → cache lookup → model call (timeout, retry on second model) → validated `NoteFacts`; keyword fallback if the model fails. |
| `kitchen/policy.py` | new | `decide(order, facts, state) → Decision`. All accept/reject/promise/flag rules in one place, no I/O. |
| `kitchen/fallback.py` | new | Keyword allergen reader used when the model is unavailable. Deliberately conservative. |
| `world.json` | copied in | Source of truth for kitchen data. |
| `tests/test_*.py` | new | Unit tests for state, queue, policy, fallback, signature. |
| `tests/replay.py` | new | Replays the 150 labelled baseline orders through the policy offline and scores them with the formula. |
| `requirements.txt` | edited | + `python-dotenv`, `pytest`. |
| `Dockerfile` | edited | Copies the package and `world.json`; `--threads 4`. |
| `.env.example`, `.gitignore` | new | Secrets stay out of source. |

Removed: hard-coded API key, `PRICES`/`COOK`/`RECIPES`/`stock` literals, `ALLERGY_WORDS` regex,
global counters, bare `except`, `print` logging.

## 3. Request flow

```
POST /kitchen
  verify HMAC (X-Imdad-Timestamp, X-Imdad-Signature)        → 401 if enforce mode and invalid
  run id changed?                                            → state.reset()
  type != ORDER_PLACED                                       → state.apply_event(); 200 {"status":"ok"}
  facts = notes.read(note)                                   → cache / model / fallback, ≤ ~4.5 s
  decision = policy.decide(order, facts, state, minute)
  if accept: state.reserve(order, cook_minutes, flagged)
  200 {decision, promised_minutes, allergy_risk, reason, ai_used}
```

Total budget: model 4 s timeout, one retry on the secondary model 3 s, everything else < 50 ms.
Any exception anywhere → a safe `reject` with `reason: "internal error"` rather than a 500.

## 4. Policy rules (`policy.py`)

Inputs: items, value, minute, `NoteFacts{allergens, eater_items, key_account, cancel_risk}`, state.

1. **Allergy flag** = `allergens ∩ allergens_of(eater_items ∩ order_items)`. `eater_items = None`
   means "anyone eating this order" (all items); `[]` means "nobody in this order". Flagging never
   causes a rejection.
2. **Stock**: reject if any ingredient across all items is short. (Baseline checked item 0 only.)
3. **Cancel risk**: reject ordinary orders whose note signals likely cancellation. Key accounts are
   still accepted.
4. **Queue**: `wait = queue.estimate_start(state, minute) − minute`;
   `promise = wait + cook + (3 if flagged) + buffer`. Reject if `promise > 30` or
   `minute + promise > 240` (shift end → failed order).
5. **Rush headroom**: ordinary orders must fit within `30 − reserve` (reserve = 4 min, tunable);
   key accounts may use the full 30. Keeps stations available for orders that cost 2× value to
   refuse.
6. **Value density**: when `wait > 12`, reject ordinary orders below `value / cook_minutes < 5.0`
   (tunable; calibrated with the replay harness).
7. Promise is a whole number clamped to 1..30.

Buffer starts at 2 and grows by observed `minutes_late` from `ORDER_DELIVERED` (cap 6).

## 5. Queue estimate (`queue.py`)

The kitchen is invisible, so we simulate it from what we know:
- Each accepted order: `cook` (sum of items, +3 if flagged), `accepted_at`, `started_at`
  (from `ORDER_COOK_STARTED`), `done` (from delivered/failed/cancelled).
- Stations = 4. Orders with `started_at` occupy a station until `started_at + cook`. Orders not yet
  started are queued FIFO by `accepted_at` and assigned to the earliest-free station.
- The new order's start = the earliest-free station after all queued orders are placed.

## 6. Note reader (`notes.py`)

- Normalise: lowercase, strip punctuation and whitespace; empty → `NoteFacts.none()` with no call.
- Cache: dict keyed on normalised note, per process, cleared never (notes are run-independent).
- Model: Groq, `openai/gpt-oss-20b`, `reasoning_effort: low`, `response_format: json_object`,
  `temperature 0`, `max_tokens 200`, timeout 4 s. On 429/5xx/timeout: one retry on
  `llama-3.1-8b-instant` (separate 30 rpm bucket), timeout 3 s. Then fallback.
- Prompt: system message with the allergen vocabulary (dairy, gluten, sesame, peanut), the menu
  items, and the rules from `GRADER_RULES.md` expressed as instructions (intolerance counts;
  people not eating don't count; negations; past tense; "the app saved that by mistake").
  User message: the raw note. Output schema:
  ```json
  {"allergens": ["peanut"], "eater_items": null, "key_account": false,
   "cancel_risk": false, "cancel_if_over_minutes": null}
  ```
- Validation: unknown allergens dropped, unknown items dropped, malformed JSON → fallback.
- `ai_used` is true only when the model answered (cache hits from a model answer count as true).

## 7. Fallback reader (`fallback.py`)

Keyword map → allergen (peanut: peanut, groundnut, satay sauce; sesame: sesame, tahini, seeds;
dairy: dairy, milk, cheese, creamy, lactose; gluten: gluten, wheat, flour, bread, coeliac).
Negation patterns (`not allergic`, `no .* allergy`, `used to be`, `not for him`, `not eating`,
`cats`, `waiting`) → no allergens. Key-account patterns (`order from you`, `since you opened`,
`every tuesday`, `team lunch`, `third order`, `office`). Cancel patterns (`might step out`,
`might have already ordered`, `not sure i will be home`, `i will cancel`, `cancel if`).
Bias: when an allergen keyword matches an item and no negation matches, flag. 500 vs 25.

## 8. Events (`state.apply_event`)

| Event | Effect |
|---|---|
| `ORDER_COOK_STARTED` | set `started_at` |
| `ORDER_DELIVERED` | release order; feed `minutes_late` into buffer |
| `ORDER_FAILED`, `ORDER_CANCELLED_BY_CUSTOMER` | release order (stock is not restored: cancelled food was cooked; failed means the kitchen lacked it) |
| `INVENTORY_SNAPSHOT` | `stock = min(ours, snapshot − reservations of orders not yet started)` |

## 9. Security and operations

- HMAC verification per the Guide; `SIGNATURE_MODE=enforce|log` (default enforce; log mode is
  for first deployment until Test connection confirms the secret).
- Secrets only from environment (`MODEL_API_KEY`, `IMDAD_SIGNING_SECRET`, optional
  `GEMINI_API_KEY` unused in v1).
- `GET /kitchen` health: counters (accepted, rejected, model calls, cache hits, fallbacks,
  signature failures), run id, buffer. No stock dump.
- One JSON log line per decision.
- gunicorn `--workers 1 --threads 4`; state guarded by a lock. Single instance by design.

## 10. Testing

- Unit tests per module; policy tests use the incident/false-alarm cases from `GRADER_RULES.md`.
- `tests/replay.py`: runs the 150 labelled orders with (a) perfect facts from the labels, to score
  the policy alone, and (b) the fallback reader, to score the degraded mode. Reports the formula
  lines so tuning happens offline, not with practice runs.
- A one-off script scores the live model against the 61 distinct notes (60 calls) to measure its
  accuracy before we rely on it.

## 11. Deliberately left alone

- Framework and hosting shape (Flask, gunicorn, Docker) — fine for 150 orders in 6 minutes.
- No database: state is per run and the run is single-instance by requirement.
- No async/queueing: the arena waits for each answer; concurrency is 1–4.
- Gemini failover: optional, not needed to satisfy "keeps working when the model does not".

## 12. Assumptions

- Non-`ORDER_PLACED` events carry `minute`; if absent, the last seen minute is used.
- Cooking is FIFO on the earliest free station.
- The 3-minute allergy handling applies before the order is ready.
- The wake-up test order arrives with a different run id from the scoring shift.
