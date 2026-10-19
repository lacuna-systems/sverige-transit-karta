"""Matching station names, and merging stations that are really one place.

Feeds name the same place in many ways ("Göteborg C", "Göteborg Central", "Göteborg Centralen",
"Stockholms central") and list one place as several stop areas (a railway station and the bus
terminal beside it, or the same station from two operators). Stations whose names match once those
variations are ignored, and which are close together, are merged.
"""

import re
from collections import defaultdict

from geo import metres

STATION_MERGE_M = 250  # stations with matching names this close together become one
STATION_SUFFIXES = {"station", "stationen", "resecentrum", "resecentrumet", "busstation", "bussstation", "bussterminal",
                    "terminal", "terminalen", "centralstation", "centralstationen", "c", "hpl", "hållplats", "läge",
                    "spår", "tåg", "buss", "bussplan", "central", "centralen"}
# Station words that also end compounds ("Ericsonterminal", "Kungsbackastation"), longest first.
COMPOUND_SUFFIXES = ("centralstationen", "centralstation", "resecentrumet", "resecentrum", "bussterminalen",
                     "bussterminal", "busstationen", "busstation", "terminalen", "terminal", "stationen", "station")


def station_words(name):
    """Case-folded words of a station name, without bracketed qualifiers and punctuation."""
    return re.sub(r"[^\w\s]", " ", re.sub(r"\(.*?\)", " ", name.casefold())).split()


def strip_suffixes(words):
    """Drop trailing words like "station" or "C", and the same words at the end of a compound
    ("Ericsonterminal" -> "Ericson"), as long as at least three letters are left."""
    words = list(words)
    while True:
        if len(words) > 1 and words[-1] in STATION_SUFFIXES and len("".join(words[:-1])) >= 3:
            words.pop()
            continue
        tail = next((s for s in COMPOUND_SUFFIXES if words[-1].endswith(s) and len(words[-1]) - len(s) >= 3), None)
        if not tail:
            return words
        words[-1] = words[-1][:-len(tail)]


def town_names(names):
    """Town names as used in station names: what is left of names like "Göteborg C", "Alingsås
    station" or "Halmstad Resecentrum" once the suffix is dropped (a single word)."""
    towns = set()
    for name in names:
        words = station_words(name)
        rest = strip_suffixes(words)
        if len(rest) == 1 and len(words) > 1 and len(rest[0]) >= 3:
            towns.add(rest[0])
    # Names use the genitive too ("Stockholms central"), so a town ending in s also gives its stem.
    return towns | {t[:-1] for t in towns if t.endswith("s") and len(t) > 3}


def without_genitive(word, towns):
    """ "stockholms" -> "stockholm" when the s-less form is a town."""
    return word[:-1] if word.endswith("s") and word[:-1] in towns else word


def station_key(name, towns=frozenset()):
    """Name used to match stations, tolerant of the ways feeds write the same place: case, brackets,
    punctuation and spacing are ignored, as are trailing words like "station", "C" or "Centralen"
    (also inside compounds), the genitive s of a town ("Stockholms central") and a leading town
    name. "Göteborg Nils Ericsonterminal" and "Nils Ericson Terminalen" both become "nilsericson";
    "Göteborg C" and "Göteborg Centralen" both "göteborg"."""
    words = strip_suffixes(station_words(name)) or [name.casefold()]
    words = [without_genitive(w, towns) for w in words]
    if len(words) > 1 and words[0] in towns:
        words = strip_suffixes(words[1:])
    return "".join(words)


def cluster_stations(stations, max_m=STATION_MERGE_M):
    """Group stations with the same station_key (towns taken from the names themselves) where every
    member is within max_m of every other. stations: {id: (name, (lon, lat), weight)}. Returns
    {id: group representative} for the stations in groups of two or more; the representative is
    the member with the greatest weight, then the shortest name, then the smallest id."""
    towns = town_names(v[0] for v in stations.values())
    by_key = defaultdict(list)
    for sid in sorted(stations):
        by_key[station_key(stations[sid][0], towns)].append(sid)
    alias = {}
    for members in by_key.values():
        if len(members) < 2:
            continue
        pairs = sorted((metres(stations[a][1], stations[b][1]), a, b)
                       for i, a in enumerate(members) for b in members[i + 1:])
        group = {s: {s} for s in members}
        for d, a, b in pairs:
            if d > max_m or group[a] is group[b]:
                continue
            if all(metres(stations[x][1], stations[y][1]) <= max_m for x in group[a] for y in group[b]):
                union = group[a] | group[b]
                for s in union:
                    group[s] = union
        for g in {id(g): g for g in group.values()}.values():
            if len(g) > 1:
                head = min(g, key=lambda s: (-stations[s][2], len(stations[s][0]), s))
                alias.update(dict.fromkeys(g, head))
    return alias


def merged_positions(alias, position):
    """Each group representative's position: the average of its members' positions."""
    members = defaultdict(list)
    for sid, head in alias.items():
        members[head].append(position(sid))
    return {head: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            for head, pts in members.items()}
