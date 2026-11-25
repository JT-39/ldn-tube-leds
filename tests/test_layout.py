import pytest

from tube_leds.layout import build_layout, line_pixels, snap45
from tube_leds.positions import TrainPosition
from tube_leds.tfl import load_route


@pytest.fixture(scope="module")
def bakerloo():
    return load_route("bakerloo")


def test_line_pixels_are_connected():
    pts = line_pixels((0, 0), (7, 3))
    assert pts[0] == (0, 0) and pts[-1] == (7, 3)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert max(abs(x1 - x0), abs(y1 - y0)) == 1


def test_snap45():
    assert snap45(10, 0.5) == (1, 0)
    assert snap45(1, 1.1) == (1, 1)
    assert snap45(-0.2, -3) == (0, -1)


@pytest.mark.parametrize("size", [(64, 32), (128, 64)])
def test_schematic_fits_and_has_no_collisions(bakerloo, size):
    w, h = size
    layout = build_layout(bakerloo, w, h, "schematic")
    assert layout.collisions(bakerloo) == []
    for x, y in layout.station_px:
        assert 0 <= x < w and 0 <= y < h


def test_geo_layout_collides_on_a_small_panel(bakerloo):
    # The reason tube maps are schematic.
    layout = build_layout(bakerloo, 64, 32, "geo", tracks=1)
    assert ("Charing Cross", "Embankment") in layout.collisions(bakerloo)


def test_single_track_trains_share_the_line(bakerloo):
    layout = build_layout(bakerloo, 128, 64, tracks=1)
    a, b = layout.station_px[0], layout.station_px[1]
    assert layout.train_pixels(TrainPosition("x", 0, 1, 0.0, 0, ""))[0] == a
    assert layout.train_pixels(TrainPosition("x", 1, 0, 0.0, 0, ""))[0] == b
    head, tail = layout.train_pixels(TrainPosition("x", 0, 1, 1.0, 0, ""))
    assert head == b and tail is not None


@pytest.mark.parametrize("size", [(64, 32), (64, 64), (128, 64)])
def test_two_tracks_never_touch(bakerloo, size):
    layout = build_layout(bakerloo, *size, tracks=2)
    fwd = {p for seg in layout.forward for p in seg}
    bwd = {p for seg in layout.backward for p in seg}
    assert not fwd & bwd
    for x, y in fwd | bwd:
        assert 0 <= x < size[0] and 0 <= y < size[1]


def test_opposite_trains_use_different_tracks(bakerloo):
    layout = build_layout(bakerloo, 128, 64)
    going = layout.train_pixels(TrainPosition("a", 10, 11, 0.5, 30, ""))[0]
    coming = layout.train_pixels(TrainPosition("b", 11, 10, 0.5, 30, ""))[0]
    assert going in layout.forward[10] and coming in layout.backward[10]
    assert going != coming


def test_trains_keep_left(bakerloo):
    # Harrow & Wealdstone is top-left, Elephant & Castle bottom-right, so a train
    # heading for Elephant & Castle (south-east) keeps left = runs on the upper track.
    layout = build_layout(bakerloo, 128, 64)
    assert layout.describe_tracks(bakerloo) == {"upper": "Elephant & Castle", "lower": "Harrow & Wealdstone"}


def test_waiting_train_sits_on_its_departure_track(bakerloo):
    layout = build_layout(bakerloo, 128, 64)
    head, _ = layout.train_pixels(TrainPosition("w", 0, 0, 1.0, 60, ""))
    assert head == layout.forward[0][0]
    last = len(bakerloo) - 1
    head, _ = layout.train_pixels(TrainPosition("w", last, last, 1.0, 60, ""))
    assert head == layout.backward[-1][-1]
