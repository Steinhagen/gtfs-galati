nix run nixpkgs#python3 -- generate_gtfs.py          # all routes
nix run nixpkgs#python3 -- generate_gtfs.py 102      # one route


FIXME

1. Uitat dacă stațiile următoare chiar și-au schimbat numele:
FEEA → "Facultatea de Științe Economice" at 45.443251, 28.051757 (OSM stop)
CNVA → "Colegiul Național Vasile Alecsandri" at 45.440625, 28.052130 (OSM stop)
Grădinița Prichindel → "Grădinița Motanul Încălțat" at 45.413945, 28.007024 (OSM stop)


2. OSM relations that did NOT work as expected:

105 RETUR — relation 10177284 ("Autobuz 105: Grădina publică => Micro 19"): fails the check, OSRM fallback used.
The relation returns via Strada Domnească (Parfumul Teilor → Universitate → Galeriile de Artă), but the official Transurb route uses Str. Gării → F.E.E.A. → C.N.V.A. → Albatros on Strada Traian — so it misses 4 official stops.
Bonus issue: its first way (Grădina Publică ↔ Liceul de Artă) is mapped against the direction of travel (the generator auto-orients it, but worth fixing in OSM).
106 — relation 19917546: a stub — only one stop node, no ways, no name/from/to. Both directions use OSRM until it's properly mapped.
Working as expected: 102 TUR (7514198), 102 RETUR (309380), 105 TUR (10177285).

How it works for your goal: each direction prefers its OSM relation and only falls back to OSRM if the relation fails the check (all stops within ~150 m of the shape and in visit order along it). Fix or add the relations in OSM, update the IDs in the SHAPES table, and the generator will use them directly — no other changes needed. I'll keep this report updated as we add the remaining routes.
