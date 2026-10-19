"""Geometry on (lon, lat) points: distances, simplification, and the curves that join paths.

Everything works in a local flat projection (metres per degree at the latitude in question),
which is accurate to well under a metre over the distances the map deals with.
"""

import math

M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON_EQUATOR = 111320.0
BRIDGE_TANGENT_M = 20  # a bridge leaves and joins its stretches in their direction over this distance
CONVERGE_MIN_M = 60  # a path bends onto a moved station over at least this distance...
CONVERGE_FACTOR = 3  # ...or this many times the distance it is moved, whichever is longer
CONVERGE_STEP_M = 10  # point spacing along the bend


def kx_at(lat):
    """Metres per degree of longitude at a latitude."""
    return M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))


def metres(a, b):
    """Distance in metres between two (lon, lat) points."""
    return math.hypot((b[0] - a[0]) * kx_at(a[1]), (b[1] - a[1]) * M_PER_DEG_LAT)


def length_and_gap(coords):
    """Total length and longest single step of a polyline, in metres."""
    total = gap = 0.0
    for a, b in zip(coords, coords[1:]):
        d = metres(a, b)
        total += d
        gap = max(gap, d)
    return total, gap


def point_to_polyline_m(p, line):
    """Distance in metres from a (lon, lat) point to a polyline."""
    kx, ky = kx_at(p[1]), M_PER_DEG_LAT
    px, py = p[0] * kx, p[1] * ky
    best = math.inf
    for (x0, y0), (x1, y1) in zip(line, line[1:]):
        ax, ay, bx, by = x0 * kx, y0 * ky, x1 * kx, y1 * ky
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def simplify(points, tol_m, xy=lambda p: p, max_step_m=None):
    """Iterative Douglas-Peucker; xy extracts (lon, lat) from a point. With max_step_m, extra points
    are kept so no step gets longer than that."""
    if len(points) < 3:
        return points
    kx, ky = kx_at(xy(points[0])[1]), M_PER_DEG_LAT
    pts = [(xy(p)[0] * kx, xy(p)[1] * ky) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol_m * tol_m
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, best_d = -1, tol2
        for i in range(a + 1, b):
            px, py = pts[i]
            if seg2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best_d:
                best, best_d = i, d
        if best >= 0:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    if max_step_m:
        last = 0
        for i in range(1, len(points)):
            if not keep[i] and metres(xy(points[last]), xy(points[i + 1 if i + 1 < len(points) else i])) > max_step_m:
                keep[i] = True
            if keep[i]:
                last = i
    return [p for p, k in zip(points, keep) if k]


def project_stops(pts, coords):
    """Distance along a shape for each stop. pts are shape points whose 4th field is the
    geometric distance in metres; the projection only moves forwards along the shape."""
    out, start = [], 0
    for c in coords:
        best, best_i, best_t = math.inf, start, 0.0
        kx = kx_at(c[1])
        px, py = c[0] * kx, c[1] * M_PER_DEG_LAT
        for i in range(start, len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            ax, ay, bx, by = a[0] * kx, a[1] * M_PER_DEG_LAT, b[0] * kx, b[1] * M_PER_DEG_LAT
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d < best:
                best, best_i, best_t = d, i, t
            elif best < 50 and d > best + 500:
                break  # moved well past a good match; later passes of a loop belong to later stops
        start = best_i
        a, b = pts[best_i], pts[min(best_i + 1, len(pts) - 1)]
        out.append(a[3] + (b[3] - a[3]) * best_t)
    return out


def percentile_bbox(points, low=0.02, high=0.98, decimals=4):
    """[west, south, east, north] of (lon, lat) points between two percentiles, so a few far-off
    outliers (e.g. international stations) don't stretch the extent."""
    lons = sorted(p[0] for p in points)
    lats = sorted(p[1] for p in points)

    def q(values, f):
        return round(values[min(len(values) - 1, int(len(values) * f))], decimals)
    return [q(lons, low), q(lats, low), q(lons, high), q(lats, high)]


def end_direction(coords, end, reach_m=BRIDGE_TANGENT_M):
    """Unit direction (in local metres) in which a polyline leaves through its end point `end`,
    measured from a point about reach_m back from that end, or None for a degenerate line."""
    pts = coords if end == coords[-1] else coords[::-1]
    kx = kx_at(end[1])
    for p in reversed(pts[:-1]):
        dx, dy = (end[0] - p[0]) * kx, (end[1] - p[1]) * M_PER_DEG_LAT
        if math.hypot(dx, dy) >= reach_m or p is pts[0]:
            n = math.hypot(dx, dy)
            return (dx / n, dy / n) if n > 0.5 else None
    return None


def smooth_bridge(c1, e1, c2, e2):
    """A curve from e1 (an end of polyline c1) to e2 (an end of c2) that leaves c1 and joins c2 in
    their own directions (a cubic Hermite curve), so a bridged line bends instead of kinking. An
    end whose direction points away from the other end (a line doubling back) gets no tangent."""
    kx = kx_at(e1[1])
    chord = ((e2[0] - e1[0]) * kx, (e2[1] - e1[1]) * M_PER_DEG_LAT)
    gap = math.hypot(*chord)
    t1 = end_direction(c1, e1)
    t2 = end_direction(c2, e2)
    t2 = (-t2[0], -t2[1]) if t2 else None  # c2 is entered through e2, so flip its leaving direction
    t1, t2 = [t if t and t[0] * chord[0] + t[1] * chord[1] > 0.2 * gap else (0.0, 0.0) for t in (t1, t2)]
    if t1 == t2 == (0.0, 0.0):
        return [e1, e2]
    n = max(6, min(16, int(gap / 5)))
    out = []
    for i in range(n + 1):
        s = i / n
        h10, h01, h11 = s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
        x = h10 * t1[0] * gap + h01 * chord[0] + h11 * t2[0] * gap
        y = h10 * t1[1] * gap + h01 * chord[1] + h11 * t2[1] * gap
        out.append((e1[0] + x / kx, e1[1] + y / M_PER_DEG_LAT))
    out[0], out[-1] = tuple(e1), tuple(e2)
    return out


def converge(coords, target, at_end):
    """Bend one end of a path onto target: points near that end move towards it, fading out
    smoothly along the path (points are added where the path is sparse), so the path keeps its
    own course and direction further out."""
    end = coords[-1] if at_end else coords[0]
    dx, dy = target[0] - end[0], target[1] - end[1]
    shift = metres(end, target)
    if shift < 0.5:
        return coords
    pts = coords[::-1] if at_end else list(coords)
    total = length_and_gap(pts)[0]
    reach = min(max(CONVERGE_MIN_M, CONVERGE_FACTOR * shift), 0.45 * total)

    def moved(p, s):
        u = min(1.0, s / reach) if reach > 0 else 1.0
        w = 1 - u * u * (3 - 2 * u)  # smoothstep: fully moved at the end, unmoved from `reach` on
        return (p[0] + dx * w, p[1] + dy * w)

    out, s = [], 0.0
    for i, p in enumerate(pts):
        if i:
            prev = pts[i - 1]
            step = metres(prev, p)
            if s < reach and step > CONVERGE_STEP_M:  # densify the bending part
                n = int(step // CONVERGE_STEP_M)
                for k in range(1, n + 1):
                    f = k / (n + 1)
                    out.append(moved((prev[0] + (p[0] - prev[0]) * f, prev[1] + (p[1] - prev[1]) * f), s + step * f))
            s += step
        out.append(moved(p, s))
    out[0] = tuple(target)
    return out[::-1] if at_end else out
