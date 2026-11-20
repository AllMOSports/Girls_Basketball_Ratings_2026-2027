"""
MSHSAA Girls Basketball 2026-27 -- games, schedule, and ratings in one run
=============================================================================
Same consolidated layout as Boys_Soccer_Ratings_2026.py:
  1. games JSON/CSV (strict + _all)
  2. per-team schedule JSON/CSV (with predicted scores + deltas)
  3. ratings JSON/CSV, per-class JSONs, rankings CSVs

One scrape of the MSHSAA scoreboard covers everything: every date from
SEASON_START through SEASON_END (no stop at today), so upcoming games land
in the schedule files too. The ratings only use completed games from that
same scrape -- both teams classified, both scores posted, marked Final,
not a forfeit.

Season label: 2026 = the season that STARTS in Nov 2026 (2026-27), matching
football and the 2025 label used for last season's basketball files.

REQUIRES (same directory):
  - classifications.json   (Girls Basketball's own -- refresh after MSHSAA's
                            Nov 20, 2026 class & district release)
  - mshsaa_schools.csv
  - girls_basketball_manual_name_overrides.json   (optional -- schedule name
    corrections/exclusions/score fills; skipped if missing)
"""

import requests
from bs4 import BeautifulSoup
import json
import csv
import re
import pandas as pd
from datetime import datetime, date, timedelta
import time
import socket
import urllib3.util.connection as urllib3_cn
from datetime import timezone
from collections import Counter
import difflib

# Force IPv4: mshsaa.org resolves to IPv4 and IPv6, and the IPv6 route has
# failed before ("Network is unreachable", errno 101). Harmless otherwise.
def _force_ipv4_only():
    return socket.AF_INET

urllib3_cn.allowed_gai_family = _force_ipv4_only

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

SEASON_YEAR   = 2026
SEASON_START  = date(2026, 11, 9)    # a week+ before the first contests, to catch early results
SEASON_END    = date(2027, 3, 20)    # Show-Me Showdown II (Classes 4-6) finals
BASE_URL      = "https://www.mshsaa.org/activities/scoreboard.aspx?alg=6&date={}"
MAX_POINTS    = 150   # scores above this are treated as blank (same as 2025-26)
RATINGS_MAX_POINTS = 150   # games with a score above this aren't rated
OUTPUT_PATH   = f"girls_basketball_ratings_{SEASON_YEAR}.json"
CSV_PATH      = f"girls_basketball_scoreboard_{SEASON_YEAR}.csv"
CLASSIFICATIONS_PATH  = "classifications.json"

# --- Games files ---
OUTPUT_JSON           = f"girls_basketball_games_{SEASON_YEAR}.json"       # both teams classified
OUTPUT_CSV            = f"girls_basketball_games_{SEASON_YEAR}.csv"
OUTPUT_JSON_ALL       = f"girls_basketball_games_{SEASON_YEAR}_all.json"   # >=1 team classified
OUTPUT_CSV_ALL        = f"girls_basketball_games_{SEASON_YEAR}_all.csv"
MANUAL_OVERRIDES_PATH = "girls_basketball_manual_name_overrides.json"
REQUEST_DELAY         = 0.5   # seconds between scoreboard requests

# --- Schedule files ---
SCHEDULE_JSON_PATH    = f"girls_basketball_schedule_{SEASON_YEAR}.json"
SCHEDULE_CSV_PATH     = f"girls_basketball_schedule_{SEASON_YEAR}.csv"

# --- Missing-scores checklist ---
# Past games (at least MISSING_SCORE_GRACE_DAYS old) with no score or only
# one team's score. Fill them in via "score_corrections" in
# MANUAL_OVERRIDES_PATH -- see save_missing_scores_report().
MISSING_SCORES_CSV_PATH  = f"girls_basketball_missing_scores_{SEASON_YEAR}.csv"
MISSING_SCORE_GRACE_DAYS = 2

# --- Unscheduled-teams checklist ---
# Classified teams with NO games anywhere in the scrape (scheduled or
# completed), plus the closest unmatched scoreboard names and their MSHSAA
# IDs -- usually the s= ID to add to MANUAL_OVERRIDES.
UNSCHEDULED_CSV_PATH = f"girls_basketball_unscheduled_teams_{SEASON_YEAR}.csv"
SCHOOLS_CSV           = "mshsaa_schools.csv"
ITERATIONS            = 1000
LEARNING_RATE         = 0.1

# --- v2 rating engine settings (soft weighting + shrinkage) ---
# Carried over from the 2025-26 girls basketball script. Basketball sits on a
# points scale between soccer (goals) and football, so these are starting
# points -- revisit once a few weeks of 2026-27 games are in.
COMPETITIVE_THRESHOLD = 30    # half-weight at a 30-point combined rating gap
REGULARIZATION_K      = 3.0   # pseudo-games added to every team's denominator
MOV_CAP               = 25    # a single game can contribute at most 25 points of error

# --- Early-season blend with last season (2025-26) ---
# Until a team has played BLEND_FULL_GAMES rated games, its OFF and DEF are a
# mix of this season's fit and last season's final ratings:
#     weight on this season = games played / BLEND_FULL_GAMES (capped at 1)
#     2025-26 rating used   = final 2025-26 rating x PRIOR_KEEP
# PRIOR_KEEP < 1 pulls last season toward average (seniors graduate). Teams
# with no 2025-26 rating (new co-ops, new schools) start at their 2026-27 class
# average. Teams with fewer than MIN_GAMES_SHOWN games are left out of the
# ratings/rankings files, but still get projected scores on the schedule.
PRIOR_URL        = "https://raw.githubusercontent.com/AllMOSports/Girls_Basketball_Ratings_2025-2026/main/ratings.json"
PRIOR_LOCAL_PATH = "girls_basketball_ratings_2025_final.json"   # optional local copy; used first if present
PRIOR_KEEP       = 0.67
BLEND_FULL_GAMES = 8
MIN_GAMES_SHOWN  = 3

# League average used for projected scores until enough games are in; then
# this season's real average takes over (blended over LEAGUE_AVG_FULL_GAMES).
LEAGUE_AVG_DEFAULT   = 44.5
LEAGUE_AVG_FULL_GAMES = 300

# Filled in by main() and written into the ratings files for reference.
GAMES_PLAYED = {}
CURRENT_WEIGHT = {}

# ---------------------------------------------------------------------------
# MANUAL GAMES (not listed on MSHSAA Scoreboard)
# ---------------------------------------------------------------------------
# Add any games missing from the MSHSAA scoreboard here.
# Format: ("YYYY-MM-DD", "Team 1 Name", score1, "Team 2 Name", score2)
# Team names must match exactly the names in classifications.json.

MANUAL_GAMES = [
]

# ---------------------------------------------------------------------------
# SCORE CORRECTIONS
# ---------------------------------------------------------------------------
# Fixes for games that scraped with a bad score. Matched by date + the two
# team names (order-independent), then each team's score is set explicitly.
# Format: ("YYYY-MM-DD", "Team A", correct_score_A, "Team B", correct_score_B)

SCORE_CORRECTIONS = [
]

# ---------------------------------------------------------------------------
# EXCLUDED GAMES
# ---------------------------------------------------------------------------
# Games to drop entirely -- confirmed bad/unverifiable entries rather than
# fixable score typos. Matched by date + the two team names (order-independent).
# Format: ("YYYY-MM-DD", "Team A", "Team B")

EXCLUDED_GAMES = [
]

# ---------------------------------------------------------------------------
# EXCLUDED TEAMS
# ---------------------------------------------------------------------------
# Teams removed from the ratings entirely. Every game involving them is
# dropped before the fit. Names must match classifications.json exactly.

