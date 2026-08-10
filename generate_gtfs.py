#!/usr/bin/env python3
"""Generate a GTFS feed for TRANSURB S.A. Galați (transurbgalati.ro).

Fetches per-stop timetables from the Transurb website, aligns them into trips
and writes the GTFS feed (gtfs_transurb/*.txt + gtfs_transurb.zip).

Usage:
    python3 generate_gtfs.py            # build feed for all configured routes
    python3 generate_gtfs.py 102        # build feed for route 102 only
    python3 generate_gtfs.py --help
"""

import argparse
import json
import os
import re
import sys
import time
import zipfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_URL = "https://transurbgalati.ro/program_circulatie"
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
OUT_DIR = HERE / "gtfs_transurb"
ZIP_PATH = HERE / "gtfs_transurb.zip"
UA = "gtfs-galati/1.0"  # Overpass rejects Mozilla-prefixed custom UAs

FEED_START, FEED_END = "20260101", "20261231"
# Romanian legal holidays in 2026 that follow the weekend schedule.
HOLIDAYS_2026 = ["20260101", "20260106", "20260107", "20260410", "20260413",
                 "20260501", "20260601", "20261130", "20261201", "20261225"]

# ---------------------------------------------------------------------------
# Stop catalog: canonical code -> (display name, lat, lon).
# One entry per physical stop, shared by every route.
# ---------------------------------------------------------------------------
STOPS = {
    "MICRO 19": ("Micro 19", 45.4133153, 28.0124752),
    "NEACSU": ("Neacșu", 45.4169588, 28.0130412),
    "SPITALUL JUDETEAN": ("Spitalul Județean", 45.4193509, 28.0177710),
    "PRIVILEGE": ("Privilege", 45.4261475, 28.0233600),
    "TIGLINA I": ("Țiglina I", 45.4276057, 28.0281282),
    "ROMTELECOM": ("Romtelecom", 45.4286741, 28.0396232),
    "MAZEPA": ("Mazepa", 45.4305181, 28.0462169),
    "POTCOAVA DE AUR": ("Potcoava de Aur", 45.4327326, 28.0504231),
    "GALERIILE DE ARTA": ("Galeriile de Artă", 45.4354384, 28.0557046),
    "UNIVERSITATE": ("Universitate", 45.4392722, 28.0565444),
    "PARFUMUL TEILOR": ("Parfumul Teilor", 45.4426716, 28.0559478),
    "DIRECTIA AGRICOLA": ("Direcția Agricolă", 45.4461186, 28.0541986),
    "BLOC IALOMITA": ("Bloc Ialomița", 45.4495658, 28.0524493),
    "CAMINE STUDENTESTI": ("Cămine Studențești", 45.4539921, 28.0497540),
    "CAMINUL DE BATRANI": ("Căminul de Bătrâni", 45.4580487, 28.0473113),
    "PARC C.F.R.": ("Parc C.F.R.", 45.4623733, 28.0447250),
    "STR. PRUNDULUI": ("Str. Prundului", 45.4630597, 28.0369371),
    "BARIERA TRAIAN": ("Bariera Traian", 45.4653144, 28.0362148),
    "STR. RADU NEGRU": ("Str. Radu Negru", 45.4589088, 28.0465985),
    "A.J.O.F.M.": ("A.J.O.F.M.", 45.4569492, 28.0477278),
    "MUZEUL DE ARTA": ("Muzeul de Artă", 45.4506850, 28.0515363),
    "STR. VULTUR": ("Str. Vultur", 45.4483643, 28.0529454),
    "LICEUL DE ARTA": ("Liceul de Artă", 45.4450969, 28.0548432),
    "TEATRUL DRAMATIC": ("Teatrul Dramatic", 45.4413412, 28.0561051),
    "PARC EMINESCU": ("Parc Eminescu", 45.4361285, 28.0557148),
    "CENTRU": ("Centru", 45.4342165, 28.0539635),
    "AGENTIA C.F.R.": ("Agenția C.F.R.", 45.4310705, 28.0466092),
    "PARCARE BANCI": ("Parcare Bănci", 45.4292945, 28.0414140),
    "CEC TIGLINA II": ("CEC Țiglina II", 45.4285875, 28.0355499),
    "TIGLINA II": ("Țiglina II", 45.4279325, 28.0280075),
    "FARMACIA HYGEIA": ("Farmacia Hygeia", 45.4191133, 28.0168623),
    "SERVICE VECHI": ("Service Vechi", 45.4168495, 28.0124289),
    "BLD. GALATI": ("Bld. Galați", 45.4108853, 28.0147926),
    "ZENNER": ("Zenner", 45.4093346, 28.0111119),
    "BLOC A8": ("Bloc A8", 45.4113856, 28.0083163),
    "CIMITIR CATUSA": ("Cimitir Cătușa", 45.4130442, 28.0063495),
    "GRADINITA PRICHINDEL": ("Grădinița Prichindel", 45.4139447, 28.0070235),
    "GARA CFR": ("Gara C.F.R.", 45.4444747, 28.0598390),
    "AUTOGARA": ("Autogară", 45.4432060, 28.0586207),
    "STR. GARII": ("Str. Gării", 45.4438402, 28.0537161),
    "F.E.E.A": ("Facultatea de Științe Economice", 45.4432509, 28.0517570),
    "C.N.V.A.": ("Colegiul Național Vasile Alecsandri", 45.4406250, 28.0521298),
    "ALBATROS": ("Albatros", 45.4369171, 28.0526448),
    "IATSA": ("IATSA", 45.4142302, 28.0072252),
    "BLOC B3": ("Bloc B3", 45.4110405, 28.0084235),
    "FAC. DE MEDICINA": ("Fac. de Medicină", 45.4106737, 28.0149120),
    "GRADINA PUBLICA": ("Grădina Publică", 45.4513651, 28.0510709),
    "CAMINELE COMBINATULUI": ("Căminele Combinatului", 45.4420380, 28.0133603),
    "PIATA ENERGIEI": ("Piața Energiei", 45.4398644, 28.0205822),
}

