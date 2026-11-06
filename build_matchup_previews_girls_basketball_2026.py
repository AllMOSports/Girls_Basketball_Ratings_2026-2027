"""
Matchup Previews - nightly data builder for the non-football sports (v2)
=======================================================================
 
ONE FILE FOR EVERY SPORT: the code below is identical in each sport's repo;
only the SPORT line right under the imports differs ("boys_soccer",
"girls_volleyball", "fall_softball", "boys_basketball", "girls_basketball").
Each sport's numbers
live in SPORTS. (v3 adds boys_basketball and girls_basketball, a per-sport postseason window for the
head-to-head labels, and runs without the Size-Adjusted file if it's missing --
safe to copy back into the fall repos.)
 
Writes matchup_previews_<sport>_2026.json: everything the Matchup Preview
snippet needs (same SPORT value there) for every upcoming, unplayed game in
<sport>_games_2026_all.json, so the page does one fetch and only renders.
Same file shape as the football builder (build_matchup_previews_2026.py).
 
  teams{}   one entry per team in an upcoming game: ratings + ranks, class
            rank, record (W-L-T), scored / allowed per game, margin, schedule
            strength (average opponent OVR), Size-Adjusted rank, last-5 form vs
            projection, program history since 2012, slug, logo.
  games[]   one entry per upcoming game: projected score + win probability,
            district-game flag, common opponents, head-to-head since 2012, and
            storyline tags (written but hidden on the page for now).
 
PROJECTION - same approach as the football Matchup Predictor, each sport's scale:
  raw score     = own off_rating - opponent def_rating + league_average
                  (league_average from the sport's ratings file - the same
                  formula its schedule data already uses for predicted scores)
  win prob      = 1 / (1 + e^(-1.702 * raw margin / margin_prob_scale))
  shown score   = score_mode "round": negative raw rolled over onto the
                  opponent, both capped at max_score, rounded to whole
                  goals/runs; no ties - the side behind on the raw margin drops
                  one (or, at 0-0, the side ahead gets one).
                  score_mode "sets5" (volleyball, best of 5): the favorite wins
                  3 sets; the loser's sets come from the projected set margin -
                  under 0.4 -> 3-2, under 1.0 -> 3-1, otherwise 3-0. In 2026
                  best-of-5 matches, 3-1 is the most common result when the
                  ratings are within a set and 3-0 beyond that; 3-2 is shown
                  only for near toss-ups so a close match reads as close.
margin_prob_scale per sport: the best in-season logistic fit on 2026 games
between rated teams (soccer 1.8 goals, volleyball 1.1 sets, softball 4.2 runs),
nudged a little wider because those ratings were fit to the same games.
Boys basketball starts at 12 points and girls at 11 as placeholders -- refit it on 2026-27
games once a few weeks are in, the same way the fall values were set.
Win probability ignores ties/draws.
 
UNRATED TEAMS (out-of-state opponents, and Missouri schools that aren't in the
sport's classifications): strength = average of (opponent OVR + margin) over
their games vs rated teams, margins capped at unrated_margin_cap, lightly
shrunk toward the statewide average. No score is projected for those games,
and the win-probability curve is widened by UNRATED_SCALE_FACTOR.
 
Inputs (this repo): <sport>_ratings_2026.json, <sport>_games_2026_all.json,
  per_capita_<sport>_2026.json.
Inputs (All_MO_Sports-Data): output/schools.json, school_aliases.json,
  <sport>_history.json, output/<sport>_schedule_{2012..2025}.json,
  data/<sport>_district_games_2012-2026.csv (to label postseason meetings).
  Read from DATA_DIR if set (a local clone), otherwise fetched from GitHub raw.
 
Usage:
    python3 build_matchup_previews_<sport>_2026.py
    DATA_DIR=../All_MO_Sports-Data python3 build_matchup_previews_<sport>_2026.py
"""
 
import csv
import datetime
import io
import json
import math
import os
import re
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo
 
SPORT = "girls_basketball"    # <- the only line that differs between the sport repos
 
