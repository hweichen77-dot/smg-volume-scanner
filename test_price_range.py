import numpy as np
import pandas as pd

import price_range as pr

rng = np.random.default_rng(0)
n = 6000
vol = rng.uniform(0.01, 0.1, n)
flagged = pd.Timestamp("2024-01-01") + pd.to_timedelta(np.arange(n) // 10, unit="D")
events = pd.DataFrame({"symbol": "X", "flagged": flagged, "vol": vol, "move1": rng.standard_t(4, n) * vol, "resolved1": flagged + pd.Timedelta(days=1)})
scored, finals = pr.walk_forward_all(events, 1)
hit = (scored["score"] <= scored["multiplier"]).mean()
assert abs(hit - pr.ACI_TARGET) < 0.02, hit
assert set(finals) == {0, 1, 2}
low, high = pr.ranges(10.0, 0.02, {1: finals})[1]
assert low < 10 < high and np.isclose(low * high, 100), (low, high)
print("ok", round(hit, 3))
