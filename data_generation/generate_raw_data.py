#!/usr/bin/env python3
"""
Financial Crime Copilot — synthetic raw-data generator.

Pure Python standard library only (no numpy/pandas), fully seeded and reproducible.
Produces four raw CSV files that mimic what a bank's source systems would land in a
Unity Catalog Volume, for Lakeflow (Auto Loader) to ingest:

  customers.csv     - customer master (incl. PEP flag, risk rating, PII)
  accounts.csv      - deposit/checking accounts per customer
  transactions.csv  - ~6 months of transactions across several channels
  alerts.csv        - alerts raised by a legacy rules-based TM system, with a
                      ground-truth is_suspicious label (majority are false positives)

Design: a small set of "bad actors" perform real laundering typologies (structuring,
rapid movement, high-risk-country wires, PEP misuse). Rules fire on those AND on many
benign coincidences, producing a realistic >90% false-positive alert population with
clean ground truth for training/evaluation.
"""
import csv
import os
import random
import uuid
from datetime import datetime, timedelta

SEED = 42
random.seed(SEED)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
os.makedirs(OUT_DIR, exist_ok=True)

# ---- scale knobs -----------------------------------------------------------
N_CUSTOMERS = 2000
BAD_ACTOR_RATE = 0.05            # fraction of customers who actually launder
START = datetime(2026, 1, 1)
DAYS = 180                       # ~6 months
STRUCTURING_THRESHOLD = 10000    # cash-reporting threshold analysts watch

HIGH_RISK_COUNTRIES = ["IR", "KP", "SY", "MM", "AF", "YE"]
NORMAL_COUNTRIES = ["US", "GB", "DE", "JP", "CA", "FR", "AU", "SG", "NL"]
CHANNELS = ["wire", "ach", "cash", "card", "online"]
OCCUPATIONS = ["engineer", "teacher", "nurse", "retail", "consultant",
               "driver", "chef", "analyst", "contractor", "retired"]
FIRST = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
         "Linda", "David", "Elizabeth", "Kenji", "Yuki", "Wei", "Mei", "Omar",
         "Fatima", "Carlos", "Sofia", "Ivan", "Olga"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Sato", "Tanaka", "Chen", "Wang", "Khan", "Ali", "Rossi",
        "Muller", "Nguyen", "Kim", "Petrov", "Silva"]


def rand_date(start, days):
    return start + timedelta(days=random.randint(0, days - 1),
                             hours=random.randint(0, 23),
                             minutes=random.randint(0, 59))


def money(lo, hi):
    return round(random.uniform(lo, hi), 2)


# ---- 1. customers ----------------------------------------------------------
customers = []
n_bad = int(N_CUSTOMERS * BAD_ACTOR_RATE)
bad_ids = set(random.sample(range(1, N_CUSTOMERS + 1), n_bad))

for cid in range(1, N_CUSTOMERS + 1):
    is_pep = random.random() < 0.04
    # risk rating is the bank's a-priori KYC rating (not the ground-truth label)
    base = random.random()
    if is_pep:
        base += 0.3
    risk = "HIGH" if base > 0.85 else "MEDIUM" if base > 0.55 else "LOW"
    customers.append({
        "customer_id": cid,
        "full_name": f"{random.choice(FIRST)} {random.choice(LAST)}",
        "date_of_birth": (datetime(1955, 1, 1) +
                          timedelta(days=random.randint(0, 60 * 365))).strftime("%Y-%m-%d"),
        "national_id": f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(1000,9999)}",
        "email": f"user{cid}@example.com",
        "country": random.choice(NORMAL_COUNTRIES),
        "occupation": random.choice(OCCUPATIONS),
        "kyc_risk_rating": risk,
        "is_pep": is_pep,
        "account_open_date": (START - timedelta(days=random.randint(30, 3000))).strftime("%Y-%m-%d"),
    })

# ---- 2. accounts -----------------------------------------------------------
accounts = []
acct_of_customer = {}
aid = 100000
for c in customers:
    for _ in range(random.randint(1, 2)):
        aid += 1
        accounts.append({
            "account_id": aid,
            "customer_id": c["customer_id"],
            "account_type": random.choice(["checking", "savings", "business"]),
            "open_date": c["account_open_date"],
            "opening_balance": money(500, 50000),
        })
        acct_of_customer.setdefault(c["customer_id"], []).append(aid)

# ---- 3. transactions -------------------------------------------------------
# We tag each txn with a hidden `typology` (empty for normal) so alert labeling is exact.
transactions = []


def add_txn(account_id, ts, amount, direction, channel, cp_country, typology=""):
    transactions.append({
        "txn_id": str(uuid.uuid4()),
        "account_id": account_id,
        "txn_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "amount": amount,
        "direction": direction,          # credit (in) / debit (out)
        "channel": channel,
        "counterparty_country": cp_country,
        "_typology": typology,           # hidden ground-truth tag, dropped before ingest-facing use
    })


