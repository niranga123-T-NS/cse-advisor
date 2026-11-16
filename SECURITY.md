# Security and user access

## What is in place

| Area | Protection |
|---|---|
| Who can open the app | Cloudflare Access in front of the whole site: only invited email addresses get in (one-time code by email, or Google). |
| Second lock | `functions/_middleware.js` checks the Access sign-in token's signature, audience, issuer and expiry on **every** request and refuses anything else, so a mistake in the Access settings cannot expose the site. It fails closed. |
| Live prices | Read through `functions/live/` with a read-only GitHub token kept on Cloudflare; the browser never talks to GitHub. Only `t/`, `m/`, `b/` and top-level `.json` names are allowed. |
| Browser | Strict headers from `tools/security_headers.py`: scripts only from the site itself and its exact inline scripts (by hash), no framing, no referrer, HSTS, no camera/microphone/location. |
| Third-party code | The chart library is copied into `site/vendor/` after checking npm's sha512 hash; the site loads nothing from other servers. |
| Build system | Every GitHub Action pinned to a commit SHA; each workflow has only the permissions it needs; inputs never pasted into shell commands; Python packages pinned to exact versions; Dependabot proposes security updates weekly. |
| Android app | Trusted Web Activity: the signed-in site in Chrome's engine, HTTPS only, no backups, no data or keys inside the app, signed with a 4096-bit key that is stored only encrypted (AES-256, 600,000 PBKDF2 rounds). |

## One-time setup (about 20 minutes)

1. **Cloudflare account** (free): sign up at dash.cloudflare.com. Note your **Account ID** (Workers & Pages, right-hand side).
2. **API token**: My Profile > API Tokens > Create Token > Custom: permission *Account > Cloudflare Pages > Edit*. Copy it.
3. **GitHub secrets** (repository Settings > Secrets and variables > Actions > New secret):
   - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
   - `ANDROID_KEY_PASSWORD`: 24+ random characters from a password manager. Keep a copy: it protects the app's signing key.
4. Run **Publish site** (Actions tab). It creates the Cloudflare project `cse-advisor` and deploys it. The site answers "Sign-in is not set up yet" until step 6.
5. **Cloudflare Access** (Zero Trust in the dashboard; pick a team name, Free plan, up to 50 users):
   - Access > Applications > Add > Self-hosted: domain `cse-advisor.pages.dev` (add `*.cse-advisor.pages.dev` too). Session 24 hours.
   - Policy *Allow*: Include > Emails > the people you invite.
   - Second application, same domain, path `.well-known/assetlinks.json`, policy action *Bypass* > Everyone (Android needs to read this file).
   - Copy the first application's **Application Audience (AUD) tag**.
6. **Pages variables** (Workers & Pages > cse-advisor > Settings > Variables and secrets, Production, type *Secret*):
   - `ACCESS_TEAM_DOMAIN` = `yourteam.cloudflareaccess.com`
   - `ACCESS_AUD` = the AUD tag
   - `GH_READ_TOKEN` = a GitHub fine-grained token: *Only select repositories: cse-advisor*, *Contents: Read-only*, expiry 1 year.
   Then run **Publish site** again and open https://cse-advisor.pages.dev: you should get the sign-in page.
7. **Make the repository private** (Settings > General > Danger zone) once the Cloudflare site works.
   GitHub Pages then stops by itself. See the cost note below first.
8. **Android app**: Actions > Android app > Run workflow. Download *CSE-Advisor-android* from the run, send the APK to
   your users. They allow "install unknown apps" for their browser or file manager once. To update, run it again and send the new APK.

## Adding and removing people

Zero Trust > Access > Applications > cse-advisor > Policies > edit the email list. To throw someone out at once:
remove them and use *Revoke existing sessions* (Zero Trust > My Team > Users).

## Cost note

Public repositories get unlimited GitHub Actions minutes; private ones get 2,000 a month free. The live 15-second job
alone runs about 5.5 hours each trading day (about 7,000 minutes a month), so a private repository needs paid minutes
(roughly US$40-60 a month at GitHub's current rate) or a cheaper runner. Cloudflare Pages, Functions and Access stay
free at this size (up to 50 users).

## Also recommended

- Turn on two-factor sign-in for your GitHub and Cloudflare accounts.
- Rotate `GH_READ_TOKEN` and the Cloudflare API token yearly.
