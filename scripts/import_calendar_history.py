from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.bootstrap import create_schema
from app.config import load_settings
from app.db import create_db_engine, create_session_factory, ensure_sqlite_parent
from app.services.calendar import now_in_timezone
from app.services.history_import import (
    apply_history_import,
    preview_history_import,
    write_preview_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or import awarded history into calendar_saved_notices")
    parser.add_argument("--file", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selected-by", default="system_import")
    parser.add_argument("--batch-id", default=datetime.utcnow().strftime("history-%Y%m%d%H%M%S"))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    settings = load_settings()
    ensure_sqlite_parent(settings.database_url)
    engine = create_db_engine(settings.database_url)
    create_schema(engine)
    session_factory = create_session_factory(engine)

    input_path = Path(args.file).expanduser().resolve()
    now = now_in_timezone(settings.app_timezone)
    report = preview_history_import(input_path, now)

    if args.output:
        write_preview_report(report, Path(args.output).expanduser().resolve())

    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    with session_factory() as session:
        result = apply_history_import(
            session,
            input_path,
            now=now,
            selected_by=args.selected_by,
            import_batch_id=args.batch_id,
        )
    print(json.dumps({"preview": report, "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
