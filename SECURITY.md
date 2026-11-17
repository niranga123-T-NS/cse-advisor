# Security and user access

## What is in place

| Area | Protection |
|---|---|
| Who can open the app | Email + password sign-in (Supabase Auth). Only people you add can sign in; public sign-up is switched off. |
| Every request checked | `functions/_middleware.js` checks the session token's signature (the project's public keys), issuer, audience and expiry on **every** request, renews it with the refresh token when it lapses, and otherwise sends the visitor to the sign-in page. It fails closed. |
| Sessions | Two HttpOnly, Secure, SameSite=Strict cookies: scripts in the page cannot read them and other sites cannot send them. Signing out ends the session at Supabase too. |
| Passwords | Kept only by Supabase (hashed); never stored in this app or the repository. Users can change theirs on the Account page (10+ characters). Supabase limits repeated wrong attempts. |
| Live prices | Read through `functions/live/` with a read-only GitHub token kept on Cloudflare; the browser never talks to GitHub. |
| Browser | Strict headers from `tools/security_headers.py`: scripts only from the site itself and its exact inline scripts (by hash), no inline event code, no framing, no referrer, HSTS. |
| Third-party code | The chart library is copied into `site/vendor/` after checking npm's sha512 hash; the site loads nothing from other servers. |
| Build system | Every GitHub Action pinned to a commit SHA; least-privilege permissions; inputs never pasted into shell commands; Python packages pinned; Dependabot weekly. |
| Android app | Trusted Web Activity around the signed-in site: HTTPS only, no backups, no data or keys inside the app, signed with a 4096-bit key stored only encrypted. |

## One-time setup

1. **Supabase** (free): supabase.com > New project (any name, region Singapore or Mumbai, strong database password).
   - Authentication > Sign In / Providers > Email: keep **Email** on; turn **off** "Allow new users to sign up".
   - Project Settings > API (or Data API / API Keys): copy the **Project URL** and the **publishable (anon) key**.
2. **Cloudflare** (free): dash.cloudflare.com. Compute > Workers & Pages: copy the **Account ID**.
   My Profile > API Tokens > Create Token > Custom: *Account > Cloudflare Pages > Edit*. Copy the token.
3. **GitHub secrets** (repository Settings > Secrets and variables > Actions): `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`,
   and for the Android app `ANDROID_KEY_PASSWORD` (24+ random characters; keep a copy).
4. Run **Publish site** (Actions tab). It creates the Cloudflare project `cse-advisor` and deploys it to `cse-advisor.pages.dev`.
5. **Cloudflare variables** (Workers & Pages > cse-advisor > Settings > Variables and secrets, Production, type Secret):
   `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `GH_READ_TOKEN` (GitHub fine-grained token: only this repository, Contents read-only, 1 year).
   Run **Publish site** again. https://cse-advisor.pages.dev now shows the sign-in page.

## Adding, changing and removing users

- **Add**: Supabase > Authentication > Users > Add user > Create new user: email + password, tick **Auto Confirm User**.
  Give them the address and their password; they can change it on the Account page (More > Account).
- **Reset a password**: Authentication > Users > the user > set a new password (or send a reset email if email sending is set up).
- **Remove**: Authentication > Users > the user > Delete user. Their next page load fails once their current
  session token runs out (at most an hour; set Authentication > Sessions > JWT expiry lower for faster lock-out).
- Up to 50,000 monthly users on Supabase's free plan.

## Keeping it free with a private repository

GitHub bills Actions minutes on private repositories (2,000 free a month; the live job needs about 7,000), but jobs on
**your own runner cost nothing**. Use any always-on Linux machine: Oracle Cloud "Always Free" (an ARM VM free for good;
sign-up asks for a card for identity, nothing is charged on the Always Free shapes), an old laptop, or a Raspberry Pi.

1. Create the machine (Ubuntu 22.04 or 24.04).
2. GitHub > Settings > Actions > Runners > New self-hosted runner: copy the token.
3. On the machine: `bash tools/setup_runner.sh <token>` (copy the file over, or `curl` it while the repo is still public).
4. GitHub > Settings > Secrets and variables > Actions > Variables: `RUNNER` = `self-hosted`.
   Every workflow switches to your machine; delete the variable to go back to GitHub's machines.
5. Only then make the repository private. The Android build stays on GitHub's machines (about 10 minutes per build,
   well inside the free 2,000).

## Cost note (without your own runner)

Public repositories get unlimited GitHub Actions minutes; private ones get 2,000 a month free. The live 15-second job
alone runs about 5.5 hours each trading day (about 7,000 minutes a month), so a private repository needs paid minutes
(roughly US$40-60 a month at GitHub's current rate) or a cheaper runner. Cloudflare Pages and Functions and Supabase Auth stay free at this size.

## Also recommended

- Turn on two-factor sign-in for your GitHub and Cloudflare accounts.
- Rotate `GH_READ_TOKEN` and the Cloudflare API token yearly.
