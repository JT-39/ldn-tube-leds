import pytest

from tube_leds.positions import Prediction, estimate_positions, parse_predictions
from tube_leds.tfl import Route, Station

# A toy four-station line running west to east, each stop ~1.1 km apart.
ROUTE = Route(
    "toy",
    tuple(Station(f"S{i}", f"Stop {i}", 51.5, -0.10 + i * 0.016) for i in range(4)),
)


def run_time_100(a, b):
    return 100.0  # make the arithmetic easy to check


def pred(vehicle, station, seconds, dest=None, label=""):
    return Prediction(vehicle, station, seconds, dest, label, "")


def test_progress_from_time_to_next_stop():
    preds = [pred("1", "S2", 25, dest="S3")]
    [pos] = estimate_positions(preds, ROUTE, run_time=run_time_100)
    assert (pos.from_idx, pos.to_idx) == (1, 2)
    assert pos.progress == pytest.approx(0.75)  # 25 s left of a 100 s hop


def test_soonest_prediction_is_the_next_stop():
    preds = [pred("1", "S3", 140, dest="S3"), pred("1", "S2", 40, dest="S3")]
    [pos] = estimate_positions(preds, ROUTE, run_time=run_time_100)
    assert pos.to_idx == 2


def test_direction_from_destination():
    preds = [pred("1", "S1", 50, dest="S0")]  # heading west
    [pos] = estimate_positions(preds, ROUTE, run_time=run_time_100)
    assert (pos.from_idx, pos.to_idx, pos.direction) == (2, 1, -1)


def test_direction_from_later_stops_when_destination_unknown():
    preds = [pred("1", "S2", 30, dest="ELSEWHERE"), pred("1", "S1", 130)]
    [pos] = estimate_positions(preds, ROUTE, run_time=run_time_100)
    assert pos.direction == -1


def test_direction_label_is_learned_from_confident_trains():
    preds = [
        pred("a", "S1", 10, dest="S3", label="outbound"),  # clearly eastbound
        pred("b", "S2", 10, dest="S0", label="inbound"),  # clearly westbound
        pred("c", "S2", 10, label="outbound"),  # ambiguous on its own
    ]
    pos = {p.vehicle_id: p for p in estimate_positions(preds, ROUTE, run_time=run_time_100)}
    assert pos["c"].direction == 1


def test_dead_reckoning_moves_trains_forward():
    preds = [pred("1", "S2", 80, dest="S3")]
    before = estimate_positions(preds, ROUTE, elapsed_s=0, run_time=run_time_100)[0]
    after = estimate_positions(preds, ROUTE, elapsed_s=40, run_time=run_time_100)[0]
    assert after.progress - before.progress == pytest.approx(0.4)


def test_progress_is_clipped():
    far = estimate_positions([pred("1", "S2", 500, dest="S3")], ROUTE, run_time=run_time_100)[0]
    late = estimate_positions([pred("1", "S2", 5, dest="S3")], ROUTE, elapsed_s=60, run_time=run_time_100)[0]
    assert far.progress == 0.0 and late.progress == 1.0


def test_train_waiting_at_start_of_line_sits_on_the_station():
    [pos] = estimate_positions([pred("1", "S0", 90, dest="S3")], ROUTE, run_time=run_time_100)
    assert (pos.from_idx, pos.to_idx, pos.progress) == (0, 0, 1.0)


def test_stations_off_the_route_are_ignored():
    assert estimate_positions([pred("1", "NOPE", 10, dest="S3")], ROUTE) == []


def test_parse_predictions_reads_tfl_shape():
    raw = [
        {
            "vehicleId": "236",
            "lineId": "toy",
            "naptanId": "S1",
            "timeToStation": 95,
            "destinationNaptanId": "S3",
            "direction": "Outbound",
            "currentLocation": "Between Stop 0 and Stop 1",
        },
        {"vehicleId": "", "naptanId": "S1", "timeToStation": 5},  # no id: unusable
        {"vehicleId": "9", "lineId": "other", "naptanId": "S1", "timeToStation": 5},  # other line
    ]
    [p] = parse_predictions(raw, line_id="toy")
    assert p == Prediction("236", "S1", 95.0, "S3", "outbound", "Between Stop 0 and Stop 1")


def test_align_to_route_matches_station_names():
    from tube_leds.tfl import align_to_route, load_route, station_key

    route = load_route("bakerloo")
    assert station_key("Paddington (H&C Line) Underground Station") == "paddington"
    assert station_key("Queen's Park Underground Station") == station_key("Queens Park") == "queens park"
    [row] = align_to_route(
        [{"naptanId": "940GZZLUPAC", "stationName": "Paddington (H&C Line) Underground Station",
          "destinationName": "Harrow & Wealdstone Underground Station"}],
        route,
    )
    assert row["naptanId"] == "940GZZLUPAH" and row["destinationNaptanId"] == "940GZZLUHAW"
