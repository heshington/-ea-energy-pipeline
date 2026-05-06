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
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from tqdm import tqdm

load_dotenv()

# ── Database config ────────────────────────────────────────────────────────────

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "energy_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

RAW_DATA_DIR = Path(__file__).parent.parent / "raw_data"


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
        load_mw         NUMERIC,
        generation_mw   NUMERIC,
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
    Convert wide generation format from one row per station/day with TP columns
    into one row per station/day/trading period.

    Input grain:
        station × day

    Output grain:
        station × day × trading_period
    """
    df.columns = df.columns.str.lower()

    tp_cols = [c for c in [f"tp{i}" for i in range(1, 51)] if c in df.columns]

    id_cols = [
        "site_code",
        "poc_code",
        "nwk_code",
        "gen_code",
        "fuel_code",
        "tech_code",
        "trading_date",
    ]

    missing_id_cols = [c for c in id_cols if c not in df.columns]
    if missing_id_cols:
        raise ValueError(f"Missing expected generation columns: {missing_id_cols}")

    long = df.melt(
        id_vars=id_cols,
        value_vars=tp_cols,
        var_name="trading_period_str",
        value_name="output_kw",
    )

    long["trading_period"] = (
        long["trading_period_str"]
        .str.replace("tp", "", regex=False)
        .astype(int)
    )

    long = long.dropna(subset=["output_kw"])
    long = long[long["output_kw"] != ""]

    long["output_kw"] = pd.to_numeric(long["output_kw"], errors="coerce")
    long["trading_date"] = pd.to_datetime(long["trading_date"], errors="coerce").dt.date

    long = long.dropna(subset=["trading_date", "trading_period", "output_kw"])

    return long[
        [
            "site_code",
            "poc_code",
            "nwk_code",
            "gen_code",
            "fuel_code",
            "tech_code",
            "trading_date",
            "trading_period",
            "output_kw",
        ]
    ]


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

            long_df.to_sql(
                "_gen_staging",
                engine,
                schema="raw",
                if_exists="replace",
                index=False,
                method="multi",
                chunksize=5000,
            )

            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO raw.generation_output
                        (
                            site_code,
                            poc_code,
                            nwk_code,
                            gen_code,
                            fuel_code,
                            tech_code,
                            trading_date,
                            trading_period,
                            output_kw
                        )
                    SELECT
                        site_code,
                        poc_code,
                        nwk_code,
                        gen_code,
                        fuel_code,
                        tech_code,
                        trading_date,
                        trading_period,
                        output_kw
                    FROM raw._gen_staging
                    ON CONFLICT (poc_code, trading_date, trading_period) DO NOTHING;
                """))
                conn.commit()

        except Exception as e:
            print(f"\n  ✗ Error loading {filepath.name}: {e}")

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS raw._gen_staging;"))
        conn.commit()

    print("  ✓ Generation data loaded.")


def load_wholesale_prices(engine, data_dir: Path):
    """
    Load nodal prices & volumes CSVs into raw.wholesale_prices.

    Important:
        EA raw columns LoadMegawatts and GenerationMegawatts are MW, not MWh.

    Therefore this raw table stores:
        load_mw
        generation_mw

    The MWh conversion should happen in dbt staging:
        load_mwh = load_mw * 0.5
        generation_mwh = generation_mw * 0.5
    """
    files = sorted(data_dir.glob("*.csv"))

    if not files:
        print(f"  No wholesale price files found in {data_dir}")
        return

    sample = pd.read_csv(files[0], nrows=3)
    print(f"  Wholesale prices columns: {list(sample.columns)}")
    print(f"  Loading {len(files)} wholesale price files...")

    col_map = {
        "TradingDate": "trading_date",
        "TradingPeriodNumber": "trading_period",
        "PointOfConnectionCode": "node",
        "DollarsPerMegawattHour": "price",
        "LoadMegawatts": "load_mw",
        "GenerationMegawatts": "generation_mw",
    }

    keep_cols = [
        "trading_date",
        "trading_period",
        "node",
        "price",
        "load_mw",
        "generation_mw",
    ]

    for filepath in tqdm(files, desc="  wholesale_prices", unit="file"):
        try:
            df = pd.read_csv(filepath, low_memory=False)

            df = df.rename(
                columns={raw: clean for raw, clean in col_map.items() if raw in df.columns}
            )

            available_cols = [c for c in keep_cols if c in df.columns]
            df = df[available_cols]

            if "trading_date" not in df.columns:
                print(f"\n  ✗ Skipping {filepath.name} — no trading_date column found")
                continue

            required_cols = ["trading_date", "trading_period", "node", "price"]
            missing_required_cols = [c for c in required_cols if c not in df.columns]

            if missing_required_cols:
                print(
                    f"\n  ✗ Skipping {filepath.name} — missing required columns: "
                    f"{missing_required_cols}"
                )
                continue

            df["trading_date"] = pd.to_datetime(
                df["trading_date"], errors="coerce"
            ).dt.date

            df["trading_period"] = pd.to_numeric(
                df["trading_period"], errors="coerce"
            ).astype("Int64")

            df["price"] = pd.to_numeric(df["price"], errors="coerce")

            if "load_mw" in df.columns:
                df["load_mw"] = pd.to_numeric(df["load_mw"], errors="coerce")
            else:
                df["load_mw"] = None

            if "generation_mw" in df.columns:
                df["generation_mw"] = pd.to_numeric(df["generation_mw"], errors="coerce")
            else:
                df["generation_mw"] = None

            df = df.dropna(subset=["trading_date", "trading_period", "node", "price"])

            df.to_sql(
                "_wp_staging",
                engine,
                schema="raw",
                if_exists="replace",
                index=False,
                method="multi",
                chunksize=5000,
            )

            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO raw.wholesale_prices
                        (
                            trading_date,
                            trading_period,
                            node,
                            price,
                            load_mw,
                            generation_mw
                        )
                    SELECT
                        trading_date,
                        trading_period,
                        node,
                        price,
                        load_mw,
                        generation_mw
                    FROM raw._wp_staging
                    ON CONFLICT (trading_date, trading_period, node) DO NOTHING;
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
    Load retail price CSVs.

    These are small summary files. Stored mostly as-is with filename for traceability.
    dbt can handle business-facing column mapping later.
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

            df["raw_filename"] = filepath.name

            df.to_sql(
                "retail_prices_raw",
                engine,
                schema="raw",
                if_exists="append",
                index=False,
                method="multi",
            )

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
        "generation": (load_generation, RAW_DATA_DIR / "generation"),
        "wholesale_prices": (load_wholesale_prices, RAW_DATA_DIR / "wholesale_prices"),
        "retail_prices": (load_retail_prices, RAW_DATA_DIR / "retail_prices"),
    }

    selected_loaders = loaders if dataset == "all" else {dataset: loaders[dataset]}

    for name, (loader_fn, data_dir) in selected_loaders.items():
        print(f"\n{'=' * 60}")
        print(f"Loading: {name}")
        loader_fn(engine, data_dir)

    print(f"\n{'=' * 60}")
    print("All done! Data is in the raw schema in your Postgres database.")
    print("Next: cd dbt_project && dbt run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dataset",
        choices=["all", "generation", "wholesale_prices", "retail_prices"],
        default="all",
        help="Which dataset to load (default: all)",
    )

    args = parser.parse_args()
    main(args.dataset)