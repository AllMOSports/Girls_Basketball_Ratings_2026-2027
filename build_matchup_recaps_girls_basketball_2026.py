"""
Matchup Recaps - data builder, basketball (v1)
==============================================
Same idea as the football builder (build_matchup_recaps_2026.py), for boys and
girls basketball. The two basketball repos carry this same file; only the
SPORT line below differs.

Writes one small file per game date plus a manifest:
  matchup_recaps_<sport>_2026/<YYYY-MM-DD>.json   {"date", "recaps": {id: recap}}
  matchup_recaps_<sport>_2026/manifest.json       {"dates": {date: count}, ...}
The Matchup Recap page loads the one date file it needs; the Sport Detail
snippet loads only the manifest (a game gets a "Matchup Recap" button when its
date is listed). Files are rewritten only when their content changes, so a
run with nothing new makes no commit.

A RECAP IS WRITTEN ONCE AND THEN FROZEN. It describes the game the way it
looked that night: the projection fans saw before tip-off and both teams'
ranks and records right after it. Later rating changes never rewrite it. The
Matchup Recap page adds a separate, live "Looking back" line from today's
ratings.

WHERE EACH NUMBER COMES FROM (this repo's own git history, so every game of
the season can be backfilled and no snapshot step is needed):
  pre-game projection  <sport>_ratings_2026.json as committed BEFORE tip-off
                       (the last commit before 5:00 PM Central on game day),
                       run through projection() imported from this repo's
                       previews builder with its league_average_for() - the
                       exact numbers the Matchup Preview showed that day.
                       None when either team had no rating yet.
  ranks after the game the FIRST ratings commit after game day. The recap
                       waits for it unless WAIT_DAYS have passed.
  records, streaks,    <sport>_games_2026_all.json, games up to and including
  season highs         this one.
  postseason round     All_MO_Sports-Data data/<sport>_district_games_2012-2026.csv
                       (district rounds); a game inside the postseason window
                       after a team's last district game is "State tournament".

FREEZING: existing recaps are kept as they are. Exceptions: --rebuild,
--rebuild-game <id>, and an automatic rebuild when a final score is corrected.

HEADLINE (first rule that fits):
  0. state championship             "[W] wins the Class N state championship."
  1. district final                 "[W] wins the Class N District D title."
  2. upset (winner < 35% pre-game)  "Upset: [W] knocks off [L] as a P% shot."
  3. margin within 2 of projected   "Right on the number: ..."
  4. favorite won by 12+ less       "[W] survives: a S win that was closer than projected."
  5. favorite won by 12+ more       "[W] rolls past [L], N points better than projected."
  6. default                        "[W] beats [L], S."
STORY: up to four template sentences. No AI.

Needs full git history (actions/checkout fetch-depth: 0).

Usage:
    python3 build_matchup_recaps_<sport>_2026.py
    python3 build_matchup_recaps_<sport>_2026.py --rebuild
    python3 build_matchup_recaps_<sport>_2026.py --rebuild-game 2026-12-04-jackson-vs-notre-dame
"""

import argparse
import csv
import datetime
import importlib
import io
import json
import os
import subprocess
from collections import defaultdict
from zoneinfo import ZoneInfo

SPORT = "girls_basketball"    # <- the only line that differs between the sport repos

mp = importlib.import_module(f"build_matchup_previews_{SPORT}_2026")

OUTPUT_DIR = f"matchup_recaps_{SPORT}_2026"
MANIFEST_PATH = os.path.join(OUTPUT_DIR, "manifest.json")
RATINGS_PATH = mp.RATINGS_PATH            # <sport>_ratings_2026.json
GAMES_PATH = mp.GAMES_PATH                # <sport>_games_2026_all.json
SEASON = mp.SEASON
CENTRAL = ZoneInfo("America/Chicago")
TIPOFF_HOUR = 17           # ratings committed before 5:00 PM Central on game day count as pre-game
WAIT_DAYS = 4              # give up waiting for post-game ratings after this many days
UPSET_PROB = 0.35          # winner's pre-game win probability below this = upset
ON_NUMBER_POINTS = 2       # actual margin within this of the projected margin
SWING_POINTS = 12          # "closer than projected" / "rolls past" threshold
PERF_POINTS = 8            # offense/defense sentence threshold (points vs projection)
MIN_STREAK = 3
MIN_PRIOR_GAMES = 3        # season-high notes need at least this many earlier games


