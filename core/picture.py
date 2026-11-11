"""Offline monthly calendar image. All private annotations stay on the host."""

from datetime import date
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from .store import empty_item

FONT = (
    Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DroidSansFallback.ttf"
)


def month_image(snapshot, directory):
    width, margin, cell_w, cell_h = 1720, 34, 236, 264
    catalog = snapshot["catalog"]
    pens = {p["id"]: p for p in catalog["pens"]}
    year, month = snapshot["year"], snapshot["month"]
    periods = [
        snapshot["items"].get(key, empty_item())
        for key in (str(year), f"{year:04d}-{month:02d}")
    ]
    has_period_notes = any(item["marks"] or item["notes"] for item in periods)
    # Keep the width fixed: larger type should stay larger when QQ fits the image
    # to the screen. Do not merely upscale the entire old layout.
    top = 188 + (92 if has_period_notes else 0)
    height = top + 6 * cell_h + 92
    canvas = Image.new("RGB", (width, height), "#f6f3ed")
    draw = ImageDraw.Draw(canvas)
    fonts = {
        size: ImageFont.truetype(str(FONT), size)
        for size in (22, 24, 26, 28, 30, 32, 64)
    }
    ink, muted, accent = "#26352f", "#5e6d63", "#315d40"
    today_fill, today_border = "#caddb9", "#3f714c"

    def label(x, y, value, size=28, fill=ink, maximum=None, *, bold=False):
        value = " ".join(str(value).split())
        font = fonts[size]
        stroke = 1 if bold else 0
        if maximum and draw.textlength(value, font=font) + 2 * stroke > maximum:
            suffix = "…"
            # A bounded prefix avoids expensive shaping of very long comments.
            value = value[: max(1, int(maximum / size * 2))]
            while (
                value
                and draw.textlength(value + suffix, font=font) + 2 * stroke > maximum
            ):
                value = value[:-1]
            value += suffix
        draw.text(
            (x, y),
            value,
            font=font,
            fill=fill,
            anchor="lt",
            stroke_width=stroke,
            stroke_fill=fill,
        )

    def badge(x, y, mark):
        pen = pens[mark["pen_id"]]
        draw.rounded_rectangle((x, y, x + 56, y + 36), radius=7, fill="#eae9e3")
        x0, y0, x1, y1 = x + 5, y + 7, x + 27, y + 29
        color = pen["color"]
        if pen["shape"] == "square":
            draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
        elif pen["shape"] in ("diamond", "triangle"):
            points = (
                [(x + 16, y0), (x1, y + 18), (x + 16, y1), (x0, y + 18)]
                if pen["shape"] == "diamond"
                else [(x + 16, y0), (x1, y1), (x0, y1)]
            )
            draw.line(points + points[:1], fill=color, width=3, joint="curve")
        else:
            draw.ellipse((x0, y0, x1, y1), outline=color, width=3)
        label(x + 34, y + 6, mark["layers"], 24, ink, bold=True)

    label(margin, 24, f"{year} 年 {month:02d} 月", 64, bold=True)
    label(margin, 104, catalog["title"], 28, accent, maximum=780)
    current = date.fromisoformat(snapshot["today"])
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    draw.rounded_rectangle((880, 24, width - margin, 88), radius=14, fill=today_fill)
    label(
        904,
        41,
        f"今天 {current.month} 月 {current.day} 日 · 周{weekdays[current.weekday()]}",
        32,
        accent,
        maximum=width - margin - 928,
        bold=True,
    )
    label(904, 104, "农历 / 日干支 · 标记独立排列", 26, muted)
    for index, item in enumerate(periods):
        if not item["marks"] and not item["notes"]:
            continue
        x = margin + index * 810
        label(x, 151, "全年" if index == 0 else "本月", 26, muted)
        for n, mark in enumerate(item["marks"]):
            badge(x + 74 + n * 64, 144, mark)
        descriptions = [m["comment"] for m in item["marks"] if m.get("comment")] + [
            n["text"] for n in item["notes"]
        ]
        label(
            x,
            194,
            " / ".join(descriptions),
            26,
            muted,
            maximum=780,
        )
    start = catalog["week_start"]
    for column in range(7):
        day_index = (column + start) % 7
        label(
            margin + column * cell_w + 86,
            top - 44,
            "周" + weekdays[day_index],
            32,
            "#a5503a" if day_index >= 5 else muted,
            bold=True,
        )

    today_box = None
    for i, day in enumerate(snapshot["days"]):
        x, y = margin + (i % 7) * cell_w, top + (i // 7) * cell_h
        is_today = day["date"] == snapshot["today"]
        draw.rectangle(
            (x, y, x + cell_w, y + cell_h),
            fill=today_fill
            if is_today
            else "#fffdfa"
            if day["in_month"]
            else "#efeee9",
            outline="#dfdfd7",
            width=1,
        )
        item = snapshot["items"].get(day["date"], empty_item())
        day_ink = ink if day["in_month"] or is_today else "#778174"
        label(
            x + 16, y + 14, day["day"], 64, accent if is_today else day_ink, bold=True
        )
        if is_today:
            today_box = (x + 2, y + 2, x + cell_w - 2, y + cell_h - 2)
            draw.rounded_rectangle(
                (x + 16, y + 74, x + 86, y + 102), radius=6, fill=accent
            )
            label(x + 27, y + 77, "今天", 22, "#ffffff", bold=True)
        # Two columns and two rows reserve space for all four marks.
        for n, mark in enumerate(item["marks"]):
            badge(x + 106 + (n % 2) * 62, y + 14 + (n // 2) * 44, mark)
        holiday = item["holiday"] or day["holiday"]
        status = {"holiday": "休", "workday": "班", "unknown": "待定"}.get(
            holiday["status"], ""
        )
        label(x + 16, y + 110, day["lunar_short"], 32, day_ink, maximum=132)
        label(x + 158, y + 112, day["ganzhi"]["day"]["text"], 30, muted)
        festivals = list(
            dict.fromkeys(
                ([day["solar_term"]] if day["solar_term"] else [])
                + day["festivals"]
                + item["festivals"]
            )
        )
        label(
            x + 16,
            y + 151,
            " / ".join(festivals),
            28,
            accent,
            maximum=132 if status else cell_w - 32,
        )
        if status:
            label(x + 164, y + 151, status, 28, "#925632", bold=True)
        notes = [
            (pens[m["pen_id"]]["color"], m["comment"])
            for m in item["marks"]
            if m.get("comment")
        ] + [(n["color"], n["text"]) for n in item["notes"]]
        for n, (note_color, content) in enumerate(notes[:2]):
            draw.rounded_rectangle(
                (x + 16, y + 190 + n * 34, x + 22, y + 215 + n * 34),
                radius=2,
                fill=note_color,
            )
            # Keep the more-count visible even when the text is truncated.
            reserve = 64 if n == 1 and len(notes) > 2 else 0
            label(
                x + 30,
                y + 188 + n * 34,
                content,
                28,
                day_ink,
                maximum=cell_w - 46 - reserve,
            )
            if reserve:
                label(x + cell_w - 72, y + 224, f"+{len(notes) - 2}", 26, muted)
    if today_box:
        # Draw after the grid so adjacent cells cannot overwrite this border.
        draw.rounded_rectangle(today_box, radius=8, outline=today_border, width=4)
    footer_y = top + 6 * cell_h + 22
    label(
        margin,
        footer_y,
        "标记旁数字为 1–3 层 · 完整注释与 ID 请用 /标记 查看",
        26,
        muted,
    )
    label(
        margin,
        footer_y + 36,
        "今天按北京时间（UTC+8）· 图片展示注释摘要"
        + (
            "该年份放假调休安排待确认。"
            if not snapshot["holiday_data"]["supported"]
            else ""
        ),
        24,
        muted,
    )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"month-{uuid4().hex}.png"
    try:
        canvas.save(path, format="PNG")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        canvas.close()
    return path
