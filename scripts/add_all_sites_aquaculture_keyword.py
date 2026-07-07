from __future__ import annotations

import sqlite3
from datetime import datetime


UPDATES = {
    "kimst": ["양식"],
    "kmiti": ["양식"],
    "d2b": ["양식"],
}


def main() -> None:
    conn = sqlite3.connect("/app/data/app.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(sep=" ")

    for site_code, keywords in UPDATES.items():
        site_id = cur.execute("SELECT id FROM sites WHERE code=?", (site_code,)).fetchone()[0]
        # remove broken placeholder rows from previous bad inserts
        cur.execute("DELETE FROM site_keywords WHERE site_id=? AND keyword='??'", (site_id,))
        for keyword in keywords:
            row = cur.execute(
                "SELECT id FROM site_keywords WHERE site_id=? AND keyword=?",
                (site_id, keyword),
            ).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO site_keywords(site_id, keyword, enabled, created_at) VALUES(?,?,?,?)",
                    (site_id, keyword, 1, now),
                )
            else:
                cur.execute("UPDATE site_keywords SET enabled=1 WHERE id=?", (row["id"],))

    conn.commit()

    for site_code in ["g2b", "nia", "kimst", "iris", "kmiti", "d2b"]:
        print(f"SITE {site_code}")
        site_id = cur.execute("SELECT id FROM sites WHERE code=?", (site_code,)).fetchone()[0]
        rows = cur.execute(
            "SELECT keyword FROM site_keywords WHERE site_id=? AND enabled=1 ORDER BY keyword",
            (site_id,),
        ).fetchall()
        for row in rows:
            print(row["keyword"])
        print("---")


if __name__ == "__main__":
    main()
