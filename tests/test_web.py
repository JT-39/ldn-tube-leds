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
    assert {"from", "to", "progress", "seconds", "towards"} <= trains[0].keys()


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


def test_static_pixels_cover_track_and_stations(board):
    flat = board.static_pixels()
    pts = set(zip(flat[::2], flat[1::2]))
    assert board.layout.track_pixels() <= pts
    assert all(p in pts for mark in board.layout.station_marks for p in mark)


def test_track_legend(board):
    assert json.loads(board.tracks()) == {"upper": "Elephant & Castle", "lower": "Harrow & Wealdstone"}


def test_stations_carry_line_counts_for_label_priority(board):
    stations = {s["name"]: s for s in json.loads(board.stations())}
    assert stations["Baker Street"]["lines"] > stations["Kenton"]["lines"] == 1


def test_station_lines_default_to_one():
    from tube_leds.tfl import Station

    assert Station("X", "Somewhere", 51.5, -0.1).lines == 1


def test_train_list_is_sorted_by_arrival(board):
    board.update_demo(now=2000.0)
    seconds = [t["seconds"] for t in json.loads(board.trains(now=2000.0))]
    assert seconds == sorted(seconds) and len(seconds) == 10


def test_paddington_found_by_name_and_rows_without_train_ids_still_count(board):
    # TfL files Paddington under two ids; and some rows may lack a vehicleId.
    arrivals = [
        {"vehicleId": "", "lineId": "bakerloo", "naptanId": "940GZZLUPAC", "timeToStation": 90,
         "stationName": "Paddington (H&C Line) Underground Station",
         "destinationName": "Elephant & Castle Underground Station"},
        {"vehicleId": "205", "lineId": "bakerloo", "naptanId": "940GZZLUPAC", "timeToStation": 200,
         "stationName": "Paddington Underground Station", "destinationNaptanId": "940GZZLUQPS"},
    ]
    board.update(json.dumps(arrivals), now=0.0)
    pad = [s["name"] for s in json.loads(board.stations())].index("Paddington")
    rows = json.loads(board.arrivals_at(pad, now=0.0))["arrivals"]
    assert [(r["destination"], r["seconds"]) for r in rows] == [("Elephant & Castle", 90), ("Queen's Park", 200)]
    assert [t["to"] for t in json.loads(board.trains(now=0.0))] == ["Paddington"]  # the train with an id is drawn


def test_arrivals_at_station_like_a_platform_board(board):
    arrivals = [
        {"vehicleId": "201", "lineId": "bakerloo", "naptanId": "940GZZLUOXC", "timeToStation": 150,
         "destinationNaptanId": "940GZZLUEAC", "currentLocation": "At Regent's Park"},
        {"vehicleId": "202", "lineId": "bakerloo", "naptanId": "940GZZLUOXC", "timeToStation": 40,
         "destinationNaptanId": "940GZZLUQPS", "currentLocation": "Between Piccadilly Circus and Oxford Circus"},
        {"vehicleId": "202", "lineId": "bakerloo", "naptanId": "940GZZLURGP", "timeToStation": 160,
         "destinationNaptanId": "940GZZLUQPS", "currentLocation": "Between Piccadilly Circus and Oxford Circus"},
    ]
    board.update(json.dumps(arrivals), now=0.0)
    oxc = [s["name"] for s in json.loads(board.stations())].index("Oxford Circus")
    board_view = json.loads(board.arrivals_at(oxc, now=10.0))
    assert board_view["station"] == "Oxford Circus"
    assert [(a["destination"], a["seconds"], a["towards"]) for a in board_view["arrivals"]] == [
        ("Queen's Park", 30, "Harrow & Wealdstone"),
        ("Elephant & Castle", 140, "Elephant & Castle"),
    ]
