from dividend_grid.notifiers.wecom import MAX_MARKDOWN_BYTES, WeComWebhookNotifier


def test_wecom_splits_long_markdown(monkeypatch):
    sent = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0}

    def post(url, *, json, timeout):
        sent.append(json["markdown"]["content"])
        return Response()

    monkeypatch.setattr("requests.post", post)
    content = "\n".join(f"第 {index} 行：红利低波 ETF 提醒" for index in range(500))

    WeComWebhookNotifier(webhook_url="https://example.test/webhook").send("测试提醒", content)

    assert len(sent) > 1
    assert all(len(message.encode("utf-8")) <= MAX_MARKDOWN_BYTES for message in sent)
    assert "第 0 行" in sent[0]
    assert "第 499 行" in sent[-1]
