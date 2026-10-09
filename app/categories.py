"""Block categories, coloured with the Google Calendar event palette.

`gcal_color_id` is the Calendar API colorId, so pushed events keep the same colour.
"""

CATEGORIES: dict[str, dict] = {
    "revision": {"label": "Revision", "color": "#039BE5", "gcal_color_id": "7"},   # Peacock
    "school": {"label": "School", "color": "#3F51B5", "gcal_color_id": "9"},       # Blueberry
    "work": {"label": "Work", "color": "#F4511E", "gcal_color_id": "6"},           # Tangerine
    "fitness": {"label": "Fitness", "color": "#33B679", "gcal_color_id": "2"},     # Sage
    "life": {"label": "Life", "color": "#F6BF26", "gcal_color_id": "5"},           # Banana
    "play": {"label": "Play", "color": "#8E24AA", "gcal_color_id": "3"},           # Grape
    "travel": {"label": "Travel", "color": "#616161", "gcal_color_id": "8"},       # Graphite
}

# Todoist priority colours (p1 is the most urgent).
PRIORITY_COLORS = {1: "#D1453B", 2: "#EB8909", 3: "#246FE0", 4: "#808080"}


def category(key: str) -> dict:
    return CATEGORIES.get(key, CATEGORIES["life"])
