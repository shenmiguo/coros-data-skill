"""Canonical sport labels shared by source adapters and upload matching."""

RUN_TYPE_KEYS = frozenset(
    {
        "indoor_running",
        "running",
        "treadmill_running",
        "track_running",
        "trail_running",
        "virtual_run",
    }
)

GARMIN_SPORT_MAP = {
    "bmx": "bike",
    "breathwork": "breathwork",
    "casual_walking": "walk",
    "cycling": "bike",
    "cyclocross": "bike",
    "downhill_biking": "bike",
    "e_bike_fitness": "bike",
    "e_bike_mountain": "bike",
    "elliptical_training": "cardio",
    "floor_climbing": "stair_climb",
    "gravel_cycling": "bike",
    "hiking": "hike",
    "indoor_cardio": "cardio",
    "indoor_cycling": "bike",
    "indoor_rowing": "row",
    "lap_swimming": "swim",
    "mountain_biking": "bike",
    "multi_sport": "multisport",
    "open_water_swimming": "swim",
    "other": "other",
    "pilates": "pilates",
    "road_biking": "bike",
    "rowing": "row",
    "skiing": "ski",
    "snowboarding": "snowboard",
    "speed_walking": "walk",
    "stair_climbing": "stair_climb",
    "stand_up_paddleboarding": "paddle",
    "strength_training": "strength",
    "swimming": "swim",
    "virtual_ride": "bike",
    "walking": "walk",
    "yoga": "yoga",
}


def classify_garmin(type_key):
    if type_key in RUN_TYPE_KEYS:
        return "run"
    return GARMIN_SPORT_MAP.get(type_key, f"other-{type_key or 'unknown'}")


def sports_compatible(left, right):
    """Allow generic Garmin/COROS labels to match a more specific sport."""
    if left == right:
        return True
    return (
        left in (None, "other")
        or right in (None, "other")
        or str(left).startswith("other-")
        or str(right).startswith("other-")
    )
