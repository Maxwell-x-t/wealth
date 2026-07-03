from datetime import date

from sqlalchemy.orm import Session

from app.models.models import Account, AppConfig, Instrument, InstrumentCategory


DEFAULT_CONFIG = {
    "nasdaq": "70",
    "sp500": "30",
    "a_share": "0",
    "gold": "0",
    "cash": "0",
    "qdii": "0",
    "mainland": "60",
    "hk": "40",
    "usd_cny_rate": "7.2",
    "plan_start_date": date.today().isoformat(),
    "building_first_month_amount": "100000",
    "building_monthly_amount": "50000",
    "building_months": "8",
    "dca_monthly_amount": "10000",
    "plan_horizon_years": "20",
    "mainland_nasdaq_code": "513100",
    "mainland_sp500_code": "513500",
    "hk_nasdaq_code": "QQQM",
    "hk_sp500_code": "VOO",
}


def get_config_map(db: Session) -> dict[str, str]:
    rows = db.query(AppConfig).all()
    config = dict(DEFAULT_CONFIG)
    for row in rows:
        config[row.key] = row.value
    return config


def ensure_default_config(db: Session) -> None:
    existing = {row.key for row in db.query(AppConfig).all()}
    added = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in existing:
            db.add(AppConfig(key=key, value=str(value)))
            added = True
    if added:
        db.commit()


def get_config_float(db: Session, key: str, default: float) -> float:
    config = get_config_map(db)
    return float(config.get(key, default))


def save_config(db: Session, data: dict) -> dict[str, str]:
    for key, value in data.items():
        row = db.query(AppConfig).filter(AppConfig.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(AppConfig(key=key, value=str(value)))
    db.commit()
    return get_config_map(db)


DEFAULT_INSTRUMENTS = [
    {"code": "513100", "name": "纳指ETF", "category": InstrumentCategory.NASDAQ.value, "account": "大陆", "currency": "CNY"},
    {"code": "513500", "name": "标普ETF", "category": InstrumentCategory.SP500.value, "account": "大陆", "currency": "CNY"},
    {"code": "563360", "name": "中证A500ETF", "category": InstrumentCategory.A_SHARE.value, "account": "大陆", "currency": "CNY"},
    {"code": "518880", "name": "黄金ETF", "category": InstrumentCategory.GOLD.value, "account": "大陆", "currency": "CNY"},
    {"code": "159696", "name": "恒生科技ETF", "category": InstrumentCategory.QDII.value, "account": "大陆", "currency": "CNY"},
    {"code": "QQQM", "name": "纳指100", "category": InstrumentCategory.NASDAQ.value, "account": "香港", "currency": "USD"},
    {"code": "VOO", "name": "标普500", "category": InstrumentCategory.SP500.value, "account": "香港", "currency": "USD"},
]


def _upsert_default_instruments(db: Session) -> None:
    accounts = {item.name: item for item in db.query(Account).all()}
    if not accounts:
        return

    for item in DEFAULT_INSTRUMENTS:
        account = accounts.get(item["account"])
        if not account:
            continue

        exists = (
            db.query(Instrument)
            .filter(Instrument.account_id == account.id, Instrument.code == item["code"])
            .first()
        )
        if exists:
            continue

        db.add(
            Instrument(
                code=item["code"],
                name=item["name"],
                category=item["category"],
                account_id=account.id,
                currency=item["currency"],
            )
        )

    db.commit()


def seed_database(db: Session) -> None:
    if db.query(Account).count() > 0:
        _upsert_default_instruments(db)
        return

    mainland = Account(name="大陆", currency="CNY")
    hk = Account(name="香港", currency="USD")
    db.add_all([mainland, hk])
    db.flush()

    accounts = {"大陆": mainland, "香港": hk}
    instruments = [
        Instrument(
            code=item["code"],
            name=item["name"],
            category=item["category"],
            account_id=accounts[item["account"]].id,
            currency=item["currency"],
        )
        for item in DEFAULT_INSTRUMENTS
    ]
    db.add_all(instruments)

    for key, value in DEFAULT_CONFIG.items():
        db.add(AppConfig(key=key, value=value))

    db.commit()
