import os
import re
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

BASE_URL = "https://mon-vie-via.businessfrance.fr"
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche?latest=true"
SEEN_FILE = "seen_offers.json"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_seen():
    """
    Charge l'historique des offres déjà vues.
    Compatible avec l'ancien format seen_offers.json si c'était une simple liste.
    """
    if not os.path.exists(SEEN_FILE):
        return {
            "ids": [],
            "last_new_offer_at": None
        }

    with open(SEEN_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return {
            "ids": data,
            "last_new_offer_at": None
        }

    return data


def save_seen(data):
    """
    Sauvegarde l'historique.
    """
    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def send_telegram(message):
    """
    Envoie un message Telegram.
    """
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
    """
    Nettoie les retours à la ligne excessifs.
    """
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def shorten(text, max_length=800):
    """
    Raccourcit le texte pour éviter des messages Telegram trop longs.
    """
    text = clean_text(text)

    if len(text) <= max_length:
        return text

    return text[:max_length].rsplit(" ", 1)[0] + "..."


def extract_between(text, start, end):
    """
    Extrait un bloc de texte entre deux titres.
    """
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)

    if match:
        return clean_text(match.group(1))

    return ""


def clean_location(location):
    """
    Nettoie la localisation.
    Exemple :
    'BUENOS AIRES VIA septembre 2026 12 mois'
    devient :
    'BUENOS AIRES'
    """
    if not location:
        return "Non trouvé"

    location = location.strip()

    for separator in [" VIE ", " VIA ", " vie ", " via "]:
        if separator in location:
            return location.split(separator)[0].strip()

    return location


def extract_offer_info(text):
    """
    Extrait les informations principales d'une page d'offre.
    """
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
    """
    Récupère les dernières offres visibles sur la page.
    On clique plusieurs fois sur 'Voir plus d'offres' pour charger plus que les 6 premières.
    """
    print("Chargement des offres avec Playwright...")

    offers = []
    urls_seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        # Charge plus d'offres que les 6 premières
        for i in range(5):
            button = page.locator("text=VOIR PLUS D'OFFRES")

            if button.count() == 0:
                print("Bouton Voir plus introuvable.")
                break

            try:
                print(f"Click Voir plus #{i + 1}")
                button.first.click(timeout=7000)
                page.wait_for_timeout(2500)
            except Exception as error:
                print("Impossible de cliquer sur Voir plus :", error)
                break

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
            detail_page.wait_for_timeout(2500)

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
    """
    Construit le message Telegram pour une nouvelle offre.
    """
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


def format_duration_since_last_new(last_new_offer_at):
    """
    Calcule depuis combien de temps aucune nouvelle offre n'a été trouvée.
    """
    if not last_new_offer_at:
        return "aucune nouvelle offre détectée depuis le lancement du bot"

    last_time = datetime.fromisoformat(last_new_offer_at)
    now = datetime.now(timezone.utc)

    delta = now - last_time
    total_minutes = int(delta.total_seconds() // 60)

    if total_minutes < 60:
        return f"{total_minutes} minute(s)"

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if minutes == 0:
        return f"{hours} heure(s)"

    return f"{hours} heure(s) et {minutes} minute(s)"


def check_offers():
    """
    Fonction principale :
    - récupère les offres
    - compare avec les offres déjà vues
    - envoie les nouvelles
    - si rien de nouveau, envoie un message de suivi
    """
    print("\nVérification des nouvelles offres...")

    data = load_seen()
    seen = set(data.get("ids", []))
    last_new_offer_at = data.get("last_new_offer_at")

    offers = fetch_offers()

    print(f"Offres récupérées : {len(offers)}")

    new_count = 0

    for offer in offers:
        if offer["id"] not in seen:
            send_telegram(build_message(offer))
            print("Envoyé :", offer.get("title") or offer["url"])

            seen.add(offer["id"])
            new_count += 1

    if new_count > 0:
        data["last_new_offer_at"] = datetime.now(timezone.utc).isoformat()
        print(f"{new_count} nouvelle(s) offre(s) envoyée(s).")

    else:
        duration = format_duration_since_last_new(last_new_offer_at)

        send_telegram(
            f"ℹ️ <b>Bot VIE actif</b>\n\n"
            f"Pas de nouvelle offre depuis {duration}.\n"
            f"Offres vérifiées : {len(offers)}"
        )

        print("Aucune nouvelle offre.")

    data["ids"] = list(seen)
    save_seen(data)


if __name__ == "__main__":
    print("Bot lancé")
    check_offers()
