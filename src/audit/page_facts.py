"""One snapshot of everything the collectors need from the rendered page.

A single `page.evaluate` call returns a plain dict of facts; each collector is
then a pure function over that dict (easy to test, no browser needed) and the
page is only queried once — after the page has finished loading and settled,
so client-rendered (SPA) content and late resources are included.
"""

import asyncio
from typing import Any, Dict

from src.audit.js_eval import eval_json

_READY_JS = "() => ({ state: document.readyState, resources: performance.getEntriesByType('resource').length, textLength: (document.body && document.body.innerText || '').length })"


async def wait_for_settled(page, timeout_s: float = 8.0, settle_s: float = 1.0) -> None:
    """Wait for readyState=complete, then until resource count and text length
    stop changing for `settle_s` (SPAs render after load). Bounded by timeout_s."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last = None
    stable_since = None
    while loop.time() < deadline:
        snap = await eval_json(page, _READY_JS) or {}
        key = (snap.get("state"), snap.get("resources"), snap.get("textLength"))
        if snap.get("state") == "complete":
            if key == last:
                if stable_since is not None and loop.time() - stable_since >= settle_s:
                    return
            else:
                stable_since = loop.time()
        last = key
        await asyncio.sleep(0.25)


PAGE_FACTS_JS = """() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => [...document.querySelectorAll(s)];
  const meta = (n) => q('meta[name="' + n + '"]')?.content || null;
  const prop = (p) => q('meta[property="' + p + '"]')?.content || null;
  const txt = (el) => (el.textContent || '').trim();
  const host = location.hostname;
  const isThirdParty = (u) => { try { const h = new URL(u, location.href).hostname; return h !== host && !h.endsWith('.' + host); } catch (e) { return false; } };

  const structuredData = qa('script[type="application/ld+json"]').map((s) => {
    try { const j = JSON.parse(s.textContent); const t = Array.isArray(j) ? j.map((x) => x['@type']).join(',') : (j['@type'] || j['@graph']?.map((x) => x['@type']).join(',')); return t || 'untyped'; }
    catch (e) { return 'invalid'; }
  });

  const headings = qa('h1,h2,h3,h4,h5,h6').map((h) => parseInt(h.tagName[1]));
  let headingSkips = 0;
  for (let i = 1; i < headings.length; i++) { if (headings[i] - headings[i - 1] > 1) headingSkips++; }

  const images = qa('img');
  const buttons = qa('button, [role="button"]');
  const inputs = qa('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea');
  const inputWithoutLabel = inputs.filter((el) => {
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title')) return false;
    if (el.id && q('label[for="' + el.id + '"]')) return false;
    return !el.closest('label');
  }).length;
  const links = qa('a[href]');
  const linkNoText = links.filter((a) => !txt(a) && !a.getAttribute('aria-label') && !a.querySelector('img[alt]:not([alt=""])')).length;

  const resources = performance.getEntriesByType('resource').map((r) => ({
    name: r.name, type: r.initiatorType, transferSize: r.transferSize || 0, encodedBodySize: r.encodedBodySize || 0, durationMs: Math.round(r.duration),
  }));
  const nav = performance.getEntriesByType('navigation')[0];
  const paint = {};
  performance.getEntriesByType('paint').forEach((p) => { paint[p.name] = Math.round(p.startTime); });
  const headSyncScripts = qa('head script[src]:not([async]):not([defer]):not([type="module"])').length;
  const mixedContent = location.protocol === 'https:' ? qa('img[src^="http:"], script[src^="http:"], link[href^="http:"], iframe[src^="http:"]').map((el) => el.src || el.href).slice(0, 10) : [];

  const scriptSrcs = qa('script[src]').map((s) => s.src);
  const rootEl = q('#root') || q('#app') || q('#__next') || q('#__nuxt');
  const bodyText = document.body ? (document.body.innerText || '') : '';

  return {
    url: location.href,
    title: document.title || null,
    lang: document.documentElement.lang || null,
    metaDescription: meta('description'),
    robotsMeta: meta('robots'),
    canonical: q('link[rel="canonical"]')?.href || null,
    h1Texts: qa('h1').map(txt).slice(0, 3),
    h1Count: qa('h1').length, h2Count: qa('h2').length, h3Count: qa('h3').length,
    headingSkips,
    og: { title: prop('og:title'), description: prop('og:description'), image: prop('og:image') },
    twitterCard: meta('twitter:card'),
    favicon: !!q('link[rel~="icon"], link[rel="shortcut icon"], link[rel="apple-touch-icon"]'),
    hreflangCount: qa('link[rel="alternate"][hreflang]').length,
    structuredData,
    viewport: meta('viewport'),
    landmarks: qa('header, nav, main, footer, aside, [role="banner"], [role="navigation"], [role="main"], [role="contentinfo"]').length,
    skipLink: !!qa('a[href^="#"]').find((a) => /skip/i.test(txt(a))),
    totalImages: images.length,
    imagesMissingAlt: images.filter((i) => !i.hasAttribute('alt')).length,
    totalButtons: buttons.length,
    buttonsWithoutLabel: buttons.filter((b) => !txt(b) && !b.getAttribute('aria-label') && !b.getAttribute('title') && !b.querySelector('img[alt]:not([alt=""])')).length,
    totalInputs: inputs.length, inputsWithoutLabel: inputWithoutLabel,
    totalLinks: links.length, linksWithoutText: linkNoText,
    positiveTabindex: qa('[tabindex]').filter((el) => parseInt(el.getAttribute('tabindex')) > 0).length,
    iframesWithoutTitle: qa('iframe').filter((f) => !f.getAttribute('title')).length,
    duplicateIds: (() => { const seen = {}; let d = 0; qa('[id]').forEach((el) => { if (seen[el.id]) d++; seen[el.id] = 1; }); return d; })(),
    links: links.map((a) => ({ href: a.href, text: txt(a).slice(0, 60), external: isThirdParty(a.href), nofollow: /nofollow/i.test(a.rel || '') })).filter((l) => /^https?:/.test(l.href)).slice(0, 200),
    resources,
    timing: nav ? { ttfbMs: Math.round(nav.responseStart), domInteractiveMs: Math.round(nav.domInteractive), domContentLoadedMs: Math.round(nav.domContentLoadedEventEnd), loadMs: Math.round(nav.loadEventEnd), transferSize: nav.transferSize || 0 } : null,
    paint,
    headSyncScripts,
    stylesheetCount: qa('link[rel="stylesheet"]').length,
    mixedContent,
    scriptSrcs,
    thirdPartyDomains: [...new Set(resources.map((r) => r.name).concat(scriptSrcs).filter(isThirdParty).map((u) => new URL(u).hostname))],
    frameworks: {
      react: !!(rootEl && Object.keys(rootEl).some((k) => k.startsWith('__reactContainer') || k.startsWith('_reactRootContainer'))) || !!window.React,
      next: !!q('#__next') || !!window.__NEXT_DATA__, nuxt: !!q('#__nuxt') || !!window.__NUXT__,
      vue: !!q('[data-v-app]') || !!window.__VUE__, angular: !!q('[ng-version]'), svelte: !!q('[class*="svelte-"]'),
      jquery: window.jQuery ? (window.jQuery.fn?.jquery || 'yes') : null,
      bootstrap: qa('link[href*="bootstrap"], script[src*="bootstrap"]').length > 0,
      wordpress: !!q('link[href*="wp-content"], script[src*="wp-content"], meta[name="generator"][content*="WordPress"]'),
    },
    generator: meta('generator'),
    cookies: document.cookie ? document.cookie.split(';').map((c) => c.split('=')[0].trim()).filter(Boolean) : [],
    privacyLink: !!links.find((a) => /privacy|policy|confidential/i.test(txt(a) + ' ' + a.href)),
    termsLink: !!links.find((a) => /terms|conditions|legal/i.test(txt(a) + ' ' + a.href)),
    cookieNotice: /cookie|consent/i.test(bodyText),
    hasPasswordField: !!q('input[type="password"]'),
    hasForms: qa('form').length,
    bodyText: bodyText.slice(0, 20000),
    wordCount: bodyText.trim() ? bodyText.trim().split(/\\s+/).length : 0,
  };
}"""


async def gather_page_facts(page) -> Dict[str, Any]:
    await wait_for_settled(page)
    facts = await eval_json(page, PAGE_FACTS_JS)
    if not isinstance(facts, dict):
        raise RuntimeError(f"Page facts script did not return an object: {str(facts)[:200]}")
    return facts
