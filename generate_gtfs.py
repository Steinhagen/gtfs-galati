"""Generate a GTFS feed for TRANSURB S.A. Galați (transurbgalati.ro).

Two upstream sources, no route data duplicated in this file:

* OpenStreetMap route relations describe the stops: the ordered platform
  members of a relation are the direction's stop sequence, and each platform
  node carries the stop name and position. The relation's ways are the shape,
  and its tags give the vehicle type, the termini and the headsigns.
* The Transurb website gives the timetables. The route page ("veziTraseu")
  lists the station names in travel order, which is what the timetable pages
  ("veziProgram") are keyed on.

The two are cross-checked per direction: same number of stops, in the same
order. Anything that does not line up, and anything missing or inconsistent in
OSM, is reported at the end of the build as an OSM issue to fix upstream
instead of being patched over here.

Usage:
    python3 generate_gtfs.py            # build feed for all configured routes
    python3 generate_gtfs.py 102        # build feed for route 102 only
    python3 generate_gtfs.py --refresh  # re-fetch OSM relations and route pages
    python3 generate_gtfs.py --help
"""

import argparse
import html
import json
import math
import re
import sys
import time
import unicodedata
import zipfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Fare tariffs from the Transurb website
# ---------------------------------------------------------------------------
from fares import TARIFE_URL, fetch_fares, write_fares


BASE_URL = "https://transurbgalati.ro/program_circulatie"
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
OUT_DIR = HERE / "gtfs_transurb"
ZIP_PATH = HERE / "gtfs_transurb.zip"
COLORS_FILE = HERE / "route-colors.txt"
UA = "gtfs-galati/1.0"  # Overpass rejects Mozilla-prefixed custom UAs

# ---------------------------------------------------------------------------
# Who publishes this feed, and who to tell about a problem in it.
#
# The feed maps the TRANSURB S.A. Galați network, but it is not their dataset:
# it is produced independently from their published timetables plus OSM, and
# they do not maintain it. So agency.txt keeps naming Transurb (they do run the
# service, and agency_* is the rider-facing contact for the service itself),
# while feed_info.txt names the feed's publisher and feed_contact_* points at
# the person who can actually fix the data. attributions.txt then states both
# roles explicitly: Transurb as operator, the publisher below as producer.
FEED_PUBLISHER = "Viorel-Cătălin Răpițeanu"
FEED_PUBLISHER_URL = "https://github.com/Steinhagen/gtfs-galati"
FEED_CONTACT_EMAIL = "rapiteanu.catalin@gmail.com"
FEED_CONTACT_URL = "https://github.com/Steinhagen/gtfs-galati/issues"
OPERATOR_NAME = "TRANSURB S.A. Galati"
OPERATOR_URL = "https://transurbgalati.ro"

# The site's name for a route page's default station list; other names are
# alternative itineraries of the same route ("variantaStatii").
STANDARD_VARIANT = "standard"

FEED_START, FEED_END = "20260101", "20261231"
# Romanian legal holidays in 2026 that follow the weekend schedule.
HOLIDAYS_2026 = ["20260101", "20260106", "20260107", "20260410", "20260413",
                 "20260501", "20260601", "20261130", "20261201", "20261225"]

# ---------------------------------------------------------------------------
# Route configuration: the OSM route relation per direction, and the few
# things OSM does not know (which days a route runs, when it is not the
# Monday-Friday default).
#
# Adding a route means adding its relation ids here. Stops, stop names, stop
# positions, shapes, vehicle type, termini and headsigns all come from the
# relation; the station names used to fetch timetables come from the Transurb
# route page.
#
# A route whose page carries more than one station list ("variantaStatii") is
# configured with "variants" instead of "relations": one entry per itinerary,
# keyed by the variant name the site uses, each with its own relations and the
# services it actually runs ("services": the subset of ("WD", "WE") to read from
# that variant's timetable). Route 11 is the only such route: a Monday-Friday
# itinerary to Piața Centrală and a weekend one to Grădina Publică, which OSM
# tags opening_hours=Mo-Fr and Sa-Su respectively.
# ---------------------------------------------------------------------------
ROUTES = {
    "102": {"relations": {"TUR": 7514198, "RETUR": 309380}},
    "106": {"relations": {"TUR": 21211344, "RETUR": 21211343}},
    "105": {"relations": {"TUR": 10177285, "RETUR": 10177284}},
    "43": {"relations": {"TUR": 21213681, "RETUR": 21213682}},
    "41": {"relations": {"TUR": 21214588, "RETUR": 21214510}},
    "38": {"relations": {"TUR": 21216887}},
    "37": {"relations": {"TUR": 21217269, "RETUR": 21217291}},
    "35": {"relations": {"TUR": 21217560, "RETUR": 21217559}},
    "26": {"relations": {"TUR": 10278404, "RETUR": 10278471}},
    "28": {"relations": {"TUR": 10172140, "RETUR": 10173216}},
    "24": {"relations": {"TUR": 10281326, "RETUR": 10279032}},
    "9": {"relations": {"TUR": 309379, "RETUR": 10154626}},
    "10": {"relations": {"TUR": 358092, "RETUR": 10176664}},
    "31": {"relations": {"TUR": 21222269, "RETUR": 21222268}},
    "32": {"relations": {"TUR": 21223845, "RETUR": 21223844},
           "service_days": "TF"},  # Tuesday-Friday (no Monday service)
    "33": {"relations": {"TUR": 21222431, "RETUR": 21222473},
           "service_days": "TF"},
    "34": {"relations": {"TUR": 10188176, "RETUR": 10188475}},
    "30": {"relations": {"TUR": 21226359, "RETUR": 21226358}},
    "39": {"relations": {"TUR": 309018, "RETUR": 16337135}},
    "7": {"relations": {"TUR": 16337327, "RETUR": 16337326}},
    "44": {"relations": {"TUR": 16337536, "RETUR": 16337534}},
    "39B": {"relations": {"TUR": 21240464, "RETUR": 21240463}},
    "55": {"relations": {"TUR": 21243592, "RETUR": 21243593}},
    # The site's TUR runs Din Vale -> Piața centrală, which is r21250647; the
    # relation names are the other way round from the ids' numeric order.
    "50": {"relations": {"TUR": 21250647, "RETUR": 21244075}},
    # Route 13 has two northern termini on one itinerary: 20 of its 26 trips
    # turn back at Aleea Nordului and 6 carry on to Agrogal, so neither end
    # station lists every trip and the timetable is keyed off the busiest stop
    # instead (see align_times). The site's TUR is Piața Centrală -> Agrogal.
    "13": {"relations": {"TUR": 21252930, "RETUR": 21253037}},
    # Route 11 runs two itineraries, one per service period. The Piața Centrală
    # one is Monday-Friday only: the site still prints a weekend column on its
    # pages, but those weekend departures are the Grădina Publică trips listed
    # a second time (see WEEKEND_ONLY_VARIANTS below), so only "WD" is read
    # from it. The Grădina Publică variant is weekend/holiday only.
    "11": {"variants": {
        STANDARD_VARIANT: {
            "relations": {"TUR": 10177466, "RETUR": 10179043},
            "services": ("WD",)},
        "Sâmbătă, duminică și sărbători legale către Grădina Publică": {
            "relations": {"TUR": 21251489, "RETUR": 21251488},
            "services": ("WE",)},
    }},
}

