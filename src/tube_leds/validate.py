"""Is the map right? Checking our estimates against TfL's own words.

Every TfL prediction carries a ``currentLocation`` string written by TfL's
systems, e.g. "Between Paddington and Edgware Road", "At Oxford Circus",
"Approaching Waterloo", "Left Kilburn Park". We never use it to place trains
(it's free text, and often missing), which makes it a handy *check*:

* pull out the station names it mentions,
* our estimate says the train is between stations ``from`` and ``to``,
* if every station TfL mentions is one of those two, we **agree**;
  if TfL names a different station, we **disagree**;
  if TfL's text names no station on our route, it's **unknown** (not counted).

It's a coarse test (it can't tell 20% from 80% along a segment), but it
catches the mistakes that matter: wrong direction, wrong segment, a train
drawn a stop behind or ahead.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .positions import TrainPosition
from .tfl import Route


def normalise(text: str) -> str:
    """Lower-case, '&' -> 'and', drop brackets, apostrophes and punctuation."""
    text = text.lower().replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def mentioned_stations(text: str, route: Route) -> list[int]:
    """Indices of route stations named in a piece of text.

    Longest names are matched first and then blanked out, so "South Kenton"
    doesn't also count as "Kenton".
    """
    haystack = f" {normalise(text)} "
    found = []
    by_length = sorted(range(len(route)), key=lambda i: -len(normalise(route.stations[i].name)))
    for i in by_length:
        needle = f" {normalise(route.stations[i].name)} "
        if needle in haystack:
            found.append(i)
            haystack = haystack.replace(needle, " | ")
    return sorted(found)


@dataclass(frozen=True)
class Check:
    vehicle_id: str
    from_idx: int
    to_idx: int
    tfl_text: str
    verdict: str  # "agree" | "disagree" | "unknown"


def check_positions(positions: list[TrainPosition], route: Route) -> list[Check]:
    checks = []
    for p in positions:
        named = mentioned_stations(p.current_location, route)
        if not named:
            verdict = "unknown"
        elif set(named) <= {p.from_idx, p.to_idx}:
            verdict = "agree"
        else:
            verdict = "disagree"
        checks.append(Check(p.vehicle_id, p.from_idx, p.to_idx, p.current_location, verdict))
    return checks


def summarise(checks: list[Check]) -> dict:
    counts = Counter(c.verdict for c in checks)
    checked = counts["agree"] + counts["disagree"]
    return {
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "unknown": counts["unknown"],
        "checked": checked,
        "rate": counts["agree"] / checked if checked else None,
    }
