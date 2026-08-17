"""Parse fare tariffs from the Transurb website and write GTFS Fares v2 files.

Source: https://www.transurbgalati.ro/altele/titluri_calatorie/tarife

The tariff page lists single-ride ticket prices by payment method (table 0)
and by geographic zone (table 3). This module parses both and produces:

    fare_media.txt          — payment methods (card, apps, SMS)
    fare_products.txt       — products with prices per medium
    fare_leg_rules.txt      — which products apply to which network
    fare_transfer_rules.txt — free transfers within the validity window
    networks.txt            — route network definitions
    route_networks.txt      — assignment of routes to networks
"""

import re


TARIFE_URL = "https://www.transurbgalati.ro/altele/titluri_calatorie/tarife"


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _parse_price(text: str) -> tuple[str, str]:
    """Parse '3,50 LEI' or '0,84 EUR + TVA' into ('3.50', 'RON') or ('0.84', 'EUR')."""
    text = text.strip()
    m = re.match(r"([\d]+[,.][\d]+)\s*(LEI|EUR)", text, re.IGNORECASE)
    if not m:
        # Try integer price like '5 LEI'
        m = re.match(r"(\d+)\s*(LEI|EUR)", text, re.IGNORECASE)
        if not m:
            raise ValueError(f"cannot parse price from: {text!r}")
        amount = m.group(1) + ".00"
    else:
        amount = m.group(1).replace(",", ".")
    currency = "RON" if m.group(2).upper() == "LEI" else "EUR"
    return amount, currency


