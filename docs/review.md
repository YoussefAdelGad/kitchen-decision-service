# Kitchen Decision Service — Review

Youssef Adel Gad · Tech Lead candidate · 26 September 2026
Service: https://kitchen-decision-service.onrender.com/kitchen · Code: github.com/YoussefAdelGad/kitchen-decision-service

## 1. Where we started

I deployed the inherited service unchanged, with a real model key in place of the placeholder, and ran a
practice shift. **Baseline: −1,964 AED.** Every fix was then measured on the same 150 practice orders,
one step at a time, so each number below is the value of that step, not a guess.

| Run | State of the code | Score (AED) |
|---|---|---|
| 1 | As inherited | **−1,964** |
| 2 | Model reads the note; code sets the allergy flag | −14,750 |
| 3 | + stock for every item, four-station queue, computed promise, cancel-risk rejection | 3,816 |
| 4 | + hardening, first run on the final host | 3,703 |
| 5 | + two corrections found in run 4 | **3,816** |

Run 2 looks like a disaster and was the most useful run: it fixed every allergy line, then lost 18,324 AED
to lateness, which exposed the queue problem in full and gave me the data to model the kitchen.

## 2. What was wrong, ranked by what it cost us

The baseline reconciles exactly: 2,536 revenue − 3,000 − 575 − 660 − 144 − 121 = **−1,964**.