SPORTS = {
    "boys_soccer": {
        "label": "Boys Soccer", "unit": "goal", "units": "goals",
        "score_mode": "round", "margin_prob_scale": 2.0,
        "max_score": 10,           # raw cap after the rollover (MSHSAA's 8-goal rule ends most blowouts)
        "unrated_margin_cap": 8,   # the 8-goal mercy margin
    },
    "girls_volleyball": {
        "label": "Girls Volleyball", "unit": "set", "units": "sets",
        "score_mode": "sets5", "margin_prob_scale": 1.2,
        "max_score": 3,
        "unrated_margin_cap": 3,   # sets
    },
    "fall_softball": {
        "label": "Fall Softball", "unit": "run", "units": "runs",
        "score_mode": "round", "margin_prob_scale": 4.5,
        "max_score": 15,           # 15-run rule after 3 innings
        "unrated_margin_cap": 15,
    },
    "boys_basketball": {
        "label": "Boys Basketball", "unit": "point", "units": "points",
        "score_mode": "round", "margin_prob_scale": 12.0,   # placeholder -- refit on 2026-27 games
        "max_score": 120,          # no mercy rule; just a sanity cap on the shown score
        "unrated_margin_cap": 30,  # MSHSAA running clock starts at a 30-point margin
        "postseason_mmdd": ("02-20", "03-31"),   # district + state series window
    },
    "girls_basketball": {
        "label": "Girls Basketball", "unit": "point", "units": "points",
        "score_mode": "round", "margin_prob_scale": 11.0,   # placeholder -- refit on 2026-27 games
        "max_score": 110,          # no mercy rule; just a sanity cap on the shown score
        "unrated_margin_cap": 30,  # MSHSAA running clock starts at a 30-point margin
        "postseason_mmdd": ("02-20", "03-31"),   # district + state series window
    },
}
CFG = SPORTS[SPORT]
 
SPORT_KEY = SPORT
SPORT_LABEL = CFG["label"]
SEASON = 2026
OUTPUT_PATH = f"matchup_previews_{SPORT}_2026.json"
RATINGS_PATH = f"{SPORT}_ratings_2026.json"
GAMES_PATH = f"{SPORT}_games_2026_all.json"
PER_CAPITA_PATH = f"per_capita_{SPORT}_2026.json"
 
DATA_RAW = "https://raw.githubusercontent.com/AllMOSports/All_MO_Sports-Data/main/"
LOGO_URL = DATA_RAW + "logos_main2/{slug}.png"
HISTORY_FILE = f"{SPORT}_history.json"
SCHEDULE_FILE = "output/" + SPORT + "_schedule_{year}.json"
DISTRICT_GAMES_FILE = f"data/{SPORT}_district_games_2012-2026.csv"
HISTORY_YEARS = range(2012, 2026)
 
MARGIN_PROB_SCALE = CFG["margin_prob_scale"]
MAX_SCORE = CFG["max_score"]
FORM_GAMES = 5
SHRINK_GAMES = 0.5          # pseudo-games shrinking unrated teams' estimated strength toward 0 (light:
                            # most unrated Missouri schools sit near the bottom, so heavy shrink overrates them)
UNRATED_MARGIN_CAP = CFG["unrated_margin_cap"]
UNRATED_SCALE_FACTOR = 1.5  # wider win-probability curve when one side's strength is only estimated
CENTRAL = ZoneInfo("America/Chicago")
 
 
# --------------------------------------------------------------------------
# Loading helpers (same conventions as the football builder)
# --------------------------------------------------------------------------
 
def load_json(path):
    with open(path) as f:
        return json.load(f)
 
 
def fetch_data_text(rel):
    base = os.environ.get("DATA_DIR")
    if base:
        with open(os.path.join(base, rel)) as f:
            return f.read()
    with urllib.request.urlopen(DATA_RAW + rel, timeout=60) as r:
        return r.read().decode("utf-8")
 
 
def load_data_file(rel):
    return json.loads(fetch_data_text(rel))
 
 
def make_slug(name):
    s = name.lower().strip().replace("'", "").replace(".", "").replace("(", "").replace(")", "")
    return "-".join(s.split())
 
 
