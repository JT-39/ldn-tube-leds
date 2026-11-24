"""Mapping stations onto pixels.

The obvious approach is to plot each station at its real latitude/longitude,
scaled to fit the grid. Try it (``--layout geo``) and you'll hit the problem
every tube map designer hits: central London is crowded. Charing Cross and
Embankment are about 170 m apart, which is less than one pixel at this scale.

So the default is a **schematic** layout in the spirit of Harry Beck's map:

* each segment's direction is snapped to the nearest multiple of 45 degrees,
* every segment gets the same length (``step`` pixels),
* ``step`` is the biggest value that still fits the grid.

The result keeps the rough shape of the line but spaces stations evenly.
Later you'll replace this with a hand-drawn pixel path, as Fraser did. The
rest of the code doesn't care, as long as you produce a ``Layout``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .positions import TrainPosition
from .tfl import Route

Pixel = tuple[int, int]


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    station_px: tuple[Pixel, ...]
    segments: tuple[tuple[Pixel, ...], ...]  # segments[i] = pixels from station i to i+1, both ends included

    def train_pixels(self, pos: TrainPosition) -> tuple[Pixel, Pixel | None]:
        """The pixel for a train, plus the one just behind it (its 'tail')."""
        if pos.from_idx == pos.to_idx:
            return self.station_px[pos.to_idx], None
        path = self.segments[min(pos.from_idx, pos.to_idx)]
        if pos.direction < 0:
            path = path[::-1]
        k = round(pos.progress * (len(path) - 1))
        return path[k], (path[k - 1] if k > 0 else None)

    def collisions(self, route: Route) -> list[tuple[str, str]]:
        """Pairs of stations that landed on the same pixel."""
        seen: dict[Pixel, str] = {}
        clashes = []
        for station, px in zip(route.stations, self.station_px):
            if px in seen:
                clashes.append((seen[px], station.name))
            seen.setdefault(px, station.name)
        return clashes


def line_pixels(a: Pixel, b: Pixel) -> tuple[Pixel, ...]:
    """Bresenham's line algorithm: the pixels on a straight line from a to b."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return tuple(out)
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def project_km(route: Route) -> list[tuple[float, float]]:
    """lat/lon -> flat x/y in km (x east, y *south*, because pixel rows go down)."""
    lat0 = sum(s.lat for s in route.stations) / len(route)
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57
    return [(s.lon * kx, -s.lat * ky) for s in route.stations]


def _centre(points: list[tuple[float, float]], width: int, height: int, margin: int) -> list[Pixel]:
    """Shift points so their bounding box sits in the middle of the grid."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    ox = margin + (width - 2 * margin - 1 - (max(xs) - min(xs))) / 2 - min(xs)
    oy = margin + (height - 2 * margin - 1 - (max(ys) - min(ys))) / 2 - min(ys)
    return [(round(x + ox), round(y + oy)) for x, y in points]


def geo_points(route: Route, width: int, height: int, margin: int = 1) -> list[Pixel]:
    pts = project_km(route)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    scale = min(
        (width - 1 - 2 * margin) / max(max(xs) - min(xs), 1e-9),
        (height - 1 - 2 * margin) / max(max(ys) - min(ys), 1e-9),
    )
    return _centre([(x * scale, y * scale) for x, y in pts], width, height, margin)


def snap45(dx: float, dy: float) -> Pixel:
    """Round a direction to the nearest of the 8 compass directions."""
    k = round(math.atan2(dy, dx) / (math.pi / 4))
    return round(math.cos(k * math.pi / 4)), round(math.sin(k * math.pi / 4))


def schematic_points(route: Route, width: int, height: int, margin: int = 1) -> list[Pixel]:
    pts = project_km(route)
    dirs = [snap45(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    for step in range(16, 0, -1):  # try big spacing first
        walk = [(0, 0)]
        for dx, dy in dirs:
            x, y = walk[-1]
            walk.append((x + dx * step, y + dy * step))
        xs, ys = [p[0] for p in walk], [p[1] for p in walk]
        if max(xs) - min(xs) <= width - 1 - 2 * margin and max(ys) - min(ys) <= height - 1 - 2 * margin:
            return _centre(walk, width, height, margin)
    raise ValueError(f"{len(route)} stations don't fit on a {width}x{height} grid")


def build_layout(route: Route, width: int, height: int, style: str = "schematic") -> Layout:
    if style == "schematic":
        pts = schematic_points(route, width, height)
    elif style == "geo":
        pts = geo_points(route, width, height)
    else:
        raise ValueError(f"unknown layout style: {style}")
    segments = tuple(line_pixels(a, b) for a, b in zip(pts, pts[1:]))
    return Layout(width, height, tuple(pts), segments)


def ascii_preview(layout: Layout, route: Route) -> str:
    """A quick look at the layout in the terminal. Stations are letters A, B, C..."""
    grid = [[" "] * layout.width for _ in range(layout.height)]
    for seg in layout.segments:
        for x, y in seg:
            grid[y][x] = "·"
    legend = []
    for i, ((x, y), station) in enumerate(zip(layout.station_px, route.stations)):
        mark = chr(ord("A") + i) if i < 26 else "#"
        grid[y][x] = mark
        legend.append(f"{mark} {station.name}")
    border = "+" + "-" * layout.width + "+"
    rows = [border, *("|" + "".join(r) + "|" for r in grid), border]
    return "\n".join(rows) + "\n\n" + "   ".join(legend)
