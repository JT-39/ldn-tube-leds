from tube_leds.demo import DemoFeed
from tube_leds.positions import estimate_positions, parse_predictions
from tube_leds.tfl import load_route


def test_demo_feed_round_trips_through_the_model():
    route = load_route("bakerloo")
    for t in range(0, 6000, 137):
        feed = DemoFeed(route, n_trains=8, clock=lambda: float(t))
        positions = estimate_positions(parse_predictions(feed.arrivals(), "bakerloo"), route)
        assert len(positions) == 8, f"lost a train at t={t}"
        for p in positions:
            assert 0 <= p.progress <= 1
