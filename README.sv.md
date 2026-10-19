# Kollektivtrafikkarta för Sverige

[![en](https://img.shields.io/badge/lang-en-red.svg)](README.md)
[![sv](https://img.shields.io/badge/lang-sv-yellow.svg)](README.sv.md)

En interaktiv karta över Sveriges kollektivtrafik: alla spårvagns-, tunnelbane-, tåg-, buss- och
båtlinjer hos 21 regionala trafikhuvudmän samt fjärrtågen, ritade längs sina verkliga sträckningar,
med hållplatser, filter baserade på tidtabellen och en vy över hela Sverige. Gränssnittet finns på
svenska och engelska, med ljust och mörkt tema.

Webbplatsen är helt statisk (`site/`). All bearbetning görs i förväg i Python, med enbart
standardbiblioteket, och en GitHub Action uppdaterar datan varje vecka.

## Struktur

| Sökväg                       | Innehåll                                                                                                       |
|------------------------------|----------------------------------------------------------------------------------------------------------------|
| `site/`                      | Den publicerade webbplatsen: `index.html`, `app.js`, `style.css` och den byggda datan i `site/data/`           |
| `site/i18n.js`, `site/i18n/` | Gränssnittets texter, en fil per språk (`en.json`, `sv.json`), och språkigenkänning                            |
| `update.py`                  | Hämtar alla flöden, uppdaterar färger och järnvägsgeometri, bygger alla regioner (parallellt) och hela Sverige |
| `build.py`                   | Bygger en region från GTFS: linjer, gemensamma stråk, hållplatser, trafik över dygnet, färger                  |
| `aggregate.py`               | Slår ihop de byggda regionerna till vyn över hela Sverige                                                      |
| `stations.py`                | Matchning av stationsnamn och sammanslagning av stationer som är samma plats                                   |
| `geo.py`                     | Avstånd, förenkling och kurvorna som förbinder och böjer sträckor                                              |
| `colours.py`                 | Kontrast och nyanser av varumärkesfärger                                                                       |
| `osm.py`                     | OpenStreetMap: linjefärger och tågens väg längs järnvägsnätet                                                  |
| `netex.py`                   | Trafiklab NeTEx: linjekategorier och färger                                                                    |
| `regions.json`               | Regionerna: flöden, filter, varumärkes- och kategorifärger, uteslutna linjer                                   |
| `colors/`                    | Linjefärger per region från OSM och NeTEx (uppdateras av `update.py`)                                          |
| `tests/`                     | Enhets- och integrationstester (ett litet syntetiskt GTFS-flöde) och kontroller av den byggda datan            |

## Köra lokalt

```sh
python3 update.py                # hämta och bygg allt (cirka 4 minuter)
python3 update.py vt sl          # bara vissa regioner (hela Sverige byggs också om)
python3 -m http.server -d site   # öppna sedan http://localhost:8000
```

Valfria nycklar, i miljön eller i en `.env` som git ignorerar:

- `TRAFIKLAB_GTFS_REGIONAL_KEY`: hämta GTFS från Trafiklab i stället för Mobility Databases spegling, som inte kräver nyckel.
- `TRAFIKLAB_NETEX_KEY`: behövs för `--netex`, som uppdaterar linjekategorier från NeTEx. Gratisnivån tillåter
  50 anrop i månaden och en full uppdatering använder 21.

## Språk och tema

Språk och tema väljs i inställningsmenyn (kugghjulet bredvid regionen). Står språket på
*Automatiskt* blir det svenska om tangentbordslayouten är svensk (läses från webbläsarens
tangentbords-API i Chromium-baserade webbläsare, och i andra från de första å och ö eller ä som
skrivs) eller om webbläsaren föredrar svenska, och annars engelska. Temat följer systemet om det
inte är satt till ljust eller mörkt. Båda valen sparas i webbläsaren.

För att lägga till ett språk: kopiera `site/i18n/en.json` till `site/i18n/<kod>.json`, översätt
värdena (behåll `{platshållarna}`; poster med `one`/`other` är pluralformer) och lägg till språket
i `LANGUAGES` i `site/i18n.js`. Testerna kontrollerar att alla språk har samma nycklar och platshållare.

## Tester

```sh
PYTHONHASHSEED=0 python3 -m unittest discover -s tests -t .
pipx run ruff check .
```

## GitHub Actions

- `tests.yml`: lint och tester vid varje push och pull request till `master`.
- `update-data.yml`: uppdaterar datan varje vecka och checkar in den i repot. Första måndagen i månaden
  uppdateras även NeTEx.
- `pages.yml`: kör testerna och publicerar sedan `site/` på GitHub Pages när webbplatsen eller datan ändras.
  Aktivera det en gång under *Settings → Pages → Source: GitHub Actions*.

## Licens

Koden är släppt som allmän egendom (public domain) under [Unlicense](LICENSE).

Det gäller inte material från tredje part, som behåller sina egna villkor:

- Ikonerna i `site/app.js` och `site/index.html` kommer från Googles Material Design-ikoner (Apache License 2.0).
- Den byggda datan i `site/data/` och `colors/` kommer från källorna nedan och följer deras licenser.
  Framför allt omfattas linjefärger och järnvägsgeometri från OpenStreetMap av Open Database License
  (ODbL) och kräver den källhänvisning som kartan visar.

## Data

Tidtabeller: Samtrafiken / Trafiklab GTFS Regional och NeTEx Regional. Linjefärger och järnvägsgeometri:
© OpenStreetMap-bidragsgivare. Bakgrundskarta: OpenFreeMap / OpenMapTiles.
