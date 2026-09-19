import httpx
import pytest

from src.audit.collectors.accessibility import collect_accessibility
from src.audit.collectors.links import collect_broken_links
from src.audit.collectors.performance import collect_performance
from src.audit.collectors.privacy import collect_privacy
from src.audit.collectors.security_headers import collect_security_headers
from src.audit.collectors.seo import collect_seo
from src.audit.collectors.tech_stack import collect_tech_stack
from src.audit.http_client import FetchResult
from src.report.schema import Status


def _facts(**overrides):
    """A plausible facts snapshot (modelled on tsech.online) that overrides can tweak."""
    base = {
        "url": "https://tsech.online/",
        "title": "tsech — build apps and gift cards with AI",
        "lang": "en",
        "metaDescription": "Describe the app you want and let AI build it. Create invites, cards and small web apps in minutes.",
        "robotsMeta": None,
        "canonical": "https://tsech.online/",
        "h1Texts": [], "h1Count": 0, "h2Count": 0, "h3Count": 0, "headingSkips": 0,
        "og": {"title": "tsech", "description": "Build apps with AI", "image": "https://tsech.online/og.png"},
        "twitterCard": None, "favicon": True, "hreflangCount": 0, "structuredData": [],
        "viewport": "width=device-width, initial-scale=1",
        "landmarks": 0, "skipLink": False,
        "totalImages": 1, "imagesMissingAlt": 0,
        "totalButtons": 10, "buttonsWithoutLabel": 1,
        "totalInputs": 1, "inputsWithoutLabel": 0,
        "totalLinks": 0, "linksWithoutText": 0,
        "positiveTabindex": 0, "iframesWithoutTitle": 0, "duplicateIds": 0,
        "links": [],
        "resources": [
            {"name": "https://tsech.online/assets/index-abc.js", "type": "script", "transferSize": 180000, "encodedBodySize": 500000, "durationMs": 120},
            {"name": "https://tsech.online/assets/index-abc.css", "type": "link", "transferSize": 12000, "encodedBodySize": 40000, "durationMs": 30},
        ],
        "timing": {"ttfbMs": 420, "domInteractiveMs": 900, "domContentLoadedMs": 950, "loadMs": 1200, "transferSize": 3000},
        "paint": {"first-paint": 800, "first-contentful-paint": 850},
        "headSyncScripts": 0, "stylesheetCount": 1, "mixedContent": [],
        "scriptSrcs": ["https://tsech.online/assets/index-abc.js"],
        "thirdPartyDomains": [],
        "frameworks": {"react": True, "next": False, "nuxt": False, "vue": False, "angular": False, "svelte": False, "jquery": None, "bootstrap": False, "wordpress": False},
        "generator": None,
        "cookies": [], "privacyLink": False, "termsLink": False, "cookieNotice": False,
        "hasPasswordField": True, "hasForms": 1,
        "bodyText": "Sign In One idea is all you need. AI will do the rest. Build",
        "wordCount": 14,
    }
    base.update(overrides)
    return base


def _fetch(status=200, headers=None, text="", url="https://tsech.online/"):
    return FetchResult(status_code=status, headers=headers or {}, text=text, url=url)


# ---- SEO ------------------------------------------------------------------------


def test_seo_flags_missing_h1_and_long_or_short_metadata():
    pillar = collect_seo(_facts(title="tsech", metaDescription="short"), robots=None, sitemap=None)
    checks = {f.check: f for f in pillar.findings}
    assert checks["Title tag"].status == Status.WARN and "5 characters" in checks["Title tag"].evidence
    assert checks["Meta description"].status == Status.WARN
    assert checks["H1 heading"].status == Status.BAD and checks["H1 heading"].fix
    assert checks["robots.txt"].status == Status.WARN
    assert checks["sitemap.xml"].status == Status.WARN
    assert checks["Structured data"].status == Status.WARN
    assert pillar.score < 7


def test_seo_noindex_is_critical_and_evidence_is_recorded():
    pillar = collect_seo(_facts(robotsMeta="noindex, nofollow"), robots=None, sitemap=None)
    finding = next(f for f in pillar.findings if f.check == "Indexability (robots meta)")
    assert finding.status == Status.BAD
    assert "noindex" in finding.evidence


