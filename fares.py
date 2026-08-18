"""Parse fare tariffs from the Transurb website and write GTFS Fares v2 files.

Source: https://www.transurbgalati.ro/altele/titluri_calatorie/tarife

The tariff page lists single-ride ticket prices by payment method (table 0)
and by geographic zone (tables 3 and 5). This module parses both and produces:

    fare_media.txt          — payment methods (card, apps, SMS)
    fare_products.txt       — products with prices per medium
    fare_leg_rules.txt      — which products apply to which network
    fare_transfer_rules.txt — free transfers within the validity window
    networks.txt            — route network definitions
    route_networks.txt      — assignment of routes to networks

A route's network comes from the `area` column of route-colors.txt: "urban"
(or empty) for the city network, otherwise the id of one of the extraurban
zones below, so the palette stays the only place a route's zone is written
down.
"""

import re


TARIFE_URL = "https://www.transurbgalati.ro/altele/titluri_calatorie/tarife"

# The extraurban fare zones the tariff page prices in its own columns, keyed by
# the network_id used in the feed. "match" is matched against the column header
# of tables 3 and 5, lowercased.
ZONE_SPECS = {
    "costi": {"match": "costi", "name": "Sat Costi – Galați"},
    "odaia": {"match": "odaia", "name": "Odaia Manolache – Galați"},
}


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
        "zones" : dict
            One entry per extraurban zone in ZONE_SPECS that the page prices,
            keyed by network_id, each with "name", "duration" (minutes) and
            "media" in the same shape as the urban list above.
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

    result = {"media": media_list, "duration": duration,
              "zones": _parse_zones(tables, media_list)}
    return result


