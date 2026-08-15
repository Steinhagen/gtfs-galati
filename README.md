nix run nixpkgs#python312 -- generate_gtfs.py            # all routes
nix run nixpkgs#python312 -- generate_gtfs.py 102        # one route
nix run nixpkgs#python312 -- generate_gtfs.py --refresh  # re-read OSM + route pages

Where the data comes from

The generator holds no route data of its own. Per route it only knows the OSM
route relation id per direction (plus which days the route runs, when that is
not Monday-Friday).

- OpenStreetMap route relations give the stops: the relation's platform members
  in order are the direction's stop sequence, and each platform node gives the
  stop name, position and id (name slug + node id). The relation's ways are the
  shape; its tags give the vehicle type (route_type), the termini
  (route_long_name) and the headsigns (`to`). A circular route is expected to
  carry `roundtrip=yes` and a `via`; the long name is then
  `from - via - to`, so route 38 reads
  "Micro 19 - Cartier Locuințe Sociale M17 - Micro 19" instead of
  "Micro 19 - Micro 19".
- The Transurb website gives the timetables. The route page ("veziTraseu")
  lists the station names in travel order, which is what the timetable pages
  are keyed on, so those names are read from the site instead of being written
  down here.
- route-colors.txt (`ref,#rrggbb,vehicle,area`) is the source of truth for
  route_color; route_text_color is derived from its luminance (black on light
  backgrounds, white on dark). The vehicle column is cross-checked against the
  OSM route tag.

Every build cross-checks the two sources per direction: the relation and the
route page must list the same number of stops, in the same order. A mismatch
fails the build; missing or inconsistent OSM tagging is reported as a warning.
Both are meant to be fixed in OSM, not worked around in the generator. The
report is printed at the end of a build under "OSM issues to fix upstream", and
everything it currently reports is listed below.

Adding a route: add its relation ids to ROUTES and build it. If OSM is
complete, nothing else is needed.

FIXME

1. Finish the remaining routes

The Transurb site lists 30 routes; 18 are in the feed. What the other 12 need:

  route  site stops (TUR/RETUR)  OSM
     11             22 / 15      r10177466 / r10179043 match this exactly, but
                                 see the weekend itinerary below
     15              8 / 11      r10181002 / r10181003, only 8 / 8 platforms
     20             14 / 16      r10278231 / r10278230, only 15 / 11 platforms
     23             14 / 16      r395899 is a stub: no name, 1 platform, 126 ways
      7             15 / 15      no relation (tram)
     13             11 / 11      no relation
     25             11 / 12      no relation
     39             16 / 16      no relation (tram)
    39B              6 / 6       no relation (tram)
     44             11 / 11      no relation (tram)
     50             38 / 38      no relation (extraurban)
     55             26 / 18      no relation (extraurban)

Add a route to ROUTES once its relations list every platform, in travel order;
the build fails with a count mismatch until then.

Route 11 needs more than that. Its page carries two station lists
("variantaStatii"): the standard one, Micro 13 - Piața Centrală, which the two
OSM relations already match stop for stop, and a second one, "Sâmbătă, duminică
și sărbători legale către Grădina Publică" (21/21 stations), which follows the
same path as far as Parfumul Teilor and then ends at Grădina Publică instead of
Piața Centrală. The site's own weekend column does not separate the two: of the
70 weekend departures from Micro 13, the 34 from 14:05 on are exactly the ones
the second variant lists, yet the standard page still shows all 70 as calling
at Piața Centrală (70 arrivals there plus 34 at Grădina Publică for 70
departures). So the afternoon weekend runs are listed twice, once per
itinerary. Before route 11 can be built:

- OSM needs a second pair of route relations for the Grădina Publică itinerary,
  as further variants of the same route_master;
- the generator needs to build a route's variants separately and assign the
  weekend afternoon departures to the right one, instead of reading only
  "variantaStatii=standard".

Every other route on the site has a single station list, so nothing else is
affected. Route 11's second itinerary is reported by the build.

2. Route relations to fix in OSM

- r396132: `ref=31`, no name, no platforms, 53 ways — pre-PTv2 leftover that
  belongs to no route_master, so anything reading routes by `ref` picks it up
  as a route 31 variant. Delete, or retag `disused:route`.
