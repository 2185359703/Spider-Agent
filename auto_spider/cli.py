from __future__ import annotations

import argparse

from auto_spider.db.session import init_db
from auto_spider.git.target import CollectorRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 招聘采集接入平台")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="创建开发数据库表")
    sub.add_parser("inspect-collector-repo", help="只读检查受管采集器仓库")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init-db":
        init_db()
        print("database initialized")
        return 0
    if args.command == "inspect-collector-repo":
        print(CollectorRepository().candidate_context())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
