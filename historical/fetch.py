"""
Extrae 25 años de datos históricos de Cerro Catedral del archivo ERA5 de Open-Meteo,
más el índice ENSO (ONI) de NOAA, y guarda todo en docs/historical.json para
alimentar el análisis estadístico de la temporada.

Caveats:
- ERA5 tiene grilla de ~31km. El punto efectivo para Catedral (-41.17, -71.45)
  sale a ~1187m de elevación (entre base y mid-mountain), no en el pico (2405m).
  snow_depth y snowfall serán representativos del nivel medio, no del cumbre.
- snowfall en cm de nieve fresca, snow_depth en metros, precipitation en mm.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RESORTS = {
    "catedral": {
        "name": "Cerro Catedral",
        "lat": -41.22,
        "lon": -71.48,
        "elevation_m": 2405,
        "timezone": "America/Argentina/Buenos_Aires",
        # Coords ajustadas para ERA5: punto efectivo a ~2270m (cumbre 2405m).
        # Original (-41.17, -71.45) caía a ~1187m, fuera del ski area principal.
    },
    "vallenevado": {
        "name": "Valle Nevado",
        "lat": -33.34,
        "lon": -70.24,
        "elevation_m": 3025,
        "timezone": "America/Santiago",
        # Coords ajustadas para que ERA5 caiga en mid-upper mountain (~3213m).
        # Coords originales (-33.36, -70.30) caían en valle a 2335m, fuera del ski area.
    },
    "leslenas": {
        "name": "Las Leñas",
        "lat": -35.13,
        "lon": -70.05,
        "elevation_m": 3430,
        "timezone": "America/Argentina/Buenos_Aires",
        # Coords ajustadas a mid-mountain (~2792m). Original (-35.15, -70.07) caía a 2155m.
    },
    "chapelco": {
        "name": "Chapelco",
        "lat": -40.20,
        "lon": -71.31,
        "elevation_m": 1980,
        "timezone": "America/Argentina/Buenos_Aires",
        # Grilla ~1361m, lower-mid mountain
    },
    "cerrobayo": {
        "name": "Cerro Bayo",
        "lat": -40.76,
        "lon": -71.58,
        "elevation_m": 1782,
        "timezone": "America/Argentina/Buenos_Aires",
        # Grilla ~1348m, base/lower-mid
    },
}

# Resort por argumento CLI, default catedral
RESORT_ID = sys.argv[1] if len(sys.argv) > 1 else "catedral"
if RESORT_ID not in RESORTS:
    raise SystemExit(f"Resort '{RESORT_ID}' desconocido. Opciones: {list(RESORTS.keys())}")
CATEDRAL = RESORTS[RESORT_ID]  # mantengo el nombre por compatibilidad

OUTPUT_FILE = os.path.join(BASE_DIR, "..", "docs", f"historical_{RESORT_ID}.json")

YEARS_BACK = 25
TARGET_WEEK_MONTH = 7
TARGET_WEEK_START_DAY = 4
TARGET_WEEK_END_DAY = 10

DAILY_VARS = [
    "snowfall_sum",
    "precipitation_sum",
    "rain_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
]
HOURLY_VARS = [
    "snow_depth",
    "freezing_level_height",
    "temperature_850hPa",
]


def http_get(url, timeout=60, retries=4, backoff=2):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last = e
            wait = backoff ** attempt
            print(f"    retry {attempt+1}/{retries} in {wait}s ({e})", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(wait)
    raise last


def fetch_year(start_date, end_date):
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={CATEDRAL['lat']}&longitude={CATEDRAL['lon']}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&daily={','.join(DAILY_VARS)}"
        f"&hourly={','.join(HOURLY_VARS)}"
        f"&timezone={quote(CATEDRAL['timezone'])}"
    )
    return http_get(url).json()


def safe_avg(lst):
    valid = [x for x in lst if x is not None]
    return round(sum(valid) / len(valid), 2) if valid else None


def safe_max(lst):
    valid = [x for x in lst if x is not None]
    return round(max(valid), 2) if valid else None


def safe_min(lst):
    valid = [x for x in lst if x is not None]
    return round(min(valid), 2) if valid else None


def aggregate_hourly_to_daily(hourly):
    """Agrupa el hourly por fecha local y produce stats diarias."""
    times = hourly["time"]
    snow_depth = hourly["snow_depth"]
    freeze = hourly["freezing_level_height"]
    t850 = hourly["temperature_850hPa"]

    by_date = {}
    for i, t in enumerate(times):
        d = t[:10]
        bucket = by_date.setdefault(d, {"snow_depth": [], "freeze": [], "t850": []})
        bucket["snow_depth"].append(snow_depth[i])
        bucket["freeze"].append(freeze[i])
        bucket["t850"].append(t850[i])

    result = {}
    for d, vals in by_date.items():
        sd = vals["snow_depth"]
        result[d] = {
            "snow_depth_max_m": safe_max(sd),
            "snow_depth_mean_m": safe_avg(sd),
            "snow_depth_end_m": next((round(x, 3) for x in reversed(sd) if x is not None), None),
            "freezing_level_mean_m": safe_avg(vals["freeze"]),
            "freezing_level_min_m": safe_min(vals["freeze"]),
            "freezing_level_max_m": safe_max(vals["freeze"]),
            "temp_850hpa_mean_c": safe_avg(vals["t850"]),
        }
    return result


def parse_season(api_data, year, is_current):
    daily = api_data["daily"]
    hourly_agg = aggregate_hourly_to_daily(api_data["hourly"])

    days = []
    for i, d in enumerate(daily["time"]):
        h = hourly_agg.get(d, {})
        days.append({
            "date": d,
            "snow_cm": daily["snowfall_sum"][i] or 0,
            "precip_mm": daily["precipitation_sum"][i] or 0,
            "rain_mm": daily["rain_sum"][i] or 0,
            "temp_max_c": daily["temperature_2m_max"][i],
            "temp_min_c": daily["temperature_2m_min"][i],
            "temp_mean_c": daily["temperature_2m_mean"][i],
            "wind_max_kmh": daily["wind_speed_10m_max"][i],
            "wind_gust_kmh": daily["wind_gusts_10m_max"][i],
            "snow_depth_max_m": h.get("snow_depth_max_m"),
            "snow_depth_mean_m": h.get("snow_depth_mean_m"),
            "snow_depth_end_m": h.get("snow_depth_end_m"),
            "freezing_level_mean_m": h.get("freezing_level_mean_m"),
            "freezing_level_min_m": h.get("freezing_level_min_m"),
            "freezing_level_max_m": h.get("freezing_level_max_m"),
            "temp_850hpa_mean_c": h.get("temp_850hpa_mean_c"),
        })

    summary = compute_summary(days, year)
    return {"year": year, "is_current": is_current, "daily": days, "summary": summary}


def compute_summary(days, year):
    season_total_snow = round(sum(d["snow_cm"] for d in days), 1)
    season_total_precip = round(sum(d["precip_mm"] for d in days), 1)
    season_total_rain = round(sum(d["rain_mm"] for d in days), 1)
    days_with_snow = sum(1 for d in days if d["snow_cm"] >= 1.0)

    depths = [(d["snow_depth_max_m"], d["date"]) for d in days if d["snow_depth_max_m"] is not None]
    if depths:
        peak_depth, peak_date = max(depths, key=lambda x: x[0])
    else:
        peak_depth, peak_date = None, None

    jul3 = f"{year}-07-03"
    cum_jul3 = round(sum(d["snow_cm"] for d in days if d["date"] <= jul3), 1)

    week_start = f"{year}-{TARGET_WEEK_MONTH:02d}-{TARGET_WEEK_START_DAY:02d}"
    week_end = f"{year}-{TARGET_WEEK_MONTH:02d}-{TARGET_WEEK_END_DAY:02d}"
    week_days = [d for d in days if week_start <= d["date"] <= week_end]

    target_week = None
    if week_days:
        sd_starts = [d["snow_depth_max_m"] for d in week_days if d["date"] == week_start]
        sd_ends = [d["snow_depth_end_m"] for d in week_days if d["date"] == week_end]
        sd_maxes = [d["snow_depth_max_m"] for d in week_days if d["snow_depth_max_m"] is not None]
        target_week = {
            "snow_cm_total": round(sum(d["snow_cm"] for d in week_days), 1),
            "precip_mm_total": round(sum(d["precip_mm"] for d in week_days), 1),
            "rain_mm_total": round(sum(d["rain_mm"] for d in week_days), 1),
            "days_with_snowfall_>=1cm": sum(1 for d in week_days if d["snow_cm"] >= 1.0),
            "snow_depth_start_m": sd_starts[0] if sd_starts else None,
            "snow_depth_end_m": sd_ends[0] if sd_ends else None,
            "snow_depth_max_m": max(sd_maxes) if sd_maxes else None,
            "mean_temp_max_c": safe_avg([d["temp_max_c"] for d in week_days]),
            "mean_temp_min_c": safe_avg([d["temp_min_c"] for d in week_days]),
            "mean_temp_mean_c": safe_avg([d["temp_mean_c"] for d in week_days]),
            "mean_freezing_level_m": safe_avg([d["freezing_level_mean_m"] for d in week_days]),
        }

    return {
        "season_total_snow_cm": season_total_snow,
        "season_total_precip_mm": season_total_precip,
        "season_total_rain_mm": season_total_rain,
        "days_with_snowfall_>=1cm": days_with_snow,
        "peak_snow_depth_m": round(peak_depth, 2) if peak_depth is not None else None,
        "peak_snow_depth_date": peak_date,
        "cumulative_snow_by_jul3_cm": cum_jul3,
        "target_week_jul4_10": target_week,
    }


def fetch_oni():
    """Trae NOAA ONI (Oceanic Niño Index). Devuelve dict {year: {season: anom}}.
    season es etiqueta de 3 letras del trimestre centrado (DJF, JFM, ..., NDJ)."""
    url = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
    r = http_get(url, timeout=30)
    lines = r.text.strip().split("\n")
    oni = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        season = parts[0]
        try:
            year = int(parts[1])
            anom = float(parts[3])
        except ValueError:
            continue
        oni.setdefault(str(year), {})[season] = anom
    return oni


def main():
    today = date.today()
    current_year = today.year
    end_year = current_year - 1
    start_year = end_year - YEARS_BACK + 1

    print(f"Cerro Catedral histórico — {start_year}-{end_year} ({YEARS_BACK} años) + {current_year} en curso")
    print(f"Output: {os.path.abspath(OUTPUT_FILE)}")
    print()

    seasons = {}
    for year in range(start_year, end_year + 1):
        print(f"[{year}] fetching May 1 – Sep 30...")
        try:
            data = fetch_year(f"{year}-05-01", f"{year}-09-30")
            seasons[str(year)] = parse_season(data, year, is_current=False)
            s = seasons[str(year)]["summary"]
            tw = s["target_week_jul4_10"]
            tw_snow = tw["snow_cm_total"] if tw else "n/a"
            print(f"  season total {s['season_total_snow_cm']} cm | jul4-10 {tw_snow} cm | peak depth {s['peak_snow_depth_m']} m")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(1)

    # Current season (May 1 to yesterday)
    season_start = date(current_year, 5, 1)
    if today > season_start:
        end_d = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[{current_year}] current season May 1 – {end_d}...")
        try:
            data = fetch_year(f"{current_year}-05-01", end_d)
            seasons[str(current_year)] = parse_season(data, current_year, is_current=True)
            s = seasons[str(current_year)]["summary"]
            print(f"  cumulative so far {s['season_total_snow_cm']} cm")
        except Exception as e:
            print(f"  ERROR: {e}")

    print("\nFetching NOAA ONI (ENSO index)...")
    try:
        oni = fetch_oni()
        print(f"  ONI cargado, {len(oni)} años")
    except Exception as e:
        print(f"  ERROR ONI: {e}")
        oni = {}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location": CATEDRAL,
        "target_week": {
            "month": TARGET_WEEK_MONTH,
            "start_day": TARGET_WEEK_START_DAY,
            "end_day": TARGET_WEEK_END_DAY,
            "label": "Jul 4–10",
        },
        "data_source": {
            "weather": "Open-Meteo ERA5 archive (archive-api.open-meteo.com)",
            "enso": "NOAA CPC ONI (cpc.ncep.noaa.gov/data/indices/oni.ascii.txt)",
        },
        "caveats": [
            "ERA5 grilla ~31km; punto efectivo a 1187m, no representa cumbre (2405m).",
            "snow_depth/snowfall del modelo, no observación directa del resort.",
            "Útil para tendencias y comparación relativa entre años; valores absolutos pueden diferir.",
        ],
        "seasons": seasons,
        "oni": oni,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
