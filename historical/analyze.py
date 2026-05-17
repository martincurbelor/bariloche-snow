"""
Análisis estadístico Fase 1 — Cerro Catedral, 25 años (2001-2025) + 2026 en curso.

Objetivo de la planificación: la semana del 4-10 jul. Lo que importa para esquiar
NO es la nieve que cae esa semana sino el estado del manto al ARRANCAR la semana
(producto de todo lo que pasó antes). Por eso:

  Variable objetivo primaria:   snow_depth al 4-jul (m) y acumulado total al 3-jul (cm)
  Variable objetivo secundaria: nevada DURANTE la semana 4-10 jul ("bonus de powder")

Modelos predictivos (todos para el manto al 4-jul):
  1. Empírico / climatológico (sin condicionar)
  2. Análogos top-K por estado al 15-may (acumulado + precip)
  3. Regresión lineal simple sobre cumulative-by-may-15

Foco Fase 1: snowfall, precipitación, snow_depth. Fase 2 agregará ENSO + freezing.
"""

import json
import math
import os
import statistics
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(BASE_DIR, "..", "docs", "historical.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "..", "docs", "historical_analysis.json")

CURRENT_YEAR = date.today().year
SNAPSHOT_MONTH = 5
SNAPSHOT_DAY = 15


def load():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[f]
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def describe(values, label):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"label": label, "n": 0}
    return {
        "label": label,
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "p10": pct(vals, 10),
        "p25": pct(vals, 25),
        "p75": pct(vals, 75),
        "p90": pct(vals, 90),
    }


def prob_at_least(values, threshold):
    if not values:
        return None
    return round(sum(1 for v in values if v >= threshold) / len(values), 3)


def prob_at_most(values, threshold):
    if not values:
        return None
    return round(sum(1 for v in values if v <= threshold) / len(values), 3)


def correlation(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs2, ys2 = zip(*pairs)
    mx, my = statistics.mean(xs2), statistics.mean(ys2)
    num = sum((x - mx) * (y - my) for x, y in zip(xs2, ys2))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs2))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys2))
    if sx == 0 or sy == 0:
        return None
    return round(num / (sx * sy), 3)


