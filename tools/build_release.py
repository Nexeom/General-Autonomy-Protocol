"""Build and compare two local release snapshots without fetching dependencies.

Install requirements/build-py312.txt first. The build backend runs with
--no-isolation; this helper never installs packages or publishes artifacts.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib


ROOT_FILES = frozenset({
    "pyproject.toml", "MANIFEST.in", "README.md", "LICENSE", "CHANGELOG.md",
    "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", ".gitignore", ".dockerignore",
})
SOURCE_DIRECTORIES = frozenset({
    "gap_kernel", "tests", "docs", "examples", "deploy", "requirements", "tools",
    "action-types", ".github", "evaluation-results",
})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "env", "build", "dist", "secrets", ".secrets",
    "__pycache__", ".pytest_cache", ".hypothesis", ".ruff_cache", ".mypy_cache",
})
SECRET_FILENAMES = frozenset({
    "agent-token", "tool-token", "kernel_identity.json", "trust_root.json",
    "approver.json", "profile-key.json", "evidence-key.json",
})
SECRET_SUFFIXES = frozenset({".db", ".key", ".pem", ".p12", ".pfx"})


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def excluded(relative: Path) -> bool:
    for part in relative.parts:
        if (part in EXCLUDED_DIRECTORIES or part.startswith(".gap-")
                or part.endswith(".egg-info")):
            return True
    name = relative.name
    return (name in SECRET_FILENAMES or name == ".env" or name.startswith(".env.")
            or ".sqlite" in name or relative.suffix in SECRET_SUFFIXES
            or relative.suffix in {".pyc", ".pyo", ".pyd"})


def collect_source_files(source: Path, output: Path | None = None) -> list[Path]:
    """Select public source inputs, never local state, credentials or build output.

    This allowlist mirrors MANIFEST.in. Symlinks are refused so a source snapshot
    cannot follow a link outside the tree into an operator's credentials.
    """
    selected = []
    for entry in sorted(source.iterdir()):
        if entry.name not in ROOT_FILES and entry.name not in SOURCE_DIRECTORIES:
            continue
        candidates = [entry] if entry.is_file() else sorted(entry.rglob("*"))
        if entry.is_symlink():
            raise ValueError(f"source symlinks are not supported: {entry.name}")
        for path in candidates:
            relative = path.relative_to(source)
            if excluded(relative):
                continue
            if output is not None and path.resolve().is_relative_to(output):
                continue
            if path.is_symlink():
                raise ValueError(f"source symlinks are not supported: {relative}")
            if path.is_file():
                selected.append(relative)
    return sorted(selected, key=lambda value: value.as_posix())


def snapshot_source(source: Path, destination: Path, output: Path) -> tuple[str, list[dict]]:
    inventory = []
    for relative in collect_source_files(source, output):
        payload = (source / relative).read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        inventory.append({"path": relative.as_posix(), "bytes": len(payload),
                          "sha256": hashlib.sha256(payload).hexdigest()})
    required = {"pyproject.toml", "MANIFEST.in", "requirements/build-py312.txt"}
    if not required.issubset({entry["path"] for entry in inventory}):
        raise ValueError("source snapshot is missing pyproject.toml, MANIFEST.in or the build lock")
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), inventory


def git_metadata(source: Path) -> dict:
    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(source), *arguments], stderr=subprocess.DEVNULL, text=True,
        ).strip()

    try:
        top_level = Path(git("rev-parse", "--show-toplevel")).resolve()
        # An unpacked sdist inside another repository is not that repository's
        # source commit. Require this exact project root.
        if top_level != source.resolve():
            return {"head": None, "dirty": None, "commit_timestamp": None}
        return {
            "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=all")),
            "commit_timestamp": int(git("show", "-s", "--format=%ct", "HEAD")),
        }
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {"head": None, "dirty": None, "commit_timestamp": None}


def locked_build_versions(lock_file: Path) -> dict[str, str]:
    """Check installed versions; hash-checked installation is a separate step."""
    from packaging.requirements import Requirement

    versions = {}
    for line in lock_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==":
            raise ValueError(f"build lock must use exact versions: {requirement.name}")
        wanted = specifiers[0].version
        installed = importlib.metadata.version(requirement.name)
        if installed != wanted:
            raise ValueError(
                f"{requirement.name}: installed {installed}, build lock requires {wanted}; "
                "install requirements/build-py312.txt in a fresh build environment"
            )
        versions[requirement.name] = installed
    if not {"build", "setuptools", "wheel"}.issubset(versions):
        raise ValueError("build lock must include build, setuptools and wheel")
    return versions


def normalize_sdist(path: Path, epoch: int) -> None:
    """Canonicalize archive metadata; preserve every regular file's bytes.

    Setuptools' gzip timestamp and staged file mtimes can differ between builds.
    Sort members, remove host/user metadata, and fix tar modes and gzip mtime.
    Only regular files and directories are accepted in this source distribution.
    """
    entries = []
    with tarfile.open(path, "r:gz") as archive:
        for original in archive.getmembers():
            name = PurePosixPath(original.name)
            if name.is_absolute() or ".." in name.parts or "\\" in original.name:
                raise ValueError(f"unsafe sdist member: {original.name}")
            if not original.isfile() and not original.isdir():
                raise ValueError(f"unsupported sdist member: {original.name}")
            payload = archive.extractfile(original).read() if original.isfile() else b""
            entries.append((original.name, original.isdir(), payload))
    names = [entry[0] for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("duplicate sdist member names")
    replacement = path.with_name(path.name + ".normalized")
    try:
        with replacement.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for name, directory, payload in sorted(entries):
                        member = tarfile.TarInfo(name)
                        member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
                        member.mode = 0o755 if directory else 0o644
                        member.mtime = epoch
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.size = len(payload)
                        archive.addfile(member, None if directory else io.BytesIO(payload))
        replacement.replace(path)
    finally:
        replacement.unlink(missing_ok=True)


def compare_artifacts(first: Path, second: Path) -> dict[str, str]:
    def artifacts(directory):
        return {path.name: sha256_file(path) for path in directory.iterdir()
                if path.name.endswith((".whl", ".tar.gz"))}

    left, right = artifacts(first), artifacts(second)
    if (len(left) != 2 or sum(name.endswith(".whl") for name in left) != 1
            or sum(name.endswith(".tar.gz") for name in left) != 1):
        raise ValueError("each build must produce exactly one wheel and one sdist")
    if left != right:
        raise ValueError("release artifacts differ between the two builds; inspect the run directories")
    return left


def verify_checksums(output: Path) -> int:
    count = 0
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        relative = PurePosixPath(name)
        if (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
                or relative.is_absolute() or ".." in relative.parts or "\\" in name):
            raise ValueError("invalid checksum entry")
        target = (output / relative).resolve()
        if not target.is_relative_to(output.resolve()):
            raise ValueError("checksum entry escapes output directory")
        if sha256_file(target) != expected:
            raise ValueError(f"checksum mismatch: {name}")
        count += 1
    if not count:
        raise ValueError("checksum list is empty")
    return count


def build_release(source: Path, output: Path, epoch: int | None = None) -> dict:
    source, output = source.resolve(), output.resolve()
    metadata = git_metadata(source)
    epoch_source = "explicit" if epoch is not None else "git_commit_timestamp"
    if epoch is None:
        epoch = metadata["commit_timestamp"]
    if epoch is None:
        raise ValueError("no source commit timestamp; provide --source-date-epoch")
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH must fit the gzip unsigned 32-bit timestamp")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output directory must be new or empty; existing artifacts are not overwritten")
    lock = source / "requirements" / "build-py312.txt"
    versions = locked_build_versions(lock)
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0", TZ="UTC",
                       PIP_NO_INDEX="1", UV_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    # TemporaryDirectory is created only within the caller's resolved, checked
    # output directory. Cleanup cannot target an existing source/workspace path.
    with tempfile.TemporaryDirectory(prefix=".source-snapshots-", dir=output) as temporary:
        scratch = Path(temporary)
        frozen = scratch / "snapshot"
        source_digest, inventory = snapshot_source(source, frozen, output)
        frozen_lock = frozen / "requirements" / "build-py312.txt"
        versions = locked_build_versions(frozen_lock)
        lock_digest = sha256_file(frozen_lock)
        project = tomllib.loads((frozen / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        after_snapshot = git_metadata(source)
        if metadata["head"] != after_snapshot["head"]:
            raise ValueError("source commit changed while the source snapshot was being captured")
        if after_snapshot["dirty"] is True:
            metadata["dirty"] = True
        for run in (1, 2):
            staged = scratch / f"source-{run}"
            shutil.copytree(frozen, staged)
            temporary_files = scratch / f"temporary-{run}"
            temporary_files.mkdir()
            run_environment = dict(environment, TEMP=str(temporary_files),
                                   TMP=str(temporary_files), TMPDIR=str(temporary_files))
            destination = output / f"run-{run}"
            destination.mkdir()
            command = [sys.executable, "-m", "build", "--sdist", "--wheel", "--no-isolation",
                       "--outdir", str(destination), str(staged)]
            with (destination / "build.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(command, env=run_environment, stdout=log,
                                        stderr=subprocess.STDOUT, cwd=staged, check=False)
            if result.returncode:
                raise ValueError(f"build {run} failed; see {destination / 'build.log'}")
            for archive in destination.glob("*.tar.gz"):
                normalize_sdist(archive, epoch)
    artifact_hashes = compare_artifacts(output / "run-1", output / "run-2")
    manifest = {
        "schema_version": 1,
        "release_status": "prepared_locally_not_published",
        "reproducibility": "two_local_builds_match",
        "project": {"name": project["name"], "version": project["version"]},
        "source": {**metadata, "snapshot_sha256": source_digest, "files": inventory},
        "source_date_epoch": epoch,
        "source_date_epoch_origin": epoch_source,
        "python": {"version": sys.version, "implementation": sys.implementation.name,
                   "platform": sys.platform},
        "build_tool_versions": versions,
        "build_lock_sha256": lock_digest,
        "normalization": "sdist member ordering, modes, uid/gid, owner names and tar/gzip timestamps",
        "artifacts": artifact_hashes,
        "limits": [
            "Compares two builds from one captured source snapshot in this Python/platform environment.",
            "Does not establish reproducibility across operating systems or Python/toolchain versions.",
            "Checksums are integrity records, not an independently signed release attestation.",
            "A dirty or unversioned source snapshot is not an immutable tagged release.",
        ],
    }
    manifest_path = output / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums = [f"{sha256_file(manifest_path)}  build-manifest.json"]
    checksums.extend(f"{digest}  run-{run}/{name}"
                     for run in (1, 2) for name, digest in sorted(artifact_hashes.items()))
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    verify_checksums(output)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output-dir", type=Path, help="new or empty artifact directory")
    mode.add_argument("--verify", type=Path, help="verify an existing directory's SHA256SUMS")
    parser.add_argument("--source-date-epoch", type=int,
                        help="override the default source Git commit timestamp")
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify is not None:
            count = verify_checksums(arguments.verify.resolve())
            print(f"Verified {count} artifact/manifest checksums.")
            return 0
        source = Path(__file__).resolve().parents[1]
        manifest = build_release(source, arguments.output_dir, arguments.source_date_epoch)
        print("Wheel and sdist match across two local builds; SHA256SUMS verified.")
        print(f"Source snapshot SHA256: {manifest['source']['snapshot_sha256']}")
        print(f"Source HEAD: {manifest['source']['head']}; dirty: {manifest['source']['dirty']}")
        if manifest["source"]["dirty"] is not False:
            print("This is a dirty or unversioned snapshot. An immutable release remains unprepared.")
        print("Artifacts prepared locally. Nothing was tagged, uploaded or published.")
        return 0
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError, ImportError) as exc:
        print(f"Release preparation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
