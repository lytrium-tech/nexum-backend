from enum import StrEnum


class GoalTransactionType(StrEnum):
    allocation = "allocation"
    release = "release"
    adjustment = "adjustment"
    legacy_import = "legacy_import"
