"""Generate a small synthetic GPX file with HR extensions for smoke-testing the overlay pipeline."""
import math
from datetime import datetime, timedelta, timezone

N_POINTS = 30
START = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="smoketest"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">
<trk><name>Smoke Test</name><trkseg>
"""
FOOTER = "</trkseg></trk></gpx>\n"

lines = [HEADER]
lat0, lon0 = 52.3759, 9.7320
for i in range(N_POINTS):
    t = START + timedelta(seconds=i * 2)
    lat = lat0 + 0.0003 * i
    lon = lon0 + 0.0002 * math.sin(i / 5)
    ele = 50 + 10 * math.sin(i / 6)
    hr = 120 + int(20 * math.sin(i / 4))
    lines.append(
        f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{ele:.1f}</ele>'
        f'<time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time>'
        f'<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr>'
        f'</gpxtpx:TrackPointExtension></extensions></trkpt>\n'
    )
lines.append(FOOTER)

with open("test_fixtures/sample.gpx", "w") as f:
    f.write("".join(lines))

print("wrote test_fixtures/sample.gpx with", N_POINTS, "points,",
      (N_POINTS - 1) * 2, "seconds duration")