# OSM route tag -> GTFS route_type, and the palette's vehicle column -> the
# same, so the two can be cross-checked.
OSM_ROUTE_TYPE = {"bus": 3, "trolleybus": 11, "tram": 0}
VEHICLE_ROUTE_TYPE = {"autobuz": 3, "troleibuz": 11, "tramvai": 0}

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

# ---------------------------------------------------------------------------
# Route palette: route-colors.txt holds one line per route,
# "ref,#rrggbb,vehicle,area". It is the source of truth for route_color;
# route_text_color is derived from the background luminance, so the palette is
# the only place a colour has to be edited.
# ---------------------------------------------------------------------------
# Perceived luminance (ITU-R BT.601) above which black text is used.
TEXT_COLOR_LUMA = 110.0
# Colour used for a route that is not in the palette file (and reported).
FALLBACK_COLOR = "808080"


def load_route_colors(path: Path = COLORS_FILE) -> dict[str, dict]:
    """Parse route-colors.txt into {ref: {"color": "RRGGBB", "vehicle", "area"}}."""
    palette = {}
    if not path.exists():
        print(f"warning: {path.name} not found; routes fall back to {FALLBACK_COLOR}")
        return palette
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", parts[1]):
            print(f"warning: ignoring unparsable {path.name} line: {line!r}")
            continue
        palette[parts[0]] = {"color": parts[1].lstrip("#").upper(),
                             "vehicle": parts[2] if len(parts) > 2 else "",
                             "area": parts[3] if len(parts) > 3 else ""}
    return palette


PALETTE = load_route_colors()


def text_color(background: str) -> str:
    """Readable text colour for a background: black on light, white on dark."""
    r, g, b = (int(background[i:i + 2], 16) for i in (0, 2, 4))
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return "000000" if luma > TEXT_COLOR_LUMA else "FFFFFF"


# ---------------------------------------------------------------------------
# Issue report: collected while building, printed at the end. "error" is for
# anything that makes the feed wrong or incomplete -- the build then fails so a
# bad feed is not published. "warning" is for data that is missing,
# inconsistent or self-contradictory without breaking the feed.
#
# Issues are grouped by the source that has to fix them: "osm" for tagging that
# belongs in OpenStreetMap, "site" for the Transurb website contradicting
# itself. Neither is worked around here beyond what is unavoidable.
# ---------------------------------------------------------------------------
ISSUES: list[tuple[str, str, str, str]] = []  # (severity, subject, message, source)


def issue(severity: str, subject: str, message: str, source: str = "osm") -> None:
    ISSUES.append((severity, subject, message, source))
    print(f"  {severity}: {subject}: {message}", flush=True)


class RouteDataError(Exception):
    """The OSM relation and the Transurb page do not describe the same route."""


def http_get(url: str, data: bytes | None = None, headers: dict | None = None,
             tries: int = 3, timeout: int = 60) -> bytes:
    """GET/POST with retries (the APIs used here are sometimes flaky)."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, data=data,
                                         headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt < tries:
                wait = 10 * attempt
                print(f"  rate limited (429); waiting {wait}s before retry", flush=True)
                time.sleep(wait)
            elif attempt < tries:
                print(f"  http error ({e}); retry {attempt}/{tries - 1} in {5 * attempt}s")
                time.sleep(5 * attempt)
        except Exception as e:
            last = e
            if attempt < tries:
                print(f"  http error ({e}); retry {attempt}/{tries - 1} in {5 * attempt}s")
                time.sleep(5 * attempt)
    raise last


REFRESH = False  # set from --refresh: ignore cached OSM relations and route pages


def fetch_page(url: str, cache_file: Path, refresh: bool = False) -> str:
    if cache_file.exists() and not refresh:
        return cache_file.read_text(encoding="utf-8")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = http_get(url).decode("utf-8", errors="replace")
    cache_file.write_text(data, encoding="utf-8")
    time.sleep(0.6)
    return data


# ---------------------------------------------------------------------------
# Transurb website
# ---------------------------------------------------------------------------


def site_stations(route_id: str) -> dict[str, list[str]]:
    """Station names per direction, in travel order, from the route page.

    These are the names the timetable pages are keyed on, so they are read
    from the site rather than written down here.

    A route page can carry more than one station list ("variantaStatii"): route
    11 has a separate weekend/holiday itinerary to Grădina Publică. All of them
    are returned, keyed by variant name, so a route configured with relations
    per variant can be built; a variant with no relations configured for it is
    reported and left out of the feed.
    """
    html_text = fetch_page(f"{BASE_URL}/veziTraseu?numarTraseu={route_id}",
                           CACHE_DIR / f"traseu_{route_id}.html", REFRESH)
    variants: dict[str, dict[str, list[str]]] = {}
    for m in re.finditer(r"veziProgram\?([^\"']+)", html_text):
        q = urllib.parse.parse_qs(html.unescape(m.group(1)))
        if "numeStatie" not in q or "turRetur" not in q:
            continue
        variant = q.get("variantaStatii", [STANDARD_VARIANT])[0]
        seq = variants.setdefault(variant, {"TUR": [], "RETUR": []})
        seq[q["turRetur"][0].upper()].append(q["numeStatie"][0])
    return variants or {STANDARD_VARIANT: {"TUR": [], "RETUR": []}}


def fetch_schedule(route: str, direction: str, station: str,
                   variant: str = STANDARD_VARIANT
                   ) -> tuple[list[str], list[str]]:
    """Return (weekday times, weekend times) for one station on the site."""
    query = urllib.parse.urlencode({
        "numarTraseu": route,
        "numeStatie": station,
        "turRetur": direction.lower(),
        "variantaStatii": variant,
    })
    slug_variant = "" if variant == STANDARD_VARIANT else f"_{slug(variant)[:40]}"
    cache = CACHE_DIR / f"{route}_{direction}_{station}{slug_variant}.html"
    html_text = fetch_page(f"{BASE_URL}/veziProgram?{query}", cache)
    m = re.search(r"DE \w+ PÂNĂ VINERI(.*?)WEEKEND ȘI SĂRBĂTORI LEGALE(.*?)</table>",
                  html_text, re.S)
    if m:
        return re.findall(r"(\d{2}:\d{2})", m.group(1)), re.findall(r"(\d{2}:\d{2})", m.group(2))
    # Weekday-only routes: no weekend section on the page
    m_wd = re.search(r"DE \w+ PÂNĂ VINERI(.*?)</table>", html_text, re.S)
    if m_wd:
        return re.findall(r"(\d{2}:\d{2})", m_wd.group(1)), []
    # Weekend-only itineraries (route 11's Grădina Publică variant): the page
    # carries the weekend table alone, with no Monday-Friday section at all.
    m_we = re.search(r"WEEKEND ȘI SĂRBĂTORI LEGALE(.*?)</table>", html_text, re.S)
    if m_we:
        return [], re.findall(r"(\d{2}:\d{2})", m_we.group(1))
    raise RuntimeError(f"could not parse timetable for route {route} {direction} {station}")


# ---------------------------------------------------------------------------
# OpenStreetMap
# ---------------------------------------------------------------------------
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

PLATFORM_ROLES = ("platform", "platform_entry_only", "platform_exit_only")

# Keys that record a second spelling of the same stop, read alongside `name`:
# short_name for an abbreviated form ('Bld. Dunărea' for 'Bulevardul Dunărea'),
# alt_name for a genuinely different name, official_name for the operator's
# form, loc_name for a colloquial one. A stop tagged with the spelling the
# Transurb page uses is not reported as a name mismatch, so the knowledge lives
# in OSM instead of in a lookup table here.
ALT_NAME_KEYS = ("short_name", "alt_name", "official_name", "loc_name")

# Bumped when a cached relation payload gains a field, so that caches written
# by an older build are refetched instead of silently missing it.
RELATION_SCHEMA = 2


def alt_names(tags: dict) -> list[str]:
    """The stop's alternative spellings, semicolon-separated values split out."""
    return [v.strip() for key in ALT_NAME_KEYS
            for v in (tags.get(key) or "").split(";") if v.strip()]


