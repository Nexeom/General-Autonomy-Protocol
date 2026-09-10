"""Archive determinism, source selection and checksum failure behavior."""

import gzip
import hashlib
import io
import tarfile

import pytest

from tools.build_release import (
    collect_source_files, compare_artifacts, normalize_sdist, verify_checksums,
)


def make_archive(path, files, *, timestamp, owner):
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.name, fileobj=raw, mode="wb", mtime=timestamp) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, data in files:
                    member = tarfile.TarInfo("gap_kernel-0.3.0a1/" + name)
                    member.size = len(data)
                    member.mtime = timestamp
                    member.uid = member.gid = owner
                    member.uname = member.gname = str(owner)
                    member.mode = 0o600 if owner == 1 else 0o664
                    archive.addfile(member, io.BytesIO(data))


def test_sdist_normalization_removes_host_time_and_order_variations(tmp_path):
    first, second = tmp_path / "one.tar.gz", tmp_path / "two.tar.gz"
    files = [("README.md", b"release source"), ("gap_kernel/__init__.py", b"__version__ = 'alpha'\n")]
    make_archive(first, files, timestamp=100, owner=1)
    make_archive(second, reversed(files), timestamp=200, owner=2)
    assert first.read_bytes() != second.read_bytes()
    normalize_sdist(first, 1234567890)
    normalize_sdist(second, 1234567890)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        for name, payload in files:
            entry = archive.getmember("gap_kernel-0.3.0a1/" + name)
            assert archive.extractfile(entry).read() == payload
            assert (entry.mtime, entry.uid, entry.gid, entry.mode) == (1234567890, 0, 0, 0o644)


def test_source_snapshot_includes_rebuild_inputs_and_excludes_local_credentials(tmp_path):
    public = ["pyproject.toml", "MANIFEST.in", "gap_kernel/__init__.py", "docs/RELEASE.md",
              "examples/demo.py", "deploy/Dockerfile", "requirements/build-py312.txt",
              "tests/test_gateway.py", "tools/build_release.py"]
    private = ["secrets/issuer.pem", ".gap-demo/operator/approver.json",
               "examples/secrets/credentials.txt", "deploy/.env", "deploy/tool-token",
               "tests/kernel_identity.json", "gap_kernel/__pycache__/module.pyc",
               "docs/.gap-local/token.txt", "docs/output/build-manifest.json"]
    for name in public + private:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    selected = {path.as_posix() for path in collect_source_files(tmp_path, tmp_path / "docs/output")}
    assert set(public) <= selected
    assert not set(private) & selected


def test_artifact_comparison_refuses_a_different_second_build(tmp_path):
    first, second = tmp_path / "run-1", tmp_path / "run-2"
    for directory in (first, second):
        directory.mkdir()
        (directory / "gap_kernel.whl").write_bytes(b"wheel fixture")
        (directory / "gap_kernel.tar.gz").write_bytes(b"source fixture")
    assert len(compare_artifacts(first, second)) == 2
    (second / "gap_kernel.whl").write_bytes(b"changed wheel")
    with pytest.raises(ValueError, match="differ"):
        compare_artifacts(first, second)


def test_checksum_verification_refuses_corruption_and_paths_outside_output(tmp_path):
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"original")
    checksum = hashlib.sha256(b"original").hexdigest()
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{checksum}  artifact.whl\n")
    assert verify_checksums(tmp_path) == 1
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_checksums(tmp_path)
    sums.write_text(f"{checksum}  ../outside.whl\n")
    with pytest.raises(ValueError, match="invalid checksum entry"):
        verify_checksums(tmp_path)


def test_sdist_normalization_refuses_traversal_before_replacing_archive(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    make_archive(archive, [("../../outside", b"do not extract")], timestamp=100, owner=1)
    original = archive.read_bytes()
    with pytest.raises(ValueError, match="unsafe sdist member"):
        normalize_sdist(archive, 1234567890)
    assert archive.read_bytes() == original
