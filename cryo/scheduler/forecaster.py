from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import QueryLog


@dataclass(frozen=True)
class ForecastPoint:
    hour_start: datetime
    expected_queries: float


@dataclass(frozen=True)
class QueryLoadForecast:
    table_id: uuid.UUID
    generated_at: datetime
    horizon_hours: int
    points: tuple[ForecastPoint, ...]
    expected_total_queries: float


class WorkloadForecaster:
    """EWMA + daily/weekly seasonality over hourly query counts."""

    def __init__(self, *, alpha: float = 0.35) -> None:
        self._alpha = alpha
        self._settings = get_settings()

    def forecast(
        self,
        session: Session,
        table_id: uuid.UUID,
        horizon_hours: int,
    ) -> QueryLoadForecast:
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        window_start = now - timedelta(days=self._settings.forecast_window_days)

        rows = session.execute(
            select(
                func.date_trunc("hour", QueryLog.executed_at).label("hour_start"),
                func.count(QueryLog.id).label("query_count"),
            )
            .where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= window_start,
                QueryLog.executed_at < now,
            )
            .group_by(func.date_trunc("hour", QueryLog.executed_at))
            .order_by(func.date_trunc("hour", QueryLog.executed_at).asc())
        ).all()

        hourly: dict[datetime, float] = {}
        for hour_start, query_count in rows:
            if hour_start is None:
                continue
            ts = hour_start if hour_start.tzinfo else hour_start.replace(tzinfo=UTC)
            hourly[ts] = float(query_count or 0)

        series = []
        cursor = window_start
        while cursor < now:
            bucket = cursor.replace(minute=0, second=0, microsecond=0)
            series.append((bucket, hourly.get(bucket, 0.0)))
            cursor += timedelta(hours=1)

        ewma = 0.0
        for _hour, count in series:
            ewma = self._alpha * count + (1.0 - self._alpha) * ewma

        # Lightweight seasonality factors.
        by_hour: dict[int, list[float]] = {k: [] for k in range(24)}
        by_weekday_hour: dict[tuple[int, int], list[float]] = {}
        for hour, count in series:
            by_hour[hour.hour].append(count)
            by_weekday_hour.setdefault((hour.weekday(), hour.hour), []).append(count)

        points: list[ForecastPoint] = []
        total = 0.0
        for step in range(max(1, horizon_hours)):
            ts = now + timedelta(hours=step + 1)
            daily_avg = _avg(by_hour.get(ts.hour) or [ewma])
            weekly_avg = _avg(by_weekday_hour.get((ts.weekday(), ts.hour)) or [daily_avg])
            expected = max(0.0, (ewma * 0.55) + (daily_avg * 0.25) + (weekly_avg * 0.20))
            total += expected
            points.append(ForecastPoint(hour_start=ts, expected_queries=round(expected, 4)))

        return QueryLoadForecast(
            table_id=table_id,
            generated_at=now,
            horizon_hours=max(1, horizon_hours),
            points=tuple(points),
            expected_total_queries=round(total, 4),
        )


def _avg(values: list[float]) -> float:
    return float(sum(values) / max(len(values), 1))
