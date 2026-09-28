import { defineConfig } from "@playwright/test";
export default defineConfig({testDir:"./tests",use:{baseURL:process.env.PLAYWRIGHT_BASE_URL||"http://127.0.0.1:3000"},webServer:{command:"npm run dev -- --hostname 127.0.0.1",url:"http://127.0.0.1:3000",reuseExistingServer:!process.env.CI,timeout:120_000}});
