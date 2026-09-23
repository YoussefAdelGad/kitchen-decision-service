---
title: Kitchen Decision Service
emoji: 🍳
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Kitchen Decision Service

Answers the Imdad Kitchen Arena webhook: for every order, accept or reject, with a
promised time and an allergy flag. The model reads the customer's note and returns
facts; the code owns stock, the four-station queue, the promise and the decision.

- `service.py` — the service (inherited lines kept as `# OLD:` comments)
- `original/` — the service as inherited, untouched
- `tests/` — 66 offline tests: `python -m pytest -q`
- `replay.py` — replays a recorded shift through the rules with a simulated kitchen
- `RESULTS.md`, `BASELINE.md`, `GRADER_RULES.md` — measurements and what they taught us

Secrets (Space settings → Variables and secrets): `MODEL_API_KEY`, `IMDAD_SIGNING_SECRET`.
