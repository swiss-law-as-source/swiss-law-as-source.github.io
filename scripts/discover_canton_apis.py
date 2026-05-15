#!/usr/bin/env python3
"""Capture real XHR traffic from canton-law portal SPAs to recover API contracts.

The 2026 versions of the AG/BS/LU/… LexWork apps, LexFind, and the new
ZH page route every data fetch through runtime-configured hosts that
URL probing can't recover. This script drives a headless Chromium
against each portal, performs a deterministic search, records every
request/response, and writes a per-portal JSON capture under
``data/discovery/`` for the fetcher rewire to reference.

Usage:
    .venv/bin/python scripts/discover_canton_apis.py \\
        --portal ag --portal lexfind --portal zh
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Portal definitions: how to load and exercise each SPA.
PORTALS = {
    "ag_law": {
        "url": "https://gesetzessammlungen.ag.ch/app/de/texts_of_law/301.100",
        "search_term": "",
        "ui_search": False,
    },
    "lexfind_law": {
        "url": "https://www.lexfind.ch/fe/de/tol/21072",
        "search_term": "",
        "ui_search": False,
    },
    "ag": {
        "url": "https://gesetzessammlungen.ag.ch/app/de/systematic/texts_of_law",
        "search_term": "Verfassung",
        "ui_search": True,
    },
    "bs": {
        "url": "https://www.gesetzessammlung.bs.ch/app/de/systematic/texts_of_law",
        "search_term": "Verfassung",
        "ui_search": True,
    },
    "lu": {
        "url": "https://srl.lu.ch/app/de/systematic/texts_of_law",
        "search_term": "Verfassung",
        "ui_search": True,
    },
    "be": {
        "url": "https://www.belex.sites.be.ch/app/de/systematic/texts_of_law",
        "search_term": "Verfassung",
        "ui_search": True,
    },
    "lexfind": {
        "url": "https://www.lexfind.ch/fe/de/entities/4",  # BE entity page
        "search_term": "Verfassung",
        "ui_search": False,  # entity page exercises its own loaders
    },
    "zh": {
        "url": "https://www.zh.ch/de/politik-staat/gesetze-beschluesse/gesetzessammlung.html",
        "search_term": "Verfassung",
        "ui_search": False,
    },
}


@dataclass
class RequestRecord:
    """One captured request/response pair."""
    url: str
    method: str
    status: int = 0
    content_type: str = ""
    request_body: str | None = None
    response_preview: str | None = None  # first 2 KB of body
    response_size: int = 0
    is_xhr: bool = False
    is_json: bool = False


@dataclass
class PortalCapture:
    """Everything we observed for one portal."""
    portal: str
    landing_url: str
    final_url: str = ""
    requests: list[RequestRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _capture_one(portal: str, cfg: dict, headless: bool) -> PortalCapture:
    """Drive the portal and record all network traffic."""
    capture = PortalCapture(portal=portal, landing_url=cfg["url"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            locale="de-CH",
        )
        page = ctx.new_page()

        # In-progress records keyed by request URL — we attach response data.
        pending: dict[str, RequestRecord] = {}

        def on_request(req):
            try:
                body = req.post_data
            except Exception:
                body = None
            rec = RequestRecord(
                url=req.url,
                method=req.method,
                request_body=body,
                is_xhr=req.resource_type in ("xhr", "fetch"),
            )
            pending[req.url] = rec
            capture.requests.append(rec)

        def on_response(resp):
            rec = pending.get(resp.url)
            if rec is None:
                return
            rec.status = resp.status
            ct = resp.headers.get("content-type", "")
            rec.content_type = ct
            rec.is_json = "json" in ct.lower()
            # Only keep bodies for JSON / XHR responses to keep the dump small.
            if rec.is_xhr or rec.is_json:
                try:
                    body = resp.body()
                    rec.response_size = len(body)
                    rec.response_preview = body[:2048].decode(
                        "utf-8", errors="replace",
                    )
                except Exception as e:
                    rec.response_preview = f"<error: {e}>"

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(cfg["url"], wait_until="networkidle", timeout=45000)
            capture.final_url = page.url

            if cfg.get("ui_search"):
                # Best-effort: try to fire the search; failures are logged but
                # don't abort — the page-load XHRs alone are often enough to
                # reveal catalog endpoints.
                try:
                    # Common Sitrox/LexWork search input
                    search_input = page.locator(
                        'input[type="text"], input[type="search"]'
                    ).first
                    if search_input.count():
                        search_input.fill(cfg["search_term"])
                        page.keyboard.press("Enter")
                        page.wait_for_load_state(
                            "networkidle", timeout=30000,
                        )
                except Exception as e:
                    capture.notes.append(f"ui_search failed: {e}")
        except Exception as e:
            capture.notes.append(f"goto failed: {e}")
        finally:
            ctx.close()
            browser.close()

    return capture


def _distil(capture: PortalCapture) -> dict:
    """Extract the most-likely API host + endpoint shape from a capture."""
    api_urls = [
        r for r in capture.requests
        if r.is_xhr or r.is_json
        if r.status and 200 <= r.status < 400
    ]
    # Most-common JSON-API host
    hosts: dict[str, int] = {}
    for r in api_urls:
        from urllib.parse import urlparse
        h = urlparse(r.url).netloc
        hosts[h] = hosts.get(h, 0) + 1
    top_host = max(hosts, key=lambda h: hosts[h]) if hosts else ""

    # Per-host endpoint paths (deduplicated, sorted)
    paths_by_host: dict[str, list[str]] = {}
    for r in api_urls:
        from urllib.parse import urlparse
        u = urlparse(r.url)
        paths_by_host.setdefault(u.netloc, [])
        if u.path not in paths_by_host[u.netloc]:
            paths_by_host[u.netloc].append(u.path)

    return {
        "portal": capture.portal,
        "landing_url": capture.landing_url,
        "final_url": capture.final_url,
        "top_api_host": top_host,
        "hosts_seen": hosts,
        "endpoints_by_host": paths_by_host,
        "json_request_count": len(api_urls),
        "total_request_count": len(capture.requests),
        "notes": capture.notes,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Capture XHR traffic from canton-law portals",
    )
    parser.add_argument(
        "--portal", action="append", required=True,
        choices=sorted(PORTALS.keys()),
        help="Portal key (repeatable)",
    )
    parser.add_argument(
        "--out-dir", default="data/discovery",
        help="Directory for per-portal capture JSON",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run Chromium headed (for debugging)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for portal in args.portal:
        cfg = PORTALS[portal]
        logger.info("Capturing %s (%s)…", portal, cfg["url"])
        capture = _capture_one(portal, cfg, headless=not args.headed)
        full_path = out_dir / f"{portal}.full.json"
        distil_path = out_dir / f"{portal}.json"

        full_path.write_text(json.dumps(
            {"capture": [asdict(r) for r in capture.requests],
             "portal": capture.portal,
             "landing_url": capture.landing_url,
             "final_url": capture.final_url,
             "notes": capture.notes},
            indent=2, ensure_ascii=False,
        ))
        distil_path.write_text(json.dumps(
            _distil(capture), indent=2, ensure_ascii=False,
        ))
        logger.info(
            "  → wrote %s (%d requests, %d JSON/XHR with 2xx-3xx)",
            distil_path,
            len(capture.requests),
            sum(1 for r in capture.requests
                if (r.is_xhr or r.is_json) and 200 <= (r.status or 0) < 400),
        )


if __name__ == "__main__":
    main()