def osm_api_relation(rel_id: int) -> dict:
    """Relation tags, ordered platforms and way geometry from the OSM API.

    /full is used because it returns the member nodes with their tags, which
    is where the stop names live; Overpass "out geom" does not include them.
    """
    print(f"    osm api: relation {rel_id}/full...", flush=True)
    xml = http_get(f"https://api.openstreetmap.org/api/0.6/relation/{rel_id}/full",
                   tries=3).decode("utf-8", "replace")
    root = ET.fromstring(xml)
    nodes, ways = {}, {}
    for el in root:
        if el.tag == "node":
            nodes[int(el.get("id"))] = {
                "lat": float(el.get("lat")), "lon": float(el.get("lon")),
                "tags": {t.get("k"): t.get("v") for t in el.findall("tag")}}
        elif el.tag == "way":
            ways[int(el.get("id"))] = [int(nd.get("ref")) for nd in el.findall("nd")]
    rel = next(el for el in root
               if el.tag == "relation" and int(el.get("id")) == rel_id)
    data = {"id": rel_id,
            "tags": {t.get("k"): t.get("v") for t in rel.findall("tag")},
            "platforms": [], "ways": []}
    for m in rel.findall("member"):
        typ, ref, role = m.get("type"), int(m.get("ref")), m.get("role") or ""
        if role in PLATFORM_ROLES:
            if typ != "node":
                data["platforms"].append({"node": ref, "type": typ,
                                          "name": None, "alt_names": [],
                                          "lat": None, "lon": None})
                continue
            n = nodes.get(ref, {})
            data["platforms"].append({"node": ref, "type": "node",
                                      "name": n.get("tags", {}).get("name"),
                                      "alt_names": alt_names(n.get("tags", {})),
                                      "lat": n.get("lat"), "lon": n.get("lon")})
        elif typ == "way" and not role:
            geom = [[nodes[n]["lat"], nodes[n]["lon"]] for n in ways.get(ref, [])
                    if n in nodes]
            if len(geom) >= 2:
                data["ways"].append(geom)
    return data


def overpass_relation(rel_id: int) -> dict:
    """Fallback source: relation geometry plus member node tags via Overpass."""
    query = (f"[out:json][timeout:120];rel({rel_id})->.r;.r out geom;"
             f"node(r.r);out tags;")
    last = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            print(f"    overpass: relation {rel_id} via {endpoint.split('/')[2]}...",
                  flush=True)
            raw = http_get(endpoint,
                           data=urllib.parse.urlencode({"data": query}).encode(),
                           tries=2, timeout=120)
            j = json.loads(raw)
            time.sleep(1)
            break
        except Exception as e:
            last = e
            print(f"    overpass mirror {endpoint} failed ({e})", flush=True)
    else:
        raise last
    node_tags = {e["id"]: e.get("tags", {}) for e in j["elements"]
                 if e["type"] == "node"}
    rel = next(e for e in j["elements"] if e["type"] == "relation")
    data = {"id": rel_id, "tags": rel.get("tags", {}), "platforms": [], "ways": []}
    for m in rel.get("members", []):
        role = m.get("role") or ""
        if role in PLATFORM_ROLES:
            data["platforms"].append({
                "node": m["ref"], "type": m["type"],
                "name": node_tags.get(m["ref"], {}).get("name"),
                "alt_names": alt_names(node_tags.get(m["ref"], {})),
                "lat": m.get("lat"), "lon": m.get("lon")})
        elif m["type"] == "way" and not role and m.get("geometry"):
            geom = [[p["lat"], p["lon"]] for p in m["geometry"] if p]
            if len(geom) >= 2:
                data["ways"].append(geom)
    return data


def relation_data(rel_id: int) -> dict:
    """Cached relation payload: tags, ordered platforms, way geometry."""
    cache = CACHE_DIR / f"relation_{rel_id}.json"
    if cache.exists() and not REFRESH:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("schema") == RELATION_SCHEMA:
            return cached
    try:
        data = osm_api_relation(rel_id)
    except Exception as e:
        print(f"  osm api failed for relation {rel_id} ({e}); trying Overpass")
        data = overpass_relation(rel_id)
    data["schema"] = RELATION_SCHEMA
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def slug(text: str) -> str:
    """'Liceul „Sfânta Maria”' -> 'LICEUL-SFANTA-MARIA'."""
    ascii_text = "".join(c for c in unicodedata.normalize("NFD", text)
                         if not unicodedata.combining(c))
    return re.sub(r"-+", "-", re.sub(r"[^A-Z0-9]+", "-",
                                     ascii_text.upper())).strip("-")


def stop_id(name: str, node: int) -> str:
    """Stop id for an OSM platform: name slug plus the node id.

    The node id keeps the id stable and unique: a platform is a property of the
    physical stop, the same one can be used by several routes in either
    direction, and 72 of the platforms in this network share a name with the
    platform on the other side of the street.
    """
    return f"{slug(name)}-{node}"


def normalize_stop_name(name: str) -> str:
    """Loose form used to compare a site station name with an OSM stop name."""
    ascii_name = "".join(c for c in unicodedata.normalize("NFD", name.lower())
                         if not unicodedata.combining(c))
    ascii_name = re.sub(r"\bstr\b|\bstrada\b", "", ascii_name)
    ascii_name = re.sub(r"\bbld\b|\bbd\b|\bbulevardul\b", "", ascii_name)
    ascii_name = re.sub(r"\b(piata|complex|cimitirul|cimitir|liceul|liceu)\b", "",
                        ascii_name)
    return re.sub(r"[^a-z0-9]+", " ", ascii_name).strip()