# ---------------------------------------------------------------------------
# Route configuration.
#
# Add a new route here: `stops` lists the stop codes in the order shown on
# the "veziTraseu" page (TUR and RETUR). Codes must exist in STOPS above.
#
# If the site spells a stop differently for this route than in STOPS (e.g.
# extra spaces, abbreviations), map it to the canonical code under `aliases`.
# ---------------------------------------------------------------------------
ROUTES = {
    "102": {
        "route_long_name": "Micro 19 - Bariera Traian",
        "route_type": 11,  # trolleybus
        "route_color": "058B8C",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "Bariera Traian",
                "stops": ["MICRO 19", "NEACSU", "SPITALUL JUDETEAN", "PRIVILEGE",
                          "TIGLINA I", "ROMTELECOM", "MAZEPA", "POTCOAVA DE AUR",
                          "GALERIILE DE ARTA", "UNIVERSITATE", "PARFUMUL TEILOR",
                          "DIRECTIA AGRICOLA", "BLOC IALOMITA", "CAMINE STUDENTESTI",
                          "CAMINUL DE BATRANI", "PARC C.F.R.", "STR. PRUNDULUI",
                          "BARIERA TRAIAN"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["BARIERA TRAIAN", "PARC C.F.R.", "STR. RADU NEGRU",
                          "A.J.O.F.M.", "CAMINE STUDENTESTI", "MUZEUL DE ARTA",
                          "STR. VULTUR", "LICEUL DE ARTA", "TEATRUL DRAMATIC",
                          "PARC EMINESCU", "CENTRU", "AGENTIA C.F.R.",
                          "PARCARE BANCI", "CEC TIGLINA II", "TIGLINA II",
                          "PRIVILEGE", "FARMACIA HYGEIA", "SERVICE VECHI",
                          "MICRO 19"],
            },
        },
    },
    "106": {
        "route_long_name": "Micro 19 - Gara CFR",
        "route_type": 3,  # bus
        "route_color": "7B1FA2",
        "aliases": {
            "GARA  CFR": "GARA CFR",
            "SERVICE  VECHI": "SERVICE VECHI",
            "STR.GARII": "STR. GARII",
            "C.E.C. TIGLINA II": "CEC TIGLINA II",
        },
        "directions": {
            "TUR": {
                "headsign": "Gara C.F.R.",
                "stops": ["MICRO 19", "BLD. GALATI", "ZENNER", "BLOC A8",
                          "CIMITIR CATUSA", "GRADINITA PRICHINDEL", "NEACSU",
                          "SPITALUL JUDETEAN", "PRIVILEGE", "TIGLINA I",
                          "ROMTELECOM", "MAZEPA", "POTCOAVA DE AUR",
                          "GALERIILE DE ARTA", "UNIVERSITATE", "PARFUMUL TEILOR",
                          "GARA  CFR"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["GARA CFR", "AUTOGARA", "STR.GARII", "F.E.E.A",
                          "C.N.V.A.", "ALBATROS", "CENTRU", "AGENTIA C.F.R.",
                          "PARCARE BANCI", "C.E.C. TIGLINA II", "TIGLINA II",
                          "PRIVILEGE", "FARMACIA HYGEIA", "SERVICE  VECHI",
                          "IATSA", "CIMITIR CATUSA", "BLOC B3", "ZENNER",
                          "FAC. DE MEDICINA", "MICRO 19"],
            },
        },
    },
    "105": {
        "route_long_name": "Micro 19 - Grădina Publică",
        "route_type": 3,  # bus
        "route_color": "1565C0",
        "aliases": {
            "F.E.A.A.": "F.E.E.A",
            "FACULTATEA DE MEDICINA": "FAC. DE MEDICINA",
        },
        "directions": {
            "TUR": {
                "headsign": "Grădina Publică",
                "stops": ["MICRO 19", "BLD. GALATI", "ZENNER", "BLOC A8",
                          "CIMITIR CATUSA", "GRADINITA PRICHINDEL", "NEACSU",
                          "SPITALUL JUDETEAN", "PRIVILEGE", "TIGLINA I",
                          "ROMTELECOM", "MAZEPA", "POTCOAVA DE AUR",
                          "GALERIILE DE ARTA", "UNIVERSITATE", "PARFUMUL TEILOR",
                          "DIRECTIA AGRICOLA", "BLOC IALOMITA", "GRADINA PUBLICA"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["GRADINA PUBLICA", "MUZEUL DE ARTA", "STR. VULTUR",
                          "LICEUL DE ARTA", "STR. GARII", "F.E.A.A.",
                          "C.N.V.A.", "ALBATROS", "CENTRU", "AGENTIA C.F.R.",
                          "PARCARE BANCI", "CEC TIGLINA II", "TIGLINA II",
                          "PRIVILEGE", "FARMACIA HYGEIA", "SERVICE VECHI",
                          "IATSA", "CIMITIR CATUSA", "BLOC B3", "ZENNER",
                          "FACULTATEA DE MEDICINA", "MICRO 19"],
            },
        },
    },
    "43": {
        "route_long_name": "Căminele Combinatului - Piața Energiei",
        "route_type": 3,  # bus
        "route_color": "1E88E5",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "Piața Energiei",
                "stops": ["CAMINELE COMBINATULUI", "PIATA ENERGIEI"],
            },
            "RETUR": {
                "headsign": "Căminele Combinatului",
                "stops": ["PIATA ENERGIEI", "CAMINELE COMBINATULUI"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Shape sources: OSM route relation ids per route/direction.
# Use "osrm" to generate a shape by routing between consecutive stops
# (only when no relation is mapped on OSM yet).
# ---------------------------------------------------------------------------
SHAPES = {
    "102": {"TUR": 7514198, "RETUR": 309380},
    "105": {"TUR": 10177285, "RETUR": 10177284},
    "106": {"TUR": 21211344, "RETUR": 21211343},
    "43": {"TUR": 21213681, "RETUR": 21213682},
}

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def stop_id(code: str) -> str:
    return re.sub(r"\s+", "-", code.replace(".", ""))


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
        except Exception as e:
            last = e
            if attempt < tries:
                print(f"  http error ({e}); retry {attempt}/{tries - 1} in {5 * attempt}s")
                time.sleep(5 * attempt)
    raise last


def fetch_page(url: str, cache_file: Path) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = http_get(url).decode("utf-8", errors="replace")
    cache_file.write_text(data, encoding="utf-8")
    time.sleep(0.3)
    return data


def fetch_schedule(route: str, direction: str, station: str) -> tuple[list[str], list[str]]:
    """Return (weekday times, weekend times) for one station on the site."""
    query = urllib.parse.urlencode({
        "numarTraseu": route,
        "numeStatie": station,
        "turRetur": direction.lower(),
        "variantaStatii": "standard",
    })
    cache = CACHE_DIR / f"{route}_{direction}_{station}.html"
    html = fetch_page(f"{BASE_URL}/veziProgram?{query}", cache)
    m = re.search(r"DE LUNI PÂNĂ VINERI(.*?)WEEKEND ȘI SĂRBĂTORI LEGALE(.*?)</table>",
                  html, re.S)
    if not m:
        raise RuntimeError(f"could not parse timetable for route {route} {direction} {station}")
    return re.findall(r"(\d{2}:\d{2})", m.group(1)), re.findall(r"(\d{2}:\d{2})", m.group(2))


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def overpass(query: str, cache_name: str) -> dict:
    """Run an Overpass query against the first working mirror, caching the result."""
    cache = CACHE_DIR / cache_name
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    last = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = http_get(endpoint,
                            data=urllib.parse.urlencode({"data": query}).encode(),
                            tries=2)
            cache.write_text(data.decode("utf-8", "replace"), encoding="utf-8")
            time.sleep(1)
            return json.loads(data)
        except Exception as e:
            last = e
            print(f"  overpass mirror {endpoint} failed ({e})")
    raise last


def osm_api_relation_members(rel_id: int) -> list[dict]:
    """Fallback: fetch relation geometry from the OSM API (/full, XML)."""
    url = f"https://api.openstreetmap.org/api/0.6/relation/{rel_id}/full"
    xml = http_get(url, tries=3).decode("utf-8", "replace")
    root = ET.fromstring(xml)
    nodes, ways, members = {}, {}, []
    for el in root:
        if el.tag == "node":
            nodes[int(el.get("id"))] = {"lat": float(el.get("lat")),
                                        "lon": float(el.get("lon"))}
        elif el.tag == "way":
            ways[int(el.get("id"))] = [int(nd.get("ref")) for nd in el.findall("nd")]
        elif el.tag == "relation" and int(el.get("id")) == rel_id:
            members = [(m.get("type"), int(m.get("ref")), m.get("role"))
                       for m in el.findall("member")]
    out = []
    for typ, ref, role in members:
        if typ == "way" and ref in ways:
            geom = [nodes[n] for n in ways[ref] if n in nodes]
            if len(geom) >= 2:
                out.append({"type": "way", "ref": ref, "role": role,
                            "geometry": geom})
    if not out:
        raise RuntimeError(f"no way geometry for relation {rel_id}")
    return out


def relation_members(rel_id: int, cache_name: str) -> list[dict]:
    """Relation members with way geometry: Overpass mirrors, then OSM API."""
    try:
        j = overpass(f"[out:json][timeout:60];relation({rel_id});out geom;",
                     cache_name)
        return j["elements"][0]["members"]
    except Exception:
        print(f"  overpass failed for relation {rel_id}; falling back to OSM API")
        members = osm_api_relation_members(rel_id)
        # seed the overpass cache so future runs skip the flaky endpoints
        payload = {"elements": [{"type": "relation", "id": rel_id,
                                 "members": members}]}
        (CACHE_DIR / cache_name).write_text(json.dumps(payload), encoding="utf-8")
        return members


def osm_shape_points(rel_id: int, stops: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Extract an ordered (lat, lon) polyline from an OSM route relation.

    `stops` is the direction's stop sequence used to orient the first way
    (relations sometimes map its first way against the direction of travel).
    """
    members = relation_members(rel_id, f"rel_{rel_id}.json")
    path = []
    for m in members:
        if m.get("type") != "way":
            continue
        pts = [(p["lat"], p["lon"]) for p in m["geometry"]]
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
    """Every stop must be close to the shape and visited in order."""
    tol = (max_dist_m / 111000.0) ** 2
    prev = -1
    for lat, lon in stops:
        best, idx = 1e18, -1
        for i, (pl, pn) in enumerate(pts):
            d = (pl - lat) ** 2 + (pn - lon) ** 2
            if d < best:
                best, idx = d, i
        if best > tol or idx < prev:
            return False
        prev = idx
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


def collect_route(route_id: str, cfg: dict) -> dict:
    """Return dict with 'stops' (unique), 'trips' (rows) and 'stop_times' (rows)."""
    aliases = cfg.get("aliases", {})  # site code -> canonical STOPS code
    canon = lambda code: aliases.get(code, code)
    # 1) fetch and validate per-direction timetables
    times = {}  # times[direction][station][service] = [hh:mm, ...]
    for direction, d in cfg["directions"].items():
        first = d["stops"][0]
        counts = {}
        for station in d["stops"]:
            wd, we = fetch_schedule(route_id, direction, station)
            times.setdefault(direction, {}).setdefault(station, {})["WD"] = wd
            times[direction][station]["WE"] = we
            counts[station] = (len(wd), len(we))
        n_wd, n_we = counts[first]
        for station, (c_wd, c_we) in counts.items():
            if (c_wd, c_we) != (n_wd, n_we):
                raise RuntimeError(
                    f"count mismatch route {route_id} {direction}: {first} "
                    f"({n_wd}/{n_we}) vs {station} ({c_wd}/{c_we})")
        # monotonicity check within each trip
        for service in ("WD", "WE"):
            for i in range(len(times[direction][first][service])):
                prev = None
                for station in d["stops"]:
                    t = times[direction][station][service][i]
                    if prev is not None and t < prev:
                        raise RuntimeError(
                            f"decreasing time route {route_id} {direction} "
                            f"trip {i} at {station}: {prev} -> {t}")
                    prev = t
        print(f"route {route_id} {direction}: {n_wd} WD trips, {n_we} WE trips")

    # 2) unique stops used by this route
    stops = {}
    for cfg_stops in cfg["directions"].values():
        for code in cfg_stops["stops"]:
            c = canon(code)
            if c not in STOPS:
                raise KeyError(f"route {route_id}: stop {code!r} not in STOPS catalog")
            name, lat, lon = STOPS[c]
            stops[stop_id(c)] = {"code": c, "name": name, "lat": lat, "lon": lon}

    # 3) trips + stop_times
    trips, stop_times = [], []
    stop_order = {}
    trip_no = 0
    for direction, d in cfg["directions"].items():
        direction_id = 0 if direction == "TUR" else 1
        shape_id = f"{route_id}-{direction}"
        stop_order[direction] = [STOPS[canon(s)][1:] for s in d["stops"]]
        for service in ("WD", "WE"):
            ntrip = len(times[direction][d["stops"][0]][service])
            for i in range(ntrip):
                trip_no += 1
                tid = f"{route_id}-{direction[0]}-{service}-{i + 1:03d}"
                trips.append((route_id, service, tid, d["headsign"], direction_id, shape_id))
                for seq, station in enumerate(d["stops"], start=1):
                    t = times[direction][station][service][i] + ":00"
                    stop_times.append((tid, t, t, stop_id(canon(station)), seq))
    return {"stops": stops, "trips": trips, "stop_times": stop_times,
            "stop_order": stop_order}


def write_feed(route_ids: list[str]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    all_stops, all_trips, all_st, all_order = {}, [], [], {}
    for rid in route_ids:
        cfg = ROUTES[rid]
        data = collect_route(rid, cfg)
        all_stops.update(data["stops"])
        all_trips.extend(data["trips"])
        all_st.extend(data["stop_times"])
        all_order[rid] = data["stop_order"]

    def wcsv(name: str, header: list[str], rows: list[list]) -> None:
        with open(OUT_DIR / name, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(header) + "\n")
            for r in rows:
                fh.write(",".join(str(x) for x in r) + "\n")

    wcsv("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone",
                        "agency_lang", "agency_phone"],
         [["transurb", "TRANSURB S.A. Galati", "https://transurbgalati.ro",
           "Europe/Bucharest", "ro", "+40 721 111 602"]])

    wcsv("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name",
                        "route_type", "route_color", "route_text_color"],
         [[rid, "transurb", rid, ROUTES[rid]["route_long_name"], ROUTES[rid]["route_type"],
           ROUTES[rid]["route_color"], "FFFFFF"] for rid in route_ids])

    wcsv("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"],
         [[sid, s["name"], f"{s['lat']:.7f}", f"{s['lon']:.7f}"] for sid, s in sorted(all_stops.items())])

    wcsv("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday",
                          "friday", "saturday", "sunday", "start_date", "end_date"],
         [["WD", "1", "1", "1", "1", "1", "0", "0", FEED_START, FEED_END],
          ["WE", "0", "0", "0", "0", "0", "1", "1", FEED_START, FEED_END]])

    wcsv("calendar_dates.txt", ["service_id", "date", "exception_type"],
         [[svc, d, ex] for d in HOLIDAYS_2026 for svc, ex in (("WE", 1), ("WD", 2))])

    wcsv("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign",
                       "direction_id", "shape_id"],
         all_trips)
    wcsv("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id",
                            "stop_sequence"], all_st)

    # shapes: exact road geometry per route/direction
    shape_rows = []
    shape_report = []
    for rid in route_ids:
        for direction in ROUTES[rid]["directions"]:
            shape_id = f"{rid}-{direction}"
            order = all_order[rid][direction]
            src = SHAPES.get(rid, {}).get(direction, "osrm")
            if isinstance(src, int):
                pts = osm_shape_points(src, order)
                if not shape_ok(pts, order):
                    print(f"shape {shape_id}: relation {src} fails check, "
                          f"falling back to OSRM")
                    pts = osrm_shape_points(order)
                    shape_report.append(
                        f"{shape_id}: OSRM (OSM relation {src} fails check)")
                else:
                    print(f"shape {shape_id}: {len(pts)} points (OSM relation {src})")
                    shape_report.append(f"{shape_id}: OSM relation {src}")
            else:
                pts = osrm_shape_points(order)
                print(f"shape {shape_id}: {len(pts)} points (OSRM routing)")
                shape_report.append(f"{shape_id}: OSRM (no OSM relation configured)")
            for seq, (lat, lon) in enumerate(pts, start=1):
                shape_rows.append((shape_id, f"{lat:.6f}", f"{lon:.6f}", seq))
    wcsv("shapes.txt", ["shape_id", "shape_pt_lat", "shape_pt_lon",
                        "shape_pt_sequence"], shape_rows)
    print("\nshape sources:")
    for line in shape_report:
        print("  " + line)

    wcsv("feed_info.txt",
         ["feed_publisher_name", "feed_publisher_url", "feed_lang", "default_lang",
          "feed_start_date", "feed_end_date", "feed_version", "feed_contact_email",
          "feed_contact_url"],
         [["TRANSURB S.A. Galati", "https://transurbgalati.ro", "ro", "ro",
           FEED_START, FEED_END,
           "transurb-" + time.strftime("%Y%m%d") + "-" + "-".join(route_ids),
           "transurbgl@gmail.com", "https://transurbgalati.ro/contact"]])

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(OUT_DIR.glob("*.txt")):
            zf.write(f, f.name)

    print(f"\n{len(all_stops)} stops, {len(all_trips)} trips, "
          f"{len(all_st)} stop_times")
    print(f"feed written to {OUT_DIR} and {ZIP_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate GTFS for TRANSURB Galati")
    parser.add_argument("routes", nargs="*", help="route numbers (default: all configured)")
    args = parser.parse_args()
    route_ids = args.routes or list(ROUTES.keys())
    unknown = [r for r in route_ids if r not in ROUTES]
    if unknown:
        sys.exit(f"unknown route(s): {', '.join(unknown)}; configured: {', '.join(ROUTES)}")
    write_feed(route_ids)


if __name__ == "__main__":
    main()