def test_seo_good_page_scores_high():
    facts = _facts(h1Count=1, h1Texts=["Build apps with AI"], twitterCard="summary_large_image", structuredData=["WebSite"])
    robots = _fetch(text="User-agent: *\nDisallow:\nSitemap: https://tsech.online/sitemap.xml")
    sitemap = _fetch(text="<urlset><url><loc>https://tsech.online/</loc></url></urlset>")
    pillar = collect_seo(facts, robots, sitemap)
    checks = {f.check: f for f in pillar.findings}
    assert checks["robots.txt"].status == Status.OK and "declares a Sitemap" in checks["robots.txt"].detail
    assert checks["sitemap.xml"].status == Status.OK and "1 <loc>" in checks["sitemap.xml"].detail
    assert checks["Canonical URL"].status == Status.OK
    assert pillar.score == 10.0


def test_seo_robots_disallow_all_is_critical():
    robots = _fetch(text="User-agent: *\nDisallow: /\n")
    pillar = collect_seo(_facts(), robots, sitemap=None)
    assert next(f for f in pillar.findings if f.check == "robots.txt").status == Status.BAD


def test_seo_canonical_mismatch_is_warning():
    pillar = collect_seo(_facts(canonical="https://www.tsech.online/other"), robots=None, sitemap=None)
    finding = next(f for f in pillar.findings if f.check == "Canonical URL")
    assert finding.status == Status.WARN and "page=" in finding.evidence


# ---- Security -------------------------------------------------------------------


def test_security_all_headers_present_scores_full():
    headers = {
        "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
        "strict-transport-security": "max-age=63072000; includeSubDomains",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=()",
    }
    pillar = collect_security_headers(_fetch(headers=headers), _fetch(url="https://tsech.online/", status=200), _facts())
    checks = {f.check: f for f in pillar.findings}
    assert checks["Clickjacking protection"].status == Status.OK and "frame-ancestors" in checks["Clickjacking protection"].evidence
    assert checks["HTTP → HTTPS redirect"].status == Status.OK
    assert pillar.score == 10.0


def test_security_missing_headers_and_http():
    pillar = collect_security_headers(_fetch(url="http://tsech.online/", headers={"server": "nginx/1.24.0"}), None, _facts())
    checks = {f.check: f for f in pillar.findings}
    assert checks["HTTPS"].status == Status.BAD
    assert checks["Strict-Transport-Security (HSTS)"].status == Status.NA  # not applicable without https
    assert checks["Content-Security-Policy"].status == Status.BAD and checks["Content-Security-Policy"].fix
    assert checks["Version disclosure"].status == Status.WARN and "nginx/1.24.0" in checks["Version disclosure"].evidence
    assert pillar.score < 3


def test_security_weak_hsts_mixed_content_and_cookie_flags():
    headers = {"strict-transport-security": "max-age=300", "set-cookie": "session=abc; Path=/"}
    facts = _facts(mixedContent=["http://cdn.example/lib.js"])
    pillar = collect_security_headers(_fetch(headers=headers), _fetch(url="http://tsech.online/"), facts)
    checks = {f.check: f for f in pillar.findings}
    assert checks["Strict-Transport-Security (HSTS)"].status == Status.WARN
    assert checks["Mixed content"].status == Status.BAD and "cdn.example" in checks["Mixed content"].evidence
    assert checks["Cookie flags"].status == Status.WARN and "session" in checks["Cookie flags"].evidence
    assert checks["HTTP → HTTPS redirect"].status == Status.WARN


# ---- Accessibility ----------------------------------------------------------------


def test_accessibility_flags_gaps_with_fixes():
    facts = _facts(landmarks=0, totalImages=4, imagesMissingAlt=2, totalInputs=3, inputsWithoutLabel=2, viewport="width=device-width, user-scalable=no", lang=None, headingSkips=1, duplicateIds=2)
    pillar = collect_accessibility(facts)
    checks = {f.check: f for f in pillar.findings}
    assert checks["Landmarks"].status == Status.BAD
    assert checks["Image alt text"].status == Status.BAD and checks["Image alt text"].evidence == "2/4 failing"
    assert checks["Image alt text"].detail == "2 of 4 images lack an alt attribute"
    assert checks["Form labels"].status == Status.BAD
    assert checks["Viewport meta tag"].status == Status.WARN and "zoom" in checks["Viewport meta tag"].detail
    assert checks["HTML lang attribute"].status == Status.BAD
    assert checks["Heading order"].status == Status.WARN
    assert checks["Duplicate IDs"].status == Status.WARN
    assert checks["Colour contrast"].status == Status.NA
    assert all(f.fix for f in pillar.findings if f.status in (Status.BAD, Status.WARN))
    assert pillar.score < 4


def test_accessibility_clean_page_scores_high():
    facts = _facts(landmarks=4, skipLink=True, buttonsWithoutLabel=0, totalLinks=10)
    pillar = collect_accessibility(facts)
    assert pillar.score == 10.0
    assert next(f for f in pillar.findings if f.check == "Image alt text").status == Status.OK


