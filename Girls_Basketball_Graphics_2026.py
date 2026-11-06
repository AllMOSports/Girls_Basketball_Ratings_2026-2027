"""
Girls Basketball 2026-27 -- Rankings graphics (All MO Sports)
 
Builds Top-10 rankings PNGs from the nightly ratings output:
  graphics/girls_basketball_all_classes_2026.png      (statewide)
  graphics/girls_basketball_class_<N>_2026.png        (one per class)
 
Superscript ranks (1st, 2nd, ...) are ranks WITHIN that graphic's scope:
statewide on the all-classes graphic, within the class on class graphics.
 
Repo files it uses:
  girls_basketball_ratings_2026.json    ratings output (required)
  classifications.json             only if the ratings JSON has no class field
  girls_basketball_games_2026.json      to find the "through <date>" games date
  allmosports_logo.png             top-right brand logo
  fonts/BigShouldersDisplay-800.ttf, fonts/BigShouldersDisplay-700.ttf,
  fonts/DejaVuSans-Bold.ttf        (downloaded automatically if missing)
  logos/<slug>.png                 optional local team logos (checked first)
 
Optional environment overrides:
  THROUGH_DATE=2026-12-15   force the subtitle date
  TOP_N=10                  number of teams per graphic
"""
 
import json
import os
import re
import sys
from datetime import datetime, timedelta, date
from io import BytesIO
from zoneinfo import ZoneInfo
 
import requests
from PIL import Image, ImageDraw, ImageFont
 
# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
SPORT_TITLE   = "GIRLS BASKETBALL"
SEASON        = 2026          # file names (season label = start year)
SEASON_LABEL  = "2026-27"     # what prints in the statewide title
RATINGS_FILE  = "girls_basketball_ratings_2026.json"
GAMES_FILE    = "girls_basketball_games_2026.json"
CLASS_FILE    = "classifications.json"
CLASS_SPORT_KEYS = ["girls_basketball", "girls basketball", "Girls Basketball", "basketball_girls", "gbb"]
BRAND_LOGO    = "allmosports_logo.png"
OUT_DIR       = "graphics"
FILE_PREFIX   = "girls_basketball"
TOP_N         = int(os.environ.get("TOP_N", "10"))
NOTE_TEXT     = "ONLY GAMES AGAINST MSHSAA OPPONENTS ARE INCLUDED IN THESE RATINGS"
HANDLE        = "@All_MO_Sports"
SITE          = "allmosports.com"
EVENT_WORD    = "GAMES"
SHOW_OFF_DEF  = True      # False = OVR only, team name centered in the row
DECIMALS      = 1         # basketball ratings are in points (soccer used 2 for goals)
 
# Team logos: local folder first, then this URL ({slug} is filled in).
LOCAL_LOGO_DIR = "logos"
LOGO_URL = ("https://raw.githubusercontent.com/AllMOSports/All_MO_Sports-Data/"
            "main/logos_main2/{slug}.png")
 
# Field-name candidates in the ratings JSON (first match wins).
F_TEAM  = ["display_name", "team", "Team", "school", "School", "name", "Name"]
F_OFF   = ["off", "OFF", "off_rating", "offense", "Offense", "offensive_rating", "Off"]
F_DEF   = ["def", "DEF", "def_rating", "defense", "Defense", "defensive_rating", "Def"]
F_OVR   = ["ovr", "OVR", "overall", "Overall", "ovr_rating", "rating", "Rating", "overall_rating"]
F_CLASS = ["class", "Class", "classification", "Classification", "cls"]
F_SLUG  = ["slug", "Slug", "logo_slug"]
 
# Fonts (committed in fonts/ -- downloaded on first run if missing)
FONT_DIR = "fonts"
FONT_SOURCES = {
    "BigShouldersDisplay-800.ttf":
        "https://cdn.jsdelivr.net/npm/@fontsource/big-shoulders-display/files/"
        "big-shoulders-display-latin-800-normal.woff2",
    "BigShouldersDisplay-700.ttf":
        "https://cdn.jsdelivr.net/npm/@fontsource/big-shoulders-display/files/"
        "big-shoulders-display-latin-700-normal.woff2",
    "DejaVuSans-Bold.ttf":
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
}
 
# Colors
NAVY      = (16, 30, 58)      # #101E3A
GOLD      = (235, 172, 48)    # #EBAC30  bars, circles, rules
GOLD_TEXT = (201, 142, 29)    # darker gold for text
GRAY      = (107, 116, 136)
ROW_SHADE = (233, 237, 245)
WHITE     = (255, 255, 255)
 
