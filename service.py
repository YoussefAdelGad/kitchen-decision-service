"""
Kitchen decision service.

Answers the Imdad Kitchen Arena webhook.

Repair log (lines marked "# OLD:" are the inherited code, kept for the review):
  step 1 - model reads the note only; code sets the allergy flag by joining the
           note's facts with the menu allergens. Key and menu come from env/world.json.
"""

import json
import os
import threading
import time
# OLD: import re

import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify

load_dotenv()
app = Flask(__name__)

# OLD: GROQ_API_KEY = "gsk_REPLACE-ME-WITH-YOUR-OWN-KEY-000000000000"
MODEL_API_KEY = os.environ["MODEL_API_KEY"]          # fail fast at startup if missing
MODEL_URL = "https://api.groq.com/openai/v1/chat/completions"
# OLD: MODEL_NAME = "openai/gpt-oss-20b"
# Tried in order; each model has its own free-tier bucket (~30 req/min, 8,000 tokens/min).
# gpt-oss models are reasoning models: give them room (max_tokens) or they return nothing.
MODELS = (  # (model, timeout seconds, extra request params)
    ("openai/gpt-oss-20b", 4.0, {"max_tokens": 800, "reasoning_effort": "low"}),
    ("qwen/qwen3.8-27b", 3.0, {"max_tokens": 300}),
    ("openai/gpt-oss-120b", 3.0, {"max_tokens": 800, "reasoning_effort": "low"}),
)

# ---------------------------------------------------------------------------
# Kitchen data: loaded from world.json (the data pack's source of truth)
# OLD: # kitchen data - copied from world.json so we don't have to ship the file
# OLD: PRICES = {"classic_burger": 32.0, "cheesy_fries": 18.0, "chicken_wrap": 28.0,
# OLD:           "satay_skewers": 34.0, "garden_salad": 24.0, "falafel_box": 26.0}
# OLD: COOK = {"classic_burger": 6, "cheesy_fries": 4, "chicken_wrap": 5,
# OLD:         "satay_skewers": 7, "garden_salad": 2, "falafel_box": 5}
# OLD: RECIPES = {
# OLD:     "classic_burger": {"bun": 1, "patty": 1, "cheese": 1},
# OLD:     ...
# OLD: }
# OLD: stock = {"bun": 55, "patty": 55, "cheese": 70, ...}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "world.json")) as f:
    WORLD = json.load(f)
MENU = {m["item"]: m for m in WORLD["menu"]}
COOK = {item: m["cook_minutes"] for item, m in MENU.items()}
RECIPES = {item: m["ingredients"] for item, m in MENU.items()}
ITEM_ALLERGENS = {item: set(m["allergens"]) for item, m in MENU.items()}
ALL_ALLERGENS = set().union(*ITEM_ALLERGENS.values())
STATIONS = WORLD["kitchen"]["stations"]                 # 4
MAX_PROMISE = WORLD["kitchen"]["max_promise_minutes"]   # 30
SHIFT_MINUTES = WORLD["kitchen"]["shift_minutes"]       # 240
ALLERGY_EXTRA_MINUTES = 3   # Guide: allergy_risk true costs the kitchen 3 extra minutes
HEADROOM = 4   # ordinary orders may use up to 30 - 4 minutes; key accounts the full 30
BUFFER = 1     # safety minute added to every promise (early food is never penalised)
MAX_LATE_BUFFER = 5   # enhancement 2: extra promise minutes learned from ORDER_DELIVERED lateness, capped

# ---------------------------------------------------------------------------
# Kitchen state. One process, one run at a time; reset whenever the run id changes.
# The kitchen is invisible to us, but run 2 showed it is deterministic: 4 stations,
# first come first served, cook = sum of items (+3 if flagged). So we mirror it.
# OLD: stock = {"bun": 55, "patty": 55, "cheese": 70, ...}   (never reset between runs)
stock = {}
station_free = []      # minute at which each station becomes free
open_orders = {}
accepted = 0
rejected = 0
current_run = None
late_buffer = 0        # enhancement 2: grows when ORDER_DELIVERED reports lateness
STATE_LOCK = threading.Lock()   # gunicorn runs 4 threads; only one may check-and-update the kitchen at a time


def reset_state(run_id):
    global stock, station_free, accepted, rejected, current_run, late_buffer
    stock = {s["ingredient"]: s["quantity"] for s in WORLD["opening_stock"]}
    station_free = [0] * STATIONS
    open_orders.clear()
    accepted = 0
    rejected = 0
    late_buffer = 0
    current_run = run_id
    print("state reset for run", run_id)