# 3a. normal background activity for everyone
#     Includes benign patterns that legacy rules mistake for suspicious activity
#     (remittances to high-risk countries, small-business cash, real-estate pass-through,
#     high-velocity business accounts). These become the false positives.
for c in customers:
    for acct in acct_of_customer[c["customer_id"]]:
        for _ in range(random.randint(20, 60)):
            add_txn(acct, rand_date(START, DAYS), money(5, 4000),
                    random.choice(["credit", "debit"]),
                    random.choice(CHANNELS), c["country"])

        # benign remittance to a high-risk country (trips R2, not suspicious)
        if random.random() < 0.50:
            for _ in range(random.randint(1, 2)):
                add_txn(acct, rand_date(START, DAYS), money(5100, 15000),
                        random.choice(["credit", "debit"]), "wire",
                        random.choice(HIGH_RISK_COUNTRIES))

        # small-business frequent cash under threshold (trips R1, not suspicious)
        if random.random() < 0.13:
            base_ts = rand_date(START, DAYS - 3)
            for i in range(random.randint(3, 5)):
                add_txn(acct, base_ts + timedelta(hours=i * 10),
                        money(4000, 9500), "credit", "cash", c["country"])

        # real-estate / payroll pass-through: big credit then big debit (trips R3, benign)
        if random.random() < 0.10:
            ts = rand_date(START, DAYS - 1)
            big = money(31000, 90000)
            add_txn(acct, ts, big, "credit", "ach", c["country"])
            add_txn(acct, ts + timedelta(hours=random.randint(2, 40)),
                    round(big * random.uniform(0.72, 0.9), 2), "debit", "ach", c["country"])

        # active business, many txns in one day (trips R4, benign)
        if random.random() < 0.07:
            day = rand_date(START, DAYS)
            for i in range(random.randint(16, 25)):
                add_txn(acct, day.replace(hour=random.randint(8, 20)),
                        money(20, 2500), random.choice(["credit", "debit"]),
                        random.choice(["card", "online", "ach"]), c["country"])

# 3b. injected laundering typologies for bad actors (ground truth = suspicious)
for c in customers:
    if c["customer_id"] not in bad_ids:
        continue
    acct = acct_of_customer[c["customer_id"]][0]
    typ = random.choice(["structuring", "rapid_movement", "high_risk_wire", "layering"])
    day0 = random.randint(0, DAYS - 5)

    if typ == "structuring":
        # several cash deposits just under the reporting threshold within a few days
        base_ts = START + timedelta(days=day0)
        for i in range(random.randint(3, 6)):
            add_txn(acct, base_ts + timedelta(hours=i * 8),
                    money(8500, 9900), "credit", "cash", c["country"], "structuring")

    elif typ == "rapid_movement":
        # large credit, then most of it moved out within 48h
        ts = START + timedelta(days=day0)
        big = money(40000, 120000)
        add_txn(acct, ts, big, "credit", "wire", random.choice(NORMAL_COUNTRIES), "rapid_movement")
        add_txn(acct, ts + timedelta(hours=random.randint(2, 40)),
                round(big * random.uniform(0.85, 0.98), 2), "debit", "wire",
                random.choice(HIGH_RISK_COUNTRIES), "rapid_movement")

    elif typ == "high_risk_wire":
        # repeated sizable wires to/from high-risk jurisdictions
        for i in range(random.randint(2, 5)):
            add_txn(acct, START + timedelta(days=day0 + i),
                    money(6000, 30000), random.choice(["credit", "debit"]),
                    "wire", random.choice(HIGH_RISK_COUNTRIES), "high_risk_wire")

    elif typ == "layering":
        # many rapid in/out transfers of similar size (round-tripping)
        base_ts = START + timedelta(days=day0)
        amt = money(9000, 25000)
        for i in range(random.randint(6, 12)):
            add_txn(acct, base_ts + timedelta(hours=i * 5),
                    round(amt * random.uniform(0.95, 1.05), 2),
                    "credit" if i % 2 == 0 else "debit", "wire",
                    random.choice(NORMAL_COUNTRIES + HIGH_RISK_COUNTRIES), "layering")

# ---- 4. rules-based alerts (legacy TM) -------------------------------------
# Rules fire on structural signals. They catch the injected typologies AND many benign
# coincidences => high false-positive rate. Ground truth = whether the account is a bad
# actor's account AND the txn was part of an injected pattern.
by_account = {}
for t in transactions:
    by_account.setdefault(t["account_id"], []).append(t)
for lst in by_account.values():
    lst.sort(key=lambda x: x["txn_ts"])

customer_of_account = {a["account_id"]: a["customer_id"] for a in accounts}
pep_of_customer = {c["customer_id"]: c["is_pep"] for c in customers}

alerts = []


def add_alert(account_id, rule, ts, amount, typology_truth):
    cust = customer_of_account[account_id]
    alerts.append({
        "alert_id": str(uuid.uuid4()),
        "account_id": account_id,
        "customer_id": cust,
        "rule_code": rule,
        "alert_ts": ts,
        "trigger_amount": amount,
        "is_pep": pep_of_customer[cust],
        # ground truth: suspicious only if this account belongs to a bad actor and the
        # triggering activity is a genuine injected typology
        "is_suspicious": bool(typology_truth),
        "true_typology": typology_truth or "",
    })


