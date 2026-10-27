"""
South African public holidays, as a future covariate.

A public holiday is known for every future date, so - like the calendar
and solar-geometry features - it is a legal covariate at any lead time.
It matters for load specifically: consumption on a public holiday looks
like a Sunday even when it falls on a Tuesday, and nothing in the
calendar features tells the model that.

Coverage. The rules here follow the Public Holidays Act, 1994:

  * the twelve named holidays with fixed dates,
  * Good Friday and Family Day (Easter Monday), computed from Easter,
  * the "Monday rule": a holiday that falls on a Sunday is observed on
    the following Monday as well.

This has been checked against the 2002 calendar, which is the only year
the load record covers. 2002-specific notes:

  * Easter Sunday 2002 = 31 March, so Good Friday = 29 March and
    Family Day = 1 April.
  * Youth Day, 16 June 2002, is a Sunday -> Monday 17 June is observed.
  * Freedom Day, 27 April 2002, is a Saturday -> no extra day (the rule
    is Sunday-only).
  * 2 August 2002 was declared a one-off national holiday by proclamation
    that year; it is not in the Act and is not included. Add it by hand
    if a future analysis needs it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, Set

import numpy as np
import pandas as pd

# Fixed-date holidays (month, day) under the Public Holidays Act, 1994.
_FIXED = {
    (1, 1): "New Year's Day",
    (3, 21): "Human Rights Day",
    (4, 27): "Freedom Day",
    (5, 1): "Workers' Day",
    (6, 16): "Youth Day",
    (8, 9): "National Women's Day",
    (9, 24): "Heritage Day",
    (12, 16): "Day of Reconciliation",
    (12, 25): "Christmas Day",
    (12, 26): "Day of Goodwill",
}


def easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays_for_year(year: int) -> Dict[date, str]:
    """Every observed public holiday in `year`, date -> name."""
    out: Dict[date, str] = {}
    for (month, day), name in _FIXED.items():
        out[date(year, month, day)] = name

    easter = easter_sunday(year)
    out[easter - timedelta(days=2)] = "Good Friday"
    out[easter + timedelta(days=1)] = "Family Day"

    # The Monday rule: a holiday on a Sunday is also observed the next day.
    for holiday_date, name in list(out.items()):
        if holiday_date.weekday() == 6:  # Sunday
            out.setdefault(holiday_date + timedelta(days=1), f"{name} (observed)")
    return out


def holiday_dates(years: Iterable[int]) -> Set[date]:
    dates: Set[date] = set()
    for year in years:
        dates.update(holidays_for_year(year))
    return dates


def is_holiday(index: pd.DatetimeIndex) -> np.ndarray:
    """1.0 on every timestamp that falls on a public holiday, else 0.0."""
    years = range(int(index.year.min()), int(index.year.max()) + 1)
    dates = holiday_dates(years)
    day = pd.DatetimeIndex(index.date)
    flag = np.isin(day.values, np.array(sorted(dates), dtype="datetime64[ns]"))
    return flag.astype(float)
