import os
import time
import queue
import threading
from playwright.sync_api import sync_playwright
from datetime import datetime
import phonenumbers
from phonenumbers.phonenumberutil import region_code_for_country_code
import pycountry

class WhatsAppBot:
    _instance = None

    def __init__(self):
        self.user_data_dir = os.environ.get("WA_SESSION_DIR", os.path.join(os.getcwd(), "wa_session"))
        self.status = "stopped"
        self.qr_base64 = None
        self.thread = None
        self.cmd_queue = queue.Queue()
        self.playwright = None
        self.browser = None
        self.page = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WhatsAppBot()
        return cls._instance

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.status = "starting"
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def get_status(self):
        return {
            "status": self.status,
            "qr_base64": self.qr_base64
        }

    def scrape_invite(self, link):
        """Enqueue a scrape job and wait for the result."""
        result_q = queue.Queue()
        self.cmd_queue.put(("SCRAPE", link, result_q))
        return result_q.get() # Blocking wait

    def stop(self):
        if self.status != "stopped":
            self.cmd_queue.put(("STOP", None, None))
            if self.thread:
                self.thread.join(timeout=10)

    def _get_browser_args(self):
        return [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-sync",
            "--disable-translate",
            "--hide-scrollbars",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--js-flags=--max-old-space-size=512",
        ]

    def _init_browser(self):
        """Launches a persistent browser context with low-memory container flags."""
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
            self.page = None

        os.makedirs(self.user_data_dir, exist_ok=True)
        self.browser = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True,
            bypass_csp=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            args=self._get_browser_args()
        )
        self.page = self.browser.pages[0] if self.browser.pages else self.browser.new_page()
        self.page.on("crash", lambda p: print("[WA Bot] Warning: Page crash event triggered!"))

    def _inject_wpp(self):
        """Ensures WPPConnect script is injected and ready."""
        try:
            is_ready = self.page.evaluate("() => Boolean(window.WPP && window.WPP.isReady)")
            if is_ready:
                return True
        except Exception:
            pass

        print("[WA Bot] Injecting WPPConnect wa-js...")
        wpp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wppconnect-wa.js")
        self.page.add_script_tag(path=wpp_path)
        self.page.wait_for_function("window.WPP && window.WPP.isReady", timeout=30000)
        print("[WA Bot] WPPConnect is ready!")
        return True

    def _is_page_healthy(self):
        """Verifies if the Chromium page is open and responding to evaluations."""
        try:
            if not self.page or self.page.is_closed():
                return False
            return self.page.evaluate("() => true") is True
        except Exception:
            return False

    def _recover(self):
        """Auto-recovers from a crash or closed page by reloading WhatsApp Web."""
        print("[WA Bot] Attempting auto-recovery from crash...")
        try:
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = None

            if not self.browser or not self.browser.is_connected():
                print("[WA Bot] Chromium process disconnected. Relaunching browser context...")
                self._init_browser()
            else:
                self.page = self.browser.new_page()
                self.page.on("crash", lambda p: print("[WA Bot] Warning: Page crash event triggered!"))

            print("[WA Bot] Navigating to WhatsApp Web...")
            self.page.goto("https://web.whatsapp.com", timeout=60000)

            element = self.page.wait_for_selector("canvas, #pane-side", timeout=35000)
            if element:
                tag = element.evaluate("el => el.tagName").lower()
                if tag == "canvas":
                    self.status = "needs_scan"
                    time.sleep(2)
                    self.qr_base64 = self.page.evaluate('document.querySelector("canvas").toDataURL()')
                    return False
                else:
                    self._inject_wpp()
                    self.status = "logged_in"
                    self.qr_base64 = None
                    print("[WA Bot] Auto-recovery successful! WhatsApp Web reconnected.")
                    return True
            return False
        except Exception as e:
            print(f"[WA Bot] Auto-recovery failed: {e}")
            self.status = "error"
            return False

    def _run_loop(self):
        try:
            with sync_playwright() as p:
                self.playwright = p
                self._init_browser()

                print("[WA Bot] Loading WhatsApp Web...")
                self.page.goto("https://web.whatsapp.com", timeout=60000)

                # Check if logged in or needs QR
                while True:
                    try:
                        # Check for stop command
                        with self.cmd_queue.mutex:
                            stop_idx = None
                            for i, item in enumerate(self.cmd_queue.queue):
                                if item[0] == "STOP":
                                    stop_idx = i
                                    break
                            if stop_idx is not None:
                                self.cmd_queue.queue.pop(stop_idx)
                                if self.browser:
                                    self.browser.close()
                                self.status = "stopped"
                                return

                        # Look for the chat list (logged in) or the QR code canvas
                        element = self.page.wait_for_selector("canvas, #pane-side", timeout=3000)
                        if element:
                            tag = element.evaluate("el => el.tagName").lower()
                            if tag == "canvas":
                                self.status = "needs_scan"
                                time.sleep(2) # Let QR fully render
                                self.qr_base64 = self.page.evaluate('document.querySelector("canvas").toDataURL()')
                                time.sleep(2)
                                continue
                            else:
                                if self.status != "logged_in":
                                    self._inject_wpp()
                                self.status = "logged_in"
                                self.qr_base64 = None
                                break
                    except Exception as e:
                        err_msg = str(e)
                        if "Event loop is closed" in err_msg or "Target page, context or browser has been closed" in err_msg:
                            print("[WA Bot] Browser closed, exiting loop.")
                            break
                        if "Timeout" not in err_msg:
                            print("[WA Bot] Status loop error:", err_msg)
                            time.sleep(1)

                print("[WA Bot] Logged in successfully!")

                # Command loop
                while True:
                    try:
                        cmd, data, res_q = self.cmd_queue.get(timeout=1)
                        if cmd == "STOP":
                            break
                        elif cmd == "SCRAPE":
                            result = self._handle_scrape(data)
                            res_q.put(result)
                    except queue.Empty:
                        pass

                if self.browser:
                    self.browser.close()
                self.status = "stopped"
        except Exception as e:
            import traceback
            print("[WA Bot] FATAL ERROR:")
            traceback.print_exc()
            self.status = "error"

    def _handle_scrape(self, invite_url):
        result = {
            "Invite Link": invite_url,
            "Group Name": "-",
            "Status": "Unknown",
            "Members": "-",
            "Country": "-",
            "Admin": "-",
            "Processing Status": "Failed",
            "Timestamp of Scan": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Notes": "Failed"
        }

        try:
            if not invite_url.startswith("https://chat.whatsapp.com/"):
                result["Notes"] = "Invalid link format"
                return result

            code = invite_url.split("/")[-1].strip()
            if not code:
                result["Notes"] = "Missing invite code"
                return result

            # Verify page responsiveness before attempting scrape
            if not self._is_page_healthy():
                print("[WA Bot] Page is not healthy/responsive. Triggering auto-recovery...")
                recovered = self._recover()
                if not recovered:
                    result["Notes"] = "WhatsApp session disconnected/crashed"
                    return result

            info = None
            # Allow 1 retry on crash
            for attempt in range(2):
                try:
                    info = self.page.evaluate('''async (inviteCode) => {
                        try {
                            if (!window.WPP || !window.WPP.isReady) {
                                return { error: "WPPConnect is not ready" };
                            }
                            const timeoutPromise = new Promise((_, reject) =>
                                setTimeout(() => reject(new Error("Timeout getting group info")), 15000)
                            );
                            const data = await Promise.race([
                                WPP.group.getGroupInfoFromInviteCode(inviteCode),
                                timeoutPromise
                            ]);
                            return data;
                        } catch (e) {
                            return { error: e ? (e.message || e.toString()) : "Unknown error" };
                        }
                    }''', code)
                    break
                except Exception as e:
                    err_msg = str(e)
                    print(f"[WA Bot] Scrape evaluate error (attempt {attempt+1}): {err_msg}")
                    err_lower = err_msg.lower()
                    if "target crashed" in err_lower or "target closed" in err_lower or "closed" in err_lower or "destroyed" in err_lower:
                        if attempt == 0:
                            print("[WA Bot] Target crashed detected! Attempting recovery and retry...")
                            if self._recover():
                                continue
                    result["Notes"] = f"Error: {err_msg}"
                    return result
            
            if not info or "error" in info:
                err = info.get("error", "Unknown error") if info else "Empty response"
                err_lower = err.lower()
                if "reason: 404" in err_lower or "reason: 410" in err_lower or "not found" in err_lower or "invalid invite code" in err_lower:
                    result["Processing Status"] = "Success"
                    result["Status"] = "Expired"
                    result["Notes"] = "Invalid or expired link"
                else:
                    result["Notes"] = f"WPP Error: {err}"
                return result

            # Parse the successful response
            result["Processing Status"] = "Success"
            result["Status"] = "Active"
            result["Group Name"] = info.get("subject", "-")
            
            size = info.get("size")
            if size is not None:
                result["Members"] = str(size)
                
            # Community
            is_community = info.get("isCommunity")
            is_announce = info.get("isCommunityAnnounce")
            group_type = "Community" if is_community or is_announce else "Group"
            
            # Join rules
            join_mode = info.get("joinApprovalMode")
            join_rule = "Requires Admin Approval" if join_mode else "Anyone can join"
            
            result["Notes"] = f"{group_type} | {join_rule}"
            
            # Admin phone / Country
            owner_jid = info.get("owner")
            if owner_jid:
                if isinstance(owner_jid, dict):
                    owner_str = owner_jid.get("user", "")
                else:
                    owner_str = str(owner_jid).split("@")[0]
                
                if owner_str:
                    admin_phone = f"+{owner_str}"
                    result["Admin"] = admin_phone
                    
                    try:
                        parsed_phone = phonenumbers.parse(admin_phone)
                        region = region_code_for_country_code(parsed_phone.country_code)
                        if region:
                            country = pycountry.countries.get(alpha_2=region)
                            result["Country"] = country.name if country else region
                    except Exception:
                        result["Country"] = "Unknown"
                        
            # Short sleep between requests to avoid trigger rate limits
            time.sleep(1.5)
            return result
            
        except Exception as e:
            result["Notes"] = f"Error: {str(e)}"
            return result
