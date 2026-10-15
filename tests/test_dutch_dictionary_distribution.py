"""Tiny synthetic SQLite assets; no network, raw source download or paid calls."""

import hashlib
import json
import os
import shutil
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from podcast_bot.reader.dutch import SNAPSHOT, DutchDictionary
from tools import dutch_dictionary_distribution as dist
from tools.build_dutch_dictionary import build


@pytest.fixture
def release(tmp_path):
    records = []
    forms = {"huis": "huizen", "kind": "kinderen", "jongen": "jongens", "museum": "musea"}
    for word in ("zolder", "huis", "kind", "mens", "jongen", "museum"):
        records.append(
            {
                "word": word,
                "lang_code": "nl",
                "pos": "noun",
                "senses": [{"tags": ["neuter"], "glosses": ["synthetic fixture definition"]}],
                "forms": [{"form": forms[word], "tags": ["plural"]}] if word in forms else [],
            }
        )
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    snapshot = {**SNAPSHOT, "subset_sha256": dist.digest(source)}
    artifact = tmp_path / "fixture.sqlite3"
    build(source, artifact, snapshot=snapshot)
    assets = tmp_path / "assets"
    result = dist.package(assets, artifact=artifact)
    return {
        "assets": assets,
        "pin": result["manifest_sha256"],
        "result": result,
        "artifact": artifact,
        "source": source,
        "snapshot": snapshot,
        "root": tmp_path / "installed",
    }


def test_package_contract_determinism_and_version_separation(release, tmp_path):
    r = release
    m = dist.validate_bundle(r["assets"], r["pin"])
    assert set(p.name for p in r["assets"].iterdir()) == {dist.ARTIFACT, dist.MANIFEST, dist.NOTICE}
    assert m["version"] == "dutch-dictionary-enwiktionary-20260902-20261003-r1-s1-b1"
    assert m["source_metadata"]["subset_sha256"] == r["snapshot"]["subset_sha256"]
    assert "Wiktionary contributors" in (r["assets"] / dist.NOTICE).read_text()
    second = dist.package(tmp_path / "second", artifact=r["artifact"])
    assert second == r["result"]
    built = dist.package(tmp_path / "built", source=r["source"], snapshot=r["snapshot"])
    assert built == second
    assert dist.digest(tmp_path / "built" / dist.ARTIFACT) == dist.digest(r["artifact"])
    with pytest.raises(dist.DistributionError):
        dist.package(r["assets"], artifact=r["artifact"])


def test_install_verify_offline_current_and_reader_survives_switch(release, tmp_path, monkeypatch):
    r = release
    root = r["root"]
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network"))
    first = dist.install(root, r["pin"], dist.Assets(source_dir=r["assets"]))
    assert first["status"] == "installed" and dist.verify(root)["manifest_sha256"] == r["pin"]
    old = dist.current_directory(root)
    reader = DutchDictionary(root / "current" / dist.ARTIFACT)
    try:
        assert reader.lookup("huizen")["candidates"][0]["lemma"] == "huis"
        newer = dist.package(tmp_path / "newer", artifact=r["artifact"], revision="r2")
        dist.install(root, newer["manifest_sha256"], dist.Assets(source_dir=tmp_path / "newer"))
        assert reader.lookup("zolder")["available"]
        assert old.is_dir() and dist.current_directory(root) != old
        missing = dist.Assets(source_dir=tmp_path / "not-present")
        assert dist.install(root, newer["manifest_sha256"], missing)["status"] == "already_current"
        restored = dist.rollback(root, first["version"], r["pin"])
        assert restored["artifact_sha256"] == first["artifact_sha256"]
        assert dist.current_directory(root) == old
        assert not list(root.glob(".current-*")) and not list(
            (root / "versions").glob(".download-*")
        )
    finally:
        reader.close()