# --------------------------------------------------------------------------
# git history
# --------------------------------------------------------------------------

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


class History:
    """Every committed version of one file, oldest first, loaded lazily."""

    def __init__(self, path):
        self.path = path
        self.versions = []
        for line in git("log", "--format=%H\t%cI", "--", path).splitlines():
            sha, stamp = line.split("\t")
            self.versions.append((datetime.datetime.fromisoformat(stamp), sha))
        self.versions.sort()
        self._cache = {}
        if not self.versions:
            raise SystemExit(f"No git history for {path} - run with full history (fetch-depth: 0).")

    def load(self, sha):
        if sha not in self._cache:
            self._cache[sha] = json.loads(git("show", f"{sha}:{self.path}"))
        return self._cache[sha]

    def before(self, when):
        hit = None
        for t, sha in self.versions:
            if t < when:
                hit = (t, sha)
        return hit

    def after(self, when):
        for t, sha in self.versions:
            if t > when:
                return (t, sha)
        return None

    def latest(self):
        return self.versions[-1]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def tipoff(date_str):
    d = datetime.date.fromisoformat(date_str)
    return datetime.datetime(d.year, d.month, d.day, TIPOFF_HOUR, tzinfo=CENTRAL)


def game_day_end(date_str):
    d = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=1)
    return datetime.datetime(d.year, d.month, d.day, 0, tzinfo=CENTRAL)


def pct_text(p):
    if p >= 0.995:
        return ">99%"
    if p <= 0.005:
        return "<1%"
    return f"{round(p * 100)}%"


def a_an(text):
    """Article for a percent or a score: 'an 87%', 'an 8%', 'an 11%', 'a 46%', 'a >99%',
    'an 84-81 win', 'an 11-8 win', 'a 63-54 win'."""
    import re
    m = re.match(r"(\d+)", text)
    if not m:
        return "a " + text
    n = m.group(1)
    vowel = n.startswith("8") or n in ("11", "18") or (len(n) in (5, 6) and n[:2] in ("11", "18"))
    return ("an " if vowel else "a ") + text


def ordinal_word(n):
    words = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
             9: "ninth"}
    return words.get(n, f"{n}th")


def ratings_maps(doc):
    teams = {t["school"]: t for t in doc.get("teams", [])}
    by_class = defaultdict(list)
    for t in doc.get("teams", []):
        if t.get("ovr_rating") is not None:
            by_class[t.get("classification")].append(t)
    class_rank, class_size = {}, {}
    for cls, ts in by_class.items():
        ts.sort(key=lambda t: -t["ovr_rating"])
        class_size[cls] = len(ts)
        for i, t in enumerate(ts, 1):
            class_rank[t["school"]] = i
    return teams, class_rank, class_size


def has_off_def(t):
    return t is not None and t.get("off_rating") is not None and t.get("def_rating") is not None


# --------------------------------------------------------------------------
# postseason
# --------------------------------------------------------------------------

def load_district_rounds():
    """{frozenset(teams): (round, class, district)} for this season's district games."""
    out = {}
    try:
        text = mp.fetch_data_text(mp.DISTRICT_GAMES_FILE)
    except Exception as e:
        print(f"  WARNING: {mp.DISTRICT_GAMES_FILE} unavailable ({e}); postseason labels use the window only")
        return out
    for row in csv.DictReader(io.StringIO(text)):
        try:
            if int(row["year"]) != SEASON:
                continue
            out[frozenset((row["team1"], row["team2"]))] = (row.get("round") or "", row.get("class"), row.get("district"))
        except (KeyError, ValueError):
            continue
    return out


