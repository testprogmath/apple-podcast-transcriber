"""Explicit Dutch dictionary package/install operations; never invoked by Reader startup.

Three assets, one pinned manifest, retained generations, one atomic current symlink.
POSIX flock/rename/fsync match the existing Linux deployment and macOS development.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from podcast_bot.reader.dutch import (
    BUILDER_VERSION,
    SCHEMA_VERSION,
    DutchDictionary,
    validate_metadata,
)
from tools.build_dutch_dictionary import build

ARTIFACT = "dutch.sqlite3"
MANIFEST = "dutch-artifact.json"
NOTICE = "LICENSE-DUTCH.txt"
MAX_MANIFEST = 128 * 1024
MAX_NOTICE = 256 * 1024
MAX_ARTIFACT = 512 * 1024 * 1024
SOURCE_KEYS = (
    "source",
    "source_url",
    "dump_date",
    "extraction_date",
    "compressed_sha256",
    "subset_sha256",
    "wiktextract_revision",
    "wikitextprocessor_revision",
    "input_kind",
    "input_sha256",
    "transformation",
)
COUNT_KEYS = ("processed_records", "entries", "senses", "glosses", "surfaces", "forms", "relations")
SANITY = {
    "zolder": ("zolder", "noun"),
    "huis": ("huis", "noun"),
    "huizen": ("huis", "noun"),
    "kind": ("kind", "noun"),
    "kinderen": ("kind", "noun"),
    "mens": ("mens", "noun"),
    "jongens": ("jongen", "noun"),
    "museum": ("museum", "noun"),
    "musea": ("museum", "noun"),
}


class DistributionError(Exception):
    status = "verification_failed"
    code = 2


class IncompatibleSchema(DistributionError):
    status = "incompatible_schema"
    code = 3


class DownloadFailed(DistributionError):
    status = "download_failed"
    code = 4


class InstallFailed(DistributionError):
    status = "install_failed"
    code = 5


def digest(file):
    with Path(file).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise DistributionError("A trusted SHA-256 pin is required")
    return value


def version(metadata, revision):
    if not re.fullmatch(r"r[1-9][0-9]*", revision):
        raise DistributionError("Use a positive release revision such as r1")
    content = f"enwiktionary-{metadata['dump_date'].replace('-', '')}-{metadata['extraction_date'].replace('-', '')}-{revision}"
    return (
        content,
        f"dutch-dictionary-{content}-s{metadata['schema_version']}-b{metadata['builder_version']}",
    )


def expected_metadata(manifest):
    return {
        **manifest["source_metadata"],
        **{
            k: manifest[k]
            for k in (
                "schema_version",
                "builder_version",
                "language",
                "license",
                "license_url",
                "attribution",
            )
        },
        **{f"count_{k}": str(v) for k, v in manifest["counts"].items()},
    }


def parse_manifest(data, pin):
    checksum(pin)
    if len(data) > MAX_MANIFEST or hashlib.sha256(data).hexdigest() != pin:
        raise DistributionError("Manifest checksum/size mismatch")
    try:
        m = json.loads(data)
        if (
            not isinstance(m, dict)
            or type(m["manifest_version"]) is not int
            or m["manifest_version"] != 1
        ):
            raise ValueError("manifest version")
        if m["schema_version"] != SCHEMA_VERSION or m["builder_version"] != BUILDER_VERSION:
            raise IncompatibleSchema("Unsupported dictionary schema/builder")
        if m["artifact"] != ARTIFACT or m["notice"] != NOTICE:
            raise ValueError("asset names")
        for k in ("sha256", "notice_sha256"):
            checksum(m[k])
        for key, maximum in (("bytes", MAX_ARTIFACT), ("notice_bytes", MAX_NOTICE)):
            if type(m[key]) is not int or not 0 < m[key] <= maximum:
                raise ValueError("asset size")
        if set(m["source_metadata"]) != set(SOURCE_KEYS) or set(m["counts"]) != set(COUNT_KEYS):
            raise ValueError("metadata fields")
        if (
            any(type(v) is not int or v < 0 for v in m["counts"].values())
            or not m["counts"]["entries"]
        ):
            raise ValueError("record counts")
        validate_metadata(expected_metadata(m))
        content, tag = version(expected_metadata(m), m["release_revision"])
        if m["content_version"] != content or m["version"] != tag:
            raise ValueError("content version")
        return m
    except DistributionError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise DistributionError("Invalid distribution manifest") from None


def validate_bundle(directory, pin, *, sanity=True):
    directory = Path(directory)
    with (directory / MANIFEST).open("rb") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    m = parse_manifest(raw, pin)
    for name, size, sha in (
        (ARTIFACT, m["bytes"], m["sha256"]),
        (NOTICE, m["notice_bytes"], m["notice_sha256"]),
    ):
        file = directory / name
        if not file.is_file() or file.stat().st_size != size or digest(file) != sha:
            raise DistributionError("Asset size/checksum mismatch")
    dictionary = DutchDictionary(directory / ARTIFACT, snapshot=expected_metadata(m))
    try:
        if not dictionary.available:
            raise DistributionError("Artifact schema/metadata/integrity mismatch")
        if (
            dictionary.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or dictionary.db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise DistributionError("Artifact integrity check failed")
        for table in COUNT_KEYS[1:]:
            if (
                dictionary.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                != m["counts"][table]
            ):
                raise DistributionError("Artifact count mismatch")
        if sanity:
            for surface, identity in SANITY.items():
                candidates = dictionary.lookup(surface)["candidates"]
                if identity not in {(c["lemma"], c["pos"]) for c in candidates}:
                    raise DistributionError("Representative lexical check failed")
        return m
    finally:
        dictionary.close()


def fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_asset(stream, destination, limit):
    total = 0
    with Path(destination).open("xb") as target:
        while block := stream.read(min(1024 * 1024, limit - total + 1)):
            total += len(block)
            if total > limit:
                raise DistributionError("Asset exceeds permitted byte size")
            target.write(block)
        target.flush()
        os.fsync(target.fileno())
    os.chmod(destination, 0o644)


def package(
    output, *, artifact=None, source=None, source_kind="subset", revision="r1", snapshot=None
):
    """Build from a pinned source, or reuse a locally trusted prebuilt artifact."""
    output = Path(output)
    if output.exists():
        raise DistributionError("Refusing to overwrite release assets")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=output.parent) as temporary:
        stage = Path(temporary) / "assets"
        stage.mkdir(mode=0o755)
        if source is not None:
            build(source, stage / ARTIFACT, source_kind=source_kind, snapshot=snapshot)
        elif artifact is not None:
            with Path(artifact).open("rb") as stream:
                copy_asset(stream, stage / ARTIFACT, MAX_ARTIFACT)
        else:
            raise DistributionError("Provide a pinned source or a trusted local artifact")
        dictionary = DutchDictionary(stage / ARTIFACT)
        if not dictionary.available:
            raise DistributionError("Invalid local artifact")
        metadata = dictionary.metadata.copy()
        dictionary.close()
        content, tag = version(metadata, revision)
        # Generate attribution from the artifact, so content upgrades cannot ship stale source pins.
        notice = (
            f"Dutch dictionary text/morphology: {metadata['attribution']}.\n"
            f"Licensed {metadata['license']}: {metadata['license_url']}\n"
            "Source pages and contributor histories: https://en.wiktionary.org/wiki/{URL-encoded source title}\n"
            "Each lexical candidate links its source page. Preserve applicable additional attribution notices.\n"
            "No endorsement is implied. Dictionary licensing is separate from application code.\n"
            "Distribute adapted dictionary data with attribution, license notice and change indication.\n"
            + "\n".join(f"{k}: {metadata[k]}" for k in SOURCE_KEYS)
            + f"\nschema_version: {SCHEMA_VERSION}\nbuilder_version: {BUILDER_VERSION}\n"
        )
        with (stage / NOTICE).open("xb") as stream:
            stream.write(notice.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(stage / NOTICE, 0o644)
        m = {
            "manifest_version": 1,
            "artifact": ARTIFACT,
            "version": tag,
            "content_version": content,
            "release_revision": revision,
            "schema_version": SCHEMA_VERSION,
            "builder_version": BUILDER_VERSION,
            "sha256": digest(stage / ARTIFACT),
            "bytes": (stage / ARTIFACT).stat().st_size,
            "counts": {k: int(metadata[f"count_{k}"]) for k in COUNT_KEYS},
            **{k: metadata[k] for k in ("language", "license", "license_url", "attribution")},
            "notice": NOTICE,
            "notice_bytes": (stage / NOTICE).stat().st_size,
            "notice_sha256": digest(stage / NOTICE),
            "source_metadata": {k: metadata[k] for k in SOURCE_KEYS},
        }
        raw = (json.dumps(m, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
        with (stage / MANIFEST).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(stage / MANIFEST, 0o644)
        pin = hashlib.sha256(raw).hexdigest()
        validate_bundle(stage, pin)
        fsync_dir(stage)
        os.rename(stage, output)
        fsync_dir(output.parent)
        return {
            "status": "packaged",
            "version": tag,
            "manifest_sha256": pin,
            "artifact_sha256": m["sha256"],
        }


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise DownloadFailed("Refusing non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Assets:
    def __init__(self, *, release_url=None, source_dir=None):
        if (release_url is None) == (source_dir is None):
            raise DistributionError("Choose release URL or local source directory")
        self.source_dir = Path(source_dir) if source_dir is not None else None
        self.url = release_url
        if release_url is not None:
            parsed = urllib.parse.urlsplit(release_url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise DownloadFailed("An HTTPS release URL without credentials is required")
            self.url = release_url.rstrip("/")

    def fetch(self, name, destination, limit):
        if name not in {ARTIFACT, MANIFEST, NOTICE}:
            raise DistributionError("Invalid asset name")
        try:
            if self.source_dir is not None:
                with (self.source_dir / name).open("rb") as stream:
                    copy_asset(stream, destination, limit)
            else:
                opener = urllib.request.build_opener(HTTPSRedirect())
                with opener.open(self.url + "/" + name, timeout=60) as stream:
                    copy_asset(stream, destination, limit)
        except DistributionError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            # Never echo remote URLs/redirect signatures, credentials or raw network exceptions.
            raise DownloadFailed("Asset download/read failed") from None


@contextmanager
def locked(root):
    root = Path(root)
    if not root.exists():
        root.mkdir(parents=True, mode=0o755, exist_ok=True)
        root.chmod(0o755)
    versions = root / "versions"
    if not versions.exists():
        versions.mkdir(exist_ok=True, mode=0o755)
        versions.chmod(0o755)
    fd = os.open(root / ".install.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        for stale in versions.glob(".download-*"):
            if stale.is_symlink() or stale.is_file():
                stale.unlink()
            else:
                shutil.rmtree(stale)
        for stale in root.glob(".current-*"):
            stale.unlink()
        yield root
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def current_directory(root):
    pointer = Path(root) / "current"
    if not pointer.is_symlink():
        if pointer.exists():
            raise InstallFailed("Current must be a managed symlink")
        return None
    try:
        directory = pointer.resolve(strict=True)
    except OSError:
        raise InstallFailed("Broken current pointer") from None
    if directory.parent != (Path(root) / "versions").resolve():
        raise InstallFailed("Unmanaged current pointer")
    return directory


def switch(root, directory):
    fd, name = tempfile.mkstemp(prefix=".current-", dir=root)
    os.close(fd)
    os.unlink(name)
    try:
        os.symlink(str(directory.relative_to(root)), name)
        os.replace(name, root / "current")
        fsync_dir(root)
    finally:
        Path(name).unlink(missing_ok=True)


def receipt(directory, pin):
    m = validate_bundle(directory, pin)
    return {
        "status": "verified",
        "version": m["version"],
        "manifest_sha256": pin,
        "artifact_sha256": m["sha256"],
        "schema_version": m["schema_version"],
        "builder_version": m["builder_version"],
    }


def verify(root, pin=None):
    directory = current_directory(Path(root))
    if directory is None:
        return {"status": "not_installed"}
    stored_pin = directory.name.rsplit(".", 1)[-1]
    return receipt(directory, pin or stored_pin)


def install(root, pin, assets):
    checksum(pin)
    with locked(root) as root:
        current = current_directory(root)
        if current is not None and current.name.endswith("." + pin):
            result = receipt(current, pin)
            return {**result, "status": "already_current"}
        with tempfile.TemporaryDirectory(prefix=".download-", dir=root / "versions") as temporary:
            stage = Path(temporary)
            assets.fetch(MANIFEST, stage / MANIFEST, MAX_MANIFEST)
            m = parse_manifest((stage / MANIFEST).read_bytes(), pin)
            assets.fetch(ARTIFACT, stage / ARTIFACT, m["bytes"])
            assets.fetch(NOTICE, stage / NOTICE, m["notice_bytes"])
            validate_bundle(stage, pin)
            stage.chmod(0o755)
            fsync_dir(stage)
            directory = root / "versions" / (m["version"] + "." + pin)
            if directory.is_symlink():
                raise InstallFailed("Generation must be a managed directory")
            if directory.exists():
                validate_bundle(directory, pin)
            else:
                os.rename(stage, directory)
                fsync_dir(root / "versions")
            result = receipt(directory, pin)
            switch(root, directory)
            return {**result, "status": "installed"}


def rollback(root, tag, pin):
    checksum(pin)
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,150}", tag):
        raise DistributionError("Invalid version")
    with locked(root) as root:
        directory = root / "versions" / (tag + "." + pin)
        if directory.is_symlink():
            raise InstallFailed("Generation must be a managed directory")
        result = receipt(directory, pin)
        if result["version"] != tag:
            raise DistributionError("Version mismatch")
        current_directory(root)
        switch(root, directory)
        return {**result, "status": "installed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("package")
    inputs = p.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path)
    inputs.add_argument("--artifact", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--revision", default="r1")
    p.add_argument("--source-kind", choices=("subset", "compressed"), default="subset")
    p.add_argument("--snapshot-json", type=Path)
    p = commands.add_parser("validate")
    p.add_argument("--assets", type=Path, required=True)
    p.add_argument("--manifest-sha256", required=True)
    p = commands.add_parser("install")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--manifest-sha256", required=True)
    sources = p.add_mutually_exclusive_group(required=True)
    sources.add_argument("--release-url")
    sources.add_argument("--source-dir", type=Path)
    p = commands.add_parser("verify")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--manifest-sha256")
    p = commands.add_parser("rollback")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "package":
            result = package(
                args.output,
                artifact=args.artifact,
                source=args.source,
                source_kind=args.source_kind,
                revision=args.revision,
                snapshot=json.loads(args.snapshot_json.read_text()) if args.snapshot_json else None,
            )
        elif args.command == "validate":
            m = validate_bundle(args.assets, args.manifest_sha256)
            result = {
                "status": "verified",
                "version": m["version"],
                "manifest_sha256": args.manifest_sha256,
            }
        elif args.command == "install":
            result = install(
                args.root,
                args.manifest_sha256,
                Assets(release_url=args.release_url, source_dir=args.source_dir),
            )
        elif args.command == "verify":
            result = verify(args.root, args.manifest_sha256)
        else:
            result = rollback(args.root, args.version, args.manifest_sha256)
        print(json.dumps(result, sort_keys=True))
    except DistributionError as exc:
        print(json.dumps({"status": exc.status, "error": str(exc)}))
        raise SystemExit(exc.code) from None
    except sqlite3.Error:
        print(json.dumps({"status": "verification_failed", "error": "SQLite validation failed"}))
        raise SystemExit(2) from None
    except (OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                {
                    "status": "install_failed",
                    "error": "Local operation failed; verify the current selection before retrying",
                }
            )
        )
        raise SystemExit(5) from None


if __name__ == "__main__":
    main()
