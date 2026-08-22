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

# --- BACKGROUND TOKEN POLLING ---
async def poll_token(context, client_id, device_code):
    """Pollt den VW-Server über Chromium im Hintergrund, bis Playwright den Login beendet hat."""
    while True:
        await asyncio.sleep(5)
        logging.info("Polle Token-Server im Hintergrund...")
        
        # Native Chromium POST-Anfrage
        resp = await context.request.post(
            "https://identity.vwgroup.io/oidc/v1/token",
            form={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code
            }
        )
        
        if resp.status == 200:
            data = await resp.json()
            if "access_token" in data:
                logging.info("Token erfolgreich vom VW-Server erhalten!")
                with open(TOKEN_FILE, 'w') as f:
                    json.dump(data, f, indent=4)
                logging.info(f"Tokens erfolgreich für die HACS-Integration in {TOKEN_FILE} gespeichert!")
                return True
        else:
            try:
                error_data = await resp.json()
                # authorization_pending wird von VW absichtlich geschickt, solange der User sich noch einloggt
                if error_data.get("error") != "authorization_pending":
                    logging.warning(f"Polling Status: {error_data.get('error')}")
            except Exception:
                pass

# --- PLAYWRIGHT AUTOMATION ---
async def run_browser_automation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # 1. Device Flow Variablen vorbereiten
            # Wir nutzen exakt die DEVICE_FLOW_CLIENT_ID der WeConnect App
            client_id = "650d46ca-2475-4384-85c2-6af3bf3d52f1@apps_vw-dilab_com"
            scope = "openid profile badge cars dealers vin offline_access"
            
            # 2. Initiale API-Anfrage über Chromium Context
            logging.info("Initiiere VW Device Login Flow über Chromium...")
            resp = await context.request.post(
                "https://identity.vwgroup.io/oidc/v1/device_authorization",
                form={"client_id": client_id, "scope": scope}
            )
            
            if resp.status != 200:
                logging.error(f"Fehler bei Device Auth. HTTP {resp.status}: {await resp.text()}")
                return
                
            device_data = await resp.json()
            device_code = device_data["device_code"]
            verification_url = device_data["verification_uri_complete"]

            # 3. Token-Polling als Hintergrund-Task starten
            polling_task = asyncio.create_task(poll_token(context, client_id, device_code))

            # 4. Browser auf die persönliche Auth-URL leiten
            logging.info("Navigiere zur persönlichen VW-Verifizierungsseite...")
            await page.goto(verification_url, timeout=60000)

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

            # Zugangsdaten (Device-Flow landet oft direkt beim E-Mail Feld)
            await page.fill('input[name="username"], input[name="email"]', VW_USER)
            
            # Manchmal ist E-Mail und Passwort im VW Flow separiert, daher Zwischenklick
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
                    
                    # Warten, bis der Code über die Web-UI eingereicht wurde
                    code_received_event.clear()
                    await asyncio.wait_for(code_received_event.wait(), timeout=300) # 5 Minuten Timeout
                    
                    # Code eingeben
                    logging.info(f"Gebe empfangenen Code ein: {vw_2fa_code}")
                    await page.fill('input[name="code"]', vw_2fa_code)
                    
                    # Checkbox anklicken (30 Tage speichern)
                    logging.info("Setze Haken bei 'Dieses Gerät 30 Tage speichern'...")
                    try:
                        await page.locator('label[for="rememberBrowser"]').click()
                    except Exception:
                        pass
                    
                    # Button zum Fortfahren klicken
                    logging.info("Klicke auf Fortfahren...")
                    await page.locator('button[data-action-button-primary="true"]').click()
                    await asyncio.sleep(4)
            except asyncio.TimeoutError:
                logging.error("Kein Code über die Web-UI eingegangen (Timeout nach 5 Minuten).")
                return
            except Exception:
                pass # Keine 2FA Maske, normal weiter

            # Der finale "Zulassen" / "Allow" Screen (OAuth Consent)
            logging.info("Prüfe auf App-Autorisierungs-Screen...")
            try:
                allow_btn = page.locator('button[data-action-button-primary="true"], button#allowAccess').first
                if await allow_btn.is_visible(timeout=5000):
                    logging.info("Klicke auf 'Zulassen'...")
                    await allow_btn.click()
            except Exception: 
                pass

            # Warten, bis der Polling-Task im Hintergrund den Token gemeldet hat
            logging.info("Warte auf Abschluss des API-Hintergrund-Pollings...")
            try:
                await asyncio.wait_for(polling_task, timeout=45.0)
                logging.info("Skript beendet. Tokens liegen im Config-Ordner bereit!")
                
                # Wir geben der JSON-Speicherung noch eine Sekunde Sicherheit
                await asyncio.sleep(1)
            except asyncio.TimeoutError:
                logging.warning("Timeout beim Warten auf Tokens vom VW-Server. Erstelle Debug-Screenshot...")
                await page.screenshot(path="/config/vw_login_timeout.png")
                logging.info(f"Die aktuelle URL im Browser ist: {page.url}")

        except Exception as e:
            logging.error(f"Fehler: {e}")
            await page.screenshot(path="/config/vw_login_error_state.png")
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