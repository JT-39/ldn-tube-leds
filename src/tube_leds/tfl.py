"""Getting data out of TfL's Unified API (https://api.tfl.gov.uk).

We need two kinds of data, and they behave very differently:

* The **route**: which stations a line serves, in order, with coordinates.
  It changes maybe once a year, so we fetch it once and cache it as JSON in
  ``data/routes/<line>.json``.
* **Arrivals**: live predictions like "train 236 reaches Paddington in 95 s".
  These change all the time, so we poll them every ~30 seconds.

An API key is optional. Without one, requests are rate limited more tightly.
Get a free key at https://api-portal.tfl.gov.uk and put it in the environment
variable ``TFL_APP_KEY``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path

import httpx

API = "https://api.tfl.gov.uk"
DATA_DIR = Path(os.environ.get("TUBE_LEDS_DATA", "data"))


# --------------------------------------------------------------------------
# The static part: stations and routes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Station:
    id: str  # NaPTAN id, e.g. "940GZZLUOXC" for Oxford Circus
    name: str
    lat: float
    lon: float

    def distance_m(self, other: Station) -> float:
        """Straight-line distance in metres (haversine)."""
        r = 6_371_000
        p1, p2 = math.radians(self.lat), math.radians(other.lat)
        dp = p2 - p1
        dl = math.radians(other.lon - self.lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Route:
    """An ordered list of stations along one line (no branches yet: that's step 2)."""

    line_id: str
    stations: tuple[Station, ...]

    @cached_property
    def index(self) -> dict[str, int]:
        """NaPTAN id -> position along the route. Used to join arrivals onto the route."""
        return {s.id: i for i, s in enumerate(self.stations)}

    def __len__(self) -> int:
        return len(self.stations)


def clean_name(name: str) -> str:
    for suffix in (" Underground Station", " Station"):
        name = name.removesuffix(suffix)
    return name


def route_path(line_id: str) -> Path:
    return DATA_DIR / "routes" / f"{line_id}.json"


def save_route(route: Route) -> Path:
    path = route_path(route.line_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"line_id": route.line_id, "stations": [asdict(s) for s in route.stations]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_route(line_id: str) -> Route:
    path = route_path(line_id)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached route for '{line_id}'. Run `uv run tube-leds route --line {line_id}` first."
        )
    data = json.loads(path.read_text())
    return Route(line_id, tuple(Station(**s) for s in data["stations"]))


# --------------------------------------------------------------------------
# Talking to the API
# --------------------------------------------------------------------------


def make_client() -> httpx.Client:
    params = {}
    if key := os.environ.get("TFL_APP_KEY"):
        params["app_key"] = key
    return httpx.Client(base_url=API, params=params, timeout=15)


def _get(client: httpx.Client, path: str):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def fetch_route(client: httpx.Client, line_id: str) -> Route:
    """Download the station sequence for a line.

    The response has ``orderedLineRoutes`` (lists of NaPTAN ids, one per service
    pattern) and ``stations`` / ``stopPointSequences`` (which carry lat/lon).
    We take the longest service pattern, which for a simple line like the
    Bakerloo is the whole thing end to end.
    """
    data = _get(client, f"/Line/{line_id}/Route/Sequence/outbound")

    coords: dict[str, dict] = {}
    for stop in data.get("stations", []):
        coords[stop["id"]] = stop
    for seq in data.get("stopPointSequences", []):
        for stop in seq.get("stopPoint", []):
            coords.setdefault(stop["id"], stop)
            if stop.get("stationId"):
                coords.setdefault(stop["stationId"], stop)

    patterns = [r["naptanIds"] for r in data.get("orderedLineRoutes", []) if r.get("naptanIds")]
    if not patterns:
        raise RuntimeError(f"TfL returned no route sequence for '{line_id}'")
    longest = max(patterns, key=len)

    covered = set(longest)
    extra = {n for p in patterns for n in p} - covered
    if extra:
        print(
            f"note: '{line_id}' has branches; step 1 only draws the longest route "
            f"({len(extra)} stations left out)"
        )

    stations = tuple(
        Station(
            id=n,
            name=clean_name(coords[n]["name"]),
            lat=float(coords[n]["lat"]),
            lon=float(coords[n]["lon"]),
        )
        for n in longest
        if n in coords
    )
    return Route(line_id, stations)


def fetch_arrivals(client: httpx.Client, line_id: str) -> list[dict]:
    """Every live arrival prediction on a line, as raw TfL dicts.

    Each one looks roughly like:
        {"vehicleId": "236", "naptanId": "940GZZLUPAH", "timeToStation": 95,
         "destinationNaptanId": "940GZZLUEAC", "direction": "inbound",
         "currentLocation": "Between Warwick Avenue and Paddington", ...}
    One train appears many times: once for every station it will reach soon.
    """
    return _get(client, f"/Line/{line_id}/Arrivals")
