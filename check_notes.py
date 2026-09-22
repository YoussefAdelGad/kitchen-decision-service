"""Offline check: run the note reader over the labelled baseline orders and compare
the allergy flag / key account / cancel risk with the grader's labels.

    .venv/bin/python check_notes.py            # live model, paced under 30 rpm
    .venv/bin/python check_notes.py fallback   # keyword fallback only
"""
import json
import sys
import time

import service

R = json.load(open("../datapack/baseline_run_labeled.json"))
use_fallback = len(sys.argv) > 1 and sys.argv[1] == "fallback"

t0 = time.time()
seen = {}
rows = []
for r in R:
    note = r["customer_note"]
    key = " ".join(note.lower().split())
    if key not in seen:
        if use_fallback:
            seen[key] = service.fallback_read(note) if key else service.NO_FACTS
        else:
            seen[key] = service.read_note(note)
            if key and seen[key]["ai_used"]:
                time.sleep(2.1)  # stay under 30 requests/minute
    facts = seen[key]
    flag = service.allergy_risk(facts, r["items"])
    rows.append((r, facts, flag))

def report(name, pred, truth):
    tp = sum(1 for p, t in zip(pred, truth) if p and t)
    fp = sum(1 for p, t in zip(pred, truth) if p and not t)
    fn = sum(1 for p, t in zip(pred, truth) if not p and t)
    print(f"{name:12s} correct {len(pred)-fp-fn:3d}/{len(pred)}  missed {fn:2d}  false {fp:2d}")
    return fp, fn

print(f"mode: {'fallback' if use_fallback else 'model'}; distinct notes {len(seen)}; "
      f"ai answers {sum(1 for f in seen.values() if f['ai_used'])}; {time.time()-t0:.0f}s")
fp, fn = report("allergy", [x[2] for x in rows], [x[0]["_conflict"] for x in rows])
report("key_account", [x[1]["key_account"] for x in rows], [x[0]["_key"] for x in rows])
report("cancel_risk", [x[1]["cancel_risk"] for x in rows], [x[0]["_cancel"] is not None for x in rows])
print(f"\nallergy cost if all accepted: {fn} incidents x 500 + {fp} false alarms x 25 = {fn*500 + fp*25} AED\n")
for r, facts, flag in rows:
    if flag != r["_conflict"] or facts["key_account"] != r["_key"] or facts["cancel_risk"] != (r["_cancel"] is not None):
        print(f"  {r['order_id']} {r['items']} | {r['customer_note']!r}\n"
              f"      got allergy={flag} key={facts['key_account']} cancel={facts['cancel_risk']} facts={facts['allergens']}/{facts['eater_items']}"
              f" | truth allergy={r['_conflict']} key={r['_key']} cancel={r['_cancel'] is not None}")
