"""Offline monthly calendar image. All private annotations stay on the host."""

from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from .store import empty_item

FONT = (
    Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DroidSansFallback.ttf"
)


def month_image(snapshot, directory):
    width, margin, cell_w, cell_h, top = 1720, 34, 236, 230, 244
    height = top + 6 * cell_h + 92
    canvas = Image.new("RGB", (width, height), "#f6f3ed")
    draw = ImageDraw.Draw(canvas)
    fonts = {
        size: ImageFont.truetype(str(FONT), size) for size in (18, 20, 22, 26, 40, 48)
    }
    ink, muted, accent = "#26352f", "#718078", "#426c58"
    catalog = snapshot["catalog"]
    pens = {p["id"]: p for p in catalog["pens"]}

    def label(x, y, value, size=22, fill=ink, maximum=None):
        value = " ".join(str(value).split())
        font = fonts[size]
        if maximum and draw.textlength(value, font=font) > maximum:
            suffix = "…"
            # A bounded prefix avoids expensive shaping of very long comments.
            value = value[: max(1, int(maximum / size * 2))]
            while value and draw.textlength(value + suffix, font=font) > maximum:
                value = value[:-1]
            value += suffix
        draw.text((x, y), value, font=font, fill=fill, anchor="lt")

    def badge(x, y, mark):
        pen = pens[mark["pen_id"]]
        draw.rounded_rectangle((x, y, x + 54, y + 28), radius=7, fill="#eae9e3")
        x0, y0, x1, y1 = x + 6, y + 5, x + 24, y + 23
        color = pen["color"]
        if pen["shape"] == "square":
            draw.rectangle((x0, y0, x1, y1), outline=color, width=2)
        elif pen["shape"] in ("diamond", "triangle"):
            points = (
                [(x + 15, y0), (x1, y + 14), (x + 15, y1), (x0, y + 14)]
                if pen["shape"] == "diamond"
                else [(x + 15, y0), (x1, y1), (x0, y1)]
            )
            draw.line(points + points[:1], fill=color, width=2, joint="curve")
        else:
            draw.ellipse((x0, y0, x1, y1), outline=color, width=2)
        label(x + 32, y + 5, mark["layers"], 18, ink)

    year, month = snapshot["year"], snapshot["month"]
    label(margin, 28, f"{year} 年 {month:02d} 月", 48)
    label(780, 40, catalog["title"], 26, accent, maximum=900)
    for index, key in enumerate((str(year), f"{year:04d}-{month:02d}")):
        item = snapshot["items"].get(key, empty_item())
        x = margin + index * 810
        label(x, 96, "全年" if index == 0 else "本月", 20, muted)
        for n, mark in enumerate(item["marks"]):
            badge(x + 58 + n * 62, 91, mark)
        descriptions = [m["comment"] for m in item["marks"] if m.get("comment")] + [
            n["text"] for n in item["notes"]
        ]
        label(
            x,
            132,
            " / ".join(descriptions) or "日子有序，记忆有色。",
            20,
            muted,
            maximum=780,
        )
    label(
        margin, 180, "个人月历 · 公历 / 农历 / 日干支 · 标记在日期旁独立排列", 20, muted
    )
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    start = catalog["week_start"]
    for column in range(7):
        day_index = (column + start) % 7
        label(
            margin + column * cell_w + 94,
            top - 32,
            "周" + weekdays[day_index],
            22,
            "#bb654b" if day_index >= 5 else muted,
        )

    for i, day in enumerate(snapshot["days"]):
        x, y = margin + (i % 7) * cell_w, top + (i // 7) * cell_h
        draw.rectangle(
            (x, y, x + cell_w, y + cell_h),
            fill="#fffdfa" if day["in_month"] else "#efeee9",
            outline="#dfdfd7",
            width=1,
        )
        item = snapshot["items"].get(day["date"], empty_item())
        day_ink = ink if day["in_month"] else muted
        label(x + 16, y + 18, day["day"], 40, day_ink)
        if day["date"] == snapshot["today"]:
            draw.rounded_rectangle(
                (x + 16, y + 65, x + 56, y + 69), radius=2, fill=accent
            )
        # Two columns and two rows reserve space for all four marks.
        for n, mark in enumerate(item["marks"]):
            badge(x + 92 + (n % 2) * 60, y + 13 + (n // 2) * 33, mark)
        holiday = item["holiday"] or day["holiday"]
        status = {"holiday": "休", "workday": "班", "unknown": "待定"}.get(
            holiday["status"], ""
        )
        label(x + 16, y + 83, day["lunar_short"], 22, day_ink)
        if status:
            label(x + 155, y + 85, status, 20, "#a76b49")
        festivals = list(
            dict.fromkeys(
                ([day["solar_term"]] if day["solar_term"] else [])
                + day["festivals"]
                + item["festivals"]
            )
        )
        label(
            x + 16,
            y + 114,
            " / ".join(festivals) or day["lunar"],
            20,
            accent,
            maximum=cell_w - 32,
        )
        label(x + 16, y + 143, day["ganzhi"]["day"]["text"] + "日", 18, muted)
        notes = [
            (pens[m["pen_id"]]["color"], m["comment"])
            for m in item["marks"]
            if m.get("comment")
        ] + [(n["color"], n["text"]) for n in item["notes"]]
        for n, (note_color, content) in enumerate(notes[:2]):
            draw.rounded_rectangle(
                (x + 16, y + 171 + n * 26, x + 20, y + 189 + n * 26),
                radius=2,
                fill=note_color,
            )
            # Keep the more-count visible even when the text is truncated.
            reserve = 48 if n == 1 and len(notes) > 2 else 0
            label(
                x + 28,
                y + 170 + n * 26,
                content,
                18,
                day_ink,
                maximum=cell_w - 44 - reserve,
            )
            if reserve:
                label(x + cell_w - 52, y + 196, f"+{len(notes) - 2}", 18, muted)
    footer_y = top + 6 * cell_h + 22
    label(
        margin,
        footer_y,
        "标记图形 + 1–3 层数 · 完整注释与 ID 请用 /标记 查看",
        20,
        muted,
    )
    label(
        margin,
        footer_y + 30,
        "今天按北京时间（UTC+8）；日历图片展示注释摘要。"
        + (
            "该年份放假调休安排待确认。"
            if not snapshot["holiday_data"]["supported"]
            else ""
        ),
        18,
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
