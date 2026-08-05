#!/usr/bin/env python3
"""Reproduce the intercantonal-agreements data from LexFind — minimal, readable.

Standard library only. Run it anywhere:

    python3 reproduce_concordats.py                 # full run (~340 requests, ~2 min)
    python3 reproduce_concordats.py --limit 20      # quick demo (<1 min)
    python3 reproduce_concordats.py --lang fr
    python3 reproduce_concordats.py --verify        # only check the published
                                                    # numbers against BADAC G1
    python3 reproduce_concordats.py --repo ../swiss-law   # recompute the published
                                                          # statistic from the corpus

Every mode prints the same G1 table — the 1848-2003 reference of IDHEAP/BADAC
(communiqué CP4, 15.11.2004), which states BOTH units this data answers:

    "Total 1848-2003 = 733 concordats; 2564 cantons membres"
    "estimation BADAC (résultats pondérés : 2564 = 100%)"

so its published shares (44% bilateral, 22% with >=20 cantons, …) are shares of
the 2564 MEMBERSHIPS, not of the 733 concordats — read against 733 they are
arithmetically impossible, the >=20-canton class alone exceeding 3200
memberships. What the three modes differ in is the evidence they can see:

    default   cantons NAMED IN THE TITLE only -> a strict lower bound
    --repo    the full statistic: published copies U titles U preamble parties,
              i.e. the exact numbers published on the site (same code path)
    --verify  no computation, just the published JSON vs the G1 baseline

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
import sys
import time
import urllib.request
from pathlib import Path

FE_API = "https://www.lexfind.ch/api/fe"
FRONTEND_API = "https://www.lexfind.ch/api/frontend/v1"
SITE = "https://swiss-law-as-source.github.io"
PUBLISHED_G1 = f"{SITE}/api/v1/stats/concordats_size_distribution.json"
USER_AGENT = "concordats-reproduction/1.0 (open-data verification script)"

# ── The 2003 baseline: IDHEAP/BADAC press release CP4 (2004), graph G1 ──────
# Four values are read off the publication and nothing else: the two caption
# totals, the footnote count of all-canton conventions, and the five legend
# shares (shares of the MEMBERSHIP total, not of the concordat count).  G1
# publishes no per-band concordat counts; this script implies them by dividing
# each band's memberships by the mean concordat size OBSERVED in that band by
# the run itself, falling back to the band's midpoint only where the run
# evidences nothing.  No representative size is hand-picked.
BADAC_TOTAL_CONCORDATS = 733
BADAC_TOTAL_MEMBERSHIPS = 2564
BADAC_ALL_CANTON_CONVENTIONS = 12          # "guère plus d'une dizaine"
BADAC_BANDS = {                            # band -> share of memberships
    "2": 0.44,
    "3-4": 0.08,
    "5-10": 0.20,
    "11-19": 0.06,
    "20-26": 0.22,
}
BAND_RANGES = [("2", 2, 2), ("3-4", 3, 4), ("5-10", 5, 10),
               ("11-19", 11, 19), ("20-26", 20, 26)]

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


def bands_from_sizes(sizes: list[int]) -> list[dict]:
    """Bucket signatory-set sizes into the five G1 bands.

    Only agreements with at least TWO evidenced parties are counted: a
    concordat is by definition an agreement between cantons, so a record
    showing a single canton is a membership we failed to resolve, not a
    one-canton concordat.
    """
    known = [n for n in sizes if n >= 2]
    total_m = sum(known)
    out = []
    for label, lo, hi in BAND_RANGES:
        sel = [n for n in known if lo <= n <= hi]
        memberships = sum(sel)
        share = BADAC_BANDS[label]
        # Representative size: observed within-band mean, else band midpoint.
        size = memberships / len(sel) if sel else (lo + hi) / 2
        basis = "observed_mean" if sel else "band_midpoint"
        badac_memberships = round(share * BADAC_TOTAL_MEMBERSHIPS)
        out.append({
            "band": label,
            "ours_concordats": len(sel),
            "ours_memberships": memberships,
            "ours_share_of_memberships": (memberships / total_m) if total_m else 0.0,
            "ours_mean_size": size if sel else None,
            "badac_memberships": badac_memberships,
            "badac_share_of_memberships": share,
            "badac_concordats_implied": round(badac_memberships / size),
            "badac_representative_size": round(size, 3),
            "badac_representative_size_basis": basis,
        })
    return out


def print_g1(bands: list[dict], ours: dict, label: str) -> None:
    """Print the G1 comparison — the same table the verification page renders."""
    print(f"\n{'=' * 78}\nCantons per concordat, 1848-2003 — BADAC graph G1 vs {label}\n{'=' * 78}")
    print(f"{'band':>7} | {'ours conc.':>10} {'ours memb.':>10} {'share':>7} "
          f"| {'BADAC memb.':>11} {'share':>7} {'BADAC conc.':>11}")
    print("-" * 78)
    for b in bands:
        print(f"{b['band']:>7} | {b['ours_concordats']:>10} {b['ours_memberships']:>10} "
              f"{100 * b['ours_share_of_memberships']:>6.1f}% "
              f"| {b['badac_memberships']:>11} {100 * b['badac_share_of_memberships']:>6.1f}% "
              f"{b['badac_concordats_implied']:>11}")
    print("-" * 78)
    tot = lambda k: sum(b[k] for b in bands)  # noqa: E731
    print(f"{'total':>7} | {tot('ours_concordats'):>10} {tot('ours_memberships'):>10} "
          f"{100.0:>6.1f}% | {tot('badac_memberships'):>11} {100.0:>6.1f}% "
          f"{tot('badac_concordats_implied'):>11}")
    print(f"\n  concordats  (>=2 evidenced parties) : {ours['concordats']:>6}"
          f"   BADAC caption: {BADAC_TOTAL_CONCORDATS}")
    print(f"  canton memberships (total signatures): {ours['memberships']:>6}"
          f"   BADAC caption: {BADAC_TOTAL_MEMBERSHIPS}"
          f"   ({100 * ours['memberships'] / BADAC_TOTAL_MEMBERSHIPS:.1f}%)")
    print(f"  mean cantons per concordat           : {ours['mean_signatories']:>6.2f}"
          f"   BADAC: {BADAC_TOTAL_MEMBERSHIPS / BADAC_TOTAL_CONCORDATS:.2f}")
    if "all_canton_agreements" in ours:
        print(f"  agreements with all 26 cantons       : {ours['all_canton_agreements']:>6}"
              f"   BADAC: {BADAC_ALL_CANTON_CONVENTIONS} named in the release")
    if ours.get("unresolved_single_party"):
        print(f"  records with one evidenced canton    : {ours['unresolved_single_party']:>6}"
              f"   (unresolved memberships, not concordats)")
    implied = tot("badac_concordats_implied")
    midpoints = [b["band"] for b in bands
                 if b.get("badac_representative_size_basis") == "band_midpoint"]
    print(f"\n  The 'BADAC conc.' column is not published: G1 gives membership shares only. It is\n"
          f"  reconstructed here by dividing each band's memberships by the mean concordat size\n"
          f"  this run observes in that band — {implied} concordats against the "
          f"{BADAC_TOTAL_CONCORDATS} stated "
          f"({100 * implied / BADAC_TOTAL_CONCORDATS:.1f}%).")
    if midpoints:
        print(f"  Bands with no observation, falling back to the band midpoint: "
              f"{', '.join(midpoints)}.")


def ours_summary(sizes: list[int]) -> dict:
    known = [n for n in sizes if n >= 2]
    return {
        "concordats": len(known),
        "memberships": sum(known),
        "mean_signatories": (sum(known) / len(known)) if known else 0.0,
        "unresolved_single_party": len(sizes) - len(known),
        "all_canton_agreements": sum(1 for n in known if n == 26),
    }


def run_repo_mode(repo: Path) -> None:
    """Recompute the PUBLISHED statistic from a clone of the law repository.

    Runs the site's own code path (``legalize_ch.stats``) over the markdown
    corpus, so the numbers are identical to the published JSON by construction
    rather than by a second implementation that could drift.  Needs the repo's
    only non-stdlib dependency: ``pip install pyyaml``.
    """
    sys.path.insert(0, str(repo / "src"))
    try:
        from legalize_ch.stats import (collect_all_frontmatter,
                                       generate_concordat_size_distribution)
    except ImportError as exc:
        sys.exit(f"cannot import legalize_ch from {repo}/src ({exc}).\n"
                 f"Clone https://github.com/benjamin-arfa/swiss-law and "
                 f"'pip install pyyaml', then pass --repo <that clone>.")
    print(f"scanning {repo}/ch for law files (this takes a minute)…")
    entries = collect_all_frontmatter(repo)
    print(f"  {len(entries)} law files parsed")
    dist = generate_concordat_size_distribution(entries)
    print_g1(dist["bands"], dist["ours"], "this repository (full evidence)")
    print("\nmembership evidence:")
    for k, v in dist["membership_evidence"].items():
        print(f"  {v:>6}  {k.replace('_', ' ')}")
    for note in dist["notes"]:
        print(f"\nnote: {note}")
    compare_to_published(dist)


def compare_to_published(dist: dict | None) -> None:
    """Fetch the published statistic and report it (and any drift)."""
    try:
        payload = get_json(PUBLISHED_G1, 0)
    except Exception as exc:                                # noqa: BLE001
        print(f"\n(could not fetch {PUBLISHED_G1}: {exc})")
        return
    if not isinstance(payload, dict):
        print(f"\n(unexpected payload at {PUBLISHED_G1})")
        return
    pub: dict = payload
    if dist is None:
        print_g1(pub["bands"], pub["ours"], "the published statistic")
        print("\nmembership evidence:")
        for k, v in pub.get("membership_evidence", {}).items():
            print(f"  {v:>6}  {k.replace('_', ' ')}")
        for note in pub.get("notes", []):
            print(f"\nnote: {note}")
        return
    same = (dist["ours"]["concordats"] == pub["ours"]["concordats"]
            and dist["ours"]["memberships"] == pub["ours"]["memberships"])
    print(f"\npublished on {SITE}: {pub['ours']['concordats']} concordats / "
          f"{pub['ours']['memberships']} memberships — "
          + ("identical to this run ✓" if same else
             "DIFFERENT (the site is regenerated from a newer corpus snapshot)"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", default="de", choices=["de", "fr", "it"])
    ap.add_argument("--rate", type=float, default=0.2,
                    help="seconds between requests (be polite)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N texts (0 = all)")
    ap.add_argument("--repo", type=Path, default=None,
                    help="path to a clone of the swiss-law repository: "
                         "recompute the published statistic from the corpus")
    ap.add_argument("--verify", action="store_true",
                    help="fetch the published statistic and show it against "
                         "the BADAC G1 baseline; no fetching, no computation")
    args = ap.parse_args()

    if args.repo:
        run_repo_mode(args.repo)
        return
    if args.verify:
        compare_to_published(None)
        return

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

    # G1: cantons per concordat, and the total number of canton signatures.
    # Restricted to agreements existing <=2003, the baseline's period.
    sizes = [r["n_named"] for r in rows if r["year"] and r["year"] <= "2003"]
    print_g1(bands_from_sizes(sizes), ours_summary(sizes),
             "this run (title evidence only — a lower bound)")
    print("\nWhy this run is a lower bound: titles name their parties mainly for "
          "bilateral treaties; open multilateral concordats ('Die unterzeichnenden "
          "Kantone …') name none, which is why the 2-canton band dominates here and "
          "the 20-26 band is nearly empty. The published statistic adds two evidence "
          "tiers this script cannot see from the Intlex catalog alone: cantons whose "
          "OWN collection publishes the text, and cantons enumerated as contracting "
          "parties in the recitals. Re-run with --repo <clone of swiss-law> to compute "
          "those too, or --verify to fetch the published figures.")


if __name__ == "__main__":
    main()
