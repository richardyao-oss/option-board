# Anomaly Score Revision Note

Keep the current score formula unchanged until the next validation pass.

Hypothesis to verify:
- A signal is stronger when the dominant option direction share increases versus the prior day and total option volume also expands.
- This combined condition may be more useful than score, volume, P/C, or direction share alone.

Current quick-check result from the 2026-06-01 top-25 volume sample:
- Direction share up + total volume up: 27 samples, 74.1% hit rate, +4.35% average direction-adjusted next-day return.
- Direction share up + total volume up more than 1.5x: 17 samples, 82.4% hit rate, +5.72% average direction-adjusted next-day return.
- Samples not satisfying the combo: 20 samples, 35.0% hit rate, +0.21% average direction-adjusted return.

Next validation:
- After the 2026-06-02 close is available, refresh/complete the 2026-06-02 data and rerun the same correlation check with the pending 2026-06-01 -> 2026-06-02 rows included.
- If the combined condition still materially outperforms the baseline, revise the anomaly score to emphasize direction-share improvement plus total-volume expansion.
- Do not change the production score formula before that confirmation.