@pytest.mark.parametrize(
    "mutation", ["artifact", "bytes", "notice", "manifest", "schema", "builder", "source", "names"]
)
def test_failed_verification_preserves_previous_generation(release, tmp_path, mutation):
    r = release
    root = r["root"]
    dist.install(root, r["pin"], dist.Assets(source_dir=r["assets"]))
    selected = os.readlink(root / "current")
    bad = tmp_path / "bad"
    shutil.copytree(r["assets"], bad)
    m = json.loads((bad / dist.MANIFEST).read_text())
    pin = r["pin"]
    if mutation == "artifact":
        (bad / dist.ARTIFACT).write_bytes(b"corrupt")
    elif mutation == "notice":
        (bad / dist.NOTICE).write_text("wrong attribution")
    else:
        if mutation == "bytes":
            m["bytes"] += 1
        elif mutation == "schema":
            m["schema_version"] = "999"
        elif mutation == "builder":
            m["builder_version"] = "999"
        elif mutation == "source":
            m["source_metadata"]["input_sha256"] = "0" * 64
        elif mutation == "names":
            m["artifact"] = "../escape.sqlite3"
        else:
            m["manifest_version"] = 999
        (bad / dist.MANIFEST).write_text(json.dumps(m))
        pin = dist.digest(bad / dist.MANIFEST)
    # A different request pin forces validation; already-current never hides remote mutations.
    if pin == r["pin"]:
        m["release_revision"] = "r2"
        m["content_version"], m["version"] = dist.version(dist.expected_metadata(m), "r2")
        (bad / dist.MANIFEST).write_text(json.dumps(m))
        pin = dist.digest(bad / dist.MANIFEST)
    expected = (
        dist.IncompatibleSchema if mutation in {"schema", "builder"} else dist.DistributionError
    )
    with pytest.raises(expected):
        dist.install(root, pin, dist.Assets(source_dir=bad))
    assert os.readlink(root / "current") == selected
    assert dist.verify(root)["manifest_sha256"] == r["pin"]
    assert not list((root / "versions").glob(".download-*"))


@pytest.mark.parametrize("payload", [b"garbage", b"SQLite format 3\x00" + b"0" * 100])
def test_truncated_or_corrupt_sqlite_with_matching_download_hash_rejected(
    release, tmp_path, payload
):
    r = release
    bad = tmp_path / "bad"
    shutil.copytree(r["assets"], bad)
    (bad / dist.ARTIFACT).write_bytes(payload)
    m = json.loads((bad / dist.MANIFEST).read_text())
    m["bytes"] = len(payload)
    m["sha256"] = dist.digest(bad / dist.ARTIFACT)
    (bad / dist.MANIFEST).write_text(json.dumps(m))
    pin = dist.digest(bad / dist.MANIFEST)
    with pytest.raises(dist.DistributionError):
        dist.install(r["root"], pin, dist.Assets(source_dir=bad))
    assert not (r["root"] / "current").exists()


def test_network_failure_and_interruption_before_commit_leave_current_intact(release, tmp_path):
    r = release
    root = r["root"]
    dist.install(root, r["pin"], dist.Assets(source_dir=r["assets"]))
    selected = os.readlink(root / "current")

    class Offline:
        def fetch(self, *args):
            raise dist.DownloadFailed("unavailable")

    with pytest.raises(dist.DownloadFailed):
        dist.install(root, "0" * 64, Offline())
    next_assets = tmp_path / "next"
    result = dist.package(next_assets, artifact=r["artifact"], revision="r2")
    for failure in (OSError("rename failed"), KeyboardInterrupt()):
        with patch.object(dist.os, "replace", side_effect=failure), pytest.raises(type(failure)):
            dist.install(root, result["manifest_sha256"], dist.Assets(source_dir=next_assets))
        assert os.readlink(root / "current") == selected
        assert dist.verify(root)["manifest_sha256"] == r["pin"]
    # Simulate SIGKILL debris: previous selection survives and next invocation cleans staging only.
    stale = root / "versions" / ".download-interrupted"
    stale.mkdir()
    (stale / "partial").write_bytes(b"partial")
    (root / ".current-interrupted").symlink_to("versions/missing")
    dist.install(root, r["pin"], dist.Assets(source_dir=r["assets"]))
    assert not stale.exists() and not (root / ".current-interrupted").is_symlink()


