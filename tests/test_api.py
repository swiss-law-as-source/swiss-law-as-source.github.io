"""Tests for the Swiss law REST API."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from legalize_ch.api import create_app, _sr_to_path, _parse_frontmatter


@pytest.fixture
def repo_dir(tmp_path):
    """Create a temporary repo with sample law files."""
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )

    # Create a law file
    law_dir = tmp_path / "ch" / "210" / "de"
    law_dir.mkdir(parents=True)
    content_v1 = """---
language: de
source: https://fedlex.data.admin.ch
sr_number: '210'
title: Schweizerisches Zivilgesetzbuch
version_date: '1980-01-01'
---

210
Schweizerisches Zivilgesetzbuch
Version 1980
"""
    (law_dir / "210.md").write_text(content_v1, encoding="utf-8")

    # Commit v1
    env1 = {"GIT_COMMITTER_DATE": "1980-01-01T00:00:00Z"}
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "SR 210: ZGB (1980-01-01)",
         "--date=1980-01-01T00:00:00Z"],
        cwd=str(tmp_path), capture_output=True, env={**os.environ, **env1},
    )

    # Update to v2
    content_v2 = """---
language: de
source: https://fedlex.data.admin.ch
sr_number: '210'
title: Schweizerisches Zivilgesetzbuch
version_date: '2023-01-01'
---

210
Schweizerisches Zivilgesetzbuch
Version 2023 (updated)
"""
    (law_dir / "210.md").write_text(content_v2, encoding="utf-8")

    env2 = {"GIT_COMMITTER_DATE": "2023-01-01T00:00:00Z"}
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "SR 210: ZGB (2023-01-01)",
         "--date=2023-01-01T00:00:00Z"],
        cwd=str(tmp_path), capture_output=True, env={**os.environ, **env2},
    )

    # Add a French version
    fr_dir = tmp_path / "ch" / "210" / "fr"
    fr_dir.mkdir(parents=True)
    content_fr = """---
language: fr
source: https://fedlex.data.admin.ch
sr_number: '210'
title: Code civil suisse
version_date: '2023-01-01'
---

