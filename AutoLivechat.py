import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk
import os
import sys
import threading
import time
import shutil
import requests
import json
import re
import io
from datetime import datetime, timedelta

# --- AREA ISI API KEY MANUAL (OPSIONAL) ---
# Jika kamu malas isi di Aplikasi, kamu bisa isi langsung di sini.
# Format: "KEY_1,KEY_2,KEY_3" (Pisah dengan koma, TANPA tanda kutip di dalam string)
DEFAULT_API_KEYS = "" 

# --- LIBRARY GOOGLE SHEETS ---
GSPREAD_AVAILABLE = False
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# --- LIBRARY GEMINI AI ---
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# --- LIBRARY SELENIUM ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

class BrowserAuditApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Audit Robot - V16.0.0 (24H Realtime & Cost Optimized)")
        self.root.geometry("1300x950")

        # --- TABEL HARGA PER MODEL (USD per 1M token) ---
        self.model_pricing = {
            "gemini-2.0-flash":    {"input": 0.10,  "output": 0.40},
            "gemini-2.0-flash-lite": {"input": 0.0, "output": 0.0},   # GRATIS
            "gemini-2.5-flash":    {"input": 0.15,  "output": 0.60},
            "gemini-3-flash-preview": {"input": 0.15, "output": 0.60},
        }
        self.price_input_1m = 0.075  # Default (akan diupdate per model)
        self.price_output_1m = 0.30
        self.usd_to_idr = 16500

        # --- SETUP PATH & FOLDER ---
        if getattr(sys, 'frozen', False):
            self.app_path = os.path.dirname(sys.executable)
        else:
            self.app_path = os.path.dirname(os.path.abspath(__file__))

        self.local_in = os.path.join(self.app_path, "Data_Chat_Masuk")
        self.local_out = os.path.join(self.app_path, "Data_Chat_Selesai")
        for f in [self.local_in, self.local_out]:
            if not os.path.exists(f): os.makedirs(f)

        # FILE CONFIG & LOGS
        self.config_file = os.path.join(self.app_path, "config.json")
        self.history_file = os.path.join(self.app_path, "download_history.json") 
        self.stats_file = os.path.join(self.app_path, "daily_stats.json")
        self.sop_file = os.path.join(self.app_path, "SOP.txt") 
        self.insight_log_file = os.path.join(self.app_path, "Jurnal_Saran_AI.txt") 

        # --- VARIABEL STATE ---
        self.driver = None
        self.is_monitoring = False      
        self.is_auto_clicking = False
        self.is_date_mode = False
        self.is_auto_today = False
        self.processed_history = set() 
        self.today_stats = {"date": "", "total": 0, "failed": 0, "total_cost": 0.0, "details": {}}
        self.last_auto_report_date = None 
        
        # VAR BARU: Indeks Key Aktif & Cooldown Tracker
        self.current_key_index = 0
        self.key_cooldowns = {}  # {key_last4: waktu_expired} - track kapan key bisa dipakai lagi

        self.load_stats() 
        self.default_sop = self.load_sop_from_file()

        # Build GUI
        self.setup_ui()
        self.load_config() 
        self.load_history() 
        
        # Scheduler Report Midnight
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

    # --- KELOMPOK UTILS ---

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            self.text_log.insert(tk.END, f"[{ts}] {msg}\n")
            self.text_log.see(tk.END)
        except: pass

    def extract_first_timestamp(self, content):
        """Ekstrak jam dari pesan pertama yang valid (bukan log sistem)."""
        lines = content.split('\n')
        pattern = r"\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s.*\s(\d{1,2}:\d{2}(?::\d{2})?\s?[AaPp][Mm])"
        for i, line in enumerate(lines):
            next_line = lines[i+1].strip().lower() if i+1 < len(lines) else ""
            if any(kw in next_line for kw in ["joined", "sent rich message", "invited", "transcript:", "---"]):
                continue
            match = re.search(pattern, line)
            if match and next_line: return match.group(1).strip()
        fallback = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?\s?[AaPp][Mm])", content)
        return fallback.group(1) if fallback else "Jam ??" 

    def extract_chat_date(self, content):
        """Ekstrak tanggal asli dari percakapan pertama Agent/Customer."""
        lines = content.split('\n')
        pattern = r"\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"
        for i, line in enumerate(lines):
            next_line = lines[i+1].strip().lower() if i+1 < len(lines) else ""
            if any(kw in next_line for kw in ["joined", "sent rich message", "invited", "transcript:", "---"]):
                continue
            match = re.search(pattern, line)
            if match and next_line:
                raw = match.group(1)
                if "/" in raw:
                    try:
                        dt = datetime.strptime(raw, "%m/%d/%Y")
                        return dt.strftime("%Y-%m-%d")
                    except: pass
                return raw
        return datetime.now().strftime("%Y-%m-%d")

    def extract_userid(self, content):
        match = re.search(r"User\s*ID\s*([^:]*):\s*(.*)", content, re.IGNORECASE)
        if match:
            web = match.group(1).strip().upper() 
            uid = match.group(2).strip()
            return f"USERID {web} : {uid}" if web else f"USERID : {uid}"
        m2 = re.search(r"USER\s*ID\s*:\s*(.*)", content, re.IGNORECASE)
        return f"USERID : {m2.group(1).strip()}" if m2 else "USERID Tidak Ditemukan"
        
    def extract_web_name(self, content):
        match = re.search(r"User\s*ID\s*\(?([A-Za-z0-9]+)\)?\s*:", content, re.IGNORECASE)
        return match.group(1).strip().upper() if match else "OTHERS"

    def compress_transcript(self, content):
        """Kompres transkrip chat untuk hemat token API (~40-60% lebih kecil)."""
        lines = content.split('\n')
        compressed = []
        last_speaker = ""
        for line in lines:
            line = line.strip()
            if not line: continue
            # Skip baris sistem/redundan
            if any(skip in line.lower() for skip in [
                "livechat conversation transcript", "----------",
                "sent rich message", "joined the chat",
                "was invited", "left the chat", "chat was",
                "was transferred", "assigned to"
            ]): continue
            # Skip baris timestamp saja (tanpa konten)
            if re.match(r"^\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),.*\)$", line): continue
            # Simplifikasi timestamp: "Nama (Tue, 1/13/2026, 04:37:02 pm)" → "Nama [04:37]"
            ts_match = re.match(r"^(.+?)\s*\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d+/\d+/\d+,?\s*(\d{1,2}:\d{2})(?::\d{2})?\s*[AaPp][Mm].*\)$", line)
            if ts_match:
                speaker = ts_match.group(1).strip()
                waktu = ts_match.group(2)
                # Skip jika speaker sama dengan sebelumnya (hemat token)
                if speaker == last_speaker:
                    continue
                last_speaker = speaker
                compressed.append(f"{speaker} [{waktu}]")
                continue
            # Skip URL gambar CDN (sudah diextract terpisah)
            if "cdn.livechat-files.com" in line: continue
            # Baris konten biasa
            compressed.append(line)
        return '\n'.join(compressed)

    def extract_images(self, content):
        url_pattern = r'(https?://[^\s]+(?:\.jpg|\.png|\.jpeg|\.gif)|https?://cdn\.livechat-files\.com[^\s]+)'
        return list(set(re.findall(url_pattern, content, re.IGNORECASE)))

    def extract_links(self, content):
        """Mengambil link (URL) dari isi chat, menyaring duplikat dan mengabaikan link file gambar/sistem."""
        url_pattern = r'(https?://[^\s\)]+)'
        all_urls = re.findall(url_pattern, content, re.IGNORECASE)
        # Filter: bukan gambar, bukan link livechat internal/sistem
        filtered_links = []
        for url in all_urls:
            url = url.strip("., ")
            if any(ext in url.lower() for ext in [".jpg", ".png", ".jpeg", ".gif", "cdn.livechat-files.com"]):
                continue
            if url not in filtered_links:
                filtered_links.append(url)
        # Batasi agar tidak terlalu banyak (Maks 5 link unik)
        return filtered_links[:5]

    def log_insight(self, userid, filename, insight_text):
        if not insight_text or len(insight_text) < 5: return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n[{ts}] {userid} ({filename})\n💡 INSIGHT: {insight_text}\n----------------------------------------"
        try:
            with open(self.insight_log_file, "a", encoding="utf-8") as f: f.write(entry)
        except: pass

    # --- FILE MANAGEMENT ---

    def cleanup_old_files(self, days=2):
        if not os.path.exists(self.local_out): return
        cutoff = time.time() - (days * 86400)
        deleted = 0
        try:
            for root, dirs, files in os.walk(self.local_out):
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.getmtime(fp) < cutoff:
                        os.remove(fp); deleted += 1
            if deleted > 0: self.log(f"🧹 Auto Cleanup: {deleted} file lama dihapus.")
        except: pass

    def manual_cleanup(self):
        self.cleanup_old_files(days=2); messagebox.showinfo("Clean", "Pembersihan selesai.")

    def load_sop_from_file(self):
        if os.path.exists(self.sop_file):
            try:
                with open(self.sop_file, "r", encoding="utf-8") as f: return f.read()
            except: return "Error SOP"
        return "[TEMPLATE SOP]\nAnda adalah Auditor Kualitas (QA)."

    def reload_sop(self):
        self.text_sop.delete("1.0", tk.END); self.text_sop.insert("1.0", self.load_sop_from_file())
        self.log("✅ SOP dimuat ulang."); messagebox.showinfo("Sukses", "SOP diperbarui.")

    def save_sop_from_ui(self):
        try:
            with open(self.sop_file, "w", encoding="utf-8") as f: f.write(self.text_sop.get("1.0", tk.END).strip())
            self.log("✅ SOP tersimpan."); messagebox.showinfo("Sukses", "SOP tersimpan.")
        except: pass

    def open_journal_file(self):
        if os.path.exists(self.insight_log_file): os.startfile(self.insight_log_file)
        else: messagebox.showinfo("Info", "Jurnal masih kosong.")

    # --- STATS ---

    def load_stats(self):
        cur = datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r") as f:
                    d = json.load(f)
                    if d.get("date") == cur: self.today_stats = d
                    else: self.today_stats = {"date": cur, "total": 0, "failed": 0, "total_cost": 0.0, "details": {}}
            except: pass
        else: self.today_stats = {"date": cur, "total": 0, "failed": 0, "total_cost": 0.0, "details": {}}

    def save_stats(self):
        try:
            with open(self.stats_file, "w") as f: json.dump(self.today_stats, f)
            self.update_stats_ui()
        except: pass

    def update_stats_ui(self):
        try:
            self.lbl_stats_total.config(text=f"Total: {self.today_stats['total']}")
            self.lbl_stats_failed.config(text=f"Salah/Note: {self.today_stats['failed']}")
            self.lbl_stats_cost.config(text=f"Biaya API: Rp {self.today_stats['total_cost']:,.2f}")
            
            breakdown = ""
            details = self.today_stats.get("details", {})
            if details:
                sorted_web = sorted(details.items(), key=lambda x: x[1]['total'], reverse=True)
                for web, dat in sorted_web:
                    breakdown += f"• {web}: Rp {dat['cost']:,.2f} ({dat['total']} chat)\n"
            
            self.text_stats_details.config(state="normal")
            self.text_stats_details.delete("1.0", tk.END); self.text_stats_details.insert("1.0", breakdown)
            self.text_stats_details.config(state="disabled")
        except: pass

    def increment_stats(self, is_noteworthy=False, web_name="OTHERS", cost=0.0):
        cur = datetime.now().strftime("%Y-%m-%d")
        if self.today_stats["date"] != cur:
            self.today_stats = {"date": cur, "total": 0, "failed": 0, "total_cost": 0.0, "details": {}}
        
        self.today_stats["total"] += 1
        self.today_stats["total_cost"] += cost
        if is_noteworthy: self.today_stats["failed"] += 1
            
        if web_name not in self.today_stats["details"]:
            self.today_stats["details"][web_name] = {"total": 0, "failed": 0, "cost": 0.0}
        
        self.today_stats["details"][web_name]["total"] += 1
        self.today_stats["details"][web_name]["cost"] += cost
        if is_noteworthy: self.today_stats["details"][web_name]["failed"] += 1
        self.save_stats()

    # --- UI SETUP ---

    def setup_ui(self):
        main_canvas = tk.Canvas(self.root)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=main_canvas.yview)
        scrollable_frame = tk.Frame(main_canvas)
        scrollable_frame.bind("<Configure>", lambda e: main_canvas.configure(scrollregion=main_canvas.bbox("all")))
        main_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        main_canvas.configure(yscrollcommand=scrollbar.set)
        main_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        main_f = tk.Frame(scrollable_frame, padx=10, pady=10); main_f.pack(fill="both", expand=True)

        # 1. SETTINGS
        lbl_s = tk.LabelFrame(main_f, text="1. Konfigurasi Sistem", padx=10, pady=5); lbl_s.pack(fill="x")
        tk.Label(lbl_s, text="Mode Audit:").grid(row=0, column=0, sticky="w")
        
        # Model options: dari termurah ke termahal
        self.combo_audit_mode = ttk.Combobox(lbl_s, values=[
            "GEMINI 2.0 FLASH LITE (GRATIS)",
            "GEMINI 1.5 FLASH-8B (TERMURAH)",
            "GEMINI 1.5 FLASH (MURAH & STABIL)",
            "GEMINI 2.0 FLASH (STANDAR)",
            "GEMINI 2.5 FLASH (PREMIUM)",
            "GEMINI 3 FLASH PREVIEW",
            "TIDAK PAKAI AI"
        ], state="readonly", width=32)
        self.combo_audit_mode.current(2); self.combo_audit_mode.grid(row=0, column=1, padx=5, pady=2, sticky="w")
        
        self.headless_var = tk.IntVar()
        tk.Checkbutton(lbl_s, text="Headless (Mode Hantu)", variable=self.headless_var).grid(row=0, column=2, sticky="w")
        tk.Label(lbl_s, text="Gemini API Key(s):").grid(row=1, column=0, sticky="w")
        
        # NOTE: Tooltip sederhana di UI
        tk.Label(lbl_s, text="(Pisah koma jika banyak)", font=("Arial", 7, "italic"), fg="gray").place(x=10, y=55)
        
        self.entry_api = tk.Entry(lbl_s, width=40, show="*"); self.entry_api.grid(row=1, column=1, padx=5, pady=2)
        tk.Label(lbl_s, text="Tele Token:").grid(row=2, column=0, sticky="w")
        self.entry_tele_token = tk.Entry(lbl_s, width=40); self.entry_tele_token.grid(row=2, column=1, padx=5, pady=2)
        tk.Label(lbl_s, text="Tele Chat ID:").grid(row=2, column=2, sticky="w")
        self.entry_tele_chatid = tk.Entry(lbl_s, width=30); self.entry_tele_chatid.grid(row=2, column=3, padx=5, pady=2)
        tk.Label(lbl_s, text="GSheet URL:").grid(row=3, column=0, sticky="w")
        self.entry_gsheet_name = tk.Entry(lbl_s, width=40); self.entry_gsheet_name.grid(row=3, column=1, padx=5, pady=2)
        tk.Label(lbl_s, text="JSON Path:").grid(row=3, column=2, sticky="w")
        self.entry_gsheet_json = tk.Entry(lbl_s, width=22); self.entry_gsheet_json.grid(row=3, column=3, padx=5, pady=2)
        tk.Button(lbl_s, text="📂", command=self.browse_gsheet_json, width=3).place(x=1030, y=85)
        tk.Button(lbl_s, text="💾 SIMPAN CONFIG", command=self.save_config, bg="#FFC107").grid(row=4, column=0, columnspan=2, pady=5, sticky="ew")
        tk.Button(lbl_s, text="🔌 TEST GSHEET", command=self.test_gsheet_connection, bg="#4CAF50", fg="white").grid(row=4, column=2, columnspan=2, pady=5, sticky="ew")

        # 2. BROWSER
        login_f = tk.LabelFrame(main_f, text="2. Akun Livechat", padx=10, pady=10); login_f.pack(fill="x", pady=5)
        tk.Label(login_f, text="Email:").pack(side="left"); self.entry_lc_email = tk.Entry(login_f, width=25); self.entry_lc_email.pack(side="left", padx=5)
        tk.Label(login_f, text="Pass:").pack(side="left"); self.entry_lc_password = tk.Entry(login_f, width=15, show="*"); self.entry_lc_password.pack(side="left", padx=5)
        tk.Button(login_f, text="🌐 BUKA CHROME & LOGIN", command=self.open_chrome, bg="#2196F3", fg="white").pack(side="left", padx=5)

        # 3. ACTIONS
        act_f = tk.Frame(main_f); act_f.pack(fill="x", pady=5)
        down_f = tk.LabelFrame(act_f, text="3. Downloader Loop", padx=10, pady=10, bg="#E3F2FD"); down_f.pack(side="left", fill="both", expand=True, padx=2)
        tk.Label(down_f, text="Interval(s):", bg="#E3F2FD").pack(side="left"); self.entry_interval = tk.Entry(down_f, width=5); self.entry_interval.insert(0, "300"); self.entry_interval.pack(side="left", padx=2)
        self.btn_auto_click = tk.Button(down_f, text="▶ START LOOP", command=self.toggle_auto_clicker, bg="#673AB7", fg="white"); self.btn_auto_click.pack(side="left", padx=5)
        self.lbl_history_count = tk.Label(down_f, text="History: 0", bg="#E3F2FD"); self.lbl_history_count.pack(side="left", padx=5)
        tk.Button(down_f, text="🗑 Reset", command=self.reset_history, bg="#FF5722", fg="white").pack(side="left")
        date_f = tk.LabelFrame(act_f, text="4. Download By Date", padx=10, pady=10, bg="#FFEBEE"); date_f.pack(side="left", fill="both", expand=True, padx=2)
        tk.Button(date_f, text="📅 YESTERDAY", command=lambda: self.run_date_mode("Yesterday"), bg="#FF5722", fg="white").pack(side="left", padx=5)
        tk.Button(date_f, text="📆 TODAY", command=lambda: self.run_date_mode("Today"), bg="#4CAF50", fg="white").pack(side="left", padx=5)
        self.btn_auto_today = tk.Button(date_f, text="🔄 AUTO TODAY 24H", command=self.toggle_auto_today, bg="#1565C0", fg="white"); self.btn_auto_today.pack(side="left", padx=5)

        # 5. MONITOR
        mon_f = tk.PanedWindow(main_f, orient="horizontal"); mon_f.pack(fill="both", expand=True, pady=5)
        left_p = tk.Frame(mon_f); mon_f.add(left_p, width=400)
        stat_f = tk.LabelFrame(left_p, text="📊 STATISTIK", bg="#FFF3E0", padx=10, pady=10); stat_f.pack(fill="x", pady=5)
        self.lbl_stats_total = tk.Label(stat_f, text="Total: 0", bg="#FFF3E0"); self.lbl_stats_total.pack(anchor="w")
        self.lbl_stats_failed = tk.Label(stat_f, text="Salah/Note: 0", bg="#FFF3E0", fg="red"); self.lbl_stats_failed.pack(anchor="w")
        self.lbl_stats_cost = tk.Label(stat_f, text="Biaya API: Rp 0", bg="#FFF3E0", fg="blue", font=("Arial", 10, "bold")); self.lbl_stats_cost.pack(anchor="w", pady=5)
        self.text_stats_details = tk.Text(stat_f, height=8, bg="white", font=("Arial", 8), state="disabled"); self.text_stats_details.pack(fill="x", pady=2)
        
        btn_box = tk.Frame(stat_f, bg="#FFF3E0"); btn_box.pack(fill="x")
        tk.Button(btn_box, text="📒 JURNAL", command=self.open_journal_file, bg="#795548", fg="white").pack(side="left", fill="x", expand=True, padx=1)
        tk.Button(btn_box, text="📤 REKAP", command=self.send_rekap_telegram, bg="#2196F3", fg="white").pack(side="left", fill="x", expand=True, padx=1)
        tk.Button(btn_box, text="🧹 CLEAN", command=self.manual_cleanup, bg="#FF9800", fg="white").pack(side="left", fill="x", expand=True, padx=1)

        tk.Label(left_p, text="SOP (Aturan AI):").pack(anchor="w")
        self.text_sop = scrolledtext.ScrolledText(left_p, height=8); self.text_sop.pack(fill="x", pady=5); self.text_sop.insert("1.0", self.default_sop)
        sop_btns = tk.Frame(left_p); sop_btns.pack(fill="x")
        tk.Button(sop_btns, text="🔄 RELOAD", command=self.reload_sop, bg="#2196F3", fg="white").pack(side="left", fill="x", expand=True)
        tk.Button(sop_btns, text="💾 SAVE", command=self.save_sop_from_ui, bg="#FFC107").pack(side="left", fill="x", expand=True)
        self.btn_monitor = tk.Button(left_p, text="🤖 START ROBOT", command=self.toggle_monitoring, bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), height=3); self.btn_monitor.pack(fill="x", pady=10)
        
        right_p = tk.Frame(mon_f); mon_f.add(right_p)
        self.text_log = scrolledtext.ScrolledText(right_p, bg="black", fg="#00FF00", font=("Consolas", 9)); self.text_log.pack(fill="both", expand=True)

    # --- SELENIUM CORE ---

    def open_chrome(self):
        try:
            options = webdriver.ChromeOptions()
            if self.headless_var.get() == 1: options.add_argument("--headless=new"); options.add_argument("--disable-gpu")
            options.add_argument("--start-maximized")
            # Suppress log noise (TensorFlow, GCM, etc.)
            options.add_argument("--log-level=3")
            options.add_argument("--disable-logging")
            options.add_argument("--disable-features=MediaRouter")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
            download_dir = os.path.abspath(self.local_in).replace("/", "\\")
            if not os.path.exists(download_dir): os.makedirs(download_dir)
            self.log(f"📂 Download folder: {download_dir}")
            prefs = {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "profile.default_content_setting_values.automatic_downloads": 1
            }
            options.add_experimental_option("prefs", prefs)
            # ChromeDriver: coba install, jika Access Denied pakai yang sudah ada
            try:
                driver_path = ChromeDriverManager().install()
            except PermissionError:
                import glob as g
                wdm_base = os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver")
                found = g.glob(os.path.join(wdm_base, "**", "chromedriver.exe"), recursive=True)
                if found:
                    driver_path = found[0]
                    self.log(f"⚠️ Pakai chromedriver cache: {driver_path}")
                else:
                    raise FileNotFoundError("ChromeDriver tidak ditemukan. Hapus folder .wdm lalu coba lagi.")
            service = Service(driver_path)
            service.creation_flags = 0x08000000  # CREATE_NO_WINDOW - hide chromedriver console
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_script_timeout(120)  # 120 detik untuk execute_script (default 30s terlalu pendek)
            self.driver.get("https://my.livechatinc.com/archives")
            email = self.entry_lc_email.get(); pss = self.entry_lc_password.get()
            if email and pss: threading.Thread(target=self.perform_auto_login, args=(email, pss), daemon=True).start()
            self.log("🌐 Chrome dibuka.")
        except Exception as e: self.log(f"Error: {e}")

    def perform_auto_login(self, email, password):
        try:
            wait = WebDriverWait(self.driver, 15)
            email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
            email_field.send_keys(email); self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            pass_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
            pass_field.send_keys(password); self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            time.sleep(5); self.driver.get("https://my.livechatinc.com/archives")
        except: self.log("⚠️ Login manual dibutuhkan.")

    def toggle_auto_clicker(self):
        if self.is_auto_clicking: self.is_auto_clicking = False; self.btn_auto_click.config(text="▶ START LOOP", bg="#673AB7")
        else:
            if not self.driver: messagebox.showerror("Error", "Buka Chrome!"); return
            self.is_auto_clicking = True; self.btn_auto_click.config(text="⏹ STOP LOOP", bg="#B71C1C")
            try: interval = int(self.entry_interval.get())
            except: interval = 300
            threading.Thread(target=self.run_clicker_loop, args=(interval,), daemon=True).start()

    def scroll_load_all_chats(self, sel_list, max_scroll=1000):
        """Scroll sampai semua chat termuat (max 3000+). Turbo mode: batch scroll."""
        last_count = 0
        stable_rounds = 0

        for scroll_num in range(max_scroll):
            if not self.is_auto_clicking and not self.is_date_mode and not self.is_monitoring and not self.is_auto_today:
                break

            # TURBO: 3x rapid scroll sebelum cek count (hemat waktu 3x lipat)
            for _ in range(3):
                try:
                    list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                    self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                except Exception:
                    try:
                        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    except Exception:
                        pass
                time.sleep(0.3)  # Jeda minimal antar rapid scroll

            time.sleep(0.5)  # Jeda singkat sebelum hitung

            try:
                current_count = len(self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li"))
            except Exception:
                continue

            if current_count == last_count:
                stable_rounds += 1
                if stable_rounds >= 5:
                    break  # 5x batch tidak bertambah = selesai
            else:
                stable_rounds = 0
                if current_count % 200 == 0 or current_count - last_count > 30:
                    self.log(f"📜 {current_count} chat termuat...")
            last_count = current_count

        self.log(f"📋 Total termuat: {last_count} chat.")
        return last_count

    def find_chat_list_selector(self):
        """Cari selector list chat yang aktif di halaman (auto-detect)."""
        candidates = [
            "#archives > div > div.css-og8kck > div > div > div > div.css-1j8yl8o > div > ul",
            "div[class*='css-1j8yl8o'] ul",
            "#archives ul",
            "[data-testid='archive-list'] ul",
            "div[class*='archive'] ul",
        ]
        for sel in candidates:
            try:
                items = self.driver.find_elements(By.CSS_SELECTOR, f"{sel} li")
                if len(items) > 0:
                    self.log(f"🔍 Selector ditemukan: {sel} ({len(items)} item)")
                    return sel
            except: continue
        return None

    def run_clicker_loop(self, interval):
        self.log("🔴 AUTO DOWNLOADER AKTIF.")
        while self.is_auto_clicking:
            try:
                self.driver.get("https://my.livechatinc.com/archives"); time.sleep(5)
                self.cleanup_old_files(days=2)

                # Auto-detect selector yang benar
                SEL_LIST = self.find_chat_list_selector()
                if not SEL_LIST:
                    self.log("⚠️ List chat tidak ditemukan. Tunggu & retry...")
                    time.sleep(10)
                    continue

                # Scroll sampai semua chat termuat
                total_loaded = self.scroll_load_all_chats(SEL_LIST)
                if total_loaded == 0:
                    self.log("⚠️ Tidak ada chat. Tunggu...")
                    time.sleep(interval)
                    continue

                downloaded = 0
                skipped = 0
                failed = 0
                batch_new_ids = []
                BATCH_SIZE = 25

                for i in range(total_loaded):
                    if not self.is_auto_clicking: break

                    retries = 3
                    success = False
                    for attempt in range(retries):
                        try:
                            items = self.driver.find_elements(By.CSS_SELECTOR, f"{SEL_LIST} li")
                            if i >= len(items):
                                self.log(f"⚠️ Item {i+1}/{total_loaded} diluar jangkauan, skip.")
                                break
                            item = items[i]
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
                            time.sleep(0.5)
                            item.click(); time.sleep(2)

                            c_id = self.driver.current_url.split("/")[-1].split("?")[0]
                            if not c_id or c_id == "archives":
                                time.sleep(1); continue

                            if c_id not in self.processed_history:
                                dl_ok = self.perform_download(c_id)
                                if dl_ok:
                                    downloaded += 1
                                    batch_new_ids.append(c_id)
                                    if len(batch_new_ids) >= BATCH_SIZE:
                                        self.save_history()
                                        self.log(f"💾 Progress: {downloaded} downloaded, {skipped} skip, {failed} gagal ({i+1}/{total_loaded})")
                                        batch_new_ids.clear()
                                else:
                                    failed += 1
                            else:
                                skipped += 1
                            success = True
                            break
                        except StaleElementReferenceException:
                            self.log(f"🔄 Stale item {i+1}, retry {attempt+1}/{retries}...")
                            time.sleep(2)
                        except (IndexError, NoSuchElementException):
                            break
                        except Exception as e:
                            self.log(f"⚠️ Error item {i+1}: {str(e)[:60]}")
                            break

                    if not success and i < total_loaded - 1:
                        failed += 1

                if batch_new_ids: self.save_history()
                self.log(f"✅ SELESAI SCAN: ⬇️ {downloaded} baru | ⏭ {skipped} sudah ada | ❌ {failed} gagal | Total: {total_loaded}")
            except Exception as e: self.log(f"❌ Error Downloader: {e}")
            time.sleep(interval)

    def run_date_mode(self, mode):
        if not self.driver: return
        self.is_date_mode = True; threading.Thread(target=self.date_mode_logic, args=(mode,), daemon=True).start()

    def apply_livechat_filter(self, mode):
        """Klik Add Filter → Date → Today/Yesterday di UI LiveChat archives."""
        try:
            self.log(f"🔍 Mencari tombol filter di LiveChat...")

            # STEP 1: Klik "Add filter" / "Filter" / ikon filter
            filter_btn = None
            filter_searches = [
                # By text
                ("xpath", "//*[contains(text(), 'Add filter')]"),
                ("xpath", "//*[contains(text(), 'add filter')]"),
                ("xpath", "//*[contains(text(), 'Filter')]"),
                ("xpath", "//button[contains(text(), 'Filter')]"),
                # By aria-label
                ("css", "[aria-label*='filter' i]"),
                ("css", "[aria-label*='Filter']"),
                # By data-testid
                ("css", "[data-testid*='filter']"),
                ("css", "[data-testid*='Filter']"),
                # By class
                ("css", "button[class*='filter']"),
                ("css", "div[class*='filter'] button"),
                ("css", "[class*='FilterButton']"),
                ("css", "[class*='add-filter']"),
            ]
            for method, selector in filter_searches:
                try:
                    if method == "xpath":
                        el = self.driver.find_element(By.XPATH, selector)
                    else:
                        el = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if el and el.is_displayed():
                        filter_btn = el
                        self.log(f"✅ Tombol filter ditemukan: {selector[:50]}")
                        break
                except: continue

            if not filter_btn:
                # Debug: log semua button yang ada di halaman
                try:
                    buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    btn_texts = [b.text.strip() for b in buttons[:20] if b.text.strip()]
                    self.log(f"🔍 Buttons di halaman: {btn_texts}")
                except: pass
                self.log("⚠️ Tombol filter tidak ditemukan.")
                return False

            filter_btn.click()
            time.sleep(2)

            # STEP 2: Pilih "Date" dari dropdown filter
            date_option = None
            date_searches = [
                ("xpath", "//*[contains(text(), 'Date')]"),
                ("xpath", "//*[contains(text(), 'date')]"),
                ("xpath", "//li[contains(text(), 'Date')]"),
                ("xpath", "//div[contains(text(), 'Date')]"),
                ("xpath", "//span[contains(text(), 'Date')]"),
                ("css", "[data-testid*='date']"),
            ]
            for method, selector in date_searches:
                try:
                    if method == "xpath":
                        els = self.driver.find_elements(By.XPATH, selector)
                    else:
                        els = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for el in els:
                        if el.is_displayed() and el.text.strip().lower() in ['date', 'date range', 'tanggal']:
                            date_option = el; break
                    if date_option: break
                except: continue

            if not date_option:
                # Debug: log apa yang muncul di dropdown
                try:
                    visible = self.driver.find_elements(By.XPATH,
                        "//*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop') or contains(@role,'menu') or contains(@role,'listbox')]//li | //*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop')]//div")
                    menu_texts = [v.text.strip() for v in visible if v.text.strip() and len(v.text.strip()) < 30][:15]
                    self.log(f"🔍 Filter options: {menu_texts}")
                except: pass
                self.log("⚠️ Opsi 'Date' tidak ditemukan di filter.")
                try: self.driver.find_element(By.TAG_NAME, "body").click()
                except: pass
                return False

            date_option.click()
            time.sleep(2)
            self.log(f"✅ Filter 'Date' diklik.")

            # STEP 3: Pilih Today / Yesterday
            target_text = "Today" if mode == "Today" else "Yesterday"
            period_btn = None
            period_searches = [
                f"//*[text()='{target_text}']",
                f"//*[contains(text(), '{target_text}')]",
                f"//button[contains(text(), '{target_text}')]",
                f"//li[contains(text(), '{target_text}')]",
                f"//span[contains(text(), '{target_text}')]",
                f"//div[contains(text(), '{target_text}')]",
            ]
            for xpath in period_searches:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    if el and el.is_displayed():
                        period_btn = el; break
                except: continue

            if not period_btn:
                # Debug
                try:
                    visible = self.driver.find_elements(By.XPATH, "//*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop') or contains(@class,'calendar') or contains(@class,'picker')]//button | //*[contains(@class,'popup') or contains(@class,'picker')]//li | //*[contains(@class,'popup') or contains(@class,'picker')]//span")
                    opts = [v.text.strip() for v in visible if v.text.strip() and len(v.text.strip()) < 30][:15]
                    self.log(f"🔍 Date options: {opts}")
                except: pass
                self.log(f"⚠️ Opsi '{target_text}' tidak ditemukan.")
                try: self.driver.find_element(By.TAG_NAME, "body").click()
                except: pass
                return False

            period_btn.click()
            time.sleep(3)

            # STEP 4: Klik Apply / confirm jika ada
            try:
                apply_btn = self.driver.find_element(By.XPATH,
                    "//button[contains(text(), 'Apply') or contains(text(), 'Confirm') or contains(text(), 'OK') or contains(text(), 'Save')]")
                if apply_btn.is_displayed():
                    apply_btn.click()
                    time.sleep(2)
            except: pass  # Mungkin auto-apply tanpa tombol

            self.log(f"✅ Filter '{target_text}' berhasil di-apply!")
            return True

        except Exception as e:
            self.log(f"⚠️ Error apply filter: {str(e)[:60]}")
            try: self.driver.find_element(By.TAG_NAME, "body").click()
            except: pass
            return False

    def check_chat_date_on_page(self, target_date_str):
        """Cek tanggal chat dari halaman detail yang sedang terbuka.
        target_date_str format: 'M/D/YYYY' (sesuai LiveChat) atau 'YYYY-MM-DD'.
        Return True jika cocok."""
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            # LiveChat format: "3/11/2026" atau "03/11/2026" atau "Mar 11, 2026"
            # Cek semua format yang mungkin
            if target_date_str in page_text:
                return True
            # Coba juga format tanpa leading zero
            parts = target_date_str.split("/")
            if len(parts) == 3:
                no_zero = f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
                if no_zero in page_text:
                    return True
            return False
        except:
            return True  # Jika gagal cek, anggap cocok (download saja)

    def check_item_date_in_list(self, item, target_date_str):
        """Cek tanggal dari teks yang terlihat di list item."""
        try:
            item_text = item.text
            if not item_text: return None
            # Cek format "3/11/2026", "03/11/2026"
            if target_date_str in item_text: return True
            parts = target_date_str.split("/")
            if len(parts) == 3:
                no_zero = f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
                if no_zero in item_text: return True
                # Cek juga format YYYY-MM-DD
                iso_date = f"{parts[2]}-{parts[0]}-{parts[1]}"
                if iso_date in item_text: return True
            # Cek keyword waktu relatif
            item_lower = item_text.lower()
            if "today" in item_lower or "hari ini" in item_lower:
                return target_date_str == datetime.now().strftime("%m/%d/%Y")
            if "yesterday" in item_lower or "kemarin" in item_lower:
                return target_date_str == (datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y")
            # Cek format "Mar 11" / "11 Mar" / "March 11"
            try:
                from calendar import month_abbr, month_name
                m_idx = int(parts[0]); d_val = int(parts[1])
                abbr = month_abbr[m_idx]  # "Mar"
                full = month_name[m_idx]   # "March"
                if f"{abbr} {d_val}" in item_text or f"{d_val} {abbr}" in item_text: return True
                if f"{full} {d_val}" in item_text or f"{d_val} {full}" in item_text: return True
            except: pass
            # Cek waktu saja (format "18:30", "6:30 PM") — jika hanya ada waktu tanpa tanggal, kemungkinan hari ini
            if re.search(r'\d{1,2}:\d{2}', item_text) and not re.search(r'\d{1,2}/\d{1,2}', item_text):
                # Item hanya menampilkan waktu tanpa tanggal = kemungkinan hari ini
                if target_date_str == datetime.now().strftime("%m/%d/%Y"):
                    return True
            return None
        except:
            return None

    def smart_scroll_by_date(self, sel_list, target_date_lc):
        """Scroll hanya sampai chat tanggal target habis. Cepat."""
        last_count = 0
        consecutive_old = 0
        logged_sample = False

        for scroll_num in range(500):
            if not self.is_date_mode: break

            try:
                list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
            except:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            items = self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li")
            current_count = len(items)

            if current_count > last_count:
                # Debug: log contoh teks item (hanya sekali di awal)
                if not logged_sample and current_count > 5:
                    try:
                        sample_texts = []
                        for s in [0, 1, current_count-2, current_count-1]:
                            txt = items[s].text.replace('\n', ' | ')[:80]
                            sample_texts.append(f"[{s}] {txt}")
                        self.log(f"🔍 Sample item: {'; '.join(sample_texts)}")
                        self.log(f"🔍 Target date: {target_date_lc}")
                        logged_sample = True
                    except: pass

                # Cek 3 item terakhir — masih tanggal target?
                old_count = 0
                for item in items[-3:]:
                    check = self.check_item_date_in_list(item, target_date_lc)
                    if check is False: old_count += 1

                if old_count >= 2:
                    consecutive_old += 1
                else:
                    consecutive_old = 0

                if current_count % 100 == 0:
                    self.log(f"📜 {current_count} chat termuat...")

                if consecutive_old >= 2:
                    self.log(f"📋 Melewati tanggal target. Stop di {current_count} chat.")
                    break
                last_count = current_count
            else:
                # Tidak ada item baru, tunggu 1x lagi sebelum stop
                time.sleep(1)
                retry_count = len(self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li"))
                if retry_count == current_count:
                    break
                last_count = retry_count

        total = len(self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li"))
        self.log(f"📋 Total termuat: {total} chat.")
        return total

    def date_mode_logic(self, mode):
        try:
            target_date = datetime.now() if mode == "Today" else datetime.now() - timedelta(days=1)
            date_display = target_date.strftime("%Y-%m-%d")

            # --- NAVIGASI KE ARCHIVES ---
            self.driver.get("https://my.livechatinc.com/archives")
            time.sleep(5)
            self.log(f"🗓️ {mode} ({date_display}): Buka archives...")

            # --- APPLY FILTER VIA UI LIVECHAT ---
            filter_ok = self.apply_livechat_filter(mode)

            if filter_ok:
                self.log(f"✅ Filter {mode} aktif. Semua chat di halaman = {date_display}")
                time.sleep(3)
            else:
                self.log(f"⚠️ Filter UI gagal. Chat mungkin campur tanggal.")

            # --- DETECT LIST ---
            SEL_LIST = self.find_chat_list_selector()
            if not SEL_LIST:
                time.sleep(5)
                SEL_LIST = self.find_chat_list_selector()
            if not SEL_LIST:
                self.log(f"⚠️ Tidak ada chat ditemukan.")
                self.is_date_mode = False; return

            # === SCROLL + DOWNLOAD BERSAMAAN ===
            # Proses: scroll batch → download item baru → scroll lagi → repeat
            downloaded = 0; skipped = 0; failed = 0
            processed_idx = 0       # index item terakhir yang sudah diproses
            stable_rounds = 0       # hitung berapa kali scroll tidak menambah item baru
            last_total = 0

            self.log(f"🚀 Mulai scroll + download bersamaan...")

            while self.is_date_mode:
                # --- FASE 1: Scroll untuk load item baru (3x rapid scroll) ---
                for _ in range(3):
                    try:
                        list_el = self.driver.find_element(By.CSS_SELECTOR, SEL_LIST)
                        self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                    except Exception:
                        try:
                            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        except Exception:
                            pass
                    time.sleep(0.3)
                time.sleep(0.5)

                # Hitung total item sekarang
                try:
                    current_items = self.driver.find_elements(By.CSS_SELECTOR, f"{SEL_LIST} li")
                    current_total = len(current_items)
                except Exception:
                    continue

                if current_total > last_total:
                    self.log(f"📜 {current_total} chat termuat...")
                    stable_rounds = 0
                else:
                    stable_rounds += 1

                # --- FASE 2: Download semua item baru (dari processed_idx sampai current_total) ---
                while processed_idx < current_total and self.is_date_mode:
                    try:
                        # Re-fetch items setiap kali (anti stale)
                        items = self.driver.find_elements(By.CSS_SELECTOR, f"{SEL_LIST} li")
                        if processed_idx >= len(items):
                            break

                        item = items[processed_idx]

                        # Log progress setiap 20 item
                        if processed_idx % 20 == 0:
                            self.log(f"📌 {processed_idx+1}/{current_total} | ⬇️{downloaded} ⏭{skipped} ❌{failed}")

                        # Scroll ke item & klik via JS
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", item)
                        except Exception:
                            pass
                        time.sleep(0.2)

                        try:
                            self.driver.execute_script("arguments[0].click();", item)
                        except Exception:
                            try:
                                item.click()
                            except Exception:
                                failed += 1; processed_idx += 1
                                continue

                        time.sleep(1.5)
                        c_id = self.driver.current_url.split("/")[-1].split("?")[0]
                        if not c_id or c_id == "archives":
                            processed_idx += 1
                            continue

                        if c_id not in self.processed_history:
                            if self.perform_download(c_id):
                                downloaded += 1
                            else:
                                failed += 1
                        else:
                            skipped += 1

                    except StaleElementReferenceException:
                        pass  # Akan re-fetch di iterasi berikutnya
                    except Exception as e:
                        self.log(f"⚠️ Item {processed_idx}: {str(e)[:60]}")
                        failed += 1

                    processed_idx += 1

                    if downloaded > 0 and downloaded % 25 == 0:
                        self.save_history()
                        self.log(f"💾 Saved: ⬇️{downloaded} / ~{current_total}")

                last_total = current_total

                # Selesai jika sudah 5x scroll tanpa item baru DAN semua item sudah diproses
                if stable_rounds >= 5 and processed_idx >= current_total:
                    break

            self.save_history()
            self.log(f"✅ {mode} ({date_display}) SELESAI: ⬇️ {downloaded} | ⏭ {skipped} skip | ❌ {failed} gagal | Total: {last_total}")
            self.is_date_mode = False
        except Exception as e:
            self.log(f"❌ Error Date Mode: {e}")
            self.is_date_mode = False

    # --- AUTO TODAY 24H: Loop lintas hari ---

    def toggle_auto_today(self):
        if self.is_auto_today:
            self.is_auto_today = False
            self.is_date_mode = False  # Stop juga date_mode inner loop
            self.btn_auto_today.config(text="🔄 AUTO TODAY 24H", bg="#1565C0")
            self.log("⏹ AUTO TODAY 24H dihentikan.")
        else:
            if not self.driver:
                messagebox.showerror("Error", "Buka Chrome dulu!")
                return
            self.is_auto_today = True
            self.btn_auto_today.config(text="⏹ STOP AUTO 24H", bg="#B71C1C")
            threading.Thread(target=self.run_auto_today_loop, daemon=True).start()

    def run_auto_today_loop(self):
        """Loop: download semua chat hari ini, cek chat baru tiap 5 menit, lanjut ke hari berikutnya."""
        self.log("🔄 AUTO TODAY 24H AKTIF — download, cek chat baru, & lanjut ke hari berikutnya.")
        RECHECK_INTERVAL = 300  # 5 menit

        while self.is_auto_today:
            current_date = datetime.now().strftime("%Y-%m-%d")
            self.log(f"📆 Mulai download chat tanggal: {current_date}")

            # --- Loop: download + cek chat baru selama masih hari yang sama ---
            while self.is_auto_today:
                try:
                    downloaded = self._download_one_day("Today")
                except Exception as e:
                    self.log(f"❌ Error download {current_date}: {e}")
                    downloaded = 0

                if not self.is_auto_today:
                    break

                # Cek apakah sudah ganti hari
                new_date = datetime.now().strftime("%Y-%m-%d")
                if new_date != current_date:
                    self.log(f"🌅 Hari baru terdeteksi: {new_date} — lanjut download!")
                    break

                # Masih hari yang sama — tunggu 5 menit lalu cek chat baru
                if downloaded and downloaded > 0:
                    self.log(f"✅ {downloaded} chat baru didownload. Cek lagi dalam 5 menit...")
                else:
                    self.log(f"💤 Tidak ada chat baru. Cek lagi dalam 5 menit...")

                for _ in range(RECHECK_INTERVAL // 5):
                    if not self.is_auto_today:
                        break
                    # Cek ganti hari juga selama tunggu
                    if datetime.now().strftime("%Y-%m-%d") != current_date:
                        break
                    time.sleep(5)

        self.is_auto_today = False
        self.btn_auto_today.config(text="🔄 AUTO TODAY 24H", bg="#1565C0")
        self.log("⏹ AUTO TODAY 24H berhenti total.")

    def _download_one_day(self, mode):
        """Download semua chat untuk 1 hari (Today/Yesterday). Reuse dari date_mode_logic tapi tanpa set is_date_mode=False di akhir."""
        try:
            target_date = datetime.now() if mode == "Today" else datetime.now() - timedelta(days=1)
            date_display = target_date.strftime("%Y-%m-%d")

            self.driver.get("https://my.livechatinc.com/archives")
            time.sleep(5)
            self.log(f"🗓️ {mode} ({date_display}): Buka archives...")

            filter_ok = self.apply_livechat_filter(mode)
            if filter_ok:
                self.log(f"✅ Filter {mode} aktif. Semua chat di halaman = {date_display}")
                time.sleep(3)
            else:
                self.log(f"⚠️ Filter UI gagal. Chat mungkin campur tanggal.")

            SEL_LIST = self.find_chat_list_selector()
            if not SEL_LIST:
                time.sleep(5)
                SEL_LIST = self.find_chat_list_selector()
            if not SEL_LIST:
                self.log(f"⚠️ Tidak ada chat ditemukan untuk {date_display}.")
                return

            downloaded = 0; skipped = 0; failed = 0
            processed_idx = 0
            stable_rounds = 0
            last_total = 0

            self.log(f"🚀 Mulai scroll + download bersamaan untuk {date_display}...")

            # --- Scroll batch + download item yang visible (atas ke bawah) ---
            while self.is_auto_today:
                # Scroll batch (3x rapid scroll)
                for _ in range(3):
                    try:
                        list_el = self.driver.find_element(By.CSS_SELECTOR, SEL_LIST)
                        self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                    except Exception:
                        try:
                            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        except Exception:
                            pass
                    time.sleep(0.3)
                time.sleep(0.5)

                # Hitung total item sekarang
                try:
                    current_items = self.driver.find_elements(By.CSS_SELECTOR, f"{SEL_LIST} li")
                    current_total = len(current_items)
                except Exception:
                    continue

                if current_total > last_total:
                    self.log(f"📜 {current_total} chat termuat...")
                    stable_rounds = 0
                else:
                    stable_rounds += 1

                # Download semua item baru (dari processed_idx sampai current_total)
                while processed_idx < current_total and self.is_auto_today:
                    result = self._try_download_item(SEL_LIST, processed_idx, current_total, downloaded, skipped, failed)
                    if result == "downloaded":
                        downloaded += 1
                    elif result == "skipped":
                        skipped += 1
                    else:
                        failed += 1

                    processed_idx += 1

                    if downloaded > 0 and downloaded % 25 == 0:
                        self.save_history()
                        self.log(f"💾 Saved: ⬇️{downloaded} / ~{current_total}")

                last_total = current_total

                # Selesai jika sudah 5x scroll tanpa item baru DAN semua item sudah diproses
                if stable_rounds >= 5 and processed_idx >= current_total:
                    break

            self.save_history()
            self.log(f"✅ {mode} ({date_display}) SELESAI: ⬇️ {downloaded} | ⏭ {skipped} skip | ❌ {failed} gagal | Total: {last_total}")
            return downloaded

        except Exception as e:
            self.log(f"❌ Error download hari {mode}: {e}")
            return 0

    def _try_download_item(self, sel_list, idx, total, downloaded, skipped, failed):
        """Coba download 1 item dari list. Return: 'downloaded', 'skipped', 'failed'."""
        MAX_RETRIES = 3
        for attempt in range(MAX_RETRIES):
            try:
                # Scroll container ke posisi item dulu (penting untuk virtual list)
                try:
                    list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                    # Estimasi posisi scroll berdasarkan index
                    self.driver.execute_script(
                        "var el=arguments[0]; var h=el.scrollHeight; var t=arguments[1]; var total=arguments[2]; "
                        "el.scrollTop = Math.max(0, (h * t / total) - 200);", list_el, idx, max(total, 1))
                    time.sleep(0.5)
                except Exception:
                    pass

                items = self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li")
                if idx >= len(items):
                    return "failed"
                item = items[idx]

                if idx % 20 == 0:
                    self.log(f"📌 {idx+1}/{total} | ⬇️{downloaded} ⏭{skipped} ❌{failed}")

                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
                except Exception:
                    pass
                time.sleep(0.3)

                try:
                    self.driver.execute_script("arguments[0].click();", item)
                except Exception:
                    try:
                        item.click()
                    except Exception:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(1)
                            continue
                        return "failed"

                time.sleep(1.5)
                c_id = self.driver.current_url.split("/")[-1].split("?")[0]
                if not c_id or c_id == "archives":
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(1)
                        continue
                    return "failed"

                if c_id not in self.processed_history:
                    if self.perform_download(c_id):
                        return "downloaded"
                    else:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(1)
                            continue
                        return "failed"
                else:
                    return "skipped"

            except StaleElementReferenceException:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)
                    continue
                return "failed"
            except Exception as e:
                self.log(f"⚠️ Item {idx} attempt {attempt+1}: {str(e)[:60]}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)
                    continue
                return "failed"
        return "failed"

    def perform_download(self, chat_id):
        try:
            # Auto-detect tombol menu (3 dots / more options)
            menu_selectors = [
                "div[class*='css-1ted3pi']",
                "button[aria-label='More actions']",
                "button[aria-label='Actions']",
                "[data-testid='archive-actions']",
                "div[class*='actions'] button",
                "button[class*='more']",
            ]
            menu_btn = None
            for sel in menu_selectors:
                try:
                    menu_btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                    if menu_btn: break
                except: continue

            if not menu_btn:
                # Fallback: cari button/div yang mengandung ikon titik tiga
                try:
                    menu_btn = self.driver.find_element(By.XPATH,
                        "//button[contains(@class, 'action') or contains(@aria-label, 'ore') or contains(@aria-label, 'ction')]")
                except:
                    self.log(f"⚠️ Menu tidak ditemukan: {chat_id}")
                    return False

            menu_btn.click(); time.sleep(1.5)

            # Auto-detect tombol Download (coba banyak variasi teks & selector)
            dl_btn = None
            dl_xpaths = [
                "//a[contains(text(), 'Download')]",
                "//a[contains(text(), 'download')]",
                "//a[contains(text(), 'Download transcript')]",
                "//button[contains(text(), 'Download')]",
                "//span[contains(text(), 'Download')]/..",
                "//li[contains(text(), 'Download')]",
                "//div[contains(text(), 'Download')]",
                "//a[contains(@href, 'download')]",
                "//a[contains(@href, 'transcript')]",
                "//button[contains(text(), 'Export')]",
                "//a[contains(text(), 'Export')]",
                "//*[contains(text(), 'Download transcript')]",
                "//*[contains(text(), 'Download chat')]",
            ]
            for xpath in dl_xpaths:
                try:
                    dl_btn = self.driver.find_element(By.XPATH, xpath)
                    if dl_btn and dl_btn.is_displayed(): break
                    dl_btn = None
                except: continue

            # Fallback: cari semua <a> dan <button> di dropdown/popover yang visible
            if not dl_btn:
                try:
                    # Cari semua elemen clickable yang baru muncul (dropdown menu)
                    all_links = self.driver.find_elements(By.CSS_SELECTOR,
                        "div[class*='popup'] a, div[class*='menu'] a, div[class*='dropdown'] a, "
                        "ul[role='menu'] li, div[role='menu'] div, div[class*='popover'] a, "
                        "div[class*='overlay'] a, div[class*='modal'] a")
                    for link in all_links:
                        txt = link.text.strip().lower()
                        if 'download' in txt or 'export' in txt or 'transcript' in txt:
                            dl_btn = link; break
                except: pass

            # Debug: log apa yang ada di halaman jika masih gagal
            if not dl_btn:
                try:
                    # Log semua teks di elemen popup/menu yang visible
                    visible_items = self.driver.find_elements(By.XPATH,
                        "//*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop') or contains(@class,'popover') or contains(@role,'menu')]//a | //*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop') or contains(@class,'popover') or contains(@role,'menu')]//button | //*[contains(@class,'popup') or contains(@class,'menu') or contains(@class,'drop') or contains(@class,'popover') or contains(@role,'menu')]//li")
                    menu_texts = [v.text.strip() for v in visible_items if v.text.strip()][:10]
                    self.log(f"🔍 Menu items ditemukan: {menu_texts}")
                except: pass
                self.log(f"⚠️ Tombol Download tidak ditemukan: {chat_id}")
                try: self.driver.find_element(By.TAG_NAME, "body").click()
                except: pass
                return False

            dl_btn.click()
            self.processed_history.add(chat_id)
            self.log(f"⬇️ Download: {chat_id}")
            time.sleep(1.5)
            return True
        except TimeoutException:
            self.log(f"⚠️ Timeout menu: {chat_id}")
            return False
        except Exception as e:
            self.log(f"⚠️ Gagal download {chat_id}: {str(e)[:60]}")
            return False

    # --- MONITORING LOOP (VISION + STRICT FILTER) ---

    def toggle_monitoring(self):
        if self.is_monitoring: self.is_monitoring = False; self.btn_monitor.config(text="🤖 START ROBOT", bg="#4CAF50")
        else:
            self.is_monitoring = True; self.btn_monitor.config(text="⏹ STOP ROBOT", bg="#B71C1C")
            threading.Thread(target=self.run_monitor_loop, daemon=True).start()

    def run_monitor_loop(self):
        api_key_string = self.entry_api.get().strip()
        if not api_key_string and DEFAULT_API_KEYS:
            api_key_string = DEFAULT_API_KEYS
            self.log("ℹ️ Menggunakan API Key dari Script (Hardcoded).")

        temp_keys = [k for k in api_key_string.split(',') if k.strip().replace("'", "").replace('"', "")]
        key_count = len(set(temp_keys))
        if key_count > 0:
            self.log(f"🔑 Memuat {key_count} API Key Unik untuk rotasi.")
        else:
            self.log("⚠️ Tidak ada API Key yang terdeteksi!")

        mode_audit = self.combo_audit_mode.get()
        self.log(f"🚀 MODE 24H AKTIF | Model: {mode_audit}")

        consecutive_errors = 0
        loop_count = 0

        while self.is_monitoring:
            try:
                files = sorted([f for f in os.listdir(self.local_in) if f.endswith(".txt")])

                # Jika tidak ada file, tunggu lalu cek lagi (hemat CPU)
                if not files:
                    time.sleep(3)
                    consecutive_errors = 0
                    continue

                loop_count += 1
                # Auto reset key index setiap 100 loop (hindari stuck di key terakhir)
                if loop_count % 100 == 0:
                    self.current_key_index = 0
                    self.log("🔄 Reset rotasi key.")

                for filename in files:
                    if not self.is_monitoring: break
                    file_path = os.path.join(self.local_in, filename)

                    # Pastikan file sudah selesai ditulis (cek ukuran stabil)
                    try:
                        size1 = os.path.getsize(file_path)
                        time.sleep(0.5)
                        size2 = os.path.getsize(file_path)
                        if size1 != size2:
                            self.log(f"⏳ File masih ditulis: {filename}, skip dulu...")
                            continue
                    except: continue

                    try:
                        with open(file_path, "r", encoding="utf-8") as f: content = f.read()
                    except: continue

                    # Skip file kosong/terlalu kecil
                    if len(content.strip()) < 50:
                        self.log(f"⚠️ File terlalu kecil, skip: {filename}")
                        try: os.remove(file_path)
                        except: pass
                        continue

                    userid = self.extract_userid(content); web_name = self.extract_web_name(content)
                    chat_time = self.extract_first_timestamp(content); chat_date = self.extract_chat_date(content)
                    images = self.extract_images(content)
                    links = self.extract_links(content)

                    self.log(f"⚡ Proses: {userid} | {chat_date} {chat_time}")
                    chat_cost_idr = 0.0

                    if mode_audit != "TIDAK PAKAI AI":
                        img_url = images[0] if images else None
                        audit_result, t_in, t_out = self.audit_content(
                            api_key_string, self.text_sop.get("1.0", tk.END), content,
                            userid, chat_time, chat_date, mode_audit, links, img_url
                        )

                        # Jika quota habis / stopped, JANGAN proses file ini — retry nanti
                        if audit_result in ("QUOTA_EXHAUSTED", "STOPPED"):
                            self.log(f"🔁 {filename} akan di-retry di loop berikutnya.")
                            continue

                        chat_cost_idr = ((t_in / 1000000 * self.price_input_1m) + (t_out / 1000000 * self.price_output_1m)) * self.usd_to_idr
                    else:
                        audit_result = f"{userid}\nLULUS\nSaran AI: Pelayanan Sudah Bagus"

                    # --- FILTER GSHEET ---
                    audit_res_up = audit_result.upper()
                    is_failed = "TIDAK LULUS" in audit_res_up
                    is_sop2 = "SOP 2" in audit_res_up or "TEMUAN TOPIK 2" in audit_res_up
                    is_perlu_perbaikan = "PERLU PERBAIKAN" in audit_res_up

                    if is_failed or is_sop2 or is_perlu_perbaikan:
                        status_label = "TIDAK LULUS" if is_failed else "PERLU PERBAIKAN"
                        m = re.search(r"(?:TIDAK LULUS|SOP 2|PERLU PERBAIKAN)\s*[\(\[]\s*(.*?)\s*[\)\]]", audit_result, re.IGNORECASE | re.DOTALL)
                        detail = m.group(1).strip() if m else "Deteksi Temuan"
                        row = [chat_date, chat_time, userid, status_label, "Umum", detail, filename]
                        self.send_to_google_sheet(row, web_name)
                        self.increment_stats(is_noteworthy=True, web_name=web_name, cost=chat_cost_idr)
                        if "SARAN AI:" in audit_res_up:
                            saran_match = re.search(r"Saran AI:\s*(.*)", audit_result, re.IGNORECASE)
                            if saran_match: self.log_insight(userid, filename, saran_match.group(1).strip())
                    else:
                        self.increment_stats(is_noteworthy=False, web_name=web_name, cost=chat_cost_idr)

                    self.log(f"💰 Rp {chat_cost_idr:.4f} | Total [{web_name}]: Rp {self.today_stats['details'][web_name]['cost']:,.2f}")
                    self.send_telegram_text(audit_result)

                    if images:
                        try: requests.post(f"https://api.telegram.org/bot{self.entry_tele_token.get()}/sendPhoto", data={"chat_id": self.entry_tele_chatid.get(), "photo": images[0], "caption": f"📸 Gambar dari {userid}"})
                        except: pass
                    if is_failed:
                        try:
                            with open(file_path, 'rb') as f:
                                requests.post(f"https://api.telegram.org/bot{self.entry_tele_token.get()}/sendDocument", files={'document': f}, data={'chat_id': self.entry_tele_chatid.get(), 'caption': "📜 Transkrip Pelanggaran"})
                        except: pass

                    today_folder = datetime.now().strftime("%Y-%m-%d")
                    t_folder = os.path.join(self.local_out, web_name, today_folder)
                    if not os.path.exists(t_folder): os.makedirs(t_folder)
                    try: shutil.move(file_path, os.path.join(t_folder, filename))
                    except:
                        try: os.remove(file_path)
                        except: pass

                    consecutive_errors = 0

                time.sleep(3)

            except Exception as e:
                consecutive_errors += 1
                self.log(f"❌ Error Loop #{consecutive_errors}: {e}")

                # Auto-recovery: jika error 5x berturut, jeda 30 detik lalu lanjut
                if consecutive_errors >= 5:
                    self.log(f"🔧 Auto-Recovery: Jeda 30 detik setelah {consecutive_errors}x error...")
                    time.sleep(30)
                    consecutive_errors = 0
                    # Reset stats tanggal jika sudah ganti hari
                    self.load_stats()
                else:
                    time.sleep(5)

    def audit_content(self, api_key_string, sop, content, userid, chat_time, chat_date, mode_audit, links=None, image_url=None):
        # --- PARSE & CLEAN MULTIPLE KEYS ---
        raw_keys = api_key_string.split(',')
        api_keys = []
        seen_keys = set()
        for k in raw_keys:
            clean_k = k.strip().replace("'", "").replace('"', "")
            if clean_k and clean_k not in seen_keys:
                api_keys.append(clean_k); seen_keys.add(clean_k)
        if not api_keys: return "ERROR: API Key Kosong / Format Salah", 0, 0

        # --- MODEL MAPPING (Termurah → Termahal) ---
        model_map = {
            "GEMINI 2.0 FLASH LITE (GRATIS)":     ["gemini-2.5-flash", "gemini-2.0-flash-lite"],
            "GEMINI 1.5 FLASH-8B (TERMURAH)":      ["gemini-2.5-flash", "gemini-2.0-flash-lite"],
            "GEMINI 1.5 FLASH (MURAH & STABIL)":   ["gemini-2.5-flash", "gemini-2.0-flash"],
            "GEMINI 2.0 FLASH (STANDAR)":           ["gemini-2.5-flash", "gemini-2.0-flash"],
            "GEMINI 2.5 FLASH (PREMIUM)":           ["gemini-2.5-flash"],
            "GEMINI 3 FLASH PREVIEW":               ["gemini-3-flash-preview", "gemini-2.5-flash"],
        }
        models_to_try = model_map.get(mode_audit, ["gemini-2.5-flash"])
        models_to_try = list(dict.fromkeys(models_to_try))

        # --- KOMPRES TRANSKRIP (Hemat 40-60% token) ---
        compressed = self.compress_transcript(content)
        original_len = len(content); compressed_len = len(compressed)
        savings = ((original_len - compressed_len) / original_len * 100) if original_len > 0 else 0
        self.log(f"📦 Kompres: {original_len} → {compressed_len} char ({savings:.0f}% hemat)")

        link_str = ", ".join(links) if links else "Tidak Ada"

        # --- SYSTEM INSTRUCTION (Di-cache oleh Gemini, tidak dihitung ulang tiap request) ---
        system_sop = f"""Anda Auditor QA. Audit berdasarkan SOP berikut:
{sop}
FORMAT OUTPUT WAJIB:
[USERID]
Topik: [Kategori]
[STATUS: LULUS / TIDAK LULUS (Poin) / SOP 2 (Poin)]
Link Diberikan: [Link yang CS berikan ke member, atau - jika tidak ada]
Saran AI: [Pelayanan Sudah Bagus / Perlu Perbaikan] : [Inti singkat]"""

        # --- PROMPT RINGKAS (Hanya data variabel, SOP sudah di system instruction) ---
        prompt = f"""{userid} | {chat_date} {chat_time}
Link: {link_str}
---
{compressed}"""

        image_data = None
        if image_url:
            try:
                resp = requests.get(image_url, timeout=10)
                if resp.status_code == 200:
                    image_data = {'mime_type': 'image/jpeg', 'data': resp.content}
            except: pass

        last_error = ""

        # Reset index ke range valid (hindari angka membesar terus)
        self.current_key_index = self.current_key_index % len(api_keys)

        # --- MULTI-KEY ROTATION + FALLBACK MODEL + UNLIMITED WAIT QUOTA ---
        quota_wait_count = 0

        while True:
            for model_name in models_to_try:
                keys_exhausted = 0  # Hitung berapa key yang 429

                for key_attempt in range(len(api_keys)):
                    if not self.is_monitoring and not self.is_auto_today: return "STOPPED", 0, 0

                    active_key = api_keys[self.current_key_index % len(api_keys)]
                    key_display = f"...{active_key[-4:]}" if len(active_key) > 4 else "???"

                    # Cek cooldown: skip key yang masih dalam masa tunggu
                    cooldown_until = self.key_cooldowns.get(key_display, 0)
                    if time.time() < cooldown_until:
                        self.current_key_index += 1
                        keys_exhausted += 1
                        continue

                    try:
                        genai.configure(api_key=active_key)
                        self.log(f"🤖 {model_name} | Key: {key_display}")

                        model = genai.GenerativeModel(model_name, system_instruction=system_sop)
                        parts = [prompt]
                        if image_data: parts.append(image_data)

                        response = model.generate_content(
                            parts,
                            safety_settings={
                                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE',
                            }
                        )

                        # SUKSES - reset cooldown & update harga
                        self.key_cooldowns.pop(key_display, None)
                        pricing = self.model_pricing.get(model_name, {"input": 0.075, "output": 0.30})
                        self.price_input_1m = pricing["input"]
                        self.price_output_1m = pricing["output"]

                        t_in = response.usage_metadata.prompt_token_count
                        t_out = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
                        self.log(f"✅ {model_name} OK | Token: {t_in}+{t_out}")

                        # Ambil teks respons — handle blocked/empty response
                        try:
                            result_text = response.text.strip()
                        except (ValueError, AttributeError):
                            # Response diblokir atau kosong, coba ambil dari candidates
                            if response.candidates:
                                parts_out = response.candidates[0].content.parts
                                result_text = "".join(p.text for p in parts_out if hasattr(p, 'text')).strip()
                            else:
                                block_reason = getattr(response, 'prompt_feedback', None)
                                self.log(f"⚠️ Response diblokir: {block_reason}")
                                result_text = f"{userid}\nLULUS\nSaran AI: Response diblokir safety filter, dianggap LULUS."

                        if not result_text:
                            result_text = f"{userid}\nLULUS\nSaran AI: Response kosong, dianggap LULUS."

                        return result_text, t_in, t_out

                    except Exception as e:
                        error_str = str(e)
                        last_error = error_str

                        if "400" in error_str and "API_KEY_INVALID" in error_str:
                            self.log(f"❌ Key INVALID: {key_display}")
                            self.key_cooldowns[key_display] = time.time() + 3600
                            self.current_key_index += 1
                            keys_exhausted += 1
                            continue

                        if "429" in error_str or "Resource has been exhausted" in error_str:
                            self.log(f"⚠️ QUOTA HABIS Key {key_display}")
                            self.key_cooldowns[key_display] = time.time() + 60
                            self.current_key_index += 1
                            keys_exhausted += 1
                            continue

                        elif "503" in error_str:
                            self.log(f"⚠️ Server Busy. Tunggu 5 detik...")
                            time.sleep(5)
                            continue
                        else:
                            self.log(f"❌ Error {model_name}: {error_str[:80]}")
                            break

                if keys_exhausted >= len(api_keys):
                    self.log(f"⚠️ {model_name}: Semua {len(api_keys)} key habis quota.")
                    continue

                self.log(f"⚠️ {model_name} Gagal. Coba model lain...")

            # Semua model & key gagal - tunggu quota reset lalu COBA LAGI (tidak menyerah)
            quota_wait_count += 1
            wait_secs = min(60 * quota_wait_count, 300)  # Naik bertahap: 60s, 120s, 180s... max 300s
            self.log(f"⏳ SEMUA KEY HABIS. Tunggu {wait_secs}s quota reset... (retry #{quota_wait_count})")
            for _ in range(wait_secs):
                if not self.is_monitoring and not self.is_auto_today: return "STOPPED", 0, 0
                time.sleep(1)
            # Reset semua cooldown agar dicoba lagi
            self.key_cooldowns.clear()
            self.current_key_index = 0

    # --- HELPERS TELEGRAM & SCHEDULER ---

    def send_telegram_text(self, message):
        t = self.entry_tele_token.get(); c = self.entry_tele_chatid.get()
        if t and c:
            try: requests.post(f"https://api.telegram.org/bot{t}/sendMessage", data={"chat_id": c, "text": message})
            except: pass

    def send_rekap_telegram(self, custom_title=None):
        if not self.today_stats: self.load_stats()
        msg = f"*{custom_title or '📊 LAPORAN AUDIT'}*\n📅 Tanggal: `{self.today_stats.get('date', '??')}`\n📁 Total Chat: `{self.today_stats.get('total', 0)}` (❌ {self.today_stats.get('failed', 0)})\n\n"
        details = self.today_stats.get("details", {})
        if details:
            for web, data in sorted(details.items(), key=lambda x: x[1]['total'], reverse=True):
                msg += f"🏪 *Toko: {web}*\n├ Chat: `{data.get('total', 0)}` (❌ {data.get('failed', 0)})\n└ Biaya API: `Rp {data.get('cost', 0.0):,.2f}`\n\n"
        msg += f"💵 *TOTAL TAGIHAN: Rp {self.today_stats.get('total_cost', 0.0):,.2f}*"
        self.send_telegram_text(msg)

    def scheduler_loop(self):
        last_date_check = datetime.now().strftime("%Y-%m-%d")
        while True:
            try:
                now = datetime.now()
                current_date = now.strftime("%Y-%m-%d")

                # Auto reset stats & UI saat ganti hari
                if current_date != last_date_check:
                    self.log(f"🔄 Hari baru: {current_date} — reset statistik harian.")
                    self.today_stats = {"date": current_date, "total": 0, "failed": 0, "total_cost": 0.0, "details": {}}
                    self.save_stats()
                    last_date_check = current_date

                # Kirim laporan harian di 00:00
                if now.hour == 0 and now.minute == 0:
                    if self.last_auto_report_date != current_date:
                        self.send_rekap_telegram(custom_title="🌙 LAPORAN HARIAN & BIAYA API (TUTUP BUKU)")
                        self.last_auto_report_date = current_date
            except: pass
            time.sleep(10)

    # --- CONFIG & HISTORY ---

    def browse_gsheet_json(self):
        fn = filedialog.askopenfilename(title="Pilih JSON", filetypes=[("JSON Files", "*.json")])
        if fn: self.entry_gsheet_json.delete(0, tk.END); self.entry_gsheet_json.insert(0, fn)

    def resolve_json_path(self, json_path):
        """Resolve path JSON: jika relatif, cari relatif ke app_path."""
        if not json_path: return json_path
        if os.path.isabs(json_path) and os.path.exists(json_path):
            return json_path
        # Coba relatif ke folder EXE/script
        resolved = os.path.join(self.app_path, json_path)
        if os.path.exists(resolved):
            return resolved
        return json_path  # Return apa adanya, biar error message jelas

    def test_gsheet_connection(self):
        if not GSPREAD_AVAILABLE: return
        j = self.resolve_json_path(self.entry_gsheet_json.get().strip())
        n = self.entry_gsheet_name.get().strip()
        try:
            scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(j, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_url(n) if "docs.google.com" in n else client.open(n)
            messagebox.showinfo("Sukses", f"Terhubung ke: {spreadsheet.title}")
        except Exception as e: messagebox.showerror("Error", str(e))

    def send_to_google_sheet(self, data_row, target_tab_name):
        if not GSPREAD_AVAILABLE: return
        j = self.resolve_json_path(self.entry_gsheet_json.get().strip())
        n = self.entry_gsheet_name.get().strip()
        if not j or not n: return
        try:
            scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(j, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_url(n) if "docs.google.com" in n else client.open(n)
            target_tab_name = str(target_tab_name).strip().upper() or "OTHERS"
            try: worksheet = spreadsheet.worksheet(target_tab_name)
            except gspread.exceptions.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=target_tab_name, rows=1000, cols=10)
                worksheet.append_row(["Tanggal Chat", "Jam", "UserID", "Status", "Topik", "Detail/Saran", "Filename"])
            worksheet.append_row(data_row)
        except: pass

    def save_config(self):
        c = {
            "api_key": self.entry_api.get(), "tele_token": self.entry_tele_token.get(), "tele_chatid": self.entry_tele_chatid.get(),
            "lc_email": self.entry_lc_email.get(), "lc_pass": self.entry_lc_password.get(),
            "gsheet_name": self.entry_gsheet_name.get(), "gsheet_json": self.entry_gsheet_json.get(),
            "audit_mode": self.combo_audit_mode.get(), "headless": self.headless_var.get()
        }
        with open(self.config_file, "w") as f: json.dump(c, f)
        messagebox.showinfo("Sukses", "Config tersimpan!"); self.update_stats_ui()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    c = json.load(f)
                    
                    # LOGIC: Load from config first, if empty check DEFAULT_API_KEYS
                    saved_key = c.get("api_key", "")
                    if not saved_key and DEFAULT_API_KEYS:
                        saved_key = DEFAULT_API_KEYS
                        
                    self.entry_api.insert(0, saved_key)
                    self.entry_tele_token.insert(0, c.get("tele_token", ""))
                    self.entry_tele_chatid.insert(0, c.get("tele_chatid", "")); self.entry_lc_email.insert(0, c.get("lc_email", ""))
                    self.entry_lc_password.insert(0, c.get("lc_pass", "")); self.entry_gsheet_name.insert(0, c.get("gsheet_name", ""))
                    self.entry_gsheet_json.insert(0, c.get("gsheet_json", ""))
                    self.headless_var.set(c.get("headless", c.get("headless_mode", 0)))
                    # Backward compatible: audit_mode atau selected_model
                    saved_mode = c.get("audit_mode", c.get("selected_model", ""))
                    valid_modes = [self.combo_audit_mode.cget("values")[i] for i in range(len(self.combo_audit_mode.cget("values")))] if self.combo_audit_mode.cget("values") else []
                    if saved_mode and saved_mode in str(valid_modes):
                        self.combo_audit_mode.set(saved_mode)
                self.update_stats_ui()
            except: pass
        elif DEFAULT_API_KEYS:
             # If no config file exists, use default
             self.entry_api.insert(0, DEFAULT_API_KEYS)

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f: self.processed_history = set(json.load(f))
                self.lbl_history_count.config(text=f"History: {len(self.processed_history)}")
            except: pass

    def save_history(self):
        try:
            with open(self.history_file, "w") as f: json.dump(list(self.processed_history), f)
            self.lbl_history_count.config(text=f"History: {len(self.processed_history)}")
        except: pass

    def reset_history(self):
        if messagebox.askyesno("Reset", "Hapus history?"):
            self.processed_history = set(); self.save_history(); self.log("History direset.")

if __name__ == "__main__":
    root = tk.Tk(); app = BrowserAuditApp(root); root.mainloop()