class Postseason:
    """District rounds come from the district-games file. After a team's last
    district game, postseason games are the state series, numbered by how many
    state games the team had already played: sectional, quarterfinal,
    semifinal, then the championship (both teams unbeaten in the state series)
    or the third-place game. A class with 16 districts plays four state rounds
    (sectionals first); a class with 8 plays three (quarterfinals first) -
    counted from this season's district file, so a change in format follows
    automatically."""

    STATE_ROUNDS = ["State sectional", "State quarterfinal", "State semifinal", "FINAL"]

    def __init__(self, games, district_rounds):
        self.rounds = district_rounds
        self.last_district = {}
        for g in games:
            if frozenset((g["team1"], g["team2"])) in self.rounds and mp.in_postseason_window(g["date"]):
                for n in (g["team1"], g["team2"]):
                    self.last_district[n] = max(self.last_district.get(n, ""), g["date"][:10])
        self.team_class, districts = {}, defaultdict(set)
        for (rnd, cls, dist), pair in ((v, k) for k, v in self.rounds.items()):
            districts[cls].add(dist)
            for n in pair:
                self.team_class[n] = cls
        self.state_rounds = {}
        for cls, ds in districts.items():
            k = max(1, (len(ds) - 1).bit_length())          # 16 districts -> 4 rounds, 8 -> 3
            self.state_rounds[cls] = self.STATE_ROUNDS[-k:] if k <= 4 else None
        self.state_games = defaultdict(list)       # team -> [(date, won)]
        for g in games:
            if g.get("score1") is None or g.get("score2") is None:
                continue
            if self._is_state(g):
                self.state_games[g["team1"]].append((g["date"][:10], g["score1"] > g["score2"]))
                self.state_games[g["team2"]].append((g["date"][:10], g["score2"] > g["score1"]))

    def _is_state(self, g):
        date = g["date"][:10]
        if not mp.in_postseason_window(date) or frozenset((g["team1"], g["team2"])) in self.rounds:
            return False
        last = [self.last_district.get(n) for n in (g["team1"], g["team2"])]
        return all(last) and all(date > d for d in last)

    def label(self, g):
        """(label, district_final_info) - label None for a regular-season game."""
        date = g["date"][:10]
        if not mp.in_postseason_window(date):
            return None, None
        hit = self.rounds.get(frozenset((g["team1"], g["team2"])))
        if hit:
            rnd, cls, dist = hit
            if rnd.strip().lower() == "final":
                return "District final", (cls, dist)
            return (f"District {rnd.strip().lower()}" if rnd else "District tournament"), None
        if not self._is_state(g):
            return None, None
        prior = [[w for d, w in self.state_games[n] if d < date] for n in (g["team1"], g["team2"])]
        names = self.state_rounds.get(self.team_class.get(g["team1"])) or self.state_rounds.get(self.team_class.get(g["team2"]))
        if len(prior[0]) != len(prior[1]) or not names:   # a state game missing from the file
            return "State tournament", None
        n = len(prior[0])
        if n < len(names) - 1:
            return names[n], None
        if n == len(names) - 1:
            return ("State championship" if all(all(p) for p in prior) else "Third place game"), None
        return "State tournament", None


# --------------------------------------------------------------------------
# per-team season context
# --------------------------------------------------------------------------

def team_logs(games):
    out = defaultdict(list)
    for g in games:
        if g.get("score1") is None or g.get("score2") is None:
            continue
        d = g["date"][:10]
        for me, opp, ms, os_, cl in ((g["team1"], g["team2"], g["score1"], g["score2"], g.get("team1_classified", True)),
                                     (g["team2"], g["team1"], g["score2"], g["score1"], g.get("team2_classified", True))):
            out[me].append({"date": d, "opp": opp, "pf": ms, "pa": os_, "forfeit": g.get("forfeit", False),
                            "result": "W" if ms > os_ else ("L" if ms < os_ else "T"), "classified": cl})
    for v in out.values():
        v.sort(key=lambda x: x["date"])
    return out


