import requests
import json
import re
import subprocess
import os
from datetime import datetime, timezone
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "docs", "datos.json")
GIT_EXE = r"C:\Program Files\Git\bin\git.exe"

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=-41.17&longitude=-71.45"
    "&daily=snowfall_sum,temperature_2m_max,temperature_2m_min,"
    "wind_speed_10m_max,wind_direction_10m_dominant,"
    "precipitation_sum,precipitation_probability_max,weathercode"
    "&hourly=snowfall,snow_depth,temperature_2m,wind_speed_10m,"
    "wind_direction_10m,freezing_level_height,visibility,precipitation"
    "&timezone=America%2FArgentina%2FBuenos_Aires"
    "&forecast_days=14"
)

SNOW_REPORT_URL = "https://www.snow-forecast.com/resorts/Catedral/snow-report"
FORECAST_URL_TOP = "https://www.snow-forecast.com/resorts/Catedral/6day/top"


def fetch_open_meteo():
    r = requests.get(OPEN_METEO_URL, timeout=15)
    r.raise_for_status()
    data = r.json()

    daily = data["daily"]
    hourly = data["hourly"]

    days = []
    for i, date in enumerate(daily["time"]):
        # build AM / PM / Night buckets from hourly (each day = 24 hours)
        h_start = i * 24
        am = slice(h_start + 6, h_start + 12)   # 06-12
        pm = slice(h_start + 12, h_start + 18)  # 12-18
        nt = slice(h_start + 18, h_start + 24)  # 18-24

        def avg(lst):
            valid = [x for x in lst if x is not None]
            return round(sum(valid) / len(valid), 1) if valid else None

        def total(lst):
            valid = [x for x in lst if x is not None]
            return round(sum(valid), 1) if valid else 0.0

        def most_common(lst):
            valid = [x for x in lst if x is not None]
            return max(set(valid), key=valid.count) if valid else None

        snow_h = hourly["snowfall"]
        temp_h = hourly["temperature_2m"]
        wind_h = hourly["wind_speed_10m"]
        wdir_h = hourly["wind_direction_10m"]
        freeze_h = hourly["freezing_level_height"]
        vis_h = hourly["visibility"]
        prec_h = hourly["precipitation"]

        def deg_to_cardinal(deg):
            if deg is None:
                return "—"
            dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
                    "S","SSW","SW","WSW","W","WNW","NW","NNW"]
            return dirs[round(deg / 22.5) % 16]

        # cumulative snowfall up to this day
        cumulative = round(sum(
            (daily["snowfall_sum"][j] or 0) for j in range(i + 1)
        ), 1)

        periods = {}
        for label, sl in [("am", am), ("pm", pm), ("night", nt)]:
            periods[label] = {
                "snow_cm": total(snow_h[sl]),
                "temp_c": avg(temp_h[sl]),
                "wind_kmh": avg(wind_h[sl]),
                "wind_dir": deg_to_cardinal(most_common(wdir_h[sl])),
                "freeze_m": avg(freeze_h[sl]),
                "visibility_m": avg(vis_h[sl]),
                "rain_mm": total(prec_h[sl]),
            }

        days.append({
            "date": date,
            "snow_cm": daily["snowfall_sum"][i] or 0,
            "temp_max": daily["temperature_2m_max"][i],
            "temp_min": daily["temperature_2m_min"][i],
            "wind_max_kmh": daily["wind_speed_10m_max"][i],
            "wind_dir": deg_to_cardinal(daily["wind_direction_10m_dominant"][i]),
            "precip_mm": daily["precipitation_sum"][i] or 0,
            "precip_prob": daily["precipitation_probability_max"][i],
            "weathercode": daily["weathercode"][i],
            "cumulative_snow_cm": cumulative,
            "periods": periods,
        })

    return days


def fetch_snow_report():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(SNOW_REPORT_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        report = {
            "upper_snow_cm": None,
            "lower_snow_cm": None,
            "last_7days_cm": None,
            "last_24h_cm": None,
            "season_total_cm": None,
            "resort_status": None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        # resort open/closed status
        status_el = soup.select_one(".resort-status, .open-status, [class*='status']")
        if status_el:
            report["resort_status"] = status_el.get_text(strip=True)

        # snow depth table rows
        text = soup.get_text(" ")

        # recent snowfall totals — look for patterns like "37 cm" near "7 day" etc.
        m = re.search(r"last\s+7\s+days?[^\d]*(\d+)\s*cm", text, re.IGNORECASE)
        if m:
            report["last_7days_cm"] = int(m.group(1))

        m = re.search(r"last\s+24\s+hours?[^\d]*(\d+)\s*cm", text, re.IGNORECASE)
        if m:
            report["last_24h_cm"] = int(m.group(1))

        # snow depth fields (upper / lower)
        rows = soup.select("table tr, .snow-depth tr, .report-row")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                val_text = cells[1].get_text(strip=True)
                val_match = re.search(r"(\d+)", val_text)
                if val_match:
                    val = int(val_match.group(1))
                    if "upper" in label or "top" in label or "superior" in label:
                        report["upper_snow_cm"] = val
                    elif "lower" in label or "base" in label or "inferior" in label:
                        report["lower_snow_cm"] = val

        return report

    except Exception as e:
        print(f"[snow-report] Error scraping: {e}")
        return {"scraped_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}


def fetch_recent_snowfall():
    """Scrape recent daily snowfall from snowforecast.com snow history."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    recent = []
    try:
        r = requests.get(SNOW_REPORT_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # look for the recent snowfall table
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    date_text = cells[0].get_text(strip=True)
                    snow_text = cells[1].get_text(strip=True)
                    snow_match = re.search(r"(\d+)", snow_text)
                    if snow_match and re.search(r"\d{1,2}\s+\w+|\w+\s+\d{1,2}", date_text):
                        recent.append({
                            "date": date_text,
                            "snow_cm": int(snow_match.group(1))
                        })
    except Exception as e:
        print(f"[recent-snowfall] Error: {e}")

    return recent[:10]


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
    return mapping.get(code, f"Código {code}")


def git_push():
    cmds = [
        [GIT_EXE, "-C", BASE_DIR, "add", "docs/datos.json"],
        [GIT_EXE, "-C", BASE_DIR, "commit", "-m",
         f"update: forecast {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        [GIT_EXE, "-C", BASE_DIR, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout.strip() or result.stderr.strip())


def main():
    print("Fetching Open-Meteo forecast...")
    forecast_days = fetch_open_meteo()

    # add weathercode label to each day
    for d in forecast_days:
        d["condition"] = weathercode_to_label(d["weathercode"])

    print("Fetching snow report from snowforecast.com...")
    snow_report = fetch_snow_report()

    print("Fetching recent snowfall history...")
    recent_snow = fetch_recent_snowfall()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resort": "Cerro Catedral",
        "location": {"lat": -41.17, "lon": -71.45, "elevation_m": 2405},
        "snow_report": snow_report,
        "recent_snowfall": recent_snow,
        "forecast": forecast_days,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved -> {OUTPUT_FILE}")

    print("Pushing to GitHub...")
    git_push()
    print("Done.")


if __name__ == "__main__":
    main()