def test_concurrent_installers_serialize_and_publish_one_complete_generation(release):
    r = release
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        return dist.install(r["root"], r["pin"], dist.Assets(source_dir=r["assets"]))["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: run(), range(2)))
    assert sorted(statuses) == ["already_current", "installed"]
    assert dist.verify(r["root"])["manifest_sha256"] == r["pin"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/releases/v1",
        "file:///etc/passwd",
        "https://name:secret@example.test/v1",
        "https://example.test/v1?token=secret",
    ],
)
def test_sources_require_https_without_credentials(url):
    with pytest.raises(dist.DownloadFailed):
        dist.Assets(release_url=url)


def test_manifest_trust_anchor_and_download_failure_messages(release, tmp_path):
    r = release
    raw = (r["assets"] / dist.MANIFEST).read_bytes()
    for pin in ("", "not-a-hash", "0" * 64):
        with pytest.raises(dist.DistributionError):
            dist.parse_manifest(raw, pin)
    with (
        patch(
            "urllib.request.OpenerDirector.open", side_effect=OSError("private signed URL secret")
        ),
        pytest.raises(dist.DownloadFailed, match="^Asset download/read failed$"),
    ):
        dist.Assets(release_url="https://example.test/v1").fetch(
            dist.MANIFEST, tmp_path / "download", 1024
        )
    with pytest.raises(dist.DownloadFailed):
        dist.HTTPSRedirect().redirect_request(None, None, 302, "", {}, "http://example.test/plain")


def test_manifest_size_and_downloader_limits(release, tmp_path):
    import io

    with pytest.raises(dist.DistributionError):
        dist.copy_asset(io.BytesIO(b"123"), tmp_path / "file", 2)
    raw = b"x" * (dist.MAX_MANIFEST + 1)
    with pytest.raises(dist.DistributionError):
        dist.parse_manifest(raw, hashlib.sha256(raw).hexdigest())


def test_runtime_accepts_new_compatible_content_but_not_schema_or_bad_metadata(release):
    import sqlite3

    r = release
    with sqlite3.connect(r["artifact"]) as db:
        for key, value in {
            "dump_date": "2026-10-01",
            "subset_sha256": "1" * 64,
            "input_sha256": "1" * 64,
        }.items():
            db.execute("UPDATE metadata SET value=? WHERE key=?", (value, key))
    d = DutchDictionary(r["artifact"])
    assert d.available
    d.close()
    with sqlite3.connect(r["artifact"]) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='subset_sha256'", (b"blob",))
    assert not DutchDictionary(r["artifact"]).available
    assert not DutchDictionary(r["root"] / "current" / dist.ARTIFACT).available


def test_https_download_path_installs_verified_assets(release):
    r = release
    requested = []

    def open_asset(_opener, url, timeout):
        assert timeout == 60 and url.startswith("https://example.test/pinned/")
        name = url.rsplit("/", 1)[1]
        requested.append(name)
        return (r["assets"] / name).open("rb")

    with patch("urllib.request.OpenerDirector.open", new=open_asset):
        result = dist.install(
            r["root"], r["pin"], dist.Assets(release_url="https://example.test/pinned")
        )
    assert result["status"] == "installed"
    assert requested == [dist.MANIFEST, dist.ARTIFACT, dist.NOTICE]


def test_generation_symlink_is_rejected_before_selection(release):
    r = release
    versions = r["root"] / "versions"
    versions.mkdir(parents=True)
    generation = versions / (r["result"]["version"] + "." + r["pin"])
    generation.symlink_to(r["assets"], target_is_directory=True)
    with pytest.raises(dist.InstallFailed):
        dist.install(r["root"], r["pin"], dist.Assets(source_dir=r["assets"]))
    with pytest.raises(dist.InstallFailed):
        dist.rollback(r["root"], r["result"]["version"], r["pin"])
    assert not (r["root"] / "current").exists()
