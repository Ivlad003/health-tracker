# Critical Issues & Risk Mitigation

> **Version:** 1.0.0  
> **Updated:** 2026-01-28

This document describes critical issues that must be resolved before project launch, and existing open source solutions.

---

## 🔴 Critical Issues

### 1. OAuth Token Security

**Problem:** WHOOP tokens are stored as plain text in the database.

**Risk:** If DB is compromised — all user accounts are at risk.

**Solution:**
- Use `pgcrypto` for at-rest encryption
- Or application-level AES-256-GCM
- For production — HashiCorp Vault or AWS Secrets Manager

**Priority:** Required before production

---

### 2. WHOOP API — Requirements

**Problem:** 
- WHOOP device and active subscription required
- Realtime heart rate not available via API
- Rate limit: 100 requests/minute

**Solution:**
- Verify API access before development
- Have Plan B: manual workout entry or Apple Health integration

---

### 3. FatSecret — Ukrainian Food Support

**Problem:** FatSecret doesn't have official Ukrainian region support.

**Tests before starting:**
- Search: "борщ" (borscht), "вареники" (varenyky), "сирники" (syrnyky)
- Verify calorie/macro accuracy

**Solution:**
- Local Ukrainian food database as fallback
- Allow users to add custom foods
- Alternative: USDA FoodData Central (free)

---

### 4. n8n — Not for Production API

**Problem:** n8n is not designed for high-load API serving.

**Solution:** 
- Add Fastify/Hono API layer between Web App and n8n
- Use n8n only for background jobs

---

### 5. Calorie Balance — Incomplete Calculation

**Problem:** BMR (basal metabolic rate ~1500-2000 kcal/day) not included.

**Solution:** Add Mifflin-St Jeor formula for BMR calculation.

---

### 6. Telegram Mini App — WebView Limitations

**Problems:**
- Local storage unreliable
- iOS keyboard bugs
- Telegram may close app without warning

**Solutions:**
- Use Telegram CloudStorage API
- Test on real devices
- Graceful degradation for animations

---

## 📦 Existing Solutions

### WHOOP API

| Library | Language | Notes |
|---------|----------|-------|
| [whoopy](https://pypi.org/project/whoopy/) | Python | OAuth 2.0, async, Pandas |
| [hedgertronic/whoop](https://github.com/hedgertronic/whoop) | Python | Simple client |
| [kryoseu/whoops](https://github.com/kryoseu/whoops) | Flask | Export to PostgreSQL |

### FatSecret API

| Library | Language | Notes |
|---------|----------|-------|
| [pyfatsecret](https://pypi.org/project/fatsecret/) | Python | OAuth 1.0, all endpoints |
| [fatsecret](https://www.npmjs.com/package/fatsecret) | Node.js | Promise-based |

### Telegram Mini App

| Template | Stack | Notes |
|----------|-------|-------|
| [reactjs-template](https://github.com/Telegram-Mini-Apps/reactjs-template) | React + Vite | Official |
| [@telegram-apps/sdk-react](https://www.npmjs.com/package/@telegram-apps/sdk-react) | React | Pre-built hooks |

---

## 📋 Action Plan

| # | Task | Time | Priority |
|---|------|------|----------|
| 1 | Verify WHOOP API access | 1 day | 🔴 P0 |
| 2 | Test FatSecret with UA foods | 2 hours | 🔴 P0 |
| 3 | Token encryption | 1 day | 🔴 P0 |
| 4 | BMR in calorie balance | 4 hours | 🟠 P1 |
| 5 | Error handling voice flow | 1 day | 🟠 P1 |
| 6 | Fastify API layer | 2-3 days | 🟠 P1 |
| 7 | Local UA food database | 3-5 days | 🟡 P2 |

---

## References

- [WHOOP Developer Platform](https://developer.whoop.com/)
- [FatSecret Platform API](https://platform.fatsecret.com/)
- [Telegram Mini Apps Docs](https://core.telegram.org/bots/webapps)
- [USDA FoodData Central](https://fdc.nal.usda.gov/) — free alternative
