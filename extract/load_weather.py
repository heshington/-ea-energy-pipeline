"""
Load a weather CSV (produced by fetch_weather.py) into Postgres.

Idempotent — re-running with the same or overlapping data updates rows in
place rather than creating duplicates, so it's safe to re-run after a
re-fetch.

Usage:
    python load_weather.py weather_2024-01-01_2025-12-31.csv

Reads connection details from the project's .env file using the same
variable names as load_to_postgres.py:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

Requires: psycopg2-binary, python-dotenv
    pip install psycopg2-binary python-dotenv
"""

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

# Walks up from this file looking for a .env. Safe to call even if absent.
load_dotenv()


SCHEMA_FILE = Path(__file__).parent / "weather_schema.sql"

# Upsert keyed on (location, time). EXCLUDED is the would-be-inserted row,
# so on a re-run we overwrite the stored values with the latest fetch.
INSERT_SQL = """
    INSERT INTO raw.weather_daily (
        location, time,
        precipitation_sum, rain_sum, snowfall_sum,
        temperature_2m_mean, temperature_2m_max, temperature_2m_min
    )
    VALUES %s
    ON CONFLICT (location, time) DO UPDATE SET
        precipitation_sum    = EXCLUDED.precipitation_sum,
        rain_sum             = EXCLUDED.rain_sum,
        snowfall_sum         = EXCLUDED.snowfall_sum,
        temperature_2m_mean  = EXCLUDED.temperature_2m_mean,
        temperature_2m_max   = EXCLUDED.temperature_2m_max,
        temperature_2m_min   = EXCLUDED.temperature_2m_min,
        inserted_at          = NOW();
"""

# Order matters — must match the column list in INSERT_SQL.
COLUMNS = [
    "location", "time",
    "precipitation_sum", "rain_sum", "snowfall_sum",
    "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
]


def get_conn():
    """Connect using the same env vars as load_to_postgres.py."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "energy_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
    )


def ensure_schema(conn) -> None:
    """Create the table and index if they don't already exist."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text())
    conn.commit()


def load_csv(conn, csv_path: Path) -> int:
    """Upsert all rows from the given CSV into raw.weather_daily."""
    df = pd.read_csv(csv_path, parse_dates=["time"])
    df["time"] = df["time"].dt.date  # store as DATE, not TIMESTAMP

    rows = list(df[COLUMNS].itertuples(index=False, name=None))

    with conn.cursor() as cur:
        execute_values(cur, INSERT_SQL, rows, page_size=500)
    conn.commit()
    return len(rows)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python load_weather.py <path-to-csv>")

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    print(f"Loading {csv_path}")
    with get_conn() as conn:
        ensure_schema(conn)
        n = load_csv(conn, csv_path)
        print(f"Upserted {n} rows into raw.weather_daily")


if __name__ == "__main__":
    main()