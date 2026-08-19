from app.models.usage import WarningLevel

WARNING_THRESHOLD = 70.0
CRITICAL_THRESHOLD = 90.0


def usage_percentage(used: float | int | None, limit: float | int | None) -> float | None:
    if used is None or limit is None:
        return None
    if limit <= 0:
        return None
    return round((float(used) / float(limit)) * 100.0, 1)


def warning_level(percentage: float | None) -> WarningLevel:
    if percentage is None:
        return "unknown"
    if percentage >= CRITICAL_THRESHOLD:
        return "critical"
    if percentage >= WARNING_THRESHOLD:
        return "warning"
    return "normal"
