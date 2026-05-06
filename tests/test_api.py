"""Tests for the Swiss law REST API."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

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
        cwd=str(tmp_path), capture_output=True, env={**subprocess.os.environ, **env1},
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
        cwd=str(tmp_path), capture_output=True, env={**subprocess.os.environ, **env2},
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
        cwd=str(tmp_path), capture_output=True, env={**subprocess.os.environ, **env2},
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
