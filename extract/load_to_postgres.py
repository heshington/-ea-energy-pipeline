"""
EA Dataset Loader → PostgreSQL
================================
Reads downloaded CSVs from raw_data/, unpivots the generation data from
wide (TP1-TP48 columns) to long format, and loads everything into Postgres.

Run after download_ea_data.py:
    python extract/load_to_postgres.py

Or load a single dataset:
    python extract/load_to_postgres.py --dataset generation
    python extract/load_to_postgres.py --dataset wholesale_prices
    python extract/load_to_postgres.py --dataset retail_prices

Requirements:
    pip install pandas psycopg2-binary sqlalchemy python-dotenv tqdm
"""

import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from tqdm import tqdm

load_dotenv()

# ── Database config (reads from .env) ─────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "5432")
DB_NAME     = os.getenv("DB_NAME", "energy_db")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

RAW_DATA_DIR = Path(__file__).parent.parent / "raw_data"

TRADING_PERIOD_COLS = [f"TP{i}" for i in range(1, 51)]  # TP1 … TP50


# ── Schema setup ───────────────────────────────────────────────────────────────

CREATE_SCHEMAS = """
    CREATE SCHEMA IF NOT EXISTS raw;
"""

CREATE_GENERATION_TABLE = """
    CREATE TABLE IF NOT EXISTS raw.generation_output (
        id              SERIAL PRIMARY KEY,
        site_code       TEXT,
        poc_code        TEXT,
        nwk_code        TEXT,
        gen_code        TEXT,
        fuel_code       TEXT,
        tech_code       TEXT,
        trading_date    DATE NOT NULL,
        trading_period  SMALLINT NOT NULL,
        output_kw       NUMERIC,
        UNIQUE (poc_code, trading_date, trading_period)
    );
    CREATE INDEX IF NOT EXISTS idx_gen_date
        ON raw.generation_output (trading_date);
    CREATE INDEX IF NOT EXISTS idx_gen_fuel
        ON raw.generation_output (fuel_code);
    CREATE INDEX IF NOT EXISTS idx_gen_code
        ON raw.generation_output (gen_code);
"""

CREATE_WHOLESALE_PRICES_TABLE = """
    CREATE TABLE IF NOT EXISTS raw.wholesale_prices (
        id              SERIAL PRIMARY KEY,
        trading_date    DATE NOT NULL,
        trading_period  SMALLINT NOT NULL,
        node            TEXT,
        price           NUMERIC,
        load_mwh        NUMERIC,
        generation_mwh  NUMERIC,
        UNIQUE (trading_date, trading_period, node)
    );
    CREATE INDEX IF NOT EXISTS idx_wp_date
        ON raw.wholesale_prices (trading_date);
    CREATE INDEX IF NOT EXISTS idx_wp_node
        ON raw.wholesale_prices (node);
"""

CREATE_RETAIL_PRICES_TABLE = """
    CREATE TABLE IF NOT EXISTS raw.retail_prices (
        id              SERIAL PRIMARY KEY,
        region          TEXT,
        network         TEXT,
        period_start    DATE,
        period_end      DATE,
        avg_cost_cents_per_kwh  NUMERIC,
        avg_use_kwh     NUMERIC,
        raw_filename    TEXT
    );
"""


# ── Loaders ────────────────────────────────────────────────────────────────────

def unpivot_generation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert wide format (TP1..TP48 columns) to long format.
    Input:  1 row per station × day, 48 TP columns
    Output: 1 row per station × day × trading period
    """
    # Normalise column names to lowercase to handle format changes across years
    df.columns = df.columns.str.lower()

    tp_cols = [c for c in [f"tp{i}" for i in range(1, 51)] if c in df.columns]

    id_cols = ["site_code", "poc_code", "nwk_code", "gen_code",
               "fuel_code", "tech_code", "trading_date"]

    long = df.melt(
        id_vars=id_cols,
        value_vars=tp_cols,
        var_name="trading_period_str",
        value_name="output_kw"
    )

    # Convert "tp1" → 1, "tp48" → 48
    long["trading_period"] = long["trading_period_str"].str.replace("tp", "").astype(int)

    # Drop empty/null rows (end-of-day DST padding)
    long = long.dropna(subset=["output_kw"])
    long = long[long["output_kw"] != ""]

    long["output_kw"] = pd.to_numeric(long["output_kw"], errors="coerce")
    long["trading_date"] = pd.to_datetime(long["trading_date"]).dt.date

    return long[["site_code", "poc_code", "nwk_code", "gen_code",
                 "fuel_code", "tech_code", "trading_date",
                 "trading_period", "output_kw"]]


def load_generation(engine, data_dir: Path):
    """Load all generation CSVs into raw.generation_output."""
    files = sorted(data_dir.glob("*_Generation_MD.csv"))
    if not files:
        print(f"  No generation files found in {data_dir}")
        return

    print(f"  Loading {len(files)} generation files...")

    for filepath in tqdm(files, desc="  generation", unit="file"):
        try:
            df = pd.read_csv(filepath, dtype=str, low_memory=False)
            long_df = unpivot_generation(df)

            # Upsert using temp table approach to handle duplicates gracefully
            long_df.to_sql("_gen_staging", engine, schema="raw",
                           if_exists="replace", index=False, method="multi", chunksize=5000)

            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO raw.generation_output
                        (site_code, poc_code, nwk_code, gen_code, fuel_code, tech_code,
                         trading_date, trading_period, output_kw)
                    SELECT site_code, poc_code, nwk_code, gen_code, fuel_code, tech_code,
                           trading_date, trading_period, output_kw
                    FROM raw._gen_staging
                    ON CONFLICT (poc_code, trading_date, trading_period) DO NOTHING;
                """))
                conn.commit()

        except Exception as e:
            print(f"\n  ✗ Error loading {filepath.name}: {e}")

    # Clean up staging table
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS raw._gen_staging;"))
        conn.commit()

    print("  ✓ Generation data loaded.")


