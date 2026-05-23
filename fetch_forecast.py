import requests
import json
import re
import subprocess
import os
import time
from datetime import datetime, date, timedelta, timezone
from bs4 import BeautifulSoup


def http_get(url, headers=None, timeout=30, retries=3, backoff=2):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise last

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE   = os.path.join(BASE_DIR, "docs", "datos.json")
CAMERAS_DIR   = os.path.join(BASE_DIR, "docs", "cameras")

WEBCAMS = [
    {"name": "Punta Princesa",         "url": "https://varitech.ar/cameras/cam001/latest.jpg", "file": "cam001.jpg"},
    {"name": "Playpark",               "url": "https://varitech.ar/cameras/cam002/latest.jpg", "file": "cam002.jpg"},
    {"name": "Plaza Catalina Reynal",  "url": "https://varitech.ar/cameras/cam003/latest.jpg", "file": "cam003.jpg"},
    {"name": "Cable Carril Inferior",  "url": "https://varitech.ar/cameras/cam004/latest.jpg", "file": "cam004.jpg"},
    {"name": "Pista Eventos",          "url": "https://varitech.ar/cameras/cam005/latest.jpg", "file": "cam005.jpg"},
    {"name": "Centro Superior",        "url": "https://varitech.ar/cameras/cam006/latest.jpg", "file": "cam006.jpg"},
]
GIT_EXE = r"C:\Program Files\Git\bin\git.exe"

RESORTS = [
    {
        "id": "catedral",
        "name": "Cerro Catedral",
        "country": "Argentina",
        "lat": -41.22,
        "lon": -71.48,
        "elevation_m": 2405,
        "timezone": "America/Argentina/Buenos_Aires",
        "snowforecast_slug": "Catedral",
    },
    {
        "id": "leslenas",
        "name": "Las Leñas",
        "country": "Argentina",
        "lat": -35.15,
        "lon": -70.07,
        "elevation_m": 3430,
        "timezone": "America/Argentina/Buenos_Aires",
        "snowforecast_slug": "Las-Lenas",
    },
    {
        "id": "vallenevado",
        "name": "Valle Nevado",
        "country": "Chile",
        "lat": -33.36,
        "lon": -70.30,
        "elevation_m": 3025,
        "timezone": "America/Santiago",
        "snowforecast_slug": "Valle-Nevado",
    },
]


def build_meteo_url(lat, lon, tz):
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=snowfall_sum,temperature_2m_max,temperature_2m_min,"
        "wind_speed_10m_max,wind_direction_10m_dominant,"
        "precipitation_sum,precipitation_probability_max,weathercode"
        "&hourly=snowfall,snow_depth,temperature_2m,wind_speed_10m,"
        "wind_direction_10m,freezing_level_height,visibility,precipitation"
        f"&timezone={requests.utils.quote(tz)}"
        "&forecast_days=14"
    )


def deg_to_cardinal(deg):
    if deg is None:
        return "-"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(deg / 22.5) % 16]


def fetch_open_meteo(resort):
    url = build_meteo_url(resort["lat"], resort["lon"], resort["timezone"])
    r = http_get(url)
    data = r.json()

    daily = data["daily"]
    hourly = data["hourly"]

    days = []
    for i, date in enumerate(daily["time"]):
        h_start = i * 24
        am = slice(h_start + 6, h_start + 12)
        pm = slice(h_start + 12, h_start + 18)
        nt = slice(h_start + 18, h_start + 24)

        def avg(lst):
            valid = [x for x in lst if x is not None]
            return round(sum(valid) / len(valid), 1) if valid else None

        def total(lst):
            valid = [x for x in lst if x is not None]
            return round(sum(valid), 1) if valid else 0.0

        def most_common(lst):
            valid = [x for x in lst if x is not None]
            return max(set(valid), key=valid.count) if valid else None

        snow_h  = hourly["snowfall"]
        temp_h  = hourly["temperature_2m"]
        wind_h  = hourly["wind_speed_10m"]
        wdir_h  = hourly["wind_direction_10m"]
        freeze_h= hourly["freezing_level_height"]
        vis_h   = hourly["visibility"]
        prec_h  = hourly["precipitation"]

        cumulative = round(sum((daily["snowfall_sum"][j] or 0) for j in range(i + 1)), 1)

        periods = {}
        for label, sl in [("am", am), ("pm", pm), ("night", nt)]:
            periods[label] = {
                "snow_cm":    total(snow_h[sl]),
                "temp_c":     avg(temp_h[sl]),
                "wind_kmh":   avg(wind_h[sl]),
                "wind_dir":   deg_to_cardinal(most_common(wdir_h[sl])),
                "freeze_m":   avg(freeze_h[sl]),
                "visibility_m": avg(vis_h[sl]),
                "rain_mm":    total(prec_h[sl]),
            }

        days.append({
            "date":             date,
            "snow_cm":          daily["snowfall_sum"][i] or 0,
            "temp_max":         daily["temperature_2m_max"][i],
            "temp_min":         daily["temperature_2m_min"][i],
            "wind_max_kmh":     daily["wind_speed_10m_max"][i],
            "wind_dir":         deg_to_cardinal(daily["wind_direction_10m_dominant"][i]),
            "precip_mm":        daily["precipitation_sum"][i] or 0,
            "precip_prob":      daily["precipitation_probability_max"][i],
            "weathercode":      daily["weathercode"][i],
            "cumulative_snow_cm": cumulative,
            "periods":          periods,
        })

    return days


