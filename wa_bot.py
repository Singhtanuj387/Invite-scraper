import os
import time
import queue
import threading
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
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

    def _run_loop(self):
        try:
            with sync_playwright() as p:
                self.playwright = p
                self.browser = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=True,
                    bypass_csp=True,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu"
                    ]
                )
                self.page = self.browser.pages[0] if self.browser.pages else self.browser.new_page()

                self.page.goto("https://web.whatsapp.com", timeout=60000)

                # Check if logged in or needs QR
                while True:
                    try:
                        # Check for stop command without discarding other commands
                        with self.cmd_queue.mutex:
                            # Find if there's a STOP command
                            stop_idx = None
                            for i, item in enumerate(self.cmd_queue.queue):
                                if item[0] == "STOP":
                                    stop_idx = i
                                    break
                            if stop_idx is not None:
                                self.cmd_queue.queue.pop(stop_idx)
                                self.browser.close()
                                self.status = "stopped"
                                return

                        # Look for the chat list (logged in) or the QR code canvas
                        # #pane-side is the left sidebar when logged in
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
                                    print("[WA Bot] Injecting WPPConnect wa-js...")
                                    self.page.add_script_tag(path="wppconnect-wa.js")
                                    # Wait for WPP to initialize
                                    self.page.wait_for_function("window.WPP && window.WPP.isReady", timeout=60000)
                                    print("[WA Bot] WPPConnect is ready!")
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
                        pass

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
            
            # Using WPPConnect's API to get group info without joining
            info = self.page.evaluate('''async (inviteCode) => {
                try {
                    const info = await WPP.group.getGroupInfoFromInviteCode(inviteCode);
                    return info;
                } catch (e) {
                    return { error: e.toString() };
                }
            }''', code)
            
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
                # e.g., "1234567890@c.us" or object
                if isinstance(owner_jid, dict):
                    owner_str = owner_jid.get("user", "")
                else:
                    owner_str = str(owner_jid).split("@")[0]
                
                if owner_str:
                    # Phone number requires a '+' for phonenumbers library
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
                        
            # Sleep a bit to avoid triggering spam filters too heavily
            time.sleep(2)
                
            return result
            
        except Exception as e:
            result["Notes"] = f"Error: {str(e)}"
            return result