# ---- Performance ------------------------------------------------------------------


def test_performance_thresholds_and_page_weight():
    pillar = collect_performance(_facts())
    checks = {f.check: f for f in pillar.findings}
    assert checks["Time to First Byte"].status == Status.OK and checks["Time to First Byte"].evidence == "420 ms"
    assert checks["First Contentful Paint"].status == Status.OK
    assert checks["Page weight"].status == Status.OK and "KB" in checks["Page weight"].detail
    assert "index-abc.js" in checks["Largest resources"].evidence
    assert checks["Render-blocking scripts"].status == Status.OK
    assert pillar.score == 10.0


def test_performance_slow_and_heavy_page():
    facts = _facts(
        timing={"ttfbMs": 2500, "domInteractiveMs": 5000, "domContentLoadedMs": 5000, "loadMs": 9000, "transferSize": 50000},
        paint={"first-contentful-paint": 3500},
        resources=[{"name": f"https://x/{i}.js", "type": "script", "transferSize": 400000, "encodedBodySize": 0, "durationMs": 100} for i in range(12)],
        headSyncScripts=3,
    )
    pillar = collect_performance(facts)
    checks = {f.check: f for f in pillar.findings}
    assert checks["Time to First Byte"].status == Status.BAD
    assert checks["First Contentful Paint"].status == Status.BAD
    assert checks["Page weight"].status == Status.BAD and "MB" in checks["Page weight"].detail
    assert checks["Render-blocking scripts"].status == Status.WARN
    assert pillar.score < 4


def test_performance_without_timing_is_na_not_crash():
    pillar = collect_performance(_facts(timing=None, paint={}, resources=[]))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Time to First Byte"].status == Status.NA
    assert checks["Page weight"].status == Status.NA


# ---- Tech stack -------------------------------------------------------------------


def test_tech_stack_detects_frameworks_cdn_and_trackers():
    facts = _facts(frameworks={**_facts()["frameworks"], "next": True, "jquery": "3.7.1"}, thirdPartyDomains=["www.googletagmanager.com", "cdn.jsdelivr.net"], scriptSrcs=["https://www.googletagmanager.com/gtag/js?id=G-1"])
    pillar = collect_tech_stack(facts, {"server": "nginx", "cf-ray": "abc"})
    checks = {f.check: f for f in pillar.findings}
    assert "Next.js" in checks["Frontend stack"].detail and "jQuery 3.7.1" in checks["Frontend stack"].detail
    assert "Cloudflare" in checks["Hosting / server"].detail
    assert checks["Analytics / tracking scripts"].status == Status.WARN and "googletagmanager" in checks["Analytics / tracking scripts"].evidence
    assert pillar.score is None


# ---- Privacy ----------------------------------------------------------------------


def test_privacy_missing_policy_with_login_is_critical():
    pillar = collect_privacy(_facts())
    checks = {f.check: f for f in pillar.findings}
    assert checks["Privacy policy"].status == Status.BAD and "legal requirement" in checks["Privacy policy"].detail
    assert checks["Cookie consent"].status == Status.OK  # no cookies, no third parties -> no banner needed
    assert pillar.score < 5


def test_privacy_cookies_without_consent_is_warning():
    pillar = collect_privacy(_facts(privacyLink=True, termsLink=True, cookies=["_ga", "session"], thirdPartyDomains=["www.google-analytics.com"]))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Cookie consent"].status == Status.WARN and "_ga" in checks["Cookie consent"].evidence
    assert checks["Privacy policy"].status == Status.OK


# ---- Links ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_links_none_is_na():
    pillar = await collect_broken_links(_facts(links=[]))
    assert pillar.score is None and pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_links_checks_and_flags_dead_ones(monkeypatch):
    class FakeResponse:
        def __init__(self, code):
            self.status_code = code

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def head(self, url):
            return FakeResponse(200 if "good" in url else 404)

        async def get(self, url):
            return FakeResponse(200 if "good" in url else 404)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    links = [{"href": "https://good.example/", "text": "Good", "external": True, "nofollow": False}, {"href": "https://tsech.online/dead", "text": "Dead", "external": False, "nofollow": False}]
    pillar = await collect_broken_links(_facts(links=links))
    checks = {f.check: f for f in pillar.findings}
    assert "1 internal, 1 external" in checks["Link inventory"].detail
    assert checks["Broken links"].status == Status.BAD and "/dead → 404" in checks["Broken links"].evidence
    assert pillar.score == 5.0