class SlugResolver:
    def __init__(self, schools, aliases):
        self.schools = schools
        self.by_mshsaa, self.by_name = defaultdict(list), defaultdict(list)
        for slug, s in schools.items():
            self.by_mshsaa[s.get("mshsaa_name", "")].append(slug)
            self.by_name[s.get("name", "")].append(slug)
        self.aliases = aliases
 
    def __call__(self, name):
        for table in (self.by_mshsaa, self.by_name):
            if len(table.get(name, [])) == 1:
                return table[name][0]
        if name in self.aliases:
            return self.aliases[name]
        for cand in (name, name.split(" with ")[0]):
            if make_slug(cand) in self.schools:
                return make_slug(cand)
        return None
 
 
 
def central_time(stamp):
    """The ratings scripts stamp last_updated with the GitHub runner's clock, which is
    UTC ("October 29, 2026 at 09:44 AM"). Show it in Central time instead, with CDT/CST
    picked automatically: "October 29, 2026 at 4:44 AM CDT". Anything that isn't in that
    format is passed through unchanged."""
    try:
        dt = datetime.datetime.strptime(stamp, "%B %d, %Y at %I:%M %p").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return stamp
    c = dt.astimezone(ZoneInfo("America/Chicago"))
    return f"{c.strftime('%B')} {c.day}, {c.year} at {c.strftime('%I:%M %p').lstrip('0')} {c.strftime('%Z')}"
 
 
def r1(x):
    return None if x is None else round(float(x), 1)
 
 
def r2(x):
    return None if x is None else round(float(x), 2)
 
 
def js_round(x):
    """JavaScript Math.round (halves round up), so page and data agree."""
    return int(math.floor(x + 0.5))
 
 
def week_of(date_str, season_start_monday):
    d = datetime.date.fromisoformat(date_str)
    return (d - season_start_monday).days // 7 + 1
 
 
# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------
 
def win_prob_from_margin(margin, scale=MARGIN_PROB_SCALE):
    return 1.0 / (1.0 + math.exp(-1.702 * margin / scale))
 
 
def looks_out_of_state(name):
    """Scraped out-of-state names carry a state suffix: 'Belleville West(Belleville, IL)'."""
    return bool(re.search(r"(,\s*[A-Z]{2}\.?\)|\([A-Z]{2}\.?\))\s*$", name))
 
 
def projection(off_a, def_a, off_b, def_b, league_average):
    """Returns (formula_a, formula_b, score_a, score_b, win_prob_a)."""
    raw_a = off_a - def_b + league_average
    raw_b = off_b - def_a + league_average
    formula_a, formula_b = raw_a, raw_b
    margin = raw_a - raw_b
    p = win_prob_from_margin(margin)
    if CFG["score_mode"] == "sets5":
        lose = 2 if abs(margin) < 0.4 else (1 if abs(margin) < 1.0 else 0)
        score_a, score_b = (3, lose) if margin >= 0 else (lose, 3)
        return formula_a, formula_b, score_a, score_b, p
    if raw_a < 0:
        raw_b += -raw_a
        raw_a = 0
    if raw_b < 0:
        raw_a += -raw_b
        raw_b = 0
    raw_a, raw_b = min(raw_a, MAX_SCORE), min(raw_b, MAX_SCORE)
    score_a, score_b = js_round(max(0, raw_a)), js_round(max(0, raw_b))
    if score_a == score_b:
        if margin >= 0:
            if score_b > 0:
                score_b -= 1
            else:
                score_a += 1
        else:
            if score_a > 0:
                score_a -= 1
            else:
                score_b += 1
    return formula_a, formula_b, score_a, score_b, p
 
 
# --------------------------------------------------------------------------
# Per-team / per-game blocks
# --------------------------------------------------------------------------
 
def team_results(games, start_monday):
    out = defaultdict(list)
    for g in games:
        if g["score1"] is None or g["score2"] is None:
            continue
        for me, opp, ms, os_ in ((g["team1"], g["team2"], g["score1"], g["score2"]),
                                 (g["team2"], g["team1"], g["score2"], g["score1"])):
            out[me].append({"date": g["date"], "week": week_of(g["date"], start_monday), "opp": opp,
                            "pf": ms, "pa": os_, "forfeit": g.get("forfeit", False),
                            "ot": g.get("overtime", False)})
    for v in out.values():
        v.sort(key=lambda x: x["date"])
    return out
 
 
