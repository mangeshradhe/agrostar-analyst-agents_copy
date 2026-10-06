"""
Progress + checkpoint view for the sales-call reads.

  status            progress, rejections, counts by commitment / quality / conduct flags
  --review [N]      the last N reads (default 30) with the LMD outcome JOINED AFTER the read, for review
                    (the reader never saw the outcome while reading)

Usage: /usr/bin/python3 scripts/sales_reads_status.py [--review [N]]
"""
import collections
import json
import os
import sys

import sales_reads_common as C


def reads():
    latest = {}
    if os.path.exists(C.READS_PATH):
        with open(C.READS_PATH, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                latest[(r["call_id"], r["schema_version"])] = r   # latest read wins
    return list(latest.values())


def lmd_outcome(order_id):
    calls = [c for c in C.load("lmd_calls_data.json") if c["order_id"] == order_id]
    calls.sort(key=lambda c: str(c["created_at"]))
    if not calls:
        return "no LMD call"
    last = calls[-1]
    return f"{last['call_outcome']} / {last.get('reason_category_known')}"


def main():
    rs = [r for r in reads() if r["schema_version"] == C.SCHEMA_VERSION]
    cohort, _ = C.cohort_calls()
    rejects = sum(1 for _ in open(C.REJECTS_PATH)) if os.path.exists(C.REJECTS_PATH) else 0
    print(f"schema {C.SCHEMA_VERSION}: {len(rs)} of {len(cohort)} Mustard calls read | rejections logged: {rejects}")
    if len(sys.argv) > 1 and sys.argv[1] == "--review":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        by_order = collections.defaultdict(list)
        for c in C.load("lmd_calls_data.json"):
            by_order[c["order_id"]].append(c)
        for r in sorted(rs, key=lambda r: r["read_at"])[-n:]:
            oc = sorted(by_order.get(r["order_id"], []), key=lambda c: str(c["created_at"]))
            out = f"{oc[-1]['call_outcome']} / {oc[-1].get('reason_category_known')}" if oc else "no LMD call"
            x = r["read"]
            print(f"\n{r['call_date']} order {r['order_id']} call {r['call_id']} [{r['match']}]  LMD: {out}")
            print(f"  commitment={x['commitment']['value']} who={x['who_decided']['value']} crop_fit={x['crop_and_need']['fit']} "
                  f"pay={x['payment'].get('readiness')} quality={x['reliability']['transcript_quality']}/{x['reliability']['confidence']}")
            print("  story:", x["story"])
        return
    for label, get in (("commitment", lambda x: [x["commitment"]["value"]]),
                       ("transcript quality", lambda x: [x["reliability"]["transcript_quality"]]),
                       ("offers pitched", lambda x: [o["value"] for o in x["offers_pitched"]]),
                       ("conduct flags", lambda x: [o["value"] for o in x["agent_conduct_flags"]]),
                       ("payment readiness", lambda x: [x["payment"].get("readiness")]),
                       ("crop fit", lambda x: [x["crop_and_need"]["fit"]])):
        cnt = collections.Counter(v for r in rs for v in get(r["read"]))
        print(f"{label}: {dict(cnt.most_common())}")


if __name__ == "__main__":
    main()
