"""
Kitchen decision service.

Answers the Imdad Kitchen Arena webhook. Built AI-native as agreed - the model
makes the call on each order, we just pass it what we know.

TODO: tests
TODO: move the key out before this goes anywhere real
"""

import json
import re

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

GROQ_API_KEY = "gsk_REPLACE-ME-WITH-YOUR-OWN-KEY-000000000000"
MODEL_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "openai/gpt-oss-20b"

# kitchen data - copied from world.json so we don't have to ship the file
PRICES = {"classic_burger": 32.0, "cheesy_fries": 18.0, "chicken_wrap": 28.0,
          "satay_skewers": 34.0, "garden_salad": 24.0, "falafel_box": 26.0}

COOK = {"classic_burger": 6, "cheesy_fries": 4, "chicken_wrap": 5,
        "satay_skewers": 7, "garden_salad": 2, "falafel_box": 5}

RECIPES = {
    "classic_burger": {"bun": 1, "patty": 1, "cheese": 1},
    "cheesy_fries": {"potato": 2, "cheese": 1},
    "chicken_wrap": {"tortilla": 1, "chicken": 1, "sesame_sauce": 1},
    "satay_skewers": {"chicken": 1, "peanut_sauce": 1},
    "garden_salad": {"greens": 1, "tomato": 1},
    "falafel_box": {"falafel": 3, "tahini": 1, "flatbread": 1},
}

stock = {"bun": 55, "patty": 55, "cheese": 70, "potato": 80, "tortilla": 45,
         "chicken": 70, "sesame_sauce": 45, "peanut_sauce": 30, "greens": 50,
         "tomato": 50, "falafel": 120, "tahini": 40, "flatbread": 40}

open_orders = {}
accepted = 0
rejected = 0

ALLERGY_WORDS = ["allergy", "allergic", "alergic", "alergy",
                 "peanut", "dairy", "gluten", "sesame"]


def ask_model(order):
    """Ask the model whether to take the order and how long to promise."""
    prompt = (
        "You run a busy kitchen. Decide whether to accept this order.\n"
        "You have 4 cooking stations and the customer will not wait more than "
        "30 minutes.\n"
        "Reply with JSON only: {\"decision\": \"accept\" or \"reject\", "
        "\"promised_minutes\": number}\n\n"
        "Order: " + json.dumps(order["items"]) + "\n"
        "Note from the customer: " + order.get("customer_note", "") + "\n"
        "Orders currently cooking: " + str(len(open_orders)) + "\n"
    )
    r = requests.post(
        MODEL_URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        json={"model": MODEL_NAME,
              "messages": [{"role": "user", "content": prompt}]},
    )
    text = r.json()["choices"][0]["message"]["content"]
    text = text.replace("```json", "").replace("```", "")
    return json.loads(text)


@app.route("/kitchen", methods=["POST"])
def kitchen():
    global accepted, rejected

    ev = request.get_json()
    t = ev.get("type")

    if t != "ORDER_PLACED":
        # informational events - keep the queue roughly in step
        if t == "ORDER_DELIVERED":
            if ev.get("order_id") in open_orders:
                del open_orders[ev["order_id"]]
            print("delivered", ev.get("order_id"), "late", ev.get("minutes_late"))
        elif t == "ORDER_CANCELLED_BY_CUSTOMER":
            if ev.get("order_id") in open_orders:
                del open_orders[ev["order_id"]]
            print("cancelled", ev.get("order_id"))
        elif t == "ORDER_FAILED":
            if ev.get("order_id") in open_orders:
                del open_orders[ev["order_id"]]
            print("FAILED", ev.get("order_id"), ev.get("reason"))
        elif t == "ORDER_COOK_STARTED":
            print("cooking", ev.get("order_id"))
        elif t == "INVENTORY_SNAPSHOT":
            print("snapshot at minute", ev.get("minute"))
        return jsonify({"status": "ok"})

    items = ev["items"]
    note = ev.get("customer_note", "") or ""

    # allergy check - look for anything that sounds like an allergy
    allergy = False
    low = note.lower()
    for w in ALLERGY_WORDS:
        if re.search(w, low):
            allergy = True
            break

    # do we have the ingredients?
    have = True
    for ing, qty in RECIPES[items[0]].items():
        if stock.get(ing, 0) < qty:
            have = False

    if not have:
        rejected = rejected + 1
        print("no stock for", items, "->", "reject")
        return jsonify({"decision": "reject", "allergy_risk": allergy,
                        "reason": "out of stock", "ai_used": False})

    # let the model decide
    answer = ask_model(ev)

    if answer["decision"] == "accept":
        for ing, qty in RECIPES[items[0]].items():
            stock[ing] = stock[ing] - qty

        cook_time = 0
        for i in items:
            cook_time = cook_time + COOK[i]

        promised = cook_time + 2
        if promised > 30:
            promised = 30

        open_orders[ev["order_id"]] = cook_time
        accepted = accepted + 1

        print("accept", ev["order_id"], items, "promised", promised,
              "allergy", allergy)
        return jsonify({"decision": "accept",
                        "promised_minutes": promised,
                        "allergy_risk": allergy,
                        "reason": "model said accept",
                        "ai_used": True})

    rejected = rejected + 1
    print("reject", ev["order_id"], items)
    return jsonify({"decision": "reject", "allergy_risk": allergy,
                    "reason": "model said reject", "ai_used": True})


@app.route("/kitchen", methods=["GET"])
def health():
    try:
        return jsonify({"ok": True, "accepted": accepted, "rejected": rejected,
                        "stock": stock})
    except:
        pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
