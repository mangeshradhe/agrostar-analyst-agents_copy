# Field Visit Priority Engine

---

## The Problem

**11,409 active B2B partners. 322 SMs. 131 TMs.** Who to visit and when is decided entirely on instinct — no system exists to guide it.

- Partners with overdue money go unvisited for weeks
- Partners who stop buying get no intervention — they churn silently
- Reps drive across territories inefficiently instead of visiting nearby partners together
- Healthy partners get neglected because there is no trigger to check in

---

## What We Built

A recommendation engine that gives every rep a **weekly priority list**, refreshed every morning as data changes. Within that list, the system identifies which partners are close to each other geographically — so the rep can group nearby visits into a single efficient trip rather than criss-crossing the territory.

**Each rep gets daily:**
- **Up to 4 partners to visit** — nearby partners identified from the weekly list, grouped to minimise travel
- **2 partners to call** — only shown when the visit group is full at 4; if not, those slots become visits

Every recommendation includes a plain reason:
> *"₹90,000 overdue — 27 days past due"*
> *"Partner has not ordered in 6 months"*
> *"Not visited in 97 days — time to check in"*

---

## How We Decide Who Is on the List

Every partner is scored daily across **four signals**. Each has a weight reflecting its importance. The combined score determines where a partner sits on the weekly list.

---

### 1. Overdue Money — 45%

The most important signal. We look at:
- **How much is overdue** and **how old** the debt is
- Whether the partner has been **paying regularly** in recent months
- When they **last made a payment**

A partner with ₹2 lakh overdue for 90 days ranks far higher than one with ₹10,000 overdue from last week — even though the second owes more. Age and consistency matter, not just the amount.

**Any partner with more than ₹5,000 overdue is automatically placed in the visit pool** — physical presence is needed for meaningful recovery. Their Sales tag is marked **Blocked** — pushing products when credit is unpaid makes no sense.

---

### 2. Buying Behaviour — 25%

Is this partner buying less, and is the trend getting worse? We look at:
- **Revenue this year vs same period last year**
- **How many consecutive months** orders have been declining
- **Days since their last order**
- **Unused credit available** — idle credit limit is an untapped opportunity

One declining month is a yellow flag. Two or three in a row is urgent. **A partner with zero orders in six months is treated as a visit priority** — they are silently churning and rarely come back without a physical conversation.

---

### 3. Relationship Cadence — 15%

A healthy, paying partner can still drift away if they feel ignored. Every partner has an expected visit window:
- **SM: every 15 days**
- **TM: every 45 days**

When a partner is overdue on their window — even with clean accounts and healthy sales — their score rises automatically. This surfaces partners who would otherwise be invisible: no debt, no sales alarm, but quietly becoming disconnected.

---

### 4. Territory Target Gap — 15%

When a territory is behind on its monthly sales target in a specific category, partners in that territory who sell that category score higher. The daily visit plan connects directly to the monthly number — the rep is steered towards partners most likely to help close a shortfall.

---

## How the Scores Are Calculated

**Each signal is built from sub-indicators, each with its own weight:**

| Signal | Sub-indicators |
|---|---|
| Overdue Money | Overdue amount (40%) · Age of overdue (25%) · Payment regularity (20%) · Recency of last payment (15%) |
| Buying Behaviour | YoY revenue decline (40%) · Monthly order trend (30%) · Days since last order (20%) · Unused credit (10%) |

**Scores are always relative, not absolute.** Every partner is ranked against the full fleet — not against a fixed threshold. A ₹50,000 overdue in a territory where everyone owes ₹2 lakh is not urgent. The same amount where most partners owe nothing is a red flag. This context updates automatically every day as conditions across the fleet change.

---

## What High, Medium, Low Mean on the Card

Two tags appear on every recommendation card — one for **Collection**, one for **Sales**. These are not fixed bands. They divide the entire fleet into three equal groups daily:

- **High** — top third of the fleet on this signal today
- **Medium** — middle third
- **Low** — bottom third

