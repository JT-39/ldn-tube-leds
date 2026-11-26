"""Is the service running normally? TfL's line status, and trains running late.

Two independent signals, both shown in red on the map:

* **Line status** (``/Line/{id}/Status``): TfL's own summary, e.g. "Minor Delays" or
  "Part Suspended", with free text like "Minor delays between Queen's Park and
  Harrow & Wealdstone due to ...". TfL's structured ``affectedStops`` list is
  usually empty, so we pull the stretch out of the text with the same station
  matcher used in ``validate.py``.
* **Late trains**, from the arrivals themselves: if a train is still heading for
  the same station but its predicted arrival keeps moving later, it's being
  held or running slowly. That catches delays before TfL's status changes.

A line that isn't running at all (e.g. a strike) can still have trains in the
arrivals feed: it comes from the signalling system, and some of those trains are
being moved or stabled, not carrying passengers. When the status says the whole
line has no service, we flag every train as "may not be in service".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .positions import Prediction
from .tfl import Route
from .validate import mentioned_stations

NO_SERVICE = ("suspended", "closed", "not running", "planned closure")  # whole line, unless a stretch is named
DISRUPTED = ("delay", "suspend", "clos", "reduced", "not running")
GOOD = ("good service", "no issues")


@dataclass(frozen=True)
class LineStatus:
    summary: str = "Unknown"  # e.g. "Minor Delays" (several are joined with " / ")
    reason: str = ""  # TfL's text
    good: bool = True
    no_service: bool = False  # the whole line isn't running
    stretches: tuple[tuple[int, int], ...] = ()  # (first, last) station indices named in the reason
    whole_line: bool = False  # disrupted, but no particular stretch named


def parse_status(data, route: Route) -> LineStatus:
    lines = data if isinstance(data, list) else [data]
    statuses = [s for line in lines for s in line.get("lineStatuses", [])]
    if not statuses:
        return LineStatus()
    descriptions = list(dict.fromkeys(s.get("statusSeverityDescription", "") for s in statuses))
    reasons = list(dict.fromkeys(s["reason"].strip() for s in statuses if s.get("reason")))
    lowered = [d.lower() for d in descriptions]
    good = all(any(g in d for g in GOOD) for d in lowered)
    disrupted = any(any(w in d for w in DISRUPTED) for d in lowered)

    stretches = []
    for reason in reasons:
        for sentence in re.split(r"[.;]\s", reason):  # "...between A and B. GOOD SERVICE on the rest"
            named = mentioned_stations(sentence, route)
            if len(named) >= 2:
                stretches.append((min(named), max(named)))

    no_service = not stretches and any(any(w in d for w in NO_SERVICE) and "part" not in d for d in lowered)
    return LineStatus(
        summary=" / ".join(descriptions),
        reason=" ".join(reasons),
        good=good,
        no_service=no_service,
        stretches=tuple(stretches),
        whole_line=disrupted and not stretches and not no_service,
    )


@dataclass
class DelayTracker:
    """Spots trains whose predicted arrival at the same station keeps slipping.

    For each train we remember its next station and the *earliest* arrival time
    we've seen predicted for it. If a later prediction for the same station is
    ``threshold_s`` or more behind that, the train is late by the difference.
    A normal stop at a station (30-60 s) stays under the threshold.
    """

    threshold_s: float = 120.0
    _baseline: dict[str, tuple[str, float]] = field(default_factory=dict)
    late: dict[str, float] = field(default_factory=dict)  # vehicle -> seconds late

    def update(self, predictions: list[Prediction], now: float) -> dict[str, float]:
        soonest: dict[str, Prediction] = {}
        for p in predictions:
            if p.vehicle_id == "000":  # TfL's "manually routed" id can cover several trains
                continue
            if p.vehicle_id not in soonest or p.seconds < soonest[p.vehicle_id].seconds:
                soonest[p.vehicle_id] = p

        baseline, late = {}, {}
        for vehicle, p in soonest.items():
            eta = now + p.seconds
            previous = self._baseline.get(vehicle)
            if previous and previous[0] == p.station_id:
                earliest = min(previous[1], eta)
                baseline[vehicle] = (p.station_id, earliest)
                if eta - earliest >= self.threshold_s:
                    late[vehicle] = eta - earliest
            else:
                baseline[vehicle] = (p.station_id, eta)  # new train, or it moved on: start again
        self._baseline, self.late = baseline, late
        return late

    def reset(self) -> None:
        self._baseline, self.late = {}, {}