def names_agree(site_name: str, osm_name: str, osm_alt: list[str] = ()) -> bool:
    """True when the OSM name, or any spelling recorded next to it, matches.

    A stop tagged `short_name`/`alt_name`/`official_name`/`loc_name` with the
    form the Transurb page prints counts as agreement: the two sources then do
    know each other's name, they just print different ones.
    """
    a = normalize_stop_name(site_name)
    if not a:
        return False
    for candidate in (osm_name, *osm_alt):
        b = normalize_stop_name(candidate or "")
        if not b:
            continue
        if a == b or a in b or b in a:
            return True
        if set(a.split()) & set(b.split()):
            return True
    return False


def direction_stops(route_id: str, direction: str, rel_id: int,
                    stations: list[str]) -> list[dict]:
    """The direction's stops: OSM platforms paired with the site's stations.

    OSM gives the stop (id, name, position); the site gives the station name
    its timetable is keyed on. The relation and the route page must describe
    the same sequence, otherwise one of the two is out of date and the build
    stops instead of guessing.
    """
    data = relation_data(rel_id)
    platforms = data["platforms"]
    if not platforms:
        raise RouteDataError(
            f"relation {rel_id} has no platform members, so it does not "
            f"describe the {len(stations)} stops of route {route_id} {direction}")
    if len(platforms) != len(stations):
        raise RouteDataError(
            f"relation {rel_id} has {len(platforms)} platforms but the Transurb "
            f"page lists {len(stations)} stations for route {route_id} "
            f"{direction}: {', '.join(stations)}")
    stops = []
    for station, p in zip(stations, platforms):
        if p["type"] != "node":
            raise RouteDataError(
                f"relation {rel_id}: platform for {station!r} is a "
                f"{p['type']}, not a node ({p['node']})")
        if not p["name"]:
            raise RouteDataError(
                f"relation {rel_id}: platform node {p['node']} (station "
                f"{station!r}) has no name tag")
        if not names_agree(station, p["name"], p.get("alt_names", [])):
            issue("warning", f"route {route_id} {direction}",
                  f"stop name differs from the Transurb page: OSM "
                  f"{p['name']!r} (n{p['node']}) vs site {station!r}; no "
                  f"{'/'.join(ALT_NAME_KEYS[:2])} on the node records the "
                  f"site's spelling")
        stops.append({"id": stop_id(p["name"], p["node"]), "name": p["name"],
                      "alt_names": p.get("alt_names", []),
                      "lat": p["lat"], "lon": p["lon"],
                      "node": p["node"], "station": station})
    return stops


