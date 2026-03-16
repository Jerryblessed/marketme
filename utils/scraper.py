import re
import logging
from utils.csv_utils import csv_load

log = logging.getLogger("marketme.scraper")


def scrape_leads(industry, location, keywords, limit=20):
    leads = []
    query = f"{keywords or industry} {location} business contact email"
    log.info(f"Playwright scraping: {query}")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = ctx.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

            # Try Bing first (less blocking than Google)
            try:
                page.goto(
                    f"https://www.bing.com/search?q={query.replace(' ', '+')}",
                    timeout=25000,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(2500)
                content = page.content()
                page.keyboard.press("End")
                page.wait_for_timeout(1000)
                content += page.content()
            except Exception as be:
                log.warning(f"Bing failed ({be}), trying DDG")
                page.goto(
                    f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
                    timeout=25000,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(2500)
                content = page.content()

            # Extract emails from page
            found_emails = list(set(re.findall(r'[\w.+\-]{2,}@[\w\-]+\.\w{2,6}', content)))
            skip = {
                "noreply", "no-reply", "example", "test", "privacy",
                "contact@bing", "support@microsoft", "feedback",
                "postmaster", "webmaster",
            }
            for email in found_emails[:limit]:
                if any(s in email.lower() for s in skip):
                    continue
                domain = email.split("@")[1].split(".")[0]
                name   = email.split("@")[0].replace(".", " ").replace("_", " ").title()
                leads.append({
                    "name":    name,
                    "company": domain.title(),
                    "email":   email,
                    "notes":   f"Scraped: {industry} {location}",
                })

            # Fall back to shared CSV pool if not enough results
            if len(leads) < 5:
                for row in csv_load():
                    combined = (row.get("notes", "") + " " + row.get("company", "")).lower()
                    if industry.lower() in combined or location.lower() in combined:
                        leads.append({
                            "name":    row["name"],
                            "company": row.get("company", ""),
                            "email":   row["email"],
                            "notes":   row.get("notes", ""),
                        })
                    if len(leads) >= limit:
                        break

            browser.close()
            log.info(f"Playwright found {len(leads)} leads")

    except Exception as e:
        log.error(f"Playwright error: {e}")
        # Hard fallback: return from CSV if playwright totally fails
        for row in csv_load()[:limit]:
            leads.append({
                "name":    row["name"],
                "company": row.get("company", ""),
                "email":   row["email"],
                "notes":   row.get("notes", ""),
            })

    return leads