- Route relations for services Transurb no longer lists are still tagged as
  active. If those services are gone they belong under `disused:route` /
  `disused:route_master`, like 29 (r10169798, r396129, r10169776) now is:

    ref   route_master  variants
      8   -             r358091 (no name, 18 ways, `note=inlocuieste ruta de
                        tramvai 8 pana la terminarea lucrarilor pe Strada
                        Traian`, i.e. a temporary replacement service)
     12   r10179045     r10179044, r10177585
     16   r10187871     r10187870, r10187869
     17   r10241468     r10240955, r10241467
     19   r10277669     r10277635, r10277668
     22   r10278341     r10278277, r10278340
     36   r10225669     r10225587, r10225668

  Check each against the site before retagging; the site is the authority on
  what still runs.
- r395899 (`ref=23`) is not stale, it is a stub for a route that does run:
  no name, 1 platform, 126 ways. It needs completing, see item 1.

3. Stop names to fix in OSM

Same stop spelled two ways:

- Str. Aurel Vlaicu (n6963443072, n6960962995) vs Strada Aurel Vlaicu (n6894604128)
- Str. Gheorghe Doja (n6960963007, n6963443061) vs Strada Gheorghe Doja (n6896006833)
- Moruzzi (n6896966052, n6897791669) vs Str. Moruzzi (n14090438573, n14090438580)
- Radu Negru (n6879824358) vs Strada Radu Negru (n6895095244, n6896006856)

One name on stops that are far apart, with nothing to tell them apart (the site
does distinguish them):

- Lidl: 4 stops up to 3.1 km apart, Micro 19 vs Strada Nae Leonard
  (n6894593246, n6896163910, n6960963003, n6963443065)
- Kaufland: 3 stops up to 3.2 km apart, Țiglina vs Patinoar/Micro 39
  (n6874360932, n6875283385, n6960962993)
- Strada Vultur: 3 stops up to 629 m apart, Strada Domnească vs Strada Traian
  (n6879824377, n6906785248, n6906838264)
- Radu Negru: 3 stops up to 565 m apart, Strada Domnească vs Strada Traian
  (n6879824358, n6895095244, n6896006856)
- Moruzzi: 4 stops up to 367 m apart, site: MORUZZI vs STR. ALEX. MORUZZI
  (n6896966052, n6897791669, n14090438573, n14090438580)
- Mașniță: 2 stops 312 m apart (n6894604132, n6896006839)
- Strada Aurel Vlaicu: the site calls one of them
  "STR. AUREL VLAICU - (1 DECEMBRIE)" (n6894604128 vs n6960962995,
  n6963443072)

Mixed street-name prefix: 26 stops use "Strada", 8 use "Str." (Aurel Vlaicu
n6960962995/n6963443072, Brăilei n14093112483/n14093112489, Gheorghe Doja
n6960963007/n6963443061, Moruzzi n14090438573/n14090438580), 2 use
"Bulevardul".

Inconsistent capitalisation, against the title case used by most stops:
Baia comunală (n6906785250, n6906838262), Cartierul nou (n6899023166,
n6899152594), Centrul de recoltare (n6875033915), Căminele combinatului
(n14086450125), Căminele studențești (n6879824355, n6879824375), Căminul de
bătrâni (n6879824356), Galeriile de artă (n530256646), Grădina publică
(n14086091994, n6898365514), Muzeul de artă (n6879824376), Piața centrală
(n6898532656), Potcoava de aur (n6875033916), Teatrul dramatic (n6879824378).
These flow straight into stops.txt, and into route_long_name for 35, 43
and 105.

4. Every stop the two sources name differently

Of the 488 stop visits in the feed, 60 hit a stop whose OSM name differs from
the name on the Transurb route page. Capitalisation and diacritics are ignored
in this comparison, since the site prints station names in caps without
diacritics; the site names below are written in normal orthography, the OSM
names verbatim. Two stops named the same in OSM but differently by the site are
listed at the end.

