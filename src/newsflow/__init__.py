"""newsflow - topic routing for an online news aggregator.

Lab 1 is design only: this package is the placeholder that Lab 2 fills with the
ingestion, preprocessing and training code described in ``docs/design.md``.
"""

__version__ = "0.1.0"

# The serving taxonomy of section 5.2. The raw dataset carries 42 labels that
# overlap and change over time; the system serves this frozen list. Lab 2 moves
# this into a versioned ``taxonomy.yaml`` together with the raw-to-serving map.
SERVING_CATEGORIES: tuple[str, ...] = (
    "POLITICS",
    "WORLD NEWS",
    "BUSINESS & MONEY",
    "SCIENCE & TECH",
    "ENVIRONMENT",
    "HEALTH & WELLNESS",
    "FOOD & DRINK",
    "TRAVEL",
    "STYLE & BEAUTY",
    "HOME & LIVING",
    "FAMILY & RELATIONSHIPS",
    "ENTERTAINMENT",
    "ARTS & CULTURE",
    "SPORTS",
    "CRIME & JUSTICE",
    "EDUCATION",
    "SOCIETY & IDENTITY",
)