def context_through(log, date_str, opp):
    upto = [x for x in log if x["date"] <= date_str]
    this = None
    for x in upto:
        if x["date"] == date_str and x["opp"] == opp:
            this = x
    prior = [x for x in upto if x is not this]
    w = sum(1 for x in upto if x["result"] == "W")
    l = sum(1 for x in upto if x["result"] == "L")
    t = sum(1 for x in upto if x["result"] == "T")
    streak_kind, streak = None, 0
    for x in reversed(upto):
        if streak_kind is None:
            streak_kind = x["result"]
        if x["result"] != streak_kind:
            break
        streak += 1
    return {"this": this, "prior": prior, "upto": upto, "wins": w, "losses": l, "ties": t,
            "record": f"{w}-{l}" + (f"-{t}" if t else ""), "streak_kind": streak_kind, "streak": streak}


def team_notes(name, ctx, post_teams, is_winner):
    notes = []
    this, prior = ctx["this"], ctx["prior"]
    if this is None:
        return notes
    if is_winner and ctx["streak_kind"] == "W" and ctx["streak"] >= MIN_STREAK:
        unbeaten = ctx["losses"] == 0 and ctx["ties"] == 0
        notes.append({"kind": "streak", "label": "Streak",
                      "text": f"{ordinal_word(ctx['streak']).capitalize()} straight win" + (" to open the season." if unbeaten else ".")})
    if (not is_winner) and ctx["streak_kind"] == "L" and ctx["streak"] >= MIN_STREAK:
        notes.append({"kind": "skid", "label": "Skid", "text": f"{ordinal_word(ctx['streak']).capitalize()} straight loss."})
    if (not is_winner) and this["result"] == "L" and ctx["losses"] == 1 and len(prior) >= MIN_PRIOR_GAMES:
        notes.append({"kind": "first_loss", "label": "First loss",
                      "text": f"First loss of the season after a {len(prior)}-0 start."
                      if all(p["result"] == "W" for p in prior) else "First loss of the season."})
    if is_winner and this["result"] == "W":
        beaten = [x for x in ctx["upto"] if x["result"] == "W" and post_teams.get(x["opp"], {}).get("ovr_rating") is not None]
        if len(beaten) >= 2:
            best = max(beaten, key=lambda x: post_teams[x["opp"]]["ovr_rating"])
            if best is this:
                rank = post_teams[this["opp"]].get("ovr_rank")
                notes.append({"kind": "best_win", "label": "Best win",
                              "text": f"{this['opp']}" + (f" (#{rank} statewide)" if rank else "") +
                                      f" is the highest-rated team {name} has beaten this season so far."})
    if len(prior) >= MIN_PRIOR_GAMES and not this["forfeit"]:
        if this["pf"] > max(p["pf"] for p in prior):
            prev = max(prior, key=lambda p: p["pf"])
            notes.append({"kind": "most_scored", "label": "Season high",
                          "text": f"{this['pf']} points is {name}'s most this season so far (previous high: {prev['pf']} vs {prev['opp']})."})
        if this["pa"] > max(p["pa"] for p in prior):
            prev = max(prior, key=lambda p: p["pa"])
            notes.append({"kind": "most_allowed", "label": "Allowed",
                          "text": f"{this['pa']} points is the most {name} has allowed this season so far (previous high: {prev['pa']} vs {prev['opp']})."})
    return notes


# --------------------------------------------------------------------------
# headline + story
# --------------------------------------------------------------------------

def write_headline(W, L, sw, sl, proj, dist_final, rnd=None, cls=None):
    score = f"{sw}-{sl}"
    if rnd == "State championship":
        return f"{W} wins the Class {cls} state championship." if cls else f"{W} wins the state championship."
    if dist_final:
        cls, dist = dist_final
        return f"{W} wins the Class {cls} District {dist} title." if cls and dist else f"{W} wins its district title."
    if proj:
        wp_w, margin_proj = proj["win_prob_winner"], proj["score_winner"] - proj["score_loser"]
        actual = sw - sl
        if wp_w < UPSET_PROB:
            return f"Upset: {W} knocks off {L} as {a_an(pct_text(wp_w))} shot."
        if abs(actual - margin_proj) <= ON_NUMBER_POINTS:
            how = "exactly" if actual == margin_proj else "almost exactly"
            return f"Right on the number: {W} wins by {actual}, {how} the projected margin."
        if actual <= margin_proj - SWING_POINTS:
            return f"{W} survives: {a_an(score)} win that was closer than projected."
        if actual >= margin_proj + SWING_POINTS:
            return f"{W} rolls past {L}, {actual - margin_proj} points better than projected."
    return f"{W} beats {L}, {score}."