reset_state(None)

# OLD: ALLERGY_WORDS = ["allergy", "allergic", "alergic", "alergy",
# OLD:                  "peanut", "dairy", "gluten", "sesame"]

# ---------------------------------------------------------------------------
# Note reader: the model's only job is to turn free text into facts.

MENU_BLOCK = "\n".join(
    f"- {item}: {', '.join(sorted(a)) or 'none'}" for item, a in ITEM_ALLERGENS.items())

NOTE_PROMPT = f"""You read a customer's note for a restaurant kitchen and extract facts.
You never decide whether to accept the order.

Menu items and their allergens:
{MENU_BLOCK}

allergens: list an allergen only if a person who will EAT from this order must avoid it.
- Intolerances count: "cannot digest milk", "makes me sick", "stomach cannot handle it".
- Synonyms: peanut sauce / satay sauce / groundnut = peanut. tahini / sesame sauce / seeds in
  the sauce = sesame. milk / cheese / creamy / lactose = dairy. wheat / flour / bread / coeliac = gluten.
- List nothing when the allergy is denied or past ("NOT allergic", "no X allergy here",
  "used to be allergic, fine now"), when the allergic person is not eating this order
  (neighbour, colleague not eating today), or when it is a joke (allergic to waiting, to cats).
eater_items: if the allergic person eats only specific items, list them; otherwise null.
key_account: true if the note shows a repeat or corporate customer (orders regularly,
  "since you opened", office or team lunch, "third order this week").
cancel_risk: true if the customer may not receive or may cancel ("might step out",
  "not sure I will be home", "might have already ordered", "I will cancel").

Reply with JSON only:
{{"allergens": [], "eater_items": null, "key_account": false, "cancel_risk": false}}"""

NOTE_CACHE = {}
NO_FACTS = {"allergens": [], "eater_items": None, "key_account": False,
            "cancel_risk": False, "ai_used": False}


def validate_facts(raw):
    """Keep only values we understand; anything odd becomes the safe default."""
    allergens = [a for a in raw.get("allergens") or [] if a in ALL_ALLERGENS]
    eater = raw.get("eater_items")
    if eater is not None:
        eater = [i for i in eater if i in MENU]
    return {"allergens": allergens, "eater_items": eater,
            "key_account": bool(raw.get("key_account")),
            "cancel_risk": bool(raw.get("cancel_risk")), "ai_used": False}


def fallback_read(note):
    """No model available: keyword reader, first version. Refined in a later step."""
    low = note.lower()
    if any(p in low for p in ("not allergic", "allergy here", "used to be allergic",
                              "not for him", "not eating", "cats", "waiting")):
        return NO_FACTS
    words = {"peanut": ("peanut", "groundnut", "satay sauce"),
             "sesame": ("sesame", "tahini", "seeds"),
             "dairy": ("dairy", "milk", "cheese", "creamy", "lactose"),
             "gluten": ("gluten", "wheat", "flour", "bread", "coeliac")}
    found = [a for a, ws in words.items() if any(w in low for w in ws)]
    return {**NO_FACTS, "allergens": found}


# OLD: def ask_model(order):
# OLD:     """Ask the model whether to take the order and how long to promise."""
# OLD:     prompt = (
# OLD:         "You run a busy kitchen. Decide whether to accept this order.\n"
# OLD:         "You have 4 cooking stations and the customer will not wait more than "
# OLD:         "30 minutes.\n"
# OLD:         "Reply with JSON only: {\"decision\": \"accept\" or \"reject\", "
# OLD:         "\"promised_minutes\": number}\n\n"
# OLD:         "Order: " + json.dumps(order["items"]) + "\n"
# OLD:         "Note from the customer: " + order.get("customer_note", "") + "\n"
# OLD:         "Orders currently cooking: " + str(len(open_orders)) + "\n"
# OLD:     )
# OLD:     r = requests.post(
# OLD:         MODEL_URL,
# OLD:         headers={"Authorization": "Bearer " + GROQ_API_KEY},
# OLD:         json={"model": MODEL_NAME,
# OLD:               "messages": [{"role": "user", "content": prompt}]},
# OLD:     )
# OLD:     text = r.json()["choices"][0]["message"]["content"]
# OLD:     text = text.replace("```json", "").replace("```", "")
# OLD:     return json.loads(text)
MODEL_DEADLINE_SECONDS = 6.0   # total across all model attempts; the arena allows 10 for the whole answer


