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

from .app import LINE_COLOURS, draw_frame
from .demo import DemoFeed
from .layout import build_layout
from .positions import estimate_positions, parse_predictions
from .tfl import Route, Station
from .validate import check_positions, summarise


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
        self.fetched_at = 0.0
        self._demo: DemoFeed | None = None

    # -- data in ------------------------------------------------------------

    def update(self, arrivals_json: str, now: float) -> int:
        """Load a fresh TfL response. Returns the number of trains seen."""
        self.predictions = parse_predictions(json.loads(arrivals_json), self.route.line_id)
        self.fetched_at = now
        return len({p.vehicle_id for p in self.predictions})

    def update_demo(self, now: float) -> int:
        """Same as update(), but from the simulated feed."""
        if self._demo is None:
            self._demo = DemoFeed(self.route, clock=lambda: self._demo_now)
        self._demo_now = now
        return self.update(json.dumps(self._demo.arrivals()), now)

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

    def lit_pixels(self, now: float, show_track: bool = True) -> list[int]:
        """Flat list [x, y, r, g, b, x, y, r, g, b, ...] of every lit LED right now."""
        positions = estimate_positions(self.predictions, self.route, elapsed_s=now - self.fetched_at)
        canvas = _RecordingCanvas()
        draw_frame(canvas, self.layout, positions, self.colour, show_track=show_track)
        return [v for (x, y), rgb in canvas.pixels.items() for v in (x, y, *rgb)]

    def stations(self) -> str:
        """JSON list of {name, x, y, lines}, for labels and tooltips."""
        return json.dumps(
            [
                {"name": s.name, "x": x, "y": y, "lines": s.lines}
                for s, (x, y) in zip(self.route.stations, self.layout.station_px)
            ]
        )

    def trains(self, now: float) -> str:
        """JSON list of trains (like `tube-leds peek`), for the table under the map."""
        names = [s.name for s in self.route.stations]
        positions = estimate_positions(self.predictions, self.route, elapsed_s=now - self.fetched_at)
        verdicts = {c.vehicle_id: c.verdict for c in self._checks()}
        rows = [
            {
                "id": p.vehicle_id,
                "from": names[p.from_idx],
                "to": names[p.to_idx],
                "towards": {1: names[-1], -1: names[0]}.get(p.direction, ""),
                "progress": round(p.progress, 3),
                "seconds": round(p.seconds_to_next),
                "tfl": p.current_location,
                "check": verdicts.get(p.vehicle_id, "unknown"),
            }
            for p in positions
        ]
        return json.dumps(sorted(rows, key=lambda r: names.index(r["to"])))

    # -- checking ---------------------------------------------------------------

    def _checks(self):
        # Compare at the moment the data was fetched: TfL's text describes the train *then*,
        # and our dead-reckoned dots have moved on since.
        positions = estimate_positions(self.predictions, self.route, elapsed_s=0.0)
        return check_positions(positions, self.route)

    def check_summary(self) -> str:
        """JSON {agree, disagree, unknown, checked, rate}: our estimate vs TfL's own location text."""
        return json.dumps(summarise(self._checks()))

    def arrivals_at(self, index: int, now: float) -> str:
        """Next trains at one station, like a platform board (to compare with TfL Go, Apple Maps...)."""
        station = self.route.stations[index]
        names = [s.name for s in self.route.stations]
        elapsed = now - self.fetched_at
        heading = {p.vehicle_id: p.direction for p in estimate_positions(self.predictions, self.route, elapsed_s=elapsed)}
        rows = []
        for p in self.predictions:
            seconds = p.seconds - elapsed
            if p.station_id != station.id or seconds < -20:  # skip trains that have already left
                continue
            dest = self.route.index.get(p.destination_id)
            direction = heading.get(p.vehicle_id) or (0 if dest is None else (dest > index) - (dest < index))
            rows.append(
                {
                    "id": p.vehicle_id,
                    "seconds": max(round(seconds), 0),
                    "destination": names[dest] if dest is not None else "",
                    "towards": {1: names[-1], -1: names[0]}.get(direction, ""),
                }
            )
        rows.sort(key=lambda r: r["seconds"])
        return json.dumps({"station": station.name, "arrivals": rows})
