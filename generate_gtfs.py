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
import math
import os
import re
import sys
import time
import zipfile
import urllib.error
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
# Stop catalog: canonical code -> (display name, lat, lon, osm_node).
# One entry per physical stop, shared by every route. Declared once here only.
#
# lat/lon and osm_node are the *canonical* platform: the side of the street
# used by the route's forward (TUR) direction. Stops served from a second
# platform in the other direction are NOT listed per route -- the opposite
# platform is read automatically from the OSM route relation at build time
# (see resolve_platforms), which is why adding a route needs no coordinates.
# ---------------------------------------------------------------------------
STOPS = {
    "MICRO 19": ("Micro 19", 45.4133153, 28.0124752, 123745651),
    "NEACSU": ("Neacșu", 45.4169588, 28.0130412, 529895200),
    "SPITALUL JUDETEAN": ("Spitalul Județean", 45.4193509, 28.0177710, 530187911),
    "PRIVILEGE": ("Privilege", 45.4261475, 28.0233600, 6878557416),
    "TIGLINA I": ("Țiglina I", 45.4276057, 28.0281282, 529898235),
    "ROMTELECOM": ("Romtelecom", 45.4286741, 28.0396232, 472614576),
    "MAZEPA": ("Mazepa", 45.4305181, 28.0462169, 530256106),
    "POTCOAVA DE AUR": ("Potcoava de Aur", 45.4327326, 28.0504231, 6875033916),
    "GALERIILE DE ARTA": ("Galeriile de Artă", 45.4354384, 28.0557046, 530256646),
    "UNIVERSITATE": ("Universitate", 45.4392722, 28.0565444, 530257449),
    "PARFUMUL TEILOR": ("Parfumul Teilor", 45.4426716, 28.0559478, 530257932),
    "DIRECTIA AGRICOLA": ("Direcția Agricolă", 45.4454016, 28.0548729, 530258190),
    "BLOC IALOMITA": ("Bloc Ialomița", 45.4495658, 28.0524493, 6879824354),
    "CAMINE STUDENTESTI": ("Cămine Studențești", 45.4539921, 28.0497540, 6879824355),
    "CAMINUL DE BATRANI": ("Căminul de Bătrâni", 45.4580487, 28.0473113, 6879824356),
    "PARC C.F.R.": ("Parc C.F.R.", 45.4623733, 28.0447250, 6879824370),
    "STR. PRUNDULUI": ("Str. Prundului", 45.4630597, 28.0369371, 6895570670),
    "BARIERA TRAIAN": ("Bariera Traian", 45.4653144, 28.0362148, 6895570666),
    "STR. RADU NEGRU": ("Str. Radu Negru", 45.4589088, 28.0465985, 6879824358),
    "A.J.O.F.M.": ("A.J.O.F.M.", 45.4569492, 28.0477278, 6879824373),
    "MUZEUL DE ARTA": ("Muzeul de Artă", 45.4506850, 28.0515363, 6879824376),
    "STR. VULTUR": ("Str. Vultur", 45.4483643, 28.0529454, 6879824377),
    "LICEUL DE ARTA": ("Liceul de Artă", 45.4450969, 28.0548432, 534270716),
    "TEATRUL DRAMATIC": ("Teatrul Dramatic", 45.4413412, 28.0561051, 6879824378),
    "PARC EMINESCU": ("Parc Eminescu", 45.4361285, 28.0557148, 1932207593),
    "CENTRU": ("Centru", 45.4342165, 28.0539635, 530256687),
    "AGENTIA C.F.R.": ("Agenția C.F.R.", 45.4310705, 28.0466092, 530256244),
    "PARCARE BANCI": ("Parcare Bănci", 45.4292945, 28.0414140, 6875960831),
    "CEC TIGLINA II": ("CEC Țiglina II", 45.4285875, 28.0355499, 530256058),
    "TIGLINA II": ("Țiglina II", 45.4279325, 28.0280075, 530776769),
    "FARMACIA HYGEIA": ("Farmacia Hygeia", 45.4191133, 28.0168623, 6893391135),
    "SERVICE VECHI": ("Service Vechi", 45.4168495, 28.0124289, 6893391133),
    "BLD. GALATI": ("Bld. Galați", 45.4108853, 28.0147926, 4907189684),
    "ZENNER": ("Zenner", 45.4093346, 28.0111119, 6892672447),
    "BLOC A8": ("Bloc A8", 45.4113856, 28.0083163, 6892672446),
    "CIMITIR CATUSA": ("Cimitir Cătușa", 45.4130442, 28.0063495, 6893391129),
    "GRADINITA PRICHINDEL": ("Grădinița Prichindel", 45.4139447, 28.0070235, 6892672442),
    "GARA CFR": ("Gara C.F.R.", 45.4444747, 28.0598390, 6875107385),
    "AUTOGARA": ("Autogară", 45.4433347, 28.0585570, 6881503385),
    "STR. GARII": ("Str. Gării", 45.4438402, 28.0537161, 534270437),
    "F.E.E.A": ("F.E.E.A.", 45.4432509, 28.0517570, 534268996),
    "C.N.V.A.": ("C.N.V.A.", 45.4406250, 28.0521298, 534268981),
    "ALBATROS": ("Albatros", 45.4369171, 28.0526448, 614962899),
    "IATSA": ("IATSA", 45.4142302, 28.0072252, 6893391131),
    "BLOC B3": ("Bloc B3", 45.4110405, 28.0084235, 6893391127),
    "FAC. DE MEDICINA": ("Fac. de Medicină", 45.4106737, 28.0149120, 6893391123),
    "GRADINA PUBLICA": ("Grădina Publică", 45.4513651, 28.0510709, 14086091994),
    "CAMINELE COMBINATULUI": ("Căminele Combinatului", 45.4420380, 28.0133603, 14086450125),
    "PIATA ENERGIEI": ("Piața Energiei", 45.4398644, 28.0205822, 6896006837),
    "DIMITRIE CANTEMIR": ("Dimitrie Cantemir", 45.3880242, 28.0100516, 6896665346),
    "BLOC S13": ("Bloc S13", 45.3881889, 28.0139244, 6896665345),
    "UNIV. DANUBIUS": ("Universitatea Danubius", 45.4037799, 28.0152731, 6896665344),
    "SELGROS": ("Selgros", 45.4057111, 28.0185789, 14086946895),
    "CENTRUL DELFINUL": ("Centrul Delfinul", 45.4049966, 28.0220893, 14086946893),
    "ATAC": ("Auchan", 45.4039373, 28.0200582, 14086946891),
    "DANUBIUS": ("Danubius", 45.4037967, 28.0160994, 6897986189),
    "SCOALA 40": ("Școala Nr. 40", 45.4173262, 28.0101278, 6894593244),
    "PETRU GROZA": ("Petru Groza", 45.4207347, 28.0108704, 14088247742),
    "CARTIER LOCUINTE SOCIALE M17": ("Cartier Locuințe Sociale M17", 45.4231133, 28.0071980, 14088247741),
    "MATHAUS": ("Mathaus", 45.4244505, 28.0051981, 14088247738),
    "DEDEMAN": ("Dedeman", 45.4261431, 28.0088618, 14088247737),
    "BLOC L3": ("Bloc L3", 45.4213951, 28.0132961, 14088247734),
    "PIATA MICRO 17": ("Piața Micro 17", 45.4193765, 28.0138592, 6896006822),
    "SCOALA NR. 40": ("Școala Nr. 40", 45.4170616, 28.0093162, 6896163912),
    "LEVADITTI": ("Levaditti", 45.4348774, 28.0147279, 14088449870),
    "BL. BUJOR/NUFAR": ("Bloc Bujor/Nufăr", 45.4317467, 28.0129793, 14088449868),
    "CIMITIR SF. LAZAR": ("Cimitirul Sfântul Lazăr", 45.4274038, 28.0116835, 1720639175),
    "TIGLINA III": ("Țiglina III", 45.4265265, 28.0138162, 1932184795),
    "MINION": ("Minion", 45.4267704, 28.0164596, 1932184796),
    "KAUFLAND": ("Kaufland", 45.4272284, 28.0213826, 6874360932),
    "PIATA TIGLINA I": ("Piața Țiglina I", 45.4253484, 28.0292427, 6905457936),
    "GAMACRIS": ("Gamacris", 45.4223635, 28.0287486, 6905457934),
    "CLOSCA": ("Cloșca", 45.4208957, 28.0277397, 14091746744),
    "SIDERURGISTUL": ("Siderurgistul", 45.4195125, 28.0286957, 14088449866),
    "TRECERE BAC": ("Trecere BAC", 45.4167491, 28.0327079, 6905457933),
    "PIATA CENTRALA": ("Piața Centrală", 45.4379920, 28.0491857, 6898532656),
    "BAIA COMUNALA": ("Baia Comunală", 45.4433732, 28.0470814, 6906785250),
    "SPITALUL MILITAR": ("Spitalul Militar", 45.4500767, 28.0436126, 6895095250),
    "STR. CEZAR": ("Str. Cezar", 45.4525210, 28.0423838, 6895095246),
    "SPITAL MUNICIPAL": ("Spitalul Municipal", 45.4591894, 28.0389306, 6895570669),
    "DUMBRAVA ROSIE": ("Str. Dumbrava Roșie", 45.4693589, 28.0342615, 6899023168),
    "CARTIERUL NOU": ("Cartierul Nou", 45.4723052, 28.0340765, 6899023166),
    "AUTOMECANICA": ("Automecanica", 45.4749344, 28.0339283, 6899023165),
    "FITOSANITAR": ("Fitosanitar", 45.4812757, 28.0336127, 6899023163),
    "METRO": ("Metro", 45.4842680, 28.0325695, 6899023161),
    # Route 35 runs along Strada Traian, where two stops repeat street names
    # already used by the Strada Domnească stops above (~600 m away). Separate
    # physical stops, so separate catalog entries.
    "STR. VULTUR / TRAIAN": ("Str. Vultur", 45.4465182, 28.0454307, 6906785248),
    "STR. RADU NEGRU / TRAIAN": ("Str. Radu Negru", 45.4564418, 28.0403786, 6895095244),
    # Route 10 stops (port/shipyard area and Țiglina side streets)
    "BLD. DUNAREA": ("Bld. Dunărea", 45.4099336, 28.0176862, 6896713532),
    "CINEMA DACIA": ("Cinema Dacia", 45.4128375, 28.0168936, 6896713530),
    "BLD. OTELARILOR": ("Bld. Oțelarilor", 45.4143701, 28.0191223, 6896713528),
    "BLOC D19": ("Bloc D19", 45.4163758, 28.0229189, 6896713526),
    "SALA SPORTURILOR": ("Sala Sporturilor", 45.4195388, 28.0214699, 6896716339),
    "ORASELUL COPIILOR": ("Orășelul Copiilor", 45.4263668, 28.0323841, 2904472149),
    "COMPLEX FRANCEZI": ("Complex Francezi", 45.4226341, 28.0337243, 6875033913),
    "BLOC E6": ("Bloc E6", 45.4236003, 28.0374999, 6875033914),
    "CENTRUL DE RECOLTARE": ("Centrul de Recoltare", 45.4258794, 28.0386478, 6875033915),
    "COMPLEX SPICUL": ("Complex Spicul", 45.4322606, 28.0561323, 6892672437),
    "NAVROM": ("Navrom", 45.4317407, 28.0617947, 6892672435),
    "LICEUL DE MARINA": ("Liceul de Marină", 45.4341334, 28.0663207, 6896819822),
    "ANA IPATESCU": ("Ana Ipătescu", 45.4370917, 28.0680707, 14090438578),
    "STR. ALEX. MORUZZI": ("Str. Alex. Moruzzi", 45.4403339, 28.0659666, 14090438573),
    "MORUZZI": ("Moruzzi", 45.4419211, 28.0700939, 6896966052),
    "STR. LEMNARI": ("Str. Lemnari", 45.4449763, 28.0754757, 6896966050),
    "EEKELS": ("Eekels", 45.4454240, 28.0805456, 6896966048),
    "DAMEN": ("Damen", 45.4440364, 28.0829432, 6896966047),
    "TRIBUNAL": ("Tribunal", 45.4261655, 28.0380834, 6878887385),
    "BLOC O": ("Bloc O", 45.4164428, 28.0227571, 6897924687),
    "STR. OTELARILOR": ("Str. Oțelarilor", 45.4145323, 28.0190191, 6897924685),
    # Route 31 stops (western suburbs toward Barboși)
    "POLIGON": ("Poligon", 45.4081074, 27.9980931, 14090544330),
    "RELEU": ("Releu", 45.4052609, 27.9923930, 14090544327),
    "GARA BARBOSI": ("Gara Barboși", 45.4018959, 27.9885697, 14090544331),
    "BARBOSI": ("Barboși", 45.3979735, 27.9857382, 14090544333),
    # Route 33 stops
    "PLAJA DUNAREA": ("Plaja Dunărea", 45.4137758, 28.0292958, 14090652421),
    # Route 34 stops (Micro 13 - Intfor, via Strada Brăilei corridor)
    "MICRO 13": ("Micro 13", 45.4505622, 28.0196142, 6898532664),
    "COMPLEX IONESCU": ("Complex Ionescu", 45.4540387, 28.0186156, 8689062536),
    "STR. IONEL FERNIC": ("Str. Ionel Fernic", 45.4566036, 28.0188122, 6898532661),
    "STR. TRAIAN VUIA": ("Str. Traian Vuia", 45.4579682, 28.0222633, 6898532659),
    "PIATA MICRO 39": ("Piața Micro 39", 45.4548291, 28.0247599, 6895095257),
    "KAUFLAND (PATINOAR)": ("Kaufland (Patinoar)", 45.4550978, 28.0289548, 6960962993),
    "PATINOAR": ("Patinoar", 45.4554069, 28.0335182, 6895095248),
    "TREFO": ("TREFO", 45.4532279, 28.0345214, 6895095254),
    "LICEUL C.F.R.": ("Liceul C.F.R.", 45.4472971, 28.0351504, 6899152585),
    "CIMITIR ETERNITATEA": ("Cimitirul Eternitatea", 45.4424960, 28.0357115, 6899129583),
    "STR. TECUCI": ("Str. Tecuci", 45.4381813, 28.0361388, 6899129581),
    "STR. CRIZANTEMELOR": ("Str. Crizantemelor", 45.4343852, 28.0364714, 6899129579),
    "INTFOR": ("Intfor", 45.4371997, 28.0710875, 6897791667),
    "HOTEL SOFIN": ("Hotel Sofin", 45.4294194, 28.0371610, 6899023184),
    "STR. M. KOGALNICEANU": ("Str. M. Kogălniceanu", 45.4343522, 28.0366766, 6899023182),
    "MEHID": ("Mehid", 45.4487085, 28.0353274, 6899023176),
    "COMAT": ("COMAT", 45.4553759, 28.0289514, 6924935089),
    "BLOC L": ("Bloc L", 45.4508738, 28.0234253, 6896006843),
    # Route 30 stops (Micro 19 - ADA Motors via Str. Brăilei)
    "TIRIGHINA": ("Tirighina", 45.4084178, 27.9939702, 14093112491),
    "STR. BRAILEI": ("Str. Brăilei", 45.4066105, 27.988187, 14093112489),
    "ADA MOTORS (BORCAN)": ("ADA Motors (Borcan)", 45.4080801, 27.9770381, 14093112487),
}

