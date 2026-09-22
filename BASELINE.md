# Baseline — service as inherited

Practice run `run_92df720d2d`, 2026-09-22 21:12 local. Only change before this run: a real Groq key
on line 19 (the shipped placeholder key made every order a 500; a first run with the placeholder
was recorded separately).

## Score: −1,964 AED

| Line | AED | Count |
|---|---|---|
| Revenue delivered | +2,536 | 63 delivered of 150 |
| Allergy incidents | −3,000 | 6 (of 22 real conflicts in the stream) |
| False allergy flags | −575 | 23 |
| Food wasted (cooked then cancelled) | −121 | 7 |
| Lateness | −144 | 48 minutes late in total |
| Lost key accounts | −660 | 8 rejected |
| Failed after accepting | 0 | 0 |
| Walked away (bad promise) | 0 | 0 |

Rejected: 80 (53 of them were HTTP 500s, recorded by the arena as schema + transport errors).
Decisions marked AI-used: 97. p50 latency 777 ms, p95 1,187 ms.

## Root causes seen in the service log

- **53 × `KeyError: 'choices'`** — Groq returned HTTP 429 (free tier: 30 requests/min on
  `openai/gpt-oss-20b`). The code indexes the response without checking status, the exception
  propagates, Flask returns a 500, the arena records a rejection. All 53 fell in the two busiest
  minutes of the shift (95 and 64 events/min). Verified separately: a 40-call burst yields 33 × 429.
- **Allergy regex both misses and over-flags**: 6 incidents missed (notes without the keywords, e.g.
  "reacted badly to gluten", "makes me ill") and 23 false alarms (negations, allergens not in the
  order, past-tense allergies).
- **Model rejects makeable orders** when the note mentions an allergy (observed in smoke tests),
  costing revenue and key accounts for nothing.
- **Promise ignores the queue**: cook time + 2, so orders are late whenever stations are busy.
- **Failed-after-accept is 0 only because so much was rejected**; stock is deducted for the first
  item only and never restored, so a service that accepts more will hit this line.
