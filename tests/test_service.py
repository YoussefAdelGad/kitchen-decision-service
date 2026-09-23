"""Tests for the kitchen decision service.

The model call is replaced by canned facts (FACTS below) so tests run offline and
deterministically. Run with:  .venv/bin/python -m pytest -q
"""
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MODEL_API_KEY", "test-key")
import service  # noqa: E402

REAL_READ_NOTE = service.read_note   # the autouse fixture replaces service.read_note; keep the real one

NONE = {"allergens": [], "eater_items": None, "key_account": False, "cancel_risk": False, "ai_used": True}
FACTS = {
    "": service.NO_FACTS,
    "peanut": {**NONE, "allergens": ["peanut"]},
    "dairy": {**NONE, "allergens": ["dairy"]},
    "sesame only salad": {**NONE, "allergens": ["sesame"], "eater_items": ["garden_salad"]},
    "sesame only wrap": {**NONE, "allergens": ["sesame"], "eater_items": ["chicken_wrap"]},
    "sesame nobody": {**NONE, "allergens": ["sesame"], "eater_items": []},
    "key": {**NONE, "key_account": True},
    "cancel": {**NONE, "cancel_risk": True},
    "key cancel": {**NONE, "key_account": True, "cancel_risk": True},
    "boom": None,  # makes the fake reader raise
}


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    def fake_read_note(note):
        if note == "boom":
            raise RuntimeError("model exploded")
        return FACTS.get(note, NONE)
    monkeypatch.setattr(service, "read_note", fake_read_note)
    monkeypatch.setattr(service, "SIGNATURE_MODE", "off")   # signature tests switch it on themselves
    service.reset_state(None)
    yield


@pytest.fixture
def client():
    return service.app.test_client()


def order(client, items, minute=1, note="", run="t", order_id="O", value=30.0, raw=None):
    body = raw if raw is not None else {"type": "ORDER_PLACED", "order_id": order_id, "minute": minute,
                                        "items": items, "value_aed": value, "customer_note": note}
    r = client.post("/kitchen", json=body, headers={"X-Imdad-Run-Id": run})
    assert r.status_code == 200
    return r.get_json()


# ---------------------------------------------------------------- allergy flag (the 500 AED line)

def test_flag_when_allergen_is_in_the_order(client):
    assert order(client, ["satay_skewers"], note="peanut")["allergy_risk"] is True


def test_no_flag_when_allergen_is_not_in_the_order(client):
    # dairy declared, satay + salad contain no dairy -> flagging would be a 25 AED false alarm
    assert order(client, ["satay_skewers", "garden_salad"], note="dairy")["allergy_risk"] is False


def test_flag_checks_every_item_not_just_the_first(client):
    assert order(client, ["garden_salad", "cheesy_fries"], note="dairy")["allergy_risk"] is True


def test_no_flag_when_allergic_person_eats_only_an_item_not_in_the_order(client):
    # "my wife is allergic to sesame but she is only having the salad" on a chicken wrap
    assert order(client, ["chicken_wrap"], note="sesame only salad")["allergy_risk"] is False


def test_flag_when_allergic_person_eats_an_item_that_is_in_the_order(client):
    # salad is in the order but has no sesame -> no flag
    assert order(client, ["chicken_wrap", "garden_salad"], note="sesame only salad")["allergy_risk"] is False
    # the allergic person eats the wrap, which has sesame -> flag
    assert order(client, ["chicken_wrap", "garden_salad"], note="sesame only wrap")["allergy_risk"] is True


def test_no_flag_when_nobody_in_the_order_is_allergic(client):
    assert order(client, ["falafel_box"], note="sesame nobody")["allergy_risk"] is False


def test_allergy_never_causes_rejection(client):
    r = order(client, ["satay_skewers"], note="peanut")
    assert r["decision"] == "accept" and r["allergy_risk"] is True


def test_flag_adds_three_minutes_to_the_promise(client):
    plain = order(client, ["satay_skewers"], run="a")["promised_minutes"]
    flagged = order(client, ["satay_skewers"], note="peanut", run="b")["promised_minutes"]
    assert flagged == plain + service.ALLERGY_EXTRA_MINUTES


# ---------------------------------------------------------------- stock (the 2x value line)

def test_stock_is_deducted_for_every_item(client):
    order(client, ["classic_burger", "cheesy_fries"])
    assert service.stock["cheese"] == 70 - 2      # burger 1 + fries 1
    assert service.stock["potato"] == 80 - 2


