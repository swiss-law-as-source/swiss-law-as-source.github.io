# Swiss Law OpenFisca TODO

Tasks for the scheduled law-to-code converter. Pick one task per run.
Mark completed tasks with `[x]` and add the date.

## 1. Fix broken executable files (146 files)

These files have NameErrors at runtime due to undefined variables.
Use `scripts/repair_openfisca.py` or regenerate them with the updated prompt.

- [x] Fix `Country` references (39 files) — replace with Person or remove — 2026-05-10
- [ ] Fix `Institution` references (8 files)
- [ ] Fix `Household` references (6 files) — replace with Person
- [ ] Fix `Government` references (5 files)
- [ ] Fix `PERIOD` references (4 files) — replace with YEAR or MONTH
- [ ] Fix `World` references (3 files)
- [ ] Fix `Organization` references (2 files)
- [ ] Fix `datetime` references (2 files) — add `import datetime`
- [ ] Fix `Entity` references (2 files)
- [ ] Fix `Month` references (2 files) — replace with MONTH
- [ ] Fix `WEEK` references (2 files) — replace with MONTH
- [ ] Fix `person` references (2 files) — replace with Person
- [ ] Fix remaining misc references (9 files: NONE, Exists, ETD, boolean_var, ContractingParty, Mesh, agents, variables)

## 2. Translate remaining federal SR bases (68 bases, ~220 md files)

Run `python -m legalize_ch.law_to_openfisca --sr-filter <NUMBER>` for each.
Process 2-3 bases per run to stay within time/cost limits.

- [ ] SR 133, 134, 135, 136, 137 (9 md)
- [ ] SR 162, 163, 182 (5 md)
- [ ] SR 191, 193 (6 md)
- [ ] SR 273, 283, 284 (5 md)
- [ ] SR 331, 341, 343, 345, 353 (11 md)
- [ ] SR 422, 423, 424 (8 md)
- [ ] SR 426, 435 (9 md)
- [ ] SR 445, 447, 449, 454 (11 md)
- [ ] SR 515, 517 (5 md)
- [ ] SR 521, 522 (11 md)
- [ ] SR 524, 527, 528 (6 md)
- [ ] SR 610, 612, 617 (8 md)
- [ ] SR 671, 681 (6 md)
- [ ] SR 682, 683 (16 md)
- [ ] SR 686, 689 (7 md)
- [ ] SR 700, 709 (5 md)
- [ ] SR 720, 723 (6 md)
- [ ] SR 781 (26 md)
- [ ] SR 815, 841 (4 md)
- [ ] SR 843, 854, 862 (10 md)
- [ ] SR 912, 914, 917 (9 md)
- [ ] SR 931, 936, 945 (3 md)
- [ ] SR 953, 963 (4 md)
- [ ] SR 972, 973 (18 md)
- [ ] SR 975, 977, 978 (5 md)
- [ ] SR 983, 984 (7 md)

## 3. Fix ModuleNotFoundError files (29 files)

These files import `openfisca` or `openfisca_countries` which don't exist.
Regenerate them using the updated prompt in `law_to_openfisca.py`.

- [ ] Delete and regenerate files with `from openfisca import` or `from openfisca_countries import`

## 4. Post-repair validation

After all fixes and translations are done:

- [ ] Run import check across all executable files, report pass rate
- [ ] Regenerate `__init__.py` files for any SR bases that got new files
- [ ] Commit and push results
