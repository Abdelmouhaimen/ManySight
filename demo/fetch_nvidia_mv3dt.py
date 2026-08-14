"""Download and safely extract NVIDIA's optional MV3DT demo assets locally."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL = (
    "https://github.com/NVIDIA/DeepStream/raw/refs/heads/main/"
    "src/apps/reference_apps/deepstream-tracker-3d-multi-view/assets/datasets.zip"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "demo-assets")
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storelens-mv3dt-download-") as temporary:
        archive = Path(temporary) / "datasets.zip"
        print("Downloading the NVIDIA-hosted sample archive. Review NVIDIA's terms before use.")
        with urllib.request.urlopen(URL, timeout=120) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        print(f"Downloaded archive SHA-256: {digest}")
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (destination / member.filename).resolve()
                if destination != target and destination not in target.parents:
                    raise RuntimeError(f"archive contains an unsafe path: {member.filename}")
            bundle.extractall(destination)
    dataset = destination / "datasets" / "mtmc_12cam"
    print(f"Installed locally at: {dataset}")
    print(f"Set STORELENS_DEMO_ASSET_DIR={dataset} when using a different destination.")


if __name__ == "__main__":
    main()
