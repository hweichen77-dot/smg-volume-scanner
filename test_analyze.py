import numpy as np
import pandas as pd

import analyze as an

rng = np.random.default_rng(1)
n = 400
close = 20 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
bars = pd.DataFrame({"close": close, "high": close * 1.01, "low": close * 0.99, "volume": rng.integers(100_000, 500_000, n).astype(float)})
full = an.features(bars)[an.FEATURES]
cut = an.features(bars.iloc[:300])[an.FEATURES]
assert np.allclose(full.iloc[:300].dropna(), cut.dropna()), "features use future bars"

rows = pd.DataFrame(rng.normal(0, 1, (20_000, len(an.FEATURES))), columns=an.FEATURES)
rows["target"] = rows["r5"] + rng.normal(0, 2, len(rows))
mu, sd = rows[an.FEATURES].mean(), rows[an.FEATURES].std()
w = an.fit_logistic(an.design(rows, mu, sd), (rows["target"] > 0).to_numpy(float))
model = {"mu": mu.to_dict(), "sd": sd.to_dict(), "w": w.tolist(), "platt": [0.0, 1.0]}
high, low = rows.assign(r5=1.5).head(1), rows.assign(r5=-1.5).head(1)
assert an.probability(high, model)[0] > 0.6 > 0.4 > an.probability(low, model)[0]
print("ok")
