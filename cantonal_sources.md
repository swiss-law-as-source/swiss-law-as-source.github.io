# Cantonal Law Sources — Research & API Endpoints

Research document for task 7.1: Identify data sources, APIs, and endpoints
for all 26 Swiss cantons.

## Overview

Swiss cantonal law is published through two main aggregation platforms plus
individual cantonal portals:

| Platform | Type | Cantons | URL |
|----------|------|---------|-----|
| **LexWork** | JSON API | 14 | Per-canton portals (see below) |
| **LexFind** | Search portal | All 26 | https://www.lexfind.ch/ |
| **Individual portals** | Varies | Some | Canton-specific |

### Strategy

1. **LexWork cantons (14):** Direct JSON API at `https://{host}/api/texts_of_law/`
   - Structured JSON responses with XHTML law text
   - Version history available
   - Pagination via search endpoint
   - Catalog via LexFind search API as fallback

2. **LexFind-only cantons (12):** HTML scraping or LexFind TOL IDs
   - LexFind serves JavaScript-rendered pages (hard to scrape)
   - Some cantons have their own structured portals (e.g. ZH, GE)
   - Fallback: manual TOL ID lists

---

## LexWork Cantons (14) — Direct API Access

These cantons use the LexWork platform, which exposes a consistent JSON API.

### API Pattern

```
Base URL:  https://{host}/api
Catalog:   GET /api/texts_of_law/?search=&limit=50&offset=0
Single:    GET /api/texts_of_law/{systematic_number}
Version:   GET /api/texts_of_law/{systematic_number}/versions/{version_id}
```

### Response Structure

```json
{
  "text_of_law": {
    "title": "...",
    "abbreviation": "...",
    "systematic_number": "100.100",
    "enactment": "2005-01-01",
    "publication_enactment": "2024-06-01",
    "selected_version": {
      "xhtml_tol": "<div>...</div>",
      "version_dates_str": "In Kraft seit: 01.06.2024"
    },
    "old_versions": [...]
  }
}
```

### Canton Details

| # | Canton | Abbr | Host | API Base | Languages | Notes |
|---|--------|------|------|----------|-----------|-------|
| 1 | Aargau | AG | `gesetzessammlungen.ag.ch` | `/api` | de | SAR (Systematische Sammlung) |
| 2 | Appenzell A.Rh. | AR | `ar.clex.ch` | `/api` | de | clex platform variant |
| 3 | Bern | BE | `www.belex.sites.be.ch` | `/api` | de, fr | Bilingual canton |
| 4 | Basel-Landschaft | BL | `bl.clex.ch` | `/api` | de | clex platform variant |
| 5 | Basel-Stadt | BS | `www.gesetzessammlung.bs.ch` | `/api` | de | SG (Systematische Gesetzessammlung) |
| 6 | Fribourg | FR | `bdlf.fr.ch` | `/api` | fr, de | Bilingual; primary language French |
| 7 | Glarus | GL | `gesetze.gl.ch` | `/api` | de | Small collection |
| 8 | Graubunden | GR | `www.gr-lex.gr.ch` | `/api` | de, rm, it | Trilingual canton (Romansh!) |
| 9 | Luzern | LU | `srl.lu.ch` | `/api` | de | SRL (Systematische Rechtssammlung) |
| 10 | St. Gallen | SG | `www.gesetzessammlung.sg.ch` | `/api` | de | nGS/sGS numbering |
| 11 | Solothurn | SO | `bgs.so.ch` | `/api` | de | BGS numbering |
| 12 | Thurgau | TG | `www.rechtsbuch.tg.ch` | `/api` | de | RB-TG numbering |
| 13 | Valais | VS | `lex.vs.ch` | `/api` | fr, de | Bilingual canton |
| 14 | Zug | ZG | `bgs.zg.ch` | `/api` | de | BGS numbering |

### Verification Status

All 14 LexWork endpoints have been verified to serve JSON at their `/api/texts_of_law/` path.
The API is consistent across all LexWork instances (same software platform).

---

## LexFind-Only Cantons (12) — Alternative Sources

These cantons do NOT use LexWork. Data must come from LexFind or canton-specific portals.

### LexFind API

