# Landing Page Status (www.nxentra.com)

Reviewed: 2026-04-16
**Rating: 8/10** (up from 6/10 on 2026-04-04)

## What's Working Well

- **Headline is sharp**: "Commerce accounting, automated" — leads with the pain, not the platform
- **Shopify-first positioning** — Shopify integration spotlight section + modules reordered (Shopify/Stripe first)
- **Technical credibility**: double-entry, event-sourced audit trail, multi-currency — appeals to the right buyer
- **Arabic toggle** — signals MENA focus, full bilingual (EN/AR) including all new sections
- **Demo credentials provided** — low friction to try
- **Pricing section** — 3 tiers (Starter $29, Growth $79, Pro $149) with feature checklists and free trial note
- **Security/trust section** — 6 badges (audit trail, tenant isolation, HTTPS, data export, RBAC, structured logging)
- **FAQ section** — 5 accordion questions covering migration, multi-currency, security, non-accountant use, post-trial
- **Social proof** — 2 testimonial cards (placeholder quotes — replace with real ones from beta users)
- **Email capture / waitlist** — simple email input above contact form, sends via Resend
- **Mobile hamburger menu** — full nav dropdown on mobile with animated toggle
- **SEO basics** — robots.txt, sitemap.xml, improved meta title/description/keywords, Twitter cards, canonical URL
- **Analytics ready** — Google Analytics via `NEXT_PUBLIC_GA_ID` env var (needs GA4 property created in Vercel)
- **Simplified "How it works"** — "Connect Shopify → We create journal entries → Reconcile payouts" (was technical "engine" language)

---

## Completed (since 2026-04-04)

- [x] **Add pricing page** — 3 tiers with feature lists, Growth highlighted as "Most popular"
- [x] **Add social proof** — 2 testimonial cards (placeholder quotes, replace with real beta feedback)
- [x] **Add security/compliance section** — 6 trust badges with accurate claims (no SOC 2 overclaim)
- [x] **Narrow the module messaging** — Shopify/Stripe first, Properties/Clinic removed from grid, stats updated to "6 modules"
- [x] **Add FAQ section** — 5 questions with accordion expand/collapse
- [x] **Fix "Watch demo" CTA** — YouTube video embedded (done prior to 2026-04-16)
- [x] **Improve contact form** — Role dropdown added (done prior to 2026-04-16)
- [x] **Simplify "How it works"** — 3-step merchant-friendly flow
- [x] **Add robots.txt + sitemap.xml** — SEO indexing enabled
- [x] **Add analytics support** — GA4 snippet ready, needs env var in Vercel
- [x] **Mobile hamburger menu** — nav was broken on mobile, now works
- [x] **Email capture / waitlist** — simple form, sends via existing Resend endpoint

---

## Remaining

### High Priority

- [ ] **Set up GA4 property** — Create in Google Analytics, add `NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX` to Vercel env vars. Analytics code is deployed but inactive without this.
- [ ] **Replace placeholder testimonials** — Current quotes are fictional. Replace with real beta user feedback when available.
- [ ] **Validate or remove 98.8% chart** — DashboardCharts shows 98.8% reconciliation rate. If not from real pilot data, remove the claim.

### Low Priority

- [ ] **Add dashboard screenshot caption** — "Real-time reconciliation dashboard showing order-to-payout matching"
- [ ] **Move Inter font to next/font/google** — Eliminates external stylesheet request, improves load speed
- [ ] **Custom OG image** — Social sharing card for links posted on Twitter/LinkedIn/Slack
- [ ] **Blog / content pages** — SEO long-tail: "How to reconcile Shopify payouts", "Shopify accounting for beginners"
