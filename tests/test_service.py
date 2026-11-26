import pytest

from tube_leds.positions import Prediction
from tube_leds.service import DelayTracker, parse_status
from tube_leds.tfl import load_route


@pytest.fixture(scope="module")
def route():
    return load_route("bakerloo")


def status(description, reason=""):
    line_status = {"statusSeverityDescription": description}
    if reason:
        line_status["reason"] = reason
    return [{"id": "bakerloo", "lineStatuses": [line_status]}]


def name(route, i):
    return route.stations[i].name


def test_good_service(route):
    s = parse_status(status("Good Service"), route)
    assert s.good and not s.no_service and not s.stretches and not s.whole_line


def test_delays_on_a_named_stretch(route):
    s = parse_status(status(
        "Minor Delays",
        "Bakerloo Line: Minor delays between Queen's Park and Harrow & Wealdstone due to an earlier faulty train. "
        "GOOD SERVICE on the rest of the line.",
    ), route)
    assert not s.good and not s.whole_line
    [(a, b)] = s.stretches
    assert {name(route, a), name(route, b)} == {"Queen's Park", "Harrow & Wealdstone"}


def test_delays_without_a_stretch_cover_the_whole_line(route):
    s = parse_status(status("Severe Delays", "Severe delays due to a signal failure at Oxford Circus."), route)
    assert s.whole_line and not s.stretches


def test_strike_means_no_service(route):
    s = parse_status(status("Suspended", "Bakerloo Line: No service due to strike action."), route)
    assert s.no_service and not s.whole_line


def test_part_suspended_is_a_stretch_not_the_whole_line(route):
    s = parse_status(status("Part Suspended", "No service between Queen's Park and Harrow & Wealdstone."), route)
    assert s.stretches and not s.no_service


def pred(vehicle, station, seconds):
    return Prediction(vehicle, station, seconds, None, "", "")


def test_delay_tracker_spots_a_slipping_train():
    t = DelayTracker(threshold_s=120)
    assert t.update([pred("1", "A", 100)], now=0) == {}  # due at t=100
    assert t.update([pred("1", "A", 80)], now=60) == {}  # due at t=140: 40 s behind, fine
    late = t.update([pred("1", "A", 150)], now=120)  # due at t=270: 170 s behind
    assert late == {"1": pytest.approx(170)}


def test_delay_tracker_resets_when_the_train_moves_on():
    t = DelayTracker(threshold_s=120)
    t.update([pred("1", "A", 100)], now=0)
    t.update([pred("1", "A", 300)], now=60)  # late...
    assert t.update([pred("1", "B", 90)], now=400) == {}  # ...but now heading for the next station


def test_delay_tracker_ignores_shared_000_ids():
    t = DelayTracker(threshold_s=1)
    t.update([pred("000", "A", 10)], now=0)
    assert t.update([pred("000", "A", 500)], now=10) == {}