def test_reject_when_any_ingredient_is_short_even_if_first_item_is_fine(client):
    service.reset_state("t")                        # same run id as the request, so no reset wipes this
    service.stock["potato"] = 1                     # fries need 2
    r = order(client, ["classic_burger", "cheesy_fries"])
    assert r["decision"] == "reject" and "stock" in r["reason"]
    assert service.stock["bun"] == 55               # nothing deducted on reject


def test_peanut_sauce_runs_out_after_thirty_satay_orders(client):
    # opening peanut_sauce = 30; spread arrivals so the queue never rejects
    results = [order(client, ["satay_skewers"], minute=i * 8, order_id=f"S{i}")["decision"] for i in range(31)]
    assert results[:30] == ["accept"] * 30
    assert results[30] == "reject"


def test_same_item_twice_needs_double_ingredients(client):
    service.reset_state("t")
    service.stock["peanut_sauce"] = 1
    assert order(client, ["satay_skewers", "satay_skewers"])["decision"] == "reject"


# ---------------------------------------------------------------- queue and promise (the 3 AED/min line)

def test_promise_is_cook_time_plus_buffer_on_an_empty_kitchen(client):
    r = order(client, ["classic_burger", "cheesy_fries"], minute=10)
    assert r["promised_minutes"] == 6 + 4 + service.BUFFER


def test_four_orders_take_four_stations_and_the_fifth_waits(client):
    for i in range(4):
        assert order(client, ["satay_skewers"], minute=0, order_id=f"O{i}")["promised_minutes"] == 7 + service.BUFFER
    fifth = order(client, ["garden_salad"], minute=0, order_id="O4")
    assert fifth["promised_minutes"] == 7 + 2 + service.BUFFER   # waits for the first free station


def test_station_frees_when_its_order_is_done(client):
    for i in range(4):
        order(client, ["satay_skewers"], minute=0, order_id=f"O{i}")     # all free at 7
    r = order(client, ["garden_salad"], minute=7, order_id="O4")
    assert r["promised_minutes"] == 2 + service.BUFFER


def test_ordinary_order_rejected_above_the_headroom_limit(client):
    for i in range(4):
        order(client, ["satay_skewers", "satay_skewers"], minute=0, order_id=f"O{i}")   # stations busy till 14
    for i in range(4):   # key accounts may promise 29, so they can fill the stations to 28
        order(client, ["satay_skewers", "satay_skewers"], minute=0, order_id=f"P{i}", note="key")
    # next satay would start at 28, ready 35 -> promise 36 -> reject
    r = order(client, ["satay_skewers"], minute=0, order_id="Q")
    assert r["decision"] == "reject" and "queue" in r["reason"]


def test_key_account_may_use_the_full_thirty_minutes(client):
    for i in range(4):
        order(client, ["satay_skewers", "satay_skewers"], minute=0, order_id=f"O{i}")   # busy till 14
    for i in range(4):
        order(client, ["classic_burger", "cheesy_fries"], minute=0, order_id=f"P{i}")   # busy till 24
    # salad: start 24, ready 26, promise 27 -> over 26 for ordinary, fine for key
    assert order(client, ["garden_salad"], minute=0, order_id="A")["decision"] == "reject"
    r = order(client, ["garden_salad"], minute=0, order_id="B", note="key")
    assert r["decision"] == "accept" and r["promised_minutes"] == 27


def test_promise_never_exceeds_thirty_or_drops_below_one(client):
    for i in range(4):
        order(client, ["satay_skewers", "satay_skewers"], minute=0, order_id=f"O{i}")
    for i in range(4):
        order(client, ["satay_skewers", "satay_skewers"], minute=0, order_id=f"P{i}", note="key")   # busy till 28
    r = order(client, ["garden_salad"], minute=0, order_id="K", note="key")            # ready 30, promise 31
    assert r["decision"] == "reject"                                                     # even key: > 30
    r = order(client, ["garden_salad"], minute=1, order_id="K2", note="key")           # ready 30, promise 30
    assert r["decision"] == "accept" and r["promised_minutes"] == 30


def test_reject_when_food_would_be_ready_after_the_shift_ends(client):
    r = order(client, ["classic_burger"], minute=236)    # ready 242 > 240
    assert r["decision"] == "reject" and "shift" in r["reason"]
    assert order(client, ["garden_salad"], minute=236)["decision"] == "accept"   # ready 238


# ---------------------------------------------------------------- cancellation and key accounts

