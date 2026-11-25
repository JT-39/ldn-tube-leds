"""A pretend TfL feed, for working offline and for tests.

Trains shuttle from one end of the route to the other and back, using the same
run-time model as positions.py. The output has the same shape as real TfL
arrivals, so everything downstream runs unchanged. This is *simulated* data:
use it to check the plumbing, not to learn anything about the real Bakerloo.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .positions import RunTime, default_run_time
from .tfl import Route


class DemoFeed:
    def __init__(
        self,
        route: Route,
        n_trains: int = 10,
        turnaround_s: float = 180.0,
        run_time: RunTime = default_run_time,
        clock: Callable[[], float] = time.time,
    ):
        self.route = route
        self.n_trains = n_trains
        self.clock = clock
        legs = [run_time(a, b) for a, b in zip(route.stations, route.stations[1:])]

        # One full cycle as a timeline of arrivals: (time, station index, direction).
        # Forward to the end, wait, back to the start, wait.
        self.events: list[tuple[float, int, int]] = []
        t = 0.0
        for i, leg in enumerate(legs):
            t += leg
            self.events.append((t, i + 1, +1))
        t += turnaround_s
        for i, leg in reversed(list(enumerate(legs))):
            t += leg
            self.events.append((t, i, -1))
        self.cycle_s = t + turnaround_s

    def arrivals(self) -> list[dict]:
        now = self.clock()
        stations, n = self.route.stations, len(self.route)
        out = []
        for k in range(self.n_trains):
            phase = (now + k * self.cycle_s / self.n_trains) % self.cycle_s
            # Next few arrivals, unrolled over two cycles so we can look past the wrap-around.
            upcoming = [(t, i, d) for t, i, d in self.events if t > phase]
            upcoming += [(t + self.cycle_s, i, d) for t, i, d in self.events]
            direction = upcoming[0][2]
            dest = stations[n - 1 if direction > 0 else 0]
            for t, i, d in upcoming[:15]:
                if d != direction or t - phase > 25 * 60:
                    break  # past its terminus, or too far ahead: TfL wouldn't list it
                prev = stations[i - d]
                out.append(
                    {
                        "vehicleId": f"D{k + 1:02d}",
                        "lineId": self.route.line_id,
                        "naptanId": stations[i].id,
                        "stationName": stations[i].name,
                        "timeToStation": round(t - phase),
                        "destinationNaptanId": dest.id,
                        "destinationName": dest.name,
                        "direction": "outbound" if direction > 0 else "inbound",
                        "currentLocation": f"Between {prev.name} and {stations[upcoming[0][1]].name}",
                    }
                )
        return out
