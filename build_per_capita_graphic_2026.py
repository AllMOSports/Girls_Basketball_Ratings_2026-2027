"""
All MO Sports - Size-Adjusted Rankings graphic (Top 10), 2026 season

One script for every sport, same as build_per_capita_2026.py:

    python build_per_capita_graphic_2026.py --sport fall_softball
    python build_per_capita_graphic_2026.py --sport boys_soccer
    python build_per_capita_graphic_2026.py --sport girls_volleyball
    python build_per_capita_graphic_2026.py --sport football
    python build_per_capita_graphic_2026.py --sport boys_basketball
    python build_per_capita_graphic_2026.py --sport girls_basketball

Reads per_capita_<sport>_2026.json (written by build_per_capita_2026.py) and
draws graphics/size_adjusted_rankings_<sport>_2026.png at 2160 x 2700
(4:5 portrait, full-size on X and Instagram).

Repo files it uses:
  per_capita_<sport>_2026.json          required
  allmosports_logo_reversed.png         top-right brand logo (white/gold on
                                        transparent). Falls back to a text
                                        wordmark if missing.
  fonts/Barlow-*.ttf, fonts/BarlowCondensed-*.ttf
                                        downloaded automatically if missing
  logos/<slug>.png                      optional local team logos (checked
                                        first, then All_MO_Sports-Data/logos_main2)

Optional environment overrides:
  TOP_N=10          number of teams on the graphic
"""

import argparse
import json
import os
import re
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
SEASON = 2026
# "season_label" is what prints in the header; basketball spans two years.
SPORTS = {
    "football":         {"label": "Football",         "decimals": 1},
    "fall_softball":    {"label": "Fall Softball",    "decimals": 2},
    "boys_soccer":      {"label": "Boys Soccer",      "decimals": 2},
    "girls_volleyball": {"label": "Girls Volleyball", "decimals": 2},
    "boys_basketball":  {"label": "Boys Basketball",  "decimals": 1,
                         "season_label": "2026-27"},
    "girls_basketball": {"label": "Girls Basketball", "decimals": 1,
                         "season_label": "2026-27"},
}
TOP_N = int(os.environ.get("TOP_N", "10"))
OUT_DIR = "graphics"
BRAND_LOGO = "allmosports_logo_reversed.png"
HANDLE = "@All_MO_Sports"
SITE = "allmosports.com"
ENROLLMENT_YEAR = "2026-27"

LOCAL_LOGO_DIR = "logos"
LOGO_URL = ("https://raw.githubusercontent.com/AllMOSports/All_MO_Sports-Data/"
            "main/logos_main2/{slug}.png")

# Shorter display names so rows stay on one line
DISPLAY_NAMES = {
    "St. Pius X (Kansas City)": "St. Pius X (KC)",
    "Lift for Life Academy Charter": "Lift for Life",
    "Northeast (Kansas City)": "Northeast (KC)",
    "Notre Dame (Cape Girardeau)": "Notre Dame (Cape)",
    "Incarnate Word Academy": "Incarnate Word",
}

FONT_DIR = "fonts"
_FS = "https://cdn.jsdelivr.net/npm/@fontsource/{pkg}/files/{pkg}-latin-{w}-normal.woff2"
FONT_SOURCES = {
    "Barlow-400.ttf": _FS.format(pkg="barlow", w=400),
    "Barlow-500.ttf": _FS.format(pkg="barlow", w=500),
    "Barlow-600.ttf": _FS.format(pkg="barlow", w=600),
    "Barlow-700.ttf": _FS.format(pkg="barlow", w=700),
    "BarlowCondensed-600.ttf": _FS.format(pkg="barlow-condensed", w=600),
    "BarlowCondensed-700.ttf": _FS.format(pkg="barlow-condensed", w=700),
    "BarlowCondensed-800.ttf": _FS.format(pkg="barlow-condensed", w=800),
}

# Colors
NAVY = (16, 30, 58)          # #101E3A
GOLD = (235, 172, 48)        # #EBAC30
GOLD_INK = (138, 100, 22)    # #8A6416  private tag / x-factor text
MUTED = (74, 85, 104)        # #4A5568
SUB = (213, 219, 232)        # #D5DBE8  light text on navy
LINE = (226, 231, 240)       # #E2E7F0
ZEBRA = (246, 248, 251)      # #F6F8FB
TRACK = (238, 241, 246)      # #EEF1F6
WHITE = (255, 255, 255)

