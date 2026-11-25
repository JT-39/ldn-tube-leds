"""Turning arrival predictions into train positions. This is the modelling step.

Trains don't report GPS positions. TfL only tells us things like "train 236
reaches Paddington in 95 s". We turn that into a position in three steps:

1. **Next stop.** Group predictions by train (``vehicleId``). The prediction
   with the smallest ``timeToStation`` is the station it reaches next.
2. **Direction.** Work out which way it's heading: from its destination if
   that's on our route, otherwise from the order of its later stops. If neither
   works, use TfL's "inbound"/"outbound" label, whose meaning we *learn* from
   the trains we're already sure about.
3. **Progress.** The previous station is one step behind. Compare "seconds to
   next stop" with how long the trip between the two stations usually takes:

       progress = 1 - seconds_to_next / run_time   (clipped to 0..1)

   0 means still at the previous station, 1 means arriving.

``run_time`` is a tiny model with two parameters: a fixed overhead (pulling
away and braking) plus distance divided by a cruising speed. The defaults are
guesses. Fitting them to real data is a nice exercise for later (see README).

Between polls we use **dead reckoning**: if 10 s have passed since the data
arrived, every train is 10 s closer to its next stop. That's what makes the
dots glide smoothly instead of jumping every 30 s.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .tfl import Route, Station, align_to_route

RunTime = Callable[[Station, Station], float]


def default_run_time(a: Station, b: Station, overhead_s: float = 30.0, speed_mps: float = 15.0) -> float:
    """Guess how many seconds a train takes from station a to station b."""
    return overhead_s + a.distance_m(b) / speed_mps


@dataclass(frozen=True)
class Prediction:
    """One row of TfL arrivals data, reduced to the fields we use."""

    vehicle_id: str
    station_id: str
    seconds: float
    destination_id: str | None
    direction_label: str  # TfL's "inbound" / "outbound" (meaning varies by line)
    current_location: str  # TfL's own words, e.g. "Between Paddington and Edgware Road"


@dataclass(frozen=True)
class TrainPosition:
    vehicle_id: str
    from_idx: int  # index into route.stations
    to_idx: int
    progress: float  # 0 = at from station, 1 = at to station
    seconds_to_next: float
    current_location: str

    @property
    def direction(self) -> int:
        return (self.to_idx > self.from_idx) - (self.to_idx < self.from_idx)


def parse_predictions(raw: Iterable[dict], line_id: str | None = None, route: Route | None = None) -> list[Prediction]:
    """TfL rows -> Predictions. Rows without a train id are dropped (we can't follow them).

    Pass ``route`` to first match stations by name where TfL's ids differ from ours.
    """
    if route is not None:
        raw = align_to_route(list(raw), route)
    out = []
    for p in raw:
        vehicle = (p.get("vehicleId") or "").strip()
        if not vehicle or "naptanId" not in p or "timeToStation" not in p:
            continue
        if line_id and p.get("lineId") and p["lineId"] != line_id:
            continue
        out.append(
            Prediction(
                vehicle_id=vehicle,
                station_id=p["naptanId"],
                seconds=float(p["timeToStation"]),
                destination_id=p.get("destinationNaptanId") or None,
                direction_label=(p.get("direction") or "").lower(),
                current_location=p.get("currentLocation") or "",
            )
        )
    return out


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def _direction_from_stops(preds: list[Prediction], index: dict[str, int]) -> int | None:
    """+1 = towards the end of the route, -1 = towards the start, None = can't tell."""
    to_idx = index[preds[0].station_id]
    dest = preds[0].destination_id
    if dest in index and index[dest] != to_idx:
        return _sign(index[dest] - to_idx)
    for p in preds[1:]:  # later stops reveal the direction
        if index[p.station_id] != to_idx:
            return _sign(index[p.station_id] - to_idx)
    return None


def estimate_positions(
    predictions: Iterable[Prediction],
    route: Route,
    elapsed_s: float = 0.0,
    run_time: RunTime = default_run_time,
) -> list[TrainPosition]:
    """Where is every train right now?

    ``elapsed_s`` is how long ago the predictions were fetched (dead reckoning).
    """
    index, n = route.index, len(route)

    # 1. Group by train, keeping only stations on our route, soonest first.
    trains: dict[str, list[Prediction]] = defaultdict(list)
    for p in predictions:
        if p.station_id in index:
            trains[p.vehicle_id].append(p)
    for preds in trains.values():
        preds.sort(key=lambda p: p.seconds)

    # 2a. Direction for the trains where the stops make it obvious.
    directions = {v: _direction_from_stops(preds, index) for v, preds in trains.items()}

    # 2b. Learn what "inbound"/"outbound" means on this line from those trains,
    #     then use it for the ambiguous ones. (A majority vote: tiny supervised learning!)
    votes = Counter(
        (preds[0].direction_label, directions[v])
        for v, preds in trains.items()
        if directions[v] is not None and preds[0].direction_label
    )
    label_meaning = {}
    for label in {label for label, _ in votes}:
        label_meaning[label] = 1 if votes[(label, 1)] >= votes[(label, -1)] else -1

    positions = []
    for vehicle, preds in trains.items():
        nxt = preds[0]
        to_idx = index[nxt.station_id]
        d = directions[vehicle] or label_meaning.get(nxt.direction_label)
        if d is None:  # last resort: a train heading into either end of the line
            d = -1 if to_idx == 0 else 1 if to_idx == n - 1 else None
        if d is None:
            continue

        seconds = max(nxt.seconds - elapsed_s, 0.0)
        from_idx = to_idx - d
        if not 0 <= from_idx < n:
            # Its next stop is the start of the line: it's waiting there to depart.
            positions.append(TrainPosition(vehicle, to_idx, to_idx, 1.0, seconds, nxt.current_location))
            continue

        total = run_time(route.stations[from_idx], route.stations[to_idx])
        progress = min(max(1.0 - seconds / total, 0.0), 1.0)
        positions.append(TrainPosition(vehicle, from_idx, to_idx, progress, seconds, nxt.current_location))

    return positions