EXCLUDED_TEAMS = [
]

# Opponents that didn't resolve to a classifications.json name. They're kept
# in the games/schedule files (as unclassified) but never rated. Listed after
# the scrape so a missed Missouri school stands out -- add its ID to
# MANUAL_OVERRIDES if it should be classified.
UNCLASSIFIED_OPPONENTS = Counter()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.mshsaa.org/"
}

# ---------------------------------------------------------------------------
# HTTP SESSION (connection reuse + retry on transient failures)
# ---------------------------------------------------------------------------

def build_session():
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except ImportError:
        from requests.packages.urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# ---------------------------------------------------------------------------
# CLASSIFICATIONS
# ---------------------------------------------------------------------------

def load_classifications(path=CLASSIFICATIONS_PATH):
    """Return team_to_class and team_to_district dicts keyed by school name."""
    with open(path) as f:
        data = json.load(f)
    team_to_class    = {}
    team_to_district = {}
    for entry in data["teams"]:
        school = entry["school"]
        team_to_class[school]    = entry["classification"]
        team_to_district[school] = entry["district"]
    return team_to_class, team_to_district


# ---------------------------------------------------------------------------
# NAME RESOLUTION
# ---------------------------------------------------------------------------

# Real school behind each ID (per the boys overrides), for reference.
WRONG_CSV_IDS = {
    "208": "Sumner",                 # CSV says West Plains
    "453": "Southland",              # CSV says Sweet Springs
    "549": "St. Mary's South Side",  # CSV says Webster Groves
    "468": "Summersville",           # CSV says Wright City
    "450": "South Pemiscot",         # CSV says Summersville
}


def build_id_to_classname(team_to_class, schools_csv=SCHOOLS_CSV):
    """
    Build { school_id_str : classification_name } by exact-matching
    mshsaa_schools.csv names to classifications.json names after stripping
    the ' High School' suffix. No fuzzy matching used.

    MANUAL_OVERRIDES carries over the 2025-26 girls basketball list (deduped).
    That list started from the boys list and added girls-only co-ops; it was
    never fully re-verified against the girls (alg=6) scoreboard. Any entry
    whose name isn't in THIS season's classifications.json is skipped and
    printed, so co-ops that changed for 2026-27 show up in the log instead of
    silently mislabeling games.
    """
    MANUAL_OVERRIDES = {
        "431": "Salisbury",
        "432": "Santa Fe",
        "434": "Scotland County",
        "435": "Scott City",
        "437": "Seymour",
        "438": "Sheldon",
        "440": "Silex",
        "443": "Skyline",
        "194": "Smith-Cotton",   # known ID collision with Savannah in mshsaa_schools.csv
        "446": "South Harrison",
        "447": "South Holt with Craig",
        "448": "South Iron",
        "454": "Southwest (Livingston County) with Breckenridge",
        "455": "Southwest (Washburn)",
        "205": "Steelville",
        "463": "Stockton",
        "464": "Stoutland",
        "466": "Strafford",
        "467": "Sturgeon",
        "473": "Tina-Avalon with Hale",
        "198": "Truman",
        "199": "Twin Rivers",
        "204": "Van Horn",
        "206": "Vashon",
        "503": "Winston with North Daviess",
        "250": "Cainsville with Ridgeway",
        "301": "Gilman City with Tri-County",
        "309": "Tina-Avalon with Hale",
        "156": "Paris",
        "430": "Russellville",
        "207": "Sullivan",
        "445": "Smithville",
        "197": "South Callaway",
        "458": "St. Elizabeth",
        "460": "Stanberry",
        "465": "Stover",
        "193": "Slater with Malta Bend",   # girls co-op (boys list has plain "Slater")
        "456": "Sparta",
        "246": "Macon County with Bucklin",
        "449": "Jefferson (Conception) with South Nodaway",
        # girls-only entries from 2025-26
        "219": "Bloomfield with Richland (Essex)",
        "25":  "Gateway with Soldan International Studies",
        "541": "Rosati-Kain",
        "544": "St. Francis Borgia",
        # 2026-27 girls co-ops: partner schools listed under their own IDs.
        # NOT Bismarck (216) or Norborne (384): both play their own co-op
        # head-to-head on the schedule, so they're separate teams.
        "462": "Osborn with Stewartsville",
        "327": "McAuley Catholic with Jasper",
        "365": "Drexel with Miami (Amoret)",
    }

    df = pd.read_csv(schools_csv)
    known_class_names = set(team_to_class.keys())

    id_to_classname = {}
    for _, row in df.iterrows():
        full_name = row["school_name"]
        sid       = str(row["school_id"])
        stripped  = full_name.replace(" High School", "").strip()

        if stripped in known_class_names:
            id_to_classname[sid] = stripped
        elif full_name in known_class_names:
            id_to_classname[sid] = full_name

    # Apply manual overrides last so they always take priority -- but only
    # ones that point at a name in THIS season's classifications.json.
    stale = {sid: n for sid, n in MANUAL_OVERRIDES.items()
             if n not in known_class_names}
    for sid, n in MANUAL_OVERRIDES.items():
        if sid not in stale:
            id_to_classname[sid] = n
    if stale:
        print(f"  [name-resolve] Skipped {len(stale)} MANUAL_OVERRIDES entries "
              f"whose name isn't in {CLASSIFICATIONS_PATH} (update or remove):")
        for sid, n in sorted(stale.items(), key=lambda x: x[1]):
            print(f"    s={sid}: {n}")

    # mshsaa_schools.csv has wrong IDs for a run of schools (the S-W range),
    # which MANUAL_OVERRIDES normally corrects. These IDs belong to schools
    # with no girls team in the 2026-27 girls classifications, so there's no
    # override for them -- and without one the CSV would hand their games to
    # the wrong girls team (e.g. Sumner's games showed up as West Plains).
    # Unmap them so their games stay unclassified under their own names.
    for sid in WRONG_CSV_IDS:
        id_to_classname.pop(sid, None)

    print(f"  [name-resolve] {len(id_to_classname)} schools mapped by ID "
          f"({len(MANUAL_OVERRIDES) - len(stale)} via manual overrides)")
    return id_to_classname


# ---------------------------------------------------------------------------
# SCRAPING
# ---------------------------------------------------------------------------
# One pass over every date SEASON_START..SEASON_END. Keeps scheduled and
# completed games, and games against non-classified opponents, for the
# games/schedule files. rated_games_from_all() later picks out the subset
# the ratings use.

def resolve_name_or_raw(row, school_cell, id_to_classname, known_teams):
    """
    Resolve a team row to (name, classified: bool).

    Primary signal: the <tr>'s data-school attribute, which MSHSAA
    populates for every team row (members and out-of-state opponents).
    If that ID maps to a known Missouri school, use the
    classifications.json name and mark classified=True.

    Fallback: the visible name in td.school > span.name, classified=False
    unless it exactly matches a classifications.json name.
    """
    sid = (row.get("data-school") or "").strip()
    if sid and sid in id_to_classname:
        return id_to_classname[sid], True

    name_span = school_cell.find("span", class_="name")
    raw = name_span.get_text(strip=True) if name_span else None
    # A still-TBD opponent renders as the literal "(, )" -- normalize to ""
    # (NOT None) so the row is kept; dropping it drops the whole game.
    if raw is not None and re.fullmatch(r"\(\s*,\s*\)", raw):
        raw = ""
    if raw:
        raw = re.sub(r"\s*\(\s*,\s*\)\s*$", "", raw).strip()
        # Stray capital "A" glued onto some names (e.g. "Trinity AcademyA")
        raw = re.sub(r"(?<=[a-z])A$", "", raw)
    if raw and raw in known_teams:
        return raw, True
    if raw:
        UNCLASSIFIED_OPPONENTS[(raw, sid or "?")] += 1
    return raw, False