**1. Six allergy incidents, −3,000.** The code searched the note for eight words (`allergy, allergic,
alergic, alergy, peanut, dairy, gluten, sesame`). All six missed conflicts used none of them: "I cannot
digest milk products" (twice), "no cheese at all, my stomach cannot handle it", "the satay sauce is
dangerous for me" (twice), "nothing with tahini or anything from that family". Milk and cheese are dairy,
satay sauce is peanut, tahini is sesame, and intolerance counts (the Guide's own example says so). A word
list cannot know that; a model can, and the model was sent every one of these notes but was never asked
about allergies, so its reading was discarded.

**2. Twenty-three false alarms, −575.** The same word list fired on any match, without looking at the
order. By cause: 9 jokes ("allergic to waiting :)", "allergic to cats") 225; 5 negations ("NOT allergic to
dairy, the app saved that by mistake") 125; 5 where the allergen was not in the food ("my son cannot have
sesame" on a burger and satay) 125; 3 past tense ("used to be allergic as a child") 75; 1 where the
allergic person was not eating this order 25. Each false alarm also cost the kitchen 3 minutes.

**3. Eight key accounts refused, −660, and 2,898 AED of orders rejected.** Two causes. (a) 53 orders
got HTTP 500: the model call had no timeout, no status check and no fallback, so when Groq rate-limited
us (30 requests/minute on the free tier) the code indexed the error body, raised `KeyError: 'choices'`,
and the arena recorded a rejection. All 53 fall in the minute 90–150 rush, where 90 orders arrive in 90
real seconds. (b) The model itself rejected 27 orders, and every one of the 27 had an allergy word in
its note: asked to "decide", it refused anyone who mentioned an allergy. Together they rejected 80 of
150 orders, including 8 repeat or corporate customers ("clinic reception, three times a week", "same as
every Tuesday for the office") the code could not recognise. The largest number on this page is the
revenue gap: 2,536 delivered against 3,834 the same orders yield with correct decisions, about 1,300
AED of lost sales the formula never shows as a penalty.

**4. Forty-eight late minutes, −144.** All 24 late orders were flagged orders, each exactly 2 minutes
late. The promise was cook time + 2; a flagged order takes cook time + 3 for allergy handling + 1 for the
kitchen to pick it up. The arithmetic guarantees 2 late minutes per flag. The queue did not bite in run 1
only because so little was accepted; run 2 showed the same promise at full acceptance: 6,108 late minutes.

**5. Seven orders cooked then cancelled, −121.** Five had said so in the note ("not sure I will be
home", "I might step out", "my friend might have already ordered"), one said "if it takes more than 20
minutes I will cancel". The code read none of it. Rejecting these is free; cooking them costs 35% plus a station.

**6. Stock, −0 in run 1, −1,024 as soon as it mattered.** Checked and deducted for the first item only,
never restored, never reset between runs. Run 1 accepted too little for it to show. Run 2 accepted 129
orders and lost 9 satay orders after the peanut sauce ran out, at twice their value.

**7. The model was asked the wrong question.** It got the items, the note and a count of open orders,
and was told to decide accept/reject and a promise, with no stock, queue or clock. Its own reasoning
trace reads "we don't know capacity, we need to guess". 189 of its 209 output tokens were that
guessing. The brief's "let the model work it out from the events" could not work: the code sent no events.

**8. Hygiene, no direct cost, real risk.** API key in the source. No signature verification. A bare
`except: pass` on the health endpoint. State carried from one run into the next. No tests.

## 3. What I fixed, and what it was worth

**The design decision.** The model reads the customer note and nothing else, and returns facts:
allergens declared, who eats them, key account, cancel risk. Code joins those facts with the menu and
owns every number: stock, queue, promise, clock, decision. This departs from the brief's "one model
call per order, no hand-written rules". Every cost line in the formula is arithmetic on facts the code
can hold exactly, except one: understanding free text. That is the only place the model adds
information, and the Guide itself warns that a per-order model design runs out of quota. Run 1 proved it.

| Fix | How | Worth |
|---|---|---|
| Allergy flag = declared allergens ∩ allergens of items the allergic person eats; a flag never rejects | model facts + menu join | 3,575; 0 incidents, 0 false alarms since |
| No crash, ever: 3 models in turn, 6 s total deadline, retry primary after a 429, keyword fallback, catch-all; notes cached by text, empty notes skip the model | 150 calls/shift → ~60 | 53 crashes; the model-off test |
| Kitchen model: 4 stations, FIFO, cook = items + 3 if flagged; promise = computed ready time + 1; reject over 30 min or past shift end | calibrated on run 2, exact to the minute | 6,108 late min → 0 |
| Stock for every item, reserved on accept, reset on new run id | | 9 failed → 0 |
| Key accounts get the full 30 min; ordinary orders 26 | headroom for 2× customers | 8 lost → 0 |
| Cancel-risk notes rejected (key accounts still served) | rejecting is free | 7 wasted → 2 |
| Signature check, secrets in env, threads + lock, learned lateness buffer, 71 tests, offline replay harness | | risk, not score |

**On the noisy score.** Runs 3 and 5 scored 3,816 on identical orders; run 4 scored 3,703. Host logs
showed why: the primary model was rate-limited twice and a backup read two notes differently. Nearly
all the noise came from *which model answered*, so the primary is now retried once before any backup
speaks. What remains is genuine: model variance on new phrasings, and cancellations the notes do not
predict. I measure every change with the offline harness first and a practice run second.

## 4. What I deliberately left alone

**Repair, not rewrite.** Still one Flask file, same routes, gunicorn and Dockerfile; every replaced line
kept above its replacement as an `# OLD:` comment, so a reviewer reads the diff in one sitting. I would
split it into modules at the next feature, not before. **No database, queue or async**: state is per run,
the run is single-instance by requirement, the arena waits for each answer. **Not done, listed as next
steps**: inventory-snapshot reconciliation (no sample of the event in the pack) and rate-limit-header
routing. **Dropped after consideration**: structured logging and extra prompt examples (no score line
moved), and a one-minute pickup refinement to the queue model (the buffer already covers it).

## 5. Assumptions

The scoring kitchen behaves as the practice one (4 stations, FIFO, same cook times). Intolerances count
as allergies. False alarms are charged only on accepted orders (confirmed from the run table).
Non-order events carry a `minute`; otherwise the last seen minute is used. The wake-up order carries a
different run id from the scoring shift. Cancel-risk phrasings cancel almost always (9 of 9 accepted did).
Free-tier limits stay at 30 requests and 8,000 tokens per minute per model.

## 6. If this had to live inside your Salesforce org

**Keep in Salesforce, because it belongs there:** the customer truth. Key accounts from Account and
Contact records, not inferred from "same as every Tuesday". Allergen profiles on the Contact. Menu and
recipes in Custom Metadata. Every decision and outcome written back as an Order via a Platform Event, so
operations can see and audit it. Salesforce as system of record and source of the facts the decision needs.

**Refuse to move: the decision engine and the note reader.** (1) *Inbound contract.* The kitchen posts
plain HTTP with an HMAC header and wants an answer in 10 s. Apex REST needs an OAuth session; the only
unauthenticated route is a public Site with a Guest User granted Apex access, which security review
rightly rejects, or MuleSoft in front, which costs more than the service. (2) *State.* Apex has no memory
between requests. The station clocks and stock sheet become a custom object row locked `FOR UPDATE` on
every order, 3–5 SOQL/DML per decision. Fine at 150 orders a shift; at ten times, about 4 orders a
second, `UNABLE_TO_LOCK_ROW` is the failure mode and the cure is a queue, which breaks the synchronous
contract. (3) *The model call.* Callout limits are not the problem (100 per transaction, 120 s cumulative,
and callout wait time no longer counts toward the 10 concurrent long-running requests). But a Named
Credential callout to Groq is what we have now with more parts, and the native route, Prompt Builder and
the Models API with the Einstein Trust Layer, is billed per Einstein Request on a paid add-on, orders of
magnitude above the 3 cents per 1,000 orders the current design costs. Worth it if the restaurant
already licenses Einstein and wants its audit and masking guarantees; not to read a delivery note.

**How the design changes.** The service stays outside and becomes a client of the org: it reads
key-account and allergen facts by customer id, and publishes each decision back. The arena's contract
has no customer id, so today the model's reading of the note stands in; in production the note would be
the fallback, not the source. Hosting options and costs are in the architecture note.
