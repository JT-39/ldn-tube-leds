"""The bridge between the web page and the model. Runs in the browser via Pyodide.

The page (web/index.html) does the browser-ish jobs: fetching from TfL and
drawing circles on a <canvas>. Everything in between is the same Python you
run on your laptop: parse_predictions -> estimate_positions -> build_layout
-> draw_frame. The page talks to one object, ``Board``, and passes plain JSON
strings and numbers back and forth, which keeps the JS <-> Python boundary
simple.
"""

from __future__ import annotations

import json

from dataclasses import asdict

from .app import DUE_HERE, LINE_COLOURS, NOT_IN_SERVICE, draw_frame, service_styles
from .demo import DemoFeed
from .layout import build_layout
from .positions import estimate_positions, parse_predictions
from .service import DelayTracker, LineStatus, parse_status
from .tfl import Route, Station, align_to_route

DUE_WINDOW_S = 600  # "due at this station" means within the next 10 minutes


class _RecordingCanvas:
    """Collects SetPixel calls. Later calls win, so trains draw over track."""

    def __init__(self):
        self.pixels: dict[tuple[int, int], tuple[int, int, int]] = {}

    def SetPixel(self, x, y, r, g, b):
        self.pixels[(x, y)] = (r, g, b)


class Board:
    def __init__(
        self, route_json: str, width: int = 128, height: int = 64, style: str = "schematic", tracks: int = 2
    ):
        data = json.loads(route_json)
        self.route = Route(data["line_id"], tuple(Station(**s) for s in data["stations"]))
        self.layout = build_layout(self.route, width, height, style, tracks)
        self.colour = LINE_COLOURS.get(self.route.line_id, (255, 255, 255))
        self.predictions = []
        self.board_rows: list[dict] = []  # every row with a station and a time, even without a train id
        self.fetched_at = 0.0
        self.simulated = False
        self.status = LineStatus()  # from TfL's /Line/{id}/Status (live only)
        self.delays = DelayTracker()
        self._demo: DemoFeed | None = None

    # -- data in ------------------------------------------------------------

    def update(self, arrivals_json: str, now: float) -> int:
        """Load a fresh TfL response. Returns the number of trains seen."""
        rows = align_to_route(json.loads(arrivals_json), self.route)
        rows = [r for r in rows if not r.get("lineId") or r["lineId"] == self.route.line_id]
        self.predictions = parse_predictions(rows, self.route.line_id)
        # Station boards don't need to follow a train, so they keep rows that lack a vehicleId.
        self.board_rows = [r for r in rows if r.get("naptanId") in self.route.index and "timeToStation" in r]
        self.fetched_at = now
        self.simulated = False
        self.delays.update(self.predictions, now)
        return len({p.vehicle_id for p in self.predictions})

    def update_demo(self, now: float) -> int:
        """Same as update(), but from the simulated feed (no delays, no line status)."""
        if self._demo is None:
            self._demo = DemoFeed(self.route, clock=lambda: self._demo_now)
        self._demo_now = now
        n = self.update(json.dumps(self._demo.arrivals()), now)
        self.simulated = True
        self.delays.reset()
        return n

    def set_status(self, status_json: str) -> str:
        """Load TfL's line status. Returns a summary for the page's banner."""
        self.status = parse_status(json.loads(status_json), self.route)
        return json.dumps(asdict(self.status))

    # -- styling ------------------------------------------------------------

    def _positions(self, now: float):
        return estimate_positions(self.predictions, self.route, elapsed_s=now - self.fetched_at)

    def _due_at(self, index: int, now: float) -> set[str]:
        """Trains predicted to reach station ``index`` within the next 10 minutes."""
        if not 0 <= index < len(self.route):
            return set()
        station_id, elapsed = self.route.stations[index].id, now - self.fetched_at
        return {
            p.vehicle_id for p in self.predictions
            if p.station_id == station_id and -20 <= p.seconds - elapsed <= DUE_WINDOW_S
        }

    def _styles(self, positions, now: float, highlight: int):
        status = None if self.simulated else self.status
        late = {} if self.simulated else self.delays.late
        colours, red = service_styles(positions, status, late, len(self.layout.forward))
        for vehicle in self._due_at(highlight, now):
            if colours.get(vehicle) != NOT_IN_SERVICE:
                colours[vehicle] = DUE_HERE  # TfL blue for the station you picked
        return colours, red

    # -- data out -----------------------------------------------------------

    def bounds(self) -> list[int]:
        """[x0, y0, x1, y1] of the drawn line, so the page can crop empty space."""
        pts = self.layout.track_pixels() | {p for mark in self.layout.station_marks for p in mark}
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        return [min(xs), min(ys), max(xs), max(ys)]

    def static_pixels(self) -> list[int]:
        """Flat [x, y, x, y, ...] of every track and station LED (what labels must avoid)."""
        pts = self.layout.track_pixels() | {p for mark in self.layout.station_marks for p in mark}
        return [v for p in sorted(pts) for v in p]

    def tracks(self) -> str:
        """JSON like {"upper": "Elephant & Castle", "lower": "Harrow & Wealdstone"}, for the legend."""
        return json.dumps(self.layout.describe_tracks(self.route))

    def lit_pixels(self, now: float, show_track: bool = True, highlight: int = -1) -> list[int]:
        """Flat list [x, y, r, g, b, x, y, r, g, b, ...] of every lit LED right now.

        ``highlight`` is a station index: trains due there within 10 minutes turn TfL blue.
        """
        positions = self._positions(now)
        colours, red = self._styles(positions, now, highlight)
        canvas = _RecordingCanvas()
        draw_frame(canvas, self.layout, positions, self.colour, show_track=show_track,
                   train_colours=colours, red_segments=red)
        return [v for (x, y), rgb in canvas.pixels.items() for v in (x, y, *rgb)]

    def stations(self) -> str:
        """JSON list of {id, name, x, y, lines}, for labels, tooltips and push subscriptions."""
        return json.dumps(
            [
                {"id": s.id, "name": s.name, "x": x, "y": y, "lines": s.lines}
                for s, (x, y) in zip(self.route.stations, self.layout.station_px)
            ]
        )

    def trains(self, now: float, highlight: int = -1) -> str:
        """JSON list of trains (like `tube-leds peek`), for the lists under the map."""
        names, last = [s.name for s in self.route.stations], len(self.route) - 1
        due = self._due_at(highlight, now)
        late = {} if self.simulated else self.delays.late
        no_service = not self.simulated and self.status.no_service

        def towards(p):
            if p.direction == 0:  # waiting at a terminus: it'll head for the other end
                return names[-1] if p.to_idx < last else names[0]
            return names[-1] if p.direction > 0 else names[0]

        rows = [
            {
                "id": p.vehicle_id,
                "from": names[p.from_idx],
                "to": names[p.to_idx],
                "towards": towards(p),
                "progress": round(p.progress, 3),
                "seconds": round(p.seconds_to_next),
                "late": round(late.get(p.vehicle_id, 0)),
                "due_here": p.vehicle_id in due,
                "in_service": not no_service,
            }
            for p in self._positions(now)
        ]
        return json.dumps(sorted(rows, key=lambda r: r["seconds"]))  # soonest arrival first

    def arrivals_at(self, index: int, now: float) -> str:
        """Next trains at one station, like a platform board (to compare with TfL Go, Apple Maps...)."""
        station = self.route.stations[index]
        names = [s.name for s in self.route.stations]
        elapsed = now - self.fetched_at
        heading = {p.vehicle_id: p.direction for p in estimate_positions(self.predictions, self.route, elapsed_s=elapsed)}
        rows = []
        for r in self.board_rows:
            seconds = float(r["timeToStation"]) - elapsed
            if r["naptanId"] != station.id or seconds < -20:  # skip trains that have already left
                continue
            dest = self.route.index.get(r.get("destinationNaptanId"))
            vehicle = (r.get("vehicleId") or "").strip()
            direction = heading.get(vehicle) or (0 if dest is None else (dest > index) - (dest < index))
            rows.append(
                {
                    "id": vehicle,
                    "seconds": max(round(seconds), 0),
                    "destination": names[dest] if dest is not None else clean_destination(r.get("destinationName")),
                    "towards": {1: names[-1], -1: names[0]}.get(direction, ""),
                }
            )
        rows.sort(key=lambda r: r["seconds"])
        return json.dumps({"station": station.name, "arrivals": rows})


def clean_destination(name: str | None) -> str:
    return (name or "").removesuffix(" Underground Station").removesuffix(" Station")
