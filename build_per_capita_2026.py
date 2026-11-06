"""
All MO Sports - Size-Adjusted ("per capita") Rankings, 2026 season

One script for every sport. Pick the sport with --sport:

    python build_per_capita_2026.py --sport fall_softball
    python build_per_capita_2026.py --sport boys_soccer
    python build_per_capita_2026.py --sport girls_volleyball
    python build_per_capita_2026.py --sport football
    python build_per_capita_2026.py --sport boys_basketball
    python build_per_capita_2026.py --sport girls_basketball

Method: Rating Over Expected
  1. Fit an expected-rating curve on this season's PUBLIC teams:
         OVR = a + b * ln(enrollment)
  2. Points Over Expected (POE) = actual OVR - expected OVR. Teams are ranked
     by POE.
  3. Private schools draw from a wider area, so their expected rating uses
     PRIVATE_FACTOR x their enrollment (a per-sport number, see SPORTS below).
     Charters stay on raw enrollment. Co-ops add up their members'
     enrollments, each member scaled by its own factor, so a public school
     co-oping with a private one gets a blended factor.
  4. A size-adjusted z-score (POE / local spread) is kept as a reference
     column only.

Every run also prints the factor the current data implies for private
schools ("measured private factor"), so you can check whether the number in
SPORTS still fits.

Inputs (in the repo the script runs in, or the GitHub raw URL as fallback):
  - <sport>_ratings_2026.json           this sport's ratings
  - mshsaa_enrollments_2026-2027.json   MSHSAA 2026-27 enrollments

Outputs:
  - per_capita_<sport>_2026.json
  - per_capita_<sport>_2026.csv
"""

import argparse
import csv
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

import numpy as np

# ---------------------------------------------------------------------------
# Per-sport config
# ---------------------------------------------------------------------------
# private_factor: multiplier on private-school enrollment. A number, or
#   "auto" to re-measure it from the current data on every run (it then moves
#   a little every week; a fixed number keeps the method steady).
# 2026 values, measured from Oct. 21 ratings by comparing private schools
# with the public-school curve (the "measured" number each run prints):
#   football 2.17 -> 2.2 | boys soccer 2.98 -> 3.0 | girls volleyball 2.98 -> 3.0
#   fall softball 0.88 -> 1.0: privates land above the curve only about half
#   the time in softball, so no adjustment.
# boys + girls basketball: "auto" until the ratings settle (early-season ratings are
#   noisy). Once January's runs print a steady "measured from current data"
#   number, round it to one decimal and put it here, same as the fall sports.
SPORTS = {
    "football": {
        "label": "Football",
        "repo": "football-ratings-2026",
        "ratings_file": "football_ratings_2026.json",
        "private_factor": 2.2,
    },
    "fall_softball": {
        "label": "Fall Softball",
        "repo": "Fall_Softball_Ratings_2026",
        "ratings_file": "fall_softball_ratings_2026.json",
        "private_factor": 1.0,
    },
    "boys_soccer": {
        "label": "Boys Soccer",
        "repo": "Boys_Soccer_Ratings_2026",
        "ratings_file": "boys_soccer_ratings_2026.json",
        "private_factor": 3.0,
    },
    "girls_volleyball": {
        "label": "Girls Volleyball",
        "repo": "Girls_Volleyball_Ratings_2026",
        "ratings_file": "girls_volleyball_ratings_2026.json",
        "private_factor": 3.0,
    },
    "boys_basketball": {
        "label": "Boys Basketball",
        "repo": "Boys_Basketball_Ratings_2026",
        "ratings_file": "boys_basketball_ratings_2026.json",
        "private_factor": "auto",
    },
    "girls_basketball": {
        "label": "Girls Basketball",
        "repo": "Girls_Basketball_Ratings_2026",
        "ratings_file": "girls_basketball_ratings_2026.json",
        "private_factor": "auto",
    },
}

SEASON = 2026
ENROLLMENT_FILE = "mshsaa_enrollments_2026-2027.json"
RAW_BASE = "https://raw.githubusercontent.com/AllMOSports/{repo}/main/{file}"

# Co-ops: True = sum every member's enrollment; False = primary school only.
COOP_COMBINE = True