# ---------------------------------------------------------------------------
# Route configuration.
#
# Add a new route here: `stops` lists the stop codes in the order shown on
# the "veziTraseu" page (TUR and RETUR). Codes must exist in STOPS above.
#
# If the site spells a stop differently for this route than in STOPS (e.g.
# extra spaces, abbreviations), map it to the canonical code under `aliases`.
#
# No coordinates go here. Where a direction stops on the opposite side of the
# street, the platform is resolved from the route's OSM relation in SHAPES, so
# the same stop is never re-declared across routes.
# ---------------------------------------------------------------------------
ROUTES = {
    "102": {
        "route_long_name": "Micro 19 - Bariera Traian",
        "route_type": 11,  # trolleybus
        "route_color": "26D100",
        "route_text_color": "000000",
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
        "route_color": "400244",
        "route_text_color": "FFFFFF",
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
        "route_color": "FB483A",
        "route_text_color": "000000",
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
        "route_color": "AA00FF",
        "route_text_color": "FFFFFF",
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
    "41": {
        "route_long_name": "Dimitrie Cantemir - Micro 19",
        "route_type": 3,  # bus
        "route_color": "4054C7",
        "route_text_color": "FFFFFF",
        "aliases": {
            "FACULTATEA DE MEDICINA": "FAC. DE MEDICINA",
        },
        "directions": {
            "TUR": {
                "headsign": "Micro 19",
                "stops": ["DIMITRIE CANTEMIR", "BLOC S13", "UNIV. DANUBIUS",
                          "SELGROS", "CENTRUL DELFINUL", "ATAC", "DANUBIUS",
                          "FACULTATEA DE MEDICINA", "MICRO 19"],
            },
            "RETUR": {
                "headsign": "Dimitrie Cantemir",
                "stops": ["MICRO 19", "BLD. GALATI", "DANUBIUS", "SELGROS",
                          "CENTRUL DELFINUL", "ATAC", "BLOC S13",
                          "DIMITRIE CANTEMIR"],
            },
        },
    },
    "38": {
        "route_long_name": "Micro 19 - Cartier Locuințe Sociale Micro 17 (buclă)",
        "route_type": 3,  # bus
        "route_color": "C350E9",
        "route_text_color": "000000",
        "aliases": {
            "MICRO 19 (SOSIRE)": "MICRO 19",
        },
        "directions": {
            "TUR": {
                "headsign": "Micro 19",
                "stops": ["MICRO 19", "SCOALA 40", "PETRU GROZA",
                          "CARTIER LOCUINTE SOCIALE M17", "MATHAUS", "DEDEMAN",
                          "BLOC L3", "PIATA MICRO 17", "SCOALA NR. 40",
                          "IATSA", "GRADINITA PRICHINDEL", "MICRO 19 (SOSIRE)"],
            },
        },
    },
    "37": {
        "route_long_name": "Levaditti - Trecere BAC",
        "route_type": 3,  # bus
        "route_color": "F5996A",
        "route_text_color": "000000",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "Trecere BAC",
                "stops": ["LEVADITTI", "BL. BUJOR/NUFAR", "CIMITIR SF. LAZAR",
                          "TIGLINA III", "MINION", "KAUFLAND", "TIGLINA I",
                          "PIATA TIGLINA I", "GAMACRIS", "SIDERURGISTUL",
                          "TRECERE BAC"],
            },
            "RETUR": {
                "headsign": "Levaditti",
                "stops": ["TRECERE BAC", "GAMACRIS", "PIATA TIGLINA I",
                          "TIGLINA II", "KAUFLAND", "MINION", "TIGLINA III",
                          "CIMITIR SF. LAZAR", "BL. BUJOR/NUFAR", "LEVADITTI"],
            },
        },
    },
    "35": {
        "route_long_name": "Piața Centrală - Metro",
        "route_type": 3,  # bus
        "route_color": "89B1F5",
        "route_text_color": "000000",
        "aliases": {
            # on Strada Traian, not the Strada Domnească stops of the same name
            "STR. VULTUR": "STR. VULTUR / TRAIAN",
            "STR. RADU NEGRU": "STR. RADU NEGRU / TRAIAN",
        },
        "directions": {
            "TUR": {
                "headsign": "Metro",
                "stops": ["PIATA CENTRALA", "BAIA COMUNALA", "STR. VULTUR",
                          "SPITALUL MILITAR", "STR. CEZAR", "STR. RADU NEGRU",
                          "SPITAL MUNICIPAL", "STR. PRUNDULUI", "BARIERA TRAIAN",
                          "DUMBRAVA ROSIE", "CARTIERUL NOU", "AUTOMECANICA",
                          "FITOSANITAR", "METRO"],
            },
            "RETUR": {
                "headsign": "Piața Centrală",
                "stops": ["METRO", "FITOSANITAR", "AUTOMECANICA",
                          "CARTIERUL NOU", "DUMBRAVA ROSIE", "BARIERA TRAIAN",
                          "STR. PRUNDULUI", "SPITAL MUNICIPAL", "STR. RADU NEGRU",
                          "STR. CEZAR", "SPITALUL MILITAR", "STR. VULTUR",
                          "BAIA COMUNALA", "PIATA CENTRALA"],
            },
        },
    },
    "9": {
        "route_long_name": "Cimitirul Sfântul Lazăr - Gara C.F.R.",
        "route_type": 3,  # bus
        "route_color": "E1B348",
        "route_text_color": "000000",
        "aliases": {
            "GALERIA DE ARTA": "GALERIILE DE ARTA",
            "GARA C.F.R.": "GARA CFR",
            "F.S.E.A.": "F.E.E.A",
            "STR. GARII": "STR. GARII",
        },
        "directions": {
            "TUR": {
                "headsign": "Gara C.F.R.",
                "stops": ["CIMITIR SF. LAZAR", "TIGLINA III", "MINION",
                          "KAUFLAND", "TIGLINA I", "ROMTELECOM", "MAZEPA",
                          "POTCOAVA DE AUR", "GALERIA DE ARTA", "UNIVERSITATE",
                          "PARFUMUL TEILOR", "GARA C.F.R."],
            },
            "RETUR": {
                "headsign": "Cimitirul Sfântul Lazăr",
                "stops": ["GARA C.F.R.", "AUTOGARA", "STR. GARII", "F.S.E.A.",
                          "C.N.V.A.", "ALBATROS", "CENTRU", "AGENTIA C.F.R.",
                          "PARCARE BANCI", "CEC TIGLINA II", "TIGLINA II",
                          "KAUFLAND", "MINION", "TIGLINA III",
                          "CIMITIR SF. LAZAR"],
            },
        },
    },
    "10": {
        "route_long_name": "Micro 19 - Damen",
        "route_type": 3,  # bus
        "route_color": "F99BAD",
        "route_text_color": "000000",
        "aliases": {
            "AGENTIA CFR": "AGENTIA C.F.R.",
        },
        "directions": {
            "TUR": {
                "headsign": "Damen",
                "stops": ["MICRO 19", "BLD. GALATI", "BLD. DUNAREA",
                          "CINEMA DACIA", "BLD. OTELARILOR", "BLOC D19",
                          "SALA SPORTURILOR", "PRIVILEGE", "TIGLINA I",
                          "ORASELUL COPIILOR", "COMPLEX FRANCEZI", "BLOC E6",
                          "CENTRUL DE RECOLTARE", "ROMTELECOM", "MAZEPA",
                          "POTCOAVA DE AUR", "COMPLEX SPICUL", "NAVROM",
                          "LICEUL DE MARINA", "ANA IPATESCU",
                          "STR. ALEX. MORUZZI", "MORUZZI", "STR. LEMNARI",
                          "EEKELS", "DAMEN"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["DAMEN", "EEKELS", "STR. LEMNARI", "MORUZZI",
                          "STR. ALEX. MORUZZI", "ANA IPATESCU",
                          "LICEUL DE MARINA", "NAVROM", "COMPLEX SPICUL",
                          "CENTRU", "AGENTIA CFR", "PARCARE BANCI",
                          "TRIBUNAL", "BLOC E6", "COMPLEX FRANCEZI",
                          "ORASELUL COPIILOR", "TIGLINA II", "PRIVILEGE",
                          "SALA SPORTURILOR", "BLOC O", "STR. OTELARILOR",
                          "CINEMA DACIA", "MICRO 19"],
            },
        },
    },
    "31": {
        "route_long_name": "Micro 19 - Barboși",
        "route_type": 3,  # bus
        "route_color": "A94023",
        "route_text_color": "FFFFFF",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "Barboși",
                "stops": ["MICRO 19", "IATSA", "POLIGON", "RELEU",
                          "GARA BARBOSI", "BARBOSI"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["BARBOSI", "GARA BARBOSI", "RELEU", "POLIGON",
                          "GRADINITA PRICHINDEL", "MICRO 19"],
            },
        },
    },
    "32": {
        "route_long_name": "Micro 19 - Plaja Dunărea",
        "route_type": 3,  # bus
        "route_color": "039BE5",
        "route_text_color": "000000",
        "aliases": {},
        "service_days": "TF",  # Tuesday-Friday (no Monday service)
        "directions": {
            "TUR": {
                "headsign": "Plaja Dunărea",
                "stops": ["MICRO 19", "NEACSU", "SPITALUL JUDETEAN",
                          "PRIVILEGE", "CLOSCA", "SIDERURGISTUL",
                          "PLAJA DUNAREA"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["PLAJA DUNAREA", "CLOSCA", "PRIVILEGE",
                          "FARMACIA HYGEIA", "SERVICE VECHI", "MICRO 19"],
            },
        },
    },
    "33": {
        "route_long_name": "Țiglina II - Plaja Dunărea",
        "route_type": 3,  # bus
        "route_color": "006064",
        "route_text_color": "FFFFFF",
        "aliases": {},
        "service_days": "TF",  # Tuesday-Friday (no Monday service)
        "directions": {
            "TUR": {
                "headsign": "Plaja Dunărea",
                "stops": ["TIGLINA II", "TIGLINA I", "PIATA TIGLINA I",
                          "GAMACRIS", "SIDERURGISTUL", "PLAJA DUNAREA"],
            },
            "RETUR": {
                "headsign": "Țiglina II",
                "stops": ["PLAJA DUNAREA", "GAMACRIS", "PIATA TIGLINA I",
                          "TIGLINA II"],
            },
        },
    },
    "34": {
        "route_long_name": "Micro 13 - Intfor",
        "route_type": 3,  # bus
        "route_color": "F1EB2C",
        "route_text_color": "000000",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "Intfor",
                "stops": ["MICRO 13", "COMPLEX IONESCU", "STR. IONEL FERNIC",
                          "STR. TRAIAN VUIA", "PIATA MICRO 39",
                          "KAUFLAND (PATINOAR)", "PATINOAR", "TREFO",
                          "LICEUL C.F.R.", "CIMITIR ETERNITATEA", "STR. TECUCI",
                          "STR. CRIZANTEMELOR", "ROMTELECOM", "MAZEPA",
                          "POTCOAVA DE AUR", "COMPLEX SPICUL", "NAVROM",
                          "LICEUL DE MARINA", "ANA IPATESCU",
                          "STR. ALEX. MORUZZI", "INTFOR"],
            },
            "RETUR": {
                "headsign": "Micro 13",
                "stops": ["INTFOR", "LICEUL DE MARINA", "NAVROM",
                          "COMPLEX SPICUL", "CENTRU", "AGENTIA C.F.R.",
                          "PARCARE BANCI", "HOTEL SOFIN",
                          "STR. M. KOGALNICEANU", "STR. TECUCI",
                          "CIMITIR ETERNITATEA", "MEHID", "TREFO", "COMAT",
                          "PIATA MICRO 39", "BLOC L", "MICRO 13"],
            },
        },
    },
    "30": {
        "route_long_name": "Micro 19 - ADA Motors (Borcan)",
        "route_type": 3,  # bus
        "route_color": "303030",
        "route_text_color": "FFFFFF",
        "aliases": {},
        "directions": {
            "TUR": {
                "headsign": "ADA Motors (Borcan)",
                "stops": ["MICRO 19", "IATSA", "TIRIGHINA", "STR. BRAILEI",
                          "ADA MOTORS (BORCAN)"],
            },
            "RETUR": {
                "headsign": "Micro 19",
                "stops": ["ADA MOTORS (BORCAN)", "STR. BRAILEI", "TIRIGHINA",
                          "GRADINITA PRICHINDEL", "MICRO 19"],
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
    "41": {"TUR": 21214588, "RETUR": 21214510},
    "38": {"TUR": 21216887},
    "37": {"TUR": 21217269, "RETUR": 21217291},
    "35": {"TUR": 21217560, "RETUR": 21217559},
    "9": {"TUR": 309379, "RETUR": 10154626},
    "10": {"TUR": 358092, "RETUR": 10176664},
    "31": {"TUR": 21222269, "RETUR": 21222268},
    "32": {"TUR": 21223845, "RETUR": 21223844},
    "33": {"TUR": 21222431, "RETUR": 21222473},
    "34": {"TUR": 10188176, "RETUR": 10188475},
    "30": {"TUR": 21226359, "RETUR": 21226358},
}

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def stop_id(code: str) -> str:
    """Slug for a catalog code: 'STR. VULTUR / TRAIAN' -> 'STR-VULTUR-TRAIAN'."""
    return re.sub(r"-+", "-", re.sub(r"[^A-Z0-9]+", "-",
                                     code.replace(".", ""))).strip("-")


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


def fetch_page(url: str, cache_file: Path) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = http_get(url).decode("utf-8", errors="replace")
    cache_file.write_text(data, encoding="utf-8")
    time.sleep(0.6)
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
    m = re.search(r"DE \w+ PÂNĂ VINERI(.*?)WEEKEND ȘI SĂRBĂTORI LEGALE(.*?)</table>",
                  html, re.S)
    if m:
        return re.findall(r"(\d{2}:\d{2})", m.group(1)), re.findall(r"(\d{2}:\d{2})", m.group(2))
    # Weekday-only routes: no weekend section on the page
    m_wd = re.search(r"DE \w+ PÂNĂ VINERI(.*?)</table>", html, re.S)
    if m_wd:
        return re.findall(r"(\d{2}:\d{2})", m_wd.group(1)), []
    raise RuntimeError(f"could not parse timetable for route {route} {direction} {station}")


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
    print(f"    overpass: querying {cache_name}...", flush=True)
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
            print(f"    overpass mirror {endpoint} failed ({e})", flush=True)
    raise last


def osm_api_relation_members(rel_id: int) -> list[dict]:
    """Fallback: fetch relation geometry from the OSM API (/full, XML)."""
    print(f"    osm api: fetching relation {rel_id}/full...", flush=True)
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
        if typ == "node" and ref in nodes:
            out.append({"type": "node", "ref": ref, "role": role,
                        "lat": nodes[ref]["lat"], "lon": nodes[ref]["lon"]})
        elif typ == "way" and ref in ways:
            geom = [nodes[n] for n in ways[ref] if n in nodes]
            if len(geom) >= 2:
                out.append({"type": "way", "ref": ref, "role": role,
                            "geometry": geom})
    if not any(m["type"] == "way" for m in out):
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


PLATFORM_ROLES = ("platform", "platform_entry_only", "platform_exit_only")

# A resolved platform further than this from the catalog position is treated as
# a different physical stop that happens to share a name (Galați has several),
# not as the opposite side of the same street.
PLATFORM_MAX_M = 200.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(a))


def relation_platforms(rel_id: int) -> list[tuple[int, float, float]]:
    """Ordered (osm_id, lat, lon) platforms of an OSM route relation.

    Route relations list their platforms in travel order, so this is the
    direction's stop sequence with the exact per-direction platform position
    (which side of the street the vehicle actually stops at).
    """
    out = []
    for m in relation_members(rel_id, f"rel_{rel_id}.json"):
        if m.get("role") not in PLATFORM_ROLES:
            continue
        if m["type"] == "node" and "lat" in m:
            out.append((m["ref"], m["lat"], m["lon"]))
        elif m.get("geometry"):  # platform mapped as a way: use its midpoint
            pts = [p for p in m["geometry"] if p]
            if pts:
                out.append((m["ref"],
                            sum(p["lat"] for p in pts) / len(pts),
                            sum(p["lon"] for p in pts) / len(pts)))
    return out


def resolve_platforms(route_id: str, direction: str, codes: list[str],
                      canon) -> dict[str, tuple[float, float]]:
    """Map a direction's stop codes to their OSM platform positions.

    Returns {code: (lat, lon)} for the stops whose platform in this direction
    differs from the canonical catalog position. Falls back to an empty dict
    (catalog positions everywhere) when the relation cannot be used, so a
    stale or half-mapped relation degrades instead of corrupting the feed.
    """
    rel = SHAPES.get(route_id, {}).get(direction)
    if not isinstance(rel, int):
        return {}
    try:
        plats = relation_platforms(rel)
    except Exception as e:
        print(f"  route {route_id} {direction}: cannot read platforms "
              f"from relation {rel} ({e}); using catalog positions")
        return {}
    if len(plats) != len(codes):
        print(f"  route {route_id} {direction}: relation {rel} has "
              f"{len(plats)} platforms but the route has {len(codes)} stops; "
              f"using catalog positions")
        return {}

    out = {}
    for code, (osm_id, lat, lon) in zip(codes, plats):
        c = canon(code)
        expected = STOPS[c][3]
        if osm_id == expected:
            continue  # canonical platform, keep the catalog entry
        cat_lat, cat_lon = STOPS[c][1:3]
        if haversine(cat_lat, cat_lon, lat, lon) > PLATFORM_MAX_M:
            print(f"  route {route_id} {direction}: platform n{osm_id} for "
                  f"{c!r} is {haversine(cat_lat, cat_lon, lat, lon):.0f}m from "
                  f"the catalog position; using catalog position")
            continue
        out[code] = (lat, lon)
    return out


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


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def align_times(station_times: list[list[str]]) -> list[list[str | None]]:
    """Align each station's sorted times to the trips.

    The first station defines the trips. Later stations may have fewer times,
    meaning some trips do not stop there (the time is set to None).
    """
    n = len(station_times[0])
    rows = [[None] * n for _ in station_times]
    rows[0] = list(station_times[0])
    for k in range(1, len(station_times)):
        tk = station_times[k]
        if len(tk) == n:
            rows[k] = list(tk)
            continue
        if len(tk) > n:
            raise RuntimeError(f"station {k} has {len(tk)} times, more than {n} trips")
        j = 0
        for i in range(n):
            if j >= len(tk):
                break
            t = tk[j]
            prev = station_times[0][i]
            nxt = station_times[0][i + 1] if i + 1 < n else None
            # a time belongs to trip i if it falls inside trip i's slot and
            # the trip can plausibly reach this station within 45 minutes
            if t >= prev and (nxt is None or t < nxt) and _minutes(t) - _minutes(prev) <= 45:
                rows[k][i] = t
                j += 1
            # otherwise trip i skips this station
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


def collect_route(route_id: str, cfg: dict) -> dict:
    """Return dict with 'stops' (unique), 'trips' (rows) and 'stop_times' (rows)."""
    aliases = cfg.get("aliases", {})  # site code -> canonical STOPS code
    canon = lambda code: aliases.get(code, code)
    # 1) fetch per-direction timetables
    times = {}  # times[direction][station][service] = [hh:mm, ...]
    for direction, d in cfg["directions"].items():
        n_stops = len(d["stops"])
        for i, station in enumerate(d["stops"], 1):
            print(f"  [{direction} {i}/{n_stops}] {station}", flush=True)
            wd, we = fetch_schedule(route_id, direction, station)
            times.setdefault(direction, {}).setdefault(station, {})["WD"] = wd
            times[direction][station]["WE"] = we

    # 2) resolve each direction's platforms from its OSM route relation, then
    #    collect the unique stops (a stop served from a different platform in
    #    this direction gets a direction-suffixed stop id)
    resolved = {}
    for direction, d in cfg["directions"].items():
        rel = SHAPES.get(route_id, {}).get(direction)
        if isinstance(rel, int):
            print(f"  [platforms] {direction}: fetching relation {rel}...", flush=True)
        resolved[direction] = resolve_platforms(route_id, direction, d["stops"], canon)
    stops = {}
    for direction, d in cfg["directions"].items():
        platforms = resolved[direction]
        for code in d["stops"]:
            c = canon(code)
            if c not in STOPS:
                raise KeyError(f"route {route_id}: stop {code!r} not in STOPS catalog")
            name, lat, lon = STOPS[c][:3]
            if code in platforms:
                lat, lon = platforms[code]
                sid = f"{stop_id(c)}-{direction}"
            else:
                sid = stop_id(c)
            stops.setdefault(sid, {"code": c, "name": name, "lat": lat, "lon": lon})

    # 3) trips + stop_times
    trips, stop_times = [], []
    stop_order = {}
    trip_no = 0
    for direction, d in cfg["directions"].items():
        direction_id = 0 if direction == "TUR" else 1
        shape_id = f"{route_id}-{direction}"
        platforms = resolved[direction]

        def sid_for(code, platforms=platforms, direction=direction):
            if code in platforms:
                return f"{stop_id(canon(code))}-{direction}"
            return stop_id(canon(code))

        def eff_pos(code, platforms=platforms):
            if code in platforms:
                return platforms[code]
            return STOPS[canon(code)][1:3]

        stop_order[direction] = [eff_pos(s) for s in d["stops"]]
        wd_service = cfg.get("service_days", "WD")
        for service in ("WD", "WE"):
            svc_id = wd_service if service == "WD" else "WE"
            station_times = [times[direction][station][service]
                             for station in d["stops"]]
            if not station_times[0]:
                continue  # no service in this period (e.g. weekday-only route)
            rows = align_times(station_times)
            n = len(rows[0])
            skipped = sum(1 for k in range(1, len(rows))
                          for i in range(n) if rows[k][i] is None)
            if skipped:
                print(f"  route {route_id} {direction} {service}: "
                      f"{skipped} skipped stop visits")
            for i in range(n):
                trip_no += 1
                tid = f"{route_id}-{direction[0]}-{svc_id}-{i + 1:03d}"
                trips.append((route_id, svc_id, tid, d["headsign"],
                              direction_id, shape_id))
                for k, station in enumerate(d["stops"], start=1):
                    t = rows[k - 1][i]
                    if t is None:
                        continue
                    t = t + ":00"
                    stop_times.append((tid, t, t, sid_for(station), k))
        print(f"route {route_id} {direction}: "
              f"{len(times[direction][d['stops'][0]]['WD'])} WD trips, "
              f"{len(times[direction][d['stops'][0]]['WE'])} WE trips")
    return {"stops": stops, "trips": trips, "stop_times": stop_times,
            "stop_order": stop_order}


def prefetch_relations(route_ids: list[str]) -> None:
    """Fetch all uncached OSM relations in a single Overpass query."""
    needed = []
    for rid in route_ids:
        for direction, rel_id in SHAPES.get(rid, {}).items():
            if isinstance(rel_id, int):
                cache = CACHE_DIR / f"rel_{rel_id}.json"
                if not cache.exists():
                    needed.append(rel_id)
    if not needed:
        return
    print(f"\nPrefetching {len(needed)} OSM relation(s): {needed}", flush=True)
    # Fetch all in one query
    id_list = "".join(f"relation({r});" for r in needed)
    query = f"[out:json][timeout:120];({id_list});out geom;"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    last = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            print(f"  trying {endpoint}...", flush=True)
            data = http_get(endpoint,
                            data=urllib.parse.urlencode({"data": query}).encode(),
                            tries=2, timeout=120)
            j = json.loads(data.decode("utf-8", "replace"))
            # Split into individual cache files
            for el in j.get("elements", []):
                rel_id = el["id"]
                cache = CACHE_DIR / f"rel_{rel_id}.json"
                payload = json.dumps({"elements": [el]})
                cache.write_text(payload, encoding="utf-8")
            print(f"  cached {len(j.get('elements', []))} relations", flush=True)
            time.sleep(1)
            return
        except Exception as e:
            last = e
            print(f"  failed ({e})", flush=True)
    print(f"  prefetch failed; will fetch individually as fallback", flush=True)


def write_feed(route_ids: list[str]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    prefetch_relations(route_ids)
    all_stops, all_trips, all_st, all_order = {}, [], [], {}
    for i, rid in enumerate(route_ids, 1):
        print(f"\n[{i}/{len(route_ids)}] Route {rid}: {ROUTES[rid]['route_long_name']}",
              flush=True)
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

    sorted_ids = sorted(route_ids, key=lambda r: int(r))
    wcsv("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name",
                        "route_type", "route_color", "route_text_color"],
         [[rid, "transurb", rid, ROUTES[rid]["route_long_name"], ROUTES[rid]["route_type"],
           ROUTES[rid]["route_color"], ROUTES[rid].get("route_text_color", "000000")]
          for rid in sorted_ids])

    wcsv("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"],
         [[sid, s["name"], f"{s['lat']:.7f}", f"{s['lon']:.7f}"] for sid, s in sorted(all_stops.items())])

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
           "transurb-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
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
