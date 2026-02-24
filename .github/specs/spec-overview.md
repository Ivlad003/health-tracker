# Spec: Project Overview
# Health & Wellness Tracker Bot

## Status: Draft
## Version: 0.1.0
## Last Updated: 2026-01-28

---

## 1. Summary

Health & Wellness Tracker Bot is a Telegram Web App that enables users to track their calorie intake and expenditure, physical activity from WHOOP devices, and daily mood/wellness through voice messages and manual input.

## 2. Goals

### Primary Goals
- Enable easy food logging via voice messages with AI transcription
- Provide accurate calorie tracking using FatSecret database
- Sync workout and recovery data from WHOOP devices
- Generate daily/weekly wellness reports

### Secondary Goals
- Mood and energy tracking
- Sleep quality correlation with performance
- Personalized insights and recommendations

## 3. Non-Goals

- Not a replacement for medical advice
- Not a social platform
- No direct integration with other fitness devices (initially)

## 4. Background

Users need a simple way to track their nutrition and fitness data without manual data entry. By combining voice input, AI processing, and API integrations, we can reduce friction significantly.

## 5. Technical Overview

### Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                   TELEGRAM WEB APP                               │
│                  (Frontend - React/Vue)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         n8n                                      │
│                 (Automation & Orchestration)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Telegram   │  │   OpenAI    │  │   HTTP      │              │
│  │   Trigger   │  │   Whisper   │  │   Requests  │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FatSecret  │ │    WHOOP    │ │  Database   │
    │     API     │ │     API     │ │ (PostgreSQL)│
    └─────────────┘ └─────────────┘ └─────────────┘
```

### Tech Stack
- **Frontend:** Telegram Web App (React + TypeScript)
- **Backend:** n8n workflows + PostgreSQL
- **APIs:** FatSecret, WHOOP, OpenAI
- **Hosting:** Dokploy (Docker)

## 6. User Stories

### US-001: Voice Food Logging
As a user, I want to log my meals by sending a voice message, so that I can track my food intake without typing.

### US-002: Automatic Calorie Calculation
As a user, I want the app to automatically calculate calories from my food description, so that I don't have to look up nutritional information.

### US-003: WHOOP Data Sync
As a WHOOP user, I want my workout and recovery data to automatically sync, so that I can see my complete health picture.

### US-004: Daily Summary
As a user, I want to receive a daily summary of my calorie balance, so that I can track my progress.

### US-005: Mood Tracking
As a user, I want to log my mood and energy levels, so that I can correlate them with my nutrition and activity.

## 7. API Integrations

### FatSecret API
- **Purpose:** Food database and nutritional information
- **Auth:** OAuth 2.0 (Client Credentials)
- **Endpoints:** `food.search`, `food/v5`, `food/barcode`

### WHOOP API
- **Purpose:** Workout, recovery, and sleep data
- **Auth:** OAuth 2.0 (Authorization Code)
- **Endpoints:** `/v2/workout`, `/v2/recovery`, `/v2/sleep`, `/v2/cycle`

### OpenAI API
- **Purpose:** Speech-to-text (Whisper) and text analysis (GPT)
- **Auth:** API Key

## 8. Data Models

See `spec-data-models.md` for detailed schema.

## 9. Milestones

### Phase 1: MVP (2-3 weeks)
- [ ] Basic Telegram bot setup
- [ ] Voice message processing with Whisper
- [ ] FatSecret API integration
- [ ] PostgreSQL database setup
- [ ] Basic food logging and retrieval

### Phase 2: WHOOP Integration (1-2 weeks)
- [ ] WHOOP OAuth implementation
- [ ] Workout data sync
- [ ] Recovery and sleep data sync
- [ ] Combined calorie dashboard

### Phase 3: Web App UI (2-3 weeks)
- [ ] Telegram Web App frontend
- [ ] Dashboard with charts
- [ ] History and search
- [ ] Settings management

### Phase 4: Advanced Features (ongoing)
- [ ] Mood and energy tracking
- [ ] Weekly/monthly reports
- [ ] Insights and recommendations
- [ ] Export functionality

## 10. Success Metrics

- Daily Active Users (DAU)
- Food entries per user per day
- Voice recognition accuracy
- User retention (7-day, 30-day)
- Response time < 5 seconds

## 11. Open Questions

1. Should we support multiple languages for voice input?
2. What's the fallback when FatSecret doesn't find a food item?
3. How do we handle WHOOP users vs non-WHOOP users?

## 12. References

- [FatSecret API Docs](https://platform.fatsecret.com/docs)
- [WHOOP API Docs](https://developer.whoop.com/docs)
- [Telegram Web App Docs](https://core.telegram.org/bots/webapps)
- [OpenAI Whisper](https://platform.openai.com/docs/guides/speech-to-text)
