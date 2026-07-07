from __future__ import annotations

import sqlite3
from datetime import datetime


SITE_KEYWORDS = {
    "g2b": [
        "기상",
        "기후",
        "기상정보",
        "기상자료",
        "기상관측",
        "기상센서",
        "수치예보",
        "수치예보모델",
        "수치예보시스템",
        "장기예보",
        "확률장기예보",
        "항공기상",
        "항공기상예보",
        "스마트항공예보",
        "해양기상",
        "도로기상",
        "상세기상",
        "기상관측부이",
        "기상레이더",
        "기상재해",
        "해양",
        "수산",
        "양식",
        "스마트양식",
        "육상양식",
        "육상양식장",
        "양식장 자동화",
        "아쿠아포닉스",
        "RAS",
        "어선",
        "어업",
        "해양안전",
        "선박",
        "해양환경",
        "해양관측",
        "공간정보",
        "GIS",
        "AI",
        "인공지능",
        "데이터",
        "빅데이터",
        "데이터 로거",
        "영상분석",
        "모니터링",
        "자동화",
        "통합관리",
        "유지관리",
        "유지보수",
        "국립수산과학원",
        "국립해양조사원",
        "해양환경공단",
        "기상청",
        "항공기상청",
    ],
    "kimst": [
        "해양",
        "수산",
        "양식",
        "스마트양식",
        "육상양식",
        "육상양식장",
        "양식장 자동화",
        "아쿠아포닉스",
        "RAS",
        "어선",
        "어업",
        "원양어선",
        "전자모니터링",
        "해양안전",
        "선박",
        "해양환경",
        "해양관측",
        "해양쓰레기",
        "수산자원",
        "해양기상",
        "AI",
        "인공지능",
        "데이터",
        "빅데이터",
        "공간정보",
        "GIS",
        "모니터링",
        "자동화",
        "플랫폼",
        "테스트베드",
    ],
    "nia": [
        "AI",
        "인공지능",
        "데이터",
        "빅데이터",
        "데이터 바우처",
        "데이터바우처",
        "데이터바우처지원사업",
        "AI가공",
        "공동활용",
        "공공데이터",
        "데이터 플랫폼",
        "데이터 품질",
        "데이터 구축",
        "데이터 개방",
        "마이데이터",
        "클라우드",
        "디지털서비스",
        "지능정보",
        "정보화전략",
        "ISP",
        "공간정보",
        "GIS",
        "기상데이터",
        "해양데이터",
        "수산데이터",
        "모니터링",
    ],
    "d2b": [
        "기상",
        "기상관측",
        "기상센서",
        "항공기상",
        "해양",
        "수산",
        "양식",
        "어선",
        "선박",
        "해양안전",
        "안전",
        "감시",
        "관측",
        "정찰",
        "AI",
        "인공지능",
        "데이터",
        "영상분석",
        "모니터링",
        "자동화",
        "공간정보",
        "GIS",
    ],
    "kmiti": [
        "기상",
        "기후",
        "기상정보",
        "기상자료",
        "기상관측",
        "기상센서",
        "기상관측자료",
        "수치예보",
        "수치예보모델",
        "수치예보시스템",
        "장기예보",
        "확률장기예보",
        "항공기상",
        "항공기상예보",
        "스마트항공예보",
        "해양기상",
        "도로기상",
        "상세기상",
        "기상관측부이",
        "기상레이더",
        "기상재해",
        "방재기상",
        "폭염",
        "한파",
        "태풍",
        "해무",
        "부이",
        "miniAWS",
        "AI",
        "인공지능",
        "데이터",
        "모니터링",
        "유지관리",
        "유지보수",
        "통합관리",
        "표준화",
    ],
    "iris": [
        "기상",
        "기후",
        "기상정보",
        "기상자료",
        "기상관측",
        "수치예보",
        "수치예보모델",
        "항공기상",
        "해양기상",
        "도로기상",
        "기상재해",
        "해양",
        "수산",
        "양식",
        "스마트양식",
        "육상양식",
        "아쿠아포닉스",
        "RAS",
        "어선",
        "어업",
        "해양안전",
        "선박",
        "해양환경",
        "해양관측",
        "AI",
        "인공지능",
        "데이터",
        "빅데이터",
        "공간정보",
        "GIS",
        "영상분석",
        "모니터링",
        "자동화",
        "플랫폼",
        "테스트베드",
        "디지털트윈",
    ],
}


def main() -> None:
    conn = sqlite3.connect("/app/data/app.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(sep=" ")

    for site_code, keywords in SITE_KEYWORDS.items():
        site = cur.execute("SELECT id FROM sites WHERE code=?", (site_code,)).fetchone()
        if site is None:
            print(f"skip missing site={site_code}")
            continue
        site_id = site["id"]
        for keyword in keywords:
            existing = cur.execute(
                "SELECT id FROM site_keywords WHERE site_id=? AND keyword=?",
                (site_id, keyword),
            ).fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO site_keywords(site_id, keyword, enabled, created_at)
                    VALUES(?,?,?,?)
                    """,
                    (site_id, keyword, 1, now),
                )
            else:
                cur.execute(
                    "UPDATE site_keywords SET enabled=1 WHERE id=?",
                    (existing["id"],),
                )

    conn.commit()

    for site_code in SITE_KEYWORDS:
        rows = cur.execute(
            """
            SELECT sk.keyword
            FROM site_keywords sk
            JOIN sites s ON s.id = sk.site_id
            WHERE s.code=? AND sk.enabled=1
            ORDER BY sk.keyword
            """,
            (site_code,),
        ).fetchall()
        print(f"{site_code}: {len(rows)}")
        print(", ".join(row["keyword"] for row in rows))


if __name__ == "__main__":
    main()
