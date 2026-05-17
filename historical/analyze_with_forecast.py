"""
Análisis con forecast integrado — Cerro Catedral.

Trae el forecast fresco directo de Open-Meteo (horizonte 16 días, máximo disponible)
en lugar de leer docs/datos.json (que puede estar stale). Combina actuals 2026 con
el forecast para proyectar el estado al final del horizonte (~31-may típicamente)
y busca los K años históricos con estado más parecido al mismo día del calendario.

Features del snapshot al cierre del forecast:
  - cum_snow_cm:   nieve acumulada desde 1-may
  - cum_precip_mm: precipitación acumulada desde 1-may
  - window_temp_min_mean_c: temp_min promedio en la ventana de forecast
  - window_freezing_level_mean_m: nivel de cero promedio (cuando está disponible)

Ranking: 1 = mejor temporada al corte (más nieve acumulada). 26 = peor.

Outputs:
  - docs/historical_analysis_fc.json
  - Reporte impreso
"""

import json
import math
import os
import statistics
import time
from datetime import date
from urllib.parse import quote

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RESORTS = {
    "catedral":    {"name": "Cerro Catedral", "lat": -41.17, "lon": -71.45, "tz": "America/Argentina/Buenos_Aires"},
    "vallenevado": {"name": "Valle Nevado",   "lat": -33.34, "lon": -70.24, "tz": "America/Santiago"},
    "leslenas":    {"name": "Las Leñas",      "lat": -35.15, "lon": -70.07, "tz": "America/Argentina/Buenos_Aires"},
}

import sys
RESORT_ID = sys.argv[1] if len(sys.argv) > 1 else "catedral"
if RESORT_ID not in RESORTS:
    raise SystemExit(f"Resort '{RESORT_ID}' desconocido. Opciones: {list(RESORTS.keys())}")
CATEDRAL = RESORTS[RESORT_ID]  # mantengo nombre por compatibilidad con resto del script

