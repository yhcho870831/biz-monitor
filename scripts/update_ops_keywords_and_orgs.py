from __future__ import annotations

import json
import sqlite3
from datetime import datetime


G2B_KEYWORDS = [
    "기상",
    "양식",
    "육상양식",
    "육상양식장",
    "국립수산과학원",
    "스마트양식",
    "양식장 자동화",
    "AI + 기상",
    "AI + 해양",
    "AI + 양식",
]

ORG_MATCHES = [
    "국립수산과학원",
    "기상청",
    "항공기상청",
]


def main() -> None:
    conn = sqlite3.connect("/app/data/app.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(sep=" ")

    site_id = cur.execute("SELECT id FROM sites WHERE code='g2b'").fetchone()[0]
    cur.execute("DELETE FROM site_keywords WHERE site_id=?", (site_id,))
    for keyword in G2B_KEYWORDS:
        cur.execute(
            "INSERT INTO site_keywords(site_id, keyword, enabled, created_at) VALUES(?,?,?,?)",
            (site_id, keyword, 1, now),
        )

    cur.execute("DELETE FROM company_projects WHERE category='organization-match'")
    for org in ORG_MATCHES:
        cur.execute(
            """
            INSERT INTO company_projects(
                project_name, organization, category, keywords_json, enabled, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                f"기관 매칭용:{org}",
                org,
                "organization-match",
                json.dumps([], ensure_ascii=False),
                1,
                now,
                now,
            ),
        )

    conn.commit()

    print("G2B KEYWORDS")
    for row in cur.execute(
        """
        SELECT sk.keyword
        FROM site_keywords sk
        JOIN sites s ON s.id = sk.site_id
        WHERE s.code='g2b' AND sk.enabled=1
        ORDER BY sk.keyword
        """
    ):
        print(row["keyword"])

    print("ORG MATCHES")
    for row in cur.execute(
        """
        SELECT organization
        FROM company_projects
        WHERE category='organization-match'
        ORDER BY organization
        """
    ):
        print(row["organization"])


if __name__ == "__main__":
    main()
