"""Block categories, coloured with the Google Calendar event palette.

`gcal_color_id` is the Calendar API colorId, so pushed events keep the same colour,
and existing calendar events are sorted into categories by their colour.
"""

CATEGORIES: dict[str, dict] = {
    "school": {"label": "School", "color": "#8E24AA", "gcal_color_id": "3"},       # Grape
    "revision": {"label": "Revision", "color": "#7986CB", "gcal_color_id": "1"},   # Lavender
    "work": {"label": "Work", "color": "#8E24AA", "gcal_color_id": "3"},           # Grape
    "fitness": {"label": "Exercise", "color": "#F4511E", "gcal_color_id": "6"},    # Tangerine
    "social": {"label": "Social", "color": "#F6BF26", "gcal_color_id": "5"},       # Banana
    "travel": {"label": "Travel", "color": "#0B8043", "gcal_color_id": "10"},      # Basil
    "life": {"label": "Life", "color": "#039BE5", "gcal_color_id": "7"},           # Peacock
}

# Older names that map onto a current category.
ALIASES = {"play": "social"}

# Google Calendar colorId to category, for events created outside this app.
# School and work share Grape, so Grape events count as school.
COLOR_TO_CATEGORY = {
    "1": "revision", "2": "travel", "3": "school", "4": "social", "5": "social",
    "6": "fitness", "7": "life", "9": "school", "10": "travel", "11": "fitness",
}

# Todoist priority colours (p1 is the most urgent).
PRIORITY_COLORS = {1: "#D1453B", 2: "#EB8909", 3: "#246FE0", 4: "#808080"}


def category(key: str) -> dict:
    return CATEGORIES.get(ALIASES.get(key, key), CATEGORIES["life"])
