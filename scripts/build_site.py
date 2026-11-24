"""Assemble the static website into _site/ (GitHub Pages publishes this folder).

    uv run python scripts/build_site.py
    python -m http.server -d _site 8000     # then open http://localhost:8000

The site is just: index.html + our Python package zipped up + the route JSON.
Pyodide (Python compiled to WebAssembly) is loaded from a CDN by the page.
"""

import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_site"


def main() -> None:
    build = os.environ.get("GITHUB_SHA", "dev")[:7]
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "routes").mkdir(parents=True)

    html = (ROOT / "web" / "index.html").read_text()
    (OUT / "index.html").write_text(html.replace("__BUILD__", build))

    for route in (ROOT / "data" / "routes").glob("*.json"):
        shutil.copy(route, OUT / "routes" / route.name)

    with zipfile.ZipFile(OUT / "tube_leds.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((ROOT / "src" / "tube_leds").glob("*.py")):
            z.write(f, f"tube_leds/{f.name}")

    (OUT / ".nojekyll").touch()  # serve files as-is, no Jekyll processing
    print(f"built {OUT} (build {build})")


if __name__ == "__main__":
    main()