Different wording — someone has to decide which name is right:

  Transurb page                      OSM                              node
  Agenția C.F.R.                     Agenția de Voiaj CFR             n530256244
  Biserica Sf. Vasile                Biserica „Sfântul Vasile”        n6894604138
  Bl. Bujor/Nufăr                    Bloc Bujor/Nufăr                 n14088449868, n14088455589
  Cămine Studențești                 Căminele studențești             n6879824355, n6879824375
  CEC Țiglina II                     CEC Țiglina 2                    n530256058
  Cimitir Cătușa                     Cimitirul „Cătușa”               n6892672444, n6893391129
  Cimitir Eternitatea                Cimitirul Eternitatea            n6899023178, n6899129583
  Cimitir Sf. Lazăr                  Cimitirul „Sfântul Lazăr”        n1720639175, n14088455591
  F.S.E.A. / F.E.A.A. / F.E.E.A      F.E.E.A.                         n534268996
  Fac. de Medicină                   Facultatea de Medicină           n6893391123
  Galeria de Artă                    Galeriile de artă                n530256646
  Liceul Sf. Maria                   Liceul „Sfânta Maria”            n6960963008, n6963443059
  Piața Țiglina I                    Piața Țiglina 1                  n6905457929, n6905457936
  Școala 40 / Școala Nr. 40          Școala Nr. 40                    n6894593244
  Spital Municipal                   Spitalul Municipal               n6895570669, n6896006858
  Str. Ghe. Doja                     Str./Strada Gheorghe Doja        n6896006833, n6960963007, n6963443061
  Str. Alex. Moruzzi                 Str. Moruzzi                     n14090438573, n14090438580
  Str. Aurel Vlaicu - (1 Decembrie)  Strada Aurel Vlaicu              n6894604128
  Str. M. Kogălniceanu               Strada Mihail Kogălniceanu       n6899023182
  Țiglina I / II / III               Țiglina 1 / 2 / 3                n529898235, n530776769, n1932184795, n1932184800
  Univ. Danubius                     Universitatea Danubius           n6896665344

OSM is more specific than the page (probably fine, listed for completeness):

  Albatros                           Bloc Albatros                    n614962899
  Romtelecom                         Romtelecom - Bănci                n472614576
  Spitalul Județean                  Spitalul Județean de Urgență     n530187911
  Universitate                       Universitatea „Dunărea de Jos”   n530257449
  Danubius                           Universitatea Danubius           n6897986189

The page is more specific than OSM:

  Centrul Delfinul                   Delfinul                         n14086946893
  Kaufland (Patinoar)                Kaufland                         n6960962993
  Carrefour-Shopping City            Shopping City                    n6960963010
  Micro 19 (Sosire)                  Micro 19                         n123745651
    - not a naming problem: this is the loop's second call at Micro 19

Street-name prefix only, the page abbreviating what OSM spells out (or the
reverse for Gării):

  Str. Gării -> Gării (n534270437), Str. Radu Negru -> Radu Negru
  (n6879824358), Bld. Dunărea -> Bulevardul Dunărea (n6896713532),
  Bld. Galați -> Bulevardul Galați (n4907189684), Bld./Str. Oțelarilor ->
  Strada Oțelarilor (n6896713528, n6897924685), and Str. -> Strada for
  9 Mai (n6894604130), Cezar (n6895095246, n6896006854), Crizantemelor
  (n6899129579), Dumbrava Roșie (n6899023168, n6899152592), Ionel Fernic
  (n6898532661), Lemnari (n6896966050, n6897791671), Oltului (n6878887889,
  n6894593237), Prundului (n6895570670, n6896006860), Radu Negru (n6895095244,
  n6896006856), Tecuci (n6899023180, n6899129581), Traian Vuia (n6898532659),
  Vultur (n6879824377, n6906785248, n6906838264).

Punctuation and spacing only:

  Gara CFR -> Gara C.F.R. (n6875107385), Parcare Bănci -> Parcare - Bănci
  (n6875960831).

One OSM name for two stops the page tells apart — both directions call at both
stops, so the feed shows the same name twice in one trip:

  route 41 TUR  Universitatea Danubius (n6896665344, n6897986189), 65 m apart;
                the page calls them UNIV. DANUBIUS and DANUBIUS
  route 38 TUR  Școala Nr. 40 (n6894593244, n6896163912), 70 m apart;
                the page calls them SCOALA 40 and SCOALA NR. 40

The site is inconsistent with itself for a few stops, which is a website
problem rather than an OSM one: Agenția C.F.R. / Agenția CFR, CEC Țiglina II /
C.E.C. Țiglina II, Gara CFR / "Gara  CFR" (double space), Str. Gării /
"Str.Gării", Str. Cezar / Cezar, F.S.E.A. / F.E.A.A. / F.E.E.A for the same
stop.
