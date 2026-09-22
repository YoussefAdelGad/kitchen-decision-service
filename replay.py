"""Offline replay: run a decision policy over a recorded shift with a simulated kitchen
and score it with the Guide's formula. The kitchen model (4 stations, FIFO, cook = sum of
items + 3 if flagged) reproduced run 2's lateness exactly, so this is a faithful estimate.

    .venv/bin/python replay.py                 # default policy parameters
    .venv/bin/python replay.py --headroom 4 --density 0
"""
import argparse
import json

W = json.load(open("world.json"))
MENU = {m["item"]: m for m in W["menu"]}
COOK = {i: m["cook_minutes"] for i, m in MENU.items()}
RECIPES = {i: m["ingredients"] for i, m in MENU.items()}
ALLERGENS = {i: set(m["allergens"]) for i, m in MENU.items()}
S = W["scoring"]
STATIONS, MAX_PROMISE, SHIFT = W["kitchen"]["stations"], W["kitchen"]["max_promise_minutes"], W["kitchen"]["shift_minutes"]


def replay(orders, headroom=4, density=0.0, density_wait=12, buffer=0, reject_cancel=True):
    stock = {s["ingredient"]: s["quantity"] for s in W["opening_stock"]}
    free = [0] * STATIONS
    score = {"revenue": 0, "incident": 0, "false_alarm": 0, "failed": 0, "waste": 0, "late": 0, "key_lost": 0}
    n = {"accept": 0, "reject": 0, "late_min": 0}
    for r in sorted(orders, key=lambda r: r["minute"]):
        items, m, value = r["items"], r["minute"], r["value_aed"]
        conflict, key, cancel = r["_conflict"], r["_key"], r["_cancel"] is not None
        flag = conflict                      # assume the note reader is right (it was 150/150)
        cook = sum(COOK[i] for i in items) + (3 if flag else 0)

        def reject(reason):
            n["reject"] += 1
            if key:
                score["key_lost"] += S["penalty_lost_key_account_x_value"] * value

        need = {}
        for i in items:
            for ing, q in RECIPES[i].items():
                need[ing] = need.get(ing, 0) + q
        if any(stock[ing] < q for ing, q in need.items()):
            reject("stock"); continue
        if reject_cancel and cancel and not key:
            reject("cancel"); continue
        s = min(range(STATIONS), key=lambda k: free[k])
        start = max(free[s], m)
        ready = start + cook
        promise = ready - m + buffer
        limit = MAX_PROMISE if key else MAX_PROMISE - headroom
        if promise > limit or ready > SHIFT:
            reject("queue"); continue
        if density and not key and (start - m) > density_wait and value / cook < density:
            reject("density"); continue

        # accept
        n["accept"] += 1
        free[s] = ready
        for ing, q in need.items():
            stock[ing] -= q
        if cancel:                            # cancel-risk notes cancelled 9/9 times when accepted
            score["waste"] += S["penalty_waste_x_value"] * value
            continue
        score["revenue"] += value
        late = max(0, ready - m - promise)
        score["late"] += S["penalty_per_minute_late_aed"] * late
        n["late_min"] += late
    total = score["revenue"] - sum(v for k, v in score.items() if k != "revenue")
    return total, score, n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headroom", type=int, default=4)
    ap.add_argument("--density", type=float, default=0.0)
    ap.add_argument("--buffer", type=int, default=0)
    ap.add_argument("--no-reject-cancel", action="store_true")
    a = ap.parse_args()
    R = json.load(open("../datapack/baseline_run_labeled.json"))
    total, score, n = replay(R, a.headroom, a.density, buffer=a.buffer, reject_cancel=not a.no_reject_cancel)
    print(f"estimated score: {total:,.0f} AED   accepted {n['accept']}  rejected {n['reject']}  late minutes {n['late_min']}")
    for k, v in score.items():
        print(f"  {k:12s} {v:8,.0f}")
