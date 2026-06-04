# Cantonal Law Directory Structure

## Design

Cantonal laws are stored under `ch/{canton}/{lang}/{systematic_number}.md`, mirroring
the federal law layout (`ch/de/`, `ch/fr/`, `ch/it/`) but scoped per canton.

```
ch/
├── de/              # Federal laws (German)
├── fr/              # Federal laws (French)
├── it/              # Federal laws (Italian)
├── zh/              # Canton Zürich
│   ├── de/
│   │   ├── 131.1.md
│   │   └── 700.1.md
│   ├── fr/
│   └── it/
├── bs/              # Canton Basel-Stadt
│   ├── de/
│   │   └── 300.100.md
│   ├── fr/
│   └── it/
├── ge/              # Canton Genève
│   ├── de/
│   ├── fr/
│   │   └── A.2.05.md
│   └── it/
└── ...              # (all 26 cantons)
```

## Conventions

| Aspect | Rule |
|--------|------|
| Canton code | 2-letter lowercase abbreviation (ISO/CH standard) |
| Language dirs | `de`, `fr`, `it` per canton (even if only one is used) |
| Filename | `{systematic_number}.md` — using the canton's own numbering |
| Frontmatter | YAML with `canton`, `systematic_number`, `title`, `language`, `source`, `version_date` |
| Source | `LexWork` (14 cantons with direct API) or `LexFind` (12 cantons via fallback) |

## Rationale

- **Flat per language**: No nesting by systematic-number category — cantonal numbering
  systems vary wildly (numeric, alphanumeric, hierarchical). A flat directory keeps
  paths predictable and `canton_to_path()` trivial.
- **Under `ch/`**: All Swiss law (federal + cantonal) lives under a single `ch/` tree,
  making the repo structure self-documenting.
- **Three language dirs always present**: Even cantons with a single official language
  get all three dirs for consistency and to accommodate future bilingual texts.

## Path function

```python
def canton_to_path(canton: str, systematic_number: str, language: str) -> str:
    return f"ch/{canton}/{language}/{systematic_number}.md"
```

## Migration from `kt/` (legacy)

The previous structure `kt/{canton}/{number}/{lang}/{number}.md` has been replaced.
Existing files were migrated to the new layout in May 2026.