def estimate_unrated(games, ovr):
    sums, counts = defaultdict(float), defaultdict(int)
    for g in games:
        if g["score1"] is None or g["score2"] is None or g.get("forfeit"):
            continue
        for me, opp, ms, os_ in ((g["team1"], g["team2"], g["score1"], g["score2"]),
                                 (g["team2"], g["team1"], g["score2"], g["score1"])):
            if me not in ovr and opp in ovr:
                m = max(-UNRATED_MARGIN_CAP, min(UNRATED_MARGIN_CAP, ms - os_))
                sums[me] += ovr[opp] + m
                counts[me] += 1
    return {t: sums[t] / (counts[t] + SHRINK_GAMES) for t in sums}
 
 
def in_postseason_window(date_str):
    """Fallback postseason test when the district-games file has no entry: is
    this date inside the sport's postseason window? Fall sports default to
    November onward (the old rule); basketball sets its own window."""
    lo, hi = CFG.get("postseason_mmdd", ("11-01", "12-31"))
    mmdd = (date_str or "")[5:10]
    return bool(mmdd) and lo <= mmdd <= hi


def load_postseason_pairs():
    """{(year, frozenset(team names))} for district tournament games."""
    pairs = set()
    try:
        text = fetch_data_text(DISTRICT_GAMES_FILE)
    except Exception as e:
        print(f"  WARNING: {DISTRICT_GAMES_FILE} unavailable ({e}); postseason labels use the fallback rule")
        return pairs
    for row in csv.DictReader(io.StringIO(text)):
        try:
            pairs.add((int(row["year"]), frozenset((row["team1"], row["team2"]))))
        except (KeyError, ValueError):
            continue
    return pairs
 
 
def load_h2h_history(names, postseason_pairs):
    meetings = defaultdict(list)
    for y in HISTORY_YEARS:
        try:
            doc = load_data_file(SCHEDULE_FILE.format(year=y))
        except Exception as e:
            print(f"  WARNING: {SCHEDULE_FILE.format(year=y)} unavailable ({e})")
            continue
        seen = set()
        for me, games in doc.get("teams", doc).items():
            if me not in names:
                continue
            for g in games if isinstance(games, list) else games.get("games", []):
                opp = g.get("opponent")
                if opp not in names or g.get("team_score") is None or g.get("opp_score") is None:
                    continue
                k = (frozenset((me, opp)), g.get("date"))
                if k in seen:
                    continue
                seen.add(k)
                meetings[frozenset((me, opp))].append({
                    "season": y, "date": g.get("date"), "team": me, "opp": opp,
                    "team_score": g["team_score"], "opp_score": g["opp_score"]})
    for pair, v in meetings.items():
        v.sort(key=lambda x: x["date"] or "")
        by_year = defaultdict(list)
        for m in v:
            by_year[m["season"]].append(m)
        for y, ms in by_year.items():
            in_bracket = (y, pair) in postseason_pairs
            for i, m in enumerate(ms):
                # bracket file says these two met in districts that year: the last meeting is the district game
                m["postseason"] = (in_bracket and i == len(ms) - 1) or (not in_bracket and i > 0
                                                                       and in_postseason_window(m["date"]))
    return meetings
 
 
def h2h_block(a, b, history, results_2026):
    rows = []
    for m in history.get(frozenset((a, b)), []):
        a_score = m["team_score"] if m["team"] == a else m["opp_score"]
        b_score = m["opp_score"] if m["team"] == a else m["team_score"]
        rows.append({"season": m["season"], "date": m["date"], "a": a_score, "b": b_score,
                     "postseason": m["postseason"]})
    for r in results_2026.get(a, []):
        if r["opp"] == b:
            rows.append({"season": SEASON, "date": r["date"], "a": r["pf"], "b": r["pa"], "postseason": False})
    rows.sort(key=lambda x: x["date"] or "")
    if not rows:
        return None
    a_w = sum(1 for x in rows if x["a"] > x["b"])
    b_w = sum(1 for x in rows if x["b"] > x["a"])
    ties = len(rows) - a_w - b_w
    streak_team, streak = None, 0
    for x in reversed(rows):
        w = a if x["a"] > x["b"] else (b if x["b"] > x["a"] else None)
        if w is None:
            break
        if streak_team is None:
            streak_team = w
        if w != streak_team:
            break
        streak += 1
    closest = min(rows, key=lambda x: (abs(x["a"] - x["b"]), x["date"] or ""))
    return {"since": rows[0]["season"], "meetings": len(rows), "wins": [a_w, b_w], "ties": ties,
            "avg_margin_a": round(sum(x["a"] - x["b"] for x in rows) / len(rows), 1),
            "streak": {"team": streak_team, "games": streak} if streak_team else None,
            "closest": closest, "games": list(reversed(rows))}
 
 
