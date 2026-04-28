"""Render rough mock previews of Favilla pages so we can iterate without rebuilding APK.

Reads colors from Android resource hex strings (hardcoded here to match colors.xml v2).
Outputs PNG at 360x780 (phone-ish).
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

# Colors (mirror values/colors.xml v2)
PAPER = "#FAF9F5"
PAPER_CREAM = "#FEFAF1"
PAPER_DIVIDER = "#E5E3DA"
INK = "#1F1E1B"
INK_STRONG = "#2A2933"
INK_MUTED = "#6B6862"
INK_FAINT = "#9A968F"
PLUM = "#6E5BA8"
PLUM_DEEP = "#4D3F7A"
PLUM_SOFT = "#A693D6"
PLUM_WASH = "#EDE6F7"
PLUM_MIST = "#F6F2FB"
ACCENT_HOME_HERO = "#D9CDF1"
ACCENT_STUDIO = "#B7A6D9"
ACCENT_STUDIO_CARD = "#E9DFEF"
ACCENT_WARM = "#EEAB9A"
ACCENT_CREAM = "#F5DED6"
ACCENT_LAVENDER = "#CABDBD"

W, H = 360, 780

def font(size, bold=False, italic=False):
    """Best-effort font loader (Windows). Falls back to default."""
    candidates = []
    if italic:
        candidates += ["georgiai.ttf", "timesi.ttf"]
    elif bold:
        candidates += ["seguisb.ttf", "arialbd.ttf"]
    else:
        candidates += ["segoeui.ttf", "arial.ttf"]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def rounded(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text(draw, xy, s, color, sz, bold=False, italic=False):
    draw.text(xy, s, fill=color, font=font(sz, bold=bold, italic=italic))


# ---------------- HOME ----------------
def render_home():
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # Greeting
    text(d, (20, 36), "Good morning,", PLUM_DEEP, 22, italic=True)
    text(d, (20, 64), "Favilla.", INK_STRONG, 26, bold=True)
    text(d, (20, 100), "Wed · Apr 28 · 12 thoughts", INK_FAINT, 11)
    text(d, (310, 70), "Edit", PLUM, 11)

    # Ambient mood strip (no illustration, just gradient-ish bands)
    y = 130
    for i, col in enumerate([PLUM_MIST, PLUM_WASH, ACCENT_HOME_HERO]):
        rounded(d, (20, y + i * 4, 340, y + i * 4 + 4), 2, col)

    # "Last thought" card (replaces hero)
    rounded(d, (20, 150, 340, 250), 22, PLUM_MIST)
    text(d, (36, 168), "💭 last thought", PLUM, 11)
    text(d, (36, 188), "“今天散步前先把那篇", INK_STRONG, 14, italic=True)
    text(d, (36, 208), " 草稿发出去。”", INK_STRONG, 14, italic=True)
    text(d, (36, 230), "1 hour ago · via Chat", INK_FAINT, 10)

    # Quick access title
    text(d, (20, 274), "Quick access", INK_STRONG, 16, bold=True)

    # Tiles 2x2
    tile_h = 96
    coords = [
        (20, 304, "Chat", "💬", PLUM_MIST),
        (184, 304, "Studio", "✦", ACCENT_CREAM),
        (20, 304 + tile_h + 12, "Walk", "🌿", ACCENT_CREAM),
        (184, 304 + tile_h + 12, "Dashboard", "◐", PLUM_MIST),
    ]
    for x, y, label, glyph, bg in coords:
        rounded(d, (x, y, x + 156, y + tile_h), 18, bg)
        text(d, (x + 16, y + 14), glyph, PLUM_DEEP, 22)
        text(d, (x + 16, y + tile_h - 30), label, INK_STRONG, 15, bold=True)

    # Add tile (dashed look — solid line, lighter)
    yy = 304 + 2 * (tile_h + 12)
    rounded(d, (20, yy, 340, yy + 64), 18, PAPER, outline=PLUM_SOFT, width=1)
    text(d, (W // 2 - 36, yy + 22), "+ Add tile", PLUM, 13)

    img.save(OUT / "home.png")
    return img


# ---------------- DASHBOARD ----------------
def render_dashboard():
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    text(d, (20, 32), "03", PLUM_SOFT, 13)
    text(d, (44, 28), "Dashboard", INK_STRONG, 26, bold=True)
    d.ellipse((300, 32, 332, 64), fill=PLUM_WASH)
    text(d, (20, 76), "Today", INK_FAINT, 11)
    text(d, (20, 92), "Apr 28, Wed", INK_STRONG, 16, bold=True)

    # Week strip
    y = 130
    for i, (l, n, e) in enumerate(zip("MTWTFSS",
                                      [22, 23, 24, 25, 26, 27, 28],
                                      ["🙂", "💪", "✨", "🌿", "☕", "🌙", "🎈"])):
        x = 20 + i * 46
        is_today = (i == 6)  # Wed=2 actually but mock
        if is_today:
            rounded(d, (x, y, x + 40, y + 70), 14, PLUM_MIST)
        text(d, (x + 10, y + 6), e, INK, 18)
        text(d, (x + 13, y + 32), str(n), PLUM if is_today else INK_STRONG, 12, bold=True)
        text(d, (x + 16, y + 50), l, PLUM if is_today else INK_FAINT, 10)

    # Encouragement banner
    y2 = 220
    rounded(d, (20, y2, 340, y2 + 64), 18, ACCENT_CREAM)
    text(d, (36, y2 + 14), "✨", INK, 18)
    text(d, (62, y2 + 14), "You're doing great today!", INK_STRONG, 13, bold=True)
    text(d, (62, y2 + 36), "Keep it up, Favilla.", INK_MUTED, 11)
    text(d, (308, y2 + 14), "🎉", INK, 18)

    # Focus + Streak
    y3 = 300
    rounded(d, (20, y3, 176, y3 + 110), 18, PLUM_MIST)
    text(d, (36, y3 + 14), "Focus Time", PLUM_DEEP, 11)
    text(d, (36, y3 + 36), "3h 24m", INK_STRONG, 22, bold=True)
    text(d, (36, y3 + 78), "↑ 12% from yesterday", PLUM, 10)

    rounded(d, (184, y3, 340, y3 + 110), 18, ACCENT_CREAM)
    text(d, (200, y3 + 14), "Streak", INK_MUTED, 11)
    text(d, (200, y3 + 36), "7 days", INK_STRONG, 22, bold=True)
    text(d, (200, y3 + 78), "🔥 keep it up", INK_MUTED, 10)

    # Health Overview
    y4 = 426
    rounded(d, (20, y4, 340, y4 + 150), 20, "#FFFFFF", outline=PAPER_DIVIDER, width=1)
    # ring
    d.ellipse((40, y4 + 22, 124, y4 + 106), fill=PLUM_WASH)
    d.ellipse((46, y4 + 28, 118, y4 + 100), fill="#FFFFFF")
    text(d, (60, y4 + 44), "87", PLUM_DEEP, 22, bold=True)
    text(d, (60, y4 + 72), "Good", PLUM, 10)
    text(d, (148, y4 + 22), "Health Overview", INK_STRONG, 14, bold=True)
    grid = [("❤ HR", "72 bpm"), ("○ O₂", "98%"), ("🌙 sleep", "7h 32m"), ("👣 steps", "6,215")]
    for i, (lab, val) in enumerate(grid):
        gx = 148 + (i % 2) * 90
        gy = y4 + 50 + (i // 2) * 32
        text(d, (gx, gy), lab, INK_FAINT, 9)
        text(d, (gx, gy + 12), val, INK_STRONG, 11, bold=True)

    # Upcoming
    text(d, (20, y4 + 168), "Upcoming", INK_STRONG, 15, bold=True)
    y5 = y4 + 196
    rounded(d, (20, y5, 340, y5 + 90), 18, "#FFFFFF", outline=PAPER_DIVIDER, width=1)
    for i, (t, h) in enumerate([("Project Meeting", "10:00"), ("Reading Time", "15:30")]):
        ry = y5 + 8 + i * 42
        d.ellipse((34, ry + 8, 54, ry + 28), fill=PLUM_WASH)
        text(d, (66, ry + 6), t, INK_STRONG, 12, bold=True)
        text(d, (66, ry + 22), h, INK_MUTED, 10)
        text(d, (320, ry + 6), "›", INK_FAINT, 18)

    img.save(OUT / "dashboard.png")
    return img


# ---------------- STUDIO ----------------
def render_studio():
    img = Image.new("RGB", (W, H), PAPER_CREAM)
    d = ImageDraw.Draw(img)

    text(d, (22, 30), "studio", PLUM_DEEP, 32, bold=True)
    text(d, (130, 36), "✦", ACCENT_STUDIO, 22)
    text(d, (320, 36), "🔍", INK, 16)
    text(d, (22, 76), "Create, reflect, grow.", INK_MUTED, 12, italic=True)

    # Reading + window
    y = 116
    rounded(d, (22, y, 184, y + 140), 22, ACCENT_STUDIO_CARD)
    text(d, (38, y + 14), "Currently Reading", PLUM_DEEP, 10)
    text(d, (38, y + 32), "Sapiens", INK_STRONG, 18, bold=True)
    rounded(d, (38, y + 76, 168, y + 82), 3, PLUM_WASH)
    rounded(d, (38, y + 76, 38 + int(130 * 0.6), y + 82), 3, PLUM)
    text(d, (38, y + 92), "60%", PLUM, 11)

    # window circle
    d.ellipse((196, y, 336, y + 140), fill=ACCENT_WARM)
    text(d, (250, y + 50), "🪟", INK, 40)

    # Notes + Journal
    y2 = y + 154
    rounded(d, (22, y2, 178, y2 + 130), 22, ACCENT_CREAM)
    text(d, (38, y2 + 14), "📁", INK, 24)
    text(d, (38, y2 + 78), "Notes", INK_STRONG, 18, bold=True)
    text(d, (38, y2 + 100), "24 notes", INK_MUTED, 11)

    rounded(d, (190, y2, 340, y2 + 130), 22, ACCENT_LAVENDER)
    text(d, (206, y2 + 14), "🪶", INK, 24)
    text(d, (206, y2 + 78), "Journal", INK_STRONG, 18, bold=True)
    text(d, (206, y2 + 100), "Write freely", INK_MUTED, 11)

    # Plans + button
    y3 = y2 + 146
    rounded(d, (22, y3, 290, y3 + 110), 22, ACCENT_HOME_HERO)
    text(d, (38, y3 + 14), "🏔️", INK, 22)
    text(d, (38, y3 + 42), "Plans", PLUM_DEEP, 18, bold=True)
    text(d, (38, y3 + 64), "3 active · this week", PLUM_DEEP, 10)
    rounded(d, (38, y3 + 86, 274, y3 + 92), 3, "#FFFFFF")
    rounded(d, (38, y3 + 86, 38 + int(236 * 0.42), y3 + 92), 3, PLUM)
    d.ellipse((298, y3 + 28, 340, y3 + 70), fill=PLUM)
    text(d, (310, y3 + 30), "+", "#FFFFFF", 24, bold=True)

    # Quote + Diary
    y4 = y3 + 124
    rounded(d, (22, y4, 178, y4 + 100), 22, ACCENT_STUDIO_CARD)
    text(d, (38, y4 + 14), "🍃", INK, 14)
    text(d, (38, y4 + 38), "Every page is a", PLUM_DEEP, 11, italic=True)
    text(d, (38, y4 + 54), "new version of you.", PLUM_DEEP, 11, italic=True)

    rounded(d, (190, y4, 340, y4 + 100), 22, PLUM_DEEP)
    text(d, (206, y4 + 14), "📔", INK, 22)
    text(d, (206, y4 + 50), "Diary", "#FFFFFF", 16, bold=True)
    text(d, (206, y4 + 72), "Last entry · Yesterday", "#CCBBDD", 10)

    img.save(OUT / "studio.png")
    return img


def main():
    home = render_home()
    dash = render_dashboard()
    studio = render_studio()
    # Strip 3 pages side by side
    strip = Image.new("RGB", (W * 3 + 32, H), "#222")
    strip.paste(home, (8, 0))
    strip.paste(dash, (W + 16, 0))
    strip.paste(studio, (2 * W + 24, 0))
    strip.save(OUT / "all3.png")
    print(f"OK -> {OUT}")


if __name__ == "__main__":
    main()