# Private (non-boundaried, non-charter) schools. Their enrollment gets the
# sport's private_factor. Public magnet schools (Metro, Lincoln College Prep,
# Paseo, etc.) are treated as public. Edit as needed.
PRIVATE_SCHOOLS = {
    "Barstow", "Bishop DuBourg", "Bishop LeBlond", "Calvary Lutheran", "Cardinal Ritter",
    "Chaminade College Prep", "Christian Brothers College",
    "Christian Fellowship Sr.", "Columbia Independent", "Cor Jesu Academy",
    "Cristo Rey", "Crossroads College Preparatory", "De Smet Jesuit",
    "Duchesne", "Faith Christian Academy", "Father Tolton", "Gloria Deo Academy", "Greenwood",
    "Helias Catholic", "Incarnate Word Academy", "John Burroughs",
    "Lutheran (Kansas City)", "Lutheran North", "Lutheran South",
    "Lutheran St. Charles", "MICDS", "McAuley Catholic",
    "Missouri Military Academy", "Nerinx Hall", "New Covenant Academy",
    "New Heights Christian", "Northland Christian",
    "Notre Dame (Cape Girardeau)", "Notre Dame (St. Louis)",
    "Notre Dame de Sion", "Pembroke Hill", "Principia", "Priory", "Rockhurst",
    "Rosati-Kain", "SLUH", "Sacred Heart", "Saxony Lutheran",
    "School of the Ozarks", "Springfield Catholic", "St. Dominic",
    "St. Francis Borgia", "St. Joseph Christian", "St. Joseph's Academy",
    "St. Mary's South Side", "St. Michael the Archangel",
    "St. Paul Lutheran (Concordia)", "St. Paul Lutheran (Farmington)",
    "St. Pius X (Festus)", "St. Pius X (Kansas City)", "St. Teresa's Academy",
    "St. Vincent", "Summit Christian Academy",
    "Thomas Jefferson Independent Day", "Ursuline Academy", "Valle Catholic",
    "Veritas Christian Academy", "Vianney", "Villa Duchesne",
    "Visitation Academy", "Westminster Christian Academy", "Whitfield",
}

# Charter schools: non-boundaried, but as a group they don't outperform their
# enrollment, so they stay on raw enrollment (factor 1.0).
CHARTER_SCHOOLS = {
    "Academie Lafayette", "Believe Acad Charter", "Confluence Prep Academy Charter",
    "Crossroads Prep Charter", "Ewing Marion Kauffman", "Frontier STEM",
    "Frontier School of Excellence Charter", "Gateway Science Charter",
    "Grand Center Arts Charter", "Guadalupe Centers Charter",
    "Hogan Prep Academy Charter", "KIPP KC Legacy", "KIPP St. Louis Sr.",
    "Lift for Life Academy Charter", "University Academy Charter",
}

# Manual enrollment fills/fixes, keyed by individual school name.
ENROLLMENT_OVERRIDES = {
    # "School Name": 123,
}

PLAYS_LIKE_ROUND = 10
SPREAD_FLOOR_FRAC = 0.5


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_json(filename, repo):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            print(f"  loaded {path}")
            return json.load(f)
    url = RAW_BASE.format(repo=repo, file=filename)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            print(f"  loaded {url}")
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        sys.exit(f"ERROR: could not load {filename} locally or from {url}: {e}")


def members_of(school):
    """'A with B, C' -> ['A', 'B', 'C']; a single school -> [school]."""
    if " with " not in school:
        return [school]
    primary, partners = school.split(" with ", 1)
    return [primary.strip()] + [p.strip() for p in partners.split(",")]


def school_type(name):
    if name in PRIVATE_SCHOOLS:
        return "private"
    if name in CHARTER_SCHOOLS:
        return "charter"
    return "public"


def resolve_team(school, lookup, private_factor):
    """Return (enrollment, effective_enrollment, type, missing_members) or None."""
    members = members_of(school)
    if not COOP_COMBINE:
        members = members[:1]
    known = []
    missing = []
    for m in members:
        if m in ENROLLMENT_OVERRIDES:
            known.append((m, float(ENROLLMENT_OVERRIDES[m])))
        elif m in lookup:
            known.append((m, lookup[m]))
        else:
            missing.append(m)
    if not known or members[0] in missing:
        return None
    enr = sum(e for _, e in known)
    eff = sum(e * (private_factor if school_type(m) == "private" else 1.0) for m, e in known)
    types = {school_type(m) for m, _ in known}
    if types == {"public"}:
        kind = "public"
    elif len(types) == 1:
        kind = types.pop()
    else:
        kind = "mixed"
    return enr, eff, kind, missing


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def fit_line(x, y):
    """Ordinary least squares y = a + b*x. Returns (a, b, r2)."""
    b, a = np.polyfit(x, y, 1)
    pred = a + b * x
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(a), float(b), r2


