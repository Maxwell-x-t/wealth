# Wealth 与红利策略合并

`wealth` 为主工程，Vue 页面、FastAPI 接口和 SQLite 账本共用原有账户、证券与交易表。`strategy/` 收入 dividend-grid-reminder 当前工作区的策略代码、配置、回测脚本和测试；不包含通知凭据、个人账户文件和回测数据缓存。

## 页面

- `/strategy`：实际股数、现金、总资产、登记后收益、红利网格与 512890 RSI 信号。
- 录入成交：买入、卖出、成交税费、修改与删除；512890 需要填写成交后层数。
- 资金记录：转入、转出、实际现金分红、额外税费和送转股数调整。
- 原来的总览、交易列表、投资计划和回测入口继续保留。

交易是已发生的成交登记，不连接券商、不自动下单。策略仍使用现有 20% 现金底线和 20% ETF 配额；实际成交不会因为超过策略建议而拒绝登记，但现金不足或历史超卖会拒绝保存。

## 账户与收益

红利策略账户独立于原有大陆、香港账户，使用其自身现金与持仓计算资金限额。全部账户仍汇总到总览。原有账户中的买卖记录不改变其原有收益口径。

期初持仓不伪造买入交易。没有提供历史成本的持仓以登记日参考市值为收益起点，总览将这类成本列标为“期初市值”。策略账户的买卖只是现金与证券间转换，转入／转出才影响净投入；分红计入登记后收益。收益不代表登记日前的累计投资盈亏。

每次写入会在同一个 SQLite 写事务中，按日期与录入顺序从期初重放全部记录，同时更新余额快照。回填、修改和删除都执行相同校验。客户端生成请求编号，网络重试不会重复入账。

## 启动

Python 3.10+、Node 22.12+。项目根目录执行 `bash start.sh`。启动脚本选择空闲端口并输出策略页面地址。

独立部署可通过 `WEALTH_DATA_DIR` 指定数据库目录，通过 `WEALTH_API_URL` 配置 Vite 的后端代理。默认数据库为 `backend/data/wealth.db`，仅监听本机。`WEALTH_DISABLE_SYNC=1` 禁止后台行情同步，供测试或第二个本地实例使用。

## 导入与迁移

在 backend 目录运行：

```sh
.venv/bin/python -m app.import_strategy --account /absolute/path/account.json --prices /absolute/path/reference-prices.json --pointer ../strategy/account.json
```

`reference-prices.json` 是登记日的代码到价格映射，例如 `{"sh600036": 41.35}`。导入前备份原库；账户名重复或已有非零持仓与清单重叠时中止，要求先核对。导入不会把已有账户再复制一遍。

策略 CLI 的 `account.json` 可以引用同一账本：

```json
{"wealth_ledger": {"database": "/absolute/path/wealth.db", "account_id": 3}}
```

读取使用 SQLite 只读连接，不依赖网页服务在线；库不可读或账户不存在时停止计算，不回退到旧余额。旧提醒如需继续运行，应使用随本次合并更新的账户读取器和上述指针。数据库、账户指针、通知凭据均不得提交到 Git。

## 验证

```sh
cd backend
.venv/bin/python -m pytest tests -q
cd ../strategy
../backend/.venv/bin/python -m pytest tests -q
cd ../frontend
npm run build
npx playwright test
```

浏览器测试使用独立的 `/private/tmp/wealth-browser-e2e` 测试库与 Chrome，不操作真实账户。首次运行测试需在后端环境安装 pytest，并安装前端开发依赖。

原工程的 ECharts 5 / vue-echarts 7 依赖仍有 npm 报告的中等级别安全公告，需要后续单独验证主版本升级。本次没有强行升级整个图表框架。