def parse_score(text):
    text = text.strip()
    if not text:
        return None
    try:
        score = int(text)
    except ValueError:
        return None
    return score if 0 <= score <= MAX_POINTS else None


def is_forfeit(row1, row2):
    return "forfeit" in (row1.get_text() + row2.get_text()).lower()


def is_overtime(row1, row2):
    """
    First-pass OT detection: "overtime" or a standalone "OT" token in the
    game's row text. Confirm against a real MSHSAA basketball OT game once
    one shows up and adjust if needed.
    """
    text = row1.get_text() + " " + row2.get_text()
    return bool(re.search(r"overtime|\bOT\b", text, re.IGNORECASE))


def scrape_date(target_date, id_to_classname, known_teams, session):
    """
    Scans every row in every table for a td.school cell (rather than fixed
    row/column positions), so it works for both completed games (score
    column) and scheduled games (no score). Tables with exactly 2 team rows
    are one game. Score is captured if present, else None.
    """
    url = BASE_URL.format(target_date.strftime("%m%d%Y"))
    try:
        resp = session.get(url, timeout=(10, 25), headers=HEADERS)
        resp.raise_for_status()
    except requests.exceptions.Timeout as e:
        print(f"  TIMEOUT {target_date}: {e}")
        return [], "timeout"
    except requests.RequestException as e:
        print(f"  Failed {target_date}: {e}")
        return [], "error"

    soup  = BeautifulSoup(resp.text, "html.parser")
    games = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        team_rows = []
        for row in rows:
            school_cell = row.find("td", class_="school")
            if school_cell is None:
                continue

            name, classified = resolve_name_or_raw(row, school_cell, id_to_classname, known_teams)
            if name is None:
                continue

            score_cell = row.find("td", class_="score")
            score = parse_score(score_cell.get_text()) if score_cell else None

            team_rows.append((name, classified, score, row))

        if len(team_rows) != 2:
            continue

        (name1, classified1, s1, row1), (name2, classified2, s2, row2) = team_rows
        if name1 == name2:
            continue

        games.append({
            "date": target_date.strftime("%Y-%m-%d"),
            "team1": name1,
            "team1_classified": classified1,
            "score1": s1,
            "team2": name2,
            "team2_classified": classified2,
            "score2": s2,
            "forfeit": is_forfeit(row1, row2),
            "overtime": is_overtime(row1, row2),
            # Internal only: whether the scoreboard marks this game Final.
            "_final": "final" in rows[-1].get_text().lower(),
        })

    return games, None


def scrape_full_season(id_to_classname, known_teams):
    all_games   = []
    current     = SEASON_START
    scrape_t0   = time.perf_counter()
    slow_days   = []
    failed_days = []
    session     = build_session()

    while current <= SEASON_END:
        day_t0 = time.perf_counter()
        print(f"  Scraping {current}...", end=" ", flush=True)
        day_games, fail_reason = scrape_date(current, id_to_classname, known_teams, session)
        all_games.extend(day_games)
        day_elapsed = time.perf_counter() - day_t0
        print(f"{len(day_games)} games ({day_elapsed:.1f}s)")
        if day_elapsed > 3.0:
            slow_days.append((current, day_elapsed))
        if fail_reason is not None:
            failed_days.append((current, fail_reason))
        current += timedelta(days=1)
        time.sleep(REQUEST_DELAY)

    # Second pass: retry any date that never returned data (carried over
    # from the 2025-26 basketball script -- most failures are transient).
    if failed_days:
        print(f"\n  Retrying {len(failed_days)} failed date(s) (second pass)...")
        still_failed = []
        for d, reason in failed_days:
            time.sleep(1.0)
            print(f"  Retrying {d}...", end=" ", flush=True)
            day_games, fail_reason = scrape_date(d, id_to_classname, known_teams, session)
            print(f"{len(day_games)} games")
            if fail_reason is None:
                all_games.extend(day_games)
            else:
                still_failed.append((d, fail_reason))
        failed_days = still_failed

    scrape_elapsed = time.perf_counter() - scrape_t0
    print(f"\n  [TIMING] Scraping took {scrape_elapsed:.1f}s total "
          f"for {len(all_games)} games.")
    if slow_days:
        print(f"  [TIMING] {len(slow_days)} slow day(s) (>3s each):")
        for d, secs in slow_days:
            print(f"    {d}: {secs:.1f}s")
    if failed_days:
        print(f"\n  *** {len(failed_days)} date(s) NEVER returned data, "
              f"even after retry: ***")
        for d, reason in failed_days:
            print(f"    {d} ({reason})")
    else:
        print("  All dates returned successfully -- no known data gaps "
              "from scraping failures.")
    if UNCLASSIFIED_OPPONENTS:
        top = UNCLASSIFIED_OPPONENTS.most_common(25)
        print(f"\n  {len(UNCLASSIFIED_OPPONENTS)} opponent name(s) aren't in "
              f"{CLASSIFICATIONS_PATH} (kept in the schedule, not rated). "
              f"Most frequent -- check for any missed Missouri schools:")
        for (name, sid), n in top:
            print(f"    {name} (s={sid}): {n}")
    return all_games


# ---------------------------------------------------------------------------
# GAMES FILE CLEANUP
# ---------------------------------------------------------------------------

def deduplicate_schedule_games(all_games):
    """Score-independent dedup on (date, both team names)."""
    seen = set()
    unique_games = []
    duplicates = 0
    for g in all_games:
        key = (g["date"], frozenset([g["team1"], g["team2"]]))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique_games.append(g)

    if duplicates:
        print(f"  Removed {duplicates} duplicate game(s). "
              f"{len(unique_games)} unique games remain.")
    else:
        print(f"  No duplicates found. {len(unique_games)} games.")
    return unique_games


def load_manual_overrides(path=MANUAL_OVERRIDES_PATH):
    """
    Loads the hand-maintained corrections/exclusions file.

    corrections: keyed by (date, the ALREADY-classified team's name) ->
    corrected name for the OTHER (unclassified) side.

    exclusions: exact (date, team1, team2) triples (as originally scraped)
    for specific bad/duplicate games to drop outright.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"  [overrides] {path} not found -- skipping corrections/exclusions.")
        return {}, set()
    except json.JSONDecodeError as e:
        print(f"  [overrides] WARNING: {path} is not valid JSON ({e}). "
              f"Skipping corrections/exclusions for this run -- fix the "
              f"file (check for a stray comma or brace near that line) "
              f"and re-run to pick them back up.")
        return {}, set()

    corrections = {
        (c["date"], c["known_team"]): c["corrected_opponent"]
        for c in data.get("corrections", [])
    }
    exclusions = {
        (e["date"], e.get("team1"), e.get("team2"))
        for e in data.get("exclusions", [])
    }
    print(f"  [overrides] Loaded {len(corrections)} correction(s) and "
          f"{len(exclusions)} exclusion(s) from {path}")
    return corrections, exclusions


def apply_manual_overrides(all_games, corrections, exclusions):
    """
    1. Drop games where NEITHER side resolved to a classified team.
    2. Drop anything in the manual exclusion list.
    3. Apply a manual correction to the unclassified side's name.
    """
    def _norm(name):
        if name is not None and (name == "" or re.fullmatch(r"\(\s*,\s*\)", name)):
            return None
        return name

    kept = []
    dropped_both_unclassified = 0
    dropped_excluded = 0
    corrected = 0

    for g in all_games:
        g["team1"] = _norm(g["team1"])
        g["team2"] = _norm(g["team2"])
        c1, c2 = g["team1_classified"], g["team2_classified"]

        if not c1 and not c2:
            dropped_both_unclassified += 1
            continue

        raw_key = (g["date"], g["team1"], g["team2"])
        if raw_key in exclusions:
            dropped_excluded += 1
            continue

        if c1 and not c2:
            fix = corrections.get((g["date"], g["team1"]))
            if fix is not None and fix != g["team2"]:
                g["team2"] = fix
                corrected += 1
        elif c2 and not c1:
            fix = corrections.get((g["date"], g["team2"]))
            if fix is not None and fix != g["team1"]:
                g["team1"] = fix
                corrected += 1

        kept.append(g)

    print(f"  [overrides] Dropped {dropped_both_unclassified} game(s) with no classified "
          f"side, {dropped_excluded} manually-excluded game(s); "
          f"applied {corrected} name correction(s). {len(kept)} games remain.")
    return kept


def load_schedule_score_corrections(path=MANUAL_OVERRIDES_PATH):
    """score_corrections list from the overrides file (fills missing scores only)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []
    return data.get("score_corrections", [])


