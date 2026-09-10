# Preparing the 0.3.0a1 alpha release

The repository already has a published `v0.2.0-alpha` tag at commit `c33e403`.
The candidate described here is **0.3.0a1**, with a proposed future tag
`v0.3.0a1`. These instructions prepare and verify local artifacts. They do not
create a tag, upload to a package index, or establish that this candidate has
been released or independently audited.

## Build environment

Use Python **3.12** and a fresh environment dedicated to building. The hashed
`requirements/build-py312.txt` lock includes exact build-tool versions and
platform markers. The separate `requirements/alpha-py312.txt` lock contains the
runtime and development environment; it is not needed just to build artifacts.

From the repository root on Linux or macOS:

```sh
python3.12 -m venv ../gap-build-venv
../gap-build-venv/bin/python -m pip install --require-hashes -r requirements/build-py312.txt
../gap-build-venv/bin/python tools/build_release.py --output-dir ../gap-release-0.3.0a1
../gap-build-venv/bin/python tools/build_release.py --verify ../gap-release-0.3.0a1
```

From the repository root in Windows PowerShell:

```powershell
py -3.12 -m venv ..\gap-build-venv
..\gap-build-venv\Scripts\python.exe -m pip install --require-hashes -r requirements/build-py312.txt
..\gap-build-venv\Scripts\python.exe tools\build_release.py --output-dir ..\gap-release-0.3.0a1
..\gap-build-venv\Scripts\python.exe tools\build_release.py --verify ..\gap-release-0.3.0a1
```

Installing the lock is the network-dependent step. The build helper performs no
dependency installation, invokes the installed build tools with `--no-isolation`,
and disables package-index access in their environment. Its output directory
must be new or empty; existing artifacts are never overwritten.

The default `SOURCE_DATE_EPOCH` is the current source commit's timestamp. Supply
`--source-date-epoch <integer>` to use an explicitly recorded timestamp. Building
from an unpacked source archive requires this argument because the archive has
no Git metadata. Use `source_date_epoch` from the original build manifest.

## What is verified

The helper captures one public-source snapshot and builds it in two separate
source and output directories. `MANIFEST.in` includes the package, documentation,
examples, deployment definitions, dependency locks, tests, and build helper in
the source distribution. Wheels contain the Python package normally. The
snapshot excludes generated state, caches, `.gap-*` directories, secret
directories, provisioned credentials, and build output. Source symlinks are
refused. This filter is not a general secret scanner; review the public source
before releasing it.

For source archives, the helper preserves file bytes while normalizing member
ordering, file modes, owners, tar timestamps, and the gzip timestamp. Wheel
timestamps are controlled by `SOURCE_DATE_EPOCH`. It then requires matching
SHA256 values for both independently built wheels and both normalized source
archives. A mismatch fails the command and leaves build logs for inspection.

Successful output contains:

- `run-1/` and `run-2/`: matching wheel and source archive, plus each build log.
- `build-manifest.json`: source HEAD, dirty status, a hashed source-file inventory,
  snapshot digest, epoch, Python/platform details, build-lock digest, installed
  build-tool versions, and artifact hashes.
- `SHA256SUMS`: hashes for the manifest and artifacts in both runs.

`--verify` checks the listed files against `SHA256SUMS`. A changed or missing
artifact fails verification. The checksums are not a signature: anyone who can
replace both an artifact and the checksum list can rewrite them together.

The reproducibility claim is deliberately limited to **two local builds from
the captured snapshot under the recorded toolchain and platform**. It does not
establish byte-identical output across operating systems, Python versions, or
different toolchains. The helper checks installed versions against the lock;
hash-verified package installation is performed by the separate pip command.

## Source state and release decision

A dirty checkout is permitted for development, but the manifest reports
`dirty: true` and identifies its contents by the source snapshot digest. Such
artifacts are working-tree snapshots. Their reproducibility does not mean an
immutable, tagged release is ready. An unpacked archive records null Git HEAD
and dirty status, alongside its captured file inventory.

Before a maintainer publishes `v0.3.0a1`, review and commit the intended source,
run the required tests and boundary checks, and rerun this build from the clean
candidate commit. Check that `source.head` is the intended commit,
`source.dirty` is false, and artifact hashes match. Retain the manifest and
checksums with the release assets. No build step here publishes automatically.

The candidate remains an alpha reference implementation. Successful packaging
and local reproducibility checks do not establish deployment security, general
agent safety, regulatory compliance, or an external security review. See
[the threat model](THREAT_MODEL.md), [known gaps](KNOWN_GAPS.md), and
[evaluation notes](EVALUATION.md) for the scope of available evidence.
