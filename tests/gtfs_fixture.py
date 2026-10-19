"""A tiny synthetic GTFS feed for build tests.

Four stations along an east-west street (each with a platform per direction), served by:
  T1  tram "1"  S1-S2-S3-S4 weekdays, shape with distances
  T2  tram "2"  S1-S2-S3 weekdays + Saturday, shape without distances (stops get projected)
  B10 bus "10"  S1-S4 weekdays, no shape at all (straight placeholder)
  D1  dial-a-ride (route_type 1501), which the map leaves out
  X1  bus "99" run by another agency, for agency filters
"""

import csv
from pathlib import Path

AGENCY = "Testtrafik"
LON0, LAT = 11.90, 57.70
STEP = 0.01  # ~600 m between stations


def station_lon(i):
    return LON0 + STEP * i


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_feed(directory, feed_version="2026-10-05"):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    write(d / "agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone"],
          [["A", AGENCY, "https://example.org", "Europe/Stockholm"],
           ["O", "Other Buses", "https://example.org", "Europe/Stockholm"]])
    write(d / "feed_info.txt", ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_version"],
          [["Test", "https://example.org", "sv", feed_version]])

    stops = []
    for i in range(4):
        sid = f"S{i + 1}"
        stops.append([sid, f"Station {i + 1}", LAT, station_lon(i), 1, ""])
        # Platforms: east-bound a few metres north of the street centre, west-bound south.
        stops.append([f"{sid}E", f"Station {i + 1}", LAT + 0.00008, station_lon(i), 0, sid])
        stops.append([f"{sid}W", f"Station {i + 1}", LAT - 0.00008, station_lon(i), 0, sid])
    write(d / "stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"], stops)

    write(d / "routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
          [["T1", "A", "1", "", 900], ["T2", "A", "2", "", 900], ["B10", "A", "10", "", 700],
           ["D1", "A", "D", "", 1501], ["X1", "O", "99", "", 700], ["B20", "A", "20", "", 700]])

    # Shapes: points every ~60 m along the street, slightly north (east-bound side).
    shapes = []
    for sid, n_stations, with_dist in (("SH1", 4, True), ("SH2", 3, False)):
        pts = 10 * (n_stations - 1) + 1
        for k in range(pts):
            lon = LON0 + STEP * k / 10
            dist = round(k * STEP / 10 * 111320 * 0.5348, 2) if with_dist else ""  # cos(57.7°) ≈ 0.5348
            shapes.append([sid, LAT + 0.00008, round(lon, 6), k + 1, dist])
    write(d / "shapes.txt",
          ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"], shapes)

    write(d / "calendar.txt",
          ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                               "start_date", "end_date"],
          [["WK", 1, 1, 1, 1, 1, 0, 0, "20261005", "20261101"]])
    # Saturdays only through calendar_dates; one weekday removed by an exception.
    write(d / "calendar_dates.txt", ["service_id", "date", "exception_type"],
          [["SA", "20261010", 1], ["SA", "20261017", 1], ["WK", "20261007", 2]])

    trips, stop_times = [], []

    def trip(tid, route, service, shape, stations, start_min, platform="E", dists=None):
        trips.append([route, service, tid, shape])
        for seq, i in enumerate(stations):
            t = start_min + 2 * seq
            hhmm = f"{t // 60:02d}:{t % 60:02d}:00"
            dist = dists[seq] if dists else ""
            stop_times.append([tid, hhmm, hhmm, f"S{i}{platform}", seq + 1, dist])

    seg = round(STEP * 111320 * 0.5348, 2)
    for k in range(6):  # tram 1: six weekday trips, 07:00-07:50
        trip(f"T1_{k}", "T1", "WK", "SH1", [1, 2, 3, 4], 7 * 60 + 10 * k, dists=[0, seg, 2 * seg, 3 * seg])
    for k in range(3):  # tram 2: weekday and Saturday
        trip(f"T2_{k}", "T2", "WK", "SH2", [1, 2, 3], 8 * 60 + 20 * k)
    trip("T2_sat", "T2", "SA", "SH2", [1, 2, 3], 10 * 60)
    trip("B10_0", "B10", "WK", "", [1, 4], 25 * 60)  # after midnight: belongs to the previous service day
    trip("D1_0", "D1", "WK", "", [1, 2], 9 * 60)
    trip("X1_0", "X1", "WK", "", [2, 3], 9 * 60)
    trip("B20_0", "B20", "SA", "", [3, 4], 11 * 60)  # Saturdays only, and only 2 of the first 4 weeks
    write(d / "trips.txt", ["route_id", "service_id", "trip_id", "shape_id"], trips)
    write(d / "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                                 "shape_dist_traveled"], stop_times)
    return d
