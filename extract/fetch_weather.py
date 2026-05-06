"""
Smoke test: pull a small slice of Open-Meteo historical weather data
for the major NZ hydro catchments. One week of recent data, daily resolution.

Goal: validate the API response shape and the multi-location loop before
scaling up to a longer date range and wiring into Postgres.

Run: python fetch_weather.py
"""

import json
from pathlib import Path

import requests
import pandas as pd


# --- Config -----------------------------------------------------------------
# Major NZ hydro catchments. Coordinates are approximate centroids of each lake.
LOCATIONS = [
    {"name": "lake_pukaki",    "lat": -44.07, "lon": 170.14},
    {"name": "lake_tekapo",    "lat": -43.88, "lon": 170.52},
    {"name": "lake_ohau",      "lat": -44.25, "lon": 169.85},
    {"name": "lake_hawea",     "lat": -44.55, "lon": 169.27},
    {"name": "lake_manapouri", "lat": -45.50, "lon": 167.35},
    {"name": "lake_te_anau",   "lat": -45.20, "lon": 167.72},
    {"name": "lake_taupo",     "lat": -38.78, "lon": 175.95},
]

# One week is plenty for a smoke test. Extend the range once the pipeline is solid.
START_DATE = "2024-01-01"
END_DATE = "2025-12-31"

# Variables relevant to hydro inflow / pricing correlation.
# See https://open-meteo.com/en/docs/historical-weather-api for the full list.
DAILY_VARS = [
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
]

# Cache raw JSON locally so we can iterate on parsing without re-hitting the API.
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

OUTPUT_CSV = Path(f"weather_{START_DATE}_{END_DATE}.csv")


# --- Fetch ------------------------------------------------------------------
def fetch_weather(location: dict) -> dict:
    cache_file = CACHE_DIR / f"{location['name']}_{START_DATE}_{END_DATE}.json"
    if cache_file.exists():
        print(f"  cached    {location['name']}")
        return json.loads(cache_file.read_text())

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": location["lat"],
        "longitude": location["lon"],
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Pacific/Auckland",
    }
    print(f"  fetching  {location['name']}")
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data, indent=2))
    return data


def to_dataframe(data: dict, location_name: str) -> pd.DataFrame:
    df = pd.DataFrame(data["daily"])
    df["time"] = pd.to_datetime(df["time"])
    df.insert(0, "location", location_name)
    return df


# --- Inspect + transform ----------------------------------------------------
def main() -> None:
    print(f"Pulling {len(LOCATIONS)} locations from {START_DATE} to {END_DATE}")
    frames = []
    units = None
    for loc in LOCATIONS:
        data = fetch_weather(loc)
        units = units or data.get("daily_units")
        frames.append(to_dataframe(data, loc["name"]))

    df = pd.concat(frames, ignore_index=True)

    # Eyeball this on first run — catches surprises like cm vs mm.
    print(f"\nDaily units: {units}")
    print(f"Shape: {df.shape}")
    print(f"Date range: {df['time'].min().date()} to {df['time'].max().date()}")

    print("\nRows per location (should all be equal):")
    print(df["location"].value_counts().to_string())

    print("\nHead:")
    print(df.head().to_string())

    print("\nPer-location precipitation totals over the window:")
    totals = df.groupby("location")["precipitation_sum"].sum().round(1)
    print(totals.to_string())

    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()