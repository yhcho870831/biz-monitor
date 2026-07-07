from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CompanyProject


def list_active_projects(session: Session) -> List[CompanyProject]:
    stmt = select(CompanyProject).where(CompanyProject.enabled.is_(True))
    return list(session.execute(stmt).scalars().all())


def parse_project_keywords(project: CompanyProject) -> List[str]:
    if not project.keywords_json:
        return []
    try:
        data = json.loads(project.keywords_json)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    return []


def replace_projects(
    session: Session,
    projects: Iterable[dict],
) -> int:
    now = datetime.utcnow()
    session.query(CompanyProject).delete()

    count = 0
    for item in projects:
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        session.add(
            CompanyProject(
                project_name=str(item.get("project_name", "")).strip(),
                organization=str(item.get("organization", "")).strip() or None,
                category=str(item.get("category", "")).strip() or None,
                keywords_json=json.dumps(
                    [str(keyword).strip() for keyword in keywords if str(keyword).strip()],
                    ensure_ascii=False,
                ),
                enabled=bool(item.get("enabled", True)),
                created_at=now,
                updated_at=now,
            )
        )
        count += 1

    session.commit()
    return count
