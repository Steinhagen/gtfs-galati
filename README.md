nix run nixpkgs#python312 -- generate_gtfs.py          # all routes
nix run nixpkgs#python312 -- generate_gtfs.py 102      # one route

FIXME

1. Finish the remaining routes:

Trasee Extraurbane:
    50
    55

Trasee Urbane:
  - Microbuz mic
    7
    44
    39|39B

  - Autobuze
    11
    13
    15
    20
    23
    24
    25
    26
    28
    30
    32
    33
    34


FIX stations:

> All 106 stops in gtfs_transurb/stops.txt exist in OSM. Checked by pulling every bus_stop / platform / stop_position node inside the Galați bbox from Overpass (575 objects) and matching by distance + name.

- All 106 sit on an OSM stop object.
- Each GTFS stop maps to a distinct OSM node, so no duplicates or collapsed pairs.
- 71 match the OSM name verbatim; 34 differ only in spelling conventions, not identity.

The name variants, if you want to align them (GTFS → OSM):

Agenția C.F.R.          Agenția de Voiaj CFR
A.J.O.F.M.              AJOFM
Albatros                Bloc Albatros
Bld. Galați             Bulevardul Galați
Centrul Delfinul        Delfinul
Danubius                Universitatea Danubius   (same OSM name as UNIV-DANUBIUS)
Romtelecom              Romtelecom - Bănci
Spitalul Județean       Spitalul Județean de Urgență
Str. Gării              Gării
Str. Radu Negru         Radu Negru / Strada Radu Negru
Universitate            Universitatea „Dunărea de Jos”
Țiglina I/II/III        Țiglina 1/2/3
CEC Țiglina II          CEC Țiglina 2
Piața Țiglina I         Piața Țiglina 1
Str. X                  Strada X   (Cezar, Prundului, Vultur, Dumbrava Roșie)


Note DANUBIUS and UNIV-DANUBIUS are separate physical stops (~1 km apart) but both nearby OSM nodes are named "Universitatea Danubius" — worth a look if you care about name uniqueness.

Separately: the routes still listed in the README FIXME (50, 55, 7, 44, 39/39B, 11-34) aren't built yet, so their stops aren't in stops.txt. OSM does have relations for several of them (11, 15, 20, 24, 26, 28, 34, 39, 44, 7), so
shape data is available when you get to them. Routes 13, 23, 25, 30, 32, 33, 50, 55 either have no relation or an unnamed/stale one
