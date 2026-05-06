import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  webpack: (config, { dev }) => {
    if (dev) {
      const currentIgnored = config.watchOptions?.ignored;
      const ignored = Array.isArray(currentIgnored)
        ? currentIgnored
        : currentIgnored
          ? [currentIgnored]
          : [];
      const nonEmptyIgnored = ignored.filter((item): item is string => typeof item === "string" && item.length > 0);

      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          ...nonEmptyIgnored,
          "**/.next/**",
          "**/node_modules/**",
          "**/.pytest_cache/**",
          path.join(__dirname, "../docs/harness/web/**")
        ]
      };
    }

    return config;
  }
};

export default nextConfig;
