import { test, expect } from "@playwright/test";
test("replay briefing opens its exact evidence",async({page})=>{
 await page.goto("/");await expect(page.getByText("REPLAY",{exact:true})).toBeVisible();
 await page.getByRole("button",{name:"▧ 查看来源与证据 ↗"}).first().click();
 const dialog=page.getByRole("dialog");await expect(dialog.getByText("存储文本",{exact:true})).toBeVisible();
 await expect(dialog.locator("mark")).not.toBeEmpty();
 await dialog.getByRole("button",{name:"关闭证据"}).click();await expect(dialog).not.toBeVisible();
});
test("local evidence search returns cited snippets",async({page})=>{
 await page.goto("/");await page.getByRole("button",{name:/证据检索/}).click();
 await page.getByLabel("检索问题").fill("Agent 工作流");await page.getByRole("button",{name:"检索证据 ↗",exact:true}).click();
 await expect(page.locator("blockquote").first()).toBeVisible();
});

test("automation makes local-mode limitations explicit",async({page})=>{
 await page.goto("/");await page.getByRole("button",{name:/每日自动化/}).click();
 await expect(page.getByText("LOCAL FILE",{exact:true})).toBeVisible();
 await expect(page.getByText(/只保存本地/).first()).toBeVisible();
});