def _strip_tags(s: str) -> str:
    """Remove HTML tags, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_fares(page_html: str) -> dict:
    """Parse the tariff page HTML, returning fare data for the urban zone.

    Parameters
    ----------
    page_html : str
        The full HTML content of the tariff page.

    Returns
    -------
    dict with keys:
        "media" : list of dicts, each with:
            "id"           — fare_media_id (e.g. "transport_card")
            "name"         — fare_media_name (e.g. "Card transport")
            "type"         — fare_media_type (2=cEMV, 3=account-based)
            "product_id"   — fare_product_id this medium contributes to
            "product_name" — fare_product_name
            "amount"       — price as string (e.g. "3.50")
            "currency"     — "RON" or "EUR"
        "duration" : int
            Validity window in minutes for the urban zone.
    """
    # Extract all <table> elements in document order.
    tables = re.findall(r"<table[^>]*>(.*?)</table>", page_html, re.DOTALL)
    if len(tables) < 4:
        raise RuntimeError(
            f"tariff page has {len(tables)} tables, expected at least 4; "
            f"page structure may have changed")

    # Table 0: "Tarife bilete" — urban single-ride fares.
    # Each row defines a fare medium and its price:
    #   Row 0: "Bilet 1 călătorie – durată 60 minute" (transport card)
    #   Row 1: "... prin aplicația Transport Galați (plată cu cardul) ..."
    #   Row 2: "... prin aplicația 24Pay (plată cu cardul) ..."
    #   Row 3: "... prin aplicația 24Pay (plată prin SMS) ..."
    rows_0 = re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.DOTALL)
    media_list = []
    for row_html in rows_0:
        cells = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
        if len(cells) < 2:
            continue
        desc, price_text = cells[0], cells[1]
        try:
            amount, currency = _parse_price(price_text)
        except ValueError:
            continue
        desc_lower = desc.lower()
        if "transport galați" in desc_lower:
            media_list.append({
                "id": "app_tg", "name": "Aplicația Transport Galați", "type": 3,
                "product_id": "urban_ride",
                "product_name": "Bilet 1 călătorie",
                "amount": amount, "currency": currency,
            })
        elif "24pay" in desc_lower and "sms" in desc_lower:
            media_list.append({
                "id": "sms_24pay", "name": "24Pay SMS", "type": 3,
                "product_id": "urban_ride_sms",
                "product_name": "Bilet 1 călătorie SMS",
                "amount": amount, "currency": currency,
            })
        elif "24pay" in desc_lower:
            media_list.append({
                "id": "app_24pay", "name": "Aplicația 24Pay", "type": 3,
                "product_id": "urban_ride",
                "product_name": "Bilet 1 călătorie",
                "amount": amount, "currency": currency,
            })
        elif "călătorie" in desc_lower and "60 minute" in desc_lower:
            # Generic ticket = transport card (cEMV, validated on board)
            media_list.append({
                "id": "transport_card", "name": "Card transport", "type": 2,
                "product_id": "urban_ride",
                "product_name": "Bilet 1 călătorie",
                "amount": amount, "currency": currency,
            })

    if not media_list:
        raise RuntimeError(
            "tariff page Table 0 yielded no fare media; page structure may have changed")

    # Table 3: "Bilete (60/90 minute)" — zone durations.
    rows_3 = re.findall(r"<tr[^>]*>(.*?)</tr>", tables[3], re.DOTALL)
    duration = 60  # default
    for row_html in rows_3:
        cells = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
        if len(cells) >= 2 and "60 minute" in cells[0].lower():
            m = re.search(r"(\d+)\s*minute", cells[0], re.IGNORECASE)
            if m:
                duration = int(m.group(1))
            break

    result = {"media": media_list, "duration": duration}
    return result


def write_fares(fares: dict, sorted_route_ids: list[str], palette: dict,
                wcsv) -> None:
    """Write all GTFS Fares v2 files using the parsed fare data.

    Parameters
    ----------
    fares : dict
        Output of fetch_fares().
    sorted_route_ids : list[str]
        Route ids that were successfully built, in output order.
    palette : dict
        Route colour palette (ref -> {"color", "vehicle", "area"}).
    wcsv : callable
        The CSV writer function: wcsv(filename, header, rows).
    """
    duration_sec = fares["duration"] * 60
    media_list = fares["media"]

    # Which network does each built route belong to?
    route_networks = []
    for rid in sorted_route_ids:
        area = palette.get(rid, {}).get("area", "urban")
        if area == "extraurban":
            # Once routes 50/55 are added, assign them to network "costi" or
            # "odaia" based on their actual destination. Until then, skip.
            pass
        else:
            route_networks.append(["urban", rid])

    wcsv("networks.txt",
         ["network_id", "network_name"],
         [["urban", "Rețeaua urbană Galați"]])

    wcsv("route_networks.txt",
         ["network_id", "route_id"],
         route_networks)

    wcsv("fare_media.txt",
         ["fare_media_id", "fare_media_name", "fare_media_type"],
         [[m["id"], m["name"], m["type"]] for m in media_list])

    # Deduplicate product ids — multiple media can share the same product
    seen_products = set()
    product_rows = []
    for m in media_list:
        key = (m["product_id"], m["id"])
        if key not in seen_products:
            seen_products.add(key)
            product_rows.append([m["product_id"], m["product_name"],
                                 m["id"], m["amount"], m["currency"]])

    wcsv("fare_products.txt",
         ["fare_product_id", "fare_product_name", "fare_media_id", "amount", "currency"],
         product_rows)

    # Leg rules: one per unique product_id
    seen_leg_rules = set()
    leg_rule_rows = []
    for m in media_list:
        pid = m["product_id"]
        if pid not in seen_leg_rules:
            seen_leg_rules.add(pid)
            leg_rule_rows.append([f"urban_{pid}", "urban", pid])

    wcsv("fare_leg_rules.txt",
         ["fare_leg_rule_id", "network_id", "fare_product_id"],
         leg_rule_rows)

    wcsv("fare_transfer_rules.txt",
         ["from_leg_group_id", "to_leg_group_id", "fare_transfer_type",
          "duration_limit", "duration_limit_type"],
         [["", "", 0, duration_sec, 1]])
