// Health Tracker Landing Page - CodeSpeak Specification
// Static landing page for Health Tracker app with Privacy Policy

HealthTrackerLanding is a static website hosted on GitHub Pages with two pages:
- Main landing page (index.html) presenting the Health Tracker app
- Privacy Policy page (privacy.html) required for WHOOP API access

It is styled with Tailwind CSS and uses no JavaScript frameworks (vanilla JS only for animations).
The design is mobile-first, responsive, minimalist with dark/neutral tones and green accent (#10B981).
Fonts: Plus Jakarta Sans (headlines), Inter (body text) from Google Fonts CDN.

The main landing page has these sections:

1. Header with logo (heart-pulse icon + "Health Tracker" text), navigation links (Features, Privacy), and CTA button "Get Started".

2. Hero Section with:
   - Badge: "Powered by WHOOP API"
   - Headline: "Your Personal Health Command Center."
   - Subline: "Track calories, sync WHOOP recovery data, and optimize your training load. All through a simple Telegram bot."
   - Primary CTA: "Open Telegram Bot" linking to Telegram bot
   - Secondary CTA: "Learn More" scrolling to features
   - Trust line: "Free to use · WHOOP integration · Privacy-first"

3. Integrations bar showing partner logos: WHOOP, FatSecret, Telegram, PostgreSQL.

4. Features Section with 3 cards:
   - Smart Calorie Tracking: "Search and log meals with FatSecret food database. Track daily intake and expenditure with accurate nutritional data."
   - WHOOP Integration: "Auto-sync strain, recovery, sleep and workout data. See your recovery score, HRV, and resting heart rate at a glance."
   - Training Load Optimization: "AI-powered recommendations based on your WHOOP recovery data. Know when to push hard and when to rest."

5. How It Works Section with 3 steps:
   - Step 1: "Connect WHOOP" - "Authorize your WHOOP data via secure OAuth 2.0 connection"
   - Step 2: "Log Your Meals" - "Search and track food through our Telegram bot with FatSecret database"
   - Step 3: "Get Insights" - "Receive personalized training and nutrition recommendations"

6. Stats Section (green background) with metrics.

7. AI Recommendations Section highlighting personalized health insights.

8. FAQ Section with common questions about WHOOP integration, data privacy, and usage.

9. Final CTA Section encouraging users to get started.

10. Footer with brand info, product/company/legal link columns, and copyright "2026 Health Tracker".

The Privacy Policy page (privacy.html) includes:
- Header (same as landing page)
- Privacy Hero with title "Privacy Policy" and effective date
- Content sections covering:
  1. Information We Collect (WHOOP OAuth data, FatSecret data, Telegram user input, auto-collected data)
  2. How We Use Your Data
  3. Data Storage and Security (PostgreSQL, encrypted, OAuth tokens secured)
  4. Third-Party Integrations (WHOOP, FatSecret, Telegram with links to their policies)
  5. Your Rights and Controls (access, delete, revoke, export, GDPR compliance)
  6. Cookies and Analytics
  7. Changes to This Policy
  8. Contact information (privacy@healthtracker.app)
- Footer (same as landing page)