def measured_private_factor(teams, a, b):
    """Factor that would make the average private team land exactly on the curve."""
    res = [t["ovr_rating"] - (a + b * math.log(t["enrollment"]))
           for t in teams if t["school_type"] == "private"]
    if len(res) < 3:
        return None, len(res)
    return math.exp(float(np.mean(res)) / b), len(res)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    cfg = SPORTS[args.sport]
    sport = args.sport

    print(f"{cfg['label']} {SEASON}: loading data...")
    ratings = load_json(cfg["ratings_file"], cfg["repo"])
    enr_data = load_json(ENROLLMENT_FILE, cfg["repo"])
    lookup = {t["name"]: float(t["enrollment"]) for t in enr_data["teams"] if t.get("enrollment")}

    # --- 1. Curve on public teams (raw enrollment) ------------------------
    base = []
    skipped = []
    for t in ratings["teams"]:
        r = resolve_team(t["school"], lookup, 1.0)
        if r is None:
            skipped.append(t["school"])
            continue
        enr, _, kind, missing = r
        if missing:
            print(f"  co-op {t['school']}: no enrollment for {missing}, using known members only")
        base.append({**t, "enrollment": enr, "school_type": kind})
    if skipped:
        print(f"\nSkipped {len(skipped)} team(s) with no enrollment "
              f"(add to ENROLLMENT_OVERRIDES): {', '.join(skipped)}")

    fit_set = [t for t in base if t["school_type"] == "public"]
    x_fit = np.log(np.array([t["enrollment"] for t in fit_set]))
    y_fit = np.array([t["ovr_rating"] for t in fit_set])
    a, b, r2 = fit_line(x_fit, y_fit)
    if b <= 0:
        sys.exit("ERROR: fitted slope is not positive; check input data.")

    measured, n_priv = measured_private_factor(base, a, b)
    factor = cfg["private_factor"]
    if factor == "auto":
        factor = round(measured, 1) if measured else 1.0
    factor = float(factor)

    # --- 2. Spread model --------------------------------------------------
    abs_res = np.abs(y_fit - (a + b * x_fit))
    sa, sb, _ = fit_line(x_fit, abs_res)
    k = math.sqrt(math.pi / 2)
    typical_sd = float(np.std(y_fit - (a + b * x_fit), ddof=2))
    sd_floor = SPREAD_FLOOR_FRAC * typical_sd
    enr_min = min(t["enrollment"] for t in base)
    enr_max = max(t["enrollment"] for t in base)

    def expected(e):
        return a + b * math.log(e)

    def local_sd(e):
        return max(k * (sa + sb * math.log(e)), sd_floor)

    def plays_like(ovr):
        raw = math.exp((ovr - a) / b)
        clamped = min(max(raw, enr_min), enr_max)
        flag = "max" if raw > enr_max else ("min" if raw < enr_min else None)
        return int(round(clamped / PLAYS_LIKE_ROUND) * PLAYS_LIKE_ROUND), flag

    # --- 3. Score every team ----------------------------------------------
    teams = []
    for t in base:
        enr, eff, kind, _ = resolve_team(t["school"], lookup, factor)
        exp_ovr = expected(eff)
        poe = t["ovr_rating"] - exp_ovr
        sd = local_sd(eff)
        pl, pl_flag = plays_like(t["ovr_rating"])
        teams.append({
            **t,
            "school_type": kind,
            "non_boundaried": kind != "public",
            "enrollment": enr,
            "effective_enrollment": eff,
            "private_factor": round(eff / enr, 2),
            "expected_ovr": round(exp_ovr, 2),
            "points_over_expected": round(poe, 2),
            "z_score": round(poe / sd, 3),
            "local_sd": round(sd, 2),
            "plays_like_enrollment": pl,
            "plays_like_capped": pl_flag,
            "size_multiple": round(pl / enr, 2),
        })

    # --- 4. Ranks -----------------------------------------------------------
    teams.sort(key=lambda t: (t["points_over_expected"], t["z_score"]), reverse=True)
    for i, t in enumerate(teams, 1):
        t["per_capita_rank"] = i
    for i, t in enumerate(sorted(teams, key=lambda t: t["z_score"], reverse=True), 1):
        t["z_rank"] = i
    for i, t in enumerate([t for t in teams if not t["non_boundaried"]], 1):
        t["public_rank"] = i
    for c in sorted({t["classification"] for t in teams}):
        for i, t in enumerate([t for t in teams if t["classification"] == c], 1):
            t["class_rank"] = i

    # --- 5. Write -----------------------------------------------------------
    fields = ["per_capita_rank", "public_rank", "class_rank", "z_rank",
              "school", "classification", "district", "school_type", "non_boundaried",
              "enrollment", "private_factor", "effective_enrollment",
              "ovr_rating", "ovr_rank", "expected_ovr", "points_over_expected",
              "z_score", "local_sd", "plays_like_enrollment", "plays_like_capped",
              "size_multiple"]
    rows = [{f: (int(round(t[f])) if f in ("enrollment", "effective_enrollment") else t.get(f))
             for f in fields} for t in teams]

    out = {
        "season": SEASON,
        "sport": sport,
        "sport_label": cfg["label"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ratings_last_updated": ratings.get("last_updated"),
        "method": {
            "description": "Rating Over Expected: OVR minus the expected OVR for a school "
                           "of that enrollment, fit on this season's public-school teams.",
            "curve": "expected_ovr = a + b * ln(enrollment)",
            "a": round(a, 4), "b": round(b, 4), "r_squared": round(r2, 4),
            "fit_on": "public schools", "fit_n": len(fit_set),
            "spread_model": "local_sd = sqrt(pi/2) * (sa + sb * ln(enrollment))",
            "sa": round(sa, 4), "sb": round(sb, 4), "typical_sd": round(typical_sd, 2),
            "enrollment_range": [int(enr_min), int(enr_max)],
            "sort": "points_over_expected (descending)",
            "private_factor": factor,
            "private_factor_setting": cfg["private_factor"],
            "measured_private_factor": round(measured, 2) if measured else None,
            "private_factor_applies_to": "private schools (charters use raw enrollment; "
                                         "co-ops scale each member separately)",
            "coops_combined": COOP_COMBINE,
        },
        "skipped_no_enrollment": skipped,
        "teams": rows,
    }

    here = os.path.dirname(os.path.abspath(__file__))
    out_json = os.path.join(here, f"per_capita_{sport}_{SEASON}.json")
    out_csv = os.path.join(here, f"per_capita_{sport}_{SEASON}.csv")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # --- Console summary ----------------------------------------------------
    print(f"\nCurve: expected OVR = {a:.3f} + {b:.3f} * ln(enrollment)   "
          f"R^2 = {r2:.3f}  (n = {len(fit_set)} public teams)")
    print(f"Every doubling of enrollment = +{b * math.log(2):.2f} expected OVR")
    if measured:
        print(f"Private factor used: {factor:g}   measured from current data: "
              f"{measured:.2f} ({n_priv} private teams)")
    print(f"\nTop 15 ({len(teams)} teams scored):")
    print(f"{'#':>3}  {'School':<36}{'Enr':>6}{'xF':>5}{'OVR':>8}{'Exp':>8}{'POE':>8}")
    for t in teams[:15]:
        tag = {"private": " *", "charter": " c", "mixed": " m"}.get(t["school_type"], "")
        print(f"{t['per_capita_rank']:>3}  {(t['school'] + tag)[:35]:<36}"
              f"{int(t['enrollment']):>6}{t['private_factor']:>5.1f}{t['ovr_rating']:>8.2f}"
              f"{t['expected_ovr']:>8.2f}{t['points_over_expected']:>+8.2f}")
    print("  * private   c charter   m co-op mixing public and private/charter")
    print(f"\nWrote {os.path.basename(out_json)} and {os.path.basename(out_csv)}")


if __name__ == "__main__":
    main()
