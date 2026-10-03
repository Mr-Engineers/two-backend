"""Idempotently seed ``warehouse.products`` / ``warehouse.stock_levels`` with the SKUs of the shared catalog.

The first two rows are the contract data (docs/contracts/warehouse-api.md); the rest matches the SKUs of the
marketplace ``extended_catalog`` scenario, with a mix of items below and above their reorder threshold.
Existing rows are never modified (``ON CONFLICT DO NOTHING``). Uses the owner account ``MIGRATION_DATABASE_URL``.

    python scripts/seed_warehouse.py --env-file .env [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from dotenv import dotenv_values

# sku, name, unit, on_hand, reorder_threshold, target_level, max_order_qty
ROWS = [
    ("PAP-A4-80", "Papier A4 80 g/m², karton 5 ryz", "karton", 12, 20, 50, 500),
    ("TON-HP-59A", "Toner HP 59A czarny (CF259A)", "szt", 1, 2, 5, 20),
    ("PAP-A3-80", "Papier A3 80 g/m², ryza 500 arkuszy", "ryza", 30, 20, 60, 300),
    ("PAP-A4-COL", "Papier kolorowy A4 80 g/m², mix pastelowy, 250 arkuszy", "ryza", 8, 15, 40, 150),
    ("TON-HP-59X", "Toner HP 59X czarny wysokowydajny (CF259X)", "szt", 0, 1, 4, 15),
    ("TON-BRO-2420", "Toner Brother TN-2420 czarny", "szt", 3, 2, 6, 20),
    ("PEN-BLK-10", "Długopisy czarne, 10 sztuk", "opak", 40, 30, 120, 600),
    ("NTB-A5-80", "Notes A5 w kratkę, 80 kartek", "szt", 25, 40, 100, 500),
    ("BND-A4-50", "Segregator A4 50 mm", "szt", 18, 10, 40, 200),
    ("ENV-DL-50", "Koperty DL białe samoklejące, 50 sztuk", "opak", 6, 10, 30, 200),
    ("TAP-19-33", "Taśma klejąca przezroczysta 19 mm x 33 m", "szt", 60, 24, 72, 400),
    ("STP-24-6-1000", "Zszywki 24/6, 1000 sztuk", "opak", 9, 12, 36, 300),
    ("MRK-WB-4", "Markery do tablic suchościeralnych, 4 kolory", "opak", 35, 20, 80, 300),
    ("HLG-4", "Zakreślacze fluorescencyjne, 4 kolory", "opak", 28, 15, 60, 300),
    ("NTE-76-YEL", "Karteczki samoprzylepne 76x76 mm, żółte, 100 kartek", "szt", 50, 30, 120, 600),
    ("CLP-28-100", "Spinacze biurowe 28 mm, 100 sztuk", "opak", 45, 25, 100, 500),
    ("FLD-A4-100", "Koszulki na dokumenty A4, 100 sztuk", "opak", 14, 20, 60, 300),
    ("TON-CAN-045", "Toner Canon CRG-045 czarny", "szt", 4, 2, 6, 20),
    ("INK-HP-305", "Tusz HP 305 czarny (3YM61AE)", "szt", 6, 4, 12, 60),
    ("PAP-A4-REC", "Papier A4 recykling 80 g/m², ryza 500 arkuszy", "ryza", 40, 30, 100, 600),
    ("STPLR-25", "Zszywacz biurowy do 25 kartek", "szt", 12, 6, 20, 100),
    ("LBL-A4-105", "Etykiety samoprzylepne A4 105x42 mm, 100 arkuszy", "opak", 9, 8, 24, 100),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        for row in ROWS:
            print(row)
        return 0

    import psycopg

    url = dotenv_values(args.env_file).get("MIGRATION_DATABASE_URL")
    if not url:
        print(f"MIGRATION_DATABASE_URL missing in {args.env_file}", file=sys.stderr)
        return 2
    added = 0
    with psycopg.connect(url, connect_timeout=15) as conn, conn.cursor() as cur:
        for sku, name, unit, on_hand, threshold, target, max_qty in ROWS:
            cur.execute(
                "INSERT INTO warehouse.products (sku, name, unit, reorder_threshold, target_level, max_order_qty)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sku) DO NOTHING",
                (sku, name, unit, threshold, target, max_qty),
            )
            cur.execute(
                "INSERT INTO warehouse.stock_levels (sku, on_hand) VALUES (%s, %s)"
                " ON CONFLICT (sku) DO NOTHING RETURNING sku",
                (sku, on_hand),
            )
            if cur.fetchone():
                added += 1
                cur.execute(
                    "INSERT INTO warehouse.stock_movements (sku, movement_type, delta, on_hand_after, reason, actor)"
                    " VALUES (%s, 'scenario_reset', 0, %s, 'seed_warehouse.py initial stock', 'seed')",
                    (sku, on_hand),
                )
        cur.execute("SELECT count(*) FROM warehouse.low_stock")
        low = cur.fetchone()[0]
    print(f"added {added} SKU(s); {low} currently in warehouse.low_stock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
