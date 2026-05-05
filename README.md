# Swiss Federal Law (SR) — Version-Controlled

Every Swiss federal law in Markdown, with a full git history of every amendment since inception.

## Stats

| | DE | FR | IT | Total |
|---|---|---|---|---|
| Laws | 9,035 | 9,035 | 9,034 | 27,104 |
| Commits | — | — | — | 18,710 |

## How it works

1. Fetches the complete SR catalog from [Fedlex SPARQL](https://fedlex.data.admin.ch/sparqlendpoint)
2. For each law, retrieves every consolidation version (historical snapshots)
3. Converts AKN XML / HTML to clean Markdown with YAML frontmatter
4. Commits each version with the correct author-date, so `git log` shows the legislative timeline

## Usage

**Browse a law's history:**
```bash
git log --follow ch/de/142.20.md   # Immigration Act amendments
```

**See what changed on a specific date:**
```bash
git log --after="2020-01-01" --before="2020-12-31" --oneline
```

**Get the text of a law at a point in time:**
```bash
git log --before="2015-06-01" -1 --format="%H" -- ch/de/220.md | xargs git show | head -50
```

## File structure

```
ch/
├── de/          # German texts (9,035 laws)
│   ├── 101.md  # Federal Constitution
│   ├── 210.md  # Civil Code
│   └── ...
├── fr/          # French texts
└── it/          # Italian texts
```

Each file has YAML frontmatter:
```yaml
---
sr: "101"
title: "Bundesverfassung der Schweizerischen Eidgenossenschaft"
language: de
source_url: https://fedlex.data.admin.ch/eli/cc/1999/404
---
```

## Running the pipeline

```bash
pip install -e .
legalize-ch run          # Full pipeline (fetches all laws)
legalize-ch run --sr 101 # Single law
```

## Data source

All data is sourced from [Fedlex](https://www.fedlex.admin.ch/), the official publication platform of Swiss federal law, via their public SPARQL endpoint.

## License

The pipeline code is MIT-licensed. The legal texts are public domain (Swiss federal law is not subject to copyright per Art. 5 URG).
