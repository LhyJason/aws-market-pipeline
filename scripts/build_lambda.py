"""Assemble a Lambda package directory: a service's src/ plus its Linux dependencies.

Terraform's archive_file cannot run pip, so services with native dependencies
(the processor bundles DuckDB) are built here first and Terraform zips the
result. The wheels are fetched for the Lambda runtime's platform, not this
machine's, so this works the same on Windows, macOS and CI:

    python scripts/build_lambda.py processor --arch x86_64   # LocalStack
    python scripts/build_lambda.py processor --arch arm64    # AWS (dev)

Output: .build/<service>-<arch>/ at the repository root (gitignored).

The architecture must be the one the function actually runs on. LocalStack
runs functions on the Docker host's architecture, which is x86_64 on most
Windows and Intel machines; AWS runs dev on arm64 (Graviton).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.12"  # matches runtime = "python3.12" in Terraform
# Lambda's Amazon Linux 2023 ships glibc 2.34. pip matches --platform tags
# exactly, so list every manylinux level that runtime can load.
GLIBC_LEVELS = ("2_34", "2_28", "2_17")
MACHINES = {"x86_64": "x86_64", "arm64": "aarch64"}


def build(service: str, arch: str, out: Path) -> Path:
    service_dir = ROOT / "services" / service
    src = service_dir / "src"
    if not src.is_dir():
        raise SystemExit(f"no such service: {src}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    requirements = service_dir / "requirements.txt"
    if requirements.is_file():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-compile",
                "--requirement",
                str(requirements),
                "--target",
                str(out),
                *(f"--platform=manylinux_{level}_{MACHINES[arch]}" for level in GLIBC_LEVELS),
                "--implementation",
                "cp",
                "--python-version",
                PYTHON_VERSION,
                "--only-binary=:all:",
            ],
            check=True,
        )

    shutil.copytree(src, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for cache in out.rglob("__pycache__"):
        shutil.rmtree(cache)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("service", help="directory name under services/, e.g. processor")
    parser.add_argument("--arch", choices=sorted(MACHINES), required=True)
    parser.add_argument("--out", type=Path, help="default: .build/<service>-<arch>")
    args = parser.parse_args(argv)

    out = args.out or ROOT / ".build" / f"{args.service}-{args.arch}"
    size_mb = sum(f.stat().st_size for f in build(args.service, args.arch, out).rglob("*")) / 1e6
    print(f"built {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
