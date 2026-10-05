"""Render Speed / Elevation / HR / Map widgets onto transparent RGBA frames."""
import math

from PIL import Image, ImageDraw, ImageFont


POSITIONS = [
    "top-left", "top-middle", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-middle", "bottom-right",
]

SPEED_STYLES = ["Digits", "Gauge", "Bar", "Digital/LCD", "Minimal"]

THEMES = {
    "Dark / Minimal": {
        "panel": (0, 0, 0),
        "text": (255, 255, 255),
        "text_secondary": (210, 210, 210),
        "accent": (90, 200, 255),
        "track": (170, 170, 170),
        "marker": (255, 60, 60),
    },
    "Light / Clean": {
        "panel": (255, 255, 255),
        "text": (30, 30, 30),
        "text_secondary": (90, 90, 90),
        "accent": (0, 120, 220),
        "track": (120, 120, 120),
        "marker": (220, 40, 40),
    },
    "Neon / Sport": {
        "panel": (10, 10, 20),
        "text": (0, 255, 170),
        "text_secondary": (0, 200, 140),
        "accent": (255, 0, 200),
        "track": (0, 255, 170),
        "marker": (255, 0, 120),
    },
    "High Contrast": {
        "panel": (0, 0, 0),
        "text": (255, 255, 0),
        "text_secondary": (255, 255, 255),
        "accent": (255, 255, 0),
        "track": (255, 255, 255),
        "marker": (255, 0, 0),
    },
    "Sunset / Warm": {
        "panel": (35, 15, 10),
        "text": (255, 235, 210),
        "text_secondary": (255, 180, 120),
        "accent": (255, 140, 50),
        "track": (255, 170, 90),
        "marker": (255, 70, 40),
    },
    "Ocean / Cool Blue": {
        "panel": (5, 20, 35),
        "text": (220, 245, 255),
        "text_secondary": (140, 200, 230),
        "accent": (0, 180, 230),
        "track": (80, 190, 220),
        "marker": (255, 200, 0),
    },
    "Carbon / Racing": {
        "panel": (24, 24, 26),
        "text": (240, 240, 240),
        "text_secondary": (160, 160, 165),
        "accent": (220, 20, 30),
        "track": (190, 190, 195),
        "marker": (220, 20, 30),
    },
    "Forest / Trail": {
        "panel": (18, 28, 18),
        "text": (230, 240, 220),
        "text_secondary": (170, 195, 155),
        "accent": (120, 190, 80),
        "track": (150, 170, 110),
        "marker": (200, 140, 60),
    },
}
DEFAULT_THEME = "Dark / Minimal"

FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FONT_PATH_MONO = "/System/Library/Fonts/Menlo.ttc"

_font_cache = {}


MIN_FONT_SIZE = 12


def _font(size, mono=False):
    size = max(size, MIN_FONT_SIZE)
    key = (size, mono)
    if key not in _font_cache:
        path = FONT_PATH_MONO if mono else FONT_PATH
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except OSError:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _anchor_box(position, widget_w, widget_h, frame_w, frame_h, margin=24):
    if position in ("top-left", "middle-left", "bottom-left"):
        x = margin
    elif position in ("top-right", "middle-right", "bottom-right"):
        x = frame_w - widget_w - margin
    else:  # top-middle, center, bottom-middle
        x = (frame_w - widget_w) // 2

    if position in ("top-left", "top-middle", "top-right"):
        y = margin
    elif position in ("bottom-left", "bottom-middle", "bottom-right"):
        y = frame_h - widget_h - margin
    else:  # middle-left, center, middle-right
        y = (frame_h - widget_h) // 2

    return x, y


def _panel(draw, x, y, w, h, opacity, color, show_box=True):
    if not show_box:
        return
    draw.rounded_rectangle(
        [x, y, x + w, y + h], radius=14, fill=(*color, int(255 * opacity))
    )


class BaseWidget:
    def __init__(self, position="top-left", opacity=0.55, scale=1.0, theme=DEFAULT_THEME,
                 show_box=True, text_black=False):
        self.position = position
        self.opacity = opacity
        self.scale = scale
        self.theme = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.show_box = show_box
        self.text_black = text_black

    def _text_color(self, secondary=False):
        if self.text_black:
            return (0, 0, 0)
        return self.theme["text_secondary"] if secondary else self.theme["text"]