# Layout (px) -- matched to the original 1160-wide graphic
W         = 1160
ROW_TOP0  = 426
ROW_PITCH = 118
ROW_H     = 109
ROW_X0, ROW_X1 = 50, 1110
NAME_X    = 220
OVR_RIGHT = 1090      # right edge of the OVR ordinal
NAME_MAX_W = 700
 
# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"
 
 
def pick(rec, keys, default=None):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
    return default
 
 
def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())
 
 
def slugify(s):
    s = str(s).lower().replace("&", "and").replace("'", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")
 
 
def class_num(v):
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None
 
 
def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for fname, url in FONT_SOURCES.items():
        path = os.path.join(FONT_DIR, fname)
        if os.path.exists(path):
            continue
        try:
            print(f"Downloading font {fname} ...")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.content
            if url.endswith((".woff2", ".woff")):
                from fontTools.ttLib import TTFont  # pip install fonttools brotli
                f = TTFont(BytesIO(data))
                f.flavor = None
                f.save(path)
            else:
                with open(path, "wb") as fh:
                    fh.write(data)
        except Exception as e:
            print(f"  WARNING: could not get {fname}: {e}")
 
 
def load_font(fname, size):
    for p in (os.path.join(FONT_DIR, fname),
              f"/usr/share/fonts/truetype/dejavu/{fname}"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        if os.path.exists(p):
            print(f"  WARNING: {fname} missing, using DejaVu Sans Bold")
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)
 
 
def head(size, weight=800):
    return load_font(f"BigShouldersDisplay-{weight}.ttf", size)
 
 
def body(size):
    return load_font("DejaVuSans-Bold.ttf", size)
 
 
def text_w(draw, text, font):
    return draw.textlength(text, font=font)
 
 
def draw_spaced(draw, x_center, y_baseline, text, font, fill, tracking):
    total = sum(text_w(draw, c, font) for c in text) + tracking * (len(text) - 1)
    x = x_center - total / 2
    for c in text:
        draw.text((x, y_baseline), c, font=font, fill=fill, anchor="ls")
        x += text_w(draw, c, font) + tracking
 
 
def paste_circle(img, cx, cy, r, color, scale=4):
    """Anti-aliased filled circle."""
    size = 2 * r
    big = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    mask = big.resize((size, size), Image.LANCZOS)
    img.paste(Image.new("RGB", (size, size), color), (cx - r, cy - r), mask)
 
 
def fit_image(im, box_w, box_h):
    im = im.convert("RGBA")
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((box_w, box_h), Image.LANCZOS)
    return im
 
 
_logo_cache = {}
 
 
def _fetch_logo(slug):
    """Look up one slug: local logos/ folder first, then LOGO_URL."""
    if slug in _logo_cache:
        return _logo_cache[slug]
    im = None
    for ext in ("png", "webp", "jpg"):
        p = os.path.join(LOCAL_LOGO_DIR, f"{slug}.{ext}")
        if os.path.exists(p):
            im = Image.open(p)
            break
    if im is None and LOGO_URL:
        try:
            r = requests.get(LOGO_URL.format(slug=slug), timeout=15)
            if r.status_code == 200:
                im = Image.open(BytesIO(r.content))
        except Exception:
            pass
    _logo_cache[slug] = im
    return im
 
 
def logo_candidates(team):
    """Slugs to try, in order. Co-op teams ("Host with Partner, Partner2")
    fall back to the host school's logo, since that's the file that exists."""
    names = [team["slug"], slugify(team["name"])]
    m = re.split(r"\s+with\s+", team["name"], maxsplit=1, flags=re.I)
    if len(m) == 2:
        host = m[0].strip()
        names += [slugify(host)]
    out = []
    for s in names:
        if s and s not in out:
            out.append(s)
    return out
 
 
def get_team_logo(team):
    tried = logo_candidates(team)
    for slug in tried:
        im = _fetch_logo(slug)
        if im is not None:
            return im
    print(f"  (no logo for '{team['name']}' -- tried: {', '.join(tried)})")
    return None
 
 
# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
def load_ratings():
    with open(RATINGS_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for k in ("teams", "ratings", "data", "rankings", "results"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            # dict keyed by team name
            if all(isinstance(v, dict) for v in data.values()):
                data = [dict(v, team=v.get("team", k)) for k, v in data.items()]
    if not isinstance(data, list):
        sys.exit(f"Couldn't find a list of teams in {RATINGS_FILE}")
 
    teams = []
    for rec in data:
        name = pick(rec, F_TEAM)
        off, dfn, ovr = pick(rec, F_OFF), pick(rec, F_DEF), pick(rec, F_OVR)
        if name is None or off is None or dfn is None:
            continue
        off, dfn = float(off), float(dfn)
        ovr = float(ovr) if ovr is not None else off + dfn
        teams.append({
            "name": str(name),
            "off": off, "def": dfn, "ovr": ovr,
            "cls": class_num(pick(rec, F_CLASS)),
            "slug": pick(rec, F_SLUG) or slugify(name),
        })
    print(f"Loaded {len(teams)} teams from {RATINGS_FILE}")
    return teams
 
 
def load_class_map():
    """Return {normalized school name: class number} from classifications.json."""
    if not os.path.exists(CLASS_FILE):
        return {}
    with open(CLASS_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
 
    def absorb(obj):
        if isinstance(obj, list):
            for rec in obj:
                if isinstance(rec, dict):
                    nm, cl = pick(rec, F_TEAM), pick(rec, F_CLASS)
                    if nm is not None and cl is not None:
                        out[norm(nm)] = class_num(cl)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, list) and class_num(k):        # {"1": [schools]}
                    for nm in v:
                        if isinstance(nm, str):
                            out[norm(nm)] = class_num(k)
                        elif isinstance(nm, dict):
                            n2 = pick(nm, F_TEAM)
                            if n2:
                                out[norm(n2)] = class_num(k)
                elif isinstance(v, (int, str)) and class_num(v):  # {"school": 3}
                    out[norm(k)] = class_num(v)
 
    if isinstance(data, dict):
        for key in CLASS_SPORT_KEYS:
            if key in data:
                absorb(data[key])
                break
        else:
            absorb(data)
    else:
        absorb(data)
    return out
 
 
def find_through_date():
    env = os.environ.get("THROUGH_DATE")
    if env:
        return datetime.strptime(env, "%Y-%m-%d").date()
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    best = None
    if os.path.exists(GAMES_FILE):
        try:
            with open(GAMES_FILE, encoding="utf-8") as fh:
                games = json.load(fh)
            if isinstance(games, dict):
                games = next((v for v in games.values() if isinstance(v, list)), [])
            for g in games:
                if not isinstance(g, dict):
                    continue
                raw = pick(g, ["date", "Date", "game_date", "match_date"])
                if not raw:
                    continue
                # The games file also lists upcoming (unscored) games -- only
                # completed ones count toward "through <date>".
                if g.get("score1") is None or g.get("score2") is None:
                    continue
                d = None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        d = datetime.strptime(str(raw)[:19], fmt).date()
                        break
                    except ValueError:
                        continue
                if d and d <= today and (best is None or d > best):
                    best = d
        except Exception as e:
            print(f"  WARNING: couldn't read {GAMES_FILE}: {e}")
    return best or (today - timedelta(days=1))
 
 
# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
def render(title, through, teams, out_path, brand_logo):
    """teams: full list for this scope (ranks computed across all of them)."""
    if not teams:
        return
    off_rank = {id(t): i + 1 for i, t in enumerate(sorted(teams, key=lambda t: -t["off"]))}
    def_rank = {id(t): i + 1 for i, t in enumerate(sorted(teams, key=lambda t: -t["def"]))}
    ranked = sorted(teams, key=lambda t: -t["ovr"])
    top = ranked[:TOP_N]
 
    n = len(top)
    last_bottom = ROW_TOP0 + (n - 1) * ROW_PITCH + ROW_H
    rule_y = last_bottom + 30
    H = rule_y + 93
 
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
 
    # top bar, handle, logo
    d.rectangle((0, 0, W, 9), fill=GOLD)
    d.text((50, 81), HANDLE, font=body(20), fill=GRAY, anchor="ls")
    if brand_logo is not None:
        bl = fit_image(brand_logo, 108, 108)
        img.paste(bl, (1066 - bl.width // 2, 90 - bl.height // 2), bl)
 
    # titles
    tfont = head(76)
    while text_w(d, title, tfont) > W - 140 and tfont.size > 40:
        tfont = head(tfont.size - 2)
    d.text((W / 2, 249), title, font=tfont, fill=NAVY, anchor="ms")
    sub = f"THROUGH {through.strftime('%B').upper()} {ordinal(through.day).upper()} {EVENT_WORD}"
    d.text((W / 2, 305), sub, font=head(42, 700), fill=NAVY, anchor="ms")
    draw_spaced(d, W / 2, 342, NOTE_TEXT, body(18), GOLD_TEXT, 1.5)
    d.rectangle((80, 365, 1080, 367), fill=GOLD)
 
    # column headers
    d.text((NAME_X, 400), "TEAM", font=body(18), fill=GRAY, anchor="ls")
    d.text((1087, 400), "OVR RATING", font=body(18), fill=GRAY, anchor="rs")
 
    f_name0 = head(52)
    f_lbl, f_val, f_sup = body(20), body(29), body(15)
    f_ovr, f_ovr_sup = body(34), body(15)
    f_circ = head(44)
 
    for i, t in enumerate(top):
        y = ROW_TOP0 + i * ROW_PITCH
        if i % 2 == 0:
            d.rectangle((ROW_X0, y, ROW_X1, y + ROW_H), fill=ROW_SHADE)
 
        # rank circle
        cy = y + 55
        paste_circle(img, 100, cy, 31, GOLD)
        d.text((100, cy + 1), str(i + 1), font=f_circ, fill=NAVY, anchor="mm")
 
        # team logo
        logo = get_team_logo(t)
        if logo is not None:
            lg = fit_image(logo, 60, 60)
            img.paste(lg, (176 - lg.width // 2, cy - lg.height // 2), lg)
 
        # team name (shrink if long)
        f_name = f_name0
        while text_w(d, t["name"], f_name) > NAME_MAX_W and f_name.size > 30:
            f_name = head(f_name.size - 2)
        name_base = y + 54 if SHOW_OFF_DEF else y + 74
        d.text((NAME_X, name_base), t["name"], font=f_name, fill=NAVY, anchor="ls")
 
        # OFF / DEF line
        if SHOW_OFF_DEF:
            x = NAME_X
            base = y + 96
            for lbl, val, rk in (("OFF", t["off"], off_rank[id(t)]),
                                 ("DEF", t["def"], def_rank[id(t)])):
                d.text((x, base), lbl, font=f_lbl, fill=GRAY, anchor="ls")
                x += text_w(d, lbl, f_lbl) + 8
                vs = f"{val:.{DECIMALS}f}"
                d.text((x, base), vs, font=f_val, fill=NAVY, anchor="ls")
                x += text_w(d, vs, f_val) + 4
                os_ = ordinal(rk)
                d.text((x, base - 20), os_, font=f_sup, fill=NAVY, anchor="ls")
                x += text_w(d, os_, f_sup) + 32
 
        # OVR rating + ordinal
        osup = ordinal(i + 1)
        sup_w = text_w(d, osup, f_ovr_sup)
        sup_x = OVR_RIGHT - sup_w
        d.text((sup_x, y + 36), osup, font=f_ovr_sup, fill=GOLD_TEXT, anchor="ls")
        d.text((sup_x - 4, y + 68), f"{t['ovr']:.{DECIMALS}f}", font=f_ovr, fill=GOLD_TEXT, anchor="rs")
 
    # footer
    d.rectangle((80, rule_y - 1, 1080, rule_y), fill=GOLD)
    d.text((W / 2, rule_y + 43), SITE, font=body(20), fill=GRAY, anchor="ms")
 
    img.save(out_path, optimize=True)
    print(f"Saved {out_path}")
 
 
# ----------------------------------------------------------------------------
def main():
    ensure_fonts()
    os.makedirs(OUT_DIR, exist_ok=True)
 
    teams = load_ratings()
    if not teams:
        sys.exit("No teams loaded -- nothing to draw.")
 
    # fill in class from classifications.json where missing
    if any(t["cls"] is None for t in teams):
        cmap = load_class_map()
        for t in teams:
            if t["cls"] is None:
                t["cls"] = cmap.get(norm(t["name"])) or cmap.get(norm(t["slug"]))
        missing = [t["name"] for t in teams if t["cls"] is None]
        if missing:
            print(f"  {len(missing)} teams have no class (left off class graphics): "
                  + ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else ""))
 
    through = find_through_date()
    print(f"Through date: {through.isoformat()}")
 
    brand = Image.open(BRAND_LOGO) if os.path.exists(BRAND_LOGO) else None
    if brand is None:
        print(f"  WARNING: {BRAND_LOGO} not found -- graphics will have no brand logo")
 
    render(f"{SEASON_LABEL} {SPORT_TITLE} RANKINGS", through, teams,
           os.path.join(OUT_DIR, f"{FILE_PREFIX}_all_classes_{SEASON}.png"), brand)
 
    for c in sorted({t["cls"] for t in teams if t["cls"] is not None}):
        scope = [t for t in teams if t["cls"] == c]
        render(f"CLASS {c} {SPORT_TITLE} RANKINGS", through, scope,
               os.path.join(OUT_DIR, f"{FILE_PREFIX}_class_{c}_{SEASON}.png"), brand)
 
 
if __name__ == "__main__":
    main()
