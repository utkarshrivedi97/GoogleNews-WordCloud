#!/usr/bin/env python3
"""
Google News -> Country WordCloud -> Email report
Sends one email per keyword (sender and recipient both ecgccudir@gmail.com).
Do NOT hardcode your app password. Set environment variable EMAIL_PASSWORD to the 16-char Gmail App Password.
"""

import os
import re
import time
import logging
import feedparser
import requests
from bs4 import BeautifulSoup
from wordcloud import WordCloud
import pycountry
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from dateutil import tz

# ---- Configuration ----
FROM_ADDR = "ecgccudir@gmail.com"
TO_ADDR = "research@ecgc.in"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")  # MUST be set in environment

# If you want fewer articles per feed, change this (e.g., 10 or 20)
MAX_ARTICLES_PER_QUERY = 25

# Keywords / queries
QUERIES = [
    'Export Credit Insurance',
    'Export Credit Agency',
    'Debt Distress OR Debt Crisis',
    'Export Credit OR Trade Credit',
    'Political Risk OR Geopolitical Risk',
    'War OR Riot',
    'Import Regulation OR Import Control',
    'Forex Reserve OR Forex Crisis',
    'Currency Devaluation OR Currency Crisis',
    'Country Rating'
]

# HTTP request headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsWordCloudBot/1.0; +https://example.com/bot)"
}

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---- Utility: build searchable country name set ----
def build_country_name_set():
    names = set()
    
    # Exclude short codes that are common English words or URL components
    excluded_codes = {
        "as", "at", "in", "is", "it", "ad", "am", "an", "be", "do", "if", 
        "no", "of", "on", "or", "so", "to", "us", "we", "and", "are", "can",
        "for", "may", "not", "the", "all", "but", "was", "com", "org", "net",
        "edu", "gov", "mil", "int", "cat", "per", "man", "age", "car", "bus"
    }
    
    for country in pycountry.countries:
        # Always include full country names
        names.add(country.name.lower())
        if getattr(country, "official_name", None):
            names.add(country.official_name.lower())
        
        # Only include alpha_2/alpha_3 codes if they're not common words
        # and are longer than 2 characters OR are well-known abbreviations
        if getattr(country, "alpha_3", None):
            code = country.alpha_3.lower()
            if code not in excluded_codes and len(code) == 3:
                names.add(code)

    # Common alternative names and abbreviations (only well-known ones)
    aliases = [
        "usa", "u.s.", "u.s.a.", "united states", "united states of america",
        "uk", "u.k.", "britain", "great britain", "south korea", "north korea",
        "russia", "iran", "vatican", "syria", "uae", "u.a.e.", "czechia", "czech republic",
        "democratic republic of congo", "drc", "ivory coast", "côte d'ivoire", "taiwan"
    ]
    for a in aliases:
        names.add(a.lower())

    # Return set of lowercased names for fast matching
    return names


COUNTRY_NAMES = build_country_name_set()

# For nicer display mapping: map lowercase variants to title-capitalized display name
def normalize_display_name(raw):
    raw = raw.strip()
    key = raw.lower()
    
    # common replacements
    repl = {
        "usa": "United States",
        "us": "United States",
        "u.s.": "United States",
        "u.s.a.": "United States",
        "uk": "United Kingdom",
        "u.k.": "United Kingdom",
        "drc": "DR Congo",
        "uae": "UAE",
        "u.a.e.": "UAE",
        "ivory coast": "Côte d'Ivoire"
    }
    
    if key in repl:
        return repl[key]
    
    # Try to find the full country name from pycountry using alpha_3 code
    if len(key) == 3:
        try:
            country = pycountry.countries.get(alpha_3=key.upper())
            if country:
                return country.name
        except:
            pass
    
    # Try to find by name (for full country names)
    try:
        country = pycountry.countries.get(name=raw.title())
        if country:
            return country.name
    except:
        pass
    
    # Fallback to title case
    return raw.title()


# ---- Extract country names from text (title/summary/body) ----
WORD_BOUNDARY = re.compile(r"\b({})\b".format("|".join(re.escape(n) for n in sorted(COUNTRY_NAMES, key=len, reverse=True))),
                           flags=re.IGNORECASE)


def extract_countries(text):
    if not text:
        return []
    found = set()
    for m in WORD_BOUNDARY.finditer(text):
        token = m.group(1).strip()
        found.add(token.lower())
    return list(found)


# ---- Fetch feed for a query ----
def fetch_google_news_rss(query, max_items=25, days=7):
    """
    Uses Google News RSS search.
    Filters articles from the last 'days' (default: 7 days).
    """
    q = query.replace(" ", "%20")
    time_filter = f"%20when:{days}d"
    rss_url = f"https://news.google.com/rss/search?q={q}{time_filter}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(rss_url)
    entries = feed.entries[:max_items]
    return entries


# ---- Try fetching article body text (best-effort) ----
def fetch_article_body(url, timeout=10):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Get visible paragraph texts (this is best-effort)
        paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
        return " ".join(paragraphs)
    except Exception:
        return ""


# ---- Generate WordCloud image from frequency dict ----
def generate_wordcloud(freq_dict, out_path, width=900, height=600):
    if not freq_dict:
        # create a small placeholder image with a message
        wc = WordCloud(width=width, height=height, background_color="white")
        wc.generate_from_text("No countries found")
    else:
        wc = WordCloud(width=width, height=height, background_color="white")
        wc.generate_from_frequencies(freq_dict)
    wc.to_file(out_path)
    return out_path


