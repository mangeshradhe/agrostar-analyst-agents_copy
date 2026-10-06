"""
Build the PRIORITY cohort of Mustard sales calls and split it into batches for the parallel readers.

Why a priority set: reading all 1,891 Mustard calls carefully takes days; the insight we want is "what on the sales
call precedes a cancelled / held order?", which needs the troubled orders plus a delivered baseline. So:
  A  every order whose last LMD call was CANCELLED
  B  every ON_HOLD order with a non-logistics reason (no money, denies ordering, product not required, location, other, inventory)
  C  a seeded random sample of the remaining ON_HOLD orders (farmer unavailable / timing)
  D  a seeded random sample of DELIVERY_CONFIRMED orders (the baseline)
The LMD outcome is used ONLY here, to choose which calls to read. It is never shown to a reader (batch files hold
call ids only), and the reader's prompt does not mention it. The set is shuffled (seeded) and chunked so every batch
mixes tiers -- any subset of finished batches is still a fair cancelled-vs-delivered comparison.

Already-read calls stay read; they just count toward their batch. Output (local only, gitignored): output/sales_batches/.

Usage: /usr/bin/python3 scripts/sales_reads_make_batches.py [batch_size=25]
"""
import collections
import json
import os
import random
import sys

import sales_reads_common as C

SEED = 42
ON_HOLD_PRIORITY_REASONS = {"NO_MONEY", "FARMER_DENIES_ORDERING", "PRODUCT_NOT_REQUIRED", "LOCATION_CONFUSION", "OTHER", "INVENTORY_UNAVAILABLE"}
N_HOLD_SAMPLE, N_DELIVERED_SAMPLE = 250, 250


def main():
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    calls, _ = C.cohort_calls()
    by = collections.defaultdict(list)
    for c in C.load("lmd_calls_data.json"):
        by[c["order_id"]].append(c)
    for v in by.values():
        v.sort(key=lambda c: str(c["created_at"]))

    def outcome(o):
        return by[o][-1]["call_outcome"] if by.get(o) else None

    def reasons(o):
        return {c.get("reason_category_known") for c in by.get(o, [])} - {None, "NOT_APPLICABLE"}

    A, B, C_pool, D_pool = [], [], [], []
    for c in calls:
        o, out = c["order_id"], outcome(c["order_id"])
        if out == "DELIVERY_CANCELLED":
            A.append(c)
        elif out == "DELIVERY_ON_HOLD":
            (B if reasons(o) & ON_HOLD_PRIORITY_REASONS else C_pool).append(c)
        elif out == "DELIVERY_CONFIRMED":
            D_pool.append(c)
    rng = random.Random(SEED)
    Cs = rng.sample(C_pool, min(N_HOLD_SAMPLE, len(C_pool)))
    Ds = rng.sample(D_pool, min(N_DELIVERED_SAMPLE, len(D_pool)))
    tier = {c["call_id"]: t for t, grp in (("A_cancelled", A), ("B_hold_nonlogistics", B), ("C_hold_logistics_sample", Cs), ("D_delivered_sample", Ds)) for c in grp}
    ids = list(tier)
    rng.shuffle(ids)
    os.makedirs(C.BATCH_DIR, exist_ok=True)
    for f in os.listdir(C.BATCH_DIR):
        os.remove(os.path.join(C.BATCH_DIR, f))
    batches = [ids[i:i + size] for i in range(0, len(ids), size)]
    for n, b in enumerate(batches, 1):
        with open(os.path.join(C.BATCH_DIR, f"batch_{n:02d}.json"), "w") as f:
            json.dump(b, f)
    with open(os.path.join(C.BATCH_DIR, "manifest.json"), "w") as f:     # tiers, for the analysis step only
        json.dump({"seed": SEED, "tier": tier, "batches": len(batches)}, f)
    print(f"priority set {len(ids)} calls: A cancelled {len(A)} | B hold (non-logistics) {len(B)} | "
          f"C hold sample {len(Cs)} of {len(C_pool)} | D delivered sample {len(Ds)} of {len(D_pool)}")
    done = {(r['call_id']) for r in map(json.loads, open(C.READS_PATH))} if os.path.exists(C.READS_PATH) else set()
    print(f"{len(batches)} batches of up to {size}; already read inside the set: {len(set(ids) & done)}")


if __name__ == "__main__":
    main()