def read_note(note):
    """Turn the customer note into facts. Cache by note; model, then fallback.
    Never spends more than MODEL_DEADLINE_SECONDS in total, however many models hang."""
    key = " ".join((note or "").lower().split())
    if not key:
        return NO_FACTS
    if key in NOTE_CACHE:
        return NOTE_CACHE[key]
    deadline = time.monotonic() + MODEL_DEADLINE_SECONDS
    for model, timeout, extra in MODELS:
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            print("model deadline spent; falling back")
            break
        try:
            r = requests.post(
                MODEL_URL,
                headers={"Authorization": "Bearer " + MODEL_API_KEY},
                json={"model": model, "temperature": 0, **extra,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": NOTE_PROMPT},
                                   {"role": "user", "content": note}]},
                timeout=min(timeout, remaining))
            if r.status_code != 200:
                print("model", model, "status", r.status_code)
                continue
            raw = json.loads(r.json()["choices"][0]["message"]["content"])
            facts = validate_facts(raw)
            facts["ai_used"] = True
            NOTE_CACHE[key] = facts
            return facts
        except (requests.RequestException, ValueError, KeyError) as e:
            print("model", model, "failed:", type(e).__name__)
            continue
    return fallback_read(note)


def allergy_risk(facts, items):
    """True only if a declared allergen is in an item that the allergic person eats."""
    eaten = items if facts["eater_items"] is None else [i for i in items if i in facts["eater_items"]]
    in_food = set().union(*(ITEM_ALLERGENS[i] for i in eaten)) if eaten else set()
    return bool(set(facts["allergens"]) & in_food)


def reject(order_id, reason, allergy, facts):
    """Every rejection goes through here so the response shape is always the same."""
    global rejected
    rejected += 1
    print("reject", order_id, reason)
    return jsonify({"decision": "reject", "allergy_risk": allergy,
                    "reason": reason, "ai_used": facts["ai_used"]})


@app.route("/kitchen", methods=["POST"])
def kitchen():
    """Catch-all: whatever goes wrong inside, the arena gets a valid JSON answer.
    A 500 is recorded as a rejection anyway, but with no reason and no allergy flag,
    and it looks like an outage. (The inherited code returned 500 on 53 of 150 orders.)"""
    try:
        return _kitchen()
    except Exception as e:  # noqa: BLE001 - deliberate: this is the last line of defence
        print("internal error:", type(e).__name__, e)
        body = request.get_json(silent=True) or {}
        if body.get("type") == "ORDER_PLACED":
            return jsonify({"decision": "reject", "allergy_risk": False,
                            "reason": "internal error: " + type(e).__name__, "ai_used": False})
        return jsonify({"status": "ok"})


def apply_event(ev):
    """Informational events. Called under STATE_LOCK.
    ORDER_DELIVERED carries minutes_late: if the real kitchen is slower than our model,
    widen the promise buffer for the rest of the run (enhancement 2)."""
    global late_buffer
    t = ev.get("type")
    order_id = ev.get("order_id")
    if t == "ORDER_DELIVERED":
        late = int(ev.get("minutes_late") or 0)
        if late > late_buffer:
            late_buffer = min(late, MAX_LATE_BUFFER)
            print("kitchen is running late; promise buffer now", BUFFER + late_buffer)
        open_orders.pop(order_id, None)
        print("delivered", order_id, "late", late)
    elif t in ("ORDER_CANCELLED_BY_CUSTOMER", "ORDER_FAILED"):
        open_orders.pop(order_id, None)
        print(t, order_id, ev.get("reason", ""))
    elif t == "ORDER_COOK_STARTED":
        print("cooking", order_id)
    elif t == "INVENTORY_SNAPSHOT":
        print("snapshot at minute", ev.get("minute"))


def _kitchen():
    ev = request.get_json(silent=True)
    if not isinstance(ev, dict):
        raise ValueError("body is not a JSON object")
    run_id = request.headers.get("X-Imdad-Run-Id")

    if ev.get("type") != "ORDER_PLACED":
        with STATE_LOCK:
            if run_id != current_run:
                reset_state(run_id)
            apply_event(ev)
        return jsonify({"status": "ok"})

    # Validate the order before touching any state.
    items = ev.get("items")
    order_id = ev.get("order_id")
    if not isinstance(items, list) or not items or any(i not in MENU for i in items):
        return reject(order_id, f"invalid items: {items!r}", False, NO_FACTS)
    note = ev.get("customer_note", "") or ""
    if not isinstance(note, str):
        note = str(note)
    minute = int(ev.get("minute", 0))

    # The slow part (model call) runs outside the lock so other orders are not held up.
    # OLD: # allergy check - look for anything that sounds like an allergy
    # OLD: allergy = False
    # OLD: low = note.lower()
    # OLD: for w in ALLERGY_WORDS:
    # OLD:     if re.search(w, low):
    # OLD:         allergy = True
    # OLD:         break
    facts = read_note(note)
    allergy = allergy_risk(facts, items)
    # Enhancement 1 - second opinion. If the model saw no allergen at all but the keyword
    # reader finds one that is in this order, flag it: a wrong flag costs 25, a miss costs 500.
    # When the model did name allergens (even scoped to someone not eating) we trust the model.
    if not allergy and facts["ai_used"] and not facts["allergens"]:
        if allergy_risk(fallback_read(note), items):
            allergy = True
            print("hedge: keyword reader flagged", order_id, repr(note[:60]))

    with STATE_LOCK:
        if run_id != current_run:
            reset_state(run_id)
        return decide(order_id, items, minute, facts, allergy)