# ---- Build HTML list grouped by country ----
def build_articles_html(articles_by_country):
    html = []
    if not articles_by_country:
        html.append("<p>No country-specific articles found for this query in the recent feed.</p>")
        return "\n".join(html)

    html.append("<h3>Articles grouped by country</h3>")
    html.append("<div>")
    for country in sorted(articles_by_country.keys()):
        display = normalize_display_name(country)
        html.append(f"<h4 style='margin-bottom:4px'>{display}</h4>")
        html.append("<ul>")
        for art in articles_by_country[country]:
            # Protect against missing title/link
            title = art.get("title", "Untitled")
            link = art.get("link", "#")
            # Open in new tab by target attribute is not respected in some email clients but it's fine as simple HTML
            html.append(f'<li><a href="{link}">{title}</a></li>')
        html.append("</ul>")
    html.append("</div>")
    return "\n".join(html)


# ---- Send email with inline image and HTML body ----
def send_email(subject, html_body, image_path, image_cid="wordcloud"):
    if not EMAIL_PASSWORD:
        raise SystemExit("ERROR: set EMAIL_PASSWORD environment variable to your Gmail App Password (16 chars).")

    msg_root = MIMEMultipart("related")
    msg_root["From"] = FROM_ADDR
    msg_root["To"] = TO_ADDR
    msg_root["Subject"] = subject

    # Alternative (plain + html)
    msg_alt = MIMEMultipart("alternative")
    msg_root.attach(msg_alt)
    plain = "This email contains an inline WordCloud image and a list of articles grouped by country."
    msg_alt.attach(MIMEText(plain, "plain"))
    msg_alt.attach(MIMEText(html_body, "html"))

    # Attach image
    with open(image_path, "rb") as f:
        img_data = f.read()
    img = MIMEImage(img_data)
    img.add_header("Content-ID", f"<{image_cid}>")
    img.add_header("Content-Disposition", "inline", filename=os.path.basename(image_path))
    msg_root.attach(img)

    # Send via SMTP
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(FROM_ADDR, EMAIL_PASSWORD)
        server.sendmail(FROM_ADDR, [TO_ADDR], msg_root.as_string())


# ---- Main processing per query ----
def process_query(query):
    logging.info("Processing query: %s", query)
    entries = fetch_google_news_rss(query, max_items=MAX_ARTICLES_PER_QUERY)
    country_freq = {}         # display_name -> total count
    articles_by_country = {}  # lowercase_country_token -> list of articles

    for entry in entries:
        title = entry.get("title", "")
        summary = entry.get("summary", "") or entry.get("description", "")
        link = entry.get("link", "")

        combined_text = " ".join([title, summary]).strip()

        # Extract countries from title/summary
        found = extract_countries(combined_text)

        # If none found in feed text, try fetching article body (best-effort)
        if not found and link:
            time.sleep(0.5)  # polite delay
            body = fetch_article_body(link)
            found = extract_countries(body)

        # Count occurrences (in feed text and body if available)
        # We'll count number of matches in title+summary+body for frequency
        text_all = combined_text
        if link:
            body = fetch_article_body(link)
            if body:
                text_all += " " + body

        # For each found country token, count occurrences and append article
        unique_found = set(found)
        for token in unique_found:
            # count occurrences in text_all in a case-insensitive manner
            count = len(re.findall(r"\b" + re.escape(token) + r"\b", text_all, flags=re.IGNORECASE))
            display = normalize_display_name(token)
            country_freq[display] = country_freq.get(display, 0) + count
            articles_by_country.setdefault(token, []).append({"title": title, "link": link})

    # WordCloud filename safe
    safe_name = re.sub(r'[^A-Za-z0-9_\-]', '_', query).strip("_")
    image_filename = f"wordcloud_{safe_name}.png"

    # Generate wordcloud
    generate_wordcloud(country_freq, image_filename)

    # Prepare HTML: image inline (cid) and article list
    date_str = datetime.datetime.now(tz=tz.gettz("Asia/Kolkata")).strftime("%d.%m.%Y")
    subject = f"Google News search and WordCloud results for {query} dated {date_str}"

    # HTML with large inline image (set width to 900px)
    html_parts = []
    html_parts.append(f"<h2>Google News: {query}</h2>")
    html_parts.append(f'<div style="text-align:center;"><img src="cid:wordcloud" style="max-width:95%; width:900px; height:auto; display:block; margin:auto;" alt="WordCloud"></div>')
    html_parts.append("<hr>")
    html_parts.append(build_articles_html(articles_by_country))
    final_html = "\n".join(html_parts)

    # Send email
    try:
        send_email(subject, final_html, image_filename, image_cid="wordcloud")
        logging.info("Email sent for query: %s", query)
    except Exception as e:
        logging.exception("Failed to send email for query %s: %s", query, str(e))


def main():
    logging.info("Starting Google News WordCloud mailer")
    for q in QUERIES:
        try:
            process_query(q)
            # small pause between queries to avoid triggering blocks
            time.sleep(2)
        except Exception:
            logging.exception("Error processing query: %s", q)
    logging.info("All queries processed.")


if __name__ == "__main__":
    main()
