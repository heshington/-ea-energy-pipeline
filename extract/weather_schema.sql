-- Weather observations from Open-Meteo historical archive.
-- One row per (location, date). Wide format so daily JOINs against
-- raw.wholesale_prices and raw.generation_output are cheap.
 
CREATE SCHEMA IF NOT EXISTS raw;
 
CREATE TABLE IF NOT EXISTS raw.weather_daily (
    id                    SERIAL      PRIMARY KEY,
    location              TEXT        NOT NULL,
    time                  DATE        NOT NULL,
    precipitation_sum     NUMERIC(6,2),     -- mm, total liquid-equivalent
    rain_sum              NUMERIC(6,2),     -- mm
    snowfall_sum          NUMERIC(6,2),     -- cm of snow depth
    temperature_2m_mean   NUMERIC(5,2),     -- °C
    temperature_2m_max    NUMERIC(5,2),     -- °C
    temperature_2m_min    NUMERIC(5,2),     -- °C
    inserted_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (location, time)
);
 
CREATE INDEX IF NOT EXISTS idx_weather_time
    ON raw.weather_daily (time);
 
CREATE INDEX IF NOT EXISTS idx_weather_location
    ON raw.weather_daily (location);