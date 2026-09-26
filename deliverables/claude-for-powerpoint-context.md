# Context for Claude in PowerPoint — "Imdad Kitchen Backend" deck

Paste this whole file as context, then ask for the change you want (for example: "tighten slide 2",
"redraw the diagram on slide 3", "make the chart on slide 4 easier to read"). Keep it to 5 slides.

## Who is presenting, to whom, for how long

- Presenter: Youssef Adel Gad, Tech Lead candidate, presenting in his own voice, first person.
- Audience: decision-makers at Imdad who are **not engineers**. No code, no jargon; every technical
  term below has a plain-language substitute (see glossary).
- Format: 10 minutes to present the 5 slides, then 20 minutes of questions from engineers. So the
  slides must be simple, but the speaker notes can carry the detail the questions will probe.
- Theme name: **Imdad Kitchen Backend**. Palette: charcoal `#1F2A30`, amber accent `#E07A1F`, white
  content slides, pale `#F4F1EC` cards, green `#2E7D5B` for good, red `#B23A3A` for bad.
  Fonts: Cambria for titles, Calibri for body. Dark slides for 1 and 5, white for 2–4.
  No decorative bars or accent stripes; no accent lines under titles.

## The story in one paragraph

Imdad runs a kitchen. An outside service answers one question for every order: accept or reject, and
if accept, how many minutes to promise and whether the order carries an allergy risk. The service we
inherited lost money on every shift. I measured it first (−1,964 AED on a 150-order practice shift),
found the causes, fixed what mattered one step at a time, measured each step, and the same shift now
scores +3,816 AED. The key change: the AI now does the one thing only it can do, read the customer's
note, while ordinary code does everything with a number in it, exactly, every time.

## The numbers (all from the same 150 practice orders)

| Run | What the code was | Score (AED) |
|---|---|---|
| 1 | As delivered to us | −1,964 |
| 2 | AI reads the notes; code sets the allergy flag | −14,750 (fixed allergies, exposed the queue problem) |
| 3 | + stock counting, four-stove tracking, computed promise, decline likely cancellations | 3,816 |
| 4 | + hardening, first run on the final host | 3,703 |
| 5 | + two corrections found in run 4 | 3,816 |

Baseline breakdown: revenue 2,536 − allergy incidents 3,000 (6 × 500) − false allergy alarms 575
(23 × 25) − regular customers refused 660 (8) − late food 144 (48 min × 3) − food wasted 121 (7) = −1,964.
Before → after: incidents 6 → 0; false alarms 23 → 0; orders answered with an error 53 → 0; late minutes
48 → 0; regular customers refused 8 → 0; orders the kitchen could not make 0 (hidden) → 9 (run 2) → 0.
Swing: +5,780 AED per shift. Lost sales in the baseline that the penalties never show: about 1,300 AED.

## What was wrong (slide 2)

1. **It could not read allergy notes.** A list of eight words decided. It missed "I cannot digest milk
   products" and "the satay sauce is dangerous for me", and it flagged "allergic to cats, not to food".
   Cost 3,575 AED. Also: the AI refused every customer who mentioned an allergy at all.
2. **It crashed when busy.** Every order asked the AI a question; when the AI provider said "slow down"
   during the rush (90 orders in 90 seconds), the service fell over: 53 of 150 orders answered with an
   error, counted as rejections. Eight of them were regular or corporate customers.
3. **It promised times it could not keep.** Cooking time plus two minutes, whatever was already on the
   stoves. Every allergy order was late by exactly the two minutes the arithmetic guaranteed.

## What changed (slide 3)

- **The AI reads the note** and returns facts: which allergens and who is eating, whether this is a
  regular or corporate customer, whether the customer sounds likely to cancel. Repeated notes are
  remembered so they never cost a second call. If the AI is slow or down, a simple word list answers.
  Three AI models are tried in turn with a 6-second cut-off; the service always answers.
- **The code runs the kitchen**: counts every ingredient for every item; tracks the four stoves the way
  the kitchen does, to the minute (calibrated on a real run, it predicted every late order exactly);
  promises only what will be ready and says no when it will not; keeps room for regular customers;
  declines likely cancellations (rejecting is free, cooking for a cancellation is not).
- Also: every request is checked to come from Imdad (signature), no secret keys in the code, a clean
  start each shift, 71 automated tests, an offline replay so changes are measured before going live.

## What is left (slide 5)

- Risks: the final orders use notes we have never seen (the AI is there for that, the word list backs
  it up); free AI tiers allow tens of calls a minute (three models + cut-off); if the real kitchen runs
  slower than the practice one, the service learns it from the first late delivery.
- Next: take regular customers and allergen profiles from the CRM by customer id and treat the note as
  the fallback; use the kitchen's 30-minute stock snapshot; at ten times the volume, a paid AI tier
  (still cents) and a larger host.
- Cost: about 0.03 USD per 1,000 orders for the AI (zero on the free tier); hosting free today, 7 USD a
  month next. Effort: about 4 hours, 3 implementing and 1 documenting; five practice shifts.
- Live: kitchen-decision-service.onrender.com/kitchen · Code: github.com/YoussefAdelGad/kitchen-decision-service

## Likely questions and the answers in the notes

- *Why not let the AI decide everything, as the brief said?* The score is arithmetic on facts the code
  can hold exactly, except one: understanding free text. The AI adds information only there. A per-order
  AI design runs out of free-tier quota; the first run proved it (53 crashes).
- *The score is noisy; what did you do?* Measured every change on an offline replay of the recorded
  shift first. Traced the one noisy run (3,703 vs 3,816) to a backup AI model answering two notes
  after the main one was rate-limited; now the main model is retried first.
- *Salesforce?* Keep the customer truth in the org (regular customers, allergen profiles, orders as
  records). Keep the decision engine and note reader outside: the kitchen's webhook is unauthenticated
  and expects a 10-second answer, Apex has no memory between requests, and the native AI route costs
  orders of magnitude more per call. Details in the review document.

## Glossary: say this, not that

- "AI provider limits" not "rate limit / 429"; "the stoves" or "cooking stations" not "queue model";
  "a simple word list" not "regex / keyword fallback"; "answered with an error" not "HTTP 500";
  "checked to come from Imdad" not "HMAC signature"; "regular or corporate customers" not "key accounts";
  "the note" not "customer_note"; "promise" not "promised_minutes".

## Rules

- Exactly 5 slides. One message per slide. Titles are the message ("The service was losing money on
  every shift"), not labels ("Background").
- Numbers only where they change a decision: the two scores, the swing, the counts before/after.
- Keep the speaker notes; they are the presenter's script and hold the detail.
- Do not add slides for architecture, code, or the Salesforce answer; those live in the documents.