for acct, txns in by_account.items():
    # R1 STRUCTURING: >=3 cash credits < threshold within any 72h window
    cash = [t for t in txns if t["channel"] == "cash" and t["direction"] == "credit"
            and t["amount"] < STRUCTURING_THRESHOLD]
    for i in range(len(cash)):
        window = [t for t in cash
                  if 0 <= (datetime.strptime(t["txn_ts"], "%Y-%m-%d %H:%M:%S")
                           - datetime.strptime(cash[i]["txn_ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() <= 72 * 3600]
        if len(window) >= 3:
            truth = window[0]["_typology"] if window[0]["_typology"] == "structuring" else ""
            add_alert(acct, "R1_STRUCTURING", cash[i]["txn_ts"],
                      round(sum(t["amount"] for t in window), 2), truth)
            break

    # R2 HIGH_RISK_COUNTRY: a wire to/from a high-risk country > 5000 (once per account,
    # preferring a genuine-typology txn if the account has one)
    r2 = [t for t in txns if t["channel"] == "wire"
          and t["counterparty_country"] in HIGH_RISK_COUNTRIES and t["amount"] > 5000]
    if r2:
        hit = next((t for t in r2 if t["_typology"] in ("high_risk_wire", "rapid_movement", "layering")), r2[0])
        add_alert(acct, "R2_HIGH_RISK_COUNTRY", hit["txn_ts"], hit["amount"],
                  hit["_typology"] if hit["_typology"] in ("high_risk_wire", "rapid_movement", "layering") else "")

    # R3 RAPID_MOVEMENT: a large credit (>30k) followed by a debit within 48h
    for i, t in enumerate(txns):
        if t["direction"] == "credit" and t["amount"] > 30000:
            t0 = datetime.strptime(t["txn_ts"], "%Y-%m-%d %H:%M:%S")
            for u in txns[i + 1:]:
                du = datetime.strptime(u["txn_ts"], "%Y-%m-%d %H:%M:%S")
                if (du - t0).total_seconds() > 48 * 3600:
                    break
                if u["direction"] == "debit" and u["amount"] > t["amount"] * 0.7:
                    add_alert(acct, "R3_RAPID_MOVEMENT", u["txn_ts"], u["amount"],
                              t["_typology"] if t["_typology"] == "rapid_movement" else "")
                    break

    # R4 HIGH_VELOCITY: >15 txns in any single calendar day
    by_day = {}
    for t in txns:
        by_day.setdefault(t["txn_ts"][:10], []).append(t)
    for day, lst in by_day.items():
        if len(lst) > 15:
            truth = next((t["_typology"] for t in lst if t["_typology"] == "layering"), "")
            add_alert(acct, "R4_HIGH_VELOCITY", lst[0]["txn_ts"],
                      round(sum(t["amount"] for t in lst), 2), truth)
            break

    # R5 PEP_ACTIVITY: any transaction > 20k by a PEP
    if pep_of_customer[customer_of_account[acct]]:
        for t in txns:
            if t["amount"] > 20000:
                add_alert(acct, "R5_PEP_ACTIVITY", t["txn_ts"], t["amount"],
                          t["_typology"] if t["_typology"] else "")
                break


# ---- write CSVs ------------------------------------------------------------
def write_csv(name, rows, fields):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path, len(rows)


c_path, c_n = write_csv("customers.csv", customers, list(customers[0].keys()))
a_path, a_n = write_csv("accounts.csv", accounts, list(accounts[0].keys()))
# drop the hidden _typology column from the ingest-facing transactions file
txn_fields = [k for k in transactions[0].keys() if k != "_typology"]
t_path, t_n = write_csv("transactions.csv", transactions, txn_fields)
al_path, al_n = write_csv("alerts.csv", alerts, list(alerts[0].keys()))

n_susp = sum(1 for a in alerts if a["is_suspicious"])
fp_rate = 100 * (1 - n_susp / len(alerts)) if alerts else 0

print("=== Financial Crime Copilot — synthetic data generated ===")
print(f"seed={SEED}  output={os.path.relpath(OUT_DIR)}")
print(f"customers      : {c_n:>8,}  (bad actors: {len(bad_ids)}, PEPs: {sum(c['is_pep'] for c in customers)})")
print(f"accounts       : {a_n:>8,}")
print(f"transactions   : {t_n:>8,}")
print(f"alerts         : {al_n:>8,}")
print(f"  suspicious   : {n_susp:>8,}  (ground truth)")
print(f"  false-pos    : {al_n - n_susp:>8,}  => false-positive rate {fp_rate:.1f}%")
print("alerts by rule :")
by_rule = {}
for a in alerts:
    by_rule.setdefault(a["rule_code"], [0, 0])
    by_rule[a["rule_code"]][0] += 1
    by_rule[a["rule_code"]][1] += int(a["is_suspicious"])
for rule in sorted(by_rule):
    tot, sus = by_rule[rule]
    print(f"  {rule:<22} total={tot:>6,}  suspicious={sus:>5,}  precision={100*sus/tot:5.1f}%")
