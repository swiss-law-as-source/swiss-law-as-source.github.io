#!/usr/bin/env python3
"""Reproduce the intercantonal-agreements data from LexFind — minimal, readable.

Standard library only. Run it anywhere:

    python3 reproduce_concordats.py                 # full run (~340 requests, ~2 min)
    python3 reproduce_concordats.py --limit 20      # quick demo (<1 min)
    python3 reproduce_concordats.py --lang fr

What it does, in three API calls (the same endpoints the Swiss Law Collection
pipeline uses — see https://swiss-law-as-source.github.io/verification.html):

  1. GET https://www.lexfind.ch/api/fe/{lang}/entities
         -> find the entity with abbreviation "intlex": LexFind's systematic
            collection of intercantonal law (maintained by the ch Foundation).
  2. GET https://www.lexfind.ch/api/fe/{lang}/entities/{id}/systematics
         -> the collection's category tree; its leaf-node ids are then queried
            in batches of 20 with ?active_only=false&tols_for_systematics[]=…
            to list EVERY intercantonal text (id, number, title, active flag).
  3. GET https://www.lexfind.ch/api/frontend/v1/{lang}/texts-of-law/{id}/with-version-groups
         -> per text, the earliest family_active_since = the year the
            agreement (its act family) first existed.

Signatories: LexFind publishes NO official member-canton list per text, so the
best per-text evidence is the cantons NAMED IN THE TITLE (exact for bilateral
treaties "zwischen den Kantonen X und Y"; empty for open multilateral
concordats). The full Swiss Law Collection statistic goes further: it also
imports all 26 cantonal collections and counts a canton as signatory when its
own collection publishes the text — see
https://github.com/benjamin-arfa/swiss-law (src/legalize_ch/lexfind_backfill.py
and stats.py: one occurrence per signing canton per agreement).

Output: intercantonal_agreements.csv + a summary on stdout.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.request

FE_API = "https://www.lexfind.ch/api/fe"
FRONTEND_API = "https://www.lexfind.ch/api/frontend/v1"
USER_AGENT = "concordats-reproduction/1.0 (open-data verification script)"

# Canton names as they appear in treaty titles (German + French variants).
CANTON_NAMES = {
    "ZH": ["Zürich", "Zurich"], "BE": ["Bern", "Berne"], "LU": ["Luzern", "Lucerne"],
    "UR": ["Uri"], "SZ": ["Schwyz", "Schwytz"], "OW": ["Obwalden", "Obwald"],
    "NW": ["Nidwalden", "Nidwald"], "GL": ["Glarus", "Glaris"], "ZG": ["Zug", "Zoug"],
    "FR": ["Freiburg", "Fribourg"], "SO": ["Solothurn", "Soleure"],
    "BS": ["Basel-Stadt", "Bâle-Ville"], "BL": ["Basel-Landschaft", "Bâle-Campagne"],
    "SH": ["Schaffhausen", "Schaffhouse"], "AR": ["Appenzell Ausserrhoden"],
    "AI": ["Appenzell Innerrhoden"], "SG": ["St.Gallen", "St. Gallen", "Saint-Gall"],
    "GR": ["Graubünden", "Grisons"], "AG": ["Aargau", "Argovie"],
    "TG": ["Thurgau", "Thurgovie"], "TI": ["Tessin", "Ticino"],
    "VD": ["Waadt", "Vaud"], "VS": ["Wallis", "Valais"],
    "NE": ["Neuenburg", "Neuchâtel"], "GE": ["Genf", "Genève"], "JU": ["Jura"],
}

_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")  # DD.MM.YYYY


def get_json(url: str, rate: float) -> dict | list:
    time.sleep(rate)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def cantons_in_title(title: str) -> list[str]:
    return sorted(code for code, names in CANTON_NAMES.items()
                  if any(name in title for name in names))


def earliest_family_year(payload: dict) -> str:
    """Smallest family_active_since year across all version groups.

    The payload nests families -> groups -> versions; each version dict
    carries family_active_since (DD.MM.YYYY). Walked recursively so minor
    shape changes don't break the script.
    """
    years = []

    def walk(node):
        if isinstance(node, dict):
            m = _DATE.match(str(node.get("family_active_since") or ""))
            if m:
                years.append(m.group(3))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload.get("families"))
    return min(years) if years else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", default="de", choices=["de", "fr", "it"])
    ap.add_argument("--rate", type=float, default=0.2,
                    help="seconds between requests (be polite)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N texts (0 = all)")
    args = ap.parse_args()

    # 1. Resolve the Intlex entity (the intercantonal-law collection)
    entities = get_json(f"{FE_API}/{args.lang}/entities", args.rate)
    intlex = next(e for e in entities
                  if str(e.get("abbreviation", "")).lower() == "intlex")
    print(f"Intlex entity id: {intlex['id']} ({intlex.get('name', '')})")

    # 2. Its systematics tree -> leaf ids -> all texts, 20 leaves per request.
    #    The response is a dict keyed by node id (plus a virtual "" root);
    #    each node has identifier/title/parent/children/tols.
    tree_url = f"{FE_API}/{args.lang}/entities/{intlex['id']}/systematics"
    tree = get_json(tree_url, args.rate)
    nodes = {k: v for k, v in tree.items() if k}          # drop the "" root
    leaves = [k for k, n in nodes.items() if not n.get("children")]
    print(f"systematics: {len(nodes)} nodes, {len(leaves)} leaves")

    texts: dict[int, dict] = {}
    for i in range(0, len(leaves), 20):
        batch = leaves[i:i + 20]
        params = "&".join(f"tols_for_systematics[]={lid}" for lid in batch)
        filled = get_json(f"{tree_url}?active_only=false&{params}", args.rate)
        for key, node in filled.items():
            if key:
                for tol in node.get("tols") or []:
                    texts[tol["id"]] = tol
    print(f"intercantonal texts found: {len(texts)}")

    # 3. Per text: earliest family date = the agreement's year
    rows = []
    todo = sorted(texts.values(), key=lambda t: str(t.get("systematic_number")))
    if args.limit:
        todo = todo[:args.limit]
    for i, tol in enumerate(todo, 1):
        payload = get_json(
            f"{FRONTEND_API}/{args.lang}/texts-of-law/{tol['id']}/with-version-groups",
            args.rate)
        title = str(tol.get("title", ""))
        signers = cantons_in_title(title)
        rows.append({
            "tol_id": tol["id"],
            "systematic_number": tol.get("systematic_number", ""),
            "title": title,
            "year": earliest_family_year(payload),
            "active": tol.get("is_active", ""),
            "signatories_named_in_title": "|".join(signers),
            "n_named": len(signers),
        })
        if i % 25 == 0 or i == len(todo):
            print(f"  dates fetched: {i}/{len(todo)}")

    out = "intercantonal_agreements.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    dated = [r for r in rows if r["year"]]
    named = [r for r in rows if r["n_named"] >= 2]
    print(f"\nwrote {out}: {len(rows)} agreements, {len(dated)} dated, "
          f"{len(named)} with >=2 cantons named in the title "
          f"({sum(r['n_named'] for r in rows)} named memberships in total)")
    by_decade: dict[str, int] = {}
    for r in dated:
        by_decade[r["year"][:3] + "0s"] = by_decade.get(r["year"][:3] + "0s", 0) + 1
    print("per decade:", ", ".join(f"{k}: {v}" for k, v in sorted(by_decade.items())))
    print("\nNote: titles name their parties mainly for bilateral treaties; open "
          "multilateral concordats list no cantons in the title. The full "
          "statistic on swiss-law-as-source.github.io additionally counts a "
          "canton as signatory when its own collection publishes the text.")


if __name__ == "__main__":
    main()