def linear_regression(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs2, ys2 = zip(*pairs)
    mx, my = statistics.mean(xs2), statistics.mean(ys2)
    num = sum((x - mx) * (y - my) for x, y in zip(xs2, ys2))
    den = sum((x - mx) ** 2 for x in xs2)
    if den == 0:
        return None
    b = num / den
    a = my - b * mx
    yhat = [a + b * x for x in xs2]
    ss_res = sum((y - yh) ** 2 for y, yh in zip(ys2, yhat))
    ss_tot = sum((y - my) ** 2 for y in ys2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    se = math.sqrt(ss_res / (n - 2)) if n > 2 else None
    return {
        "intercept": round(a, 2),
        "slope": round(b, 3),
        "r2": round(r2, 3) if r2 is not None else None,
        "residual_se": round(se, 2) if se is not None else None,
    }


def cumulative_snow_by(daily, cutoff_date):
    return round(sum(d["snow_cm"] for d in daily if d["date"] <= cutoff_date), 1)


def cumulative_precip_by(daily, cutoff_date):
    return round(sum(d["precip_mm"] for d in daily if d["date"] <= cutoff_date), 1)


def snow_depth_at(daily, target_date):
    """Espesor de manto al iniciar el día target_date (00:00). Usa snow_depth_end del día anterior."""
    for i, d in enumerate(daily):
        if d["date"] == target_date:
            if i > 0 and daily[i - 1].get("snow_depth_end_m") is not None:
                return daily[i - 1]["snow_depth_end_m"]
            return d.get("snow_depth_max_m")
    return None


def main():
    data = load()
    seasons = data["seasons"]

    historical_years = sorted(int(y) for y in seasons if not seasons[y]["is_current"])
    current_data = seasons.get(str(CURRENT_YEAR))
    snapshot = f"{CURRENT_YEAR}-{SNAPSHOT_MONTH:02d}-{SNAPSHOT_DAY:02d}"

    # --- Build per-year rows ---
    rows = []
    for y in historical_years:
        s = seasons[str(y)]
        daily = s["daily"]
        summary = s["summary"]
        tw = summary["target_week_jul4_10"] or {}
        snap_date = f"{y}-{SNAPSHOT_MONTH:02d}-{SNAPSHOT_DAY:02d}"
        jul4 = f"{y}-07-04"
        rows.append({
            "year": y,
            # estado al snapshot (15-may)
            "snow_cum_may15_cm": cumulative_snow_by(daily, snap_date),
            "precip_cum_may15_mm": cumulative_precip_by(daily, snap_date),
            # objetivo primario: manto al 4-jul
            "snow_cum_jul3_cm": summary["cumulative_snow_by_jul3_cm"],
            "snow_depth_jul4_m": tw.get("snow_depth_start_m"),
            # objetivo secundario: lo que cae durante la semana
            "week_jul4_10_snow_cm": tw.get("snow_cm_total"),
            "week_jul4_10_precip_mm": tw.get("precip_mm_total"),
            "week_jul4_10_depth_max_m": tw.get("snow_depth_max_m"),
            "week_jul4_10_depth_end_m": tw.get("snow_depth_end_m"),
            # contexto temporada completa
            "season_total_snow_cm": summary["season_total_snow_cm"],
            "season_total_precip_mm": summary["season_total_precip_mm"],
            "peak_snow_depth_m": summary["peak_snow_depth_m"],
            "peak_snow_depth_date": summary["peak_snow_depth_date"],
        })

    # --- Estadísticos descriptivos ---
    stats = {
        # objetivo primario
        "snow_cum_jul3_cm": describe([r["snow_cum_jul3_cm"] for r in rows], "Acumulado al 3-jul (cm) — base que llega al inicio de la semana"),
        "snow_depth_jul4_m": describe([r["snow_depth_jul4_m"] for r in rows], "Espesor manto al 4-jul (m) — la pista al arrancar"),
        # objetivo secundario
        "week_jul4_10_snow_cm": describe([r["week_jul4_10_snow_cm"] for r in rows], "Nevada DURANTE la semana 4-10 jul (cm) — bonus"),
        "week_jul4_10_depth_max_m": describe([r["week_jul4_10_depth_max_m"] for r in rows], "Pico de manto durante la semana (m)"),
        "week_jul4_10_depth_end_m": describe([r["week_jul4_10_depth_end_m"] for r in rows], "Manto al CERRAR la semana 10-jul (m)"),
        # contexto inicial
        "snow_cum_may15_cm": describe([r["snow_cum_may15_cm"] for r in rows], "Acumulado al 15-may (cm) — estado actual de comparación"),
        # contexto temporada
        "season_total_snow_cm": describe([r["season_total_snow_cm"] for r in rows], "Acumulado total temporada (cm)"),
        "peak_snow_depth_m": describe([r["peak_snow_depth_m"] for r in rows], "Pico de espesor temporada (m)"),
    }

    # --- Probabilidades del manto al 4-jul ---
    depths_jul4 = [r["snow_depth_jul4_m"] for r in rows if r["snow_depth_jul4_m"] is not None]
    cum_jul3 = [r["snow_cum_jul3_cm"] for r in rows]
    probs_manto = {
        "P(manto al 4-jul >= 0.30 m)": prob_at_least(depths_jul4, 0.30),
        "P(manto al 4-jul >= 0.50 m)": prob_at_least(depths_jul4, 0.50),
        "P(manto al 4-jul >= 0.70 m)": prob_at_least(depths_jul4, 0.70),
        "P(manto al 4-jul >= 1.00 m)": prob_at_least(depths_jul4, 1.00),
        "P(manto al 4-jul <= 0.20 m)": prob_at_most(depths_jul4, 0.20),
        "P(cum al 3-jul >= 100 cm)": prob_at_least(cum_jul3, 100),
        "P(cum al 3-jul >= 150 cm)": prob_at_least(cum_jul3, 150),
        "P(cum al 3-jul >= 200 cm)": prob_at_least(cum_jul3, 200),
    }

    # --- Correlaciones (qué predice el manto al 4-jul) ---
    correlations = {
        "may15_snow -> jul4_depth":        correlation([r["snow_cum_may15_cm"] for r in rows], depths_jul4 if len(depths_jul4) == len(rows) else [r["snow_depth_jul4_m"] for r in rows]),
        "may15_snow -> jul3_cum":          correlation([r["snow_cum_may15_cm"] for r in rows], cum_jul3),
        "may15_precip -> jul4_depth":      correlation([r["precip_cum_may15_mm"] for r in rows], [r["snow_depth_jul4_m"] for r in rows]),
        "may15_precip -> jul3_cum":        correlation([r["precip_cum_may15_mm"] for r in rows], cum_jul3),
        "jul3_cum -> jul4_depth":          correlation(cum_jul3, [r["snow_depth_jul4_m"] for r in rows]),
        "jul4_depth -> season_total":      correlation([r["snow_depth_jul4_m"] for r in rows], [r["season_total_snow_cm"] for r in rows]),
        "jul4_depth -> peak_depth":        correlation([r["snow_depth_jul4_m"] for r in rows], [r["peak_snow_depth_m"] for r in rows]),
    }

    # --- Estado 2026 ---
    current_snapshot = None
    if current_data:
        cd = current_data["daily"]
        cur_cum = round(sum(d["snow_cm"] for d in cd), 1)
        cur_precip = round(sum(d["precip_mm"] for d in cd), 1)
        cur_depth = cd[-1].get("snow_depth_end_m") if cd else None
        same_cut = sorted([r["snow_cum_may15_cm"] for r in rows] + [cur_cum])
        rank = same_cut.index(cur_cum) + 1
        current_snapshot = {
            "as_of": cd[-1]["date"] if cd else None,
            "snow_cum_cm": cur_cum,
            "precip_cum_mm": cur_precip,
            "snow_depth_m": cur_depth,
            "rank_among_years_at_same_cutoff": f"{rank}/{len(same_cut)} (1 = menos nieve)",
        }

    # --- Modelo 1: Empírico ---
    model_empirical = {
        "name": "Empírico — climatología cruda (sin condicionar)",
        "targets": {
            "snow_depth_jul4_m":  _ic(stats["snow_depth_jul4_m"]),
            "snow_cum_jul3_cm":   _ic(stats["snow_cum_jul3_cm"]),
            "week_jul4_10_snow_cm": _ic(stats["week_jul4_10_snow_cm"]),
        },
        "interpretation": "Distribución bruta de los 25 años. Es la mejor estimación SIN usar info de 2026 (i.e. lo que predeciríamos en marzo).",
    }

    # --- Modelo 2: Análogos top-K por estado al 15-may ---
    model_analog = None
    if current_snapshot:
        K = 5
        cur_cum = current_snapshot["snow_cum_cm"]
        cur_pp = current_snapshot["precip_cum_mm"]
        # normalizar para distancia: usar z-score sobre histórico
        cum_vals = [r["snow_cum_may15_cm"] for r in rows]
        pp_vals = [r["precip_cum_may15_mm"] for r in rows]
        cum_std = statistics.stdev(cum_vals) or 1
        pp_std = statistics.stdev(pp_vals) or 1
        cum_mean = statistics.mean(cum_vals)
        pp_mean = statistics.mean(pp_vals)

        def dist(r):
            dz_cum = (r["snow_cum_may15_cm"] - cur_cum) / cum_std
            dz_pp = (r["precip_cum_may15_mm"] - cur_pp) / pp_std
            return math.sqrt(dz_cum**2 + dz_pp**2)

        ranked = sorted(rows, key=dist)
        analogs = ranked[:K]
        an_depths = [a["snow_depth_jul4_m"] for a in analogs if a["snow_depth_jul4_m"] is not None]
        an_cum_jul3 = [a["snow_cum_jul3_cm"] for a in analogs]
        an_week = [a["week_jul4_10_snow_cm"] for a in analogs]
        an_totals = [a["season_total_snow_cm"] for a in analogs]
        model_analog = {
            "name": f"Top-{K} análogos por (snow+precip) al 15-may",
            "current_state": {"snow_may15_cm": cur_cum, "precip_may15_mm": cur_pp},
            "analog_years": [{
                "year": a["year"],
                "may15_snow_cm": a["snow_cum_may15_cm"],
                "may15_precip_mm": a["precip_cum_may15_mm"],
                "jul4_depth_m": a["snow_depth_jul4_m"],
                "jul3_cum_cm": a["snow_cum_jul3_cm"],
                "week_snow_cm": a["week_jul4_10_snow_cm"],
                "season_total_cm": a["season_total_snow_cm"],
            } for a in analogs],
            "predictions": {
                "snow_depth_jul4_m": _pred(an_depths),
                "snow_cum_jul3_cm": _pred(an_cum_jul3),
                "week_jul4_10_snow_cm": _pred(an_week),
                "season_total_cm": _pred(an_totals),
            },
            "interpretation": "Los K años con estado más parecido al actual al 15-may (acumulado de nieve + precip), distancia euclidiana en z-scores.",
        }

    # --- Modelo 3: Regresión lineal jul3_cum ~ may15_cum ---
    xs = [r["snow_cum_may15_cm"] for r in rows]
    ys_cum = [r["snow_cum_jul3_cm"] for r in rows]
    ys_depth = [r["snow_depth_jul4_m"] for r in rows]
    reg_cum = linear_regression(xs, ys_cum)
    reg_depth = linear_regression(xs, ys_depth)
    model_regression = None
    if reg_cum and current_snapshot:
        x = current_snapshot["snow_cum_cm"]
        pred_cum = reg_cum["intercept"] + reg_cum["slope"] * x
        pred_depth = (reg_depth["intercept"] + reg_depth["slope"] * x) if reg_depth else None
        model_regression = {
            "name": "Regresión lineal: target ~ may15_cumulative",
            "snow_cum_jul3_cm": {
                "formula": f"y = {reg_cum['intercept']} + {reg_cum['slope']} · x_may15",
                "r2": reg_cum["r2"],
                "residual_se": reg_cum["residual_se"],
                "prediction": round(pred_cum, 1),
                "ic_68": [round(pred_cum - reg_cum["residual_se"], 1), round(pred_cum + reg_cum["residual_se"], 1)],
            },
            "snow_depth_jul4_m": ({
                "formula": f"y = {reg_depth['intercept']} + {reg_depth['slope']} · x_may15",
                "r2": reg_depth["r2"],
                "residual_se": reg_depth["residual_se"],
                "prediction": round(pred_depth, 3),
                "ic_68": [round(pred_depth - reg_depth["residual_se"], 3), round(pred_depth + reg_depth["residual_se"], 3)],
            } if reg_depth and pred_depth is not None else None),
            "interpretation": "R² bajo (esperable: 50 días separan may15 de jul4, mucha varianza intermedia).",
        }

    output = {
        "phase": 1,
        "focus": "Estado del manto al inicio de la semana 4-jul (target primario)",
        "snapshot_date": snapshot,
        "n_historical_years": len(rows),
        "year_range": [historical_years[0], historical_years[-1]],
        "primary_targets": ["snow_depth_jul4_m", "snow_cum_jul3_cm"],
        "secondary_targets": ["week_jul4_10_snow_cm", "week_jul4_10_depth_end_m"],
        "descriptive_stats": stats,
        "manto_probabilities": probs_manto,
        "correlations": correlations,
        "current_season_snapshot": current_snapshot,
        "predictive_models": {
            "empirical": model_empirical,
            "analog": model_analog,
            "regression": model_regression,
        },
        "per_year_table": rows,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print_report(output)
    print(f"\nGuardado -> {OUTPUT_FILE}")


def _ic(stats_block):
    return {
        "median": stats_block["median"],
        "mean": stats_block["mean"],
        "ic50": [stats_block["p25"], stats_block["p75"]],
        "ic80": [stats_block["p10"], stats_block["p90"]],
        "range": [stats_block["min"], stats_block["max"]],
    }


def _pred(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "median": round(statistics.median(vals), 2),
        "mean": round(statistics.mean(vals), 2),
        "range": [round(min(vals), 2), round(max(vals), 2)],
        "n": len(vals),
    }


def fmt(v, suf=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}{suf}"
    return f"{v}{suf}"


def print_report(out):
    print("=" * 80)
    print(f"  CERRO CATEDRAL — PROYECCIÓN ESTADO DEL MANTO AL 4-JUL  (Fase 1)")
    print(f"  Snapshot: {out['snapshot_date']} | {out['n_historical_years']} años ({out['year_range'][0]}-{out['year_range'][1]})")
    print("=" * 80)

    print("\n--- TARGETS PRIMARIOS — Estado del manto al ARRANCAR la semana ---")
    for key in ["snow_depth_jul4_m", "snow_cum_jul3_cm"]:
        s = out["descriptive_stats"][key]
        print(f"\n  {s['label']}  (n={s['n']})")
        print(f"    mean {fmt(s['mean'])}  median {fmt(s['median'])}  stdev {fmt(s['stdev'])}")
        print(f"    min {fmt(s['min'])}  p10 {fmt(s['p10'])}  p25 {fmt(s['p25'])}  p75 {fmt(s['p75'])}  p90 {fmt(s['p90'])}  max {fmt(s['max'])}")

    print("\n--- TARGETS SECUNDARIOS — Lo que pasa durante la semana ---")
    for key in ["week_jul4_10_snow_cm", "week_jul4_10_depth_end_m"]:
        s = out["descriptive_stats"][key]
        print(f"\n  {s['label']}  (n={s['n']})")
        print(f"    mean {fmt(s['mean'])}  median {fmt(s['median'])}  stdev {fmt(s['stdev'])}")
        print(f"    min {fmt(s['min'])}  p25 {fmt(s['p25'])}  p75 {fmt(s['p75'])}  max {fmt(s['max'])}")

    print("\n--- PROBABILIDADES DEL MANTO AL 4-JUL (climatología) ---")
    for k, v in out["manto_probabilities"].items():
        bar = "█" * int((v or 0) * 30)
        print(f"  {k:<30}  {fmt(v)}  {bar}")

    print("\n--- CORRELACIONES (qué predice el estado al 4-jul) ---")
    for k, v in out["correlations"].items():
        flag = "  *" if v is not None and abs(v) >= 0.4 else ""
        print(f"  {k:<35}  r = {fmt(v)}{flag}")

    cs = out["current_season_snapshot"]
    if cs:
        print(f"\n--- ESTADO 2026 (al {cs['as_of']}) ---")
        print(f"  Acumulado: {cs['snow_cum_cm']} cm  |  Precip: {cs['precip_cum_mm']} mm  |  Manto actual: {fmt(cs['snow_depth_m'])} m")
        print(f"  Ranking vs histórico al mismo corte: {cs['rank_among_years_at_same_cutoff']}")

    print("\n" + "=" * 80)
    print("  MODELOS PREDICTIVOS — Estado del manto al 4-jul 2026")
    print("=" * 80)

    pm = out["predictive_models"]

    e = pm["empirical"]
    print(f"\n[1] {e['name']}")
    for tname, tblk in e["targets"].items():
        print(f"   {tname}:  mediana {tblk['median']} | media {tblk['mean']} | IC50 {tblk['ic50']} | IC80 {tblk['ic80']}")
    print(f"   → {e['interpretation']}")

    a = pm["analog"]
    if a:
        print(f"\n[2] {a['name']}")
        print(f"   estado actual: snow {a['current_state']['snow_may15_cm']} cm, precip {a['current_state']['precip_may15_mm']} mm")
        print(f"   años análogos:")
        print(f"     {'año':>5} | {'may15_snow':>10} | {'may15_pp':>9} | {'jul4_depth':>10} | {'jul3_cum':>9} | {'week_snow':>9} | {'season_tot':>10}")
        for an in a["analog_years"]:
            depth_str = f"{an['jul4_depth_m']:.2f}m" if an['jul4_depth_m'] is not None else "—"
            print(f"     {an['year']:>5} | {an['may15_snow_cm']:>8} cm | {an['may15_precip_mm']:>7} mm | {depth_str:>10} | {an['jul3_cum_cm']:>7} cm | {an['week_snow_cm']:>7} cm | {an['season_total_cm']:>8} cm")
        print(f"\n   predicciones:")
        for tname, pred in a["predictions"].items():
            if pred:
                print(f"     {tname}:  mediana {pred['median']} | media {pred['mean']} | rango {pred['range']}")

    r = pm["regression"]
    if r:
        print(f"\n[3] {r['name']}")
        rc = r["snow_cum_jul3_cm"]
        print(f"   snow_cum_jul3_cm:  {rc['formula']}  |  R² = {rc['r2']}")
        print(f"      predicción para x={out['current_season_snapshot']['snow_cum_cm']}: {rc['prediction']} cm  (±1σ {rc['ic_68']})")
        if r["snow_depth_jul4_m"]:
            rd = r["snow_depth_jul4_m"]
            print(f"   snow_depth_jul4_m: {rd['formula']}  |  R² = {rd['r2']}")
            print(f"      predicción: {rd['prediction']} m  (±1σ {rd['ic_68']})")
        print(f"   → {r['interpretation']}")

    print()


if __name__ == "__main__":
    main()
