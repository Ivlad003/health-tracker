# 📊 Page: Dashboard
# Сторінка: Дашборд

## Overview | Огляд

**EN:** Main landing page showing daily summary of calories, activity, and key metrics.

**UA:** Головна сторінка з денним підсумком калорій, активності та ключових показників.

---

## Layout | Макет

```
┌─────────────────────────────────────┐
│           HEADER                     │
│  "Dashboard"           [Settings]    │
├─────────────────────────────────────┤
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     TODAY'S CALORIE BALANCE     │ │
│  │                                 │ │
│  │         -450 kcal               │ │
│  │      ════════════════           │ │
│  │  IN: 1,550    OUT: 2,000        │ │
│  │  ○○○○○○○○●●●                    │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌───────────┐  ┌───────────┐       │
│  │  🍎 FOOD  │  │  💪 WHOOP │       │
│  │           │  │           │       │
│  │  1,550    │  │  450 kcal │       │
│  │  kcal     │  │  burned   │       │
│  │           │  │           │       │
│  │  P: 85g   │  │  Strain:  │       │
│  │  F: 52g   │  │  12.5     │       │
│  │  C: 180g  │  │           │       │
│  └───────────┘  └───────────┘       │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     QUICK LOG                   │ │
│  │                                 │ │
│  │  [🎤 Voice]  [✏️ Manual]  [📷]  │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     RECENT ENTRIES              │ │
│  │  ─────────────────────────────  │ │
│  │  🥣 Oatmeal         08:30  156  │ │
│  │  🍌 Banana          08:30  107  │ │
│  │  🏃 Running         10:00  320  │ │
│  │  🥗 Salad           13:00  450  │ │
│  └─────────────────────────────────┘ │
│                                      │
├─────────────────────────────────────┤
│  [🏠]  [🍎]  [💪]  [📅]  [👤]      │
│   ●                                  │
└─────────────────────────────────────┘
```

---

## Components | Компоненти

### 1. Header
**EN:** Simple header with page title and settings icon.
**UA:** Простий хедер з назвою сторінки та іконкою налаштувань.

| Element | Specification |
|---------|---------------|
| Title | "Dashboard" / "Дашборд", H1, centered |
| Settings Icon | 24x24px, right aligned, tap → Profile |
| Background | Surface color or transparent |

### 2. Calorie Balance Card
**EN:** Hero card showing today's calorie deficit/surplus.
**UA:** Головна картка з денним балансом калорій.

| Element | Specification |
|---------|---------------|
| Card | Full width - 32px margins, 16px padding |
| Balance Number | 32px, Bold, Primary/Error color based on +/- |
| Progress Bar | Height 8px, rounded, shows IN vs Goal |
| IN/OUT Labels | 14px, Secondary text, below progress |
| Goal Indicator | Small dot on progress bar |

**States | Стани:**
- Deficit (negative): Green color, happy indicator
- Surplus (positive): Orange/Red color, warning indicator
- On target: Blue color, neutral indicator

### 3. Metric Cards Row
**EN:** Two cards showing Food and Activity summaries.
**UA:** Дві картки з підсумком їжі та активності.

**Food Card (Left):**
| Element | Specification |
|---------|---------------|
| Icon | 🍎 or custom, 24px |
| Title | "Food" / "Їжа", 14px, Secondary |
| Calories | 24px, Bold, Primary text |
| Macros | P/F/C, 12px, Secondary text |

**Activity Card (Right):**
| Element | Specification |
|---------|---------------|
| Icon | 💪 or WHOOP logo, 24px |
| Title | "Activity" / "Активність", 14px |
| Calories | 24px, Bold, Accent color |
| Strain | "Strain: X.X", 12px |

### 4. Quick Log Section
**EN:** Quick action buttons for logging.
**UA:** Швидкі кнопки для логування.

| Button | Icon | Action |
|--------|------|--------|
| Voice | 🎤 | Open voice recorder |
| Manual | ✏️ | Open text input |
| Camera | 📷 | Open barcode scanner |

**Button Style:**
- Size: 64x64px each
- Background: Surface with subtle shadow
- Icon: 24px, Primary color
- Label: 12px below icon

### 5. Recent Entries List
**EN:** Last 4-5 logged items (food + activities).
**UA:** Останні 4-5 записів (їжа + активності).

| Element | Specification |
|---------|---------------|
| Section Title | "Recent" / "Останні", H3 |
| List Item Height | 48px |
| Icon | Emoji or category icon, 24px |
| Name | 16px, Primary text, left |
| Time | 14px, Secondary text, right |
| Calories | 14px, medium weight, right |

**Item Types:**
- Food: 🥣🍎🥗🍖 icons, positive calories
- Activity: 🏃💪🚴 icons, negative calories (green)

### 6. Bottom Navigation
**EN:** Fixed bottom tab bar.
**UA:** Фіксована нижня панель навігації.

| Tab | Icon | Label |
|-----|------|-------|
| Dashboard | 🏠 | Home/Головна |
| Food | 🍎 | Food/Їжа |
| Activity | 💪 | Activity/Активність |
| History | 📅 | History/Історія |
| Profile | 👤 | Profile/Профіль |

---

## Interactions | Взаємодії

### Tap Actions
| Element | Action |
|---------|--------|
| Calorie Card | → History page with filter |
| Food Card | → Food Log page |
| Activity Card | → Activity page |
| Voice Button | → Open voice recorder modal |
| Manual Button | → Open text input modal |
| Camera Button | → Open camera for barcode |
| Recent Item | → Item detail view |

### Gestures
| Gesture | Action |
|---------|--------|
| Pull down | Refresh data |
| Swipe item left | Quick delete (with confirmation) |

### Animations
- Number counting animation for calories
- Progress bar fill animation on load
- Card entrance animation (fade + slide up)

---

## Empty State | Порожній стан

**EN:** Shown when no data for today.
**UA:** Показується коли немає даних за сьогодні.

```
┌─────────────────────────────────────┐
│                                      │
│           [Illustration]             │
│                                      │
│     "Start tracking your day!"       │
│     "Почніть відстежувати свій       │
│              день!"                  │
│                                      │
│     [ 🎤 Log your first meal ]       │
│                                      │
└─────────────────────────────────────┘
```

---

## Loading State | Стан завантаження

- Skeleton cards for Calorie Balance and Metrics
- Shimmer effect on skeletons
- Bottom nav visible immediately

---

## Responsive Notes | Адаптивність

- On larger screens, metric cards can be wider
- Maximum content width: 428px
- Cards should have equal height in row

---

## Accessibility | Доступність

- All interactive elements: min 44x44px
- Color contrast: WCAG AA minimum
- Screen reader labels for all icons
- Voice button: clear audio feedback