def write_story(W, L, sw, sl, ctxW, ctxL, proj, rnd, dist_final, notesW, classified_L, classified_W=True):
    out = []
    score = f"{sw}-{sl}"
    lose_txt = f"; {L} falls to {ctxL['record']}" if classified_L else ""
    if dist_final:
        cls, dist = dist_final
        out.append(f"{W} won the Class {cls} District {dist} championship with {a_an(score)} win over {L} "
                   f"and moves on to the state tournament at {ctxW['record']}.")
    elif rnd == "State championship":
        out.append(f"{W} finished the season {ctxW['record']} with {a_an(score)} win over {L} in the state championship game.")
        if classified_L:
            out.append(f"{L} finished {ctxL['record']}.")
    elif rnd:
        where = {"State tournament": "in the state tournament", "Third place game": "in the third-place game"}.get(rnd, f"in the {rnd.lower()}s" if rnd.startswith("State") else f"in the {rnd.lower()}")
        if classified_W:
            out.append(f"{W} improved to {ctxW['record']} with {a_an(score)} win over {L} {where}.")
        else:
            out.append(f"{W} beat {L} {score} {where}.")
        if classified_L and rnd not in ("Third place game", "State semifinal"):
            out.append(f"{L}'s season ends at {ctxL['record']}.")
    else:
        if classified_W:
            out.append(f"{W} improved to {ctxW['record']} with {a_an(score)} win over {L}{lose_txt}.")
        else:
            out.append(f"{W} beat {L} {score}{lose_txt}.")
    if proj:
        fav_is_w = proj["score_winner"] >= proj["score_loser"]
        fav = W if fav_is_w else L
        a, b = (proj["score_winner"], proj["score_loser"]) if fav_is_w else (proj["score_loser"], proj["score_winner"])
        wp = proj["win_prob_winner"] if fav_is_w else 1 - proj["win_prob_winner"]
        out.append(f"The pre-game projection had {fav} by {a - b} ({a}-{b}) with {a_an(pct_text(wp))} chance to win.")
        offW, offL = sw - proj["raw_winner"], sl - proj["raw_loser"]
        if offW >= PERF_POINTS and offL >= PERF_POINTS:
            out.append(f"Both offenses beat their projections by {PERF_POINTS} or more points.")
        elif offW <= -PERF_POINTS and offL <= -PERF_POINTS:
            out.append("Both defenses held the other side well under its projection.")
        else:
            if offW >= PERF_POINTS:
                out.append(f"{W}'s offense scored {round(offW)} more points than projected.")
            if offL <= -PERF_POINTS:
                out.append(f"{W}'s defense allowed {round(-offL)} fewer points than projected.")
    bits = []
    for n in notesW:
        if n["kind"] == "streak":
            bits.append(f"It was {W}'s {ordinal_word(ctxW['streak'])} straight win")
        if n["kind"] == "best_win":
            bits.append("its best win of the season so far by opponent rating" if bits else
                        f"It was {W}'s best win of the season so far by opponent rating")
    if bits:
        out.append(" and ".join(bits) + ".")
    return " ".join(s for s in out if s)


# --------------------------------------------------------------------------
# one recap
# --------------------------------------------------------------------------