def apply_schedule_score_corrections(all_games, score_corrections):
    """
    Fill a still-missing score from score_corrections, matched by date + the
    unordered pair of team names. A score MSHSAA has posted always wins.
    """
    if not score_corrections:
        return all_games

    index = {}
    for sc in score_corrections:
        key = (sc["date"], frozenset([sc["team1"], sc["team2"]]))
        index[key] = sc

    applied = 0
    for g in all_games:
        if g["score1"] is not None and g["score2"] is not None:
            continue
        key = (g["date"], frozenset([g["team1"], g["team2"]]))
        sc = index.get(key)
        if sc is None:
            continue
        if g["team1"] == sc["team1"]:
            g["score1"], g["score2"] = sc["score1"], sc["score2"]
        else:
            g["score1"], g["score2"] = sc["score2"], sc["score1"]
        g["_final"] = True
        applied += 1

    print(f"  [overrides] Filled in {applied} manually-provided score(s) "
          f"for game(s) MSHSAA hasn't posted a result for yet.")
    return all_games


def drop_next_day_repeats(all_games):
    """
    Drop a SCORED game when the same two teams, with the same score, are
    already listed on the previous day (MSHSAA sometimes lists a two-day
    tournament's results under both dates). The earlier date is kept.
    """
    def key(g):
        return (frozenset([g["team1"], g["team2"]]),
                frozenset([(g["team1"], g["score1"]), (g["team2"], g["score2"])]))

    by_date = {}
    for g in all_games:
        by_date.setdefault(g["date"], set()).add(key(g))

    kept, dropped = [], Counter()
    for g in all_games:
        if g["score1"] is not None and g["score2"] is not None:
            prev = (date.fromisoformat(g["date"]) - timedelta(days=1)).isoformat()
            if key(g) in by_date.get(prev, ()):
                dropped[g["date"]] += 1
                continue
        kept.append(g)

    if dropped:
        print(f"  Dropped {sum(dropped.values())} scored game(s) repeated from "
              f"the previous day: " +
              ", ".join(f"{d} ({n})" for d, n in sorted(dropped.items())))
    else:
        print("  No next-day repeats found.")
    return kept


def save_missing_scores_report(all_games):
    """
    Checklist of past games still missing a score. To fill one in, add it
    to "score_corrections" in MANUAL_OVERRIDES_PATH:
        {"date": "2026-12-04", "team1": "Vashon", "score1": 68,
         "team2": "Chaminade College Prep", "score2": 61}
    Team order doesn't matter. Once MSHSAA posts both scores, theirs wins.
    """
    cutoff = (date.today() - timedelta(days=MISSING_SCORE_GRACE_DAYS)).isoformat()
    rows = []
    for g in all_games:
        if g["date"] > cutoff:
            continue
        s1, s2 = g["score1"], g["score2"]
        if s1 is not None and s2 is not None:
            continue
        rows.append({
            "date": g["date"],
            "problem": "no score" if s1 is None and s2 is None else "one team's score only",
            "team1": g["team1"], "score1": s1,
            "team2": g["team2"], "score2": s2,
            "both_classified": g["team1_classified"] and g["team2_classified"],
        })
    rows.sort(key=lambda r: (r["problem"] != "one team's score only", r["date"]))
    _write_dict_csv(MISSING_SCORES_CSV_PATH, rows,
                    ["date", "problem", "team1", "score1", "team2", "score2",
                     "both_classified"])
    one_sided = sum(r["problem"] != "no score" for r in rows)
    print(f"  {len(rows)} game(s) on or before {cutoff} still missing a score "
          f"({one_sided} with only one team's score) -> {MISSING_SCORES_CSV_PATH}")


def strict_games_from_all(all_games):
    """Games where BOTH teams are classified, in the original schema."""
    strict = []
    for g in all_games:
        if not (g["team1_classified"] and g["team2_classified"]):
            continue
        strict.append({
            "date": g["date"],
            "team1": g["team1"],
            "score1": g["score1"],
            "team2": g["team2"],
            "score2": g["score2"],
            "forfeit": g["forfeit"],
            "overtime": g["overtime"],
        })
    return strict


def save_games_json(games, path=OUTPUT_JSON):
    clean = [{k: v for k, v in g.items() if not k.startswith("_")} for g in games]
    with open(path, "w") as f:
        json.dump(clean, f, indent=2)
    print(f"Saved {len(clean)} games to {path}")


def save_games_csv(all_games, path=OUTPUT_CSV):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "team1", "score1", "team2", "score2", "forfeit", "overtime"])
        for g in all_games:
            writer.writerow([g["date"], g["team1"], g["score1"], g["team2"], g["score2"],
                              g["forfeit"], g["overtime"]])
    print(f"Saved {len(all_games)} games to {path}")


def save_games_csv_all(all_games, path=OUTPUT_CSV_ALL):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "team1", "team1_classified", "score1",
                          "team2", "team2_classified", "score2",
                          "forfeit", "overtime"])
        for g in all_games:
            writer.writerow([g["date"], g["team1"], g["team1_classified"], g["score1"],
                              g["team2"], g["team2_classified"], g["score2"],
                              g["forfeit"], g["overtime"]])
    print(f"Saved {len(all_games)} games to {path}")


def rated_games_from_all(all_games):
    """
    Games the ratings use, as (date, team1, score1, team2, score2): both
    teams classified, both scores posted, marked Final, not a forfeit, no
    score above RATINGS_MAX_POINTS.
    """
    rated = []
    skipped = Counter()
    for g in all_games:
        if not (g["team1_classified"] and g["team2_classified"]):
            continue
        if g["score1"] is None or g["score2"] is None:
            continue
        if g["forfeit"]:
            skipped["forfeit"] += 1
            continue
        if not g.get("_final", True):
            skipped["scored but not marked Final"] += 1
            continue
        if max(g["score1"], g["score2"]) > RATINGS_MAX_POINTS:
            skipped[f"score above {RATINGS_MAX_POINTS}"] += 1
            continue
        rated.append((g["date"], g["team1"], g["score1"], g["team2"], g["score2"]))

    print(f"  {len(rated)} completed game(s) between classified teams go to the ratings.")
    for reason, n in skipped.items():
        print(f"  Not rated ({reason}): {n}")
    return rated


