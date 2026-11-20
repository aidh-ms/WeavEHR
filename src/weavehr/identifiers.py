"""Encounter identifiers shared by extraction, harmonisation and sharding.

These are MEDS extension columns following OMOP's visit hierarchy, not a
claim that every source is a complete OMOP database. Source expressions belong
to the table/event YAML: the same native column name can identify different
things in different tables. See ``docs/user_guide/identifiers.md``.
"""

from typing import Final

ENCOUNTER_ID_COLUMNS: Final = (
    "visit_occurrence_id",
    "visit_detail_id",
    "parent_visit_detail_id",
    "care_site_id",
)
"""Optional encounter identifiers; values are strings and null means unknown."""