def test_cancel_risk_note_is_rejected(client):
    r = order(client, ["cheesy_fries"], note="cancel")
    assert r["decision"] == "reject" and "cancel" in r["reason"]


def test_key_account_with_cancel_risk_is_still_served(client):
    assert order(client, ["cheesy_fries"], note="key cancel")["decision"] == "accept"


# ---------------------------------------------------------------- run reset

def test_new_run_id_resets_stock_and_stations(client):
    order(client, ["satay_skewers"], run="one")
    assert service.stock["peanut_sauce"] == 29 and service.station_free != [0, 0, 0, 0]
    order(client, ["garden_salad"], run="two")
    assert service.stock["peanut_sauce"] == 30 and service.current_run == "two"


def test_same_run_id_keeps_state(client):
    order(client, ["satay_skewers"], run="one", order_id="A")
    order(client, ["satay_skewers"], run="one", order_id="B")
    assert service.stock["peanut_sauce"] == 28


# ---------------------------------------------------------------- other events

@pytest.mark.parametrize("etype", ["ORDER_COOK_STARTED", "ORDER_DELIVERED", "ORDER_CANCELLED_BY_CUSTOMER",
                                   "ORDER_FAILED", "INVENTORY_SNAPSHOT", "SOMETHING_NEW"])
def test_informational_events_get_200_and_change_no_decision_state(client, etype):
    r = client.post("/kitchen", json={"type": etype, "order_id": "X", "minute": 5, "minutes_late": 3},
                    headers={"X-Imdad-Run-Id": "t"})
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}
    assert service.stock["bun"] == 55


# ---------------------------------------------------------------- harsh inputs: never a 500, always a decision

def test_model_exception_becomes_a_reject_not_a_500(client):
    r = order(client, ["garden_salad"], note="boom")
    assert r["decision"] == "reject" and "internal error" in r["reason"] and r["ai_used"] is False


def test_unknown_item_is_rejected_with_a_reason(client):
    r = order(client, ["lobster"])
    assert r["decision"] == "reject" and "invalid items" in r["reason"]


@pytest.mark.parametrize("items", [[], None, "classic_burger", 42])
def test_bad_items_field_is_rejected(client, items):
    r = order(client, items)
    assert r["decision"] == "reject"


def test_body_that_is_not_json_gets_200_ok(client):
    r = client.post("/kitchen", data="this is not json", content_type="text/plain")
    assert r.status_code == 200


def test_order_with_missing_fields_still_gets_a_decision(client):
    r = order(client, None, raw={"type": "ORDER_PLACED", "items": ["garden_salad"]})
    assert r["decision"] == "accept" and r["promised_minutes"] >= 1


def test_note_that_is_not_a_string_does_not_crash(client):
    assert order(client, ["garden_salad"], note=12345)["decision"] == "accept"


def test_very_long_note_is_fine(client):
    assert order(client, ["garden_salad"], note="x" * 20000)["decision"] == "accept"


def test_missing_run_id_header_still_works(client):
    r = service.app.test_client().post("/kitchen", json={"type": "ORDER_PLACED", "order_id": "O", "minute": 1,
                                                          "items": ["garden_salad"], "value_aed": 24})
    assert r.get_json()["decision"] == "accept"


def test_response_always_has_the_contract_fields(client):
    for items, note in ((["garden_salad"], ""), (["lobster"], ""), (["cheesy_fries"], "cancel"), (["garden_salad"], "boom")):
        r = order(client, items, note=note)
        assert set(r) >= {"decision", "allergy_risk", "reason", "ai_used"}
        assert r["decision"] in ("accept", "reject")
        if r["decision"] == "accept":
            assert isinstance(r["promised_minutes"], int) and 1 <= r["promised_minutes"] <= 30


def test_burst_of_150_orders_in_the_same_minute_never_fails(client):
    decisions = [order(client, ["classic_burger", "cheesy_fries"], minute=100, order_id=f"B{i}")["decision"] for i in range(150)]
    assert decisions.count("accept") == 8            # 4 stations x 2 orders of 10 min fit under 26
    assert decisions.count("reject") == 142
    assert min(service.stock.values()) >= 0


