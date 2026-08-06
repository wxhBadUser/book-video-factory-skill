"""Reusable foundation for the local book-video factory."""

from .project import PROJECT_DIRECTORIES, initialize_project
from .weread import WeReadClient, collect_book_source_pack
from .style_profiles import (
    DEFAULT_STYLE_PROFILE_ID,
    StyleProfile,
    StyleProfileError,
    available_style_profile_ids,
    load_style_profile,
)

__all__ = [
    "PROJECT_DIRECTORIES",
    "DEFAULT_STYLE_PROFILE_ID",
    "StyleProfile",
    "StyleProfileError",
    "WeReadClient",
    "available_style_profile_ids",
    "collect_book_source_pack",
    "initialize_project",
    "load_style_profile",
]
