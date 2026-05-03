import os
import re
import json
import time
import schedule
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

BASE_URL = "https://mon-vie-via.businessfrance.fr"
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche?latest=true"
SEEN_FILE = "seen_offers.json"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as file:
        return set(json.load(file))


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        json.dump(list(seen), file, ensure_ascii=False, indent=2)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=20
    )

    if response.status_code != 200:
        print("Erreur Telegram :", response.text)


def clean_text(text):
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def shorten(text, max_length=800):
    text = clean_text(text)
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."


def extract_between(text, start, end):
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return ""


def clean_location(location):
    if not location:
        return "Non trouvé"

    location = location.strip()

    for separator in [" VIE ", " VIA ", " vie ", " via "]:
        if separator in location:
            return location.split(separator)[0].strip()

    return location


def extract_offer_info(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    company = ""
    job_title = ""
    headline = ""
    publication_date = ""

    for i, line in enumerate(lines):
        upper_line = line.upper()

        if "PUBLIÉE LE" in upper_line or "PUBLIEE LE" in upper_line:
            publication_date = (
                line.replace("Publiée le", "")
                .replace("PUBLIÉE LE", "")
                .replace("Publiee le", "")
                .strip()
            )

            if i >= 1:
                headline = lines[i - 1]
            if i >= 2:
                job_title = lines[i - 2]
            if i >= 3:
                company = lines[i - 3]
            break

    location = clean_location(headline)

    mission = extract_between(text, "LA MISSION", "LE PROFIL IDÉAL")

    if not mission:
        mission = extract_between(text, "POSTE ET MISSION", "LE PROFIL IDÉAL")

    if not mission:
        mission = extract_between(text, "POSTE ET MISSION", "INFORMATIONS COMPLÉMENTAIRES")

    return {
        "company": company,
        "job_title": job_title,
        "location": location,
        "publication_date": publication_date,
        "mission": mission
    }


def fetch_offers():
    print("Chargement des offres avec Playwright...")

    offers = []
    urls_seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        links = page.locator("a").all()

        for link in links:
            href = link.get_attribute("href")

            if not href:
                continue

            if "/offres/" not in href:
                continue

            if "recherche" in href.lower():
                continue

            url = href if href.startswith("http") else BASE_URL + href
            offer_id = url.rstrip("/").split("/")[-1]

            if url in urls_seen:
                continue

            urls_seen.add(url)

            detail_page = browser.new_page()
            detail_page.goto(url, wait_until="networkidle", timeout=60000)
            detail_page.wait_for_timeout(3000)

            text = detail_page.locator("body").inner_text()
            info = extract_offer_info(text)

            offers.append({
                "id": offer_id,
                "url": url,
                "company": info["company"],
                "title": info["job_title"],
                "location": info["location"],
                "publication_date": info["publication_date"],
                "mission": info["mission"]
            })

            detail_page.close()

        browser.close()

    return offers


def build_message(offer):
    message = (
        "🚨 <b>Nouvelle offre VIE/VIA</b>\n\n"
        f"🏢 <b>Entreprise :</b> {offer.get('company') or 'Non trouvé'}\n"
        f"💼 <b>Poste :</b> {offer.get('title') or 'Non trouvé'}\n"
        f"📍 <b>Localisation :</b> {offer.get('location') or 'Non trouvé'}\n"
        f"🗓️ <b>Publication :</b> {offer.get('publication_date') or 'Non trouvé'}\n\n"
    )

    if offer.get("mission"):
        message += (
            "🎯 <b>Mission :</b>\n"
            f"{shorten(offer['mission'], 800)}\n\n"
        )

    message += f"🔗 <b>Lien :</b>\n{offer['url']}"

    return message


def check_offers():
    print("\nVérification des nouvelles offres...")

    seen = load_seen()
    offers = fetch_offers()

    print(f"Offres récupérées : {len(offers)}")

    new_count = 0

    for offer in offers:
        if offer["id"] not in seen:
            send_telegram(build_message(offer))
            print("Envoyé :", offer.get("title") or offer["url"])

            seen.add(offer["id"])
            new_count += 1

    save_seen(seen)

    if new_count == 0:
        print("Aucune nouvelle offre.")
    else:
        print(f"{new_count} nouvelle(s) offre(s) envoyée(s).")


if __name__ == "__main__":
    print("Bot lancé")
    send_telegram("✅ Test GitHub Actions : le bot fonctionne.")
    check_offers()
