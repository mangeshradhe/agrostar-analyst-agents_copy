"""
Validate one read of one sales call and save it. A read that fails validation is NOT saved (it is logged to
output/sales_reads_rejections.jsonl) and must be redone, not patched.

Checks are mechanical only (structure, enums, every quote found verbatim in THIS call's transcript, numbers in
values present in the transcript). They do not judge meaning.

Usage: /usr/bin/python3 scripts/sales_reads_save.py <call_id> <read.json>
"""
import fcntl
import json
import sys

import sales_reads_common as C


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    call_id, path = sys.argv[1], sys.argv[2]
    calls, _ = C.cohort_calls()
    call = next((c for c in calls if c["call_id"] == call_id), None)
    if not call:
        raise SystemExit(f"REJECTED: call {call_id} is not in the cohort")
    with open(path, encoding="utf-8") as f:
        try:
            read = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"REJECTED: not valid JSON ({e})")
    errs = C.validate(read, call["transcript"])
    if errs:
        with open(C.REJECTS_PATH, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps({"call_id": call_id, "at": C.now(), "errors": errs}, ensure_ascii=False) + "\n")
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        print(f"REJECTED {call_id} ({len(errs)} problem(s)) -- redo the read:")
        for e in errs:
            print("  -", e)
        raise SystemExit(1)
    rec = {"call_id": call_id, "order_id": call["order_id"], "transcript_sha256": C.sha(call["transcript"]),
           "schema_version": C.SCHEMA_VERSION, "match": call["match"], "call_date": call["call_start"][:10],
           "read_at": C.now(), "read": read}
    with open(C.READS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)          # several readers save in parallel; never interleave lines
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)
    print(f"SAVED {call_id} (order {call['order_id']}): commitment={read['commitment']['value']}, "
          f"quality={read['reliability']['transcript_quality']}")


if __name__ == "__main__":
    main()