210
Code civil suisse
"""
    (fr_dir / "210.md").write_text(content_fr, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "SR 210: CC (2023-01-01)",
         "--date=2023-01-01T00:00:00Z"],
        cwd=str(tmp_path), capture_output=True, env={**os.environ, **env2},
    )

    return tmp_path


@pytest.fixture
def client(repo_dir):
    """Create a test client with the temporary repo."""
    app = create_app(repo_path=repo_dir)
    return TestClient(app)


class TestSrToPath:
    def test_simple_sr(self):
        assert _sr_to_path("210", "de") == Path("ch/210/de/210.md")

    def test_dotted_sr(self):
        assert _sr_to_path("210.1", "de") == Path("ch/210/de/210.1.md")

    def test_deep_sr(self):
        assert _sr_to_path("0.101", "fr") == Path("ch/0/fr/0.101.md")


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\ntitle: Test\nsr_number: '1'\n---\nBody text"
        meta, body = _parse_frontmatter(text)
        assert meta["title"] == "Test"
        assert body == "Body text"

    def test_no_frontmatter(self):
        text = "Just plain text"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n---\nBody"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == "Body"


class TestGetLaw:
    def test_get_latest(self, client):
        resp = client.get("/api/v1/laws/210?lang=de")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sr_number"] == "210"
        assert data["title"] == "Schweizerisches Zivilgesetzbuch"
        assert data["version_date"] == "2023-01-01"
        assert "Version 2023" in data["content"]

    def test_get_by_date_historical(self, client):
        resp = client.get("/api/v1/laws/210?lang=de&date=2000-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert "Version 1980" in data["content"]
        assert data["version_date"] == "1980-01-01"

    def test_get_by_date_recent(self, client):
        resp = client.get("/api/v1/laws/210?lang=de&date=2024-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert "Version 2023" in data["content"]

    def test_get_french(self, client):
        resp = client.get("/api/v1/laws/210?lang=fr")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Code civil suisse"
        assert data["language"] == "fr"

    def test_not_found(self, client):
        resp = client.get("/api/v1/laws/999.999?lang=de")
        assert resp.status_code == 404

    def test_date_too_early(self, client):
        resp = client.get("/api/v1/laws/210?lang=de&date=1800-01-01")
        assert resp.status_code == 404

    def test_invalid_date(self, client):
        resp = client.get("/api/v1/laws/210?lang=de&date=not-a-date")
        assert resp.status_code == 400


class TestGetVersions:
    def test_list_versions(self, client):
        resp = client.get("/api/v1/laws/210/versions?lang=de")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sr_number"] == "210"
        assert len(data["versions"]) == 2
        # Versions are in reverse chronological order (git log default)
        assert data["versions"][0]["version_date"] == "2023-01-01"
        assert data["versions"][1]["version_date"] == "1980-01-01"

    def test_versions_not_found(self, client):
        resp = client.get("/api/v1/laws/999/versions?lang=de")
        assert resp.status_code == 404


class TestSearch:
    def test_search_by_sr_prefix(self, client):
        resp = client.get("/api/v1/search?q=210&lang=de")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["sr_number"] == "210"

    def test_search_by_title(self, client):
        resp = client.get("/api/v1/search?q=Zivilgesetzbuch&lang=de")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) >= 1

    def test_search_no_results(self, client):
        resp = client.get("/api/v1/search?q=xyznonexistent&lang=de")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.fixture
def publications_repo(tmp_path):
    """Repo with commits across different dates and scopes for publications tests."""
    cwd = str(tmp_path)
    subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=cwd, capture_output=True)

    def commit(rel_paths: dict[str, str], msg: str, iso: str):
        for rel, content in rel_paths.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
        env = {**os.environ,
               "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
        subprocess.run(
            ["git", "commit", "-m", msg, f"--date={iso}"],
            cwd=cwd, capture_output=True, env=env,
        )

    commit(
        {"ch/210/de/210.md": "x", "ch/210/fr/210.md": "x", "ch/210/it/210.md": "x"},
        "SR 210: Schweizerisches Zivilgesetzbuch (2023-01-01)",
        "2023-01-01T12:00:00+01:00",
    )
    commit(
        {"ch/220/de/220.md": "y"},
        "SR 220: Obligationenrecht (2023-04-15)",
        "2023-04-15T12:00:00+01:00",
    )
    commit(
        {"kt/zh/de/170.4.md": "z"},
        "SR 170.4: Gemeindegesetz (2024-06-01)",
        "2024-06-01T12:00:00+01:00",
    )
    return tmp_path


@pytest.fixture
def pub_client(publications_repo):
    app = create_app(repo_path=publications_repo)
    return TestClient(app)


class TestPublications:
    def test_by_year(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=2023")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date_prefix"] == "2023"
        assert data["count"] == 2
        srs = {p["sr_number"] for p in data["publications"]}
        assert srs == {"210", "220"}

    def test_by_year_month(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=2023-04")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["publications"][0]["sr_number"] == "220"

    def test_by_full_date(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=2023-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        pub = data["publications"][0]
        assert pub["sr_number"] == "210"
        assert pub["scope"] == "federal"
        assert sorted(pub["languages"]) == ["de", "fr", "it"]

    def test_filter_by_scope_cantonal(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=2024&scope=cantonal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["publications"][0]["scope"] == "cantonal"
        assert data["publications"][0]["sr_number"] == "170.4"

    def test_filter_by_lang(self, pub_client):
        # 220 has only DE; filtering by FR should exclude it
        resp = pub_client.get("/api/v1/publications?date=2023&lang=fr")
        assert resp.status_code == 200
        data = resp.json()
        srs = {p["sr_number"] for p in data["publications"]}
        assert srs == {"210"}

    def test_invalid_date(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=not-a-date")
        assert resp.status_code == 422

    def test_empty_window(self, pub_client):
        resp = pub_client.get("/api/v1/publications?date=1999")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


@pytest.fixture
def early_publications_repo(tmp_path):
    """Repo where one law carries a pre-1970 `original_publication_date`
    in its markdown frontmatter — git can't represent the date itself in
    a commit, so the API must surface it from the frontmatter."""
    cwd = str(tmp_path)
    subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=cwd, capture_output=True)

    treaty = tmp_path / "ch/0/de/0.742.140.313.61.md"
    treaty.parent.mkdir(parents=True, exist_ok=True)
    treaty.write_text(
        "---\n"
        "sr_number: '0.742.140.313.61'\n"
        "language: de\n"
        "title: Vertrag vom 27. Juli 1852 ...\n"
        "version_date: '1985-10-07'\n"
        "original_publication_date: '1852-07-27'\n"
        "---\n\nBody\n",
        encoding="utf-8",
    )
    # Same law in French — should be deduplicated by (sr_number, scope).
    treaty_fr = tmp_path / "ch/0/fr/0.742.140.313.61.md"
    treaty_fr.parent.mkdir(parents=True, exist_ok=True)
    treaty_fr.write_text(
        "---\n"
        "sr_number: '0.742.140.313.61'\n"
        "language: fr\n"
        "title: Traité du 27 juillet 1852 ...\n"
        "version_date: '1985-10-07'\n"
        "original_publication_date: '1852-07-27'\n"
        "---\n\nCorps\n",
        encoding="utf-8",
    )
    # A 2023 law for a contrast case (post-1970, no marker).
    modern = tmp_path / "ch/210/de/210.md"
    modern.parent.mkdir(parents=True, exist_ok=True)
    modern.write_text(
        "---\nsr_number: '210'\nlanguage: de\ntitle: ZGB\nversion_date: '2023-01-01'\n---\n\nBody\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
    iso = "2023-01-01T12:00:00+01:00"
    subprocess.run(
        ["git", "commit", "-m", "SR 210: ZGB (2023-01-01)", f"--date={iso}"],
        cwd=cwd, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso},
    )
    return tmp_path


@pytest.fixture
def early_client(early_publications_repo):
    app = create_app(repo_path=early_publications_repo)
    return TestClient(app)


class TestEarlyPublications:
    def test_query_by_year_1852(self, early_client):
        resp = early_client.get("/api/v1/publications?date=1852")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        pub = data["publications"][0]
        assert pub["sr_number"] == "0.742.140.313.61"
        assert pub["date"] == "1852-07-27"
        assert pub["scope"] == "federal"
        # Both language files of the same law are aggregated into one entry.
        assert sorted(pub["languages"]) == ["de", "fr"]

    def test_query_by_year_month_1852_07(self, early_client):
        resp = early_client.get("/api/v1/publications?date=1852-07")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_query_by_full_date_1852_07_27(self, early_client):
        resp = early_client.get("/api/v1/publications?date=1852-07-27")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_query_excludes_other_dates(self, early_client):
        resp = early_client.get("/api/v1/publications?date=1853")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_lang_filter_post_dedup(self, early_client):
        # The single law has both DE and FR; filtering by FR still finds it.
        resp = early_client.get("/api/v1/publications?date=1852&lang=fr")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert "fr" in data["publications"][0]["languages"]

    def test_lang_filter_excludes_when_missing(self, early_client):
        # Italian text isn't on disk → filtering by it should yield nothing.
        resp = early_client.get("/api/v1/publications?date=1852&lang=it")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_mixed_window_merges_git_and_frontmatter(self, early_client):
        # A window that spans the 1970 boundary should return both the
        # pre-1970 frontmatter entry and the 2023 git-log entry.
        resp = early_client.get("/api/v1/publications?date=1852-07-27")
        early = resp.json()["publications"]
        resp = early_client.get("/api/v1/publications?date=2023-01-01")
        modern = resp.json()["publications"]
        assert len(early) == 1 and early[0]["date"] == "1852-07-27"
        assert len(modern) == 1 and modern[0]["date"] == "2023-01-01"