HIST_FILE = os.path.join(BASE_DIR, "..", "docs", f"historical_{RESORT_ID}.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "..", "docs", f"historical_analysis_{RESORT_ID}_fc.json")

CURRENT_YEAR = date.today().year
K_ANALOGS = 5
FORECAST_DAYS = 16

TRAJECTORY_DATES = ["05-31", "06-15", "06-30", "07-04", "07-10", "07-31", "08-31", "09-30"]
# Referencias por resort.
# Cada referencia es (year, source_resort): mezclamos data del resort actual con bari
# cuando el usuario tiene experiencia en bari (2021, 2024) pero el análisis es para otro cerro.
REF_YEARS_BY_RESORT = {
    "catedral": {
        "bad":  [(2021, "catedral"), (2025, "catedral")],
        "good": [(2023, "catedral"), (2024, "catedral")],
    },
    "vallenevado": {
        # 2021 y 2024: tomamos data de bari (lo vivimos allá)
        "bad":  [(2021, "catedral")],
        "good": [(2024, "catedral"), (2025, "vallenevado")],
    },
    "leslenas": {
        "bad":  [(2021, "catedral"), (2025, "catedral")],
        "good": [(2023, "catedral"), (2024, "catedral")],
    },
}
REF_YEARS = REF_YEARS_BY_RESORT.get(RESORT_ID, REF_YEARS_BY_RESORT["catedral"])


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_fresh_forecast():
    """Pide a Open-Meteo el forecast con 16 días de horizonte."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={CATEDRAL['lat']}&longitude={CATEDRAL['lon']}"
        "&daily=snowfall_sum,precipitation_sum,temperature_2m_max,temperature_2m_min"
        "&hourly=snowfall,snow_depth,freezing_level_height,temperature_2m"
        f"&timezone={quote(CATEDRAL['tz'])}"
        f"&forecast_days={FORECAST_DAYS}"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def avg_periods(day, key):
    """Promedia un campo a través de am/pm/night de un día del forecast."""
    vals = []
    for p in ("am", "pm", "night"):
        v = day.get("periods", {}).get(p, {}).get(key)
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def aggregate_forecast_to_daily(fc_json):
    """Aplana el forecast de Open-Meteo a lista de dicts diarios con features útiles."""
    daily = fc_json["daily"]
    hourly = fc_json["hourly"]

    # Agrupar hourly por fecha
    by_date = {}
    for i, t in enumerate(hourly["time"]):
        d = t[:10]
        b = by_date.setdefault(d, {"freeze": [], "temp": [], "snow_depth": []})
        if hourly["freezing_level_height"][i] is not None:
            b["freeze"].append(hourly["freezing_level_height"][i])
        if hourly["temperature_2m"][i] is not None:
            b["temp"].append(hourly["temperature_2m"][i])
        if hourly["snow_depth"][i] is not None:
            b["snow_depth"].append(hourly["snow_depth"][i])

    days = []
    for i, d in enumerate(daily["time"]):
        h = by_date.get(d, {"freeze": [], "temp": [], "snow_depth": []})
        days.append({
            "date": d,
            "snow_cm": daily["snowfall_sum"][i] or 0,
            "precip_mm": daily["precipitation_sum"][i] or 0,
            "temp_max_c": daily["temperature_2m_max"][i],
            "temp_min_c": daily["temperature_2m_min"][i],
            "freezing_level_mean_m": round(sum(h["freeze"]) / len(h["freeze"]), 0) if h["freeze"] else None,
            "snow_depth_max_m": round(max(h["snow_depth"]), 3) if h["snow_depth"] else None,
        })
    return days


def build_forecast_snapshot(fc_days, current_2026):
    """Combina actuals 2026 + forecast en snapshot al cierre del horizonte."""
    actuals = current_2026["daily"]  # 1-may hasta ayer
    last_actual = actuals[-1]["date"]
    future = [d for d in fc_days if d["date"] > last_actual]

    if not future:
        raise SystemExit("Sin días futuros en el forecast (todo ya está en actuals).")

    snapshot_end = future[-1]["date"]

    cum_snow = sum(d["snow_cm"] for d in actuals) + sum(d["snow_cm"] for d in future)
    cum_precip = sum(d["precip_mm"] for d in actuals) + sum(d["precip_mm"] for d in future)

    window_temp_min = avg([d["temp_min_c"] for d in future])
    window_temp_max = avg([d["temp_max_c"] for d in future])
    window_freeze = avg([d["freezing_level_mean_m"] for d in future])
    window_snow_days = sum(1 for d in future if d["snow_cm"] >= 1.0)
    window_total_snow_forecast = round(sum(d["snow_cm"] for d in future), 1)

    return {
        "snapshot_end_date": snapshot_end,
        "actuals_through": last_actual,
        "forecast_days_added": len(future),
        "cum_snow_cm": round(cum_snow, 1),
        "cum_precip_mm": round(cum_precip, 1),
        "window_temp_min_mean_c": window_temp_min,
        "window_temp_max_mean_c": window_temp_max,
        "window_freezing_level_mean_m": window_freeze,
        "window_snow_days_>=1cm": window_snow_days,
        "window_total_forecast_snow_cm": window_total_snow_forecast,
    }


def build_historical_snapshot(year, daily, snapshot_end_date):
    """Para un año histórico, calcula el snapshot equivalente al snapshot_end del 2026.
    snapshot_end es 'YYYY-MM-DD' del 2026; lo reproyectamos al año dado."""
    md = snapshot_end_date[5:]  # 'MM-DD'
    cutoff = f"{year}-{md}"

    days_in_range = [d for d in daily if d["date"] <= cutoff]
    cum_snow = round(sum(d["snow_cm"] for d in days_in_range), 1)
    cum_precip = round(sum(d["precip_mm"] for d in days_in_range), 1)

    window = days_in_range[-14:] if len(days_in_range) >= 14 else days_in_range
    window_temp_min = avg([d["temp_min_c"] for d in window])
    window_temp_max = avg([d["temp_max_c"] for d in window])
    window_freeze = avg([d["freezing_level_mean_m"] for d in window])
    window_snow_days = sum(1 for d in window if d["snow_cm"] >= 1.0)

    return {
        "cutoff_date": cutoff,
        "cum_snow_cm": cum_snow,
        "cum_precip_mm": cum_precip,
        "window_temp_min_mean_c": window_temp_min,
        "window_temp_max_mean_c": window_temp_max,
        "window_freezing_level_mean_m": window_freeze,
        "window_snow_days_>=1cm": window_snow_days,
    }


def zscore_distance(target, candidate, std_dict, weights):
    """Distancia euclidiana ponderada en z-scores."""
    total = 0
    used = 0
    for k, w in weights.items():
        tv = target.get(k)
        cv = candidate.get(k)
        s = std_dict.get(k)
        if tv is None or cv is None or not s:
            continue
        total += w * ((tv - cv) / s) ** 2
        used += w
    if used == 0:
        return float("inf")
    return math.sqrt(total / used)


def compute_rankings(snap_2026, snapshots, snapshot_end_date):
    """Ranking de 2026 vs históricos al MISMO día del calendario.
    Convención: rank 1 = MEJOR (más nieve acumulada). rank N = peor.
    Devuelve también el ranking al snapshot_today (sólo actuals, sin forecast)."""
    today_iso = date.today().strftime("%Y-%m-%d")
    md_today = today_iso[5:]

    series_today = []
    series_forecast = []
    for s in snapshots:
        y = s["year"]
        daily = hist_cache[y]["daily"]
        cum_today = round(sum(d["snow_cm"] for d in daily if d["date"] <= f"{y}-{md_today}"), 1)
        cum_end = s["snapshot"]["cum_snow_cm"]
        series_today.append((cum_today, y))
        series_forecast.append((cum_end, y))

    cum_2026_today = round(sum(d["snow_cm"] for d in current_2026_cache["daily"]), 1)
    cum_2026_proj = snap_2026["cum_snow_cm"]

    def rank_desc(series, target_value):
        """rank 1 = más nieve. Si hay empates, target queda al final del grupo."""
        all_vals = sorted([(v, y) for v, y in series] + [(target_value, CURRENT_YEAR)], key=lambda x: (-x[0], x[1]))
        for i, (v, y) in enumerate(all_vals):
            if y == CURRENT_YEAR:
                return i + 1, len(all_vals)
        return None, len(all_vals)

    rank_today, n = rank_desc(series_today, cum_2026_today)
    rank_proj, _ = rank_desc(series_forecast, cum_2026_proj)

    # Años peores que 2026 al corte proyectado
    worse_proj = sorted([(v, y) for v, y in series_forecast if v < cum_2026_proj], key=lambda x: x[0])

    return {
        "convention": "rank 1 = mejor temporada (más nieve acumulada al corte)",
        "today": {
            "cutoff_date": today_iso,
            "cum_snow_2026_cm": cum_2026_today,
            "rank": f"{rank_today}/{n}",
            "median_historical": round(statistics.median([v for v, _ in series_today]), 1),
        },
        "projected_with_forecast": {
            "cutoff_date": snapshot_end_date,
            "cum_snow_2026_cm": cum_2026_proj,
            "rank": f"{rank_proj}/{n}",
            "median_historical": round(statistics.median([v for v, _ in series_forecast]), 1),
            "years_worse_than_2026": [{"year": y, "cum_snow_cm": v} for v, y in worse_proj],
        },
    }


hist_cache = {}
current_2026_cache = None


def compute_current_trajectory(current_data, fc_days):
    """Trayectoria 2026 combinando actuals + forecast.
    Para fechas posteriores al cierre del FC devuelve None (luego se proyecta con análogos)."""
    actuals = current_data["daily"]
    last_actual_date = actuals[-1]["date"]
    last_fc_date = fc_days[-1]["date"] if fc_days else last_actual_date

    combined = {d["date"]: d for d in actuals}
    for d in fc_days:
        if d["date"] not in combined:
            combined[d["date"]] = d
    combined_sorted = [combined[k] for k in sorted(combined)]

    cum_row = []
    depth_row = []
    for md in TRAJECTORY_DATES:
        cutoff = f"{CURRENT_YEAR}-{md}"
        if cutoff > last_fc_date:
            cum_row.append(None)
            depth_row.append(None)
            continue
        cum = round(sum(d["snow_cm"] for d in combined_sorted if d["date"] <= cutoff), 1)
        match = combined.get(cutoff)
        depth = None
        if match:
            depth = match.get("snow_depth_max_m")
            if depth is None:
                depth = match.get("snow_depth_end_m")
        cum_row.append(cum)
        depth_row.append(depth)
    return {
        "dates": TRAJECTORY_DATES,
        "cum_snow_cm": cum_row,
        "snow_depth_max_m": depth_row,
        "actuals_through": last_actual_date,
        "forecast_through": last_fc_date,
    }


def project_current_trajectory(current_traj, analog_trajectories, analog_years):
    """Extiende la trayectoria 2026 más allá del FC usando deltas medianas de los análogos
    desde el punto-ancla (el último valor conocido del FC).
    Devuelve dos listas: cum_row_projected y depth_row_projected, con valores en TODAS las fechas."""
    # Encontrar el índice del punto-ancla (último con dato real/FC)
    cum_known = current_traj["cum_snow_cm"]
    depth_known = current_traj["snow_depth_max_m"]
    anchor_idx = None
    for i in range(len(cum_known) - 1, -1, -1):
        if cum_known[i] is not None:
            anchor_idx = i
            break
    if anchor_idx is None:
        return None, None

    anchor_cum = cum_known[anchor_idx]
    anchor_depth = depth_known[anchor_idx]

    cum_proj = list(cum_known)
    depth_proj = list(depth_known)

    for j in range(anchor_idx + 1, len(TRAJECTORY_DATES)):
        # Delta cumulativa de cada análogo desde su ancla
        cum_deltas = []
        depth_deltas = []
        for y in analog_years:
            ay_cum = analog_trajectories["by_year"][y]["cum_snow_cm"]
            ay_depth = analog_trajectories["by_year"][y]["snow_depth_max_m"]
            if ay_cum[anchor_idx] is not None and ay_cum[j] is not None:
                cum_deltas.append(ay_cum[j] - ay_cum[anchor_idx])
            if (ay_depth[anchor_idx] is not None and ay_depth[j] is not None
                and anchor_depth is not None):
                depth_deltas.append(ay_depth[j] - ay_depth[anchor_idx])
        cum_proj[j] = round(anchor_cum + statistics.median(cum_deltas), 1) if cum_deltas else None
        if anchor_depth is not None and depth_deltas:
            depth_proj[j] = round(anchor_depth + statistics.median(depth_deltas), 3)
        else:
            # Si no tenemos manto en el ancla, usar la mediana absoluta de los análogos en esa fecha
            absolute_depths = [
                analog_trajectories["by_year"][y]["snow_depth_max_m"][j]
                for y in analog_years
                if analog_trajectories["by_year"][y]["snow_depth_max_m"][j] is not None
            ]
            depth_proj[j] = round(statistics.median(absolute_depths), 3) if absolute_depths else None

    return cum_proj, depth_proj


def compute_analog_trajectories(hist, years):
    """Para cada año, devuelve el acumulado y el snow_depth_max en TRAJECTORY_DATES."""
    out = {}
    for y in years:
        daily = hist["seasons"][str(y)]["daily"]
        cum_row = []
        depth_row = []
        for md in TRAJECTORY_DATES:
            cutoff = f"{y}-{md}"
            cum = round(sum(d["snow_cm"] for d in daily if d["date"] <= cutoff), 1)
            match = next((d for d in daily if d["date"] == cutoff), None)
            depth = match["snow_depth_max_m"] if match and match.get("snow_depth_max_m") is not None else None
            cum_row.append(cum)
            depth_row.append(depth)
        out[y] = {"cum_snow_cm": cum_row, "snow_depth_max_m": depth_row}
    return {"dates": TRAJECTORY_DATES, "by_year": out}


def compute_reference_trajectories(refs_with_source):
    """refs_with_source: lista de (year, source_resort_id).
    Devuelve trayectorias indexadas por la clave 'YYYY@resort' para distinguir el origen."""
    by_resort = {}  # cache de historical.json por resort
    out_by_year = {}
    for year, src in refs_with_source:
        if src not in by_resort:
            path = os.path.join(BASE_DIR, "..", "docs", f"historical_{src}.json")
            with open(path, "r", encoding="utf-8") as f:
                by_resort[src] = json.load(f)
        hist = by_resort[src]
        if str(year) not in hist["seasons"]:
            continue
        daily = hist["seasons"][str(year)]["daily"]
        cum_row, depth_row = [], []
        for md in TRAJECTORY_DATES:
            cutoff = f"{year}-{md}"
            cum = round(sum(d["snow_cm"] for d in daily if d["date"] <= cutoff), 1)
            match = next((d for d in daily if d["date"] == cutoff), None)
            depth = match["snow_depth_max_m"] if match and match.get("snow_depth_max_m") is not None else None
            cum_row.append(cum)
            depth_row.append(depth)
        key = f"{year}@{src}"
        out_by_year[key] = {
            "cum_snow_cm": cum_row,
            "snow_depth_max_m": depth_row,
            "year": year,
            "source": src,
        }
    return {"dates": TRAJECTORY_DATES, "by_year": out_by_year}


def _metric_value(year, hist, metric):
    s = hist["seasons"][str(year)]["summary"]
    tw = s["target_week_jul4_10"]
    if metric == "manto_jul4_m":
        return tw["snow_depth_start_m"]
    if metric == "season_total_cm":
        return s["season_total_snow_cm"]
    if metric == "peak_snow_depth_m":
        return s["peak_snow_depth_m"]
    return None


def _load_resort_hist(resort_id):
    path = os.path.join(BASE_DIR, "..", "docs", f"historical_{resort_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_conditional_probabilities(analogs):
    """Probabilidades sobre los análogos, comparando contra umbrales definidos por
    los años de referencia (cada uno con su resort de origen)."""
    outcomes = {
        "manto_jul4_m":      [a["outcomes"]["snow_depth_jul4_m"] for a in analogs],
        "season_total_cm":   [a["outcomes"]["season_total_cm"] for a in analogs],
        "peak_snow_depth_m": [a["outcomes"]["peak_snow_depth_m"] for a in analogs],
    }

    # Cargar hist por resort de referencia
    hist_cache_refs = {}
    def get_hist(src):
        if src not in hist_cache_refs:
            hist_cache_refs[src] = _load_resort_hist(src)
        return hist_cache_refs[src]

    result = {}
    for metric, vals in outcomes.items():
        clean = [v for v in vals if v is not None]
        if not clean:
            continue
        probs = []
        for y, src in REF_YEARS["bad"]:
            h = get_hist(src)
            if str(y) not in h["seasons"]:
                continue
            ref_v = _metric_value(y, h, metric)
            if ref_v is None:
                continue
            k = sum(1 for v in clean if v > ref_v)
            probs.append({
                "threshold": round(ref_v, 2),
                "label": f"mejor que {y} ({src} MALA)",
                "category": "bad",
                "fraction": f"{k}/{len(clean)}",
                "p": round(k / len(clean), 2),
            })
        for y, src in REF_YEARS["good"]:
            h = get_hist(src)
            if str(y) not in h["seasons"]:
                continue
            ref_v = _metric_value(y, h, metric)
            if ref_v is None:
                continue
            k = sum(1 for v in clean if v >= ref_v)
            probs.append({
                "threshold": round(ref_v, 2),
                "label": f"tan bueno como {y} ({src} BUENA)",
                "category": "good",
                "fraction": f"{k}/{len(clean)}",
                "p": round(k / len(clean), 2),
            })
        result[metric] = {
            "n_analogs": len(clean),
            "median": round(statistics.median(clean), 2),
            "range": [round(min(clean), 2), round(max(clean), 2)],
            "probabilities": probs,
        }
    return result


def main():
    global hist_cache, current_2026_cache
    hist = load_json(HIST_FILE)

    current_2026 = hist["seasons"].get(str(CURRENT_YEAR))
    if not current_2026:
        raise SystemExit("No hay datos del año actual en historical.json. Corré historical/fetch.py primero.")
    current_2026_cache = current_2026

    print("Fetching forecast fresco de Open-Meteo (16 días)...")
    fc_raw = fetch_fresh_forecast()
    fc_days = aggregate_forecast_to_daily(fc_raw)
    print(f"  forecast: {fc_days[0]['date']} → {fc_days[-1]['date']} ({len(fc_days)} días)")

    snap_2026 = build_forecast_snapshot(fc_days, current_2026)

    # Snapshots históricos al mismo día del año
    historical_years = sorted(int(y) for y in hist["seasons"] if not hist["seasons"][y]["is_current"])
    snapshots = []
    for y in historical_years:
        s_y = hist["seasons"][str(y)]
        hist_cache[y] = s_y
        snap = build_historical_snapshot(y, s_y["daily"], snap_2026["snapshot_end_date"])
        summary = s_y["summary"]
        tw = summary["target_week_jul4_10"] or {}
        snapshots.append({
            "year": y,
            "snapshot": snap,
            "outcomes": {
                "snow_depth_jul4_m": tw.get("snow_depth_start_m"),
                "snow_cum_jul3_cm": summary["cumulative_snow_by_jul3_cm"],
                "week_jul4_10_snow_cm": tw.get("snow_cm_total"),
                "season_total_cm": summary["season_total_snow_cm"],
                "peak_snow_depth_m": summary["peak_snow_depth_m"],
            },
        })

    rankings = compute_rankings(snap_2026, snapshots, snap_2026["snapshot_end_date"])

    # Stdevs históricos para z-scores
    feature_keys = [
        "cum_snow_cm",
        "cum_precip_mm",
        "window_temp_min_mean_c",
        "window_temp_max_mean_c",
        "window_freezing_level_mean_m",
    ]
    std_dict = {}
    for k in feature_keys:
        vals = [s["snapshot"][k] for s in snapshots if s["snapshot"][k] is not None]
        std_dict[k] = statistics.stdev(vals) if len(vals) > 1 else None

    # Pesos del análogo: nieve acumulada manda, lo demás soporta
    weights = {
        "cum_snow_cm": 2.0,
        "cum_precip_mm": 1.0,
        "window_temp_min_mean_c": 1.0,
        "window_freezing_level_mean_m": 1.0,
    }

    # Distancias
    ranked = sorted(
        snapshots,
        key=lambda s: zscore_distance(snap_2026, s["snapshot"], std_dict, weights),
    )
    analogs = ranked[:K_ANALOGS]

    # Predicciones
    def collect(key):
        vals = [a["outcomes"][key] for a in analogs if a["outcomes"][key] is not None]
        if not vals:
            return None
        return {
            "median": round(statistics.median(vals), 2),
            "mean": round(statistics.mean(vals), 2),
            "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
            "range": [round(min(vals), 2), round(max(vals), 2)],
            "n": len(vals),
        }

    predictions = {
        "snow_depth_jul4_m":    collect("snow_depth_jul4_m"),
        "snow_cum_jul3_cm":     collect("snow_cum_jul3_cm"),
        "week_jul4_10_snow_cm": collect("week_jul4_10_snow_cm"),
        "season_total_cm":      collect("season_total_cm"),
        "peak_snow_depth_m":    collect("peak_snow_depth_m"),
    }

    # Trayectorias de los análogos en fechas clave (acumulado y manto)
    trajectories = compute_analog_trajectories(hist, [a["year"] for a in analogs])
    # Trayectoria de los años de referencia (cada uno con su resort de origen)
    ref_all = REF_YEARS["bad"] + REF_YEARS["good"]
    ref_trajectories = compute_reference_trajectories(ref_all)
    # Trayectoria del año actual 2026: actuals + forecast donde haya
    current_trajectory = compute_current_trajectory(current_2026, fc_days)
    # Proyección de 2026 más allá del FC vía deltas medianas de los análogos
    cum_proj, depth_proj = project_current_trajectory(current_trajectory, trajectories, [a["year"] for a in analogs])
    current_trajectory["cum_snow_cm_projected"] = cum_proj
    current_trajectory["snow_depth_max_m_projected"] = depth_proj

    # Probabilidades condicionales (sólo del subconjunto de análogos)
    conditional_probabilities = compute_conditional_probabilities(analogs)

    output = {
        "method": "analog matching, snapshot expanded with Open-Meteo 16d forecast",
        "current_state_2026": snap_2026,
        "rankings": rankings,
        "analog_weights": weights,
        "feature_stdevs_historical": {k: round(v, 2) if v else None for k, v in std_dict.items()},
        "analog_years_top_k": [{
            "year": a["year"],
            "distance_z": round(zscore_distance(snap_2026, a["snapshot"], std_dict, weights), 3),
            **a["snapshot"],
            **a["outcomes"],
        } for a in analogs],
        "predictions_via_analogs": predictions,
        "analog_trajectories": trajectories,
        "reference_trajectories": ref_trajectories,
        "current_year_trajectory": current_trajectory,
        "reference_years": REF_YEARS,
        "conditional_probabilities": conditional_probabilities,
        "n_historical_years": len(snapshots),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print_report(output)
    print(f"\nGuardado -> {OUTPUT_FILE}")


def fmt(v):
    if v is None:
        return "—"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def print_report(out):
    s = out["current_state_2026"]
    analogs = out["analog_years_top_k"]
    analog_years = [a["year"] for a in analogs]
    ref_years = out["reference_years"]

    print("=" * 100)
    print(f"  PROYECCIÓN 2026 CONDICIONAL — análogos al estado al {s['snapshot_end_date']}")
    print("=" * 100)
    print(f"\nEstado 2026 al {s['snapshot_end_date']} (actuals hasta {s['actuals_through']} + {s['forecast_days_added']}d forecast):")
    print(f"  Nieve acumulada:  {s['cum_snow_cm']} cm   |   Precip: {s['cum_precip_mm']} mm")
    print(f"  FC ventana: Tmin {fmt(s['window_temp_min_mean_c'])}°C | Tmax {fmt(s['window_temp_max_mean_c'])}°C | freeze {fmt(s['window_freezing_level_mean_m'])}m | nieve esperada {s['window_total_forecast_snow_cm']} cm")

    r = out["rankings"]
    t = r["today"]
    p = r["projected_with_forecast"]
    print(f"\nRanking 2026 vs los 25 años (1 = mejor):")
    print(f"  Al {t['cutoff_date']} (sólo actuals):   {t['rank']}")
    print(f"  Al {p['cutoff_date']} (con FC):    {p['rank']}")
    worse = p.get("years_worse_than_2026", [])
    if worse:
        print(f"\n  Años peores que 2026 al {p['cutoff_date']} ({p['cum_snow_2026_cm']} cm):")
        for w in worse:
            print(f"    {w['year']}: {w['cum_snow_cm']} cm")
    else:
        print(f"\n  Ningún año del histórico tuvo MENOS nieve que 2026 al {p['cutoff_date']}.")

    print(f"\nAnálogos top-{len(analogs)}: {analog_years}")
    print(f"Referencias MALAS: {ref_years['bad']}   |   Referencias BUENAS: {ref_years['good']}")

    # --- Trayectorias ---
    tr = out["analog_trajectories"]
    rt = out.get("reference_trajectories", {"by_year": {}})
    ct = out.get("current_year_trajectory")
    dates = tr["dates"]
    last_fc_date = ct["forecast_through"] if ct else None

    def _print_2026_rows(metric_key, formatter):
        if not ct:
            return
        # Fila con dato real + FC (no proyectado): cells con valores reales, "----" donde no hay
        known = ct[metric_key]
        cells = []
        for v in known:
            if v is None:
                cells.append("   ----  |" if metric_key == "cum_snow_cm" else "   ---- |")
            else:
                cells.append(formatter(v))
        print(f"  {'2026 ★':>9} |" + "".join(cells))
        # Fila proyectada (igual al dato real donde existe, mediana de análogos donde no)
        projected = ct.get(metric_key + "_projected")
        if projected:
            cells = []
            for i, v in enumerate(projected):
                if known[i] is not None:
                    cells.append(formatter(v))  # ya cubierto arriba, mismo valor
                elif v is not None:
                    cells.append(formatter(v).replace(" ", " ").replace(" ", " "))  # mantén ancho
                else:
                    cells.append("   ----  |" if metric_key == "cum_snow_cm" else "   ---- |")
            print(f"  {'2026 proy':>9} |" + "".join(cells))

    def _print_analog_table(metric_key, formatter):
        print(f"  {'año':>9} |" + "".join(f" {d:>7} |" for d in dates))
        print("  " + "-" * (11 + 10 * len(dates)))
        _print_2026_rows(metric_key, formatter)
        print("  " + "-" * (11 + 10 * len(dates)))
        for y in analog_years:
            row = tr["by_year"][y][metric_key]
            print(f"  {y:>9} |" + "".join(formatter(v) for v in row))
        print("  " + "-" * (11 + 10 * len(dates)))
        median_row = []
        for i in range(len(dates)):
            vals = [tr["by_year"][y][metric_key][i] for y in analog_years if tr["by_year"][y][metric_key][i] is not None]
            median_row.append(statistics.median(vals) if vals else 0)
        print(f"  {'MEDIANA':>9} |" + "".join(formatter(v) for v in median_row))

    def _print_reference_table(metric_key, formatter):
        # Header con etiqueta más ancha porque incluye source
        print(f"  {'año (fuente)':>17} |" + "".join(f" {d:>7} |" for d in dates))
        print("  " + "-" * (19 + 10 * len(dates)))
        # Filas 2026 con padding extendido
        if ct:
            known = ct[metric_key]
            cells = []
            for v in known:
                if v is None:
                    cells.append("   ----  |" if metric_key == "cum_snow_cm" else "   ---- |")
                else:
                    cells.append(formatter(v))
            print(f"  {'2026 ★':>17} |" + "".join(cells))
            projected = ct.get(metric_key + "_projected")
            if projected:
                cells = []
                for i, v in enumerate(projected):
                    if v is not None:
                        cells.append(formatter(v))
                    else:
                        cells.append("   ----  |" if metric_key == "cum_snow_cm" else "   ---- |")
                print(f"  {'2026 proy':>17} |" + "".join(cells))
        print("  " + "-" * (19 + 10 * len(dates)))
        for y, src in ref_years["bad"]:
            key = f"{y}@{src}"
            if key in rt["by_year"]:
                row = rt["by_year"][key][metric_key]
                label = f"{y} {src} MALA"
                print(f"  {label:>17} |" + "".join(formatter(v) for v in row))
        for y, src in ref_years["good"]:
            key = f"{y}@{src}"
            if key in rt["by_year"]:
                row = rt["by_year"][key][metric_key]
                label = f"{y} {src} BUEN"
                print(f"  {label:>17} |" + "".join(formatter(v) for v in row))

    print(f"\n--- TABLA 1: TRAYECTORIA — acumulado (cm)  |  Análogos ---")
    print(f"  (2026 ★ = real + FC hasta {last_fc_date} | 2026 proy = extrapolado con mediana de deltas de análogos)")
    _print_analog_table("cum_snow_cm", lambda v: f" {(v if v is not None else 0):>6.1f}  |")

    print(f"\n--- TABLA 2: TRAYECTORIA — manto snow_depth_max_m  |  Análogos ---")
    _print_analog_table("snow_depth_max_m", lambda v: f"  {(v if v is not None else 0):>5.2f}m |")

    print(f"\n--- TABLA 3: TRAYECTORIA — acumulado (cm)  |  Referencias (malas/buenas) ---")
    _print_reference_table("cum_snow_cm", lambda v: f" {(v if v is not None else 0):>6.1f}  |")

    print(f"\n--- TABLA 4: TRAYECTORIA — manto snow_depth_max_m  |  Referencias (malas/buenas) ---")
    _print_reference_table("snow_depth_max_m", lambda v: f"  {(v if v is not None else 0):>5.2f}m |")

    # --- Resumen de análogos: cómo terminó cada uno ---
    print(f"\n--- CÓMO TERMINÓ CADA AÑO ANÁLOGO ---")
    print(f"  {'año':>5} | {'cum_31may':>10} | {'manto_4jul':>11} | {'cum_3jul':>9} | {'pico_manto':>11} | {'total_temp':>11}")
    for a in analogs:
        depth_str = f"{a['snow_depth_jul4_m']:.2f} m" if a['snow_depth_jul4_m'] is not None else "—"
        print(
            f"  {a['year']:>5} | "
            f"{a['cum_snow_cm']:>8} cm | "
            f"{depth_str:>11} | "
            f"{a['snow_cum_jul3_cm']:>7} cm | "
            f"{a['peak_snow_depth_m']:>9.2f} m | "
            f"{a['season_total_cm']:>9.1f} cm"
        )

    # --- Probabilidades condicionales ---
    print(f"\n--- PROBABILIDADES CONDICIONALES (n={len(analogs)} análogos) ---")
    cp = out["conditional_probabilities"]
    for metric, blk in cp.items():
        print(f"\n  {metric}:")
        print(f"    rango análogos: {blk['range'][0]}–{blk['range'][1]}  |  mediana: {blk['median']}")
        for prob in blk["probabilities"]:
            bar = "█" * int(prob["p"] * 30)
            tag = "❌" if prob["category"] == "bad" else "✓"
            print(f"    {tag} P( > {prob['threshold']:>6} : {prob['label']:<30}) = {prob['fraction']:>5}  ({prob['p']*100:>3.0f}%)  {bar}")

    print()


if __name__ == "__main__":
    main()