class SpeedWidget(BaseWidget):
    name = "speed"
    size = (180, 90)

    def __init__(self, unit="kmh", style="Digits", max_speed_kmh=60, **kwargs):
        super().__init__(**kwargs)
        self.unit = unit
        self.style = style if style in SPEED_STYLES else "Digits"
        self.max_speed_kmh = max_speed_kmh

    def _value(self, telemetry_point):
        mps = telemetry_point.speed_mps
        if self.unit == "mph":
            return mps * 2.23694, "mph"
        return mps * 3.6, "km/h"

    def draw(self, canvas, telemetry_point, frame_w, frame_h):
        val, unit_label = self._value(telemetry_point)
        method = getattr(self, f"_draw_{self.style.lower().replace('/', '_')}", None)
        if method is None:
            method = self._draw_digits
        method(canvas, val, unit_label, frame_w, frame_h)

    def _draw_digits(self, canvas, val, unit_label, frame_w, frame_h):
        pad = int(16 * self.scale)
        value_font = _font(int(36 * self.scale))
        unit_font = _font(int(16 * self.scale))

        draw = ImageDraw.Draw(canvas)
        value_text = f"{val:0.1f}"
        vb = draw.textbbox((0, 0), value_text, font=value_font)
        ub = draw.textbbox((0, 0), unit_label, font=unit_font)
        value_h = vb[3] - vb[1]
        unit_h = ub[3] - ub[1]
        gap = int(4 * self.scale)

        content_w = max(vb[2] - vb[0], ub[2] - ub[0])
        content_h = value_h + gap + unit_h
        w = content_w + 2 * pad
        h = content_h + 2 * pad

        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)

        draw.text((x + pad - vb[0], y + pad - vb[1]), value_text,
                  font=value_font, fill=(*self._text_color(), 255))
        draw.text((x + pad - ub[0], y + pad + value_h + gap - ub[1]), unit_label,
                  font=unit_font, fill=(*self._text_color(secondary=True), 255))

    def _draw_minimal(self, canvas, val, unit_label, frame_w, frame_h):
        text = f"{val:0.1f} {unit_label}"
        font = _font(int(44 * self.scale))
        draw = ImageDraw.Draw(canvas)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        base = self._text_color()
        draw.text((x - bbox[0], y - bbox[1]), text, font=font,
                   fill=(*base, int(255 * self.opacity) if self.opacity < 1 else 255))

    def _draw_digital_lcd(self, canvas, val, unit_label, frame_w, frame_h):
        w, h = int(self.size[0] * self.scale), int(self.size[1] * self.scale)
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)
        text = f"{val:05.1f}"
        accent = (0, 0, 0) if self.text_black else self.theme["accent"]
        draw.text((x + int(16 * self.scale), y + int(10 * self.scale)), text,
                  font=_font(int(34 * self.scale), mono=True), fill=(*accent, 255))
        draw.text((x + int(16 * self.scale), y + h - int(26 * self.scale)), unit_label,
                  font=_font(int(16 * self.scale), mono=True), fill=(*self._text_color(secondary=True), 255))

    def _draw_bar(self, canvas, val, unit_label, frame_w, frame_h):
        w, h = int(220 * self.scale), int(70 * self.scale)
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)

        label_w = int(90 * self.scale)
        bar_x0, bar_y0 = x + label_w, y + int(14 * self.scale)
        bar_w, bar_h = w - label_w - int(14 * self.scale), h - int(28 * self.scale)
        frac = max(0.0, min(1.0, val / max(self.max_speed_kmh, 1)))

        draw.rounded_rectangle([bar_x0, bar_y0, bar_x0 + bar_w, bar_y0 + bar_h],
                                radius=6, fill=(*self.theme["text_secondary"], 60))
        if frac > 0:
            draw.rounded_rectangle([bar_x0, bar_y0, bar_x0 + bar_w * frac, bar_y0 + bar_h],
                                    radius=6, fill=(*self.theme["accent"], 255))
        draw.text((x + int(12 * self.scale), y + int(h / 2 - 16 * self.scale)), f"{val:0.0f}",
                  font=_font(int(28 * self.scale)), fill=(*self._text_color(), 255))

    def _draw_gauge(self, canvas, val, unit_label, frame_w, frame_h):
        d = int(170 * self.scale)
        bottom_pad = int(10 * self.scale)
        w, arc_h = d, int(d * 0.68)
        h = arc_h + bottom_pad
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)

        if self.show_box:
            draw.rounded_rectangle([x, y, x + w, y + h], radius=14,
                                    fill=(*self.theme["panel"], int(255 * self.opacity)))

        cx, cy = x + w // 2, y + arc_h
        radius = w // 2 - int(10 * self.scale)
        bbox = [cx - radius, cy - radius, cx + radius, cy + radius]

        start_angle, end_angle = 180, 360
        draw.arc(bbox, start_angle, end_angle, fill=(*self.theme["text_secondary"], 160),
                 width=max(2, int(10 * self.scale)))

        frac = max(0.0, min(1.0, val / max(self.max_speed_kmh, 1)))
        sweep_angle = start_angle + frac * (end_angle - start_angle)
        draw.arc(bbox, start_angle, sweep_angle, fill=(*self.theme["accent"], 255),
                 width=max(2, int(10 * self.scale)))

        text = f"{val:0.0f}"
        font = _font(int(30 * self.scale))
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text((cx - tw / 2 - tb[0], cy - radius * 0.55 - th / 2 - tb[1]), text,
                   font=font, fill=(*self._text_color(), 255))
        small = _font(int(14 * self.scale))
        sb = draw.textbbox((0, 0), unit_label, font=small)
        sw = sb[2] - sb[0]
        draw.text((cx - sw / 2 - sb[0], cy - radius * 0.55 + th / 2), unit_label,
                   font=small, fill=(*self._text_color(secondary=True), 255))


