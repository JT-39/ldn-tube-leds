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
    layout = build_layout(bakerloo, 64, 32, "geo")
    assert ("Charing Cross", "Embankment") in layout.collisions(bakerloo)


def test_train_pixels_follow_direction(bakerloo):
    layout = build_layout(bakerloo, 128, 64)
    a, b = layout.station_px[0], layout.station_px[1]
    east = TrainPosition("x", 0, 1, 0.0, 0, "")
    west = TrainPosition("x", 1, 0, 0.0, 0, "")
    assert layout.train_pixels(east)[0] == a
    assert layout.train_pixels(west)[0] == b
    head, tail = layout.train_pixels(TrainPosition("x", 0, 1, 1.0, 0, ""))
    assert head == b and tail is not None