# Layout in 1x px (1080 x 1350); everything is drawn at SCALE x.
SCALE = 2
W, H = 1080, 1350
PAD = 48
HEADER_H = 352
STRIPE_H = 8
TABLE_HEAD_BASE = 418     # baseline of column labels
TABLE_RULE_Y = 432
ROW_Y0 = 434
ROW_H = 80
FOOTER_H = 88
# Column edges (left/right), matching the HTML grid 64/76/1fr/112/118/124/180
C_RANK = PAD
C_LOGO_CX = 124 + 38
C_NAME = 212
R_ENR, R_OVR, R_EXP, R_POE = 574, 704, 840, 1032

MONTHS = {1: "Jan.", 2: "Feb.", 3: "March", 4: "April", 5: "May", 6: "June",
          7: "July", 8: "Aug.", 9: "Sept.", 10: "Oct.", 11: "Nov.", 12: "Dec."}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def s(v):
    """1x px -> output px."""
    return int(round(v * SCALE))


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
            from fontTools.ttLib import TTFont  # pip install fonttools brotli
            f = TTFont(BytesIO(r.content))
            f.flavor = None
            f.save(path)
        except Exception as e:
            print(f"  WARNING: could not get {fname}: {e}")


def font(name, size):
    path = os.path.join(FONT_DIR, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, s(size))
    fb = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(fb):
        print(f"  WARNING: {name} missing, using DejaVu Sans Bold")
        return ImageFont.truetype(fb, s(size))
    return ImageFont.load_default(s(size))


def cond(size, w=800):
    return font(f"BarlowCondensed-{w}.ttf", size)


def body(size, w=400):
    return font(f"Barlow-{w}.ttf", size)


def tw(d, text, f):
    return d.textlength(text, font=f)


def text_spaced(d, x, y, text, f, fill, tracking):
    """Left-aligned text with letter spacing (tracking in 1x px), baseline y."""
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor="ls")
        x += tw(d, ch, f) + s(tracking)
    return x