**Sales shows "Blocked"** when a partner has meaningful overdue — their credit is frozen and a sales conversation is not appropriate until the debt is resolved.

**The reason on the card reflects the most extreme signal for that specific partner** — not the highest-weighted one. If a partner has moderate overdue but hasn't been visited in four months, the card says *"Not visited in 120 days"* — because that is the more striking fact for that partner. This ensures the rep always sees the most relevant reason, not a generic overdue message on every card.

---

## Visit vs Call — The Distinction

The system routes every eligible partner to either a visit or a call before building the daily group. This routing is based on two things: the urgency of the situation and what we know from history about how that partner behaves.

**Always a visit:**
- Partner has more than ₹5,000 overdue — physical presence needed for recovery
- Partner has placed zero orders in the last six months — dormant, needs in-person reactivation
- Historical data shows the partner only orders or pays around the time of a visit — they need the rep to show up to act

**A call when all three are true:**
- No meaningful overdue — account is clean
- Actively buying — sales are stable or growing
- Orders and pays on their own — behaviour data shows they do not need a visit to act

A call means: *"This partner is doing well on their own. We are staying connected."*

**If the visit group has fewer than 4 partners**, the top-ranked call candidates are promoted to visits to fill the gap. **Calls only appear when the rep already has a full day of 4 visits.**

---

## Travel Optimisation

The system does not scatter visits across the territory. It identifies which partners from the weekly priority list are **geographically close to each other** and groups them as today's route.

- **92% of partners** have GPS coordinates
- Remaining 8% clustered by pincode — **full coverage**
- Typical daily route: **10–43 km for 4 visits** — one efficient morning trip
- Weekly list refreshes every morning — as data changes, the best nearby group updates automatically

The grouping is purely about proximity. Which four partners end up together on a given day depends on who lives close to each other — not on what type of signal they carry.

---

## Three Examples

**Umesh Mishra — Prayagraj**
Today's nearby group from his weekly list:

| Partner | Overdue | Days Past Due |
|---|---|---|
| Jai maa durga khad beej | ₹90,353 | 27 days |
| M/S Patel Khad Bhandar | ₹16,392 | 24 days |
| M/S Pulkit Khad & Beej | ₹16,405 | 6 days |

These three happen to be geographically close. One trip covers all three.

---

**Chetan Patidar — Jhalawar**
Today's nearby group — two with overdue, two never visited:

| Partner | Overdue | Reason |
|---|---|---|
| AMBIKA KRISHI SEWA KENDRA | ₹1,57,192 | Overdue — 30 days past due |
| SUMAN AGRO AGENCY | ₹14,866 | Overdue — 2 days past due |
| GURUKRIPA KSK | ₹0 | Never visited — no recent orders |
| SHARMA KRISHI SEWA | ₹0 | Never visited — no recent orders |

The never-visited partners would stay invisible without this system.

---

**Vaibhav Singh — Gonda**
Today's nearby group — four with overdue. Visit group is full, so calls are also shown:

Visits: YES BEEJ (₹2,68,157) · CHAUDHARY BEEJ (₹18,234) · SINGH KHAD (₹2,09,952) · Aman beej (₹17,029)

Calls: RAHUL SINGH KHAD · Durga beej bhandar — both healthy, buying independently. Calling to stay connected.

---

## What Changes

| Today | With this system |
|---|---|
| Visit decisions based on instinct | Data-backed weekly priority list for every rep |
| Overdue partners — no systematic follow-up | Every partner with ₹5,000+ overdue gets a mandatory visit |
| Dormant partners — invisible | Surface before they churn |
| Reps driving across territories | Nearby partners grouped — one efficient daily route |
| Rep walks in without context | Clear reason on every recommendation |
| No cadence discipline | Every partner visited within their expected window |

---

## Three Decisions Needed

| Decision | Current assumption |
|---|---|
| Overdue threshold for mandatory visit | **₹5,000** |
| SM visit cadence | **Every 15 days** |
| TM visit cadence | **Every 45 days** |

