"""Render the static site by inlining validated tournament data into the template.

We inline the JSON (rather than fetch() it at runtime) so the output is a single
self-contained file that works on file:// (local preview / xdg-open) AND on plain
static hosting, with zero CORS or extra-request concerns. The data is validated
through models.load first, so the published page can never carry a malformed record.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import models

PLACEHOLDER = "__DATA__"


def build_site(
    data: str = "data/tournaments.json",
    template: str = "site/template.html",
    out: str = "dist/index.html",
) -> tuple[int, Path]:
    ts = models.load(data)  # validates; raises ValidationError on a bad record
    payload = json.dumps([t.to_dict() for t in ts], ensure_ascii=False, separators=(",", ":"))
    # never let data break out of the <script> element
    payload = payload.replace("</", "<\\/")

    html = Path(template).read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"template {template} is missing the {PLACEHOLDER} placeholder")
    html = html.replace(PLACEHOLDER, payload)

    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(html, encoding="utf-8")
    return len(ts), outp


def main() -> None:
    n, outp = build_site()
    print(f"wrote {outp} ({n} tournaments)")


if __name__ == "__main__":
    main()
