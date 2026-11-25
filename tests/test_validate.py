import pytest

from tube_leds.positions import TrainPosition
from tube_leds.tfl import load_route
from tube_leds.validate import check_positions, mentioned_stations, normalise, summarise


@pytest.fixture(scope="module")
def route():
    return load_route("bakerloo")


def idx(route, name):
    return [s.name for s in route.stations].index(name)


def test_normalise():
    assert normalise("Elephant & Castle") == "elephant and castle"
    assert normalise("Queen's Park") == normalise("Queens Park") == "queens park"
    assert normalise("Edgware Road (Bakerloo)") == "edgware road"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Between Paddington and Edgware Road", ["Paddington", "Edgware Road (Bakerloo)"]),
        ("At Oxford Circus Platform 3", ["Oxford Circus"]),
        ("Approaching Elephant and Castle", ["Elephant & Castle"]),
        ("Left South Kenton", ["South Kenton"]),  # not also "Kenton"
        ("Between Kenton and South Kenton", ["Kenton", "South Kenton"]),
        ("At Queens Park", ["Queen's Park"]),
        ("At Platform", []),
        ("", []),
    ],
)
def test_mentioned_stations(route, text, expected):
    assert [route.stations[i].name for i in mentioned_stations(text, route)] == sorted(
        expected, key=lambda n: idx(route, n)
    )


def test_verdicts(route):
    pad, erb, mvl = idx(route, "Paddington"), idx(route, "Edgware Road (Bakerloo)"), idx(route, "Marylebone")
    positions = [
        TrainPosition("a", pad, erb, 0.5, 30, "Between Paddington and Edgware Road"),  # same segment
        TrainPosition("b", pad, erb, 0.9, 5, "Approaching Edgware Road"),  # consistent
        TrainPosition("c", erb, mvl, 0.1, 80, "Between Paddington and Edgware Road"),  # a stop ahead of TfL
        TrainPosition("d", pad, erb, 0.5, 30, "At Platform"),  # nothing to compare
    ]
    verdicts = [c.verdict for c in check_positions(positions, route)]
    assert verdicts == ["agree", "agree", "disagree", "unknown"]
    assert summarise(check_positions(positions, route)) == {
        "agree": 2, "disagree": 1, "unknown": 1, "checked": 3, "rate": pytest.approx(2 / 3)
    }