def program_block(slug, history_doc, current_rank):
    h = history_doc.get("teams", {}).get(slug)
    if not h:
        return None
    seasons = [{"season": int(y), "w": v.get("wins"), "l": v.get("losses"), "rank": v.get("ovr_rank")}
               for y, v in sorted(h.get("history", {}).items())]
    ranked = [s for s in seasons if s["rank"]]
    best = min(ranked, key=lambda s: s["rank"]) if ranked else None
    return {
        "seasons_on_file": len(seasons),
        "first_season": seasons[0]["season"] if seasons else None,
        "winning_seasons": sum(1 for s in seasons if (s["w"] or 0) > (s["l"] or 0)),
        "best_rank": best["rank"] if best else None,
        "best_rank_season": best["season"] if best else None,
        "best_since_2010": bool(best is None or (current_rank is not None and current_rank < best["rank"])),
    }
 
 
def common_block(a, b, results):
    ra = defaultdict(list)
    for x in results.get(a, []):
        ra[x["opp"]].append(x)
    seen, out = set(), []
    for y in results.get(b, []):
        if y["opp"] in ra and y["opp"] not in (a, b) and y["opp"] not in seen:
            x = ra[y["opp"]][0]
            ma, mb = x["pf"] - x["pa"], y["pf"] - y["pa"]
            seen.add(y["opp"])
            out.append({"opp": y["opp"], "a": [x["pf"], x["pa"]], "b": [y["pf"], y["pa"]],
                        "edge": "a" if ma > mb else ("b" if mb > ma else None)})
    return out
 
 
def build_tags(A, B, proj, h2h):
    """Same storyline rules as football (minus seed stakes). Hidden on the page for now."""
    tags = []
    a, b = A["name"], B["name"]
    dog = B if proj["win_prob"][0] >= 0.5 else A
    dog_p = min(proj["win_prob"])
    if h2h and h2h["streak"] and h2h["streak"]["games"] >= 4:
        st = h2h["streak"]
        other = b if st["team"] == a else a
        wins = h2h["wins"][0] if st["team"] == a else h2h["wins"][1]
        text = (f"{st['team']} is {wins}-0 against {other} since {h2h['since']}." if st["games"] == h2h["meetings"]
                else f"{st['team']} has won {st['games']} straight meetings with {other}.")
        tags.append({"kind": "streak", "label": "The streak", "text": text})
    for t in (A, B):
        if t["losses"] == 0 and t["wins"] >= 3:
            tags.append({"kind": "unbeaten", "label": "Unbeaten", "text": f"{t['name']} puts a {t['record']} record on the line."})
    if A.get("ovr_rank") and B.get("ovr_rank") and A["ovr_rank"] <= 10 and B["ovr_rank"] <= 10:
        tags.append({"kind": "clash", "label": "Top-10 clash", "text": f"#{A['ovr_rank']} vs #{B['ovr_rank']} statewide."})
    if 0.2 <= dog_p <= 0.45 and dog.get("form_beat", 0) >= 4:
        tags.append({"kind": "upset", "label": "Upset watch",
                     "text": f"{dog['name']} has beaten its projection in {dog['form_beat']} of its last {len(dog['form'])} games."})
    if dog_p >= 0.45:
        tags.append({"kind": "tossup", "label": "Toss-up", "text": f"The ratings have this within a {CFG['unit']}."})
    return tags
 
 
# --------------------------------------------------------------------------
 