class ElevationWidget(BaseWidget):
    name = "elevation"
    size = (260, 110)

    def __init__(self, unit="m", **kwargs):
        super().__init__(**kwargs)
        self.unit = unit
        self._profile = None

    def set_telemetry(self, telemetry):
        self._telemetry = telemetry
        n = 80
        pts = []
        for i in range(n):
            frac = i / (n - 1)
            idx = int(frac * (len(telemetry.points) - 1))
            pts.append(telemetry.points[idx].ele)
        self._profile = pts
        self._ele_min = telemetry.min_ele
        self._ele_max = max(telemetry.max_ele, telemetry.min_ele + 1)
        self._duration_s = max(telemetry.duration_s, 1e-6)
        self._start_time = telemetry.start_time

    def draw(self, canvas, telemetry_point, frame_w, frame_h):
        w, h = int(self.size[0] * self.scale), int(self.size[1] * self.scale)
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)

        graph_x0, graph_y0 = x + int(14 * self.scale), y + int(14 * self.scale)
        graph_w, graph_h = w - int(28 * self.scale), h - int(50 * self.scale)
        if self._profile:
            span = self._ele_max - self._ele_min
            coords = []
            for i, ele in enumerate(self._profile):
                px = graph_x0 + (i / (len(self._profile) - 1)) * graph_w
                py = graph_y0 + graph_h - ((ele - self._ele_min) / span) * graph_h
                coords.append((px, py))
            draw.line(coords, fill=(*self.theme["accent"], 255), width=max(2, int(3 * self.scale)))

            elapsed = (telemetry_point.t - self._start_time).total_seconds()
            frac_t = max(0.0, min(1.0, elapsed / self._duration_s))
            mx = graph_x0 + frac_t * graph_w
            my = graph_y0 + graph_h - ((telemetry_point.ele - self._ele_min) / span) * graph_h
            r = max(3, int(5 * self.scale))
            draw.ellipse([mx - r, my - r, mx + r, my + r], fill=(*self.theme["marker"], 255),
                         outline=(*self.theme["text"], 255), width=max(1, int(2 * self.scale)))

        ele = telemetry_point.ele
        if self.unit == "ft":
            val = ele * 3.28084
            unit_label = "ft"
        else:
            val = ele
            unit_label = "m"
        draw.text((x + int(14 * self.scale), y + h - int(28 * self.scale)), f"{val:0.0f} {unit_label}",
                  font=_font(int(16 * self.scale)), fill=(*self._text_color(), 255))


class HRWidget(BaseWidget):
    name = "hr"
    size = (150, 90)

    def __init__(self, max_hr_zone=190, **kwargs):
        super().__init__(**kwargs)
        self.max_hr_zone = max_hr_zone

    def draw(self, canvas, telemetry_point, frame_w, frame_h):
        w, h = int(self.size[0] * self.scale), int(self.size[1] * self.scale)
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)

        hr = telemetry_point.hr
        if hr is None:
            draw.text((x + int(16 * self.scale), y + int(30 * self.scale)), "-- bpm",
                      font=_font(int(36 * self.scale)), fill=(*self._text_color(), 180))
            return

        frac = max(0.0, min(1.0, hr / self.max_hr_zone))
        if self.text_black:
            color = (0, 0, 0)
        else:
            base = self.theme["text"]
            accent = self.theme["accent"]
            color = tuple(int(base[i] * (1 - frac) + accent[i] * frac) for i in range(3))
        draw.text((x + int(16 * self.scale), y + int(10 * self.scale)), f"{hr:0.0f}",
                  font=_font(int(36 * self.scale)), fill=(*color, 255))
        draw.text((x + int(16 * self.scale), y + h - int(26 * self.scale)), "bpm",
                  font=_font(int(16 * self.scale)), fill=(*self._text_color(secondary=True), 255))


