"""python -m app.import_strategy --account FILE --prices FILE [--name NAME]."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from app.database import DATA_DIR, SessionLocal
from app.services.ledger import begin_write
from app.services.strategy_account import import_opening


def main():
    parser = argparse.ArgumentParser(description="登记实际持仓为期初账户，不生成买入交易")
    parser.add_argument("--account", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True, help="登记日每股参考市价 JSON，代码须带交易所前缀")
    parser.add_argument("--name", default="红利策略")
    parser.add_argument("--pointer", type=Path, help="写出供策略提醒读取的账本指针")
    args = parser.parse_args()
    if args.pointer and args.pointer.exists():
        raise ValueError(f"指针文件已存在，未导入：{args.pointer}")
    database = DATA_DIR / "wealth.db"
    backup = DATA_DIR / f"wealth-before-strategy-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(backup) as target:
            source.backup(target)
    from app.main import app
    with SessionLocal() as db:
        begin_write(db)
        account_id = import_opening(db, json.loads(args.account.read_text()),
                                    json.loads(args.prices.read_text()), name=args.name)
        db.commit()
    if args.pointer:
        args.pointer.write_text(json.dumps({"wealth_ledger": {"database": str(database), "account_id": account_id}}, indent=2) + "\n")
    print(json.dumps({"account_id": account_id, "database": str(database), "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