def main():
    ratings_doc = load_json(RATINGS_PATH)
    L = ratings_doc["league_average"]
    ratings = {t["school"]: t for t in ratings_doc["teams"]}
    ovr = {n: t["ovr_rating"] for n, t in ratings.items()}
    games = load_json(GAMES_PATH)
    # Size-Adjusted ranks are optional: early in a season (or if that step
    # failed tonight) the file may not exist yet.
    per_capita = ({t["school"]: t for t in load_json(PER_CAPITA_PATH)["teams"]}
                  if os.path.exists(PER_CAPITA_PATH) else {})
    if not per_capita:
        print(f"  {PER_CAPITA_PATH} not found -- Size-Adjusted ranks left out this run")
 
    first = min(datetime.date.fromisoformat(g["date"]) for g in games)
    start_monday = first - datetime.timedelta(days=first.weekday())
 
    class_rank, class_size, by_class = {}, {}, defaultdict(list)
    for t in ratings_doc["teams"]:
        by_class[t["classification"]].append(t)
    for cls, ts in by_class.items():
        ts.sort(key=lambda t: -t["ovr_rating"])
        class_size[cls] = len(ts)
        for i, t in enumerate(ts, 1):
            class_rank[t["school"]] = i
 
    strength = dict(ovr)
    strength.update({k: v for k, v in estimate_unrated(games, ovr).items() if k not in ovr})
    results = team_results(games, start_monday)
 
    # schedule strength = average opponent OVR (rated opponents only), ranked statewide
    sos = {}
    for n in ratings:
        opps = [ovr[x["opp"]] for x in results.get(n, []) if x["opp"] in ovr]
        if opps:
            sos[n] = sum(opps) / len(opps)
    sos_rank = {n: i for i, (n, _) in enumerate(sorted(sos.items(), key=lambda kv: -kv[1]), 1)}
 
    today = datetime.datetime.now(CENTRAL).date().isoformat()
    placeholder = {"tbd", "tba", "bye"}      # unset tournament slots
    upcoming = [g for g in games if (g["score1"] is None or g["score2"] is None) and g["date"] >= today
                and g["team1"].strip().lower() not in placeholder and g["team2"].strip().lower() not in placeholder]
    print(f"Upcoming games: {len(upcoming)} (from {today}; "
          f"{sum(1 for g in games if g['score1'] is None and g['date'] < today)} past-dated games without scores skipped)")
 
    print("Loading All_MO_Sports-Data files...")
    resolver = SlugResolver(load_data_file("output/schools.json")["schools"], load_data_file("school_aliases.json"))
    history_doc = load_data_file(HISTORY_FILE)
    names_in_play = {g["team1"] for g in upcoming} | {g["team2"] for g in upcoming}
    h2h_hist = load_h2h_history(set(ratings) | names_in_play, load_postseason_pairs())
 
    def team_block(name):
        r = ratings.get(name)
        allres = results.get(name, [])
        res = [x for x in allres if not x["forfeit"]]
        slug = resolver(name)
        w = sum(1 for x in allres if x["pf"] > x["pa"])
        l = sum(1 for x in allres if x["pf"] < x["pa"])
        t_ = len(allres) - w - l
        t = {"name": name, "slug": slug, "logo": LOGO_URL.format(slug=slug) if slug else None,
             "rated": r is not None, "out_of_state": r is None and looks_out_of_state(name),
             # out-of-state teams: only their games vs Missouri teams are on file, so no record shown
             "record": None if (r is None and looks_out_of_state(name)) else f"{w}-{l}" + (f"-{t_}" if t_ else ""),
             "wins": w, "losses": l, "ties": t_,
             "ppg": r1(sum(x["pf"] for x in res) / len(res)) if res else None,
             "papg": r1(sum(x["pa"] for x in res) / len(res)) if res else None,
             "margin": r1(sum(x["pf"] - x["pa"] for x in res) / len(res)) if res else None}
        if r:
            t.update({"class": r["classification"], "district": r["district"],
                      "ovr": r2(r["ovr_rating"]), "ovr_rank": r["ovr_rank"],
                      "off": r2(r["off_rating"]), "off_rank": r["off_rank"],
                      "def": r2(r["def_rating"]), "def_rank": r["def_rank"],
                      "class_rank": class_rank.get(name), "class_size": class_size.get(r["classification"]),
                      "sos": r2(sos.get(name)), "sos_rank": sos_rank.get(name)})
        pc = per_capita.get(name)
        if pc:
            t["size_adj_rank"], t["enrollment"] = pc.get("per_capita_rank"), pc.get("enrollment")
        form = []
        for x in allres[-FORM_GAMES:][::-1]:
            delta = None
            if name in strength and x["opp"] in strength and not x["forfeit"]:
                delta = r1((x["pf"] - x["pa"]) - (strength[name] - strength[x["opp"]]))
            orr = ratings.get(x["opp"])
            form.append({"week": x["week"], "date": x["date"], "opp": x["opp"],
                         "opp_rank": orr["ovr_rank"] if orr else None, "pf": x["pf"], "pa": x["pa"],
                         "ot": x["ot"], "forfeit": x["forfeit"], "delta": delta})
        t["form"] = form
        t["form_beat"] = sum(1 for f in form if f["delta"] is not None and f["delta"] > 0)
        t["program"] = program_block(slug, history_doc, t.get("ovr_rank")) if slug else None
        return t
 
    teams_out, games_out = {}, []
    for g in upcoming:
        a, b = g["team1"], g["team2"]
        for n in (a, b):
            if n not in teams_out:
                teams_out[n] = team_block(n)
        A, B = teams_out[a], teams_out[b]
        proj = None
        if A["rated"] and B["rated"]:
            ra, rb = ratings[a], ratings[b]
            pa, pb, sa, sb, p = projection(ra["off_rating"], ra["def_rating"], rb["off_rating"], rb["def_rating"], L)
            proj = {"raw": [r2(pa), r2(pb)], "score": [sa, sb], "win_prob": [round(p, 4), round(1 - p, 4)]}
        elif a in strength and b in strength:
            p = win_prob_from_margin(strength[a] - strength[b], MARGIN_PROB_SCALE * UNRATED_SCALE_FACTOR)
            proj = {"raw": None, "score": None, "win_prob": [round(p, 4), round(1 - p, 4)]}
        district_game = bool(A.get("class") and A.get("class") == B.get("class") and A.get("district") == B.get("district"))
        h2h = h2h_block(a, b, h2h_hist, results)
        entry = {"id": f"{g['date']}-{A['slug'] or make_slug(a)}-vs-{B['slug'] or make_slug(b)}",
                 "date": g["date"], "week": week_of(g["date"], start_monday), "teams": [a, b],
                 "class": A.get("class") if district_game else None,
                 "district": A.get("district") if district_game else None,
                 "district_game": district_game, "projection": proj, "stakes": None,
                 "common": common_block(a, b, results), "h2h": h2h}
        entry["tags"] = build_tags(A, B, proj, h2h) if proj else []
        games_out.append(entry)
 
    games_out.sort(key=lambda x: (x["date"], sum(teams_out[n].get("ovr_rank") or 999 for n in x["teams"])))
    weeks = sorted({x["week"] for x in games_out})
    week_labels = {}
    for w in weeks:
        mon = start_monday + datetime.timedelta(days=7 * (w - 1))
        sun = mon + datetime.timedelta(days=6)
        week_labels[str(w)] = f"{mon.strftime('%b')} {mon.day}–" + (f"{sun.day}" if sun.month == mon.month else f"{sun.strftime('%b')} {sun.day}")
 
    out = {"generated": datetime.datetime.now(CENTRAL).isoformat(timespec="seconds"),
           "sport": SPORT_KEY, "sport_label": SPORT_LABEL, "season": SEASON,
           "ratings_last_updated": central_time(ratings_doc.get("last_updated")), "league_average": L,
           "margin_prob_scale": MARGIN_PROB_SCALE, "history_since": HISTORY_YEARS.start,
           "score_mode": CFG["score_mode"],
           "classes": sorted(by_class), "weeks": weeks, "week_labels": week_labels,
           "teams": teams_out, "games": games_out}
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {OUTPUT_PATH}: {len(games_out)} games, {len(teams_out)} teams, "
          f"{os.path.getsize(OUTPUT_PATH) / 1024:.0f} KB")
 
 
if __name__ == "__main__":
    main()