def route_metadata(route_id: str, rel_data: dict[str, dict],
                   stops: dict[str, list[dict]]) -> dict:
    """route_type, long name and headsigns, all read from the relation tags."""
    types = {d: OSM_ROUTE_TYPE.get(data["tags"].get("route"))
             for d, data in rel_data.items()}
    for d, t in types.items():
        if t is None:
            issue("error", f"route {route_id} {d}",
                  f"relation {rel_data[d]['id']} has route="
                  f"{rel_data[d]['tags'].get('route')!r}, which is not one of "
                  f"{', '.join(OSM_ROUTE_TYPE)}")
    known = {t for t in types.values() if t is not None}
    if len(known) > 1:
        issue("error", f"route {route_id}",
              f"directions disagree on the vehicle type: {types}")
    route_type = known.pop() if len(known) == 1 else 3
    vehicle = PALETTE.get(route_id, {}).get("vehicle")
    if vehicle and VEHICLE_ROUTE_TYPE.get(vehicle) != route_type:
        issue("warning", f"route {route_id}",
              f"OSM says route_type {route_type} but route-colors.txt says "
              f"{vehicle} ({VEHICLE_ROUTE_TYPE.get(vehicle)})")

    headsigns = {}
    for d, data in rel_data.items():
        to = data["tags"].get("to")
        last = stops[d][-1]["name"]
        if not to:
            issue("warning", f"route {route_id} {d}",
                  f"relation {data['id']} has no 'to' tag; the headsign falls "
                  f"back to the last stop, {last!r}")
            to = last
        elif not names_agree(to, last, stops[d][-1].get("alt_names", [])):
            issue("warning", f"route {route_id} {d}",
                  f"relation {data['id']} 'to' is {to!r} but the last stop is "
                  f"{last!r}")
        frm = data["tags"].get("from")
        first = stops[d][0]["name"]
        if not frm:
            issue("warning", f"route {route_id} {d}",
                  f"relation {data['id']} has no 'from' tag; the long name "
                  f"falls back to the first stop, {first!r}")
        elif not names_agree(frm, first, stops[d][0].get("alt_names", [])):
            issue("warning", f"route {route_id} {d}",
                  f"relation {data['id']} 'from' is {frm!r} but the first stop "
                  f"is {first!r}")
        headsigns[d] = to

    main = "TUR" if "TUR" in rel_data else next(iter(rel_data))
    tags = rel_data[main]["tags"]
    frm = tags.get("from") or stops[main][0]["name"]
    to = tags.get("to") or stops[main][-1]["name"]
    via = [v.strip() for v in tags.get("via", "").split(";") if v.strip()]
    roundtrip = tags.get("roundtrip") in ("yes", "clockwise", "cw", "ccw")
    if frm == to and not via:
        issue("warning", f"route {route_id}",
              f"relation {rel_data[main]['id']} is a loop (from == to == "
              f"{frm!r}) with no 'via', so the long name reads {frm} - {frm}; "
              f"add via=<the stop that identifies the loop>"
              + ("" if roundtrip else " and roundtrip=yes"))
    elif frm == to and not roundtrip:
        issue("warning", f"route {route_id}",
              f"relation {rel_data[main]['id']} starts and ends at {frm!r} but "
              f"is not tagged roundtrip=yes")
    elif roundtrip and frm != to:
        issue("warning", f"route {route_id}",
              f"relation {rel_data[main]['id']} is tagged roundtrip=yes but "
              f"runs {frm!r} -> {to!r}")
    long_name = " - ".join([frm] + via + [to])

    entry = PALETTE.get(route_id)
    if entry is None:
        issue("warning", f"route {route_id}",
              f"not listed in {COLORS_FILE.name}; using {FALLBACK_COLOR}")
    color = entry["color"] if entry else FALLBACK_COLOR
    return {"route_type": route_type, "route_long_name": long_name,
            "headsigns": headsigns, "route_color": color,
            "route_text_color": text_color(color)}


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
def osm_shape_points(rel_data: dict,
                     stops: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Ordered (lat, lon) polyline from the relation's ways.

    `stops` is the direction's stop sequence used to orient the first way
    (relations sometimes map its first way against the direction of travel).
    """
    path: list[tuple[float, float]] = []
    for geom in rel_data["ways"]:
        pts = [(p[0], p[1]) for p in geom]
        if path:
            last = path[-1]
            d_first = (pts[0][0] - last[0]) ** 2 + (pts[0][1] - last[1]) ** 2
            d_last = (pts[-1][0] - last[0]) ** 2 + (pts[-1][1] - last[1]) ** 2
            if d_last < d_first:
                pts.reverse()
            if (pts[0][0] - last[0]) ** 2 + (pts[0][1] - last[1]) ** 2 < 1e-10:
                pts = pts[1:]
        else:  # first way: orient against the first stop
            d_start = (pts[0][0] - stops[0][0]) ** 2 + (pts[0][1] - stops[0][1]) ** 2
            d_end = (pts[-1][0] - stops[0][0]) ** 2 + (pts[-1][1] - stops[0][1]) ** 2
            if d_end < d_start:
                pts.reverse()
        path.extend(pts)
    return dedup_points(path)


def shape_ok(pts: list[tuple[float, float]], stops: list[tuple[float, float]],
             max_dist_m: float = 150.0) -> bool:
    """Every stop must be close to the shape and visited in order.

    Stops that are passed more than once (loops, or both sides of a street)
    match the first occurrence at or after the previous stop's position.
    """
    tol = (max_dist_m / 111000.0) ** 2
    prev = -1
    for lat, lon in stops:
        found = False
        for i, (pl, pn) in enumerate(pts):
            if i >= prev and (pl - lat) ** 2 + (pn - lon) ** 2 <= tol:
                prev = i
                found = True
                break
        if not found:
            return False
    return True


def osrm_shape_points(stops: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Route along the road network between consecutive stops (fallback)."""
    path = []
    for a, b in zip(stops, stops[1:]):
        url = (f"{OSRM_URL}/{b[1]:.6f},{b[0]:.6f};{a[1]:.6f},{a[0]:.6f}"
               "?overview=full&geometries=geojson&steps=false")
        cache = CACHE_DIR / f"osrm_{a[0]:.5f}_{a[1]:.5f}_{b[0]:.5f}_{b[1]:.5f}.json"
        if cache.exists():
            j = json.loads(cache.read_text(encoding="utf-8"))
        else:
            j = json.loads(http_get(url).decode("utf-8", "replace"))
            cache.write_text(json.dumps(j), encoding="utf-8")
            time.sleep(0.3)
        coords = j.get("routes", [{}])[0].get("geometry", {}).get("coordinates", [])
        if not coords:
            print(f"  osrm: no route {a} -> {b}, using straight line")
            coords = [[a[1], a[0]], [b[1], b[0]]]
        if path:
            coords = coords[1:]
        path.extend((lat, lon) for lon, lat in coords)
    return dedup_points(path)


def dedup_points(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for p in path:
        if not out or out[-1] != p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Timetables
# ---------------------------------------------------------------------------
def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# Doubled-offset phantom trips on the Transurb website
#
# A direction's timetable is regular: every trip runs the same time per leg, so
# each station's column is the first station's column plus a fixed offset.
# Route 39 is published with one column per direction that breaks this: it is
# one of the day's departures with every cumulative offset counted twice, so it
# shows up at every station except the first, as a trip with no departure of
# its own that takes 58 minutes where the real one takes 29. That is a website
# arithmetic error rather than a short working, and align_times would otherwise
# fail on it, because a later station then holds more times than the first
# station defines trips for.
#
# Such a column is dropped and reported. The test is strict on purpose: the
# regular profile has to explain every other time in every column, and the
# surplus times have to match the doubled profile to the minute, so a genuine
# trip that starts mid-route is not silently discarded.
# ---------------------------------------------------------------------------
def _surplus(times: list[int], expected: list[int]) -> list[int] | None:
    """times minus expected, or None when times does not contain all of it."""
    left = list(times)
    for t in expected:
        if t not in left:
            return None
        left.remove(t)
    return left


def regular_profile(station_times: list[list[str]]
                    ) -> tuple[list[int], list[list[int]]] | None:
    """The cumulative offset per station, plus the times it cannot explain.

    Returns None when no single offset per station lines its column up with the
    first station's departures, i.e. when running times genuinely vary between
    trips and this whole notion does not apply.
    """
    first = sorted(_minutes(t) for t in station_times[0])
    if not first:
        return None
    offsets, surplus = [0], [[]]
    for column in station_times[1:]:
        times = sorted(_minutes(t) for t in column)
        if len(times) < len(first):
            return None  # a station with fewer times: trips skip it, not our case
        found = None
        # the offset is fixed by which of the column's first few times is the
        # one belonging to the earliest departure
        for head in times[:len(times) - len(first) + 1]:
            offset = head - first[0]
            if offset < offsets[-1]:
                continue
            extra = _surplus(times, [f + offset for f in first])
            if extra is not None:
                found = (offset, extra)
                break
        if found is None:
            return None
        offsets.append(found[0])
        surplus.append(found[1])
    return offsets, surplus


def find_doubled_trip(station_times: list[list[str]]) -> list[int] | None:
    """The times of a phantom trip whose legs are the real ones doubled.

    Returns one time per station, in minutes, or None. A station's time can
    coincide with a real trip's, in which case the site prints one entry for
    both and that station has no surplus; the phantom is still identified.
    """
    profile = regular_profile(station_times)
    if profile is None:
        return None
    offsets, surplus = profile
    if not any(surplus) or any(len(s) > 1 for s in surplus):
        return None
    columns = [sorted(_minutes(t) for t in c) for c in station_times]
    for start in columns[0]:
        doubled = [start + 2 * o for o in offsets]
        if all(s[0] == t if s else t in c
               for s, t, c in zip(surplus[1:], doubled[1:], columns[1:])):
            return doubled
    return None


def drop_doubled_trip(route_id: str, direction: str, service: str,
                      station_times: list[list[str]]) -> list[list[str]]:
    """station_times with a doubled-offset phantom trip removed, if present."""
    doubled = find_doubled_trip(station_times)
    if doubled is None:
        return station_times

    def hhmm(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    real_end = doubled[0] + (doubled[-1] - doubled[0]) // 2
    issue("warning", f"route {route_id} {direction} {service}",
          f"the Transurb timetable lists a trip with every running time "
          f"doubled: the {hhmm(doubled[0])} departure reaching the last stop "
          f"at {hhmm(doubled[-1])} instead of {hhmm(real_end)}, with no "
          f"departure of its own from the first stop; it is a website "
          f"arithmetic error and is left out of the feed", source="site")
    cleaned = [list(station_times[0])]
    for column, t in zip(station_times[1:], doubled[1:]):
        remaining = list(column)
        drop = hhmm(t)
        # only a surplus entry is removed; where the phantom's time coincides
        # with a real trip's, the single printed entry belongs to the real one
        if len(remaining) > len(station_times[0]) and drop in remaining:
            remaining.remove(drop)
        cleaned.append(remaining)
    return cleaned


# A trip's time at one station has to be within this of its time at the station
# that defines the trips, otherwise it belongs to a different trip.
MAX_LEG_SPREAD_MIN = 45


def align_times(station_times: list[list[str]]) -> list[list[str | None]]:
    """Align each station's sorted times to the trips.

    The busiest station defines the trips, since not every trip need run the
    whole route: route 13 turns most trips back at Aleea Nordului and only a few
    continue to Agrogal, so its terminus has 6 times where the rest of the route
    has 26. Stations before that one are matched backwards and stations after it
    forwards; a station with fewer times leaves the trips it does not serve as
    None, whether they end short of it or start beyond it.
    """
    ref = max(range(len(station_times)), key=lambda k: len(station_times[k]))
    reference = station_times[ref]
    n = len(reference)
    rows: list[list[str | None]] = [[None] * n for _ in station_times]
    rows[ref] = list(reference)
    for k in range(len(station_times)):
        if k == ref:
            continue
        tk = station_times[k]
        if len(tk) == n:
            rows[k] = list(tk)
            continue
        j = 0
        for i in range(n):
            if j >= len(tk):
                break
            t = tk[j]
            here, prev = reference[i], reference[i - 1] if i else None
            nxt = reference[i + 1] if i + 1 < n else None
            if k > ref:
                # later in travel: the time falls in [here, nxt) and the trip
                # can plausibly get this far
                fits = (t >= here and (nxt is None or t < nxt)
                        and _minutes(t) - _minutes(here) <= MAX_LEG_SPREAD_MIN)
            else:
                # earlier in travel: the time falls in (prev, here] instead
                fits = (t <= here and (prev is None or t > prev)
                        and _minutes(here) - _minutes(t) <= MAX_LEG_SPREAD_MIN)
            if fits:
                rows[k][i] = t
                j += 1
            # otherwise this trip does not call at station k
        if j < len(tk):
            raise RuntimeError(
                f"station {k} has {len(tk) - j} time(s) that match no trip "
                f"defined by station {ref} ({n} trips): {tk[j:]}")
    # monotonicity check within each trip
    for i in range(n):
        prev = None
        for k in range(len(rows)):
            t = rows[k][i]
            if t is None:
                continue
            if prev is not None and t < prev:
                raise RuntimeError(
                    f"decreasing time in trip {i} at station {k}: {prev} -> {t}")
            prev = t
    return rows

def route_variants(route_id: str, cfg: dict) -> dict[str, dict]:
    """The route's itineraries to build, keyed by the site's variant name.

    A plain "relations" config is the single-itinerary case, i.e. one variant
    named "standard" that provides both service periods.
    """
    if "variants" in cfg:
        return cfg["variants"]
    return {STANDARD_VARIANT: {"relations": cfg["relations"],
                               "services": ("WD", "WE")}}


def collect_route(route_id: str, cfg: dict) -> dict:
    """Stops, trips, stop_times and metadata for one route."""
    all_variants = site_stations(route_id)
    wanted = route_variants(route_id, cfg)
    for name, seq in all_variants.items():
        if name not in wanted and (seq["TUR"] or seq["RETUR"]):
            issue("warning", f"route {route_id}",
                  f"the route page lists an itinerary, {name!r} "
                  f"({len(seq['TUR'])} / {len(seq['RETUR'])} stations); it needs "
                  f"its own OSM route relations and is not in the feed")

    rel_data, stops, meta = {}, {}, None
    trips, stop_times, stop_order = [], [], {}
    unique_stops = {}
    sequences = {}
    for variant, vcfg in wanted.items():
        v_out = collect_variant(route_id, cfg, variant, vcfg,
                                all_variants.get(variant))
        # A variant's stops, shapes and metadata are its own; the first one
        # named in the config provides the route's long name and colour.
        rel_data.update(v_out["rel_data"])
        stops.update(v_out["stops_by_key"])
        unique_stops.update(v_out["stops"])
        trips.extend(v_out["trips"])
        stop_times.extend(v_out["stop_times"])
        stop_order.update(v_out["stop_order"])
        sequences.update(v_out["sequences"])
        meta = meta or v_out["meta"]
    return {"stops": unique_stops, "trips": trips, "stop_times": stop_times,
            "stop_order": stop_order, "sequences": sequences, "meta": meta,
            "rel_data": rel_data}


def variant_key(direction: str, variant: str) -> str:
    """Direction key that stays unique when a route has several itineraries."""
    if variant == STANDARD_VARIANT:
        return direction
    return f"{direction}-{slug(variant)[:20]}"


def collect_variant(route_id: str, cfg: dict, variant: str, vcfg: dict,
                    stations: dict | None) -> dict:
    """One itinerary of a route: stops, trips and stop_times."""
    label = route_id if variant == STANDARD_VARIANT else f"{route_id} [{variant}]"
    if not stations:
        raise RouteDataError(
            f"the Transurb page for route {route_id} has no itinerary named "
            f"{variant!r}, but relations are configured for it")
    for direction in vcfg["relations"]:
        if not stations.get(direction):
            raise RouteDataError(
                f"the Transurb page lists no {direction} stations for "
                f"{label}, but relation {vcfg['relations'][direction]} is "
                f"configured for it")
    for direction, listed in stations.items():
        if listed and direction not in vcfg["relations"]:
            issue("error", f"route {label} {direction}",
                  f"the Transurb page lists {len(listed)} stations but no OSM "
                  f"route relation is configured, so the direction is missing "
                  f"from the feed")

    # 1) stops, from the OSM relations
    rel_data, stops = {}, {}
    for direction, rel_id in vcfg["relations"].items():
        print(f"  [platforms] {label} {direction}: relation {rel_id}", flush=True)
        key = variant_key(direction, variant)
        rel_data[key] = relation_data(rel_id)
        stops[key] = direction_stops(route_id, direction, rel_id,
                                     stations[direction])
    meta = route_metadata(route_id, rel_data, stops)

    # 2) timetables, from the Transurb website
    times = {}  # times[key][station][service] = [hh:mm, ...]
    for key, seq in stops.items():
        for i, stop in enumerate(seq, 1):
            print(f"  [{label} {key} {i}/{len(seq)}] {stop['station']}", flush=True)
            direction = "TUR" if key.startswith("TUR") else "RETUR"
            wd, we = fetch_schedule(route_id, direction, stop["station"], variant)
            times.setdefault(key, {})[i] = {"WD": wd, "WE": we}

    # 3) trips + stop_times
    trips, stop_times, stop_order = [], [], {}
    unique_stops = {}
    for key, seq in stops.items():
        for stop in seq:
            unique_stops.setdefault(stop["id"], stop)
        stop_order[key] = [(s["lat"], s["lon"]) for s in seq]
        direction = "TUR" if key.startswith("TUR") else "RETUR"
        direction_id = 0 if direction == "TUR" else 1
        shape_id = f"{route_id}-{key}"
        wd_service = cfg.get("service_days", "WD")
        built_trips: dict[str, int] = {}
        for service in ("WD", "WE"):
            svc_id = wd_service if service == "WD" else "WE"
            if service not in vcfg.get("services", ("WD", "WE")):
                # The site prints a column for a period this itinerary does not
                # run; it belongs to the route's other itinerary.
                continue
            station_times = [times[key][i][service]
                             for i in range(1, len(seq) + 1)]
            if not any(station_times):
                continue  # no service in this period (e.g. weekday-only route)
            station_times = drop_doubled_trip(route_id, key, service,
                                              station_times)
            rows = align_times(station_times)
            # align_times keys the trips off the busiest station, so the trip
            # count is the widest row rather than the first one
            n = max(len(r) for r in rows)
            skipped = sum(1 for r in rows for i in range(n) if r[i] is None)
            if skipped:
                print(f"  route {label} {key} {service}: "
                      f"{skipped} skipped stop visits")
            # trip ids stay unique across a route's itineraries: the variant is
            # part of the key, so the weekend Grădina Publică trips cannot
            # collide with the weekday Piața Centrală ones.
            prefix = f"{route_id}-{key}-{svc_id}"
            for i in range(n):
                tid = f"{prefix}-{i + 1:03d}"
                trips.append((route_id, svc_id, tid, meta["headsigns"][key],
                              direction_id, shape_id))
                for k, stop in enumerate(seq, start=1):
                    t = rows[k - 1][i]
                    if t is None:
                        continue
                    t = t + ":00"
                    stop_times.append((tid, t, t, stop["id"], k))
            built_trips[service] = n
        served = [s for s in ("WD", "WE") if s in built_trips]
        print(f"route {label} {key}: "
              + ", ".join(f"{built_trips[s]} {s} trips" for s in served))
    return {"stops": unique_stops, "stops_by_key": stops, "trips": trips,
            "stop_times": stop_times, "stop_order": stop_order,
            "sequences": stops, "meta": meta, "rel_data": rel_data}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(a))


# Two platforms of one stop sit on opposite sides of a street; further apart
# than this and a shared name means two different stops with the same name.
SAME_NAME_MAX_M = 300.0


def name_key(name: str) -> str:
    """Grouping key that ignores the street-name prefix.

    'Radu Negru' and 'Strada Radu Negru' are the same name written two ways.
    """
    return re.sub(r"^(STRADA|STR|BULEVARDUL|BLD|BD)-", "", slug(name))


def report_name_inconsistencies(all_stops: dict[str, dict]) -> None:
    """Report OSM stop names that are inconsistent or ambiguous.

    Two platforms of the same stop spelled differently ('LIDL' / 'Lidl') is an
    OSM inconsistency; it shows up as two differently named stops in the feed.
    One name used by stops far apart from each other makes the feed ambiguous
    for passengers, since nothing distinguishes them.
    """
    groups: dict[str, list[dict]] = {}
    for s in all_stops.values():
        groups.setdefault(name_key(s["name"]), []).append(s)
    for key, stops in sorted(groups.items()):
        names = {s["name"] for s in stops}
        if len(names) > 1:
            issue("warning", "stop names",
                  f"same stop spelled differently in OSM: {sorted(names)} "
                  f"(nodes {sorted(s['node'] for s in stops)})")
        far = [(a, b) for i, a in enumerate(stops) for b in stops[i + 1:]
               if haversine(a["lat"], a["lon"], b["lat"], b["lon"]) > SAME_NAME_MAX_M]
        if far:
            nodes = sorted({s["node"] for pair in far for s in pair})
            dists = sorted({round(haversine(a["lat"], a["lon"], b["lat"], b["lon"]))
                            for a, b in far})
            issue("warning", "stop names",
                  f"{sorted(names)[0]!r} names {len(set(nodes))} stops that are "
                  f"{'/'.join(f'{d} m' for d in dists)} apart, with nothing to "
                  f"tell them apart (nodes {nodes})")


def report_ambiguous_within_route(all_order_names: dict) -> None:
    """Report one direction calling at two different stops of the same name.

    The Transurb page distinguishes them (e.g. UNIV. DANUBIUS vs DANUBIUS), so
    riders of that direction see the same stop name twice in the feed.
    """
    for (route_id, direction), stops in sorted(all_order_names.items()):
        by_name = {}
        for s in stops:
            by_name.setdefault(s["name"], set()).add(s["node"])
        for name, nodes in by_name.items():
            if len(nodes) > 1:
                issue("warning", f"route {route_id} {direction}",
                      f"calls at two different stops both named {name!r} "
                      f"(nodes {sorted(nodes)}); the route page gives them "
                      f"different names")


def write_feed(route_ids: list[str]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    all_stops, all_trips, all_st, all_order = {}, [], [], {}
    meta, rel_data, built, sequences = {}, {}, [], {}
    for i, rid in enumerate(route_ids, 1):
        print(f"\n[{i}/{len(route_ids)}] Route {rid}", flush=True)
        try:
            data = collect_route(rid, ROUTES[rid])
        except RouteDataError as e:
            issue("error", f"route {rid}", str(e))
            continue
        all_stops.update(data["stops"])
        all_trips.extend(data["trips"])
        all_st.extend(data["stop_times"])
        all_order[rid] = data["stop_order"]
        for direction, seq in data["sequences"].items():
            sequences[(rid, direction)] = seq
        meta[rid] = data["meta"]
        rel_data[rid] = data["rel_data"]
        built.append(rid)
        print(f"  {meta[rid]['route_long_name']} "
              f"(route_type {meta[rid]['route_type']}, "
              f"#{meta[rid]['route_color']})")

    report_name_inconsistencies(all_stops)
    report_ambiguous_within_route(sequences)

    def wcsv(name: str, header: list[str], rows: list[list]) -> None:
        with open(OUT_DIR / name, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(header) + "\n")
            for r in rows:
                fh.write(",".join(str(x) for x in r) + "\n")

    wcsv("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone",
                        "agency_lang", "agency_phone"],
         [["transurb", "TRANSURB S.A. Galati", "https://transurbgalati.ro",
           "Europe/Bucharest", "ro", "+40 721 111 602"]])

    def _route_sort_key(r):
        """Sort routes numerically, with letter suffixes after the number."""
        m = re.match(r"(\d+)(.*)", r)
        return (int(m.group(1)), m.group(2)) if m else (9999, r)
    sorted_ids = sorted(built, key=_route_sort_key)
    wcsv("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name",
                        "route_type", "route_color", "route_text_color"],
         [[rid, "transurb", rid, meta[rid]["route_long_name"],
           meta[rid]["route_type"], meta[rid]["route_color"],
           meta[rid]["route_text_color"]] for rid in sorted_ids])

    wcsv("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"],
         [[sid, s["name"], f"{s['lat']:.7f}", f"{s['lon']:.7f}"]
          for sid, s in sorted(all_stops.items())])

    # Determine which service_ids are actually used
    used_services = set(t[1] for t in all_trips)

    calendar_rows = []
    if "WD" in used_services:
        calendar_rows.append(["WD", "1", "1", "1", "1", "1", "0", "0", FEED_START, FEED_END])
    if "TF" in used_services:
        calendar_rows.append(["TF", "0", "1", "1", "1", "1", "0", "0", FEED_START, FEED_END])
    if "WE" in used_services:
        calendar_rows.append(["WE", "0", "0", "0", "0", "0", "1", "1", FEED_START, FEED_END])

    wcsv("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday",
                          "friday", "saturday", "sunday", "start_date", "end_date"],
         calendar_rows)

    cal_dates_rows = []
    for d in HOLIDAYS_2026:
        if "WE" in used_services:
            cal_dates_rows.append(["WE", d, 1])
        if "WD" in used_services:
            cal_dates_rows.append(["WD", d, 2])
        if "TF" in used_services:
            cal_dates_rows.append(["TF", d, 2])
    wcsv("calendar_dates.txt", ["service_id", "date", "exception_type"], cal_dates_rows)

    wcsv("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign",
                       "direction_id", "shape_id"],
         all_trips)
    wcsv("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id",
                            "stop_sequence"], all_st)

    # shapes: the relation's own geometry, checked against the stop sequence
    shape_rows = []
    shape_report = []
    for rid in built:
        for direction, data in rel_data[rid].items():
            shape_id = f"{rid}-{direction}"
            order = all_order[rid][direction]
            pts = osm_shape_points(data, order)
            if pts and shape_ok(pts, order):
                print(f"shape {shape_id}: {len(pts)} points (OSM relation {data['id']})")
                shape_report.append(f"{shape_id}: OSM relation {data['id']}")
            else:
                issue("error", f"route {rid} {direction}",
                      f"relation {data['id']} geometry does not pass along its "
                      f"own stops in order; the shape falls back to OSRM routing")
                pts = osrm_shape_points(order)
                shape_report.append(f"{shape_id}: OSRM (relation "
                                    f"{data['id']} fails the shape check)")
            for seq, (lat, lon) in enumerate(pts, start=1):
                shape_rows.append((shape_id, f"{lat:.6f}", f"{lon:.6f}", seq))
    wcsv("shapes.txt", ["shape_id", "shape_pt_lat", "shape_pt_lon",
                        "shape_pt_sequence"], shape_rows)
    print("\nshape sources:")
    for line in shape_report:
        print("  " + line)

    # Fares v2 — prices fetched from transurbgalati.ro/altele/titluri_calatorie/tarife
    #
    # Three fare zones by geography, each with its own price and duration:
    #   urban (Galați + Comuna Vânători interior): 60 min
    #   costi (Sat Costi – Galați):               60 min
    #   odaia (Odaia Manolache – Galați):          90 min
    #
    # Four fare media (the ways a rider can pay):
    #   transport_card  — physical card, validated on board (cEMV)
    #   app_tg          — "Transport Galați" app, card payment (account-based)
    #   app_24pay       — "24Pay" app, card payment (account-based)
    #   sms_24pay       — "24Pay" app, SMS payment (account-based, EUR)
    #
    # Transfers are free within the urban network for the 60-minute validity
    # window: riders must scan at each boarding but are not charged again. An
    # extraurban leg (routes 50 and 55) is always paid, whatever ticket the
    # rider is holding, so changing from 102 to 50 or 55 at Piața Centrală
    # costs both legs.
    #
    # Passes (Plus Nominal, Basic bundles) are informational for riders but do
    # not affect routing; GTFS Fares v2 rider_categories and fare_containers
    # are not yet widely consumed, so only single-ride products are emitted.

    print("\nFares:", flush=True)
    tarife_html = fetch_page(TARIFE_URL, CACHE_DIR / "tarife.html", REFRESH)
    fares = fetch_fares(tarife_html)
    for m in fares["media"]:
        print(f"  {m['id']}: {m['amount']} {m['currency']} "
              f"({m['name']}, type {m['type']})")
    print(f"  transfer window: {fares['duration']} min")
    write_fares(fares, sorted_ids, PALETTE, wcsv)

    # feed_info: the publisher is whoever produced this dataset, which is not
    # the operator. feed_contact_* is the technical contact for the feed, so a
    # consumer who spots a problem in the data reaches the person maintaining
    # it rather than Transurb's customer service (that stays in agency.txt).
    wcsv("feed_info.txt",
         ["feed_publisher_name", "feed_publisher_url", "feed_lang", "default_lang",
          "feed_start_date", "feed_end_date", "feed_version", "feed_contact_email",
          "feed_contact_url"],
         [[FEED_PUBLISHER, FEED_PUBLISHER_URL, "ro", "ro",
           FEED_START, FEED_END,
           "transurb-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
           FEED_CONTACT_EMAIL, FEED_CONTACT_URL]])

    # attributions: spell out that Transurb operates the network while this
    # dataset is produced independently. Without agency_id/route_id/trip_id an
    # attribution applies to the whole dataset, which is what both of these do.
    wcsv("attributions.txt",
         ["attribution_id", "organization_name", "is_producer", "is_operator",
          "is_authority", "attribution_url", "attribution_email"],
         [["publisher", FEED_PUBLISHER, 1, 0, 0,
           FEED_PUBLISHER_URL, FEED_CONTACT_EMAIL],
          ["operator", OPERATOR_NAME, 0, 1, 0, OPERATOR_URL, ""]])

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(OUT_DIR.glob("*.txt")):
            zf.write(f, f.name)

    print(f"\n{len(all_stops)} stops, {len(all_trips)} trips, "
          f"{len(all_st)} stop_times")
    print(f"feed written to {OUT_DIR} and {ZIP_PATH}")

    errors = [i for i in ISSUES if i[0] == "error"]
    osm_issues = [i for i in ISSUES if i[3] == "osm"]
    site_issues = [i for i in ISSUES if i[3] == "site"]
    for label, group in (("OSM issues to fix upstream", osm_issues),
                         ("Transurb website issues to report", site_issues)):
        if not group:
            continue
        n_err = sum(1 for i in group if i[0] == "error")
        print(f"\n{label} ({n_err} error(s), {len(group) - n_err} warning(s)):")
        for severity, subject, message, _ in group:
            print(f"  [{severity}] {subject}: {message}")
    if not ISSUES:
        print("\nOSM data matches the Transurb website for every route.")
    if errors:
        sys.exit(f"\n{len(errors)} OSM error(s) make the feed incomplete or "
                 f"wrong; fix them in OpenStreetMap and rebuild")


def main() -> None:
    global REFRESH
    parser = argparse.ArgumentParser(description="Generate GTFS for TRANSURB Galati")
    parser.add_argument("routes", nargs="*", help="route numbers (default: all configured)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch OSM relations and Transurb route pages "
                             "instead of using the cache")
    args = parser.parse_args()
    REFRESH = args.refresh
    route_ids = args.routes or list(ROUTES.keys())
    unknown = [r for r in route_ids if r not in ROUTES]
    if unknown:
        sys.exit(f"unknown route(s): {', '.join(unknown)}; configured: {', '.join(ROUTES)}")
    write_feed(route_ids)


if __name__ == "__main__":
    main()