_SF_CONDITION_TO_CODE = {
    "clear": 0, "sunny": 0,
    "mostly clear": 1, "mostly sunny": 1,
    "some clouds": 2, "partly cloudy": 2,
    "cloudy": 3, "overcast": 3, "dull": 3,
    "fog": 45, "mist": 45,
    "drizzle": 51, "light drizzle": 51,
    "light rain": 61, "rain showers": 61,
    "rain": 63, "heavy rain": 65,
    "light snow": 71, "snow showers": 85,
    "snow": 73, "heavy snow": 75,
    "blizzard": 75, "thunderstorm": 95,
}


def _sf_condition_code(text):
    t = text.lower().strip()
    return _SF_CONDITION_TO_CODE.get(t, 3)


def _sf_parse_wind(cell):
    m = re.match(r"(\d+)([A-Z]+)", cell.strip())
    if m:
        return int(m.group(1)), m.group(2)
    return None, "-"


def _sf_num(v):
    v = v.strip()
    if v in ("—", "", "-"): return 0.0
    try: return float(v)
    except: return 0.0


def fetch_sf_forecast(resort):
    """Scrape 16-day forecast from snow-forecast.com (requires member cookie)."""
    cookie = os.environ.get("SF_SESSION_COOKIE", "").strip()
    if not cookie:
        return None

    slug = resort.get("snowforecast_slug")
    if not slug:
        return None

    url = f"https://www.snow-forecast.com/resorts/{slug}/16-day/top"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers,
                         cookies={"_current_session": cookie}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  [SF forecast] request error: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.select_one("table.forecast-table__table--content")
    if not table:
        print("  [SF forecast] table not found (cookie expired?)")
        return None

    rows = table.find_all("tr")

    # --- Date cells (colspan=3 per day) ---
    today = date.today()
    date_cells = rows[1].find_all("td")
    dates = []
    prev_day = 0
    mo, yr = today.month, today.year
    for cell in date_cells:
        m = re.search(r"(\d+)$", cell.get_text(strip=True))
        if not m:
            continue
        day_num = int(m.group(1))
        if day_num < prev_day:          # month rolled over
            mo = mo + 1 if mo < 12 else 1
            if mo == 1:
                yr += 1
        prev_day = day_num
        dates.append(date(yr, mo, day_num).isoformat())

    # --- Period labels (AM/PM/night per day) ---
    periods_row = [c.get_text(strip=True).lower() for c in rows[2].find_all("td")]

    def get_row_vals(label):
        for row in rows:
            hdr = row.find("th")
            if hdr and label in hdr.get_text(strip=True).lower():
                return [c.get_text(strip=True) for c in row.find_all("td")]
        return []

    snow_vals   = get_row_vals("cm")
    rain_vals   = get_row_vals("mm")
    tmax_vals   = get_row_vals("max")
    tmin_vals   = get_row_vals("min")
    freeze_vals = get_row_vals("freeze")
    wind_vals   = get_row_vals("km/h")
    cond_vals   = rows[4].find_all("td") if len(rows) > 4 else []
    cond_vals   = [c.get_text(strip=True) for c in cond_vals]

    # Build per-period index aligned to dates
    n_periods = len(periods_row)
    period_dates = []
    for i, d_str in enumerate(dates):
        period_dates += [d_str] * 3      # 3 periods per day
    period_dates = period_dates[:n_periods]

    # Group into days
    day_map = {}
    for idx, (d_str, period) in enumerate(zip(period_dates, periods_row)):
        if d_str not in day_map:
            day_map[d_str] = {"am": {}, "pm": {}, "night": {}}
        p = period if period in ("am", "pm", "night") else "am"
        day_map[d_str][p] = {
            "snow_cm":  _sf_num(snow_vals[idx]) if idx < len(snow_vals) else 0.0,
            "rain_mm":  _sf_num(rain_vals[idx]) if idx < len(rain_vals) else 0.0,
            "temp_c":   None,
            "wind_kmh": _sf_parse_wind(wind_vals[idx])[0] if idx < len(wind_vals) else None,
            "wind_dir": _sf_parse_wind(wind_vals[idx])[1] if idx < len(wind_vals) else "-",
            "freeze_m": _sf_num(freeze_vals[idx]) or None if idx < len(freeze_vals) else None,
            "visibility_m": None,
        }

    # Build daily forecast list
    days = []
    cumulative = 0.0
    for i, d_str in enumerate(dates):
        ps = day_map.get(d_str, {"am": {}, "pm": {}, "night": {}})
        snow_day = sum(ps[p].get("snow_cm", 0) for p in ("am", "pm", "night"))
        rain_day = sum(ps[p].get("rain_mm", 0) for p in ("am", "pm", "night"))
        cumulative = round(cumulative + snow_day, 1)

        tmax = float(tmax_vals[i]) if i < len(tmax_vals) and tmax_vals[i] not in ("—","") else None
        tmin = float(tmin_vals[i]) if i < len(tmin_vals) and tmin_vals[i] not in ("—","") else None
        wind_speeds = [ps[p]["wind_kmh"] for p in ("am", "pm", "night") if ps[p].get("wind_kmh")]
        wind_max = max(wind_speeds) if wind_speeds else None
        cond_text = cond_vals[i * 3] if i * 3 < len(cond_vals) else ""

        days.append({
            "date":               d_str,
            "snow_cm":            round(snow_day, 1),
            "temp_max":           tmax,
            "temp_min":           tmin,
            "wind_max_kmh":       wind_max,
            "wind_dir":           "-",
            "precip_mm":          round(rain_day + snow_day * 0.1, 1),
            "precip_prob":        None,
            "weathercode":        _sf_condition_code(cond_text),
            "cumulative_snow_cm": cumulative,
            "periods":            ps,
            "source":             "snow-forecast.com",
        })

    print(f"  [SF forecast] {len(days)} días · top 2179m · "
          f"total nieve: {cumulative} cm")
    return days


