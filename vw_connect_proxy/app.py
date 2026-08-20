import asyncio
import json
import logging
import os
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

# Globale Events zur Steuerung des 2FA-Ablaufs
code_received_event = asyncio.Event()
vw_2fa_code = ""
vw_tokens = {}

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

# --- PLAYWRIGHT AUTOMATISIERUNG ---
async def intercept_tokens(response):
    """Fängt API-Responses von VW ab und speichert die Tokens."""
    global vw_tokens
    if "identity.vwgroup.io/oidc/v1/token" in response.url and response.status == 200:
        try:
            data = await response.json()
            if "access_token" in data:
                vw_tokens = data
                logging.info("BINGO! Access Token und Refresh Token erfolgreich abgefangen.")
                with open(TOKEN_FILE, 'w') as f:
                    json.dump(vw_tokens, f)
                logging.info(f"Tokens für die HACS-Integration unter {TOKEN_FILE} gespeichert.")
        except Exception as e:
            logging.error(f"Fehler beim Auslesen des Tokens: {e}")

async def run_browser_automation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        
        # Netzwerk-Traffic abhören, um Tokens abzufangen
        page.on("response", intercept_tokens)

        try:
            logging.info("Navigiere zur VW-Anmeldeseite...")
            await page.goto("https://www.volkswagen.de/de/besitzer-und-nutzer/myvolkswagen.html", timeout=60000)

            # Cookie Banner
            try:
                cookie_button = page.locator('button:has-text("Alle akzeptieren"), button#accept-all-btn, button[data-testid="uc-accept-all-button"]')
                await cookie_button.click(timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # Zum Login
            await page.locator('text="Anmelden oder registrieren"').click(timeout=10000)
            await asyncio.sleep(3)

            # Zugangsdaten
            await page.fill('input[name="username"]', VW_USER)
            await page.fill('input[name="password"]', VW_PASSWORD)
            await page.click('button[type="submit"][name="action"]')

            # Prüfen, ob 2FA verlangt wird
            try:
                if await page.locator('input[name="code"]').is_visible(timeout=5000):
                    logging.info("!!! VW verlangt 2FA. Bitte öffne die Web UI des Add-ons in Home Assistant und trage den Code ein !!!")
                    
                    # Warten, bis der Code über die Web-UI eingereicht wurde
                    code_received_event.clear()
                    await asyncio.wait_for(code_received_event.wait(), timeout=300) # 5 Minuten Timeout
                    
                    logging.info(f"Gebe empfangenen Code ein: {vw_2fa_code}")
                    await page.fill('input[name="code"]', vw_2fa_code)
                    await page.check('input#rememberBrowser')
                    await page.click('button[type="submit"][name="action"]')
            except asyncio.TimeoutError:
                logging.error("Kein Code über die Web-UI eingegangen (Timeout nach 5 Minuten).")
                return
            except Exception:
                pass # Keine 2FA Maske, normal weiter

            # Auf Weiterleitung warten und bei Timeout einen Screenshot machen
            try:
                logging.info("Warte auf finale Weiterleitung ins Portal...")
                await page.wait_for_url("**/portal/**", timeout=30000) 
                logging.info("Login erfolgreich abgeschlossen! Browser wird in 10 Sekunden geschlossen.")
                
                # Kurz warten, damit die Token-Requests im Hintergrund sicher durchlaufen
                await asyncio.sleep(10)
                
            except Exception as e:
                logging.warning("Timeout beim Warten auf '/portal/'. Erstelle Debug-Screenshot...")
                
                # Aktuelle URL ins Log schreiben (oft verrät das schon das Problem)
                logging.info(f"Die aktuelle URL im Browser ist: {page.url}")
                
                # Screenshot im Home Assistant config-Ordner speichern
                debug_path = "/config/vw_login_timeout.png"
                await page.screenshot(path=debug_path)
                logging.info(f"Screenshot erfolgreich unter {debug_path} gespeichert!")
                
                # Wir lassen ihn trotzdem noch kurz warten, falls noch Netzwerk-Requests für die Tokens laufen
                await asyncio.sleep(10)

        except Exception as e:
            logging.error(f"Fehler: {e}")
        finally:
            await browser.close()

async def main():
    # Startet Webserver und Playwright gleichzeitig
    await asyncio.gather(
        start_webserver(),
        run_browser_automation()
    )

if __name__ == "__main__":
    asyncio.run(main())