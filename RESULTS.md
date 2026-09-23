# Practice run results (same 150 practice orders each time)

| Run | Code state | Score (AED) | Revenue | Incidents | False alarms | Failed | Wasted | Late min | Key lost | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| run_92df720d2d | as inherited (+ real key), laptop tunnel | **−1,964** | 2,536 | 6 | 23 | 0 | 7 | 48 | 8 | 53 |
| run_981ab866d4 | step 1: model reads notes, code sets flag | **−14,750** | 4,772 | 0 | 0 | 9 | 12 | 6,108 | 0 | 0 |
| run_ab23cb9b43 | step 2: stock for all items, 4-station queue, computed promise, cancel-risk rejection, run reset | **3,816** | 3,834 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| run_0b130b41bd | hardened, first run on Render: catch-all, deadline, threads + lock, keyword hedge, learned buffer, signature check | **3,703** | 3,764 | 0 | 1 | 0 | 4 | 0 | 0 | 0 |

Offline replay estimate for the step 2 rules is 3,904 (assumes the note reader is perfect).

**Step 1** is worth showing on its own: it fixed every allergy line and every crash, then lost
18,324 AED to lateness because it accepted everything with a promise that ignored the queue.
The per-order table from that run is what let us calibrate the kitchen model (4 stations,
FIFO, cook = sum of items + 3 if flagged, 1 minute pickup).

**Step 2** remaining costs: two cancellations (18 AED). One note ("sorry I had to cancel the
last two orders") the model did not read as a cancel risk; one key account served with a
27-minute promise cancelled anyway.

**Run 4** (first on the real host): the one false alarm was the keyword hedge firing on
"only having the salad" after the model answered with an empty allergen list plus a scoped
eater; the two extra cancellations were two notes read by the backup model after the primary
was rate-limited twice in the rush. Both fixed after this run: the hedge stands down when the
model scoped the eater, and the primary is retried once after a 429 before any backup answers.
The 113 AED gap between runs 3 and 4 on identical orders is the run-to-run noise the
assignment warns about; nearly all of it came from *which model* answered a note.
