"""
Unit 4: time windows done properly.

Every log search the SIEM server runs has a window, and the window is never optional: an
unbounded query on a log cluster reads every index it can see. This module turns what a model
writes ("now-24h", "2026-10-09T02:00", "2026-10-09T04:00+02:00") into an exact UTC window,
refuses windows that are backwards, in the future, too wide or older than retention, and
picks the daily indexes that window actually needs.

The rules it enforces, each refused with an error that says what to do instead:
  - absolute times may carry an offset; times without one are read in the caller's `tz`
    (default UTC), and the answer always says how it read them;
  - relative times use the `now-24h` form most search tools teach (m, h, d);
  - a window has a maximum span that depends on the tool: aggregations are cheap enough to
    cover all of retention, a raw search is not;
  - logs older than retention don't exist, so a window that starts before it is refused.

    python examples/04/windows.py "now-2d" "now-1d"          # parse and explain one window
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RETENTION = timedelta(days=21)          # the estate keeps 21 daily indexes (env/seed)
_RELATIVE = re.compile(r"^now(?:\s*-\s*(\d+)\s*([mhd]))?$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


# Unit 9: round "now" down to this many seconds (0: exact). Within one step every caller's
# "now-7d" is the same window, so the same question is the same query: a result cache and
# OpenSearch's own request cache can answer it. The cost: the newest few seconds of logs wait
# for the next step.
ROUND_NOW = int(os.environ.get("MCP_ROUND_NOW", "0"))


def current_time() -> datetime:
    now = datetime.now(UTC)
    if ROUND_NOW > 0:
        now = datetime.fromtimestamp(now.timestamp() // ROUND_NOW * ROUND_NOW, UTC)
    return now


class WindowError(ValueError):
    """A window the caller must change. The message says how."""


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise WindowError(f"unknown time zone {tz!r}; use an IANA name such as UTC, "
                          "Europe/Paris or America/New_York.") from None


def parse_time(text: str, tz: str = "UTC", now: datetime | None = None) -> datetime:
    """One end of a window, as an aware UTC datetime."""
    now = now or current_time()
    value = str(text).strip()
    m = _RELATIVE.match(value.lower())
    if m:
        amount, unit = m.groups()
        return now - timedelta(**{_UNITS[unit]: int(amount)}) if amount else now
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WindowError(f"can't read the time {value!r}. Use ISO 8601 "
                          "(2026-10-09T02:00:00Z, or with an offset like +02:00), a date "
                          "(2026-10-09), or a relative time like now-24h.") from None
    if dt.tzinfo is None:                       # no offset: read it in the caller's zone
        dt = dt.replace(tzinfo=zone(tz))
    return dt.astimezone(UTC)


def span_text(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes % 1440 == 0 and minutes > 1440:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def iso(dt: datetime, tz: str = "UTC") -> str:
    """A time as the caller asked to see it, with its offset (Z for UTC)."""
    if tz == "UTC":
        return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return dt.astimezone(zone(tz)).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Window:
    start: datetime          # inclusive, UTC
    end: datetime            # exclusive, UTC
    tz: str                  # how the caller wants times shown

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    def iso(self, dt: datetime) -> str:
        return iso(dt, self.tz)

    def describe(self) -> dict:
        """Echoed in every answer, so the caller sees how its words were read."""
        out = {"start": self.iso(self.start), "end": self.iso(self.end),
               "span": span_text(self.span)}
        if self.tz != "UTC":
            out["tz"] = self.tz
            out["start_utc"], out["end_utc"] = iso(self.start), iso(self.end)
        return out

    def range_query(self, field: str = "@timestamp") -> dict:
        return {"range": {field: {"gte": self.start.isoformat(), "lt": self.end.isoformat(),
                                  "format": "strict_date_optional_time"}}}


def window(start: str, end: str = "now", *, tz: str = "UTC", max_span: timedelta,
           now: datetime | None = None, tool: str = "this tool",
           retention: timedelta | None = RETENTION) -> Window:
    """Parse and check a window. Raises WindowError with an instruction when it's unusable."""
    now = now or current_time()
    zone(tz)
    s, e = parse_time(start, tz, now), parse_time(end, tz, now)
    if e <= s:
        raise WindowError(f"the window ends ({e:%Y-%m-%dT%H:%M}Z) before it starts "
                          f"({s:%Y-%m-%dT%H:%M}Z); swap start and end.")
    if s > now:
        raise WindowError("the window starts in the future; logs only go up to now.")
    e = min(e, now)                                     # nothing exists after now
    if retention is not None:
        oldest = (now - retention).replace(hour=0, minute=0, second=0, microsecond=0)
        if e <= oldest:
            raise WindowError(f"logs are kept for {retention.days} days; the oldest is from "
                              f"{oldest:%Y-%m-%d}. Nothing before that exists.")
        if s < oldest:
            raise WindowError(f"the window starts before retention ({retention.days} days, "
                              f"oldest {oldest:%Y-%m-%dT%H:%M}Z). Start at "
                              f"now-{retention.days}d or later.")
    if e - s > max_span:
        raise WindowError(f"the window is {span_text(e - s)}; {tool} allows at most "
                          f"{span_text(max_span)}. Narrow it, for example to the spike an "
                          "event_histogram shows, or to the incident's suggested_window.")
    return Window(s, e, tz)


def daily_indexes(prefix: str, w: Window) -> list[str]:
    """The daily indexes (<prefix>-YYYY.MM.DD, UTC days) a window touches, oldest first.

    Searching `prefix-*` would also work, and give the same answer: OpenSearch would open
    every one of the 21 daily indexes and find nothing in 19 of them. Naming the days keeps
    the query to the shards that can hold an answer.
    """
    day = w.start.replace(hour=0, minute=0, second=0, microsecond=0)
    names = []
    while day < w.end:
        names.append(f"{prefix}-{day:%Y.%m.%d}")
        day += timedelta(days=1)
    return names


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 3:
        print(__doc__)
        return 2
    try:
        w = window(*argv[:2], tz=argv[2] if len(argv) > 2 else "UTC", max_span=RETENTION)
    except WindowError as e:
        print(f"refused: {e}")
        return 1
    print(w.describe())
    print("indexes:", ",".join(daily_indexes("logs-auth", w)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