def _zone_columns(header_row: str) -> dict[str, int]:
    """Map network_id -> column index, from a zone table's header row."""
    cells = [_strip_tags(c) for c in
             re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", header_row, re.DOTALL)]
    columns = {}
    for zone_id, spec in ZONE_SPECS.items():
        for i, cell in enumerate(cells):
            if spec["match"] in cell.lower():
                columns[zone_id] = i
                break
    return columns


def _zone_rows(table: str) -> tuple[dict[str, int], list[list[str]]]:
    """A zone table's column mapping and its body rows as stripped cells."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)
    if not rows:
        return {}, []
    columns = _zone_columns(rows[0])
    body = [[_strip_tags(c) for c in
             re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL)]
            for r in rows[1:]]
    return columns, body


def _parse_zones(tables: list[str], urban_media: list[dict]) -> dict:
    """Single-ride price and validity per extraurban zone.

    Table 3 gives the ticket price per zone in a "Bilet 60 minute" / "Bilet 90
    minute" row, which is also where the zone's validity window comes from.
    Table 5 prices the app and SMS media per zone. A zone whose columns hold no
    price ("-" or "–") is not returned, so a zone the page stops pricing simply
    disappears instead of being emitted at the urban price.
    """
    if len(tables) < 6:
        return {}
    price_columns, price_rows = _zone_rows(tables[3])
    media_columns, media_rows = _zone_rows(tables[5])
    # The medium each row of table 5 prices, in the same order as the page.
    sms = next((m for m in urban_media if m["id"] == "sms_24pay"), None)
    app_tg = next((m for m in urban_media if m["id"] == "app_tg"), None)
    app_24pay = next((m for m in urban_media if m["id"] == "app_24pay"), None)

    zones = {}
    for zone_id, spec in ZONE_SPECS.items():
        col = price_columns.get(zone_id)
        if col is None:
            continue
        ticket = None
        for cells in price_rows:
            if len(cells) <= col or not cells[0].lower().startswith("bilet"):
                continue
            m = re.search(r"(\d+)\s*minute", cells[0], re.IGNORECASE)
            try:
                amount, currency = _parse_price(cells[col])
            except ValueError:
                continue  # "-" / "–": this zone has no ticket of that duration
            ticket = (amount, currency, int(m.group(1)) if m else 60)
            break
        if ticket is None:
            continue
        amount, currency, duration = ticket
        product_id = f"{zone_id}_ride"
        media = []
        if any(m["id"] == "transport_card" for m in urban_media):
            media.append({"id": "transport_card", "name": "Card transport",
                          "type": 2, "product_id": product_id,
                          "product_name": f"Bilet 1 călătorie {spec['name']}",
                          "amount": amount, "currency": currency})
        # Table 5: the app rows are priced per zone, the SMS row separately
        # because it is a different product (and in EUR).
        mcol = media_columns.get(zone_id)
        for cells in media_rows if mcol is not None else []:
            if len(cells) <= mcol:
                continue
            label = cells[0].lower()
            try:
                zamount, zcurrency = _parse_price(cells[mcol])
            except ValueError:
                continue
            if label.startswith("sms") and sms:
                media.append({**sms, "product_id": f"{zone_id}_ride_sms",
                              "product_name":
                                  f"Bilet 1 călătorie SMS {spec['name']}",
                              "amount": zamount, "currency": zcurrency})
            elif "transport galați" in label:
                for m in (app_tg, app_24pay):
                    if m:
                        media.append({**m, "product_id": product_id,
                                      "product_name":
                                          f"Bilet 1 călătorie {spec['name']}",
                                      "amount": zamount,
                                      "currency": zcurrency})
        zones[zone_id] = {"name": spec["name"], "duration": duration,
                          "media": media}
    return zones


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
    zones = fares.get("zones", {})

    # A route's zone comes from the palette's area column: "urban" (or empty)
    # for the city network, otherwise the network_id of a priced zone.
    networks = {"urban": {"name": "Rețeaua urbană Galați",
                          "media": fares["media"],
                          "duration": fares["duration"]}}
    for zone_id, zone in zones.items():
        networks[zone_id] = zone

    route_networks = []
    for rid in sorted_route_ids:
        area = palette.get(rid, {}).get("area") or "urban"
        if area == "urban":
            route_networks.append(["urban", rid])
        elif area in networks:
            route_networks.append([area, rid])
        else:
            print(f"  warning: route {rid} is area {area!r}, which the tariff "
                  f"page does not price; it gets no fare")

    used_networks = {n for n, _ in route_networks}

    wcsv("networks.txt",
         ["network_id", "network_name"],
         [[nid, networks[nid]["name"]] for nid in networks
          if nid in used_networks])

    wcsv("route_networks.txt",
         ["network_id", "route_id"],
         route_networks)

    # fare_media.txt is network-independent: the same card, apps and SMS pay
    # for every zone, only the price differs.
    seen_media = set()
    media_rows = []
    for nid in networks:
        if nid not in used_networks:
            continue
        for m in networks[nid]["media"]:
            if m["id"] not in seen_media:
                seen_media.add(m["id"])
                media_rows.append([m["id"], m["name"], m["type"]])

    wcsv("fare_media.txt",
         ["fare_media_id", "fare_media_name", "fare_media_type"],
         media_rows)

    # Deduplicate product ids — multiple media can share the same product
    seen_products = set()
    product_rows = []
    leg_rule_rows = []
    seen_leg_rules = set()
    for nid in networks:
        if nid not in used_networks:
            continue
        for m in networks[nid]["media"]:
            key = (m["product_id"], m["id"])
            if key not in seen_products:
                seen_products.add(key)
                product_rows.append([m["product_id"], m["product_name"],
                                     m["id"], m["amount"], m["currency"]])
            pid = m["product_id"]
            if (nid, pid) not in seen_leg_rules:
                seen_leg_rules.add((nid, pid))
                # leg_group_id groups a network's legs so that
                # fare_transfer_rules can reference them; it is per network,
                # since a transfer's price window depends on the zone, not on
                # which medium paid for the leg.
                leg_rule_rows.append([f"{nid}_ride", nid, pid])

    wcsv("fare_products.txt",
         ["fare_product_id", "fare_product_name", "fare_media_id", "amount", "currency"],
         product_rows)

    wcsv("fare_leg_rules.txt",
         ["leg_group_id", "network_id", "fare_product_id"],
         leg_rule_rows)

    # Transfers are free within the validity window. The window differs per
    # zone (60 or 90 min), so a transfer between two networks gets the longer
    # of the two, and each pair of networks gets a rule of its own.
    #
    # transfer_count is conditionally forbidden/required: it applies only when
    # from and to are the same leg group, where -1 means an unlimited number of
    # transfers inside the window.
    transfer_rows = []
    for nid in networks:
        if nid not in used_networks:
            continue
        for other in networks:
            if other not in used_networks:
                continue
            window = max(networks[nid]["duration"], networks[other]["duration"])
            transfer_rows.append([f"{nid}_ride", f"{other}_ride",
                                  -1 if nid == other else "", 0,
                                  window * 60, 1])

    wcsv("fare_transfer_rules.txt",
         ["from_leg_group_id", "to_leg_group_id", "transfer_count",
          "fare_transfer_type", "duration_limit", "duration_limit_type"],
         transfer_rows)
