# 🍎 Page: Food Log
# Сторінка: Логування їжі

## Overview | Огляд

**EN:** Page for logging food intake via voice, text, or barcode scanning.

**UA:** Сторінка для логування їжі через голос, текст або сканування штрих-коду.

---

## Layout | Макет

```
┌─────────────────────────────────────┐
│           HEADER                     │
│  [←]      "Log Food"        [Save]   │
├─────────────────────────────────────┤
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     INPUT METHOD TABS           │ │
│  │  [🎤 Voice] [✏️ Text] [📷 Scan] │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     VOICE INPUT AREA            │ │
│  │                                 │ │
│  │         [    🎤    ]            │ │
│  │         Hold to record          │ │
│  │                                 │ │
│  │  "I had oatmeal with banana     │ │
│  │   for breakfast..."             │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     MEAL TYPE                   │ │
│  │                                 │ │
│  │  (●) Breakfast  ( ) Lunch       │ │
│  │  ( ) Dinner     ( ) Snack       │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     PARSED ITEMS                │ │
│  │  ─────────────────────────────  │ │
│  │  ☑️ Oatmeal        200g   156   │ │
│  │     [Edit serving]              │ │
│  │  ─────────────────────────────  │ │
│  │  ☑️ Banana         1 pc   107   │ │
│  │     [Edit serving]              │ │
│  │  ─────────────────────────────  │ │
│  │                                 │ │
│  │  Total: 263 kcal                │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │  [        Save Entry        ]   │ │
│  └─────────────────────────────────┘ │
│                                      │
├─────────────────────────────────────┤
│  [🏠]  [🍎]  [💪]  [📅]  [👤]      │
│          ●                           │
└─────────────────────────────────────┘
```

---

## Components | Компоненти

### 1. Header
| Element | Specification |
|---------|---------------|
| Back Button | ← icon, 24px, left |
| Title | "Log Food" / "Додати їжу", H1, centered |
| Save Button | Text or icon, right, Primary color |

### 2. Input Method Tabs
**EN:** Toggle between input methods.
**UA:** Перемикання між методами вводу.

| Tab | Icon | State |
|-----|------|-------|
| Voice | 🎤 | Default active |
| Text | ✏️ | Secondary |
| Scan | 📷 | Secondary |

**Style:**
- Pill-shaped segmented control
- Active: Primary background, white text
- Inactive: Transparent, Primary text
- Height: 40px

### 3. Voice Input Area

#### Recording State
```
┌─────────────────────────────────────┐
│                                      │
│      [████████████░░░░░░░░░]        │
│              0:03                    │
│                                      │
│         [    🔴    ]                 │
│         Recording...                 │
│                                      │
│         [Cancel]                     │
│                                      │
└─────────────────────────────────────┘
```

#### Idle State
| Element | Specification |
|---------|---------------|
| Mic Button | 64x64px, Primary color, circular |
| Hint Text | "Hold to record" / "Утримуйте для запису" |
| Background | Light gray area, dashed border |

#### Processing State
| Element | Specification |
|---------|---------------|
| Spinner | Animated, centered |
| Text | "Processing..." / "Обробка..." |

#### Result State
| Element | Specification |
|---------|---------------|
| Transcript | 14px, italic, gray background |
| Re-record Button | "Try again" / "Спробувати знову" |

### 4. Text Input Area (Alternative Tab)
```
┌─────────────────────────────────────┐
│  What did you eat?                   │
│  ┌─────────────────────────────────┐ │
│  │ I had oatmeal with banana...    │ │
│  │                                 │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
│  [ Analyze ]                         │
└─────────────────────────────────────┘
```

| Element | Specification |
|---------|---------------|
| Label | "What did you eat?" / "Що ви їли?" |
| Textarea | Multi-line, 120px min height |
| Analyze Button | Secondary style, triggers AI parsing |

