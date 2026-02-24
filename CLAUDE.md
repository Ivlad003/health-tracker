# CLAUDE.md - AI Assistant Instructions

## Project Overview

**Health & Wellness Tracker Bot** - Telegram Web App for tracking calories, physical activity, and mood with FatSecret and WHOOP API integration.

---

## 🌐 BILINGUAL DOCUMENTATION REQUIREMENTS

### ⚠️ CRITICAL: All documentation MUST be maintained in TWO languages

This project uses **bilingual documentation** (Ukrainian 🇺🇦 and English 🇬🇧).

### Rules for maintaining documentation:

1. **Every documentation file must exist in both languages:**
   - Ukrainian version: `docs/uk/filename.md`
   - English version: `docs/en/filename.md`

2. **When creating new documentation:**
   - ALWAYS create both language versions simultaneously
   - Use the same file structure in both `docs/uk/` and `docs/en/`
   - Keep content synchronized between versions

3. **When updating documentation:**
   - Update BOTH language versions
   - If you update `docs/uk/api.md`, you MUST also update `docs/en/api.md`
   - Mark sections as `[NEEDS_TRANSLATION]` if temporary async update is needed

4. **File naming convention:**
   - Use English file names for both versions
   - Example: `docs/uk/getting-started.md` and `docs/en/getting-started.md`

5. **README files:**
   - Root `README.md` - English (primary)
   - `README.uk.md` - Ukrainian version in root

6. **Code comments:**
   - Code comments should be in English
   - User-facing strings should support i18n

### Documentation structure:
```
docs/
├── uk/                    # 🇺🇦 Ukrainian documentation
│   ├── README.md
│   ├── getting-started.md
│   ├── api-integration.md
│   └── architecture.md
├── en/                    # 🇬🇧 English documentation
│   ├── README.md
│   ├── getting-started.md
│   ├── api-integration.md
│   └── architecture.md
└── design/               # Design specs (bilingual in single files)
    └── pages/
```

---

## 📁 Project Structure

```
health-tracker/
├── .github/
│   └── specs/            # GitHub Spec Kit specifications
├── docs/
│   ├── uk/               # Ukrainian docs
│   ├── en/               # English docs
│   └── design/           # Design specifications
│       └── pages/        # Page-by-page design specs
├── database/
│   └── migrations/       # SQL migrations
├── n8n/
│   └── workflows/        # n8n workflow JSON files
├── CLAUDE.md             # This file
├── README.md             # English README
└── README.uk.md          # Ukrainian README
```

---

## 🛠 Tech Stack

- **Bot Platform:** Telegram Web App
- **Automation:** n8n (self-hosted)
- **Database:** PostgreSQL 15+
- **APIs:**
  - FatSecret API (food calories)
  - WHOOP API (activity tracking)
  - OpenAI Whisper (speech-to-text)
- **Hosting:** Dokploy (Docker-based)

---

## 🔑 Key Commands

```bash
# Database migrations
psql -d healthlog -f database/migrations/001_initial_schema.sql

# Start n8n locally
docker-compose up -d n8n

# Run tests
npm test
```

---

## 📋 GitHub Spec Kit

Specifications are stored in `.github/specs/` directory following the GitHub Spec Kit format:

- `spec-overview.md` - Project overview
- `spec-api-*.md` - API specifications
- `spec-feature-*.md` - Feature specifications

---

## 🎨 Design Specifications

Design specs for Telegram Web App are in `docs/design/pages/`:
- Each page has its own specification file
- Specs are bilingual (UK/EN sections in same file)
- Include: layout, components, interactions, states

---

## ⚡ Quick Reference

| Resource | Location |
|----------|----------|
| PRD | `.github/specs/spec-overview.md` |
| API Docs | `docs/{uk,en}/api-integration.md` |
| DB Schema | `database/migrations/` |
| Design Specs | `docs/design/pages/` |
| n8n Workflows | `n8n/workflows/` |

---

## 🚨 Important Notes

1. **Always maintain bilingual docs** - This is mandatory
2. **Use GitHub Spec Kit format** for specifications
3. **Follow Telegram Web App guidelines** for UI/UX
4. **Keep sensitive data in .env** - Never commit secrets
