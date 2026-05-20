"""
Actualización incremental del año en curso en docs/historical.json.
Fetches solo los días que faltan desde el último dato hasta ayer.
Los datos históricos (años anteriores) no se tocan.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE_DIR, "..", "docs", "historical.json")

RESORT = {
    "lat": -41.17,
    "lon": -71.45,
    "timezone": "America/Argentina/Buenos_Aires",
}

DAILY_VARS = [
    "snowfall_sum", "precipitation_sum", "rain_sum",
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "wind_speed_10m_max", "wind_gusts_10m_max",
]
HOURLY_VARS = ["snow_depth", "freezing_level_height", "temperature_850hPa"]

TARGET_MONTH, WEEK_START, WEEK_END = 7, 4, 10
SEASON_START_MONTH = 5


def http_get(url, retries=4, backoff=2):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            wait = backoff ** attempt
            print(f"  retry {attempt+1}/{retries} in {wait}s ({e})", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(wait)
    raise last


def fetch_range(start_d, end_d):
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={RESORT['lat']}&longitude={RESORT['lon']}"
        f"&start_date={start_d}&end_date={end_d}"
        f"&daily={','.join(DAILY_VARS)}"
        f"&hourly={','.join(HOURLY_VARS)}"
        f"&timezone={quote(RESORT['timezone'])}"
    )
    return http_get(url).json()


def safe_avg(lst):
    v = [x for x in lst if x is not None]
    return round(sum(v) / len(v), 2) if v else None


def safe_max(lst):
    v = [x for x in lst if x is not None]
    return round(max(v), 2) if v else None


def safe_min(lst):
    v = [x for x in lst if x is not None]
    return round(min(v), 2) if v else None


def agg_hourly(hourly):
    times = hourly["time"]
    by_d = {}
    for i, t in enumerate(times):
        d = t[:10]
        b = by_d.setdefault(d, {"sd": [], "fr": [], "t8": []})
        b["sd"].append(hourly["snow_depth"][i])
        b["fr"].append(hourly["freezing_level_height"][i])
        b["t8"].append(hourly["temperature_850hPa"][i])
    result = {}
    for d, v in by_d.items():
        sd = v["sd"]
        result[d] = {
            "snow_depth_max_m":      safe_max(sd),
            "snow_depth_mean_m":     safe_avg(sd),
            "snow_depth_end_m":      next((round(x, 3) for x in reversed(sd) if x is not None), None),
            "freezing_level_mean_m": safe_avg(v["fr"]),
            "freezing_level_min_m":  safe_min(v["fr"]),
            "freezing_level_max_m":  safe_max(v["fr"]),
            "temp_850hpa_mean_c":    safe_avg(v["t8"]),
        }
    return result


def parse_days(api_data):
    daily = api_data["daily"]
    hourly_agg = agg_hourly(api_data["hourly"])
    days = []
    for i, d in enumerate(daily["time"]):
        h = hourly_agg.get(d, {})
        days.append({
            "date":          d,
            "snow_cm":       daily["snowfall_sum"][i] or 0,
            "precip_mm":     daily["precipitation_sum"][i] or 0,
            "rain_mm":       daily["rain_sum"][i] or 0,
            "temp_max_c":    daily["temperature_2m_max"][i],
            "temp_min_c":    daily["temperature_2m_min"][i],
            "temp_mean_c":   daily["temperature_2m_mean"][i],
            "wind_max_kmh":  daily["wind_speed_10m_max"][i],
            "wind_gust_kmh": daily["wind_gusts_10m_max"][i],
            **{k: h.get(k) for k in [
                "snow_depth_max_m", "snow_depth_mean_m", "snow_depth_end_m",
                "freezing_level_mean_m", "freezing_level_min_m",
                "freezing_level_max_m", "temp_850hpa_mean_c",
            ]},
        })
    return days


def compute_summary(days, year):
    snow_total   = round(sum(d["snow_cm"]   for d in days), 1)
    precip_total = round(sum(d["precip_mm"] for d in days), 1)
    rain_total   = round(sum(d["rain_mm"]   for d in days), 1)
    snow_days    = sum(1 for d in days if d["snow_cm"] >= 1.0)

    depths = [(d["snow_depth_max_m"], d["date"]) for d in days if d["snow_depth_max_m"] is not None]
    peak_depth, peak_date = max(depths, key=lambda x: x[0]) if depths else (None, None)

    jul3 = f"{year}-07-03"
    cum_jul3 = round(sum(d["snow_cm"] for d in days if d["date"] <= jul3), 1)

    week_s = f"{year}-{TARGET_MONTH:02d}-{WEEK_START:02d}"
    week_e = f"{year}-{TARGET_MONTH:02d}-{WEEK_END:02d}"
    week_days = [d for d in days if week_s <= d["date"] <= week_e]
    target_week = None
    if week_days:
        sd_starts = [d["snow_depth_max_m"] for d in week_days if d["date"] == week_s]
        sd_ends   = [d["snow_depth_end_m"] for d in week_days if d["date"] == week_e]
        sd_maxes  = [d["snow_depth_max_m"] for d in week_days if d["snow_depth_max_m"] is not None]
        target_week = {
            "snow_cm_total":             round(sum(d["snow_cm"]   for d in week_days), 1),
            "precip_mm_total":           round(sum(d["precip_mm"] for d in week_days), 1),
            "rain_mm_total":             round(sum(d["rain_mm"]   for d in week_days), 1),
            "days_with_snowfall_>=1cm":  sum(1 for d in week_days if d["snow_cm"] >= 1.0),
            "snow_depth_start_m":        sd_starts[0] if sd_starts else None,
            "snow_depth_end_m":          sd_ends[0]   if sd_ends   else None,
            "snow_depth_max_m":          max(sd_maxes) if sd_maxes else None,
            "mean_temp_max_c":           safe_avg([d["temp_max_c"]  for d in week_days]),
            "mean_temp_min_c":           safe_avg([d["temp_min_c"]  for d in week_days]),
            "mean_temp_mean_c":          safe_avg([d["temp_mean_c"] for d in week_days]),
            "mean_freezing_level_m":     safe_avg([d["freezing_level_mean_m"] for d in week_days]),
        }

    return {
        "season_total_snow_cm":       snow_total,
        "season_total_precip_mm":     precip_total,
        "season_total_rain_mm":       rain_total,
        "days_with_snowfall_>=1cm":   snow_days,
        "peak_snow_depth_m":          round(peak_depth, 2) if peak_depth else None,
        "peak_snow_depth_date":       peak_date,
        "cumulative_snow_by_jul3_cm": cum_jul3,
        "target_week_jul4_10":        target_week,
    }


def main():
    today     = date.today()
    yesterday = today - timedelta(days=1)
    year      = today.year
    yr_key    = str(year)

    if not os.path.exists(OUT_FILE):
        print(f"ERROR: {OUT_FILE} no existe. Corré historical/fetch.py primero.")
        sys.exit(1)

    with open(OUT_FILE, encoding="utf-8") as f:
        data = json.load(f)

    season_start = date(year, SEASON_START_MONTH, 1)
    if today <= season_start:
        print("La temporada todavía no empezó. Sin cambios.")
        return

    existing = data["seasons"].get(yr_key, {}).get("daily", [])

    if existing:
        last_date  = date.fromisoformat(existing[-1]["date"])
        fetch_from = last_date + timedelta(days=1)
    else:
        fetch_from = season_start

    if fetch_from > yesterday:
        print(f"[{year}] Ya al día (último dato: {yesterday}). Sin cambios.")
        return

    start_d = fetch_from.strftime("%Y-%m-%d")
    end_d   = yesterday.strftime("%Y-%m-%d")
    n_new   = (yesterday - fetch_from).days + 1
    print(f"[{year}] Fetcheando {start_d} → {end_d} ({n_new} días nuevos)...")

    try:
        api_data = fetch_range(start_d, end_d)
        new_days = parse_days(api_data)
    except Exception as e:
        print(f"  ERROR al fetchear: {e}")
        sys.exit(1)

    existing_dates = {d["date"] for d in existing}
    to_add   = [d for d in new_days if d["date"] not in existing_dates]
    all_days = existing + to_add

    data["seasons"][yr_key] = {
        "year":       year,
        "is_current": True,
        "daily":      all_days,
        "summary":    compute_summary(all_days, year),
    }
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  +{len(to_add)} días → total {len(all_days)} días | Guardado → {OUT_FILE}")


if __name__ == "__main__":
    main()
