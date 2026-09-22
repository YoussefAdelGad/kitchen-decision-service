# Practice run results (same 150 practice orders each time)

| Run | Code state | Score (AED) | Revenue | Incidents | False alarms | Failed | Wasted | Late min | Key lost | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| run_92df720d2d | as inherited (+ real key) | **−1,964** | 2,536 | 6 | 23 | 0 | 7 | 48 | 8 | 53 |
| run_981ab866d4 | step 1: model reads notes, code sets flag | **−14,750** | 4,772 | 0 | 0 | 9 | 12 | 6,108 | 0 | 0 |
| run_ab23cb9b43 | step 2: stock for all items, 4-station queue, computed promise, cancel-risk rejection, run reset | **3,816** | 3,834 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |

Offline replay estimate for step 2 was 3,904 (assumes the note reader is perfect).

Step 1 is worth showing on its own: it fixed every allergy line and every crash, then lost
18,324 AED to lateness because it accepted everything with a promise that ignored the queue.
The per-order table from that run is what let us calibrate the kitchen model (4 stations,
FIFO, cook = sum of items + 3 if flagged, 1 minute pickup).

Remaining costs in step 2: two cancellations (18 AED). One note ("sorry I had to cancel the
last two orders") the model did not read as a cancel risk; one key account served with a
27-minute promise cancelled anyway.
