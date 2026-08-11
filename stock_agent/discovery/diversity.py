from __future__ import annotations

from .schemas import CandidateFeatureSnapshot


def diversity_filter(candidates: list[CandidateFeatureSnapshot], max_same_sector: int = 2,
                     max_same_theme: int = 2, max_same_size_bucket: int | None = None,
                     sort_by: str = "composite_score") -> list[CandidateFeatureSnapshot]:
    sectors: dict[str, int] = {}
    themes: dict[str, int] = {}
    sizes: dict[str, int] = {}
    selected: list[CandidateFeatureSnapshot] = []
    for candidate in sorted(candidates, key=lambda item: (
            -(float(getattr(item, sort_by, 0.0) or 0.0) if sort_by != "composite_score" else item.composite_score),
            item.security.ticker)):
        sector = candidate.security.sector_canonical
        candidate_themes = candidate.security.themes or (candidate.security.industry_canonical,)
        if sectors.get(sector, 0) >= max_same_sector:
            candidate.risk_flags.append("DIVERSITY_SECTOR_LIMIT")
            continue
        if any(themes.get(theme, 0) >= max_same_theme for theme in candidate_themes):
            candidate.risk_flags.append("DIVERSITY_THEME_LIMIT")
            continue
        size = candidate.size_bucket or "UNKNOWN"
        if max_same_size_bucket is not None and sizes.get(size, 0) >= max_same_size_bucket:
            candidate.risk_flags.append("DIVERSITY_SIZE_BUCKET_LIMIT")
            continue
        selected.append(candidate)
        sectors[sector] = sectors.get(sector, 0) + 1
        for theme in candidate_themes:
            themes[theme] = themes.get(theme, 0) + 1
        sizes[size] = sizes.get(size, 0) + 1
    return selected
