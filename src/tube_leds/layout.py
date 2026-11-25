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

**Two tracks.** By default each direction gets its own track, one pixel either
side of the centre line, with an empty pixel between them. Trains keep left,
as on real UK railways, so you can see which way a train is going from the
track it's on. The offset at each station is a *miter*: the corner of the
two neighbouring segments' offsets, so both tracks stay parallel around bends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .positions import TrainPosition
from .tfl import Route

Pixel = tuple[int, int]
Segments = tuple[tuple[Pixel, ...], ...]  # segments[i] = pixels from station i to i+1, both ends included


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    station_px: tuple[Pixel, ...]  # centre of each station (for labels and tooltips)
    forward: Segments  # track used by trains heading towards the END of the route (+1)
    backward: Segments  # track used by trains heading towards the START (-1); same as forward if single-track
    station_marks: tuple[tuple[Pixel, ...], ...]  # pixels to light for each station

    def track(self, direction: int) -> Segments:
        return self.backward if direction < 0 else self.forward

    def track_pixels(self) -> set[Pixel]:
        return {px for segs in (self.forward, self.backward) for seg in segs for px in seg}

    def train_pixels(self, pos: TrainPosition) -> tuple[Pixel, Pixel | None]:
        """The pixel for a train, plus the one just behind it (its 'tail')."""
        last = len(self.station_px) - 1
        if pos.from_idx == pos.to_idx:
            # Waiting at a terminus: put it on the track it's about to leave on.
            direction = 1 if pos.to_idx < last else -1
            segs = self.track(direction)
            seg = segs[min(pos.to_idx, len(segs) - 1)]
            return (seg[0] if direction > 0 else seg[-1]), None
        path = self.track(pos.direction)[min(pos.from_idx, pos.to_idx)]
        if pos.direction < 0:
            path = path[::-1]
        k = round(pos.progress * (len(path) - 1))
        return path[k], (path[k - 1] if k > 0 else None)

    def describe_tracks(self, route: Route) -> dict[str, str]:
        """Which side each direction runs on, in words, e.g. {'upper': 'Elephant & Castle', ...}."""
        fwd = [p for seg in self.forward for p in seg]
        bwd = [p for seg in self.backward for p in seg]
        dx = sum(x for x, _ in fwd) / len(fwd) - sum(x for x, _ in bwd) / len(bwd)
        dy = sum(y for _, y in fwd) / len(fwd) - sum(y for _, y in bwd) / len(bwd)
        end, start = route.stations[-1].name, route.stations[0].name
        if abs(dy) >= abs(dx):
            return {"upper": end, "lower": start} if dy < 0 else {"upper": start, "lower": end}
        return {"left": end, "right": start} if dx < 0 else {"left": start, "right": end}

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


def left_offsets(pts: list[Pixel], max_len: float = 2.0) -> list[Pixel]:
    """For each station, the pixel offset to the LEFT of the direction of travel.

    "Left" for a train going from station i towards station i+1. Pixel rows go
    down, so facing east (1, 0) your left is up (0, -1): left = (dy, -dx).
    At a bend, the miter (n1 + n2) / (1 + n1·n2) is the point exactly one unit
    from *both* neighbouring segments.
    """
    normals = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0) or 1.0
        dx, dy = (x1 - x0) / length, (y1 - y0) / length
        normals.append((dy, -dx))

    out = []
    for i in range(len(pts)):
        around = [normals[j] for j in (i - 1, i) if 0 <= j < len(normals)]
        if len(around) == 1:
            mx, my = around[0]
        else:
            (ax, ay), (bx, by) = around
            dot = ax * bx + ay * by
            mx, my = (ax + bx) / (1 + dot), (ay + by) / (1 + dot)
            if (m := math.hypot(mx, my)) > max_len:  # very sharp bend: don't overshoot
                mx, my = mx * max_len / m, my * max_len / m
        out.append((round(mx), round(my)))
    return out


def build_layout(route: Route, width: int, height: int, style: str = "schematic", tracks: int = 2) -> Layout:
    margin = 2 if tracks == 2 else 1  # room for the outer track
    if style == "schematic":
        pts = schematic_points(route, width, height, margin)
    elif style == "geo":
        pts = geo_points(route, width, height, margin)
    else:
        raise ValueError(f"unknown layout style: {style}")

    def segments(points: list[Pixel]) -> Segments:
        return tuple(line_pixels(a, b) for a, b in zip(points, points[1:]))

    if tracks == 1:
        single = segments(pts)
        return Layout(width, height, tuple(pts), single, single, tuple((p,) for p in pts))

    offsets = left_offsets(pts)
    fwd = [(x + ox, y + oy) for (x, y), (ox, oy) in zip(pts, offsets)]  # trains keep left
    bwd = [(x - ox, y - oy) for (x, y), (ox, oy) in zip(pts, offsets)]
    marks = tuple((f, c, b) for f, c, b in zip(fwd, pts, bwd))  # a little bar across both tracks
    return Layout(width, height, tuple(pts), segments(fwd), segments(bwd), marks)


def ascii_preview(layout: Layout, route: Route) -> str:
    """A quick look at the layout in the terminal. Stations are letters A, B, C..."""
    grid = [[" "] * layout.width for _ in range(layout.height)]
    for x, y in layout.track_pixels():
        grid[y][x] = "·"
    for mark in layout.station_marks:
        for x, y in mark:
            grid[y][x] = "-"
    legend = []
    for i, ((x, y), station) in enumerate(zip(layout.station_px, route.stations)):
        mark = chr(ord("A") + i) if i < 26 else "#"
        grid[y][x] = mark
        legend.append(f"{mark} {station.name}")
    border = "+" + "-" * layout.width + "+"
    rows = [border, *("|" + "".join(r) + "|" for r in grid), border]
    return "\n".join(rows) + "\n\n" + "   ".join(legend)