LexFind (https://www.lexfind.ch/) is the Swiss law search portal operated by the
Conference of Cantonal Chancelleries. It covers all 26 cantons but:
- Pages are JavaScript-rendered (React SPA) — not easily scrapable
- Has an internal search API: `GET /fe/api/search?canton={XX}&jurisdiction=cantonal`
- Law text pages: `GET /fe/{lang}/tol/{tol_id}/{lang}`
- TOL (Text of Law) IDs are required for direct access

### Canton-by-Canton Analysis

| # | Canton | Abbr | Own Portal | Format | Languages | Data Quality | Priority |
|---|--------|------|-----------|--------|-----------|-------------|----------|
| 1 | Appenzell I.Rh. | AI | None (tiny canton) | LexFind only | de | Low volume | Low |
| 2 | Geneve | GE | https://silgeneve.ch/ | HTML, structured | fr | Good; own portal | High |
| 3 | Jura | JU | https://rsju.jura.ch/ | HTML | fr | Moderate | Medium |
| 4 | Neuchatel | NE | https://rsn.ne.ch/ | HTML | fr | Moderate | Medium |
| 5 | Nidwalden | NW | https://www.gesetzessammlung.nw.ch/ | HTML/PDF | de | Limited | Low |
| 6 | Obwalden | OW | https://www.ow.ch/rechtssammlung | HTML/PDF | de | Limited | Low |
| 7 | Schaffhausen | SH | https://sh.clex.ch/ | Possibly clex API! | de | Check if API works | Medium |
| 8 | Schwyz | SZ | https://www.sz.ch/srsz | HTML | de | Limited | Low |
| 9 | Ticino | TI | https://www.lexfind.ch/ (RL-TI) | LexFind only | it | Only Italian canton | High |
| 10 | Uri | UR | https://www.ur.ch/recht | HTML/PDF | de | Limited | Low |
| 11 | Vaud | VD | https://www.lexfind.ch/ | LexFind only | fr | Large canton | High |
| 12 | Zurich | ZH | https://www.zh.ch/de/politik-staat/gesetze-beschluesse/gesetzessammlung.html | HTML, OS-based | de | Largest canton | High |

### Notable Findings

#### Zurich (ZH) — Highest Priority
- **Portal:** https://www.zh.ch/de/politik-staat/gesetze-beschluesse/gesetzessammlung.html
- **Data format:** HTML pages with systematic numbering (LS/OS numbers)
- **API:** No public JSON API found; content served via CMS (Magnolia)
- **Alternative:** LexFind TOL IDs available for all ZH laws
- **Volume:** ~1,500+ laws (largest cantonal collection)
- **Strategy:** Use LexFind TOL IDs; scrape HTML as fallback

#### Geneve (GE) — High Priority
- **Portal:** https://silgeneve.ch/legis/
- **Data format:** Structured HTML with systematic numbering
- **API:** SILGENEVE system has some structured endpoints
- **Volume:** ~800+ laws
- **Language:** French only
- **Strategy:** Scrape SILGENEVE portal; LexFind fallback

#### Ticino (TI) — High Priority (only Italian-speaking canton)
- **Portal:** Via LexFind (RL-TI collection)
- **Data format:** LexFind HTML
- **Language:** Italian only
- **Volume:** ~600+ laws
- **Strategy:** LexFind TOL IDs

#### Schaffhausen (SH) — Not LexWork
- **Portal:** https://sh.clex.ch/
- **Note:** Uses `clex.ch` domain like AR and BL, but API endpoint returns 404
- **Tested:** `https://sh.clex.ch/api/texts_of_law/` -> "Ressource nicht gefunden"
- **Conclusion:** Despite the clex domain, SH does not expose a LexWork JSON API
- **Strategy:** LexFind fallback only

#### Vaud (VD) — High Priority
- **Portal:** No dedicated portal found; uses LexFind
- **Volume:** ~1,000+ laws
- **Language:** French only
- **Strategy:** LexFind TOL IDs

---

## Data Source Comparison

| Source | Structured API | Version History | Coverage | Reliability |
|--------|---------------|----------------|----------|-------------|
| LexWork | Yes (JSON) | Yes (old_versions) | 14 cantons | High |
| LexFind | Partial (search) | No | All 26 | Medium |
| Canton portals | Varies | Rarely | Individual | Variable |
| Fedlex | No cantonal | No | Federal only | High |

---

## Implementation Recommendations

### Phase 1: LexWork cantons (14 cantons)
Already implemented in `cantonal.py`. These provide the best data quality
with structured JSON APIs and version history.

**Immediate actions:**
- Verify SH (Schaffhausen) has a working clex API — if so, add to LexWork list
- Run catalog fetch for all 14 cantons to measure coverage
- Process AG and BS (already have test data in `kt/`)

### Phase 2: High-priority LexFind cantons (ZH, GE, TI, VD)
These are the largest/most important cantons without LexWork.

**Strategy:**
- Build LexFind TOL ID catalog per canton (one-time scrape or manual list)
- Fetch text via LexFind HTML pages
- Parse HTML to extract law content

### Phase 3: Remaining cantons (AI, JU, NE, NW, OW, SZ, UR)
Lower priority due to smaller collections or limited digital availability.

**Strategy:**
- LexFind fallback for all
- Canton-specific scrapers only if LexFind quality is insufficient

---

## API Endpoints Summary

### LexWork API (14 cantons)
```
GET https://{host}/api/texts_of_law/
GET https://{host}/api/texts_of_law/{number}
GET https://{host}/api/texts_of_law/{number}/versions/{id}
```

### LexFind API (all 26 cantons)
```
GET https://www.lexfind.ch/fe/api/search?canton={XX}&jurisdiction=cantonal&language={lang}&limit=50
GET https://www.lexfind.ch/fe/{lang}/tol/{tol_id}/{lang}
```

### Fedlex SPARQL (federal only — for reference)
```
POST https://fedlex.data.admin.ch/sparqlendpoint
```

---

## References

- LexFind: https://www.lexfind.ch/
- LexWork platform: https://www.lexwork.ch/ (vendor)
- Fedlex: https://www.fedlex.admin.ch/
- Conference of Cantonal Chancelleries: https://www.kdk.ch/