def fetch_snow_report(resort):
    url = f"https://www.snow-forecast.com/resorts/{resort['snowforecast_slug']}/snow-report"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        report = {
            "upper_snow_cm": None,
            "lower_snow_cm": None,
            "last_7days_cm": None,
            "last_24h_cm": None,
            "resort_status": None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        status_el = soup.select_one(".resort-status, .open-status, [class*='status']")
        if status_el:
            report["resort_status"] = status_el.get_text(strip=True)

        text = soup.get_text(" ")

        m = re.search(r"last\s+7\s+days?[^\d]*(\d+)\s*cm", text, re.IGNORECASE)
        if m:
            report["last_7days_cm"] = int(m.group(1))

        m = re.search(r"last\s+24\s+hours?[^\d]*(\d+)\s*cm", text, re.IGNORECASE)
        if m:
            report["last_24h_cm"] = int(m.group(1))

        for row in soup.select("table tr, .snow-depth tr, .report-row"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                val_match = re.search(r"(\d+)", cells[1].get_text(strip=True))
                if val_match:
                    val = int(val_match.group(1))
                    if "upper" in label or "top" in label:
                        report["upper_snow_cm"] = val
                    elif "lower" in label or "base" in label:
                        report["lower_snow_cm"] = val

        return report

    except Exception as e:
        print(f"  [snow-report:{resort['id']}] {e}")
        return {"scraped_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}


def fetch_historical_seasons(resort, num_years=5):
    from datetime import date, timedelta
    today = date.today()
    current_year = today.year
    end_year = current_year - 1
    start_year = end_year - num_years + 1
    seasons = []

    # Past completed seasons
    for year in range(start_year, end_year + 1):
        try:
            url = (
                "https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={resort['lat']}&longitude={resort['lon']}"
                f"&start_date={year}-05-01&end_date={year}-09-30"
                "&daily=snowfall_sum"
                f"&timezone={requests.utils.quote(resort['timezone'])}"
            )
            r = http_get(url, timeout=30)
            daily = r.json()["daily"]["snowfall_sum"]
            cumulative, total = [], 0.0
            for v in daily:
                total += v or 0
                cumulative.append(round(total, 1))
            seasons.append({"year": year, "cumulative": cumulative, "total": round(total, 1), "is_current": False})
            print(f"  [{year}] {round(total, 1)} cm")
        except Exception as e:
            print(f"  [{year}] Error: {e}")

    # Current season: May 1 to yesterday
    season_start = date(current_year, 5, 1)
    if today > season_start:
        try:
            end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            url = (
                "https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={resort['lat']}&longitude={resort['lon']}"
                f"&start_date={current_year}-05-01&end_date={end_date}"
                "&daily=snowfall_sum"
                f"&timezone={requests.utils.quote(resort['timezone'])}"
            )
            r = http_get(url, timeout=30)
            daily = r.json()["daily"]["snowfall_sum"]
            cumulative, total = [], 0.0
            for v in daily:
                total += v or 0
                cumulative.append(round(total, 1))
            seasons.append({"year": current_year, "cumulative": cumulative, "total": round(total, 1), "is_current": True})
            print(f"  [{current_year}] {round(total, 1)} cm (en curso, {len(cumulative)} días)")
        except Exception as e:
            print(f"  [{current_year}] Error: {e}")

    return seasons


def weathercode_to_label(code):
    mapping = {
        0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado",
        3: "Nublado", 45: "Niebla", 48: "Niebla con escarcha",
        51: "Llovizna leve", 53: "Llovizna", 55: "Llovizna intensa",
        61: "Lluvia leve", 63: "Lluvia", 65: "Lluvia intensa",
        71: "Nevada leve", 73: "Nevada", 75: "Nevada intensa",
        77: "Granizo", 80: "Chaparrones leves", 81: "Chaparrones",
        82: "Chaparrones intensos", 85: "Chubascos de nieve",
        86: "Chubascos de nieve intensos", 95: "Tormenta",
        96: "Tormenta con granizo", 99: "Tormenta intensa con granizo",
    }
    return mapping.get(code, f"Codigo {code}")


def fetch_webcams():
    os.makedirs(CAMERAS_DIR, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    results = []
    for cam in WEBCAMS:
        dest = os.path.join(CAMERAS_DIR, cam["file"])
        try:
            r = requests.get(cam["url"], headers=headers, timeout=15)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
            results.append({"name": cam["name"], "file": f"cameras/{cam['file']}", "ok": True})
            print(f"  [{cam['name']}] OK ({len(r.content)//1024} KB)")
        except Exception as e:
            print(f"  [{cam['name']}] Error: {e}")
            results.append({"name": cam["name"], "file": f"cameras/{cam['file']}", "ok": False})
    return results


def git_push():
    cmds = [
        [GIT_EXE, "-C", BASE_DIR, "add", "docs/datos.json", "docs/cameras/"],
        [GIT_EXE, "-C", BASE_DIR, "commit", "-m",
         f"update: forecast {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        [GIT_EXE, "-C", BASE_DIR, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout.strip() or result.stderr.strip())


def main():
    print("Downloading webcam images...")
    webcams = fetch_webcams()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "webcams": webcams,
        "resorts": {},
    }

    for resort in RESORTS:
        print(f"[{resort['name']}] Fetching forecast...")
        forecast_days = None
        if resort.get("snowforecast_slug"):
            forecast_days = fetch_sf_forecast(resort)
        if not forecast_days:
            print(f"  → fallback Open-Meteo")
            forecast_days = fetch_open_meteo(resort)
        for d in forecast_days:
            d["condition"] = weathercode_to_label(d["weathercode"])

        print(f"[{resort['name']}] Fetching snow report...")
        snow_report = fetch_snow_report(resort)

        resort_data = {
            "name":        resort["name"],
            "country":     resort["country"],
            "elevation_m": resort["elevation_m"],
            "snow_report": snow_report,
            "forecast":    forecast_days,
        }

        if resort["id"] == "catedral":
            print(f"[{resort['name']}] Fetching historical seasons...")
            resort_data["historical_seasons"] = fetch_historical_seasons(resort)

        output["resorts"][resort["id"]] = resort_data

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved -> {OUTPUT_FILE}")

    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("Skipping git_push (running in GitHub Actions; workflow handles commit).")
    else:
        print("Pushing to GitHub...")
        git_push()
        print("Done.")


if __name__ == "__main__":
    main()
