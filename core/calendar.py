"""Deterministic date facts; Gregorian dates and annotations remain separate."""

import copy
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from importlib.metadata import version

import chinese_calendar as holidays
from lunar_python import Solar

from .store import MAX_YEAR, MIN_YEAR, ValidationError

GAN_ELEMENTS = dict(zip("甲乙丙丁戊己庚辛壬癸", "木木火火土土金金水水"))
ZHI_ELEMENTS = dict(zip("子丑寅卯辰巳午未申酉戌亥", "水土木木土火火土金金土水"))
HOLIDAY_NAMES = {entry.value: entry.chinese for entry in holidays.Holiday}
HOLIDAY_YEARS = sorted({day.year for day in holidays.holidays})
HOLIDAY_VERSION = version("chinesecalendar")
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def year_month(year, month):
    # Reject floats, booleans and silent coercion such as 2026.2 -> 2026.
    if not isinstance(year, (int, str)) or isinstance(year, bool):
        raise ValidationError("年份无效")
    if not isinstance(month, (int, str)) or isinstance(month, bool):
        raise ValidationError("月份无效")
    try:
        year, month = int(year), int(month)
    except ValueError as exc:
        raise ValidationError("年月无效") from exc
    if not MIN_YEAR <= year <= MAX_YEAR or not 1 <= month <= 12:
        raise ValidationError(f"请选择 {MIN_YEAR}–{MAX_YEAR} 年的有效月份")
    return year, month


def grid_dates(year, month, week_start):
    first = date(year, month, 1)
    first -= timedelta(days=(first.weekday() - week_start) % 7)
    return [first + timedelta(days=i) for i in range(42)]


def pillar(ganzhi):
    return {
        "text": ganzhi,
        "gan": ganzhi[0],
        "zhi": ganzhi[1],
        "gan_element": GAN_ELEMENTS[ganzhi[0]],
        "zhi_element": ZHI_ELEMENTS[ganzhi[1]],
    }


def holiday_info(day):
    result = {
        "source": "chinesecalendar",
        "name": "",
        "known": day.year in HOLIDAY_YEARS,
    }
    if not result["known"]:
        result["status"] = "unknown"
    elif day in holidays.workdays:
        result.update(
            status="workday", name=HOLIDAY_NAMES.get(holidays.workdays[day], "调休上班")
        )
    elif day in holidays.holidays:
        result.update(
            status="holiday", name=HOLIDAY_NAMES.get(holidays.holidays[day], "节假日")
        )
    else:
        result["status"] = "weekend" if day.weekday() >= 5 else "normal"
    return result


@lru_cache(maxsize=4096)
def _date_info(day, boundary):
    solar = Solar.fromYmd(day.year, day.month, day.day)
    lunar = solar.getLunar()
    ganzhi_year = (
        lunar.getYearInGanZhiByLiChun()
        if boundary == "lichun"
        else lunar.getYearInGanZhi()
    )
    lunar_month = (
        ("闰" if lunar.getMonth() < 0 else "") + lunar.getMonthInChinese() + "月"
    )
    festivals = list(dict.fromkeys(solar.getFestivals() + lunar.getFestivals()))
    return {
        "date": day.isoformat(),
        "year": day.year,
        "month": day.month,
        "day": day.day,
        "weekday": day.weekday(),
        "weekday_label": "星期" + WEEKDAYS[day.weekday()],
        "lunar": lunar_month + lunar.getDayInChinese(),
        "lunar_short": lunar_month if lunar.getDay() == 1 else lunar.getDayInChinese(),
        "ganzhi": {
            "year": pillar(ganzhi_year),
            "month": pillar(lunar.getMonthInGanZhi()),
            "day": pillar(lunar.getDayInGanZhi()),
        },
        "solar_term": lunar.getJieQi(),
        "festivals": festivals,
        "holiday": holiday_info(day),
        "editable": MIN_YEAR <= day.year <= MAX_YEAR,
    }


def date_info(day, boundary="spring_festival"):
    return copy.deepcopy(_date_info(day, boundary))


class CalendarService:
    def __init__(self, store):
        self.store = store

    def month(self, year, month):
        year, month = year_month(year, month)
        # Query both possible grids in one consistent SQLite snapshot. Settings and
        # annotations therefore cannot come from different revisions during edits.
        candidates = grid_dates(year, month, 0) + grid_dates(year, month, 6)
        keys = [str(year), f"{year:04d}-{month:02d}"] + [
            d.isoformat() for d in candidates
        ]
        snapshot = self.store.snapshot(keys)
        catalog = snapshot["catalog"]
        days = [
            {**date_info(day, catalog["year_boundary"]), "in_month": day.month == month}
            for day in grid_dates(year, month, catalog["week_start"])
        ]
        return {
            **snapshot,
            "year": year,
            "month": month,
            "today": today().isoformat(),
            "days": days,
            "bounds": {"min": MIN_YEAR, "max": MAX_YEAR},
            "holiday_data": {
                "region": "中国大陆",
                "version": HOLIDAY_VERSION,
                "first_year": min(HOLIDAY_YEARS),
                "last_year": max(HOLIDAY_YEARS),
                "supported": year in HOLIDAY_YEARS,
                "source_url": "https://github.com/liriansu-opus/chinese-calendar",
            },
        }
