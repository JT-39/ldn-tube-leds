"""Drawing frames, the main loop, and the command line.

    uv run tube-leds run --demo      # simulated trains, no internet needed
    uv run tube-leds run             # live Bakerloo line
    uv run tube-leds peek            # print a table of where every train is
    uv run tube-leds route           # refresh station data from TfL + preview the layout
    uv run tube-leds record          # save raw snapshots for analysis later
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from . import tfl
from .demo import DemoFeed
from .layout import Layout, ascii_preview, build_layout
from .positions import TrainPosition, estimate_positions, parse_predictions
from .service import DelayTracker, parse_status

# TfL's official line colours. Brown is famously hard to show on RGB LEDs;
# tweak it until it looks right on your panel.
LINE_COLOURS = {
    "bakerloo": (179, 99, 5),
    "central": (227, 32, 23),
    "jubilee": (160, 165, 169),
    "victoria": (0, 152, 212),
    "waterloo-city": (149, 205, 186),
}
TRAIN = (255, 255, 255)  # white: a train
STATION = (255, 140, 0)  # bright orange: a station
LATE = (230, 30, 30)  # red: a train running late, or a disrupted stretch of track
DUE_HERE = (30, 80, 255)  # TfL blue, brightened (the official #0019A8 vanishes on a black board):
#                           a train due at the station you picked, within 10 minutes
NOT_IN_SERVICE = (110, 110, 110)  # grey: the line isn't running, so this train probably isn't either
TRACK_BRIGHTNESS = 0.45  # the track is the line colour, dimmed, so stations and trains stand out


def dim(colour: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in colour)


def draw_frame(
    canvas,
    layout: Layout,
    positions: list[TrainPosition],
    colour,
    show_track: bool = True,
    *,
    train_colours: dict[str, tuple[int, int, int]] | None = None,
    red_segments: set[tuple[int, int]] | None = None,
) -> None:
    """Paint one frame. ``canvas`` is anything with SetPixel(x, y, r, g, b).

    ``train_colours`` overrides the colour of particular trains (by vehicle id);
    ``red_segments`` is a set of (direction, segment index) to paint red, even
    when the rest of the track is hidden.
    """
    if show_track:
        track = dim(colour, TRACK_BRIGHTNESS)
        for x, y in layout.track_pixels():
            canvas.SetPixel(x, y, *track)
    for direction, i in red_segments or ():
        for x, y in layout.track(direction)[i]:
            canvas.SetPixel(x, y, *dim(LATE, 0.6))
    for mark in layout.station_marks:
        for x, y in mark:
            canvas.SetPixel(x, y, *STATION)
    for pos in positions:
        (x, y), _ = layout.train_pixels(pos)
        canvas.SetPixel(x, y, *(train_colours or {}).get(pos.vehicle_id, TRAIN))


def service_styles(positions, status, late, n_segments) -> tuple[dict, set]:
    """Colours for trains and track from the line status and late trains (shared by LED panel and web)."""
    colours, red = {}, set()
    if status is not None:
        if status.whole_line:
            red |= {(d, i) for d in (1, -1) for i in range(n_segments)}
        for a, b in status.stretches:
            red |= {(d, i) for d in (1, -1) for i in range(a, b)}
    for p in positions:
        if status is not None and status.no_service:
            colours[p.vehicle_id] = NOT_IN_SERVICE
        elif p.vehicle_id in late:
            colours[p.vehicle_id] = LATE
            if p.from_idx != p.to_idx:
                red.add((p.direction, min(p.from_idx, p.to_idx)))
    return colours, red


def make_matrix(width: int, height: int):
    try:
        # The real thing, on a Raspberry Pi with hzeller/rpi-rgb-led-matrix installed.
        from rgbmatrix import RGBMatrix, RGBMatrixOptions
    except ImportError:
        # Same API, but draws in your browser at http://localhost:8888
        from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = height
    options.cols = width
    # With real chained panels you'll set cols to ONE panel's width and use
    # chain_length / parallel instead. That's a hardware-step detail.
    return RGBMatrix(options=options)


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x")
    return int(w), int(h)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def get_route(line: str, refresh: bool) -> tfl.Route:
    """Use the cached route, refreshing it from TfL first if asked (and possible)."""
    if refresh:
        try:
            with tfl.make_client() as client:
                route = tfl.fetch_route(client, line)
            tfl.save_route(route)
            return route
        except Exception as exc:  # no internet, rate limited, etc.
            print(f"warning: couldn't refresh the route from TfL ({exc}); using the cached copy")
    return tfl.load_route(line)


class LiveFeed:
    def __init__(self, line: str):
        self.line = line
        self.client = tfl.make_client()

    def arrivals(self) -> list[dict]:
        return tfl.fetch_arrivals(self.client, self.line)

    def status(self) -> list[dict]:
        return tfl.fetch_status(self.client, self.line)


def cmd_run(args) -> None:
    route = get_route(args.line, refresh=not args.demo)
    width, height = parse_size(args.size)
    layout = build_layout(route, width, height, args.layout, args.tracks)
    for a, b in layout.collisions(route):
        print(f"note: {a} and {b} share a pixel with this layout")

    feed = DemoFeed(route, clock=lambda: time.time() * args.speed) if args.demo else LiveFeed(args.line)
    poll_s = args.poll / args.speed if args.demo else args.poll
    colour = LINE_COLOURS.get(args.line, (255, 255, 255))

    matrix = make_matrix(width, height)
    canvas = matrix.CreateFrameCanvas()
    print(f"{route.line_id}: {len(route)} stations on a {width}x{height} grid. Ctrl+C to stop.")
    if "RGBMatrixEmulator" in type(matrix).__module__:
        print("Emulator running: open http://localhost:8888 in your browser.")

    predictions, fetched_at = [], 0.0
    delays, status, status_at = DelayTracker(), None, 0.0
    try:
        while True:
            now = time.time()
            if not args.demo and now - status_at >= 120:  # TfL's line status, every 2 minutes
                status_at = now
                try:
                    status = parse_status(feed.status(), route)
                    print(f"{datetime.now():%H:%M:%S}  status: {status.summary}")
                except Exception as exc:
                    print(f"warning: status fetch failed ({exc})")
            if now - fetched_at >= poll_s:
                try:
                    predictions = parse_predictions(feed.arrivals(), route.line_id, route)
                    fetched_at = now
                    if not args.demo:
                        delays.update(predictions, now)
                    print(f"{datetime.now():%H:%M:%S}  {len({p.vehicle_id for p in predictions})} trains, {len(delays.late)} late")
                except Exception as exc:
                    print(f"warning: fetch failed ({exc}); retrying shortly")
                    fetched_at = now - poll_s + 5  # try again in 5 s, keep showing old data
            elapsed = (time.time() - fetched_at) * (args.speed if args.demo else 1)
            positions = estimate_positions(predictions, route, elapsed_s=elapsed)
            colours, red = service_styles(positions, status, delays.late, len(layout.forward))
            canvas.Clear()
            draw_frame(canvas, layout, positions, colour, show_track=not args.no_track, train_colours=colours, red_segments=red)
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(1 / args.fps)
    except KeyboardInterrupt:
        print("bye")


def cmd_peek(args) -> None:
    route = get_route(args.line, refresh=False)
    feed = DemoFeed(route) if args.demo else LiveFeed(args.line)
    positions = estimate_positions(parse_predictions(feed.arrivals(), route.line_id, route), route)
    names = [s.name for s in route.stations]
    print(f"{'train':<6} {'from':<24} {'  to':<26} {'progress':>8} {'next in':>8}   TfL says")
    for p in sorted(positions, key=lambda p: (p.direction, p.to_idx)):
        print(
            f"{p.vehicle_id:<6} {names[p.from_idx]:<24} → {names[p.to_idx]:<24} "
            f"{p.progress:>8.0%} {p.seconds_to_next:>7.0f}s   {p.current_location}"
        )
    print(f"\n{len(positions)} trains. Compare our estimate with TfL's own 'currentLocation' text.")


def cmd_route(args) -> None:
    route = get_route(args.line, refresh=not args.offline)
    width, height = parse_size(args.size)
    layout = build_layout(route, width, height, args.layout, args.tracks)
    print(ascii_preview(layout, route))
    if args.tracks == 2:
        print("tracks: " + ", ".join(f"{side} → {dest}" for side, dest in layout.describe_tracks(route).items()))
    for a, b in layout.collisions(route):
        print(f"! {a} and {b} share a pixel")


def cmd_record(args) -> None:
    """Append one JSON line per poll. Load later with pandas/polars to study the data."""
    out = tfl.DATA_DIR / "snapshots" / f"{args.line}-{datetime.now():%Y%m%d-%H%M}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.minutes * 60
    with tfl.make_client() as client, out.open("a") as f:
        while time.time() < end:
            try:
                rows = tfl.fetch_arrivals(client, args.line)
                stamp = datetime.now(timezone.utc).isoformat()
                f.write(json.dumps({"fetched_at": stamp, "arrivals": rows}) + "\n")
                f.flush()
                print(f"{stamp}  {len(rows)} predictions -> {out}")
            except Exception as exc:
                print(f"warning: {exc}")
            time.sleep(args.every)


def cmd_check(args) -> None:
    """How often does our estimate agree with TfL's own 'currentLocation' text?"""
    from collections import Counter

    from .validate import check_positions, summarise

    route = get_route(args.line, refresh=False)
    names = [s.name for s in route.stations]

    if args.snapshots:
        # Evaluate every snapshot recorded with `tube-leds record`, at the moment it was fetched.
        checks = []
        for path in args.snapshots:
            with open(path) as f:
                for line in f:
                    raw = json.loads(line)["arrivals"]
                    checks += check_positions(estimate_positions(parse_predictions(raw, route.line_id, route), route), route)
        source = f"{len(args.snapshots)} snapshot file(s)"
    else:
        feed = DemoFeed(route) if args.demo else LiveFeed(args.line)
        positions = estimate_positions(parse_predictions(feed.arrivals(), route.line_id, route), route)
        checks = check_positions(positions, route)
        source = "simulated feed" if args.demo else "live TfL data"

    s = summarise(checks)
    print(f"Checked against TfL's own location text ({source}):")
    if not s["checked"]:
        print("  nothing to compare (no trains, or TfL gave no station names)")
        return
    print(f"  agree {s['agree']} / {s['checked']} = {s['rate']:.0%}   (plus {s['unknown']} with no station named)")
    wrong = [c for c in checks if c.verdict == "disagree"]
    if wrong and not args.snapshots:
        print("\nDisagreements:")
        for c in wrong:
            print(f"  train {c.vehicle_id:<5} we say {names[c.from_idx]} → {names[c.to_idx]};  TfL says: {c.tfl_text}")
    if wrong and args.snapshots:
        print("\nWhere we disagree most (our segment):")
        for (a, b), n in Counter((c.from_idx, c.to_idx) for c in wrong).most_common(8):
            print(f"  {n:>4}  {names[a]} → {names[b]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tube-leds", description="Live tube trains on an LED matrix")
    sub = parser.add_subparsers(dest="command")

    def common(p):
        p.add_argument("--line", default="bakerloo", help="TfL line id (default: bakerloo)")
        return p

    run = common(sub.add_parser("run", help="show trains on the (emulated) LED matrix"))
    run.add_argument("--demo", action="store_true", help="use simulated trains instead of live data")
    run.add_argument("--size", default="128x64", help="grid size WxH (default 128x64)")
    run.add_argument("--layout", choices=["schematic", "geo"], default="schematic")
    run.add_argument("--tracks", type=int, choices=[1, 2], default=2, help="one track per direction (2) or shared (1)")
    run.add_argument("--poll", type=float, default=30, help="seconds between TfL requests")
    run.add_argument("--fps", type=float, default=20)
    run.add_argument("--speed", type=float, default=1, help="demo only: fast-forward factor")
    run.add_argument("--no-track", action="store_true", help="only light trains (like the real map)")
    run.set_defaults(func=cmd_run)

    peek = common(sub.add_parser("peek", help="print where every train is right now"))
    peek.add_argument("--demo", action="store_true")
    peek.set_defaults(func=cmd_peek)

    route = common(sub.add_parser("route", help="refresh the station list and preview the layout"))
    route.add_argument("--size", default="128x64")
    route.add_argument("--layout", choices=["schematic", "geo"], default="schematic")
    route.add_argument("--tracks", type=int, choices=[1, 2], default=2)
    route.add_argument("--offline", action="store_true", help="don't contact TfL, use the cached file")
    route.set_defaults(func=cmd_route)

    record = common(sub.add_parser("record", help="save raw arrivals snapshots to data/snapshots"))
    record.add_argument("--minutes", type=float, default=60)
    record.add_argument("--every", type=float, default=30, help="seconds between snapshots")
    record.set_defaults(func=cmd_record)

    check = common(sub.add_parser("check", help="compare our positions with TfL's own location text"))
    check.add_argument("--demo", action="store_true", help="check the simulated feed (sanity test)")
    check.add_argument("--snapshots", nargs="+", metavar="FILE", help="evaluate recorded .jsonl files instead")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)