### 5. Barcode Scanner (Alternative Tab)
```
┌─────────────────────────────────────┐
│                                      │
│    ┌─────────────────────────────┐  │
│    │                             │  │
│    │      [ Camera View ]        │  │
│    │                             │  │
│    │    ═══════════════════      │  │
│    │    Align barcode here       │  │
│    │                             │  │
│    └─────────────────────────────┘  │
│                                      │
│    Or enter manually: [_________]   │
│                                      │
└─────────────────────────────────────┘
```

### 6. Meal Type Selector
**EN:** Radio button group for meal type.
**UA:** Радіо-кнопки для типу прийому їжі.

| Option | Label EN | Label UA |
|--------|----------|----------|
| breakfast | Breakfast | Сніданок |
| lunch | Lunch | Обід |
| dinner | Dinner | Вечеря |
| snack | Snack | Перекус |

**Style:**
- 2x2 grid layout
- Radio button + label
- Selected: Primary color border
- Auto-detect based on time (hint)

### 7. Parsed Items List
**EN:** Editable list of recognized food items.
**UA:** Редагований список розпізнаних продуктів.

| Element | Specification |
|---------|---------------|
| Checkbox | 24px, allows deselecting items |
| Food Name | 16px, Primary text |
| Serving | 14px, Secondary, editable |
| Calories | 16px, Bold, right aligned |
| Edit Link | "Edit" / "Змінити", text button |

**Item Actions:**
- Tap checkbox → toggle inclusion
- Tap "Edit" → open serving editor
- Swipe left → remove item

### 8. Serving Editor Modal
```
┌─────────────────────────────────────┐
│           Edit Serving               │
│  ─────────────────────────────────  │
│                                      │
│  Oatmeal                             │
│                                      │
│  Amount:  [  200  ] [▼ grams    ]   │
│                                      │
│  Per 100g:                           │
│  • Calories: 78 kcal                 │
│  • Protein: 2.5g                     │
│  • Fat: 1.5g                         │
│  • Carbs: 14g                        │
│                                      │
│  Your serving: 156 kcal              │
│                                      │
│  [Cancel]              [Apply]       │
└─────────────────────────────────────┘
```

### 9. Total & Save
| Element | Specification |
|---------|---------------|
| Total Row | "Total: XXX kcal", Bold, right |
| Save Button | Full width, Primary, 48px height |

---

## Interactions | Взаємодії

### Voice Recording Flow
1. User taps & holds mic button
2. Recording indicator appears
3. User releases → recording stops
4. Loading state while processing
5. Transcript appears
6. AI parses → items appear in list
7. User can edit/confirm
8. Save

### Text Input Flow
1. User types description
2. Taps "Analyze"
3. Loading state
4. Parsed items appear
5. User edits/confirms
6. Save

### Barcode Flow
1. Camera opens automatically
2. User aligns barcode
3. Auto-detect & lookup
4. Product appears in list
5. User confirms serving size
6. Save

---

## States | Стани

### Empty State
**EN:** Initial state before any input.
**UA:** Початковий стан до введення.

- Show input area prominently
- Helpful hint text
- Meal type pre-selected based on time

### Error States

**Voice Recognition Failed:**
```
"Couldn't understand the audio. Please try again."
"Не вдалося розпізнати аудіо. Спробуйте ще раз."
[Try Again]
```

**Food Not Found:**
```
"Couldn't find 'борщ' in database."
"Не знайдено 'борщ' в базі даних."
[Add manually] [Search alternatives]
```

**Network Error:**
```
"Connection error. Check your internet."
"Помилка з'єднання. Перевірте інтернет."
[Retry]
```

### Success State
After saving:
- Brief success message
- Return to Dashboard
- Entry appears in Recent list

---

## Validation | Валідація

| Field | Rule |
|-------|------|
| Items | At least 1 item selected |
| Serving | > 0 |
| Meal Type | Required |

---

## Accessibility | Доступність

- Voice input button: large target area (64px)
- Clear audio feedback during recording
- Error messages announced to screen readers
- High contrast for recording state
