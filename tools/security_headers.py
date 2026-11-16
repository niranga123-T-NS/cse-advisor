"""Write site/_headers (Cloudflare Pages) with strict security headers.

The Content-Security-Policy allows scripts only from this site plus the exact inline scripts the pages contain
(by SHA-256 hash), so an injected <script> or event handler cannot run. Run after build_site.py, before deploying.
"""
import base64
import hashlib
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def inline_hashes() -> list[str]:
    out = set()
    for f in sorted(SITE.glob("*.html")):
        for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", f.read_text(), re.S):
            out.add("'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'")
    return sorted(out)


def main():
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'self' " + " ".join(inline_hashes()),
        "style-src 'self' 'unsafe-inline'",        # style="" attributes in generated markup; no script can run from CSS
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
    ])
    common = [
        f"Content-Security-Policy: {csp}",
        "Strict-Transport-Security: max-age=31536000; includeSubDomains",
        "X-Content-Type-Options: nosniff",
        "X-Frame-Options: DENY",
        "Referrer-Policy: no-referrer",
        "Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        "Cross-Origin-Opener-Policy: same-origin",
        "Cross-Origin-Resource-Policy: same-origin",
        "X-Robots-Tag: noindex, nofollow",
    ]
    lines = ["/*"] + ["  " + h for h in common]
    lines += ["", "/data/*", "  Cache-Control: private, no-cache", "", "/live/*", "  Cache-Control: no-store"]
    (SITE / "_headers").write_text("\n".join(lines) + "\n")
    print(f"site/_headers written ({len(inline_hashes())} inline scripts allowed by hash)")


if __name__ == "__main__":
    main()
