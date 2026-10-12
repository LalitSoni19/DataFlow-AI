"""Synthetic sample data generation for local testing."""

from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REGIONS = {
    "North": [("Delhi", "Delhi"), ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh")],
    "West": [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Ahmedabad", "Gujarat")],
    "South": [("Bengaluru", "Karnataka"), ("Chennai", "Tamil Nadu"), ("Hyderabad", "Telangana")],
    "East": [("Kolkata", "West Bengal"), ("Bhubaneswar", "Odisha"), ("Guwahati", "Assam")],
}
CATEGORIES = {
    "Electronics": [("Wireless Earbuds", 2499, 1250), ("Smart Watch", 4999, 2700), ("Bluetooth Speaker", 3499, 1700)],
    "Home and Kitchen": [("Mixer Grinder", 2999, 1550), ("Air Fryer", 5999, 3300), ("Water Bottle", 699, 250)],
    "Books": [("Data Engineering Handbook", 899, 350), ("Business Analytics", 749, 290), ("Productivity Journal", 399, 120)],
    "Fashion": [("Cotton Shirt", 1299, 500), ("Running Shoes", 2999, 1300), ("Backpack", 1599, 650)],
}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _month_starts() -> list[date]:
    return [date(year, month, 1) for year in (2025, 2026) for month in range(1, 13)]


def generate_sample_data(
    output_dir: Path,
    *,
    seed: int = 42,
    customer_count: int = 500,
    order_count: int = 2_400,
    include_quality_issues: bool = True,
) -> dict[str, Path]:
    """Generate fake customers, products, orders, items, and web events."""

    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    customers: list[dict[str, Any]] = []
    for index in range(1, customer_count + 1):
        region = rng.choice(list(REGIONS))
        city, state = rng.choice(REGIONS[region])
        customers.append(
            {
                "customer_id": f"C-{index:04d}",
                "customer_name": f"Customer {index:04d}",
                "email": f"customer{index:04d}@example.test",
                "city": city,
                "state": state,
                "country": "India",
                "region": region,
                "signup_date": (date(2024, 1, 1) + timedelta(days=rng.randrange(700))).isoformat(),
                "segment": rng.choices(["Standard", "Premium", "Business"], weights=[70, 23, 7])[0],
            }
        )

    products: list[dict[str, Any]] = []
    product_index = 1
    for category, items in CATEGORIES.items():
        for name, price, cost in items:
            for variant in range(1, 9):
                products.append(
                    {
                        "product_id": f"P-{product_index:04d}",
                        "product_name": f"{name} {variant}",
                        "category": category,
                        "subcategory": name,
                        "list_price": round(price * rng.uniform(0.9, 1.15), 2),
                        "unit_cost": round(cost * rng.uniform(0.9, 1.1), 2),
                        "supplier": f"Supplier {1 + product_index % 7}",
                    }
                )
                product_index += 1

    months = _month_starts()
    month_weights = [1.0] * len(months)
    month_weights[months.index(date(2026, 7, 1))] = 1.35
    month_weights[months.index(date(2026, 8, 1))] = 0.72
    orders: list[dict[str, Any]] = []
    order_items: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for order_number in range(1, order_count + 1):
        month = rng.choices(months, weights=month_weights, k=1)[0]
        order_date = month + timedelta(days=rng.randrange(28))
        customer = rng.choice(customers)
        region = customer["region"]
        if month == date(2026, 8, 1) and rng.random() < 0.45:
            region = rng.choice(["West", "South", "East"])
        order_id = f"ORD-{order_number:06d}"
        orders.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_date": order_date.isoformat(),
                "status": rng.choices(["completed", "cancelled", "refunded"], weights=[92, 5, 3])[0],
                "payment_method": rng.choice(["card", "upi", "wallet", "cash_on_delivery"]),
                "region": region,
                "marketing_channel": rng.choice(["organic", "paid_search", "email", "social", "referral"]),
            }
        )
        selectable_products = products
        if month == date(2026, 8, 1):
            selectable_products = [product for product in products if product["category"] != "Electronics"] * 2 + products
        for line_number in range(1, rng.randint(1, 4) + 1):
            product = rng.choice(selectable_products)
            unit_price = round(float(product["list_price"]) * rng.uniform(0.88, 1.0), 2)
            order_items.append(
                {
                    "order_item_id": f"{order_id}-{line_number}",
                    "order_id": order_id,
                    "product_id": product["product_id"],
                    "quantity": rng.randint(1, 4),
                    "unit_price": unit_price,
                    "discount_amount": round(unit_price * rng.choice([0, 0, 0.05, 0.1]), 2),
                }
            )
        events.append(
            {
                "event_id": f"EVT-{order_number:06d}",
                "customer_id": customer["customer_id"],
                "timestamp": datetime.combine(order_date, datetime.min.time()).isoformat() + "Z",
                "event_type": "purchase",
                "page": "/checkout",
                "device": rng.choice(["mobile", "desktop", "tablet"]),
            }
        )

    if include_quality_issues:
        orders.extend(
            [
                {**orders[0]},
                {**orders[1], "order_id": "ORD-BAD-0001", "customer_id": "C-9999"},
            ]
        )
        order_items.extend(
            [
                {**order_items[0], "order_item_id": "BAD-ITEM-0001", "quantity": -2},
                {**order_items[1], "order_item_id": "BAD-ITEM-0002", "product_id": "P-9999"},
            ]
        )

    paths = {
        "customers": output_dir / "customers.csv",
        "products": output_dir / "products.csv",
        "orders": output_dir / "orders.csv",
        "order_items": output_dir / "order_items.csv",
        "website_events": output_dir / "website_events.json",
        "campaigns_api_snapshot": output_dir / "campaigns_api_snapshot.json",
    }
    _write_csv(paths["customers"], customers)
    _write_csv(paths["products"], products)
    _write_csv(paths["orders"], orders)
    _write_csv(paths["order_items"], order_items)
    paths["website_events"].write_text(json.dumps(events, indent=2), encoding="utf-8")
    campaigns = [
        {"campaign_id": "CMP-001", "name": "Monsoon Electronics", "channel": "paid_search", "budget": 100000},
        {"campaign_id": "CMP-002", "name": "Festival Home", "channel": "social", "budget": 75000},
    ]
    paths["campaigns_api_snapshot"].write_text(json.dumps(campaigns, indent=2), encoding="utf-8")
    return paths