class MapWidget(BaseWidget):
    name = "map"
    size = (220, 220)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._track_norm = None

    def set_telemetry(self, telemetry):
        lat_min, lat_max = telemetry.lat_min, telemetry.lat_max
        lon_min, lon_max = telemetry.lon_min, telemetry.lon_max
        lat_center = (lat_min + lat_max) / 2.0

        # Correct for longitude convergence so the track isn't horizontally stretched
        # away from the equator (1 deg lon = cos(lat) * 1 deg lat, in real-world distance).
        lon_scale = math.cos(math.radians(lat_center))

        lat_span = max(lat_max - lat_min, 1e-9)
        lon_span = max((lon_max - lon_min) * lon_scale, 1e-9)

        # Fit the track's true aspect ratio inside the widget's square-ish drawable
        # area, instead of independently stretching lat/lon to fill x/y.
        geo_aspect = lon_span / lat_span  # width / height, in real-world units

        pad = 0.18

        self._lat_min, self._lat_max = lat_min, lat_max
        self._lon_min, self._lon_max = lon_min, lon_max
        self._lon_scale = lon_scale
        self._lat_span, self._lon_span = lat_span, lon_span
        self._geo_aspect = geo_aspect
        self._pad = pad
        self._telemetry = telemetry
        self._track_latlon = [(p.lat, p.lon) for p in telemetry.points]
        self._track_norm = True  # marker that telemetry is set

    def _project_px(self, lat, lon, w, h):
        pad = self._pad
        drawable_w = w * (1.0 - 2 * pad)
        drawable_h = h * (1.0 - 2 * pad)

        # Scale the geo bounding box to fit inside the drawable area while
        # preserving its true aspect ratio (no stretch), then center it.
        if self._geo_aspect > drawable_w / drawable_h:
            box_w = drawable_w
            box_h = drawable_w / self._geo_aspect
        else:
            box_h = drawable_h
            box_w = drawable_h * self._geo_aspect

        off_x = (w - box_w) / 2.0
        off_y = (h - box_h) / 2.0

        fx = ((lon - self._lon_min) * self._lon_scale) / self._lon_span
        fy = 1.0 - (lat - self._lat_min) / self._lat_span

        return off_x + fx * box_w, off_y + fy * box_h

    def draw(self, canvas, telemetry_point, frame_w, frame_h):
        w, h = int(self.size[0] * self.scale), int(self.size[1] * self.scale)
        x, y = _anchor_box(self.position, w, h, frame_w, frame_h)
        draw = ImageDraw.Draw(canvas)
        _panel(draw, x, y, w, h, self.opacity, self.theme["panel"], self.show_box)

        if self._track_norm:
            coords = [self._project_px(lat, lon, w, h) for lat, lon in self._track_latlon]
            coords = [(x + px, y + py) for px, py in coords]
            draw.line(coords, fill=(*self.theme["track"], 220), width=max(2, int(3 * self.scale)))

            px, py = self._project_px(telemetry_point.lat, telemetry_point.lon, w, h)
            cx, cy = x + px, y + py
            r = max(3, int(6 * self.scale))
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                fill=(*self.theme["marker"], 255),
                outline=(*self.theme["text"], 255),
                width=max(1, int(2 * self.scale)),
            )


WIDGET_CLASSES = {
    "speed": SpeedWidget,
    "elevation": ElevationWidget,
    "hr": HRWidget,
    "map": MapWidget,
}


def build_widgets(config, telemetry, theme=DEFAULT_THEME):
    """config: dict like {'speed': {'enabled': True, 'position': 'top-left', ...}, ...}"""
    widgets = []
    for key, cls in WIDGET_CLASSES.items():
        wc = config.get(key, {})
        if not wc.get("enabled"):
            continue
        kwargs = {k: v for k, v in wc.items() if k != "enabled"}
        kwargs.setdefault("theme", theme)
        widget = cls(**kwargs)
        if hasattr(widget, "set_telemetry"):
            widget.set_telemetry(telemetry)
        widgets.append(widget)
    return widgets


def render_frame(widgets, telemetry_point, frame_w, frame_h):
    canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    for widget in widgets:
        widget.draw(canvas, telemetry_point, frame_w, frame_h)
    return canvas
