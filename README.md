nix run nixpkgs#python312 -- generate_gtfs.py          # all routes
nix run nixpkgs#python312 -- generate_gtfs.py 102      # one route

Route colours come from route-colors.txt (`ref,#rrggbb,vehicle,area`); it is
the source of truth for route_color, and route_text_color is derived from it
(black on light backgrounds, white on dark). Routes missing from the file fall
back to the `route_color` in ROUTES (currently 32, 33, 43).

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
    25

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

Separately: the routes still listed in the README FIXME (50, 55, 7, 44, 39/39B, 11-25) aren't built yet, so their stops aren't in stops.txt. OSM does have relations for several of them (11, 15, 20, 24, 26, 28, 39, 44, 7), so
shape data is available when you get to them. Routes 13, 23, 25, 50, 55 either have no relation or an unnamed/stale one

---

 Six stop pairs are affected, across routes 24, 26, 28, 34, 38 and 102. Two are new with route 24, four predate it.

  ┌────────────────────────┬────────────────────┬─────────────────────────────────┬─────────────────────────────────────┐
  │ Coordinates            │ Stop               │ Duplicate ids                   │ Routes/directions                   │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4548415, 28.0346851 │ TREFO              │ TREFO-TUR / TREFO-RETUR         │ 24/TUR vs 34/RETUR — new            │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4543572, 28.0238786 │ Piața Micro 39     │ PIATA-MICRO-39-TUR / -RETUR     │ 24/TUR vs 26/RETUR + 34/RETUR — new │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4633441, 28.0438172 │ Parc C.F.R.        │ PARC-CFR-TUR / -RETUR           │ 102/TUR vs 26/RETUR                 │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4648565, 28.0357945 │ Bariera Traian     │ BARIERA-TRAIAN-TUR / -RETUR     │ 26/TUR vs 28/RETUR                  │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4543728, 28.0493431 │ Cămine Studențești │ CAMINE-STUDENTESTI-TUR / -RETUR │ 26/TUR vs 102/RETUR                 │
  ├────────────────────────┼────────────────────┼─────────────────────────────────┼─────────────────────────────────────┤
  │ 45.4173262, 28.0101278 │ Școala Nr. 40      │ SCOALA-40 / SCOALA-NR-40-TUR    │ 38/TUR vs 28/TUR                    │
  └────────────────────────┴────────────────────┴─────────────────────────────────┴─────────────────────────────────────┘

  The first five share one cause: resolve_platforms suffixes the stop id with the direction of the route being built, but a platform's side of the street is a property of the physical stop, not of TUR/RETUR. When route A travels north past
  a platform and route B travels south past it, the same OSM node ends up as …-TUR for one route and …-RETUR for the other. All five involve the pair (26, 102) on the Bariera Traian corridor or the pair (24, 34) on Strada Brăilei — routes
  that run the same street in opposite nominal directions.

  The Școala Nr. 40 case is different and worth separating: STOPS holds two catalog codes for what is one physical stop — SCOALA 40 (n6894593244) and SCOALA NR. 40 (n6896163912), 70 m apart, both displayed "Școala Nr. 40". Route 28 TUR
  resolves SCOALA NR. 40 to n6894593244 from its relation, landing exactly on SCOALA 40's catalog position. That one is a catalog duplicate, fixable by merging the two codes with an alias, independently of the id-suffix design.

