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

# --- PLAYWRIGHT ROUTE INTERCEPTOR ---
async def intercept_weconnect_redirect(route):
    """Fängt die weconnect:// Weiterleitung ab und extrahiert den Auth Code."""
    global auth_code
    url = route.request.url
    if url.startswith("weconnect://"):
        logging.info(f"App-Redirect abgefangen: {url}")
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        # Manchmal hängt der Code auch im Fragment
        if not query_params and parsed_url.fragment:
            query_params = urllib.parse.parse_qs(parsed_url.fragment)
            
        code = query_params.get("code", [None])[0]
        if code:
            logging.info("Authorization Code erfolgreich abgefangen!")
            auth_code = code
            auth_flow_finished_event.set()
            
        await route.abort()
    else:
        await route.continue_()

# --- PLAYWRIGHT AUTOMATION ---
async def run_browser_automation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()

        # Route Interceptor aktivieren
        await page.route("**/*", intercept_weconnect_redirect)

        try:
            # 1. PKCE Parameter generieren
            code_verifier, code_challenge = generate_pkce_pair()
            nonce = uuid.uuid4().hex
            
            # 2. Reinen Authorization Code Flow initiieren (ohne implicit 'token')
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
            await page.goto(auth_url, timeout=60000)

            # Screenshot im Home Assistant config-Ordner speichern
            debug_path = "/config/vw_login_001.png"
            await page.screenshot(path=debug_path)
            logging.info(f"Screenshot erfolgreich unter {debug_path} gespeichert!")

            # Cookie Banner
            try:
                cookie_button = page.locator('button:has-text("Alle akzeptieren"), button#accept-all-btn, button[data-testid="uc-accept-all-button"]')
                await cookie_button.click(timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # Zugangsdaten
            await page.fill('input[name="username"], input[name="email"]', VW_USER)
            
            try:
                await page.click('button[type="submit"][name="action"]')
                await asyncio.sleep(2)
            except Exception:
                pass

            # Passwort
            await page.fill('input[name="password"]', VW_PASSWORD)
            await page.click('button[type="submit"][name="action"]')
            await asyncio.sleep(3)

            # Prüfen, ob 2FA verlangt wird
            try:
                if await page.locator('input[name="code"]').is_visible(timeout=5000):
                    logging.info("!!! VW verlangt 2FA. Bitte öffne die Web UI des Add-ons in Home Assistant und trage den Code ein !!!")
                    
                    code_received_event.clear()
                    await asyncio.wait_for(code_received_event.wait(), timeout=300)
                    
                    logging.info(f"Gebe empfangenen Code ein: {vw_2fa_code}")
                    await page.fill('input[name="code"]', vw_2fa_code)
                    
                    try:
                        await page.locator('label[for="rememberBrowser"]').click()
                    except Exception:
                        pass
                    
                    await page.locator('button[data-action-button-primary="true"]').click()
                    await asyncio.sleep(4)
            except asyncio.TimeoutError:
                logging.error("Kein Code über die Web-UI eingegangen (Timeout nach 5 Minuten).")
                return
            except Exception:
                pass

            # Finaler Zustimmungsbildschirm ("Zulassen"), falls vorhanden
            try:
                allow_btn = page.locator('button[data-action-button-primary="true"], button#allowAccess').first
                if await allow_btn.is_visible(timeout=5000):
                    logging.info("Klicke auf 'Zulassen'...")
                    await allow_btn.click()
            except Exception:
                pass

            # 3. Warten, bis der Redirect weconnect:// abgefangen wurde
            logging.info("Warte auf App-Weiterleitung...")
            await asyncio.wait_for(auth_flow_finished_event.wait(), timeout=45.0)

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
            logging.error(f"Fehler: {e}")
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