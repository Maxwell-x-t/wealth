import { test, expect } from "@playwright/test";

test("desktop and mobile journal preserve balances through buys, edits and reversals", async ({
  page,
}) => {
  test.setTimeout(60000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/strategy");
  await expect(page.getByTestId("cash")).toHaveText("10,000.00");
  await expect(page.locator(".n-spin-body")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/strategy-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "录入成交", exact: true }).click();
  await page
    .getByLabel("证券", { exact: true })
    .selectOption({ label: "600036 招商银行" });
  await page.getByLabel("数量（股／份）").fill("100");
  await page.getByLabel("成交价格（元）").fill("40");
  await page.getByLabel("税费合计（元）").fill("5");
  await page.getByRole("button", { name: "保存成交" }).click();
  await expect(page.getByTestId("cash")).toHaveText("5,995.00");
  await page.reload();
  await expect(page.getByTestId("cash")).toHaveText("5,995.00");
  await page.getByText("成交 (1)", { exact: true }).click();
  await page.getByRole("button", { name: "修改成交" }).click();
  await page.getByLabel("税费合计（元）").fill("6");
  await page.getByRole("button", { name: "保存成交" }).click();
  await expect(page.getByTestId("cash")).toHaveText("5,994.00");
  await page.getByRole("button", { name: "删除成交" }).click();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByTestId("cash")).toHaveText("10,000.00");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("持仓", { exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "招商银行 600036" }),
  ).toBeVisible();
  await expect(page.locator(".n-spin-body")).toHaveCount(0);
  await expect(page.locator(".n-message")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/strategy-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "资金记录", exact: true }).click();
  await page
    .getByRole("combobox", { name: "变动类型" })
    .selectOption("dividend");
  await page
    .getByRole("combobox", { name: "关联证券" })
    .selectOption({ label: "600036 招商银行" });
  await page.getByLabel("实际金额（元）").fill("100");
  await page.getByRole("button", { name: "保存记录" }).click();
  await expect(page.getByTestId("cash")).toHaveText("10,100.00");
  await page.getByRole("button", { name: "录入成交", exact: true }).click();
  await page
    .getByLabel("证券", { exact: true })
    .selectOption({ label: "600036 招商银行" });
  await page.getByLabel("数量（股／份）").fill("1000");
  await page.getByLabel("成交价格（元）").fill("40");
  await page.getByRole("button", { name: "保存成交" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "现金不足",
  );
  await expect(page.locator(".n-message")).toHaveCount(0);
  const dialog = await page.getByRole("dialog").boundingBox();
  expect(dialog.y).toBeGreaterThanOrEqual(0);
  expect(dialog.y + dialog.height).toBeLessThanOrEqual(844);
  await page.screenshot({
    path: "test-results/trade-mobile-validation.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByTestId("cash")).toHaveText("10,100.00");
  expect(errors).toEqual([]);
});
