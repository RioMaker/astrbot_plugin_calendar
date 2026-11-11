from datetime import date, timedelta

import pytest

from core.calendar import (
    CalendarService,
    date_info,
    grid_dates,
    holiday_info,
    year_month,
)
from core.store import CalendarStore, ValidationError


@pytest.mark.parametrize(
    "year,month,start",
    [(2024, 2, 0), (2026, 11, 0), (2026, 2, 6), (1901, 1, 0), (2099, 12, 6)],
)
def test_standard_grid_is_six_complete_weeks(year, month, start):
    days = grid_dates(year, month, start)
    assert len(days) == 42
    assert days[0].weekday() == start
    assert all(b - a == timedelta(days=1) for a, b in zip(days, days[1:]))
    assert sum(day.month == month for day in days) >= 28


def test_leap_day_and_lunar_festival():
    assert date_info(date(2024, 2, 29))["date"] == "2024-02-29"
    spring = date_info(date(2026, 2, 17))
    assert spring["lunar"] == "正月初一"
    assert "春节" in spring["festivals"]
    assert spring["ganzhi"]["year"] == {
        "text": "丙午",
        "gan": "丙",
        "zhi": "午",
        "gan_element": "火",
        "zhi_element": "火",
    }


def test_year_boundary_is_explicit_and_different_between_lichun_and_new_year():
    before_spring = date(2024, 2, 5)
    assert (
        date_info(before_spring, "spring_festival")["ganzhi"]["year"]["text"] == "癸卯"
    )
    assert date_info(before_spring, "lichun")["ganzhi"]["year"]["text"] == "甲辰"
    assert date_info(date(2024, 2, 3), "lichun")["ganzhi"]["year"]["text"] == "癸卯"


def test_official_2026_spring_schedule_distinguishes_makeup_work():
    # 国办发明电〔2025〕7号：2/15–23 放假，2/14、2/28 上班。
    assert all(
        holiday_info(date(2026, 2, day))["status"] == "holiday" for day in range(15, 24)
    )
    assert holiday_info(date(2026, 2, 14))["status"] == "workday"
    assert holiday_info(date(2026, 2, 28))["status"] == "workday"
    assert holiday_info(date(2026, 2, 24))["status"] == "normal"
    assert holiday_info(date(2026, 3, 1))["status"] == "weekend"


def test_unknown_year_is_never_inferred_to_be_a_statutory_holiday():
    for day in range(1, 8):
        info = holiday_info(date(2099, 1, day))
        assert info["status"] == "unknown"
        assert info["known"] is False


def test_cached_facts_cannot_be_modified_by_callers():
    day = date(2026, 2, 17)
    info = date_info(day)
    info["festivals"].append("changed")
    assert "changed" not in date_info(day)["festivals"]


@pytest.mark.parametrize(
    "year,month",
    [
        (1900, 1),
        (2100, 1),
        (2026, 13),
        (2026, 0),
        (True, 1),
        (2026.5, 1),
        (2026, []),
        ("invalid", 1),
    ],
)
def test_invalid_months_fail_before_calendar_calculation(year, month):
    with pytest.raises(ValidationError):
        year_month(year, month)


def test_snapshot_includes_year_month_and_adjacent_date_annotations(tmp_path):
    store = CalendarStore(tmp_path / "calendar.sqlite3")
    for key in ["2026", "2026-11", "2026-10-26", "2026-12-06"]:
        store.mutate(
            {
                "revision": store.snapshot([])["revision"],
                "operation": "paint",
                "target": key,
                "pen_id": "income",
            }
        )
    snapshot = CalendarService(store).month(2026, 11)
    assert len(snapshot["days"]) == 42
    assert set(snapshot["items"]) == {"2026", "2026-11", "2026-10-26", "2026-12-06"}
    assert snapshot["holiday_data"]["supported"] is True
