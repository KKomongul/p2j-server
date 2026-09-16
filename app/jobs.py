"""배치를 손으로 한 번 돌리는 진입점.

    uv run python -m app.jobs                     # 전일 대상
    uv run python -m app.jobs --date 2026-09-15   # 특정 날짜 보정

서버가 내려가 있어 04:10 배치를 걸렀을 때, 또는 Railway 를 여러 인스턴스로 띄워
`BATCH_ENABLED=false` 로 두고 외부 cron 에서 부를 때 쓴다.
같은 날짜를 두 번 돌려도 결과가 같다 (services/batch.py 참고).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date

from app.db.session import dispose_engine, get_session_factory
from app.services.batch import run_daily


async def _main(target: date | None) -> int:
    async with get_session_factory()() as session:
        report = await run_daily(session, target)
    await dispose_engine()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 1 if report.failures else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="P2J 일일 배치")
    parser.add_argument("--date", help="판정 대상 날짜 (YYYY-MM-DD). 기본은 전일.")
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else None
    return asyncio.run(_main(target))


if __name__ == "__main__":
    raise SystemExit(main())