def apply_score_corrections(all_games, corrections=SCORE_CORRECTIONS):
    """Fix known-bad scores, matched by date + both team names (order-independent)."""
    lookup = {}
    for date_str, team_a, score_a, team_b, score_b in corrections:
        lookup[(date_str, frozenset([team_a, team_b]))] = {team_a: score_a, team_b: score_b}

    corrected = 0
    fixed_games = []
    for date_str, t1, s1, t2, s2 in all_games:
        key = (date_str, frozenset([t1, t2]))
        fix = lookup.get(key)
        if fix is not None:
            new_s1 = fix.get(t1, s1)
            new_s2 = fix.get(t2, s2)
            if (new_s1, new_s2) != (s1, s2):
                corrected += 1
            fixed_games.append((date_str, t1, new_s1, t2, new_s2))
        else:
            fixed_games.append((date_str, t1, s1, t2, s2))

    if corrected:
        print(f"  Corrected {corrected} game score(s) via SCORE_CORRECTIONS.")
    else:
        print("  No SCORE_CORRECTIONS matched (nothing changed).")

    return fixed_games


def apply_exclusions(all_games, exclusions=EXCLUDED_GAMES):
    """Drop games confirmed bad/unverifiable (date + both names, order-independent)."""
    exclude_keys = {(date_str, frozenset([team_a, team_b]))
                     for date_str, team_a, team_b in exclusions}

    filtered_games = [
        g for g in all_games
        if (g[0], frozenset([g[1], g[3]])) not in exclude_keys
    ]

    removed = len(all_games) - len(filtered_games)
    if removed:
        print(f"  Removed {removed} excluded game(s) via EXCLUDED_GAMES.")
    else:
        print("  No EXCLUDED_GAMES matched (nothing removed).")

    return filtered_games


def apply_team_exclusions(all_games, team_to_class, excluded=EXCLUDED_TEAMS):
    """Drop every game involving a team in EXCLUDED_TEAMS."""
    excluded = set(excluded)
    typos = sorted(excluded - set(team_to_class))
    if typos:
        print(f"  WARNING: EXCLUDED_TEAMS name(s) not in {CLASSIFICATIONS_PATH} "
              f"(check spelling): {typos}")

    kept = [g for g in all_games if g[1] not in excluded and g[3] not in excluded]
    removed = len(all_games) - len(kept)
    print(f"  Excluded {len(excluded)} team(s); removed {removed} game(s) "
          f"involving them.")
    return kept


def deduplicate_games(all_games):
    """Dedup rated games on (date, both team names), order-independent."""
    seen         = set()
    unique_games = []
    duplicates   = 0

    for game in all_games:
        date_str, t1, s1, t2, s2 = game
        key = (date_str, frozenset([t1, t2]))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique_games.append(game)

    if duplicates:
        print(f"  Removed {duplicates} duplicate game(s). "
              f"{len(unique_games)} unique games remain.")
    else:
        print(f"  No duplicates found. {len(unique_games)} games.")

    return unique_games


def report_missing_teams(all_games, team_to_class):
    """Print classifications.json teams with zero scraped games."""
    teams_with_games = set()
    for _, t1, _, t2, _ in all_games:
        teams_with_games.add(t1)
        teams_with_games.add(t2)

    missing = sorted(
        t for t in team_to_class
        if t not in teams_with_games and t not in EXCLUDED_TEAMS
    )

    if missing:
        print(f"\n  NO COMPLETED GAMES: {len(missing)} classification schools have "
              f"no completed games yet.")
        print(f"  Early in the season most of these just haven't played -- see")
        print(f"  the UNSCHEDULED TEAMS list for schools that may need an ID.")
        print(f"  Missing: {missing}\n")
    else:
        print("\n  All classification schools have at least one game. \n")


