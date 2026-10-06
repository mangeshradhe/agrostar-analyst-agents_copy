"""
Print the NEXT unread Mustard sales call for Claude to read -- one call only.

Shows the reader ONLY: call id, order id, the order's items (name / qty / price) and the speaker-labelled
transcript. Deliberately NOT shown: anything from disposition_data (ai_summary, dispositions, lead_interest)
and the LMD outcome -- see ../02-sales_call_reading_plan.md.

Usage: /usr/bin/python3 scripts/sales_reads_next.py [--batch NN]   (NN = a batch file from sales_reads_make_batches.py)
"""
import json
import sys

import sales_reads_common as C


GLOSSARY = (
    "SPEECH-TO-TEXT GLOSSARY (confusions seen in these transcripts): 'cash per' = Kasper (AgroStar's mustard hybrid, NOT a payment term); "
    "'bees' = beej (seed); 'sharashon' / 'rayada' / 'raya' = mustard (sarson / rai); 'bavan ikkis' = 5221 (AgroStar hybrid); 'paintalis chalis' = 45S46 (a Pioneer hybrid); "
    "'keo' = ke; 'peo' = par; 'theoli/thaili' = packet/bag; 'chhudava lena' = take delivery (pay on receipt). Numbers are often spelled as words.")


def main():
    batch = sys.argv[sys.argv.index('--batch') + 1] if '--batch' in sys.argv else None
    call, remaining, total = C.next_call(batch)
    if not call:
        print(f"ALL DONE: {total} calls read at schema {C.SCHEMA_VERSION}" + (f" (batch {batch})." if batch else "."))
        return
    print(f"=== CALL {call['call_id']} | order {call['order_id']} | match: {call['match']} | "
          f"{total - remaining + 1} of {total} (schema {C.SCHEMA_VERSION}) | transcript_sha256 {call['transcript_sha256']}")
    print(GLOSSARY)
    print("ORDER ITEMS (reference only):")
    for i in call["order_items"]:
        print(f"  - {i['item']} x{i['qty']}  ₹{i['total_price']:.0f}")
    print(f"TRANSCRIPT ({len(call['transcript'])} turns; agent=owner, farmer=client):")
    for spk, text in call["transcript"]:
        print(f"{'AGENT ' if spk == 'owner' else 'FARMER'}: {text}")
    print("=== END CALL " + call["call_id"])


if __name__ == "__main__":
    main()
