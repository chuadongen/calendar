"""Block categories, coloured with the Google Calendar event palette.

Each category maps to one Google Calendar colour (chosen in Settings). Pushed
events use that colour, and events created elsewhere are sorted into categories
by their colour.
"""

# The 11 Google Calendar event colours: colorId -> (name, hex).
GCAL_COLORS: dict[str, tuple[str, str]] = {
    "11": ("Tomato", "#D50000"), "4": ("Flamingo", "#E67C73"), "6": ("Tangerine", "#F4511E"),
    "5": ("Banana", "#F6BF26"), "2": ("Sage", "#33B679"), "10": ("Basil", "#0B8043"),
    "7": ("Peacock", "#039BE5"), "9": ("Blueberry", "#3F51B5"), "1": ("Lavender", "#7986CB"),
    "3": ("Grape", "#8E24AA"), "8": ("Graphite", "#616161"),
}

# Category key -> (label, default colorId). Order matters: when two categories share
# a colour, events with that colour count as the first one.
DEFAULTS: dict[str, tuple[str, str]] = {
    "school": ("School", "3"),
    "revision": ("Revision", "1"),
    "work": ("Work", "3"),
    "fitness": ("Exercise", "6"),
    "social": ("Social", "5"),
    "travel": ("Travel", "10"),
    "life": ("Life", "7"),
}

# Older names that map onto a current category.
ALIASES = {"play": "social"}

# Filled by apply_colors(); mutated in place so every importer sees the change.
CATEGORIES: dict[str, dict] = {}
COLOR_TO_CATEGORY: dict[str, str] = {}

# Todoist priority colours (p1 is the most urgent).
PRIORITY_COLORS = {1: "#D1453B", 2: "#EB8909", 3: "#246FE0", 4: "#808080"}


def apply_colors(overrides: dict[str, str] | None = None) -> None:
    """Set each category's colour from {category: colorId}, falling back to the defaults."""
    overrides = overrides or {}
    CATEGORIES.clear()
    COLOR_TO_CATEGORY.clear()
    for key, (label, default_id) in DEFAULTS.items():
        color_id = overrides.get(key) if overrides.get(key) in GCAL_COLORS else default_id
        CATEGORIES[key] = {"label": label, "color": GCAL_COLORS[color_id][1], "gcal_color_id": color_id}
        COLOR_TO_CATEGORY.setdefault(color_id, key)


def category(key: str) -> dict:
    return CATEGORIES.get(ALIASES.get(key, key), CATEGORIES["life"])


apply_colors()
