#!/usr/bin/env python3
"""Append Stripe subscriptions not yet recorded in subscriptions.csv.

Reads the API key from STRIPE_API_KEY. Rows are append-only, deduplicated
by subscription id, so a subscription is recorded once with the state it
had when first seen.
"""

import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.stripe.com/v1/subscriptions"
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscriptions.csv")
FIELDS = [
    "created_utc",
    "subscription_id",
    "customer_email",
    "customer_name",
    "plan",
    "quantity_tb",
    "status",
    "cluster_uuid",
]


def stripe_get(params):
    req = urllib.request.Request(API_URL + "?" + urllib.parse.urlencode(params, doseq=True))
    req.add_header("Authorization", "Bearer " + os.environ["STRIPE_API_KEY"])
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def list_subscriptions():
    params = {"status": "all", "limit": 100, "expand[]": "data.customer"}
    while True:
        page = stripe_get(params)
        yield from page["data"]
        if not page.get("has_more"):
            return
        params["starting_after"] = page["data"][-1]["id"]


def row_for(sub):
    customer = sub.get("customer")
    if not isinstance(customer, dict):
        customer = {}
    item = (sub.get("items", {}).get("data") or [{}])[0]
    interval = (item.get("price", {}).get("recurring") or {}).get("interval", "")
    cluster_uuid = (
        sub.get("metadata", {}).get("cluster_uuid")
        or customer.get("metadata", {}).get("cluster_uuid")
        or ""
    )
    return {
        "created_utc": datetime.fromtimestamp(sub["created"], tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "subscription_id": sub["id"],
        "customer_email": customer.get("email") or "",
        "customer_name": customer.get("name") or "",
        "plan": {"month": "monthly", "year": "yearly"}.get(interval, interval),
        "quantity_tb": item.get("quantity", ""),
        "status": sub.get("status", ""),
        "cluster_uuid": cluster_uuid,
    }


def main():
    with open(CSV_PATH, newline="") as f:
        known = {row["subscription_id"] for row in csv.DictReader(f)}
    new_rows = [row_for(s) for s in list_subscriptions() if s["id"] not in known]
    if not new_rows:
        print("No new subscriptions.")
        return
    new_rows.sort(key=lambda r: r["created_utc"])
    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerows(new_rows)
    print(f"Recorded {len(new_rows)} new subscription(s).")


if __name__ == "__main__":
    sys.exit(main())
