"""
EA (Electricity Authority) Bulk Dataset Downloader
====================================================
Downloads Generation Output, Nodal Prices & Volumes, and Retail Prices
from the EA's public Azure Blob Storage for years 2018 to present.

Run this from your project root:
    python extract/download_ea_data.py

Requirements:
    pip install requests tqdm

What gets downloaded (into ./raw_data/):
    raw_data/
    ├── generation/     ← YYYYMM_Generation_MD.csv  (monthly, half-hourly by station)
    ├── wholesale_prices/  ← nodal prices & volumes (monthly)
    └── retail_prices/  ← regional retail price CSVs (small, just a couple files)
"""

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from tqdm import tqdm

# ── Azure Blob Storage config ──────────────────────────────────────────────────
BASE_URL = "https://emidatasets.blob.core.windows.net/publicdata"
SAS_TOKEN = "?sv=2021-10-04&si=publicdata&sr=c&sig=f034UWz1xmMbk89jd76zY0M%2BwycFDhhumejUrjqlfIw%3D"

# ── Date range ─────────────────────────────────────────────────────────────────
START_YEAR = 2018
END_YEAR = datetime.now().year

# ── Local output paths ─────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent.parent / "raw_data"

DATASET_PATHS = {
    "generation":        "Datasets/Wholesale/Generation/Generation_MD",
    "wholesale_prices":  "Datasets/Wholesale/DispatchAndPricing/NodalPricesAndVolumes",
    "retail_prices":     "Datasets/Retail/RegionalPowerPrices",
}


def list_blobs(prefix: str) -> list[dict]:
    """List all blobs under a given prefix using the Azure Blob XML listing API."""
    blobs = []
    marker = None

    while True:
        url = f"{BASE_URL}{SAS_TOKEN}&restype=container&comp=list&prefix={prefix}"
        if marker:
            url += f"&marker={marker}"

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)

        for blob in root.iter("Blob"):
            name = blob.find("Name").text
            size_el = blob.find(".//Size")
            size = int(size_el.text) if size_el is not None else 0
            blobs.append({"name": name, "size": size})

        next_marker = root.findtext("NextMarker")
        if next_marker:
            marker = next_marker
        else:
            break

    return blobs


def filter_by_year(blobs: list[dict], start: int, end: int) -> list[dict]:
    """
    Filter blobs to only include files whose name contains a year in [start, end].
    EA generation files are named like: 201801_Generation_MD.csv
    Nodal prices files are named like:  20180101_NodaIPricesAndVolumes.csv
    """
    filtered = []
    for blob in blobs:
        filename = blob["name"].split("/")[-1]
        # Extract the year from the first 4 characters of the filename (YYYY...)
        try:
            file_year = int(filename[:4])
            if start <= file_year <= end:
                filtered.append(blob)
        except (ValueError, IndexError):
            pass  # skip files that don't start with a year
    return filtered


def download_file(blob: dict, dest_dir: Path, overwrite: bool = False) -> tuple[str, bool]:
    """Download a single blob to dest_dir. Returns (filename, was_skipped)."""
    filename = blob["name"].split("/")[-1]
    dest_path = dest_dir / filename

    if not overwrite and dest_path.exists() and dest_path.stat().st_size == blob["size"]:
        return filename, True  # already downloaded, skip

    url = f"{BASE_URL}/{blob['name']}{SAS_TOKEN}"

    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        return filename, False
    except Exception as e:
        print(f"\n  ✗ Failed {filename}: {e}")
        return filename, False


def download_dataset(name: str, blob_prefix: str, dest_dir: Path, workers: int = 8):
    """List, filter, and bulk-download an EA dataset."""
    print(f"\n{'='*60}")
    print(f"Dataset: {name}")
    print(f"  Listing blobs under: {blob_prefix}")

    try:
        all_blobs = list_blobs(blob_prefix)
    except Exception as e:
        print(f"  ✗ Could not list blobs: {e}")
        print(f"  → The folder path may be slightly different. Check the EA website.")
        print(f"    Try browsing: {BASE_URL}/{blob_prefix}{SAS_TOKEN}")
        return

    # For retail prices, we want all files (there are only a few)
    if name == "retail_prices":
        blobs_to_download = all_blobs
    else:
        blobs_to_download = filter_by_year(all_blobs, START_YEAR, END_YEAR)

    print(f"  Found {len(all_blobs)} total files, {len(blobs_to_download)} in range {START_YEAR}–{END_YEAR}")

    if not blobs_to_download:
        print("  → No files matched. The prefix path may need adjusting — see note above.")
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    downloaded = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_file, blob, dest_dir): blob
            for blob in blobs_to_download
        }
        with tqdm(total=len(blobs_to_download), desc=f"  {name}", unit="file") as pbar:
            for future in as_completed(futures):
                filename, was_skipped = future.result()
                if was_skipped:
                    skipped += 1
                else:
                    downloaded += 1
                pbar.update(1)

    print(f"  ✓ Downloaded: {downloaded}  |  Skipped (already exists): {skipped}")


def discover_paths():
    """Probe the blob storage to find the actual folder structure."""
    prefixes_to_check = [
        "Datasets/Wholesale/Generation/",
        "Datasets/Wholesale/DispatchAndPricing/",
        "Datasets/Retail/",
    ]

    for prefix in prefixes_to_check:
        print(f"\n=== {prefix} ===")
        url = (
            f"{BASE_URL}{SAS_TOKEN}"
            f"&restype=container&comp=list"
            f"&prefix={prefix}"
            f"&delimiter=/"
        )
        resp = requests.get(url, timeout=15)
        root = ET.fromstring(resp.text)
        for p in root.iter("BlobPrefix"):
            print("  FOLDER:", p.find("Name").text)
        for b in root.iter("Blob"):
            print("  FILE:  ", b.find("Name").text)


def main():
    print("EA Bulk Dataset Downloader")
    print(f"Date range: {START_YEAR} – {END_YEAR}")
    print(f"Output dir: {OUTPUT_DIR.resolve()}")

    # Uncomment this to discover actual folder paths before downloading:
    #discover_paths()
    #return

    for name, prefix in DATASET_PATHS.items():
        dest = OUTPUT_DIR / name
        download_dataset(name, prefix, dest)

    print(f"\n{'='*60}")
    print("Done! Files saved to:", OUTPUT_DIR.resolve())
    print("\nNext step: run  python extract/load_to_postgres.py  to load into your DB.")


if __name__ == "__main__":
    main()
