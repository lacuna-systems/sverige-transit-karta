"""Trafiklab NeTEx Regional: line categories and colours, used as a colour fallback after OSM.

The GTFS feeds carry no line colours. NeTEx Regional sometimes does (a Line's <Presentation>),
and some regions group their lines into categories (<GroupOfLines>, e.g. Skåne's "Stadsbuss
Malmö", "Regionbuss", "Pågatåg") that regions.json can map to the operator's category colours.
Lines are identified by the same route_id as in GTFS ("SE:012:Line:9011012000600000").

Downloads need a Trafiklab NeTEx Regional key (TRAFIKLAB_NETEX_KEY, or TRAFIK_NETEX_KEY in .env).
The free tier allows 50 calls a month, so update.py only downloads with --netex, and the GitHub
workflow passes it once a month. Extracted results are kept in colors/<region>.json, so builds
in between reuse them.
"""

import json
import os
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
NETEX_URL = "https://opendata.samtrafiken.se/netex/{slug}/{slug}.zip?key={key}"
LINE_RE = re.compile(rb'<Line\s[^>]*id="[^"]*:Line:(\d+)"[^>]*>(.*?)</Line>', re.S)
GROUP_RE = re.compile(rb"<GroupOfLines\s[^>]*>(.*?)</GroupOfLines>", re.S)
NAME_RE = re.compile(rb"<Name>([^<]*)</Name>")
LINEREF_RE = re.compile(rb'<LineRef\s[^>]*ref="[^"]*:Line:(\d+)"')
COLOUR_RE = re.compile(rb"<Presentation>.*?<Colour>([0-9A-Fa-f]{6})</Colour>", re.S)


def api_key():
    """The NeTEx key from the environment, or from a local .env file (never committed)."""
    for name in ("TRAFIKLAB_NETEX_KEY", "TRAFIK_NETEX_KEY"):
        if os.environ.get(name):
            return os.environ[name].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*(TRAFIKLAB_NETEX_KEY|TRAFIK_NETEX_KEY)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S*))', line)
            if m:
                return next(g for g in m.groups()[1:] if g is not None)
    return None


def download(slug, dest, key):
    """Download one region's NeTEx zip to dest. The key never appears in errors or logs."""
    req = urllib.request.Request(NETEX_URL.format(slug=slug, key=key),
                                 headers={"Accept-Encoding": "gzip, deflate", "User-Agent": "sweden-transit-map"})
    try:
        with urllib.request.urlopen(req, timeout=900) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f, 1 << 20)
    except Exception as e:  # noqa: BLE001 - re-raised without the key
        raise RuntimeError(f"NeTEx download for {slug} failed: {str(e).replace(key, '<key>')}") from None
    if not zipfile.is_zipfile(dest):
        raise RuntimeError(f"NeTEx download for {slug} is not a zip file")


def extract(zip_path):
    """Line colours and categories from a NeTEx zip:
    {"colours": {route_id: "#RRGGBB"}, "groups": {route_id: group name}}."""
    colours, groups = {}, {}
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".xml"):
                continue
            data = z.read(name)
            for m in LINE_RE.finditer(data):
                c = COLOUR_RE.search(m.group(2))
                if c:
                    colours[m.group(1).decode()] = "#" + c.group(1).decode().upper()
            for g in GROUP_RE.finditer(data):
                body = g.group(1)
                # The group's own name comes before its member list.
                gname = NAME_RE.search(body.split(b"<members>", 1)[0])
                if not gname:
                    continue
                for rid in LINEREF_RE.findall(body):
                    groups.setdefault(rid.decode(), gname.group(1).decode().strip())
    return {"colours": dict(sorted(colours.items())), "groups": dict(sorted(groups.items()))}


def group_colour(group, table):
    """Colour for a NeTEx group name from a region's category table: an exact name first, then the
    longest table key the group name starts with ("Stadsbuss Malmö" -> "Stadsbuss")."""
    if group in table:
        return table[group]
    prefixes = [k for k in table if group.startswith(k)]
    return table[max(prefixes, key=len)] if prefixes else None


def route_colours(cfg, netex_data):
    """route_id -> colour from NeTEx: the line's own Presentation colour, else its category's colour."""
    table = cfg.get("category_colours", {})
    out = {}
    for rid, group in netex_data.get("groups", {}).items():
        colour = group_colour(group, table)
        if colour:
            out[rid] = colour
    out.update(netex_data.get("colours", {}))
    return out


def refresh_region(cfg, workdir, key, log=print, local_zip=None):
    """Download (or read local_zip) and extract a region's NeTEx, storing the result in
    colors/<region>.json["netex"]."""
    if local_zip:
        data = extract(local_zip)
    else:
        dest = Path(workdir) / f"{cfg['id']}-netex.zip"
        download(cfg.get("netex", cfg["feeds"][0]["slug"]), dest, key)
        data = extract(dest)
        dest.unlink()
    path = ROOT / "colors" / f"{cfg['id']}.json"
    colours = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    colours["netex"] = data
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(colours, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    log(f"    NeTEx: {len(data['colours'])} line colours, {len(data['groups'])} lines in "
        f"{len(set(data['groups'].values()))} categories")
    return data