def load_wholesale_prices(engine, data_dir: Path):
    """
    Load nodal prices & volumes CSVs into raw.wholesale_prices.
    Column names may vary - this handles the most common format.
    Adjust column mapping below if your files differ.
    """
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        print(f"  No wholesale price files found in {data_dir}")
        return

    # Peek at the first file to discover columns
    sample = pd.read_csv(files[0], nrows=3)
    print(f"  Wholesale prices columns: {list(sample.columns)}")
    print(f"  Loading {len(files)} wholesale price files...")

    # Common column name variations in EA nodal price files:
    COL_MAP = {
     "TradingDate":            "trading_date",
    "TradingPeriodNumber":    "trading_period",
    "PointOfConnectionCode":  "node",
    "DollarsPerMegawattHour": "price",
    "LoadMegawatts":          "load_mwh",
    "GenerationMegawatts":    "generation_mwh",
}

    for filepath in tqdm(files, desc="  wholesale_prices", unit="file"):
        try:
            df = pd.read_csv(filepath, low_memory=False)
            df = df.rename(columns={k: v for k, v in COL_MAP.items() if k in df.columns})

            # Only keep columns we have
            keep = [c for c in ["trading_date", "trading_period", "node",
                                 "price", "load_mwh", "generation_mwh"]
                    if c in df.columns]
            df = df[keep]

            if "trading_date" not in df.columns:
                print(f"\n  ✗ Skipping {filepath.name} — no trading_date column found")
                continue

            df["trading_date"] = pd.to_datetime(df["trading_date"], errors="coerce").dt.date

            df.to_sql("_wp_staging", engine, schema="raw",
                      if_exists="replace", index=False, method="multi", chunksize=5000)

            with engine.connect() as conn:
                conn.execute(text("""
                INSERT INTO raw.wholesale_prices
                    (trading_date, trading_period, node, price, load_mwh, generation_mwh)
                SELECT trading_date, trading_period, node,
                    price, load_mwh, generation_mwh
                FROM raw._wp_staging;
            """))
                conn.commit()

        except Exception as e:
            print(f"\n  ✗ Error loading {filepath.name}: {e}")

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS raw._wp_staging;"))
        conn.commit()

    print("  ✓ Wholesale prices loaded.")


def load_retail_prices(engine, data_dir: Path):
    """
    Load retail price CSVs. These are small summary files (a few per year).
    Logs the actual columns so you can adjust the mapping after inspecting.
    """
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        print(f"  No retail price files found in {data_dir}")
        return

    print(f"  Loading {len(files)} retail price file(s)...")

    for filepath in files:
        try:
            df = pd.read_csv(filepath)
            print(f"  {filepath.name} — columns: {list(df.columns)[:8]}...")

            # Store as-is with filename for traceability, let dbt handle column mapping
            df["raw_filename"] = filepath.name
            df.to_sql("retail_prices_raw", engine, schema="raw",
                      if_exists="append", index=False, method="multi")

            print(f"  ✓ Loaded {len(df)} rows from {filepath.name}")

        except Exception as e:
            print(f"  ✗ Error loading {filepath.name}: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def get_engine():
    url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(url)


def setup_schema(engine):
    print("Setting up raw schema and tables...")
    with engine.connect() as conn:
        conn.execute(text(CREATE_SCHEMAS))
        conn.execute(text(CREATE_GENERATION_TABLE))
        conn.execute(text(CREATE_WHOLESALE_PRICES_TABLE))
        conn.execute(text(CREATE_RETAIL_PRICES_TABLE))
        conn.commit()
    print("  ✓ Schema ready.")


def main(dataset: str = "all"):
    engine = get_engine()
    setup_schema(engine)

    loaders = {
        "generation":       (load_generation,       RAW_DATA_DIR / "generation"),
        "wholesale_prices": (load_wholesale_prices,  RAW_DATA_DIR / "wholesale_prices"),
        "retail_prices":    (load_retail_prices,     RAW_DATA_DIR / "retail_prices"),
    }

    to_run = loaders if dataset == "all" else {dataset: loaders[dataset]}

    for name, (loader_fn, data_dir) in to_run.items():
        print(f"\n{'='*60}")
        print(f"Loading: {name}")
        loader_fn(engine, data_dir)

    print(f"\n{'='*60}")
    print("All done! Data is in the raw schema in your Postgres database.")
    print("Next: cd dbt_project && dbt run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        choices=["all", "generation", "wholesale_prices", "retail_prices"],
        default="all",
        help="Which dataset to load (default: all)"
    )
    args = parser.parse_args()
    main(args.dataset)
