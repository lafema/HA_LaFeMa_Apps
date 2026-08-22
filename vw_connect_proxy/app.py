import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
import uuid
from aiohttp import web
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Credentials aus der HA Add-on Konfiguration laden
try:
    with open('/data/options.json') as f:
        options = json.load(f)
        VW_USER = options.get('vw_username', '')
        VW_PASSWORD = options.get('vw_password', '')
except FileNotFoundError:
    VW_USER = os.getenv('VW_USER', '')
    VW_PASSWORD = os.getenv('VW_PASSWORD', '')

TOKEN_FILE = '/config/.storage/vw_tokens.json'

CLIENT_ID = "a24fba63-34b3-4d43-b181-942111e6bda8@apps_vw-dilab_com"
REDIRECT_URI = "weconnect://authenticated"
SCOPE = "openid profile badge cars dealers vin offline_access"

# Globale Events zur Steuerung des 2FA-Ablaufs
code_received_event = asyncio.Event()
auth_flow_finished_event = asyncio.Event()
vw_2fa_code = ""
auth_code = ""

# --- PKCE HELPER ---
def generate_pkce_pair():
    """Generiert code_verifier und code_challenge für PKCE."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(hashed).decode('ascii').rstrip('=')
    return code_verifier, code_challenge

# --- AIOHTTP WEBSERVER (INGRESS) ---
async def handle_get(request):
    """Zeigt die Eingabemaske im Home Assistant Dashboard an."""
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>VW Login Proxy</title><style>body{font-family:sans-serif; padding:20px;}</style></head>
    <body>
        <h2>VW 2FA Code Eingabe</h2>
        <p>Falls eine E-Mail mit einem Code ankam, bitte hier eintragen:</p>
        <form method="POST">
            <input type="text" name="code" placeholder="Code (z.B. 123456)" required style="padding:10px; font-size:16px;">
            <button type="submit" style="padding:10px 20px; font-size:16px; background:#001e50; color:#fff; border:none;">Senden</button>
        </form>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def handle_post(request):
    """Nimmt den Code aus dem Formular entgegen und weckt Playwright auf."""
    global vw_2fa_code
    data = await request.post()
    vw_2fa_code = data.get('code', '').strip()
    
    # Event setzen, damit das wartende Playwright-Skript weiterläuft
    code_received_event.set()
    
    return web.Response(text="Code empfangen! Du kannst dieses Fenster schließen. Schau in die Logs.", content_type='text/html')

async def start_webserver():
    app = web.Application()
    app.add_routes([web.get('/', handle_get), web.post('/', handle_post)])
    runner = web.AppRunner(app)
    await runner.setup()
    # Der Port muss mit ingress_port aus der config.yaml übereinstimmen
    site = web.TCPSite(runner, '0.0.0.0', 8099)
    await site.start()
    logging.info("Webserver für HA Ingress auf Port 8099 gestartet.")

# --- PLAYWRIGHT EVENT LISTENER ---
async def handle_request(request):
    """Lauscht passiv auf alle Anfragen und fängt den weconnect-Redirect ab."""
    global auth_code
    url = request.url
    
    # Sobald Chromium versucht, den App-Link zu öffnen, schlagen wir zu
    if url.startswith("weconnect://"):
        logging.info(f"App-Redirect erfolgreich gesichtet: {url}")
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        # Falls der Code im Fragment steht (VW ändert das manchmal)
        if not query_params and parsed_url.fragment:
            query_params = urllib.parse.parse_qs(parsed_url.fragment)
            
        code = query_params.get("code", [None])[0]
        if code:
            logging.info("Authorization Code erfolgreich aus der URL extrahiert!")
            auth_code = code
            # Signal an das Hauptskript senden, dass wir fertig sind
            auth_flow_finished_event.set()

# --- PLAYWRIGHT AUTOMATION ---
async def run_browser_automation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()

        # Wir nutzen den passiven Event-Listener
        page.on("request", handle_request)

        try:
            # 1. PKCE Parameter generieren
            code_verifier, code_challenge = generate_pkce_pair()
            nonce = uuid.uuid4().hex
            
            # 2. Reinen Authorization Code Flow initiieren
            auth_params = {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPE,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "nonce": nonce
            }
            auth_url = f"https://identity.vwgroup.io/oidc/v1/authorize?{urllib.parse.urlencode(auth_params)}"

            logging.info("Starte Authorization-Code Flow über Playwright...")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await page.goto(auth_url, timeout=60000)
                    break 
                except Exception as e:
                    if "ERR_NETWORK_CHANGED" in str(e) and attempt < max_retries - 1:
                        logging.warning(f"Netzwerk-Ruckler erkannt. Versuch {attempt + 2} von {max_retries} in 3 Sekunden...")
                        await asyncio.sleep(3)
                    else:
                        raise 

            # Cookie Banner
            try:
                cookie_button = page.locator('button:has-text("Alle akzeptieren"), button#accept-all-btn, button[data-testid="uc-accept-all-button"]')
                await cookie_button.click(timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # Zugangsdaten
            logging.info("Gebe E-Mail ein...")
            await page.fill('input[type="email"], input[name="email"], input[name="username"]', VW_USER)
            
            if not await page.locator('input[type="password"]').is_visible():
                logging.info("Passwort-Feld nicht direkt sichtbar, klicke auf Weiter...")
                try:
                    await page.locator('button:has-text("Continue"), button:has-text("Weiter"), button:has-text("Fortfahren"), button[type="submit"]').first.click(timeout=3000)
                except Exception:
                    pass
                await page.locator('input[type="password"]').wait_for(timeout=5000)

            logging.info("Gebe Passwort ein...")
            await page.fill('input[type="password"]', VW_PASSWORD)
            
            await page.keyboard.press('Tab')
            await asyncio.sleep(1)
            
            logging.info("Sende Login-Formular ab...")
            try:
                submit_btn = page.locator('button:has-text("Continue"), button:has-text("Weiter"), button:has-text("Fortfahren"), button[type="submit"]').first
                await submit_btn.click(timeout=3000)
            except Exception as e:
                logging.debug(f"Klick ignoriert (Navigation hat gestartet): {e}")

            logging.info("Prüfe auf direkten Redirect (wie im Inkognito-Test beobachtet)...")
            
            # Wir warten 10 Sekunden. Wenn in dieser Zeit der App-Redirect erfolgt, 
            # überspringen wir den ganzen restlichen UI-Quatsch!
            try:
                await asyncio.wait_for(auth_flow_finished_event.wait(), timeout=10.0)
                logging.info("Direkter Redirect erkannt! Überspringe 2FA & Zustimmungs-Screens.")
            except asyncio.TimeoutError:
                # Nur wenn nach 10 Sekunden KEIN Redirect kam, schauen wir nach, 
                # ob eine 2FA-Maske den Prozess blockiert.
                logging.info("Kein sofortiger Redirect. Prüfe auf 2FA-Abfrage...")
                try:
                    if await page.locator('input[name="code"]').is_visible(timeout=3000):
                        logging.info("!!! VW verlangt 2FA. Bitte öffne die Web UI des Add-ons in Home Assistant !!!")
                        
                        code_received_event.clear()
                        await asyncio.wait_for(code_received_event.wait(), timeout=300)
                        
                        logging.info(f"Gebe empfangenen Code ein: {vw_2fa_code}")
                        await page.fill('input[name="code"]', vw_2fa_code)
                        
                        try:
                            await page.locator('label[for="rememberBrowser"]').click(timeout=2000)
                        except Exception: pass
                        
                        try:
                            await page.locator('button[data-action-button-primary="true"]').click(timeout=3000)
                        except Exception: pass
                        
                        # Zustimmungs-Screen nach 2FA (falls vorhanden)
                        try:
                            allow_btn = page.locator('button[data-action-button-primary="true"], button#allowAccess').first
                            if await allow_btn.is_visible(timeout=3000):
                                logging.info("Klicke auf 'Zulassen'...")
                                await allow_btn.click(timeout=3000)
                        except Exception: pass
                        
                        logging.info("Warte auf finale App-Weiterleitung nach 2FA...")
                        await asyncio.wait_for(auth_flow_finished_event.wait(), timeout=15.0)
                        
                except Exception as e:
                    logging.warning(f"Abbruch beim Warten auf Redirect: {e}")
                    await page.screenshot(path="/config/vw_login_timeout.png")
                    return

            # 4. Token-Tausch am OIDC Token-Endpoint über Chromium Context
            if auth_code:
                logging.info("Tausche Authorization Code gegen Tokens ein...")
                token_resp = await context.request.post(
                    "https://identity.vwgroup.io/oidc/v1/token",
                    form={
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "code": auth_code,
                        "redirect_uri": REDIRECT_URI,
                        "code_verifier": code_verifier
                    }
                )
                
                if token_resp.status == 200:
                    token_data = await token_resp.json()
                    logging.info("BINGO! Tokens erfolgreich erhalten!")
                    with open(TOKEN_FILE, 'w') as f:
                        json.dump(token_data, f, indent=4)
                    logging.info(f"Tokens erfolgreich für die HACS-Integration in {TOKEN_FILE} gespeichert!")
                else:
                    logging.error(f"Fehler beim Token-Tausch. HTTP {token_resp.status}: {await token_resp.text()}")

        except Exception as e:
            logging.error(f"Fehler im Ablauf: {e}")
            await page.screenshot(path="/config/vw_login_error_state.png")
        finally:
            await browser.close()

async def main():
    await asyncio.gather(
        start_webserver(),
        run_browser_automation()
    )

if __name__ == "__main__":
    asyncio.run(main())