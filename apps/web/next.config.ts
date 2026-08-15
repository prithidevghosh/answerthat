import type { NextConfig } from 'next';

const config: NextConfig = {
  reactStrictMode: true,
  // Margin plates are pre-encoded AVIF/WebP at their display size (see
  // scripts/build-plates.md). Running them back through the image optimizer
  // buys nothing and would pull in a sharp binary at build time.
  images: { unoptimized: true },
};

export default config;