def spaced_width(d, text, f, tracking):
    return sum(tw(d, ch, f) for ch in text) + s(tracking) * (len(text) - 1)


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if tw(d, trial, f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def slugify(name):
    primary = re.split(r"\s+with\s+", name, maxsplit=1)[0]
    p = primary.lower().replace("'", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", "-", p).strip("-")


_logo_cache = {}


def team_logo(name):
    slug = slugify(name)
    if slug in _logo_cache:
        return _logo_cache[slug]
    im = None
    for ext in ("png", "webp", "jpg"):
        p = os.path.join(LOCAL_LOGO_DIR, f"{slug}.{ext}")
        if os.path.exists(p):
            im = Image.open(p)
            break
    if im is None:
        try:
            r = requests.get(LOGO_URL.format(slug=slug), timeout=15)
            if r.status_code == 200:
                im = Image.open(BytesIO(r.content))
        except Exception:
            pass
    if im is None:
        print(f"  (no logo for '{name}' -- tried {slug})")
    _logo_cache[slug] = im
    return im


def fit(im, box_w, box_h):
    im = im.convert("RGBA")
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)
    im.thumbnail((box_w, box_h), Image.LANCZOS)
    return im


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def fmt(x, dp, plus):
    x = round(x, dp)
    sign = "+" if (plus and x >= 0) else ("" if x >= 0 else "−")
    return f"{sign}{abs(x):.{dp}f}"


def ratings_date(text):
    """'October 21, 2026 at 07:19 AM' -> 'Oct. 21' (AP style)."""
    m = re.match(r"([A-Za-z]+)\s+(\d+)", text or "")
    if not m:
        return None
    names = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
    try:
        mon = names.index(m.group(1).lower()) + 1
    except ValueError:
        return None
    return f"{MONTHS[mon]} {int(m.group(2))}"


def display_name(name):
    if name in DISPLAY_NAMES:
        return DISPLAY_NAMES[name]
    return name.replace(" with ", " / ")


# ----------------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------------
def draw_header(img, d, label, brand, season_label):
    d.rectangle((0, 0, s(W), s(HEADER_H)), fill=NAVY)
    d.rectangle((0, s(HEADER_H), s(W), s(HEADER_H + STRIPE_H)), fill=GOLD)

    text_spaced(d, s(PAD), s(62), f"{season_label} {label} · TOP {TOP_N}".upper(),
                cond(24, 600), GOLD, 3)
    d.text((s(PAD), s(146)), "SIZE-ADJUSTED", font=cond(84, 800), fill=WHITE, anchor="ls")
    d.text((s(PAD), s(226)), "RANKINGS", font=cond(84, 800), fill=GOLD, anchor="ls")

    f_sub = body(24)
    sub = ("Who gets the most out of their enrollment? Each team's rating "
           "vs. what's expected for a school its size.")
    for i, line in enumerate(wrap(d, sub, f_sub, s(640))[:2]):
        d.text((s(PAD), s(280 + i * 32)), line, font=f_sub, fill=SUB, anchor="ls")

    if brand is not None:
        lg = fit(brand, s(200), s(200))
        img.paste(lg, (s(W - PAD) - lg.width, s(44)), lg)
    else:
        f = cond(40, 800)
        parts = [("All ", WHITE), ("MO", GOLD), (" Sports", WHITE)]
        x = s(W - PAD) - sum(tw(d, t, f) for t, _ in parts)
        for t, c in parts:
            d.text((x, s(80)), t, font=f, fill=c, anchor="ls")
            x += tw(d, t, f)


def draw_table_head(d):
    f = cond(19, 700)
    text_spaced(d, s(C_RANK), s(TABLE_HEAD_BASE), "RANK", f, MUTED, 1)
    text_spaced(d, s(C_NAME), s(TABLE_HEAD_BASE), "SCHOOL", f, MUTED, 1)

    def right(label, r, y, color=MUTED):
        text_spaced(d, s(r) - spaced_width(d, label, f, 1), s(y), label, f, color, 1)

    right("ENROLL.", R_ENR, TABLE_HEAD_BASE)
    right("OVR", R_OVR, TABLE_HEAD_BASE)
    right("EXPECTED", R_EXP, TABLE_HEAD_BASE - 21)
    right("FOR SIZE", R_EXP, TABLE_HEAD_BASE)
    right("OVER EXPECTED", R_POE, TABLE_HEAD_BASE, NAVY)
    d.rectangle((s(PAD), s(TABLE_RULE_Y), s(W - PAD), s(TABLE_RULE_Y + 2)), fill=NAVY)


def draw_tag(d, x, y_center, text):
    f = body(13, 600)
    tw_ = spaced_width(d, text, f, 1)
    padx, h = s(7), s(22)
    box = (x, y_center - h // 2, x + tw_ + 2 * padx, y_center + h // 2)
    d.rounded_rectangle(box, radius=s(4), outline=GOLD_INK, width=max(1, s(1)))
    text_spaced(d, x + padx, y_center + s(5), text, f, GOLD_INK, 1)


def draw_row(img, d, i, t, dp, max_poe):
    top = ROW_Y0 + i * ROW_H
    if i % 2:
        d.rectangle((s(PAD), s(top), s(W - PAD), s(top + ROW_H)), fill=ZEBRA)
    d.rectangle((s(PAD), s(top + ROW_H - 1), s(W - PAD), s(top + ROW_H)), fill=LINE)
    cy = top + ROW_H / 2

    d.text((s(C_RANK), s(cy)), str(i + 1), font=cond(44, 800), fill=NAVY, anchor="lm")

    logo = team_logo(t["school"])
    if logo is not None:
        lg = fit(logo, s(62), s(62))
        img.paste(lg, (s(C_LOGO_CX) - lg.width // 2, s(cy) - lg.height // 2), lg)

    # Enrollment (right-aligned) -- drawn first so the name knows its room
    f_enr = body(25, 500)
    enr = f"{int(t['enrollment']):,}"
    factor = t.get("private_factor", 1.0) or 1.0
    x_txt = s(R_ENR)
    if factor > 1:
        f_x = body(14, 600)
        xs = f"×{factor:g}"
        x_txt -= tw(d, xs, f_x)
        d.text((x_txt, s(cy - 6)), xs, font=f_x, fill=GOLD_INK, anchor="ls")
        x_txt -= s(3)
    d.text((x_txt, s(cy + 9)), enr, font=f_enr, fill=NAVY, anchor="rs")
    enr_left = x_txt - tw(d, enr, f_enr)

    # Name (shrinks to fit) + class line
    name = display_name(t["school"])
    size = 27
    room = enr_left - s(C_NAME) - s(16)
    f_name = body(size, 700)
    while tw(d, name, f_name) > room and size > 18:
        size -= 1
        f_name = body(size, 700)
    d.text((s(C_NAME), s(cy - 4)), name, font=f_name, fill=NAVY, anchor="ls")
    f_cls = body(17)
    cls = f"Class {t['classification']}"
    d.text((s(C_NAME), s(cy + 22)), cls, font=f_cls, fill=MUTED, anchor="ls")
    tag = {"private": "PRIVATE", "charter": "CHARTER", "mixed": "CO-OP"}.get(t.get("school_type"))
    if tag:
        draw_tag(d, s(C_NAME) + tw(d, cls, f_cls) + s(8), s(cy + 16), tag)

    # OVR + statewide-rank superscript
    f_ovr = body(27, 600)
    f_sup = body(14, 500)
    sup = ordinal(int(t["ovr_rank"]))
    x_sup = s(R_OVR) - tw(d, sup, f_sup)
    d.text((x_sup, s(cy - 6)), sup, font=f_sup, fill=MUTED, anchor="ls")
    d.text((x_sup - s(2), s(cy + 9)), fmt(t["ovr_rating"], dp, False),
           font=f_ovr, fill=NAVY, anchor="rs")

    # Expected for size
    d.text((s(R_EXP), s(cy + 9)), fmt(t["expected_ovr"], dp, True),
           font=body(25), fill=MUTED, anchor="rs")

    # Over expected + bar
    poe = t["points_over_expected"]
    d.text((s(R_POE), s(cy + 6)), fmt(poe, dp, True), font=cond(38, 800), fill=NAVY, anchor="rs")
    bar_y = s(cy + 16)
    bx0, bx1 = s(R_POE - 150), s(R_POE)
    d.rounded_rectangle((bx0, bar_y, bx1, bar_y + s(6)), radius=s(3), fill=TRACK)
    frac = max(0.0, min(1.0, poe / max_poe)) if max_poe > 0 else 0
    if frac > 0:
        d.rounded_rectangle((bx1 - int((bx1 - bx0) * frac), bar_y, bx1, bar_y + s(6)),
                            radius=s(3), fill=GOLD)


def draw_footer(d, factor, updated):
    y0 = H - FOOTER_H
    d.rectangle((0, s(y0), s(W), s(H)), fill=NAVY)
    adj = (f"co-ops combined; privates ×{factor:g}" if factor > 1
           else "co-ops combined; no private adjustment")
    note = (f"Expected rating by {ENROLLMENT_YEAR} MSHSAA enrollment, fit on public schools "
            f"({adj}). Ranked by points over expected. OVR superscript = statewide rank.")
    if updated:
        note += f" Ratings through {updated}."
    f = body(16)
    lines = wrap(d, note, f, s(760))[:3]
    y = y0 + FOOTER_H / 2 - (len(lines) - 1) * 23 / 2 + 6
    for i, line in enumerate(lines):
        d.text((s(PAD), s(y + i * 23)), line, font=f, fill=SUB, anchor="ls")
    d.text((s(W - PAD), s(y0 + 40)), SITE, font=cond(26, 700), fill=GOLD, anchor="rs")
    d.text((s(W - PAD), s(y0 + 66)), HANDLE, font=body(18), fill=SUB, anchor="rs")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Size-Adjusted Rankings graphic")
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    cfg = SPORTS[args.sport]

    src = f"per_capita_{args.sport}_{SEASON}.json"
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    teams = data["teams"][:TOP_N]
    if not teams:
        raise SystemExit(f"No teams in {src}")
    factor = float((data.get("method") or {}).get("private_factor") or 1.0)
    updated = ratings_date(data.get("ratings_last_updated"))
    max_poe = max(t["points_over_expected"] for t in teams)

    ensure_fonts()
    brand = Image.open(BRAND_LOGO) if os.path.exists(BRAND_LOGO) else None
    if brand is None:
        print(f"  ({BRAND_LOGO} not found -- using text wordmark)")

    img = Image.new("RGB", (s(W), s(H)), WHITE)
    d = ImageDraw.Draw(img)
    draw_header(img, d, cfg["label"], brand, cfg.get("season_label", str(SEASON)))
    draw_table_head(d)
    for i, t in enumerate(teams):
        draw_row(img, d, i, t, cfg["decimals"], max_poe)
    draw_footer(d, factor, updated)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"size_adjusted_rankings_{args.sport}_{SEASON}.png")
    img.save(out, optimize=True)
    print(f"Saved {out} ({img.width}x{img.height}), top {len(teams)} from {src}")


if __name__ == "__main__":
    main()
