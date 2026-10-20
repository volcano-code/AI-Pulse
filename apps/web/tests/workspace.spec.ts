import { test, expect } from "@playwright/test";

// Native Chromium -> Next rewrite -> real FastAPI -> database/SQL worker.
// No page.route(), response mocking, fake model, SMTP or public network here.
test("replay briefing opens its stored Unicode evidence", async ({ page, request }, info) => {
  const brief = await (await request.get("/api/v1/briefs/latest")).json();
  expect(brief.data_mode).toBe("replay");
  expect(brief.usage.model_calls).toBe(0);
  expect(brief.selection_summary.source_count).toBe(3);
  await page.goto("/");
  await expect(page.getByText("REPLAY", { exact: true })).toBeVisible();
  await expect(page.getByTestId("brief-selection-summary")).toContainText("3 个来源");
  await expect(page.getByRole("button", { name: "↻ 采集来源" })).toBeDisabled();
  await page.getByRole("button", { name: "▧ 查看来源与证据 ↗" }).first().click();
  const dialog = page.getByRole("dialog");
  const item = brief.items[0];
  const snapshot = await (await request.get('/api/v1/snapshots/' + item.snapshot_id)).json();
  expect(Array.from(snapshot.text).slice(item.quote_start, item.quote_end).join("")).toBe(item.evidence_quote);
  await expect(dialog.locator("mark")).toHaveText(item.evidence_quote);
  await page.screenshot({ path: info.outputPath("evidence-desktop.png"), fullPage: true });
  await dialog.getByRole("button", { name: "关闭证据" }).click();
  await expect(dialog).not.toBeVisible();
});

test("SSE evidence search returns saved snippets without a model", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /证据检索/ }).click();
  await page.getByLabel("检索问题").fill("Agent 工作流");
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/ask/stream') && r.request().method() === 'POST');
  await page.getByRole("button", { name: "检索证据 ↗", exact: true }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(page.locator("blockquote").first()).toBeVisible();
});

test("research receipts and exact citations survive reload", async ({ page, request }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: /研究助手/ }).click();
  await page.getByLabel("研究问题", { exact: true }).fill("Agent 工作流");
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/investigations') && r.request().method() === 'POST');
  await page.getByRole("button", { name: "开始证据研究 ↗" }).click();
  const result = await (await response).json();
  expect(result.status).toBe("completed");
  expect(result.result.model_calls).toBe(0);
  expect(result.result.retrieval.effective_mode).toBe("lexical");
  expect(result.result.citations.length).toBeGreaterThan(0);
  for (const citation of result.result.citations) {
    const snapshot = await (await request.get('/api/v1/snapshots/' + citation.snapshot_id)).json();
    expect(Array.from(snapshot.text).slice(citation.quote_start, citation.quote_end).join("")).toBe(citation.quote);
  }
  await expect(page.getByTestId("retrieval-receipt")).toContainText("实际检索方式：词项检索");
  await page.screenshot({ path: info.outputPath("research-desktop.png"), fullPage: true });
  await page.reload();
  await page.getByRole("button", { name: /研究助手/ }).click();
  await page.getByTestId("investigation-history").filter({ hasText: "Agent 工作流" }).first().click();
  await expect(page.getByTestId("retrieval-receipt")).toContainText("词项检索");
  await expect(page.getByTestId("investigation-result").locator("blockquote").first()).toHaveText(result.result.citations[0].quote);
});

test("research abstains on an unknown question", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /研究助手/ }).click();
  await page.getByLabel("研究问题", { exact: true }).fill("qzxunmatchedkeyword");
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/investigations') && r.request().method() === 'POST');
  await page.getByRole("button", { name: "开始证据研究 ↗" }).click();
  const result = await (await response).json();
  expect(result.result.stop_reason).toBe("insufficient_evidence");
  expect(result.result.claims).toEqual([]);
  expect(result.result.citations).toEqual([]);
  await expect(page.getByTestId("investigation-result")).toContainText("不能据此给出结论");
});

test("preference switch -> real durable worker -> new brief, history unchanged", async ({ page, request }) => {
  const previous = await (await request.get("/api/v1/briefs/latest")).json();
  const prefs = await (await request.get("/api/v1/preferences")).json();
  await expect.poll(async () => (await (await request.get("/api/v1/automation")).json()).worker_online).toBe(true);
  try {
    await page.goto("/");
    await page.getByRole("button", { name: /偏好设置/ }).click();
    await page.getByLabel("均衡简报来源", { exact: true }).uncheck();
    await page.getByRole("button", { name: "保存偏好" }).click();
    await expect(page.getByRole("status")).toContainText("偏好已保存");
    await page.getByRole("navigation").getByRole("button", { name: /每日简报/ }).click();
    const started = page.waitForResponse(r => r.url().endsWith('/api/v1/runs') && r.request().method() === 'POST');
    await page.getByRole("button", { name: "生成简报 ↗", exact: true }).click();
    const { run_id } = await (await started).json();
    await expect.poll(async () => (await (await request.get('/api/v1/runs/' + run_id)).json()).status).toBe("succeeded");
    const run = await (await request.get('/api/v1/runs/' + run_id)).json();
    expect(run.engine).toBe("durable");
    await expect(page.getByTestId("brief-selection-summary")).toContainText("按相关性排序");
    const original = await (await request.get('/api/v1/briefs/' + previous.id)).json();
    expect(original.selection_summary).toEqual(previous.selection_summary);
    expect(original.items.map((x: { snapshot_id: string }) => x.snapshot_id)).toEqual(previous.items.map((x: { snapshot_id: string }) => x.snapshot_id));
  } finally {
    await request.put("/api/v1/preferences", { data: prefs });
  }
});

test("automation displays an online worker and local-file delivery limits", async ({ page, request }) => {
  await expect.poll(async () => (await (await request.get("/api/v1/automation")).json()).worker_online).toBe(true);
  await page.goto("/");
  await page.getByRole("button", { name: /每日自动化/ }).click();
  await expect(page.getByText("LOCAL FILE", { exact: true })).toBeVisible();
  await expect(page.getByText(/只保存本地/).first()).toBeVisible();
  expect(await (await request.get("/api/v1/deliveries")).json()).toEqual([]);
});

test("mobile viewport can navigate to research without horizontal overflow", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByTestId("brief-selection-summary")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.getByRole("button", { name: /研究助手/ }).click();
  await expect(page.getByLabel("研究问题", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath("research-mobile.png"), fullPage: true });
});