def test_concurrent_requests_do_not_corrupt_stock_or_stations(client):
    """Harsh: 40 threads hit the handler at once. Stock and stations must stay consistent."""
    errors = []
    def hit(i):
        try:
            c = service.app.test_client()
            c.post("/kitchen", json={"type": "ORDER_PLACED", "order_id": f"C{i}", "minute": 0,
                                     "items": ["satay_skewers"], "value_aed": 34}, headers={"X-Imdad-Run-Id": "t"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=hit, args=(i,)) for i in range(40)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    used = 30 - service.stock["peanut_sauce"]
    assert used == service.accepted, f"stock says {used} accepted, counter says {service.accepted}"
    assert service.accepted <= 12   # 4 stations x 3 satay (7 min) = 21 min < 26; a 4th on a station would be 28


# ---------------------------------------------------------------- fallback reader (model down)

@pytest.mark.parametrize("note,items,expected", [
    ("the satay sauce is dangerous for me", ["satay_skewers"], True),
    ("severe peanut allergy - please double check", ["satay_skewers"], True),
    ("no peanut allergy here, I just do not like coriander", ["satay_skewers"], False),
    ("I used to be allergic to peanut as a child, I am completely fine now", ["satay_skewers"], False),
    ("allergic to cats, not to food", ["satay_skewers"], False),
    ("my neighbour has a peanut allergy, this order is not for him", ["satay_skewers"], False),
    ("I cannot digest milk products", ["cheesy_fries"], True),
    ("I cannot digest milk products", ["satay_skewers"], False),
    ("nothing with tahini please", ["falafel_box"], True),
    ("no bread or wheat products for me please, coeliac", ["chicken_wrap"], True),
])
def test_fallback_reader_flags_only_real_conflicts(note, items, expected):
    facts = service.fallback_read(note)
    assert service.allergy_risk(facts, items) is expected
    assert facts["ai_used"] is False


def test_validate_facts_drops_garbage():
    f = service.validate_facts({"allergens": ["peanut", "shellfish", 3], "eater_items": ["garden_salad", "pizza"],
                                "key_account": "yes", "cancel_risk": None})
    assert f == {"allergens": ["peanut"], "eater_items": ["garden_salad"], "key_account": True,
                 "cancel_risk": False, "ai_used": False}


# ---------------------------------------------------------------- hardening: deadline, hedge, learned buffer

def test_model_attempts_never_exceed_the_total_deadline(monkeypatch):
    """Harsh: every model hangs. read_note must give up within MODEL_DEADLINE_SECONDS and fall back."""
    import time as _t
    import requests as _rq
    calls = []
    def hanging_post(url, headers=None, json=None, timeout=None):
        calls.append(timeout)
        _t.sleep(timeout)
        raise _rq.exceptions.Timeout("hung")
    monkeypatch.setattr(service.requests, "post", hanging_post)
    monkeypatch.setattr(service, "MODEL_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(service, "MODELS", (("a", 0.6, {}), ("b", 0.6, {}), ("c", 0.6, {})))
    t0 = _t.monotonic()
    facts = REAL_READ_NOTE("the satay sauce is dangerous for me")
    elapsed = _t.monotonic() - t0
    assert elapsed < 1.3, elapsed
    assert facts["ai_used"] is False and facts["allergens"] == ["peanut"]   # fallback answered
    assert sum(calls) <= 1.0 + 1e-6                                          # timeouts were trimmed to the deadline


def test_hedge_flags_when_model_sees_nothing_but_keywords_match_the_order(client, monkeypatch):
    monkeypatch.setattr(service, "read_note", lambda note: NONE)   # model answered: no allergens
    r = order(client, ["satay_skewers"], note="severe peanut allergy - please double check")
    assert r["allergy_risk"] is True and r["decision"] == "accept"


def test_hedge_does_not_fire_when_model_scoped_the_allergen_to_someone_not_eating(client, monkeypatch):
    monkeypatch.setattr(service, "read_note", lambda note: {**NONE, "allergens": ["sesame"], "eater_items": ["garden_salad"]})
    r = order(client, ["chicken_wrap"], note="my wife is allergic to sesame but she is only having the salad")
    assert r["allergy_risk"] is False


def test_hedge_does_not_fire_on_negations(client, monkeypatch):
    monkeypatch.setattr(service, "read_note", lambda note: NONE)
    assert order(client, ["satay_skewers"], note="no peanut allergy here, I just do not like coriander")["allergy_risk"] is False


def test_late_delivery_widens_the_promise_for_the_rest_of_the_run(client):
    base = order(client, ["garden_salad"], minute=0, order_id="A")["promised_minutes"]
    client.post("/kitchen", json={"type": "ORDER_DELIVERED", "order_id": "A", "minute": 6, "minutes_late": 3},
                headers={"X-Imdad-Run-Id": "t"})
    assert order(client, ["garden_salad"], minute=10, order_id="B")["promised_minutes"] == base + 3
    client.post("/kitchen", json={"type": "ORDER_DELIVERED", "order_id": "B", "minute": 20, "minutes_late": 40},
                headers={"X-Imdad-Run-Id": "t"})
    assert order(client, ["garden_salad"], minute=30, order_id="C")["promised_minutes"] == base + service.MAX_LATE_BUFFER
    order(client, ["garden_salad"], minute=0, order_id="D", run="fresh")
    assert service.late_buffer == 0                                          # reset with the run


def test_delivered_without_lateness_changes_nothing(client):
    base = order(client, ["garden_salad"], minute=0, order_id="A")["promised_minutes"]
    client.post("/kitchen", json={"type": "ORDER_DELIVERED", "order_id": "A", "minute": 6, "minutes_late": 0},
                headers={"X-Imdad-Run-Id": "t"})
    assert order(client, ["garden_salad"], minute=10, order_id="B")["promised_minutes"] == base


# ---------------------------------------------------------------- webhook signature

def _signed_headers(body_bytes, secret, ts="1789459200"):
    import hashlib as _h, hmac as _m
    mac = _m.new(secret.encode(), f"{ts}.".encode() + body_bytes, _h.sha256).hexdigest()
    return {"X-Imdad-Run-Id": "t", "X-Imdad-Timestamp": ts, "X-Imdad-Signature": "sha256=" + mac}


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setattr(service, "IMDAD_SIGNING_SECRET", "whsec_test")
    monkeypatch.setattr(service, "SIGNATURE_MODE", "enforce")
    monkeypatch.setattr(service, "signature_failures", 0)
    return "whsec_test"


def test_valid_signature_is_accepted(client, signed):
    body = json.dumps({"type": "ORDER_PLACED", "order_id": "O", "minute": 1, "items": ["garden_salad"], "value_aed": 24}).encode()
    r = client.post("/kitchen", data=body, content_type="application/json", headers=_signed_headers(body, signed))
    assert r.status_code == 200 and r.get_json()["decision"] == "accept"


def test_wrong_secret_is_refused_and_touches_no_state(client, signed):
    body = json.dumps({"type": "ORDER_PLACED", "order_id": "O", "minute": 1, "items": ["satay_skewers"], "value_aed": 34}).encode()
    r = client.post("/kitchen", data=body, content_type="application/json", headers=_signed_headers(body, "wrong"))
    assert r.status_code == 401
    assert service.stock["peanut_sauce"] == 30 and service.signature_failures == 1


def test_missing_signature_is_refused(client, signed):
    r = client.post("/kitchen", json={"type": "ORDER_PLACED", "order_id": "O", "items": ["garden_salad"]}, headers={"X-Imdad-Run-Id": "t"})
    assert r.status_code == 401


def test_tampered_body_is_refused(client, signed):
    body = json.dumps({"type": "ORDER_PLACED", "order_id": "O", "minute": 1, "items": ["garden_salad"], "value_aed": 24}).encode()
    headers = _signed_headers(body, signed)
    tampered = body.replace(b"garden_salad", b"satay_skewers")
    assert client.post("/kitchen", data=tampered, content_type="application/json", headers=headers).status_code == 401


def test_log_mode_counts_but_still_answers(client, signed, monkeypatch):
    monkeypatch.setattr(service, "SIGNATURE_MODE", "log")
    r = client.post("/kitchen", json={"type": "ORDER_PLACED", "order_id": "O", "minute": 1, "items": ["garden_salad"], "value_aed": 24}, headers={"X-Imdad-Run-Id": "t"})
    assert r.status_code == 200 and r.get_json()["decision"] == "accept" and service.signature_failures == 1


def test_guide_reference_vector_matches_our_check(signed):
    """The Guide's own reference implementation, run against ours on the same inputs."""
    import hashlib as _h, hmac as _m
    def guide_valid(secret, timestamp, body_bytes, header):
        mac = _m.new(secret.encode(), f"{timestamp}.".encode() + body_bytes, _h.sha256).hexdigest()
        return _m.compare_digest("sha256=" + mac, header)
    body = b'{"event_id": "EVT-0143", "type": "ORDER_PLACED"}'
    h = _signed_headers(body, signed)
    assert guide_valid(signed, h["X-Imdad-Timestamp"], body, h["X-Imdad-Signature"])
    with service.app.test_request_context("/kitchen", method="POST", data=body, headers=h):
        assert service.signature_valid(service.request)
