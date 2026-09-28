import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    const upstream = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
    return [{ source: "/api/v1/:path*", destination: `${upstream}/api/v1/:path*` }];
  },
};
export default config;
