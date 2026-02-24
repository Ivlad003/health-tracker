# 🎨 Design Specification: Telegram Web App
# Health & Wellness Tracker

## Overview | Огляд

This document provides design specifications for the Telegram Web App interface.
Цей документ містить дизайн-специфікації для інтерфейсу Telegram Web App.

---

## 📱 Design System

### Brand Colors | Кольори бренду

| Name | HEX | Usage |
|------|-----|-------|
| Primary | `#4CAF50` | Main actions, success states |
| Primary Dark | `#388E3C` | Pressed states |
| Secondary | `#FF9800` | Calories, warnings |
| Accent | `#2196F3` | WHOOP data, links |
| Background | `#F5F5F5` | Main background |
| Surface | `#FFFFFF` | Cards, inputs |
| Text Primary | `#212121` | Main text |
| Text Secondary | `#757575` | Secondary text |
| Error | `#F44336` | Errors, deficit |
| Success | `#4CAF50` | Positive balance |

### Typography | Типографіка

| Style | Font | Size | Weight | Usage |
|-------|------|------|--------|-------|
| H1 | System | 24px | Bold | Page titles |
| H2 | System | 20px | SemiBold | Section headers |
| H3 | System | 18px | Medium | Card titles |
| Body | System | 16px | Regular | Main content |
| Body Small | System | 14px | Regular | Secondary info |
| Caption | System | 12px | Regular | Labels, hints |
| Number Large | System | 32px | Bold | Key metrics |

### Spacing | Відступи

- `xs`: 4px
- `sm`: 8px
- `md`: 16px
- `lg`: 24px
- `xl`: 32px

### Border Radius | Радіус заокруглення

- Cards: 12px
- Buttons: 8px
- Inputs: 8px
- Pills: 20px

### Shadows | Тіні

```css
/* Card shadow */
box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);

/* Elevated shadow */
box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
```

---

## 📄 Pages | Сторінки

1. **[Dashboard](pages/01-dashboard.md)** - Main overview page
2. **[Food Log](pages/02-food-log.md)** - Food logging interface
3. **[Activity](pages/03-activity.md)** - WHOOP activity data
4. **[History](pages/04-history.md)** - Historical data view
5. **[Profile](pages/05-profile.md)** - User settings

---

## 🧩 Common Components | Спільні компоненти

### Navigation Bar
- Fixed bottom navigation
- 5 items: Dashboard, Food, Activity, History, Profile
- Active state: filled icon + primary color
- Inactive: outlined icon + secondary text color

### Header
- Telegram-native header usage
- Back button on sub-pages
- Title centered
- Optional action button (right)

### Cards
- White background
- 12px border radius
- 16px padding
- Subtle shadow

### Buttons

**Primary Button**
- Background: Primary color
- Text: White, 16px, Medium
- Height: 48px
- Full width or auto

**Secondary Button**
- Background: Transparent
- Border: 1px Primary color
- Text: Primary color

**Text Button**
- No background
- Primary color text
- Used for less important actions

### Input Fields
- Height: 48px
- Border: 1px #E0E0E0
- Focus border: Primary color
- Placeholder: Secondary text color
- Label above input

### Loading States
- Skeleton loaders for content
- Spinner for actions
- Pull-to-refresh indicator

---

## 📐 Layout Guidelines | Рекомендації з макету

### Safe Areas
- Respect Telegram Web App safe areas
- Use `var(--tg-viewport-height)` for full height

### Responsive
- Design for 375px width (iPhone SE)
- Scale up for larger devices
- Max content width: 428px (centered)

### Touch Targets
- Minimum 44x44px for interactive elements
- Adequate spacing between targets

---

## 🎭 States | Стани

### Empty States
- Illustration + text
- Clear call-to-action
- Friendly, encouraging tone

### Error States
- Red border/background
- Error icon
- Helpful error message
- Retry action when applicable

### Loading States
- Skeleton for content
- Disabled state for buttons
- Progress indicators for long operations

---

## 📁 File Structure | Структура файлів

```
docs/design/
├── README.md                 # This file
└── pages/
    ├── 01-dashboard.md       # Dashboard page spec
    ├── 02-food-log.md        # Food logging spec
    ├── 03-activity.md        # Activity page spec
    ├── 04-history.md         # History page spec
    └── 05-profile.md         # Profile page spec
```

---

## 🔗 Resources | Ресурси

- [Telegram Web App Guidelines](https://core.telegram.org/bots/webapps#design-guidelines)
- [Material Design 3](https://m3.material.io/)
- [Figma Template](#) (TODO: Add link)
