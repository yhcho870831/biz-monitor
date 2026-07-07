from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, SiteKeyword


def list_enabled_site_keywords(session: Session) -> List[Tuple[str, str]]:
    stmt = (
        select(Site.code, SiteKeyword.keyword)
        .join(SiteKeyword, Site.id == SiteKeyword.site_id)
        .where(Site.enabled.is_(True))
        .where(SiteKeyword.enabled.is_(True))
        .order_by(Site.code.asc(), SiteKeyword.keyword.asc())
    )
    return list(session.execute(stmt).all())


def add_site_keyword(session: Session, site_code: str, keyword: str) -> tuple[bool, bool]:
    site = session.execute(select(Site).where(Site.code == site_code)).scalars().one_or_none()
    if site is None:
        raise ValueError("Unsupported site: %s" % site_code)

    normalized_keyword = keyword.strip()
    existing = session.execute(
        select(SiteKeyword).where(
            SiteKeyword.site_id == site.id,
            SiteKeyword.keyword == normalized_keyword,
        )
    ).scalars().one_or_none()

    if existing is not None:
        if not existing.enabled:
            existing.enabled = True
            session.commit()
            return False, True
        return False, False

    session.add(
        SiteKeyword(
            site_id=site.id,
            keyword=normalized_keyword,
            enabled=True,
            created_at=datetime.utcnow(),
        )
    )
    session.commit()
    return True, False


def sync_site_keywords(session: Session, site_code: str, keywords: List[str]) -> dict[str, int]:
    site = session.execute(select(Site).where(Site.code == site_code)).scalars().one_or_none()
    if site is None:
        raise ValueError("Unsupported site: %s" % site_code)

    desired = {keyword.strip() for keyword in keywords if keyword.strip()}
    existing_rows = session.execute(
        select(SiteKeyword).where(SiteKeyword.site_id == site.id)
    ).scalars().all()

    enabled = 0
    disabled = 0
    added = 0
    now = datetime.utcnow()

    existing_by_keyword = {row.keyword: row for row in existing_rows}
    for keyword in sorted(desired):
        row = existing_by_keyword.get(keyword)
        if row is None:
            session.add(
                SiteKeyword(
                    site_id=site.id,
                    keyword=keyword,
                    enabled=True,
                    created_at=now,
                )
            )
            added += 1
            enabled += 1
            continue
        if not row.enabled:
            row.enabled = True
            enabled += 1

    for row in existing_rows:
        if row.keyword in desired:
            continue
        if row.enabled:
            row.enabled = False
            disabled += 1

    session.commit()
    return {
        "desired": len(desired),
        "enabled": enabled,
        "disabled": disabled,
        "added": added,
    }


def disable_site_keyword(session: Session, site_code: str, keyword: str) -> tuple[bool, bool]:
    site = session.execute(select(Site).where(Site.code == site_code)).scalars().one_or_none()
    if site is None:
        raise ValueError("Unsupported site: %s" % site_code)

    normalized_keyword = keyword.strip()
    existing = session.execute(
        select(SiteKeyword).where(
            SiteKeyword.site_id == site.id,
            SiteKeyword.keyword == normalized_keyword,
        )
    ).scalars().one_or_none()

    if existing is None:
        return False, False
    if not existing.enabled:
        return True, False

    existing.enabled = False
    session.commit()
    return True, True