def build_recap(g, gid, logs, rh, post_season, resolver, now):
    date = g["date"][:10]
    to, end = tipoff(date), game_day_end(date)
    waited = (now - end).days >= WAIT_DAYS

    post = rh.after(end)
    if post is None:
        if not waited:
            return None, "waiting for post-game ratings"
        post = rh.latest()
    post_doc = rh.load(post[1])
    post_teams, post_class_rank, post_class_size = ratings_maps(post_doc)
    pre = rh.before(to)
    pre_doc = rh.load(pre[1]) if pre else {}
    pre_teams = ratings_maps(pre_doc)[0] if pre else {}

    rnd, dist_final = post_season.label(g)

    s1, s2 = g["score1"], g["score2"]
    if s1 >= s2:
        W, L, sw, sl, cw, cl = g["team1"], g["team2"], s1, s2, g.get("team1_classified", True), g.get("team2_classified", True)
    else:
        W, L, sw, sl, cw, cl = g["team2"], g["team1"], s2, s1, g.get("team2_classified", True), g.get("team1_classified", True)
    tie = s1 == s2

    ctxW = context_through(logs[W], date, L)
    ctxL = context_through(logs[L], date, W)

    proj = None
    if has_off_def(pre_teams.get(W)) and has_off_def(pre_teams.get(L)) and pre_doc.get("league_average") is not None:
        a, b = pre_teams[W], pre_teams[L]
        la = mp.league_average_for(pre_doc)
        raw_w, raw_l, sc_w, sc_l, wp_w = mp.projection(a["off_rating"], a["def_rating"], b["off_rating"], b["def_rating"], la)
        proj = {"raw_winner": round(raw_w, 2), "raw_loser": round(raw_l, 2), "score_winner": sc_w, "score_loser": sc_l,
                "win_prob_winner": round(wp_w, 4), "league_average": la,
                "ratings_updated": mp.central_time(pre_doc.get("last_updated"))}

    cls = None
    for nm in (W, L):
        if post_teams.get(nm, {}).get("classification") is not None:
            cls = post_teams[nm]["classification"]
            break

    notesW = [] if tie else team_notes(W, ctxW, post_teams, True)
    notesL = [] if tie else team_notes(L, ctxL, post_teams, False)
    if proj and not tie and proj["win_prob_winner"] < UPSET_PROB:
        notesW.insert(0, {"kind": "upset", "label": "Upset", "text": f"Won with {a_an(pct_text(proj['win_prob_winner']))} pre-game chance to win."})
    if dist_final and not tie:
        notesW.insert(0, {"kind": "title", "label": "District title",
                          "text": f"Class {dist_final[0]} District {dist_final[1]} champions."})

    def side(name, score, ctx, classified, notes, raw_for, raw_against):
        pt = post_teams.get(name)
        slug = resolver(name) if classified else None
        perf = None
        if raw_for is not None and not g.get("forfeit"):
            off = score - raw_for
            deff = raw_against - (ctx["this"]["pa"] if ctx["this"] else 0)
            perf = {"off": round(off, 1), "def": round(deff, 1), "ovr": round(off + deff, 1)}
        return {
            "name": name, "slug": slug, "logo": mp.LOGO_URL.format(slug=slug) if slug else None,
            "score": score, "classified": bool(classified),
            "record": ctx["record"] if classified else None,
            "ovr_rank": pt.get("ovr_rank") if pt else None,
            "class": pt.get("classification") if pt else None,
            "class_rank": post_class_rank.get(name),
            "class_size": post_class_size.get(pt.get("classification")) if pt else None,
            "ovr_rating": pt.get("ovr_rating") if pt else None,
            "perf": perf, "notes": notes, "odds": None,
        }

    winner = side(W, sw, ctxW, cw, notesW, proj["raw_winner"] if proj else None, proj["raw_loser"] if proj else None)
    loser = side(L, sl, ctxL, cl, notesL, proj["raw_loser"] if proj else None, proj["raw_winner"] if proj else None)

    if tie:
        headline, story = f"{W} and {L} play to a {sw}-{sl} tie.", ""
    elif g.get("forfeit"):
        headline, story = f"{W} wins by forfeit over {L}.", ""
    else:
        headline = write_headline(W, L, sw, sl, proj, dist_final, rnd, cls)
        story = write_story(W, L, sw, sl, ctxW, ctxL, proj, rnd, dist_final, notesW, cl, cw)

    return {
        "id": gid, "sport": SPORT, "date": date, "week": None, "round": rnd, "class": cls,
        "postseason": bool(rnd), "tie": tie, "forfeit": bool(g.get("forfeit")), "overtime": bool(g.get("overtime")),
        "sides": [winner, loser],
        "projection": proj,
        "headline": headline, "story": story,
        "frozen_at": now.astimezone(CENTRAL).isoformat(timespec="minutes"),
        "post_ratings_updated": mp.central_time(post_doc.get("last_updated")),
        "post_ratings_stamp": post_doc.get("last_updated"),
        "sources": {"pre_ratings": pre[1][:10] if pre else None, "post_ratings": post[1][:10]},
    }, None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="recompute every recap (unfreeze all)")
    ap.add_argument("--rebuild-game", action="append", default=[], help="recompute one recap id (repeatable)")
    args = ap.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)

    games = mp.load_json(GAMES_PATH)
    logs = team_logs(games)
    rh = History(RATINGS_PATH)
    resolver = mp.SlugResolver(mp.load_data_file("output/schools.json")["schools"],
                               mp.load_data_file("school_aliases.json"))
    post_season = Postseason(games, load_district_rounds())

    existing = {}
    if os.path.isdir(OUTPUT_DIR) and not args.rebuild:
        for fn in sorted(os.listdir(OUTPUT_DIR)):
            if fn.endswith(".json") and fn != "manifest.json":
                existing.update(mp.load_json(os.path.join(OUTPUT_DIR, fn)).get("recaps", {}))

    def sid(name):
        return resolver(name) or mp.make_slug(name)

    recaps, waiting, rebuilt_scores = {}, 0, []
    for g in games:
        if g.get("score1") is None or g.get("score2") is None:
            continue
        date = g["date"][:10]
        gid = f"{date}-{sid(g['team1'])}-vs-{sid(g['team2'])}"
        old = existing.get(gid)
        if old and gid not in args.rebuild_game:
            if sorted(s["score"] for s in old["sides"]) == sorted([g["score1"], g["score2"]]):
                recaps[gid] = old
                continue
            rebuilt_scores.append(gid)
        rec, _why = build_recap(g, gid, logs, rh, post_season, resolver, now)
        if rec is None:
            waiting += 1
            continue
        recaps[gid] = rec

    by_date = defaultdict(dict)
    for gid, r in recaps.items():
        by_date[r["date"]][gid] = r
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    changed = 0
    for date, items in sorted(by_date.items()):
        path = os.path.join(OUTPUT_DIR, f"{date}.json")
        text = json.dumps({"season": SEASON, "sport": SPORT, "date": date, "recaps": dict(sorted(items.items()))},
                          separators=(",", ":"))
        old_text = open(path).read() if os.path.exists(path) else None
        if text != old_text:
            with open(path, "w") as f:
                f.write(text)
            changed += 1
    dates = {d: len(v) for d, v in sorted(by_date.items())}
    old_manifest = mp.load_json(MANIFEST_PATH) if os.path.exists(MANIFEST_PATH) else {}
    if changed or old_manifest.get("dates") != dates:
        latest_doc = rh.load(rh.latest()[1])
        with open(MANIFEST_PATH, "w") as f:
            json.dump({"season": SEASON, "sport": SPORT, "generated": now.astimezone(CENTRAL).isoformat(timespec="minutes"),
                       "league_average": mp.league_average_for(latest_doc), "dates": dates}, f, indent=1)

    new = len([k for k in recaps if k not in existing])
    print(f"{len(recaps)} recaps ({new} new, {len(rebuilt_scores)} rebuilt for score corrections), "
          f"{waiting} final games still waiting for post-game ratings, {changed} date files written.")
    for gid in rebuilt_scores:
        print(f"  score corrected -> rebuilt {gid}")


if __name__ == "__main__":
    main()
