# What the grader actually does — derived from the baseline run table

Source: the per-order table of practice run `run_92df720d2d` (150 orders), hand-labelled in
`datapack/label_notes.py`. The labels below reproduce the dashboard exactly:
22 conflicts, 6 incidents, 23 false alarms, 8 key accounts rejected. So these are the rules.

## Allergy conflict = declared allergen ∩ allergens of the items in THIS order, for a person eating it

| Note pattern | Counts as | Evidence |
|---|---|---|
| "cannot digest milk", "stomach cannot handle" cheese, "makes me very sick" | conflict (intolerance = allergy) | ORD-0026/0033/0042 were incidents |
| "the satay sauce is dangerous for me" | peanut conflict | ORD-0071/0132 incidents |
| "nothing with tahini or anything from that family" | sesame conflict | ORD-0129 incident |
| "groundnut family", "no seeds in the sauce", "coeliac", "anything with flour" | peanut / sesame / gluten / gluten | consistent with totals |
| "ordering for a colleague who cannot have X" | conflict if X in order | ORD-0045 flagged sesame on burgers = false alarm, i.e. only the item join matters |
| "my son cannot have X", "daughter reacted badly to X" | conflict if X in order | ORD-0144 dairy on salad = false alarm |
| "my wife is allergic to X but she is only having the salad" | **no conflict** | ORD-0025 flagged = false alarm |
| "my neighbour has a X allergy, this order is not for him" | no conflict | totals |
| "my colleague asked me to check for dairy, he is not eating today" | no conflict | totals |
| "NOT allergic to X, the app saved that by mistake" | no conflict | ORD-0013/0118/0148 false alarms |
| "no X allergy here, I just do not like coriander" | no conflict | ORD-0027/0127 false alarms |
| "I used to be allergic to X as a child, completely fine now" | no conflict | ORD-0030/0058/0143 false alarms |
| "allergic to cats", "allergic to waiting :)" | no conflict | 10 false alarms |
| Misspellings ("alergic"), Arabic ("بدون dairy") | normal | ORD-0074 sesame on burger = false alarm because burger has no sesame |

False alarms are only charged on **accepted** orders (23 accepted-and-flagged, 29 flagged in total).

## Lateness came entirely from the allergy flag

All 24 late orders were flagged orders, each exactly 2 minutes late; no unflagged order was late.
The flag adds 3 kitchen minutes and the promise (cook + 2) ignored it. The queue never bit in this
run because so little was accepted.

## Cancellation notes cancel almost every time

Wasted (cooked then cancelled) 7 orders. Of the accepted orders carrying a cancel-risk note,
5 of 5 were cancelled ("I might step out", "my friend might have already ordered",
"not sure I will be home"), plus "if it takes more than 20 minutes I will cancel" was cancelled
even with a 12-minute promise. One cancellation (ORD-0058) had no signal at all: background noise.
Cost is 0.35 × value **and** the stock and station time. Rejecting these is free.

## Key accounts are identified only from the note

15 key-account orders in the stream (10%). Templates seen:
"this is for the clinic reception, we order from you three times a week",
"we have been ordering from you since you opened", "third order this week, you know what we like",
"our regular team lunch, please keep it consistent", "same as every Tuesday for the office, 12 of us".
Rejecting one costs 2 × value, so they must be prioritised when capacity is short.

## Capacity is the binding constraint in the rush, not stock

- 150 orders = 1,047 cook-minutes against 960 station-minutes (4 stations × 240).
- Arrivals per 30-minute window: 13, 9, 9, **44, 46**, 7, 13, 9. The two rush windows carry
  618 cook-minutes against 240 available. A large share of rush orders must be rejected, and the
  service has to choose which: key accounts and value per cook-minute.
- Stock if everything were accepted: cheese 74/70, peanut sauce 39/30, chicken 69/70. Stock
  matters late in the shift; capacity matters at minute 90–150.

## Note repetition

61 distinct notes across 150 orders; 30 empty. A cache keyed on the normalised note text removes
about 60% of model calls, which keeps a 30 request/minute free tier viable.
