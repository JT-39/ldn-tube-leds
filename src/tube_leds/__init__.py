"""Live London Underground trains on an LED matrix.

The pipeline, one module per idea:

    tfl.py        get data      route (static) + arrival predictions (live)
    positions.py  model it      predictions -> where each train is right now
    layout.py     map it        stations -> pixels on the LED grid
    app.py        show it       draw a frame, loop, CLI
    demo.py       fake it       a pretend TfL feed for offline testing
"""
