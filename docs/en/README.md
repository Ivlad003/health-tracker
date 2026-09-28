# 📖 Documentation

[🇺🇦 Українська версія](../uk/README.md)

Welcome to Health & Wellness Tracker Bot documentation!

## 📚 Table of Contents

- [Getting Started](getting-started.md) - How to start using the bot
- [API Integration](api-integration.md) - Technical API documentation
- [Architecture](architecture.md) - System architecture description
- [⚠️ Critical Issues](critical-issues.md) - Risks and solutions
- [🍽 Food Logging](food-logging.md) - History-first matching, barcodes, label photos, FatSecret outbox, Web App API
- [📱 Telegram Web App](webapp.md) - Mini App frontend: auth/session recovery, screens, theming, caching, development, roadmap
- [Food History, Photos, Barcodes & Web App](plans/2026-09-26-food-history-photo-barcode.md) - Research, manual management, default products and admin implementation plan
- [Apple Health Shortcut Optimization](plans/2026-09-27-apple-health-shortcut-optimization.md) - Apple-source research, device benchmark procedure, prioritized plan, and empty URL setup

## 🎯 Quick Start

1. Find the bot in Telegram: `@HealthTrackerBot`
2. Press `/start` to begin
3. Connect WHOOP or Apple Health (optional)
4. Start logging food with voice messages!

## 🔗 Useful Links

- [Design Specifications](../design/)
- [GitHub Specs](../../.github/specs/)
- [Database](../../database/migrations/)

## ⚠️ Important Before Development

Before starting development, make sure to review [critical issues](critical-issues.md):
- OAuth token security
- WHOOP API requirements (device required)
- FatSecret limitations for Ukrainian foods
- Telegram Mini App WebView constraints