def decide(order_id, items, minute, facts, allergy):
    """Rules 1-5. Must be called under STATE_LOCK: it reads and updates stock and stations."""
    global accepted

    # Rule 1 - stock, for every item in the order.
    # OLD: # do we have the ingredients?
    # OLD: have = True
    # OLD: for ing, qty in RECIPES[items[0]].items():
    # OLD:     if stock.get(ing, 0) < qty:
    # OLD:         have = False
    need = {}
    for i in items:
        for ing, qty in RECIPES[i].items():
            need[ing] = need.get(ing, 0) + qty
    if any(stock.get(ing, 0) < qty for ing, qty in need.items()):
        return reject(order_id, "out of stock", allergy, facts)

    # Rule 2 - a note that signals a likely cancellation: rejecting is free,
    # cooking then binning it costs 35% of the value plus a station. Key accounts still get served.
    if facts["cancel_risk"] and not facts["key_account"]:
        return reject(order_id, "note signals likely cancellation", allergy, facts)

    # Rule 3 - the queue. Mirror the kitchen: earliest free station, FIFO.
    # OLD: # let the model decide
    # OLD: answer = ask_model(ev)
    # OLD: if answer["decision"] == "accept":
    # OLD:     cook_time = 0
    # OLD:     for i in items:
    # OLD:         cook_time = cook_time + COOK[i]
    # OLD:     promised = cook_time + 2
    # OLD:     if promised > 30:
    # OLD:         promised = 30
    cook_time = sum(COOK[i] for i in items) + (ALLERGY_EXTRA_MINUTES if allergy else 0)
    s = min(range(STATIONS), key=lambda k: station_free[k])
    start = max(station_free[s], minute)
    ready = start + cook_time
    promised = ready - minute + BUFFER + late_buffer

    # Rule 4 - the limit. Ordinary orders keep HEADROOM minutes free for key accounts.
    limit = MAX_PROMISE if facts["key_account"] else MAX_PROMISE - HEADROOM
    if promised > limit:
        return reject(order_id, f"queue too long: ready in {promised} min", allergy, facts)
    if ready > SHIFT_MINUTES:
        return reject(order_id, "would not be ready before the shift ends", allergy, facts)

    # Rule 5 - accept: claim the station and the stock.
    station_free[s] = ready
    # OLD:     for ing, qty in RECIPES[items[0]].items():
    # OLD:         stock[ing] = stock[ing] - qty
    for ing, qty in need.items():
        stock[ing] -= qty
    open_orders[order_id] = {"minute": minute, "ready": ready, "promised": promised}
    accepted = accepted + 1

    print("accept", order_id, items, "minute", minute, "promised", promised,
          "allergy", allergy, "key", facts["key_account"])
    return jsonify({"decision": "accept",
                    "promised_minutes": max(1, min(MAX_PROMISE, int(promised))),
                    "allergy_risk": allergy,
                    # OLD: "reason": "model said accept",
                    "reason": (f"station free at {start}, ready at {ready}; "
                               f"allergens {','.join(facts['allergens']) or 'none'}"
                               + ("; key account" if facts["key_account"] else "")),
                    "ai_used": facts["ai_used"]})

    # OLD: rejected = rejected + 1
    # OLD: print("reject", ev["order_id"], items)
    # OLD: return jsonify({"decision": "reject", "allergy_risk": allergy,
    # OLD:                 "reason": "model said reject", "ai_used": True})


@app.route("/kitchen", methods=["GET"])
def health():
    try:
        return jsonify({"ok": True, "run": current_run, "accepted": accepted, "rejected": rejected,
                        "station_free": station_free, "late_buffer": late_buffer,
                        "stock": stock, "note_cache": len(NOTE_CACHE)})
    except:
        pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
