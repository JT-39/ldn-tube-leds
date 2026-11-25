import json
from pathlib import Path

import pytest

from tube_leds.web import Board

ROUTE_JSON = Path("data/routes/bakerloo.json").read_text()


@pytest.fixture
def board():
    return Board(ROUTE_JSON)


def test_demo_round_trip(board):
    assert board.update_demo(now=1000.0) == 10
    lit = board.lit_pixels(now=1010.0)
    assert len(lit) % 5 == 0 and len(lit) > 0
    trains = json.loads(board.trains(now=1010.0))
    assert len(trains) == 10
    assert {"from", "to", "progress", "seconds", "tfl"} <= trains[0].keys()


def test_real_shaped_tfl_response(board):
    arrivals = [
        {
            "vehicleId": "236",
            "lineId": "bakerloo",
            "naptanId": "940GZZLUPAH",  # Paddington
            "timeToStation": 40,
            "destinationNaptanId": "940GZZLUEAC",
            "direction": "inbound",
            "currentLocation": "Between Warwick Avenue and Paddington",
        }
    ]
    assert board.update(json.dumps(arrivals), now=0.0) == 1
    [train] = json.loads(board.trains(now=0.0))
    assert (train["from"], train["to"], train["towards"]) == ("Warwick Avenue", "Paddington", "Elephant & Castle")


def test_track_toggle_and_bounds(board):
    board.update("[]", now=0.0)
    with_track = board.lit_pixels(now=0.0, show_track=True)
    without = board.lit_pixels(now=0.0, show_track=False)
    station_pixels = {p for mark in board.layout.station_marks for p in mark}
    assert len(without) == len(station_pixels) * 5  # only the station bars
    assert len(with_track) > len(without)
    x0, y0, x1, y1 = board.bounds()
    assert 0 <= x0 < x1 < 128 and 0 <= y0 < y1 < 64


def test_track_legend(board):
    assert json.loads(board.tracks()) == {"upper": "Elephant & Castle", "lower": "Harrow & Wealdstone"}
