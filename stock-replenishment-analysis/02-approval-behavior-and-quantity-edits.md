# Deep-dive: Approval behavior & quantity edits (July System TOs)

*Question: of the system-generated TOs, and of those approved, how many had quantities edited? If edited, is it set to 0 (denial) or reduced (conservative)?*

## Answer: zero edits. Approvers are strictly binary.

Of the 1,170 approved System TO lines in July, **not one had its quantity edited** — every approval went through at exactly the system-proposed quantity. Nobody manually sets quantities to 0; the zeroing observed in the data is done by the system itself and functions as full denial, never partial trimming.

## Field-level evidence

Three quantity columns exist: `transfer_qty` (system proposal), `edited_transfer_qty`, `actual_transfer_qty`.

| Cohort | Lines | Proposed qty | actual = proposed | Reduced (0<actual<proposed) | Set to 0 | Increased |
|---|---:|---:|---:|---:|---:|---:|
| APPROVED | 1,170 | 339,405 | **1,170 (100%)** | 0 | 0 | 0 |
| REJECTED (human) | 127 | 24,066 | 127 | 0 | 0 | 0 |
| Unactioned (waiting/system-cancelled/archived) | 384 | 56,725 | 350 | 0 | 0 | 34* |

*\*The 34 "increases" are archived lines where `transfer_qty` was wiped to 0 and `actual_transfer_qty` preserved the original — an artifact, not a real edit.*

`edited_transfer_qty` semantics (verified): **0 on every line not yet approved** (waiting / rejected / system-cancelled / locked), **equal to `transfer_qty` on every approved line**. It is a workflow field, not a human-edit record.

## Behavioral conclusions

1. **Approvers are binary** — pass untouched or reject whole. No "send 60% of what the system asked" behavior anywhere: no partial reductions, no manual zeroing, no top-ups.
2. **Quantity-to-zero happens, but the system does it.** Later re-allocation runs zero out lines still unapproved in the queue → 127 human rejections (reason: "transfer qty reduced to zero during re-allocation" — the human just confirms a line already killed) + 28 SYSTEM_CANCELLED. Always 100% reduction (denial), never a trim.
3. **Open question for the product team:** does the approval UI even offer a quantity-edit option? If not, the 90% approval rate is partly a UI constraint — a reviewer who thinks "right transfer, too much qty" has no middle button. The data alone can't distinguish genuine endorsement from forced binary choice.
4. Caveat: if pre-approval edits overwrote `transfer_qty` in place, originals would be invisible. Evidence argues against it (rejected lines retain full original qty in `transfer_qty`, zero recorded separately in `edited_transfer_qty`), but confirmable in a minute by the product team.

## Related: unique SKU counts (July System TO)

- **355 unique SKUs** have System TO lines
- **268 SKUs** have ≥1 approved line
- **180 SKUs** already physically moving (picked/invoiced/dispatched)
- Manual benchmark touched only 170–180 SKUs in comparable windows → system rebalances ~50% more of the catalog even counting approved-only.