def _norm_name(name):
    n = name.lower().replace("high school", "").replace("'", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def suggest_matches(team, limit=3):
    """Unmatched scoreboard names that look like this classified team.
    Compares the full name and the host school (before ' with ')."""
    targets = {_norm_name(team), _norm_name(team.split(" with ")[0])}
    scored = []
    for (raw, sid), n in UNCLASSIFIED_OPPONENTS.items():
        r = _norm_name(re.sub(r"\(.*\)$", "", raw))   # drop "(City, ST)"
        if not r:
            continue
        best = max(difflib.SequenceMatcher(None, r, t).ratio() for t in targets)
        if any(r == t or (len(r) > 3 and (r in t or t in r)) for t in targets):
            best = max(best, 0.95)
        if best >= 0.75:
            scored.append((best, raw, sid, n))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


def report_unscheduled_teams(schedule_games, team_to_class, team_to_district):
    """
    Every classifications.json team that appears in NO game in the whole
    scrape -- completed or scheduled, any date SEASON_START..SEASON_END.
    These are the schools that almost certainly need an s= ID added to
    MANUAL_OVERRIDES (or have no schedule posted on MSHSAA yet). Writes
    UNSCHEDULED_CSV_PATH with suggested matches from the unmatched names.
    """
    seen = set()
    for g in schedule_games:
        if g["team1_classified"]:
            seen.add(g["team1"])
        if g["team2_classified"]:
            seen.add(g["team2"])

    missing = sorted(t for t in team_to_class
                     if t not in seen and t not in EXCLUDED_TEAMS)
    rows = []
    print("\n" + "=" * 70)
    if not missing:
        print(f"  All {len(team_to_class)} classification teams have at least "
              f"one game (scheduled or played) in the scrape.")
    else:
        print(f"  UNSCHEDULED TEAMS: {len(missing)} of {len(team_to_class)} "
              f"classification teams have NO games anywhere in the scrape")
        print(f"  (scheduled or played). Likely need an ID in MANUAL_OVERRIDES,")
        print(f"  or their schedule isn't posted on MSHSAA yet.")
        for t in missing:
            sugg = suggest_matches(t)
            hint = "; ".join(f"{raw} (s={sid}, {n} games)" for _, raw, sid, n in sugg)
            print(f"    - {t} (Class {team_to_class[t]}, District "
                  f"{team_to_district.get(t)})" + (f"  -> maybe: {hint}" if hint else ""))
            rows.append({
                "school": t,
                "classification": team_to_class[t],
                "district": team_to_district.get(t),
                "suggested_matches": hint,
                "suggested_override": (f'"{sugg[0][2]}": "{t}",'
                                       if sugg and sugg[0][2] != "?" else ""),
            })
    print("=" * 70)
    _write_dict_csv(UNSCHEDULED_CSV_PATH, rows,
                    ["school", "classification", "district",
                     "suggested_matches", "suggested_override"])
    print(f"  Wrote {UNSCHEDULED_CSV_PATH} ({len(rows)} team(s))")
    return missing


def report_teams_not_rated(ovr_rating, team_to_class):
    """Every classifications.json team NOT in the ratings output, with the reason."""
    not_rated = sorted(t for t in team_to_class if t not in ovr_rating)

    print("\n" + "=" * 70)
    if not not_rated:
        print(f"  All {len(team_to_class)} classification teams are included "
              f"in the ratings.")
    else:
        print(f"  TEAMS NOT INCLUDED IN RATINGS: {len(not_rated)} of "
              f"{len(team_to_class)}")
        for t in not_rated:
            g = GAMES_PLAYED.get(t, 0)
            reason = ("excluded via EXCLUDED_TEAMS" if t in EXCLUDED_TEAMS
                      else f"{g} completed game(s), needs {MIN_GAMES_SHOWN} to be shown" if g
                      else "no completed games in scraped data")
            print(f"    - {t} (Class {team_to_class[t]}): {reason}")
    print("=" * 70)
    return not_rated


# ---------------------------------------------------------------------------
# CSV OUTPUT
# ---------------------------------------------------------------------------

def save_csv(all_games):
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Home Team", "Home Score", "Away Team", "Away Score"])
        for date_str, t1, s1, t2, s2 in all_games:
            writer.writerow([date_str, t1, s1, t2, s2])
    print(f"Saved {len(all_games)} games to {CSV_PATH}")


# ---------------------------------------------------------------------------
# SCHEDULE BUILD
# ---------------------------------------------------------------------------
# Per-team schedule file the Sport Detail snippet reads:
#   {"season": ..., "generated": ..., "teams": {schoolName: [game, ...]}}
# Ratings-dependent fields (when both teams are rated):
#   predicted_team_score = league_average + team OFF - opponent DEF
#   predicted_opp_score  = league_average + opponent OFF - team DEF
#   off_delta = team_score - predicted_team_score
#   def_delta = predicted_opp_score - opp_score
#   ovr_delta = off_delta + def_delta

def compute_result(team_score, opp_score):
    """W/L/T, or None for an upcoming/unplayed game."""
    if team_score is None or opp_score is None:
        return None
    if team_score > opp_score:
        return "W"
    if team_score < opp_score:
        return "L"
    return "T"


def make_schedule_entry(game_date, opponent, team_score, opp_score, forfeit,
                        overtime):
    return {
        "date": game_date,
        "opponent": opponent,
        "home_away": None,
        "team_score": team_score,
        "opp_score": opp_score,
        "result": compute_result(team_score, opp_score),
        "predicted_team_score": None,
        "predicted_opp_score": None,
        "off_delta": None,
        "def_delta": None,
        "ovr_delta": None,
        "forfeit": bool(forfeit),
        "overtime": bool(overtime),
    }


def add_predictions(entry, team, opp, ratings, forfeit):
    """Fill the ratings-dependent fields on one schedule entry, in place."""
    if not ratings:
        return
    off, dfn, league_avg = ratings
    if team not in off or opp not in off:
        return
    pred_team = league_avg + off[team] - dfn[opp]
    pred_opp  = league_avg + off[opp] - dfn[team]
    entry["predicted_team_score"] = round(pred_team, 2)
    entry["predicted_opp_score"]  = round(pred_opp, 2)
    if entry["team_score"] is None or entry["opp_score"] is None or forfeit:
        return
    off_delta = entry["team_score"] - pred_team
    def_delta = pred_opp - entry["opp_score"]
    entry["off_delta"] = round(off_delta, 2)
    entry["def_delta"] = round(def_delta, 2)
    entry["ovr_delta"] = round(off_delta + def_delta, 2)


def build_schedule(games, season=SEASON_YEAR, ratings=None):
    teams = {}
    for g in games:
        game_date = g.get("date")
        team1, team2 = g.get("team1"), g.get("team2")
        score1, score2 = g.get("score1"), g.get("score2")
        forfeit = g.get("forfeit", False)
        overtime = g.get("overtime", False)

        if not team1 or not team2:
            print(f"  Skipping malformed game (missing team name): {g}")
            continue

        e1 = make_schedule_entry(game_date, team2, score1, score2, forfeit, overtime)
        e2 = make_schedule_entry(game_date, team1, score2, score1, forfeit, overtime)
        add_predictions(e1, team1, team2, ratings, forfeit)
        add_predictions(e2, team2, team1, ratings, forfeit)
        teams.setdefault(team1, []).append(e1)
        teams.setdefault(team2, []).append(e2)

    for schedule in teams.values():
        schedule.sort(key=lambda entry: entry["date"] or "9999-99-99")

    return {
        "season": season,
        "generated": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
    }


def _write_dict_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_schedule(schedule):
    with open(SCHEDULE_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)

    fields = ["team", "date", "opponent", "home_away", "team_score",
              "opp_score", "result", "predicted_team_score",
              "predicted_opp_score", "off_delta", "def_delta", "ovr_delta",
              "forfeit", "overtime"]
    rows = [{"team": team, **entry}
            for team in sorted(schedule["teams"])
            for entry in schedule["teams"][team]]
    _write_dict_csv(SCHEDULE_CSV_PATH, rows, fields)

    print(f"  Built {SCHEDULE_JSON_PATH} + {SCHEDULE_CSV_PATH}: "
          f"{len(schedule['teams'])} teams, {len(rows)} team-game rows.")


# ---------------------------------------------------------------------------
# RATING ENGINE (v2 -- soft competitiveness weighting + shrinkage regularization)
# ---------------------------------------------------------------------------

def competitiveness_weight(gap, scale=COMPETITIVE_THRESHOLD):
    """
    Smooth weight in (0, 1] based on the current OVR gap between two teams.
    gap=0 -> 1.0, gap=scale -> 0.5, gap=2*scale -> 0.2
    """
    return 1.0 / (1.0 + (gap / scale) ** 2)


def run_iterations(games, teams, off_rating, def_rating, league_avg,
                   iterations, phase_label="Fit"):
    for iteration in range(iterations):
        off_error  = {t: 0.0 for t in teams}
        def_error  = {t: 0.0 for t in teams}
        weight_sum = {t: 0.0 for t in teams}

        for t1, t2, actual_s1, actual_s2 in games:
            gap = abs((off_rating[t1] + def_rating[t1]) -
                      (off_rating[t2] + def_rating[t2]))
            w = competitiveness_weight(gap)

            predicted_s1 = off_rating[t1] - def_rating[t2] + league_avg
            predicted_s2 = off_rating[t2] - def_rating[t1] + league_avg

            error_s1 = actual_s1 - predicted_s1
            error_s2 = actual_s2 - predicted_s2

            error_s1 = max(-MOV_CAP, min(MOV_CAP, error_s1))
            error_s2 = max(-MOV_CAP, min(MOV_CAP, error_s2))

            off_error[t1] += w * error_s1
            off_error[t2] += w * error_s2
            def_error[t1] += -w * error_s2
            def_error[t2] += -w * error_s1

            weight_sum[t1] += w
            weight_sum[t2] += w

        for team in teams:
            denom = weight_sum[team] + REGULARIZATION_K
            off_rating[team] += (off_error[team] / denom) * LEARNING_RATE
            def_rating[team] += (def_error[team] / denom) * LEARNING_RATE

        if (iteration + 1) % 100 == 0:
            print(f"  [{phase_label}] Iteration {iteration + 1}/{iterations} complete")


def load_prior_ratings():
    """Last season's final ratings: {school: (off, def)}. Empty if unavailable."""
    import os
    try:
        if os.path.exists(PRIOR_LOCAL_PATH):
            with open(PRIOR_LOCAL_PATH) as f:
                data = json.load(f)
        else:
            resp = requests.get(PRIOR_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        print(f"  WARNING: couldn't load 2025-26 ratings ({e}) -- no early-season blend this run")
        return {}
    prior = {t["school"]: (t["off_rating"], t["def_rating"]) for t in data.get("teams", [])}
    print(f"  Loaded {len(prior)} teams' 2025-26 final ratings ({data.get('last_updated')})")
    return prior


def _pkey(name):
    """2025-26 files drop the parentheses ("Central Cape Girardeau"), so
    compare names with parentheses removed."""
    return " ".join(name.replace("(", " ").replace(")", " ").split()).lower()


def match_prior(team, prior, prior_by_member, this_season=()):
    """Same name first; then each 2026-27 co-op member (host first) matched
    to any member of a 2025-26 team, for co-ops that changed names or hosts;
    last, the host with its parenthetical dropped ("DeLaSalle Charter (KC)"),
    unless that shorter name is a different school this season ("Delta")."""
    parts = team.split(" with ")
    members = [parts[0].strip()] + ([m.strip() for m in parts[1].split(",")] if len(parts) > 1 else [])
    keys = [_pkey(team)] + [_pkey(m) for m in members]
    short = re.sub(r"\s*\(.*?\)", "", members[0]).strip()
    if short != members[0] and short not in this_season:
        keys.append(_pkey(short))
    for key in keys:
        if key in prior_by_member:
            return prior_by_member[key]
    return None


def blend_with_prior(off_cur, def_cur, games_played, team_to_class, prior):
    """Returns blended (off, def, ovr) for EVERY classified team."""
    prior_by_member = {}
    for name, val in prior.items():          # full names win over co-op members
        prior_by_member[_pkey(name)] = val
    for name, val in prior.items():
        parts = name.split(" with ")
        members = [parts[0]] + (parts[1].split(",") if len(parts) > 1 else [])
        for m in members:
            prior_by_member.setdefault(_pkey(m), val)

    kept = {}
    for team in team_to_class:
        p = match_prior(team, prior, prior_by_member, team_to_class)
        if p is not None:
            kept[team] = (p[0] * PRIOR_KEEP, p[1] * PRIOR_KEEP)

    class_avg = {}
    for c in set(team_to_class.values()):
        vals = [kept[t] for t in kept if team_to_class[t] == c]
        class_avg[c] = ((sum(v[0] for v in vals) / len(vals), sum(v[1] for v in vals) / len(vals))
                        if vals else (0.0, 0.0))

    off_b, def_b = {}, {}
    no_prior = 0
    for team in team_to_class:
        if team in EXCLUDED_TEAMS:
            continue
        g = games_played.get(team, 0)
        w = min(g / BLEND_FULL_GAMES, 1.0) if team in off_cur else 0.0
        if team in kept:
            p_off, p_def = kept[team]
        else:
            p_off, p_def = class_avg[team_to_class[team]]
            no_prior += 1
        c_off, c_def = off_cur.get(team, 0.0), def_cur.get(team, 0.0)
        off_b[team] = w * c_off + (1 - w) * p_off
        def_b[team] = w * c_def + (1 - w) * p_def
        CURRENT_WEIGHT[team] = round(w, 3)
    ovr_b = {t: round(off_b[t] + def_b[t], 2) for t in off_b}
    full = sum(1 for t in off_b if CURRENT_WEIGHT[t] >= 1)
    print(f"  Blend: {len(off_b)} teams; {full} fully on 2026-27 ratings "
          f"({BLEND_FULL_GAMES}+ games); {no_prior} with no 2025-26 rating "
          f"(started at class average)")
    return off_b, def_b, ovr_b


def calculate_ratings(all_games, iterations=ITERATIONS):
    games = [(t1, t2, s1, s2) for _, t1, s1, t2, s2 in all_games]

    teams = list({t for t1, t2, _, _ in games for t in (t1, t2)})
    if not teams:
        return {}, {}, {}, 0

    all_scores = [s for _, _, s1, s2 in games for s in (s1, s2)]
    league_avg = sum(all_scores) / len(all_scores)
    print(f"  League average: {league_avg:.2f} points per game")

    off_rating = {t: 0.0 for t in teams}
    def_rating = {t: 0.0 for t in teams}

    print(f"\n  Running rating fit ({iterations} iterations, soft-weighted "
          f"by competitiveness [scale={COMPETITIVE_THRESHOLD}], "
          f"shrinkage K={REGULARIZATION_K}, MOV cap={MOV_CAP})...")
    print(f"  [TIMING] {len(teams)} teams, {len(games)} games going into the fit.")
    engine_t0 = time.perf_counter()
    run_iterations(games, teams, off_rating, def_rating, league_avg,
                   iterations=iterations, phase_label="Fit")
    print(f"  [TIMING] Rating fit took {time.perf_counter() - engine_t0:.1f}s.")

    ovr_rating = {t: round(off_rating[t] + def_rating[t], 2) for t in teams}
    return off_rating, def_rating, ovr_rating, league_avg


# ---------------------------------------------------------------------------
# JSON OUTPUT
# ---------------------------------------------------------------------------

def build_team_entries(off_rating, def_rating, ovr_rating,
                       team_to_class, team_to_district,
                       class_filter=None):
    all_teams = list(ovr_rating.keys())

    pool = (
        [t for t in all_teams if team_to_class.get(t) == class_filter]
        if class_filter is not None
        else all_teams
    )

    ovr_sorted = sorted(pool, key=lambda t: ovr_rating[t], reverse=True)
    off_sorted = sorted(pool, key=lambda t: off_rating[t], reverse=True)
    def_sorted = sorted(pool, key=lambda t: def_rating[t], reverse=True)

    ovr_rank = {t: i + 1 for i, t in enumerate(ovr_sorted)}
    off_rank = {t: i + 1 for i, t in enumerate(off_sorted)}
    def_rank = {t: i + 1 for i, t in enumerate(def_sorted)}

    return [
        {
            "ovr_rank":       ovr_rank[t],
            "school":         t,
            "classification": team_to_class.get(t),
            "district":       team_to_district.get(t),
            "ovr_rating":     ovr_rating[t],
            "off_rating":     round(off_rating[t], 2),
            "off_rank":       off_rank[t],
            "def_rating":     round(def_rating[t], 2),
            "def_rank":       def_rank[t],
            "games":          GAMES_PLAYED.get(t, 0),
            "current_weight": CURRENT_WEIGHT.get(t),
        }
        for t in ovr_sorted
    ]


def save_overall_json(off_rating, def_rating, ovr_rating, league_avg,
                      team_to_class, team_to_district):
    entries = build_team_entries(off_rating, def_rating, ovr_rating,
                                 team_to_class, team_to_district)
    output = {
        "last_updated":   datetime.now().strftime("%B %d, %Y at %I:%M %p"),
        "league_average": round(league_avg, 2),
        "teams": entries,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Saved {len(entries)} teams to {OUTPUT_PATH}")
    print("Top 5 overall:")
    for e in entries[:5]:
        print(f"  {e['ovr_rank']}. {e['school']} (Class {e['classification']}) "
              f"| OVR: {e['ovr_rating']:+.2f} "
              f"| OFF: {e['off_rating']:+.2f} "
              f"| DEF: {e['def_rating']:+.2f}")


def save_class_jsons(off_rating, def_rating, ovr_rating, league_avg,
                     team_to_class, team_to_district):
    for cls in sorted(set(team_to_class.values())):  # Classes 1-6
        entries = build_team_entries(off_rating, def_rating, ovr_rating,
                                     team_to_class, team_to_district,
                                     class_filter=cls)
        if not entries:
            print(f"  Class {cls}: no teams found — skipping.")
            continue

        path = f"girls_basketball_ratings_{SEASON_YEAR}_class{cls}.json"
        output = {
            "last_updated":   datetime.now().strftime("%B %d, %Y at %I:%M %p"),
            "league_average": round(league_avg, 2),
            "classification": cls,
            "teams": entries,
        }
        with open(path, "w") as f:
            json.dump(output, f, indent=2)

        print(f"  Class {cls}: {len(entries)} teams → {path}")
        print("    Top 3: " + " | ".join(
            f"{e['ovr_rank']}. {e['school']} ({e['ovr_rating']:+.2f})"
            for e in entries[:3]
        ))


# ---------------------------------------------------------------------------
# CSV RANKINGS OUTPUT
# ---------------------------------------------------------------------------

def save_rankings_csv(off_rating, def_rating, ovr_rating,
                      team_to_class, team_to_district,
                      class_filter=None):
    """Rankings CSV for all teams (class_filter=None) or one class."""
    all_teams = list(ovr_rating.keys())

    pool = (
        [t for t in all_teams if team_to_class.get(t) == class_filter]
        if class_filter is not None
        else all_teams
    )

    if not pool:
        label = f"Class {class_filter}" if class_filter else "Overall"
        print(f"  {label}: no teams — skipping CSV.")
        return

    ovr_sorted = sorted(pool, key=lambda t: ovr_rating[t], reverse=True)
    off_sorted = sorted(pool, key=lambda t: off_rating[t], reverse=True)
    def_sorted = sorted(pool, key=lambda t: def_rating[t], reverse=True)

    ovr_rank = {t: i + 1 for i, t in enumerate(ovr_sorted)}
    off_rank = {t: i + 1 for i, t in enumerate(off_sorted)}
    def_rank = {t: i + 1 for i, t in enumerate(def_sorted)}

    rows = [
        {
            "School":      t,
            "OFF Rating":  round(off_rating[t], 2),
            "DEF Rating":  round(def_rating[t], 2),
            "OVR Rating":  round(ovr_rating[t], 2),
            "OFF Rank":    off_rank[t],
            "DEF Rank":    def_rank[t],
            "OVR Rank":    ovr_rank[t],
        }
        for t in ovr_sorted
    ]

    df = pd.DataFrame(rows, columns=[
        "School", "OFF Rating", "DEF Rating", "OVR Rating",
        "OFF Rank", "DEF Rank", "OVR Rank"
    ])

    if class_filter is None:
        path  = f"girls_basketball_rankings_{SEASON_YEAR}_all.csv"
        label = "All teams"
    else:
        path  = f"girls_basketball_rankings_{SEASON_YEAR}_class{class_filter}.csv"
        label = f"Class {class_filter}"

    df.to_csv(path, index=False)
    print(f"  {label}: {len(df)} teams — {path}")


def save_all_rankings_csvs(off_rating, def_rating, ovr_rating,
                           team_to_class, team_to_district):
    """Overall + one CSV per class."""
    save_rankings_csv(off_rating, def_rating, ovr_rating,
                      team_to_class, team_to_district,
                      class_filter=None)
    for cls in sorted(set(team_to_class.values())):
        save_rankings_csv(off_rating, def_rating, ovr_rating,
                          team_to_class, team_to_district,
                          class_filter=cls)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"=== MSHSAA Girls Basketball {SEASON_YEAR}-{(SEASON_YEAR + 1) % 100:02d}: "
          f"games, schedule, ratings ===")

    print("\nLoading classifications...")
    team_to_class, team_to_district = load_classifications()
    known_teams = set(team_to_class.keys())
    print(f"  Loaded {len(team_to_class)} teams from {CLASSIFICATIONS_PATH}")

    print("\nBuilding school ID → classification name lookup...")
    id_to_classname = build_id_to_classname(team_to_class, SCHOOLS_CSV)

    print(f"\nScraping {SEASON_START} to {SEASON_END}...")
    raw_games = scrape_full_season(id_to_classname, known_teams)
    print(f"\nTotal games found (before overrides/dedup, >=1 classified team): "
          f"{len(raw_games)}")
    if not raw_games:
        # Exit before writing anything, so a failed scrape can't overwrite
        # good files with empty ones.
        print("No games found -- exiting without writing any files.")
        exit(1)

    # Overrides run BEFORE dedup: MSHSAA sometimes lists a game twice with
    # team1/team2 swapped, and each raw copy needs its own chance to match.
    print("\nApplying manual name corrections/exclusions...")
    corrections, exclusions = load_manual_overrides()
    raw_games = apply_manual_overrides(raw_games, corrections, exclusions)

    print("\nApplying manual score fills...")
    raw_games = apply_schedule_score_corrections(
        raw_games, load_schedule_score_corrections())

    print("\nDeduplicating games...")
    schedule_games = deduplicate_schedule_games(raw_games)
    schedule_games = drop_next_day_repeats(schedule_games)
    strict_games = strict_games_from_all(schedule_games)
    print(f"Of those, {len(strict_games)} have both teams classified "
          f"({len(schedule_games) - len(strict_games)} have exactly one classified side).")

    print("\nSaving games files...")
    save_games_json(strict_games, OUTPUT_JSON)
    save_games_csv(strict_games, OUTPUT_CSV)
    save_games_json(schedule_games, OUTPUT_JSON_ALL)
    save_games_csv_all(schedule_games, OUTPUT_CSV_ALL)

    print("\nChecking for missing scores...")
    save_missing_scores_report(schedule_games)

    print("\nChecking for classified teams with no games at all...")
    report_unscheduled_teams(schedule_games, team_to_class, team_to_district)

    print("\nSelecting completed games for ratings...")
    all_games = rated_games_from_all(schedule_games)

    if MANUAL_GAMES:
        print(f"\nAdding {len(MANUAL_GAMES)} manual game(s)...")
        all_games.extend(MANUAL_GAMES)

    print("\nApplying score corrections...")
    all_games = apply_score_corrections(all_games)

    print("\nApplying game exclusions...")
    all_games = apply_exclusions(all_games)

    print("\nDeduplicating games...")
    all_games = deduplicate_games(all_games)

    print("\nChecking for missing teams...")
    report_missing_teams(all_games, team_to_class)

    print("\nApplying team exclusions...")
    all_games = apply_team_exclusions(all_games, team_to_class)

    print("Saving scoreboard CSV...")
    save_csv(all_games)

    print(f"\nRunning ratings engine ({ITERATIONS} iterations)...")
    if all_games:
        off_cur, def_cur, _, league_cur = calculate_ratings(all_games)
    else:
        print("  No completed games yet -- using 2025-26 ratings only.")
        off_cur, def_cur, league_cur = {}, {}, LEAGUE_AVG_DEFAULT

    for _, t1, _, t2, _ in all_games:
        GAMES_PLAYED[t1] = GAMES_PLAYED.get(t1, 0) + 1
        GAMES_PLAYED[t2] = GAMES_PLAYED.get(t2, 0) + 1

    lw = min(len(all_games) / LEAGUE_AVG_FULL_GAMES, 1.0)
    league_avg = lw * league_cur + (1 - lw) * LEAGUE_AVG_DEFAULT
    print(f"  League average for projections: {league_avg:.2f} "
          f"({len(all_games)} games; {lw:.0%} this season's actual average)")

    print("\nBlending with 2025-26 ratings...")
    off_all, def_all, ovr_all = blend_with_prior(
        off_cur, def_cur, GAMES_PLAYED, team_to_class, load_prior_ratings())

    # Ratings/rankings files only list teams with MIN_GAMES_SHOWN+ games.
    shown = [t for t in off_all if GAMES_PLAYED.get(t, 0) >= MIN_GAMES_SHOWN]
    off_rating = {t: off_all[t] for t in shown}
    def_rating = {t: def_all[t] for t in shown}
    ovr_rating = {t: ovr_all[t] for t in shown}
    print(f"  {len(shown)} team(s) have {MIN_GAMES_SHOWN}+ games and appear in the ratings files.")

    print("\nBuilding schedule files (with predictions)...")
    # Every classified team gets projections, shown in the rankings or not.
    save_schedule(build_schedule(schedule_games,
                                 ratings=(off_all, def_all, league_avg)))

    print("\nSaving overall ratings JSON...")
    save_overall_json(off_rating, def_rating, ovr_rating, league_avg,
                      team_to_class, team_to_district)

    print("\nSaving per-class ratings JSONs...")
    save_class_jsons(off_rating, def_rating, ovr_rating, league_avg,
                     team_to_class, team_to_district)

    print("\nSaving rankings CSVs...")
    save_all_rankings_csvs(off_rating, def_rating, ovr_rating,
                           team_to_class, team_to_district)

    report_teams_not_rated(ovr_rating, team_to_class)

    print("\n=== Done ===")
