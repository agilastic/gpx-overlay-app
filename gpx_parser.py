"""Parse a GPX file into a time-indexed telemetry series (speed, elevation, HR, lat/lon)."""
import math
from datetime import timedelta

import gpxpy


HR_NAMESPACES = [
    "{http://www.garmin.com/xmlschemas/TrackPointExtension/v1}hr",
    "{http://www.garmin.com/xmlschemas/TrackPointExtension/v2}hr",
    "hr",
]


def _extract_hr(point):
    ext = getattr(point, "extensions", None)
    if not ext:
        return None
    for el in ext:
        for tag in HR_NAMESPACES:
            if el.tag == tag:
                try:
                    return float(el.text)
                except (TypeError, ValueError):
                    pass
        for child in el:
            for tag in HR_NAMESPACES:
                if child.tag == tag:
                    try:
                        return float(child.text)
                    except (TypeError, ValueError):
                        pass
    return None


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class TelemetryPoint:
    __slots__ = ("t", "lat", "lon", "ele", "speed_mps", "hr")

    def __init__(self, t, lat, lon, ele, speed_mps, hr):
        self.t = t
        self.lat = lat
        self.lon = lon
        self.ele = ele
        self.speed_mps = speed_mps
        self.hr = hr


class Telemetry:
    def __init__(self, points):
        if not points:
            raise ValueError("GPX file contains no trackpoints with timestamps")
        self.points = points
        self.start_time = points[0].t
        self.duration_s = (points[-1].t - points[0].t).total_seconds()
        self.has_hr = any(p.hr is not None for p in points)
        self.lats = [p.lat for p in points]
        self.lons = [p.lon for p in points]
        self.lat_min, self.lat_max = min(self.lats), max(self.lats)
        self.lon_min, self.lon_max = min(self.lons), max(self.lons)
        self.max_speed_mps = max(p.speed_mps for p in points)
        self.max_ele = max(p.ele for p in points)
        self.min_ele = min(p.ele for p in points)
        self.max_hr = max((p.hr for p in points if p.hr is not None), default=0)
        self.min_hr = min((p.hr for p in points if p.hr is not None), default=0)

    def sample_at(self, elapsed_s):
        """Return interpolated TelemetryPoint for elapsed seconds since start_time."""
        target = self.start_time + timedelta(seconds=elapsed_s)
        pts = self.points
        if target <= pts[0].t:
            return pts[0]
        if target >= pts[-1].t:
            return pts[-1]
        lo, hi = 0, len(pts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if pts[mid].t < target:
                lo = mid + 1
            else:
                hi = mid
        p1 = pts[lo - 1] if lo > 0 else pts[0]
        p2 = pts[lo]
        span = (p2.t - p1.t).total_seconds()
        frac = 0.0 if span <= 0 else (target - p1.t).total_seconds() / span
        lat = p1.lat + (p2.lat - p1.lat) * frac
        lon = p1.lon + (p2.lon - p1.lon) * frac
        ele = p1.ele + (p2.ele - p1.ele) * frac
        speed = p1.speed_mps + (p2.speed_mps - p1.speed_mps) * frac
        hr = None
        if p1.hr is not None and p2.hr is not None:
            hr = p1.hr + (p2.hr - p1.hr) * frac
        elif p1.hr is not None:
            hr = p1.hr
        elif p2.hr is not None:
            hr = p2.hr
        return TelemetryPoint(target, lat, lon, ele, speed, hr)


def load_gpx(path):
    with open(path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    raw = []
    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                if pt.time is None:
                    continue
                raw.append(pt)

    if not raw:
        raise ValueError("No timestamped trackpoints found in GPX file")

    raw.sort(key=lambda p: p.time)

    points = []
    prev = None
    for pt in raw:
        hr = _extract_hr(pt)
        ele = pt.elevation if pt.elevation is not None else 0.0
        speed = 0.0
        if prev is not None:
            dt = (pt.time - prev.time).total_seconds()
            if dt > 0:
                dist = _haversine_m(prev.latitude, prev.longitude, pt.latitude, pt.longitude)
                speed = dist / dt
        points.append(TelemetryPoint(pt.time, pt.latitude, pt.longitude, ele, speed, hr))
        prev = pt

    if len(points) > 1:
        points[0].speed_mps = points[1].speed_mps

    return Telemetry(points)
