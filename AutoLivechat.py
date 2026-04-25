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

# --- VERSI APLIKASI ---
APP_VERSION = "16.5.0"

# --- KONFIGURASI AUTO-UPDATE ---
GITHUB_OWNER = "Kitakeren17"
GITHUB_REPO = "Autolc24hours"
UPDATE_EXE_NAME = "AutoLivechat.exe"

def compare_versions(current, latest):
    try:
        c = [int(x) for x in current.split(".")]
        l = [int(x) for x in latest.replace("v", "").split(".")]
        return l > c
    except:
        return False

def check_for_update_on_start():
    try:
        import subprocess
        api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200: return
        data = resp.json()
        latest_version = data.get("tag_name", "").replace("v", "")
        if not latest_version or not compare_versions(APP_VERSION, latest_version): return
        download_url = None
        for asset in data.get("assets", []):
            if asset["name"] == UPDATE_EXE_NAME:
                download_url = asset["browser_download_url"]
                break
        if not download_url: return
        jawab = messagebox.askyesno("Update Tersedia!", f"Versi baru: v{latest_version}\nVersi Anda: v{APP_VERSION}\n\nDownload dan install update?")
        if not jawab: return
        if not getattr(sys, 'frozen', False):
            messagebox.showinfo("Info", "Auto-update hanya bisa di versi .exe")
            return
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        resp = requests.get(download_url, stream=True, timeout=120)
        with open(new_exe, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192): f.write(chunk)
        bat_path = os.path.join(os.path.dirname(current_exe), "_update.bat")
        with open(bat_path, "w") as bat:
            bat.write(f'@echo off\necho Mengupdate AutoLivechat...\ntimeout /t 2 /nobreak >nul\n')
            bat.write(f'del "{current_exe}"\nrename "{new_exe}" "{os.path.basename(current_exe)}"\n')
            bat.write(f'start "" "{current_exe}"\ndel "%~f0"\n')
        subprocess.Popen(["cmd", "/c", bat_path], creationflags=0x08000000)
        sys.exit(0)
    except: pass

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
        self.root.title(f"AI Audit Robot - V{APP_VERSION} (24H Realtime & Cost Optimized)")
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
        self.download_stats_file = os.path.join(self.app_path, "download_stats.json")

        # Auto-extract bundled files saat pertama kali jalan
        if getattr(sys, 'frozen', False):
            bundled_files = ["config.json", "SOP.txt", "SOP_TENZO_2026-03-16.txt", "kunci.json"]
            for bf in bundled_files:
                dest = os.path.join(self.app_path, bf)
                if not os.path.exists(dest):
                    src = os.path.join(sys._MEIPASS, bf)
                    if os.path.exists(src):
                        try:
                            import shutil
                            shutil.copy2(src, dest)
                        except: pass

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
        self.saran_ai_enabled = True
        self.audited_history = set()
        self.audited_history_file = os.path.join(self.app_path, "audited_history.json")

        self.load_stats()
        self.load_audited_history()
        self.download_stats = self.load_download_stats()
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
            # Skip baris sistem/redundan (PERTAHANKAN info handover/transfer/join)
            line_lower = line.lower()
            if any(skip in line_lower for skip in [
                "livechat conversation transcript", "----------",
                "sent rich message"
            ]): continue
            # Pertahankan info penting untuk deteksi handover
            if any(keep in line_lower for keep in [
                "joined the chat", "was transferred", "assigned to",
                "was invited", "left the chat", "chat was"
            ]):
                compressed.append(f"[SYSTEM] {line.strip()}")
                continue
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
                # JANGAN hapus file di folder _duplikat_skip (buat investigasi)
                if "_duplikat_skip" in root:
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.getmtime(fp) < cutoff:
                        os.remove(fp); deleted += 1
            if deleted > 0: self.log(f"🧹 Auto Cleanup: {deleted} file lama dihapus (>2 hari).")
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

    def toggle_saran_ai(self):
        self.saran_ai_enabled = not self.saran_ai_enabled
        if self.saran_ai_enabled:
            self.btn_saran_ai.config(text="💡 SARAN AI: ON", bg="#4CAF50")
            self.log("💡 Saran AI diaktifkan.")
        else:
            self.btn_saran_ai.config(text="💡 SARAN AI: OFF", bg="#9E9E9E")
            self.log("💡 Saran AI dinonaktifkan.")
        # Auto-save ke config
        try: self.save_config_silent()
        except: pass

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

    # --- DOWNLOAD STATS PER HARI ---

    def load_download_stats(self):
        if os.path.exists(self.download_stats_file):
            try:
                with open(self.download_stats_file, "r") as f: return json.load(f)
            except: pass
        return {}

    def save_download_stats(self):
        try:
            with open(self.download_stats_file, "w") as f:
                json.dump(self.download_stats, f, indent=2)
        except: pass

    def update_download_stats(self, date_str, downloaded, skipped, failed, archives_total=None):
        """Update statistik download untuk tanggal tertentu."""
        if date_str not in self.download_stats:
            self.download_stats[date_str] = {
                "downloaded": 0, "skipped": 0, "failed": 0,
                "archives_total": None, "file_count": 0,
                "last_update": ""
            }
        entry = self.download_stats[date_str]
        entry["downloaded"] += downloaded
        entry["skipped"] += skipped
        entry["failed"] += failed
        if archives_total is not None:
            entry["archives_total"] = archives_total
        # Hitung file aktual di Data_Chat_Selesai
        entry["file_count"] = self._count_downloaded_for_date(date_str)
        entry["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_download_stats()
        self.update_download_stats_ui()

    def update_download_stats_ui(self):
        """Update tampilan download stats di UI."""
        try:
            text = ""
            # Urutkan terbaru dulu, max 7 hari
            sorted_dates = sorted(self.download_stats.keys(), reverse=True)[:7]
            for dt in sorted_dates:
                d = self.download_stats[dt]
                total_dl = d.get("file_count", d.get("downloaded", 0))
                archives = d.get("archives_total")
                if archives:
                    persen = min(100, (total_dl / archives * 100)) if archives > 0 else 0
                    status = "OK" if total_dl >= archives else "KURANG"
                    text += f"{dt}: {total_dl}/{archives} ({persen:.0f}%) [{status}]\n"
                else:
                    text += f"{dt}: {total_dl} chat (total archives ?)\n"
            self.text_dl_stats.config(state="normal")
            self.text_dl_stats.delete("1.0", tk.END)
            self.text_dl_stats.insert("1.0", text)
            self.text_dl_stats.config(state="disabled")
        except: pass

    def refresh_download_stats(self):
        """Refresh: hitung ulang file aktual di Data_Chat_Selesai untuk semua tanggal yang tercatat."""
        for date_str in list(self.download_stats.keys()):
            actual = self._count_downloaded_for_date(date_str)
            self.download_stats[date_str]["file_count"] = actual
            self.download_stats[date_str]["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_download_stats()
        self.update_download_stats_ui()
        self.log("🔄 Download stats di-refresh dari file aktual.")

    def show_download_stats_detail(self):
        """Tampilkan detail download stats di popup."""
        msg = "=== STATISTIK DOWNLOAD PER HARI ===\n\n"
        sorted_dates = sorted(self.download_stats.keys(), reverse=True)[:14]
        for dt in sorted_dates:
            d = self.download_stats[dt]
            total_dl = d.get("file_count", d.get("downloaded", 0))
            archives = d.get("archives_total")
            dl_new = d.get("downloaded", 0)
            skip = d.get("skipped", 0)
            fail = d.get("failed", 0)
            last = d.get("last_update", "?")
            msg += f"Tanggal: {dt}\n"
            if archives:
                selisih = archives - total_dl
                msg += f"  File: {total_dl} / {archives} (kurang {selisih})\n"
            else:
                msg += f"  File: {total_dl} (total archives tidak diketahui)\n"
            msg += f"  Download: {dl_new} | Skip: {skip} | Gagal: {fail}\n"
            msg += f"  Update: {last}\n\n"
        messagebox.showinfo("Download Stats", msg)

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
        self.btn_saran_ai = tk.Button(btn_box, text="💡 SARAN AI: ON", command=self.toggle_saran_ai, bg="#4CAF50", fg="white")
        self.btn_saran_ai.pack(side="left", fill="x", expand=True, padx=1)

        # --- PANEL DOWNLOAD STATS PER HARI ---
        dl_stat_f = tk.LabelFrame(left_p, text="📥 DOWNLOAD PER HARI", bg="#E8F5E9", padx=10, pady=5); dl_stat_f.pack(fill="x", pady=5)
        self.text_dl_stats = tk.Text(dl_stat_f, height=5, bg="white", font=("Consolas", 8), state="disabled"); self.text_dl_stats.pack(fill="x", pady=2)
        dl_btn_box = tk.Frame(dl_stat_f, bg="#E8F5E9"); dl_btn_box.pack(fill="x")
        tk.Button(dl_btn_box, text="📋 DETAIL", command=self.show_download_stats_detail, bg="#388E3C", fg="white").pack(side="left", fill="x", expand=True, padx=1)
        tk.Button(dl_btn_box, text="🔄 REFRESH", command=self.refresh_download_stats, bg="#1976D2", fg="white").pack(side="left", fill="x", expand=True, padx=1)
        # Load data awal
        self.root.after(500, self.update_download_stats_ui)

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
            options.add_argument("--disable-features=MediaRouter,SafeBrowsingEnhancedProtection")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-default-apps")
            options.add_argument("--no-first-run")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--safebrowsing-disable-download-protection")
            options.add_argument("--disable-client-side-phishing-detection")
            options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            download_dir = os.path.abspath(self.local_in).replace("/", "\\")
            if not os.path.exists(download_dir): os.makedirs(download_dir)
            self.log(f"📂 Download folder: {download_dir}")
            prefs = {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "safebrowsing.disable_download_protection": True,
                "profile.default_content_setting_values.automatic_downloads": 1,
                "profile.default_content_setting_values.images": 2,
                "profile.managed_default_content_settings.images": 2,
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
            self.driver.set_page_load_timeout(60)  # max 60 detik load halaman
            self.driver.get("https://my.livechatinc.com/archives")
            email = self.entry_lc_email.get(); pss = self.entry_lc_password.get()
            if email and pss: threading.Thread(target=self.perform_auto_login, args=(email, pss), daemon=True).start()
            self.log("🌐 Chrome dibuka.")
        except Exception as e: self.log(f"Error: {e}")

    def perform_auto_login(self, email, password):
        try:
            wait = WebDriverWait(self.driver, 15)
            email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
            email_field.clear()
            email_field.send_keys(email)
            self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            time.sleep(2)
            pass_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
            pass_field.clear()
            pass_field.send_keys(password)
            self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            self.log("🔐 Login submit, tunggu redirect...")
            time.sleep(8)
            self.driver.get("https://my.livechatinc.com/archives")
            time.sleep(3)
            if self.is_logged_in():
                self.log("✅ Auto-login berhasil!")
            else:
                self.log("⚠️ Auto-login selesai tapi belum terdeteksi login. Mungkin perlu login manual.")
        except Exception as e:
            self.log(f"⚠️ Login gagal: {str(e)[:60]}. Login manual dibutuhkan.")

    def toggle_auto_clicker(self):
        if self.is_auto_clicking: self.is_auto_clicking = False; self.btn_auto_click.config(text="▶ START LOOP", bg="#673AB7")
        else:
            if not self.driver: messagebox.showerror("Error", "Buka Chrome!"); return
            self.is_auto_clicking = True; self.btn_auto_click.config(text="⏹ STOP LOOP", bg="#B71C1C")
            try: interval = int(self.entry_interval.get())
            except: interval = 300
            threading.Thread(target=self.run_clicker_loop, args=(interval,), daemon=True).start()

    def scroll_load_all_chats(self, sel_list, max_scroll=1000):
        """Scroll sampai semua chat termuat. Stabil & hemat memory."""

        last_count = 0
        stable_rounds = 0

        for scroll_num in range(max_scroll):
            if not self.is_auto_clicking and not self.is_date_mode and not self.is_monitoring and not self.is_auto_today:
                break

            # Scroll 2x per round (cukup, tidak perlu agresif)
            for _ in range(2):
                try:
                    list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                    self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                except Exception:
                    try:
                        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    except Exception:
                        pass
                time.sleep(0.3)

            time.sleep(0.5)

            try:
                current_count = len(self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li"))
            except Exception:
                continue

            if current_count == last_count:
                stable_rounds += 1
                if stable_rounds >= 8:
                    break
            else:
                stable_rounds = 0
                if current_count % 100 == 0 or current_count - last_count > 30:
                    self.log(f"📜 {current_count} chat termuat...")

                # Setiap 500 chat, jeda 3 detik agar Chrome tidak overload
                if current_count % 500 == 0 and current_count > 0:
                    self.log(f"⏸️ Jeda 3 detik — beri Chrome waktu bernapas ({current_count} chat)...")
                    time.sleep(3)

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
                # Cek login sebelum setiap scan cycle
                if not self.ensure_logged_in():
                    self.log("❌ Belum login. Tunggu 60 detik lalu retry...")
                    time.sleep(60)
                    continue
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
                if downloaded > 0:
                    scan_date = datetime.now().strftime("%Y-%m-%d")
                    self.update_download_stats(scan_date, downloaded, skipped, failed)
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

            # Rapid scroll 5x sebelum cek
            for _ in range(5):
                try:
                    list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                    self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                except:
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.1)
            time.sleep(0.3)

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

            # --- CEK LOGIN ---
            if not self.ensure_logged_in():
                self.log(f"❌ Belum login. Tidak bisa download {date_display}.")
                self.is_date_mode = False
                return

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
            self.update_download_stats(date_display, downloaded, skipped, failed)
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
        self._last_health_check = time.time()

        while self.is_auto_today:
            current_date = datetime.now().strftime("%Y-%m-%d")
            self.log(f"📆 Mulai download chat tanggal: {current_date}")

            # --- Loop: download + cek chat baru selama masih hari yang sama ---
            while self.is_auto_today:
                # Health-check: cek login setiap 30 menit
                now = time.time()
                if now - self._last_health_check >= 1800:
                    self._last_health_check = now
                    self.log("🩺 Health-check: cek status login...")
                    if not self.is_logged_in():
                        self.log("⚠️ Health-check: session expired! Re-login...")
                        if not self.ensure_logged_in():
                            self.log("❌ Health-check: gagal re-login. Tunggu 60 detik...")
                            time.sleep(60)
                            continue
                    else:
                        self.log("✅ Health-check: login OK.")

                try:
                    downloaded = self._download_one_day("Today")
                except Exception as e:
                    err_str = str(e).lower()
                    self.log(f"❌ Error download {current_date}: {str(e)[:80]}")
                    downloaded = 0
                    # Auto-recovery: jika Chrome crash, buka ulang otomatis
                    if any(k in err_str for k in ["connectionpool", "read timed", "no such window",
                            "session not created", "unable to connect", "connection refused",
                            "chrome not reachable", "invalid session", "target window already closed"]):
                        self.log("🔄 Chrome crash terdeteksi. Membuka Chrome baru otomatis...")
                        try:
                            if self.driver:
                                try: self.driver.quit()
                                except: pass
                                self.driver = None
                            time.sleep(3)
                            self.open_chrome()
                            time.sleep(8)
                            # Validasi login yang benar (bukan cuma cek URL)
                            if self.ensure_logged_in():
                                self.log("✅ Chrome baru siap & login berhasil. Lanjut download...")
                            else:
                                self.log("❌ Chrome baru dibuka tapi gagal login. Tunggu 60 detik...")
                                time.sleep(60)
                            continue  # retry download
                        except Exception as re_err:
                            self.log(f"❌ Gagal restart Chrome: {str(re_err)[:60]}")
                            break

                if not self.is_auto_today:
                    break

                # Cek apakah sudah ganti hari
                new_date = datetime.now().strftime("%Y-%m-%d")
                if new_date != current_date:
                    self.log(f"🌅 Hari baru terdeteksi: {new_date}")
                    # Tuntaskan Yesterday SAMPAI SELESAI — loop sampai done >= archives_total
                    self.log(f"📥 Tuntaskan download {current_date} (Yesterday) sampai semua chat terdownload...")
                    MAX_YESTERDAY_ROUNDS = 10
                    empty_rounds = 0
                    yesterday_done = False
                    for r in range(1, MAX_YESTERDAY_ROUNDS + 1):
                        if not self.is_auto_today:
                            break
                        self.log(f"🔁 Yesterday round {r}/{MAX_YESTERDAY_ROUNDS}...")
                        try:
                            y_dl = self._download_one_day("Yesterday")
                        except Exception as e:
                            self.log(f"⚠️ Error Yesterday round {r}: {str(e)[:60]}")
                            y_dl = 0

                        y_total = getattr(self, "_last_archives_total", None)
                        y_done = getattr(self, "_last_final_downloaded", 0)

                        if y_total:
                            sisa = y_total - y_done
                            self.log(f"📊 Yesterday round {r}: ⬇️{y_dl} baru | {y_done}/{y_total} | sisa={sisa}")
                            if sisa <= 0:
                                self.log(f"✅ Yesterday LENGKAP ({y_done}/{y_total}). Lanjut hari baru.")
                                yesterday_done = True
                                break
                            # archives_total terdeteksi tapi masih kurang — lanjut round berikutnya
                            empty_rounds = 0
                        else:
                            # Fallback: archives_total tidak terdeteksi → pakai logika "no new download"
                            self.log(f"📊 Yesterday round {r}: ⬇️{y_dl} baru (total archives tidak terdeteksi)")
                            if y_dl == 0:
                                empty_rounds += 1
                                if empty_rounds >= 2:
                                    self.log(f"✅ {empty_rounds}x round tanpa chat baru. Anggap Yesterday selesai.")
                                    yesterday_done = True
                                    break
                            else:
                                empty_rounds = 0
                    if not yesterday_done and self.is_auto_today:
                        self.log(f"⚠️ Max {MAX_YESTERDAY_ROUNDS} round Yesterday tercapai tapi belum lengkap. Lanjut ke hari baru (sisa akan di-handle round berikutnya).")
                    self.log(f"➡️ Lanjut ke hari baru: {new_date}")
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

    def _get_archives_total(self):
        """Baca total chat dari halaman LiveChat archives.
        Strategi: prefer pattern berlabel eksplisit ('of X', 'X chats', 'X results'),
        ambil nilai maksimum. Reject badge/counter tanpa label (unreliable).
        """
        # --- PASS 1: pattern berlabel eksplisit (reliable) ---
        labeled_candidates = []
        try:
            labeled_xpaths = [
                "//*[contains(text(), 'of ')]",
                "//*[contains(text(), 'results')]",
                "//*[contains(text(), 'chats')]",
                "//*[contains(text(), 'Results')]",
                "//*[contains(text(), 'Chats')]",
                "//*[contains(text(), 'total')]",
                "//*[contains(text(), 'Total')]",
            ]
            for xpath in labeled_xpaths:
                try:
                    els = self.driver.find_elements(By.XPATH, xpath)
                    for el in els:
                        txt = el.text.strip()
                        if not txt or len(txt) > 200:
                            continue
                        # Pattern 1: "Showing X of Y" / "1-50 of 2,500" → ambil angka TERBESAR
                        m_of = re.search(r'of\s+([\d,]+)', txt, re.IGNORECASE)
                        if m_of:
                            try:
                                labeled_candidates.append(int(m_of.group(1).replace(',', '')))
                                continue
                            except: pass
                        # Pattern 2: "2,500 chats" / "2500 results"
                        m_cr = re.search(r'([\d,]+)\s*(?:chats?|results?)', txt, re.IGNORECASE)
                        if m_cr:
                            try:
                                labeled_candidates.append(int(m_cr.group(1).replace(',', '')))
                                continue
                            except: pass
                        # Pattern 3: "Total: 2,500"
                        m_tot = re.search(r'total\s*[:\s]\s*([\d,]+)', txt, re.IGNORECASE)
                        if m_tot:
                            try:
                                labeled_candidates.append(int(m_tot.group(1).replace(',', '')))
                            except: pass
                except: continue
        except: pass

        if labeled_candidates:
            # Pilih maksimum dari kandidat berlabel (total biasanya angka terbesar)
            return max(labeled_candidates)

        # --- PASS 2: fallback badge/counter — hanya terima angka masuk akal (>= 10) ---
        unlabeled_candidates = []
        try:
            unlabeled_xpaths = [
                "//*[contains(@class, 'total')]",
                "//*[contains(@class, 'count')]",
            ]
            for xpath in unlabeled_xpaths:
                try:
                    els = self.driver.find_elements(By.XPATH, xpath)
                    for el in els:
                        txt = el.text.strip()
                        if not txt or len(txt) > 30:
                            continue
                        nums = re.findall(r'[\d,]+', txt)
                        for n in nums:
                            try:
                                num = int(n.replace(',', ''))
                                if num >= 10:  # reject badge kecil (notifikasi, dsb)
                                    unlabeled_candidates.append(num)
                            except: pass
                except: continue
        except: pass

        if unlabeled_candidates:
            return max(unlabeled_candidates)
        return None

    def _count_downloaded_for_date(self, date_str):
        """Hitung berapa file yang sudah didownload untuk tanggal tertentu."""
        total = 0
        # Hitung di Data_Chat_Selesai (semua web_name)
        for web_name in os.listdir(self.local_out):
            web_path = os.path.join(self.local_out, web_name)
            if not os.path.isdir(web_path): continue
            date_path = os.path.join(web_path, date_str)
            if os.path.isdir(date_path):
                # Hitung file .txt langsung + di subfolder Batch
                for root, dirs, files in os.walk(date_path):
                    total += len([f for f in files if f.endswith(".txt")])
        # Hitung file di Data_Chat_Masuk/{date_str}/ (subfolder tanggal)
        masuk_date_path = os.path.join(self.local_in, date_str)
        if os.path.isdir(masuk_date_path):
            total += len([f for f in os.listdir(masuk_date_path) if f.endswith(".txt")])
        # Backward compatible: cek juga file di root Data_Chat_Masuk yang belum dipindah
        try:
            for f in os.listdir(self.local_in):
                fpath = os.path.join(self.local_in, f)
                if f.endswith(".txt") and os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                            if date_str in fh.read(500):
                                total += 1
                    except: pass
        except: pass
        return total

    def _download_one_day(self, mode):
        """Download semua chat untuk 1 hari (Today/Yesterday).
        Fixed v2: no infinite loop, fast-skip, day-change aware, memory-safe refresh."""
        try:
            target_date = datetime.now() if mode == "Today" else datetime.now() - timedelta(days=1)
            date_display = target_date.strftime("%Y-%m-%d")

            downloaded = 0
            skipped = 0
            failed = 0
            no_new_rounds = 0
            archives_total = None
            CLICK_BEFORE_REFRESH = 400  # refresh setiap 400 klik (bukan per item)

            # === OUTER LOOP: buka archives → scroll → download → refresh jika perlu ===
            while self.is_auto_today:

                # --- CEK GANTI HARI ---
                if mode == "Today" and datetime.now().strftime("%Y-%m-%d") != date_display:
                    self.log(f"🌅 Hari berganti saat download. Stop {date_display}.")
                    break

                # --- CEK LOGIN SEBELUM MULAI ---
                if not self.ensure_logged_in():
                    self.log(f"❌ Tidak bisa lanjut download {date_display}: belum login.")
                    return downloaded

                # --- BUKA ARCHIVES + FILTER ---
                self.driver.get("https://my.livechatinc.com/archives")
                time.sleep(5)
                self.log(f"🗓️ {mode} ({date_display}): Buka archives...")

                filter_ok = self.apply_livechat_filter(mode)
                if filter_ok:
                    self.log(f"✅ Filter {mode} aktif.")
                    time.sleep(3)
                else:
                    # Retry filter 1x sebelum lanjut
                    self.log(f"⚠️ Filter UI gagal. Retry 1x...")
                    time.sleep(3)
                    self.driver.get("https://my.livechatinc.com/archives")
                    time.sleep(5)
                    filter_ok = self.apply_livechat_filter(mode)
                    if filter_ok:
                        self.log(f"✅ Filter {mode} berhasil setelah retry.")
                        time.sleep(3)
                    else:
                        self.log(f"⚠️ Filter tetap gagal. Chat mungkin campur tanggal — hati-hati.")

                # --- CEK PROGRESS ---
                archives_total = self._get_archives_total()
                already_downloaded = self._count_downloaded_for_date(date_display)
                # Sanity check 1: archives_total < downloaded → angka UI tidak valid
                if archives_total and archives_total < already_downloaded:
                    self.log(f"⚠️ Archives={archives_total} < Downloaded={already_downloaded}. Angka UI tidak valid, abaikan & scan normal.")
                    archives_total = None
                if archives_total:
                    remaining = max(0, archives_total - already_downloaded)
                    self.log(f"📊 Archives={archives_total} | Downloaded={already_downloaded} | Sisa={remaining}")
                    # Sanity check 2: JANGAN early-exit kalau belum ada file tersimpan hari ini.
                    # "Semua sudah terdownload" padahal 0 file = pasti angka UI salah.
                    if remaining == 0 and already_downloaded == 0:
                        self.log(f"⚠️ Archives={archives_total} match tapi Downloaded=0. Angka UI mencurigakan, tetap scan.")
                        archives_total = None
                    elif remaining == 0:
                        self.log(f"✅ Semua chat {date_display} sudah terdownload!")
                        return downloaded
                if not archives_total:
                    self.log(f"📊 Sudah download={already_downloaded} (total archives tidak terdeteksi / diabaikan)")

                SEL_LIST = self.find_chat_list_selector()
                if not SEL_LIST:
                    time.sleep(5)
                    SEL_LIST = self.find_chat_list_selector()
                if not SEL_LIST:
                    self.log(f"⚠️ Tidak ada chat ditemukan untuk {date_display}.")
                    return downloaded

                # === INNER LOOP: scroll + download incremental ===
                round_dl = 0
                processed_idx = 0
                stable_rounds = 0
                last_total = 0
                items_clicked = 0
                timeout_count = 0
                need_refresh = False

                self.log(f"🚀 Scroll + download {date_display}...")

                while self.is_auto_today and not need_refresh:
                    # Cek ganti hari setiap iterasi scroll
                    if mode == "Today" and datetime.now().strftime("%Y-%m-%d") != date_display:
                        self.log(f"🌅 Hari berganti. Stop scroll.")
                        break

                    # --- SCROLL untuk load item baru ---
                    for _ in range(3):
                        try:
                            list_el = self.driver.find_element(By.CSS_SELECTOR, SEL_LIST)
                            self.driver.execute_script(
                                "arguments[0].scrollTop = arguments[0].scrollHeight;", list_el)
                        except Exception:
                            try:
                                self.driver.execute_script(
                                    "window.scrollTo(0, document.body.scrollHeight);")
                            except Exception:
                                pass
                        time.sleep(0.3)
                    time.sleep(0.5)

                    try:
                        current_items = self.driver.find_elements(
                            By.CSS_SELECTOR, f"{SEL_LIST} li")
                        current_total = len(current_items)
                    except Exception:
                        continue

                    if current_total > last_total:
                        stable_rounds = 0
                        if current_total % 100 == 0:
                            self.log(f"📜 {current_total} chat termuat...")
                        # Jeda setiap 500 item agar Chrome bernapas
                        if current_total % 500 == 0 and current_total > 0:
                            time.sleep(2)
                    else:
                        stable_rounds += 1

                    # --- DOWNLOAD item baru ---
                    while processed_idx < current_total and self.is_auto_today:
                        # Cek ganti hari setiap 100 item
                        if processed_idx % 100 == 0 and mode == "Today":
                            if datetime.now().strftime("%Y-%m-%d") != date_display:
                                break

                        # FAST-SKIP: extract chat_id dari href, tanpa klik
                        chat_id = self._extract_chat_id_from_item(SEL_LIST, processed_idx)
                        if chat_id and chat_id in self.processed_history:
                            skipped += 1
                            processed_idx += 1
                            if skipped % 200 == 0:
                                self.log(f"⏭ Fast-skip: {skipped} sudah ada | idx: {processed_idx}/{current_total}")
                            continue

                        # PERLU KLIK — download atau cek manual
                        result = self._try_download_item(
                            SEL_LIST, processed_idx, current_total,
                            downloaded, skipped, failed)

                        items_clicked += 1

                        if result == "downloaded":
                            downloaded += 1
                            round_dl += 1
                            timeout_count = 0
                        elif result == "skipped":
                            skipped += 1
                            timeout_count = 0
                        elif result == "timeout":
                            timeout_count += 1
                            failed += 1
                            self.log(f"⚠️ Timeout #{timeout_count}")
                            if timeout_count >= 3:
                                self.log("❌ 3x timeout. Trigger refresh...")
                                need_refresh = True
                                break
                        else:
                            failed += 1
                            timeout_count = 0

                        processed_idx += 1

                        # Save progress berkala
                        if downloaded > 0 and downloaded % 25 == 0:
                            self.save_history()
                            self.log(f"💾 Progress: ⬇️{downloaded} | ⏭{skipped} | ❌{failed}")

                        # Refresh setelah N klik (cegah Chrome Aw Snap)
                        if items_clicked >= CLICK_BEFORE_REFRESH:
                            self.log(f"🧹 {items_clicked} klik tercapai, refresh Chrome...")
                            need_refresh = True
                            break

                    last_total = current_total

                    # Semua item diproses & scroll stabil = round selesai
                    if stable_rounds >= 5 and processed_idx >= current_total:
                        break

                # --- AKHIR ROUND ---
                self.save_history()
                self.log(f"✅ Round: ⬇️{round_dl} baru | ⏭ skip | Total download: ⬇️{downloaded} | Klik: {items_clicked}")

                # Cek apakah masih ada chat baru
                if round_dl == 0:
                    no_new_rounds += 1
                    if no_new_rounds >= 2:
                        self.log(f"✅ {no_new_rounds}x tidak ada chat baru. Download {date_display} selesai.")
                        break
                else:
                    no_new_rounds = 0

            # === VERIFIKASI AKHIR & CATAT STATS ===
            self.save_history()
            final_downloaded = self._count_downloaded_for_date(date_display)
            if archives_total:
                selisih = archives_total - final_downloaded
                if selisih <= 0:
                    self.log(f"✅ {mode} ({date_display}) LENGKAP: {final_downloaded}/{archives_total} chat | ⬇️{downloaded} baru")
                else:
                    self.log(f"⚠️ {mode} ({date_display}) BELUM LENGKAP: {final_downloaded}/{archives_total} (kurang {selisih}) | ⬇️{downloaded} baru")
            else:
                self.log(f"✅ {mode} ({date_display}) SELESAI: {final_downloaded} terdownload | ⬇️{downloaded} baru")

            # Expose stats untuk outer loop (verifikasi Yesterday saat ganti hari)
            self._last_archives_total = archives_total
            self._last_final_downloaded = final_downloaded

            # Catat ke download stats per hari
            self.update_download_stats(date_display, downloaded, skipped, failed, archives_total)

            return downloaded

        except Exception as e:
            self.log(f"❌ Error download hari {mode}: {e}")
            return 0

    def is_logged_in(self):
        """Cek apakah user benar-benar sudah login di LiveChat (bukan cuma cek URL)."""
        try:
            if not self.driver:
                return False
            current_url = self.driver.current_url
            # Halaman login biasanya di accounts.livechatinc.com atau ada form login
            if "accounts.livechatinc.com" in current_url:
                return False
            # Cek apakah ada elemen yang hanya muncul setelah login
            login_indicators = [
                "input[type='email']",   # form login masih tampil = belum login
                "input[type='password']", # form password masih tampil = belum login
            ]
            for sel in login_indicators:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        return False  # masih di halaman login
                except:
                    continue
            # Cek apakah ada elemen archives (hanya muncul setelah login)
            archive_indicators = [
                "div[class*='archive']",
                "#archives",
                "[data-testid='archive-list']",
                "div[class*='css-1j8yl8o']",
            ]
            for sel in archive_indicators:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        return True
                except:
                    continue
            # Fallback: kalau URL archives dan tidak ada form login = kemungkinan login
            if "/archives" in current_url and "my.livechatinc.com" in current_url:
                return True
            return False
        except Exception:
            return False

    def ensure_logged_in(self, max_retries=3):
        """Pastikan sudah login. Kalau belum, coba auto re-login. Return True jika berhasil."""
        for attempt in range(max_retries):
            if self.is_logged_in():
                return True
            self.log(f"🔐 Belum login (attempt {attempt+1}/{max_retries}). Mencoba auto re-login...")
            try:
                self.driver.get("https://my.livechatinc.com/archives")
                time.sleep(3)
                # Cek lagi setelah navigasi (mungkin cookie masih valid)
                if self.is_logged_in():
                    self.log("✅ Session masih valid setelah refresh.")
                    return True
                # Perlu login ulang
                email = self.entry_lc_email.get()
                password = self.entry_lc_password.get()
                if not email or not password:
                    self.log("❌ Email/password kosong. Tidak bisa auto re-login.")
                    return False
                self.perform_auto_login(email, password)
                # Tunggu login selesai (max 30 detik)
                for _ in range(15):
                    time.sleep(2)
                    if self.is_logged_in():
                        self.log("✅ Auto re-login berhasil!")
                        return True
            except Exception as e:
                self.log(f"⚠️ Re-login attempt {attempt+1} gagal: {str(e)[:60]}")
                time.sleep(3)
        self.log("❌ Gagal login setelah semua percobaan. Butuh login manual.")
        return False

    def _check_driver_alive(self):
        """Cek apakah ChromeDriver masih responsif."""
        try:
            self.driver.title
            return True
        except Exception:
            return False

    def _recover_driver(self):
        """Coba recover ChromeDriver jika connection timeout."""
        try:
            self.log("🔄 ChromeDriver timeout, mencoba recover...")
            # Tunggu Chrome stabil
            time.sleep(5)
            if self._check_driver_alive():
                self.log("✅ ChromeDriver pulih!")
                # Refresh halaman archives
                self.driver.get("https://my.livechatinc.com/archives")
                time.sleep(5)
                # Pastikan masih login setelah recover
                if not self.is_logged_in():
                    self.log("⚠️ ChromeDriver pulih tapi session expired. Re-login...")
                    return self.ensure_logged_in()
                return True
            else:
                self.log("❌ ChromeDriver tidak merespon.")
                return False
        except Exception as e:
            self.log(f"❌ Recover gagal: {str(e)[:60]}")
            return False

    def _try_download_item(self, sel_list, idx, total, downloaded, skipped, failed):
        """Coba download 1 item dari list. Return: 'downloaded', 'skipped', 'failed', 'timeout'."""
        MAX_RETRIES = 3
        for attempt in range(MAX_RETRIES):
            try:
                # Scroll container ke posisi item dulu (penting untuk virtual list)
                try:
                    list_el = self.driver.find_element(By.CSS_SELECTOR, sel_list)
                    self.driver.execute_script(
                        "var el=arguments[0]; var h=el.scrollHeight; var t=arguments[1]; var total=arguments[2]; "
                        "el.scrollTop = Math.max(0, (h * t / total) - 200);", list_el, idx, max(total, 1))
                    time.sleep(0.2)
                except Exception:
                    pass

                items = self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li")
                if idx >= len(items):
                    return "failed"
                item = items[idx]

                if idx % 50 == 0:
                    self.log(f"📌 {idx+1}/{total} | ⬇️{downloaded} ⏭{skipped} ❌{failed}")

                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
                except Exception:
                    pass
                time.sleep(0.1)

                try:
                    self.driver.execute_script("arguments[0].click();", item)
                except Exception:
                    try:
                        item.click()
                    except Exception:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(0.5)
                            continue
                        return "failed"

                time.sleep(0.8)
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
                err_str = str(e).lower()
                # Deteksi connection timeout ke ChromeDriver
                if "connectionpool" in err_str or "read timed" in err_str or "max retries" in err_str or "connection refused" in err_str:
                    self.log(f"⚠️ ChromeDriver timeout item {idx+1}, tunggu 10s lalu retry...")
                    time.sleep(10)
                    if not self._check_driver_alive():
                        if self._recover_driver():
                            continue  # retry setelah recover
                        return "timeout"  # sinyal ke caller untuk stop sementara
                    # Driver masih hidup, retry
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return "failed"
                self.log(f"⚠️ Item {idx} attempt {attempt+1}: {str(e)[:60]}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)
                    continue
                return "failed"
        return "failed"

    def _extract_chat_id_from_item(self, sel_list, idx):
        """Extract chat_id dari list item TANPA klik (via href). Return None jika gagal."""
        try:
            items = self.driver.find_elements(By.CSS_SELECTOR, f"{sel_list} li")
            if idx >= len(items): return None
            item = items[idx]
            # Coba dari <a href="/archives/CHAT_ID">
            try:
                links = item.find_elements(By.CSS_SELECTOR, "a[href*='/archives/']")
                for link in links:
                    href = link.get_attribute("href") or ""
                    if "/archives/" in href:
                        cid = href.split("/archives/")[-1].split("?")[0].split("/")[0]
                        if cid and cid != "archives" and len(cid) > 3:
                            return cid
            except: pass
            # Coba dari <a> biasa
            try:
                links = item.find_elements(By.TAG_NAME, "a")
                for link in links:
                    href = link.get_attribute("href") or ""
                    if "/archives/" in href:
                        cid = href.split("/")[-1].split("?")[0]
                        if cid and cid != "archives" and len(cid) > 3:
                            return cid
            except: pass
            # Coba data attribute
            try:
                for attr in ["data-id", "data-chat-id"]:
                    val = item.get_attribute(attr)
                    if val and len(val) > 3: return val
            except: pass
        except: pass
        return None

    def _detect_sop_category(self, detail, audit_result):
        """Deteksi kategori SOP dari detail kesalahan dan hasil audit AI."""
        text = (detail + " " + audit_result).lower()
        # Urutan penting: cek yang paling spesifik dulu
        if any(k in text for k in [
            "hashtag", "macro bocor", "#dpo",
            "tidak bantu daftarkan", "tidak memproses data pendaftaran", "pendaftaran",
            "sop reset password", "lupa password", "reset password",
            "gagal login", "gagal koneksi"
        ]):
            return "Hashtag & Pendaftaran"
        if any(k in text for k in [
            "deposit", "withdraw", "wd ", " wd", "depo ",
            "dana", "mutasi", "transfer", "rekening",
            "pending", "diproses", "keliru"
        ]):
            return "Respon Deposit & Withdraw"
        if any(k in text for k in [
            "bonus", "cashback", "rebate", "freechip", "promo",
            "hashtag double", "klaim bonus"
        ]):
            return "Bonus"
        if any(k in text for k in [
            "bot tidak nyambung", "bot nyambung", "[bot]",
            "username tidak valid", "id tidak valid",
            "handover", "takeover", "tidak merespon setelah"
        ]):
            return "Kinerja Bot & Handover"
        if any(k in text for k in [
            "qris", "gangguan bank", "bank offline",
            "no rekening", "syarat reset", "dana limit"
        ]):
            return "Teknis & Gangguan"
        if any(k in text for k in [
            "tidak sopan", "capslock", "caps lock", "kasar", "sarkas",
            "slow response", "lambat", "5 menit",
            "salah jawab", "jawaban tidak sesuai", "salah berikan",
            "tidak menyelesaikan", "menggantung", "tidak selesai",
            "no wa", "nomor wa", "whatsapp",
            "rtp", "link alternatif", "bocoran"
        ]):
            return "Etika & Standar Pelayanan"
        return "Umum"

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
                    menu_btn = WebDriverWait(self.driver, 1.5).until(
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

            menu_btn.click(); time.sleep(0.7)

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
            self.log(f"⬇️ Download: {chat_id}")
            time.sleep(0.5)

            # Rename file: LiveChat_transcript_xxx.txt → xxx.txt
            old_file = os.path.join(self.local_in, f"LiveChat_transcript_{chat_id}.txt")
            new_file = os.path.join(self.local_in, f"{chat_id}.txt")
            file_found = False
            for _ in range(15):  # tunggu max 7.5 detik
                if os.path.exists(old_file):
                    try:
                        os.rename(old_file, new_file)
                        file_found = True
                        break
                    except PermissionError:
                        time.sleep(0.5)
                        continue
                    except:
                        file_found = os.path.exists(old_file)  # gagal rename tapi file ada
                        break
                elif os.path.exists(new_file):
                    file_found = True
                    break
                time.sleep(0.5)

            # Verifikasi: file benar-benar ada di disk sebelum catat ke history
            if not file_found and not os.path.exists(new_file) and not os.path.exists(old_file):
                self.log(f"⚠️ File tidak terdownload ke disk: {chat_id}")
                return False

            # Pindahkan ke subfolder tanggal: Data_Chat_Masuk/{YYYY-MM-DD}/
            actual_file = new_file if os.path.exists(new_file) else old_file
            try:
                if os.path.exists(actual_file):
                    with open(actual_file, "r", encoding="utf-8", errors="ignore") as f:
                        preview = f.read(500)
                    # Cek file tidak kosong
                    if len(preview.strip()) < 20:
                        self.log(f"⚠️ File kosong/corrupt: {chat_id}")
                        try: os.remove(actual_file)
                        except: pass
                        return False
                    chat_date = self.extract_chat_date(preview)
                    date_folder = os.path.join(self.local_in, chat_date)
                    if not os.path.exists(date_folder):
                        os.makedirs(date_folder)
                    final_path = os.path.join(date_folder, f"{chat_id}.txt")
                    if not os.path.exists(final_path):
                        shutil.move(actual_file, final_path)
            except: pass  # Jika gagal pindah, file tetap di root (masih bisa diproses)

            self.processed_history.add(chat_id)
            return True
        except TimeoutException:
            self.log(f"⚠️ Timeout menu: {chat_id}")
            return False
        except Exception as e:
            self.log(f"⚠️ Gagal download {chat_id}: {str(e)[:60]}")
            return False

    def organize_batch_folders(self):
        """Rapikan file di Data_Chat_Selesai per batch 250 file per tanggal."""
        try:
            # Batch per web_name/date folder di Data_Chat_Selesai
            BATCH_SIZE = 250
            total_moved = 0

            for web_name in os.listdir(self.local_out):
                web_path = os.path.join(self.local_out, web_name)
                if not os.path.isdir(web_path):
                    continue

                for date_folder in os.listdir(web_path):
                    date_path = os.path.join(web_path, date_folder)
                    if not os.path.isdir(date_path):
                        continue

                    # Hitung file .txt langsung di folder ini (bukan di subfolder)
                    files = sorted([f for f in os.listdir(date_path)
                                    if f.endswith(".txt") and os.path.isfile(os.path.join(date_path, f))])
                    if len(files) <= BATCH_SIZE:
                        continue

                    total_files = len(files)
                    total_batches = (total_files + BATCH_SIZE - 1) // BATCH_SIZE

                    for batch_num in range(total_batches):
                        start = batch_num * BATCH_SIZE
                        end = min(start + BATCH_SIZE, total_files)
                        batch_files = files[start:end]

                        # Nama folder stabil: Batch_1, Batch_2, dst (tanpa range angka)
                        batch_folder = os.path.join(date_path, f"Batch_{batch_num + 1}")
                        if not os.path.exists(batch_folder):
                            os.makedirs(batch_folder)

                        for f in batch_files:
                            src = os.path.join(date_path, f)
                            dst = os.path.join(batch_folder, f)
                            if os.path.exists(src) and not os.path.exists(dst):
                                try:
                                    shutil.move(src, dst)
                                    total_moved += 1
                                except: pass

            if total_moved > 0:
                self.log(f"📂 Batch: {total_moved} file dirapikan di Data_Chat_Selesai")
        except Exception as e:
            self.log(f"⚠️ Batch folder error: {str(e)[:60]}")

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
                # Scan file .txt di root + subfolder tanggal Data_Chat_Masuk/
                file_list = []  # list of (filename, full_path)
                for root_dir, dirs, dir_files in os.walk(self.local_in):
                    for f in dir_files:
                        if f.endswith(".txt"):
                            file_list.append((f, os.path.join(root_dir, f)))
                file_list.sort(key=lambda x: x[0])

                # Jika tidak ada file, tunggu lalu cek lagi (hemat CPU)
                if not file_list:
                    time.sleep(3)
                    consecutive_errors = 0
                    continue

                loop_count += 1
                # Auto reset key index setiap 100 loop (hindari stuck di key terakhir)
                if loop_count % 100 == 0:
                    self.current_key_index = 0
                    self.log("🔄 Reset rotasi key.")

                for filename, file_path in file_list:
                    if not self.is_monitoring: break

                    # Rename jika masih format lama (LiveChat_transcript_xxx.txt → xxx.txt)
                    if filename.startswith("LiveChat_transcript_"):
                        new_name = filename.replace("LiveChat_transcript_", "")
                        new_path = os.path.join(os.path.dirname(file_path), new_name)
                        if not os.path.exists(new_path):
                            try:
                                os.rename(file_path, new_path)
                                filename = new_name
                                file_path = new_path
                            except (PermissionError, FileNotFoundError):
                                # File sedang diakses download thread, skip dulu
                                continue
                            except: pass
                        else:
                            try: os.remove(file_path)
                            except: pass
                            continue

                    # Anti-duplikat: extract chat_id bersih dari filename
                    chat_id_from_file = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                    if chat_id_from_file in self.audited_history:
                        # Jangan hapus langsung — pindahkan ke Data_Chat_Selesai/duplikat
                        try:
                            dup_folder = os.path.join(self.local_out, "_duplikat_skip")
                            if not os.path.exists(dup_folder): os.makedirs(dup_folder)
                            shutil.move(file_path, os.path.join(dup_folder, filename))
                        except:
                            try: os.remove(file_path)
                            except: pass
                        continue

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
                        # Hapus dari processed_history agar bisa di-download ulang
                        self.processed_history.discard(chat_id_from_file)
                        self.save_history()
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
                        audit_result = f"{userid}\nTopik: Normal/Lancar\nLULUS"

                    # --- FILTER GSHEET ---
                    audit_res_up = audit_result.upper()
                    is_failed = "TIDAK LULUS" in audit_res_up
                    is_sop2 = "SOP 2" in audit_res_up or "TEMUAN TOPIK 2" in audit_res_up
                    is_perlu_perbaikan = "PERLU PERBAIKAN" in audit_res_up

                    if is_failed or is_sop2 or is_perlu_perbaikan:
                        status_label = "TIDAK LULUS" if is_failed else "PERLU PERBAIKAN"
                        m = re.search(r"(?:TIDAK LULUS|SOP 2|PERLU PERBAIKAN)\s*[\(\[]\s*(.*?)\s*[\)\]]", audit_result, re.IGNORECASE | re.DOTALL)
                        detail = m.group(1).strip() if m else "Deteksi Temuan"
                        # Bersihkan prefix Tugas/Kategori dari detail
                        detail = re.sub(r"^Tugas\s*\d+\s*[-–:]\s*", "", detail, flags=re.IGNORECASE).strip()
                        detail = re.sub(r"^(?:Hashtag & Pendaftaran|Respon Deposit & Withdraw|Bonus|Etika & Standar Pelayanan|Teknis & Gangguan|Kinerja Bot & (?:Kelalaian )?Handover|Kategori)\s*[-–:]\s*", "", detail, flags=re.IGNORECASE).strip()

                        # --- SPESIFIKKAN DETAIL YANG CAMPUR (Deposit/Withdraw → pilih salah satu) ---
                        content_lower = content.lower()
                        if "deposit/withdraw" in detail.lower() or "deposit/wd" in detail.lower() or "depo/wd" in detail.lower():
                            # Cek dari transcript apakah masalahnya deposit atau withdraw
                            has_wd = any(k in content_lower for k in ["withdraw", " wd ", "tarik dana", "penarikan", "cairkan"])
                            has_depo = any(k in content_lower for k in ["deposit", " depo ", "setor", "transfer masuk", "top up"])
                            if has_wd and not has_depo:
                                detail = detail.replace("Deposit/Withdraw", "Withdraw").replace("deposit/withdraw", "withdraw")
                                detail = detail.replace("Deposit/WD", "WD").replace("deposit/wd", "wd")
                                detail = detail.replace("Depo/WD", "WD").replace("depo/wd", "wd")
                            elif has_depo and not has_wd:
                                detail = detail.replace("Deposit/Withdraw", "Deposit").replace("deposit/withdraw", "deposit")
                                detail = detail.replace("Deposit/WD", "Deposit").replace("deposit/wd", "deposit")
                                detail = detail.replace("Depo/WD", "Deposit").replace("depo/wd", "deposit")
                            # Kalau dua-duanya ada, biarkan yang paling dominan
                            elif has_wd and has_depo:
                                wd_count = sum(content_lower.count(k) for k in ["withdraw", " wd ", "tarik dana"])
                                depo_count = sum(content_lower.count(k) for k in ["deposit", " depo ", "setor"])
                                if wd_count > depo_count:
                                    detail = detail.replace("Deposit/Withdraw", "Withdraw").replace("deposit/withdraw", "withdraw")
                                    detail = detail.replace("Deposit/WD", "WD").replace("deposit/wd", "wd")
                                else:
                                    detail = detail.replace("Deposit/Withdraw", "Deposit").replace("deposit/withdraw", "deposit")
                                    detail = detail.replace("Deposit/WD", "Deposit").replace("deposit/wd", "deposit")

                        # --- DETEKSI KATEGORI SOP DARI DETAIL ---
                        kategori = self._detect_sop_category(detail, audit_result)

                        file_id = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                        row = [chat_date, chat_time, userid, status_label, kategori, detail, file_id]
                        self.send_to_google_sheet(row, web_name)
                        self.increment_stats(is_noteworthy=True, web_name=web_name, cost=chat_cost_idr)
                        if self.saran_ai_enabled and "SARAN AI:" in audit_res_up:
                            saran_match = re.search(r"Saran AI:\s*(.*)", audit_result, re.IGNORECASE)
                            if saran_match: self.log_insight(userid, filename, saran_match.group(1).strip())
                    else:
                        self.increment_stats(is_noteworthy=False, web_name=web_name, cost=chat_cost_idr)

                    self.log(f"💰 Rp {chat_cost_idr:.4f} | Total [{web_name}]: Rp {self.today_stats['details'][web_name]['cost']:,.2f}")

                    # --- TELEGRAM: Hanya kirim yang bermasalah ---
                    if is_failed or is_sop2 or is_perlu_perbaikan:
                        tele_msg = audit_result
                        if not self.saran_ai_enabled:
                            tele_msg = re.sub(r"(?i)\n?Saran AI:.*", "", tele_msg).strip()
                        self.send_telegram_text(tele_msg)

                        if images:
                            try: requests.post(f"https://api.telegram.org/bot{self.entry_tele_token.get()}/sendPhoto", data={"chat_id": self.entry_tele_chatid.get(), "photo": images[0], "caption": f"📸 Gambar dari {userid}"})
                            except: pass
                        if is_failed:
                            try:
                                with open(file_path, 'rb') as f:
                                    requests.post(f"https://api.telegram.org/bot{self.entry_tele_token.get()}/sendDocument", files={'document': f}, data={'chat_id': self.entry_tele_chatid.get(), 'caption': "📜 Transkrip Pelanggaran"})
                            except: pass

                    # --- CEK GAGAL LOGIN / LOADING LAMA (sesuai SOP Tugas 2) ---
                    gagal_keywords = [
                        # Formal
                        "gagal login", "gagal koneksi", "gagal login/koneksi",
                        "tidak bisa login", "tidak bisa masuk", "tidak bisa akses",
                        "login gagal", "masuk gagal", "akses gagal",
                        "loading lama", "loading terlalu lama", "tidak bisa loading",
                        "error login", "login error", "login eror", "eror login",
                        "koneksi error", "connection error", "koneksi lemot", "koneksi lambat",
                        "failed to login", "can't login", "cannot login",
                        # Slang / informal Indonesia
                        "ga bisa login", "gk bisa login", "gak bisa login", "g bisa login",
                        "ga bisa masuk", "gk bisa masuk", "gak bisa masuk", "g bisa masuk",
                        "ga bisa akses", "gk bisa akses", "gak bisa akses",
                        "gabisa login", "gabisa masuk", "gabisa akses",
                        "ngga bisa login", "nga bisa login", "ngga bisa masuk", "nga bisa masuk",
                        "tdk bisa login", "tdk bisa masuk", "tdk bs login", "tdk bs masuk",
                        "ga bs login", "gk bs login", "gak bs login",
                        "susah login", "susah masuk", "sulit login", "sulit masuk",
                        # Akun terkunci / blokir
                        "akun ke lock", "akun kena lock", "akun terkunci", "akun ke-lock",
                        "akun kena blokir", "akun terblokir", "akun diblokir", "akun di blokir",
                        # Web/loading bermasalah
                        "web nya ga kebuka", "web ga kebuka", "web tidak kebuka",
                        "web nya error", "web error", "website error",
                        "web nya lemot", "web lemot", "aplikasi lemot",
                        "loading terus", "loading mulu", "stuck loading", "muter terus",
                        # Provider/game tidak bisa dibuka (PG, Pragmatic, dll)
                        "pg ga bisa dibuka", "pg gk bisa dibuka", "pg gak bisa dibuka",
                        "pg ga bsa dibuka", "pg gk bsa dibuka", "pg tidak bisa dibuka",
                        "pg tdk bisa dibuka", "pg ga kebuka", "pg tidak kebuka", "pg ga bisa di buka",
                        "pg tidak bisa di buka", "pg error", "pg nya error", "pg nya ga bisa dibuka",
                        "pragmatic ga bisa dibuka", "pragmatic gk bisa dibuka", "pragmatic gak bisa dibuka",
                        "pragmatic tidak bisa dibuka", "pragmatic tdk bisa dibuka",
                        "pragmatic tidak bisa di buka", "pragmatic ga bisa di buka",
                        "pragmatic ga kebuka", "pragmatic tidak kebuka",
                        "pragmatic error", "pragmatic nya error", "pragmatic nya ga bisa dibuka",
                        "slot ga bisa dibuka", "slot tidak bisa dibuka", "slot ga kebuka",
                        "game ga bisa dibuka", "game tidak bisa dibuka", "game ga kebuka",
                        "provider ga bisa dibuka", "provider tidak bisa dibuka",
                        "ga bisa dibuka", "tidak bisa dibuka", "tdk bisa dibuka", "ga kebuka",
                    ]
                    audit_lower = (audit_result + "\n" + content).lower()
                    content_lower_full = content.lower()
                    gagal_matched = [kw for kw in gagal_keywords if kw in audit_lower]

                    # Regex fuzzy: WAJIB ada connector (bisa|bs|dapat) supaya "gak masuk" (konteks WD) tidak match
                    fuzzy_pattern = r"\b(?:ga|gk|g|gak|gag|ngga|nga|tdk|tidak|ndak|gabisa|gabs)\s+(?:bisa|bs|dapat|dpt)\s+(?:login|masuk|akses|loading|di\s*buka|dibuka|kebuka)\b"
                    if re.search(fuzzy_pattern, audit_lower):
                        if not gagal_matched:
                            gagal_matched.append("fuzzy:tidak bisa login/masuk/akses/dibuka")

                    # Regex fuzzy: provider (pg/pragmatic/slot/game) + (ga/tidak) + bisa + (dibuka/kebuka/akses)
                    provider_pattern = r"\b(?:pg|pragmatic|slot|game|provider)\s+(?:soft\s+)?(?:nya\s+)?(?:ga|gk|g|gak|ngga|tdk|tidak|ndak)\s*(?:bisa|bs)?\s*(?:di\s*buka|dibuka|kebuka|akses|diakses|dimainkan|main)\b"
                    if re.search(provider_pattern, audit_lower):
                        if not any("provider" in m for m in gagal_matched):
                            gagal_matched.append("fuzzy:provider tidak bisa dibuka")

                    # ANTI-FALSE-POSITIVE: kalau chat konteks WD/Depo/saldo dan tidak ada kata 'login' eksplisit, skip flag
                    if gagal_matched:
                        wd_context_kw = ["wd", "withdraw", "tarik dana", "penarikan", "cairkan",
                                         "deposit", "depo", "setor", "transfer", "top up",
                                         "saldo", "dana", "uang", "duit", "rekening", "belum masuk",
                                         "gak masuk", "ga masuk", "blm masuk", "belom masuk"]
                        login_context_kw = ["login", "loging", "log in", "masuk akun", "akun saya",
                                            "password", "username", "user id", "user name", "akses akun",
                                            "akun terblokir", "akun ke-lock", "akun kena", "akun tidak"]
                        has_wd_context = any(kw in content_lower_full for kw in wd_context_kw)
                        has_login_context = any(kw in content_lower_full for kw in login_context_kw)
                        # Hanya skip kalau ada konteks WD TAPI tidak ada konteks login sama sekali
                        if has_wd_context and not has_login_context:
                            # Cek tambahan: kalau audit_result eksplisit bilang gagal login, tetap flag
                            ai_explicit_login = any(kw in audit_lower for kw in [
                                "gagal login", "tidak bisa login", "ga bisa login", "gk bisa login",
                                "login error", "error login"])
                            if not ai_explicit_login:
                                self.log(f"⚠️ Gagal-login keyword match tapi konteks WD/Depo. Skip flag: {gagal_matched[:2]}")
                                gagal_matched = []

                    is_gagal_login = len(gagal_matched) > 0

                    # --- CEK LUPA PASSWORD ---
                    lupa_pw_keywords = ["lupa password", "lupa pass", "lupa kata sandi",
                                        "reset password", "reset pass", "ganti password",
                                        "forgot password", "change password", "ubah password",
                                        "tidak bisa login password", "password salah", "wrong password"]
                    content_lower = content.lower()
                    is_lupa_password = any(kw in content_lower for kw in lupa_pw_keywords)

                    date_folder = chat_date if chat_date else datetime.now().strftime("%Y-%m-%d")
                    if is_gagal_login:
                        # Catat ke sheet tab "GAGAL LOGIN"
                        file_id = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                        gagal_detail = gagal_matched[0].title()
                        gagal_row = [chat_date, chat_time, userid, "GAGAL LOGIN", web_name, gagal_detail, file_id]
                        self.send_to_google_sheet(gagal_row, "GAGAL LOGIN")
                        self.log(f"🚫 GAGAL LOGIN/LOADING: {userid} → sheet GAGAL LOGIN")
                        # Pindah ke folder GAGAL LOGIN
                        gagal_folder = os.path.join(self.local_out, "GAGAL LOGIN", date_folder)
                        if not os.path.exists(gagal_folder): os.makedirs(gagal_folder)
                        try:
                            shutil.move(file_path, os.path.join(gagal_folder, filename))
                        except Exception as mv_err:
                            self.log(f"⚠️ Gagal pindah file {filename}: {str(mv_err)[:40]}")
                    elif is_lupa_password:
                        # Catat ke sheet tab "LUPA PASSWORD"
                        file_id = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                        lupa_row = [chat_date, chat_time, userid, "LUPA PASSWORD", web_name, "", file_id]
                        self.send_to_google_sheet(lupa_row, "LUPA PASSWORD")
                        self.log(f"🔑 LUPA PASSWORD: {userid} → sheet LUPA PASSWORD")
                        # Pindah ke folder web_name/tanggal seperti biasa
                        t_folder = os.path.join(self.local_out, web_name, date_folder)
                        if not os.path.exists(t_folder): os.makedirs(t_folder)
                        try: shutil.move(file_path, os.path.join(t_folder, filename))
                        except Exception as mv_err:
                            self.log(f"⚠️ Gagal pindah file {filename}: {str(mv_err)[:40]}")
                    else:
                        t_folder = os.path.join(self.local_out, web_name, date_folder)
                        if not os.path.exists(t_folder): os.makedirs(t_folder)
                        try: shutil.move(file_path, os.path.join(t_folder, filename))
                        except Exception as mv_err:
                            self.log(f"⚠️ Gagal pindah file {filename}: {str(mv_err)[:40]}")

                    # Catat ke audited history (anti-duplikat) — simpan setiap file
                    self.audited_history.add(chat_id_from_file)
                    self.save_audited_history()
                    # Rapikan batch di Data_Chat_Selesai setiap 250 file
                    if len(self.audited_history) % 250 == 0:
                        self.organize_batch_folders()

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

        # Simpan audited history saat monitoring berhenti
        self.save_audited_history()

    def screening_content(self, api_key_string, content, userid, chat_time, chat_date, mode_audit):
        """Tahap 1: Screening cepat — hanya jawab LULUS atau PERIKSA. Hemat ~70% cost."""
        raw_keys = api_key_string.split(',')
        api_keys = []
        seen_keys = set()
        for k in raw_keys:
            clean_k = k.strip().replace("'", "").replace('"', "")
            if clean_k and clean_k not in seen_keys:
                api_keys.append(clean_k); seen_keys.add(clean_k)
        if not api_keys: return "PERIKSA", 0, 0

        model_map = {
            "GEMINI 2.0 FLASH LITE (GRATIS)":     ["gemini-2.0-flash-lite"],
            "GEMINI 1.5 FLASH-8B (TERMURAH)":      ["gemini-2.0-flash-lite"],
            "GEMINI 1.5 FLASH (MURAH & STABIL)":   ["gemini-2.0-flash"],
            "GEMINI 2.0 FLASH (STANDAR)":           ["gemini-2.0-flash"],
            "GEMINI 2.5 FLASH (PREMIUM)":           ["gemini-2.5-flash"],
            "GEMINI 3 FLASH PREVIEW":               ["gemini-3-flash-preview"],
        }
        models_to_try = model_map.get(mode_audit, ["gemini-2.5-flash"])

        compressed = self.compress_transcript(content)

        system_screening = """Anda screener QA livechat. Tugas: tentukan apakah chat ini perlu audit detail.
Jawab HANYA satu kata: LULUS atau PERIKSA.

LULUS HANYA jika salah satu kondisi ini terpenuhi:
- Member GHOSTING: tidak ada pesan manual sama sekali dari member setelah greeting Bot/CS
- SPAM/TEST CHAT: member hanya kirim karakter acak atau langsung pergi

SEMUA kondisi lain = PERIKSA, termasuk:
- Ada percakapan antara member dan CS/Bot (apapun topiknya)
- Ada keluhan, pertanyaan, atau permintaan dari member
- Ada topik deposit, withdraw, bonus, password, akun
- Ada handover atau transfer ke CS
- Chat ditangani Bot maupun Human

Jika ragu, jawab PERIKSA.
Jawab HANYA: LULUS atau PERIKSA"""

        prompt = f"""{userid} | {chat_date} {chat_time}
---
{compressed}"""

        self.current_key_index = self.current_key_index % len(api_keys)

        for model_name in models_to_try:
            for key_attempt in range(len(api_keys)):
                if not self.is_monitoring and not self.is_auto_today: return "STOPPED", 0, 0
                active_key = api_keys[self.current_key_index % len(api_keys)]
                key_display = f"...{active_key[-4:]}" if len(active_key) > 4 else "???"

                cooldown_until = self.key_cooldowns.get(key_display, 0)
                if time.time() < cooldown_until:
                    self.current_key_index += 1
                    continue

                try:
                    genai.configure(api_key=active_key)
                    self.log(f"🔍 Screening {model_name} | Key: {key_display}")
                    model = genai.GenerativeModel(model_name, system_instruction=system_screening)
                    response = model.generate_content(
                        [prompt],
                        safety_settings={
                            'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                            'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                            'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                            'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE',
                        },
                        generation_config=genai.types.GenerationConfig(max_output_tokens=10, temperature=0)
                    )

                    self.key_cooldowns.pop(key_display, None)
                    pricing = self.model_pricing.get(model_name, {"input": 0.075, "output": 0.30})
                    self.price_input_1m = pricing["input"]
                    self.price_output_1m = pricing["output"]

                    t_in = response.usage_metadata.prompt_token_count
                    t_out = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0

                    try:
                        result = response.text.strip().upper()
                    except (ValueError, AttributeError):
                        result = "PERIKSA"

                    verdict = "LULUS" if "LULUS" in result else "PERIKSA"
                    self.log(f"🔍 Screening: {verdict} | Token: {t_in}+{t_out}")
                    return verdict, t_in, t_out

                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str or "Resource has been exhausted" in error_str:
                        self.key_cooldowns[key_display] = time.time() + 60
                        self.current_key_index += 1
                        continue
                    elif "400" in error_str and "API_KEY_INVALID" in error_str:
                        self.key_cooldowns[key_display] = time.time() + 3600
                        self.current_key_index += 1
                        continue
                    else:
                        self.log(f"⚠️ Screening error: {error_str[:60]}")
                        return "PERIKSA", 0, 0

        # Jika semua gagal, default PERIKSA (aman, akan di-audit detail)
        self.log("⚠️ Screening gagal, default PERIKSA")
        return "PERIKSA", 0, 0

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

ATURAN PENTING TAMBAHAN:
- Bot bertanya "ada yang bisa dibantu kembali" atau closing setelah proses deposit/WD/masalah selesai = LULUS, ini BUKAN "Jawaban Bot Tidak Nyambung".
- "Jawaban Bot Tidak Nyambung" HANYA jika Bot menjawab topik yang SAMA SEKALI BERBEDA dari pertanyaan member (contoh: member tanya WD tapi Bot jawab cara daftar).
- Jika ada baris [SYSTEM] yang menunjukkan handover/transfer ke CS tapi CS tidak merespon, vonis = "CS Tidak Merespon Setelah Handover", BUKAN "Jawaban Bot Tidak Nyambung".

FORMAT OUTPUT WAJIB:
[USERID]
Topik: [Kategori]
[STATUS: LULUS / TIDAK LULUS (Poin) / SOP 2 (Poin)]
Analisa: [1-2 kalimat singkat alasan vonis]"""

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
                            },
                            generation_config=genai.types.GenerationConfig(max_output_tokens=500, temperature=0)
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
                                result_text = f"{userid}\nTopik: Blocked by Safety Filter\nPERLU PERBAIKAN (Response diblokir safety filter — perlu review manual)"

                        if not result_text:
                            result_text = f"{userid}\nTopik: Empty Response\nPERLU PERBAIKAN (AI tidak memberikan response — perlu review manual)"

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

                        elif "Unable to process input image" in error_str or "image" in error_str.lower() and "400" in error_str:
                            self.log(f"⚠️ Gambar error, retry tanpa gambar...")
                            image_data = None
                            parts = [prompt]
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
        """Resolve path JSON: cek bundled resource, relatif ke app_path, atau absolut."""
        if not json_path: return json_path
        if os.path.isabs(json_path) and os.path.exists(json_path):
            return json_path
        # Coba relatif ke folder EXE/script
        resolved = os.path.join(self.app_path, json_path)
        if os.path.exists(resolved):
            return resolved
        # Coba dari bundled resource (PyInstaller _MEIPASS)
        if getattr(sys, 'frozen', False):
            bundled = os.path.join(sys._MEIPASS, json_path)
            if os.path.exists(bundled):
                # Copy ke app_path agar bisa dipakai
                try:
                    import shutil
                    dest = os.path.join(self.app_path, json_path)
                    shutil.copy2(bundled, dest)
                    return dest
                except:
                    return bundled
        return json_path

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
        if not GSPREAD_AVAILABLE:
            self.log(f"⚠️ GSheet SKIP ({target_tab_name}): gspread library tidak tersedia")
            return
        j = self.resolve_json_path(self.entry_gsheet_json.get().strip())
        n = self.entry_gsheet_name.get().strip()
        if not j or not n:
            self.log(f"⚠️ GSheet SKIP ({target_tab_name}): config kosong (json={bool(j)}, name={bool(n)})")
            return

        target_tab_upper = str(target_tab_name).strip().upper() or "OTHERS"
        file_id = data_row[-1] if data_row else ""

        # Retry 3x untuk handle transient API errors
        last_err = None
        for attempt in range(1, 4):
            try:
                scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_name(j, scope)
                client = gspread.authorize(creds)
                spreadsheet = client.open_by_url(n) if "docs.google.com" in n else client.open(n)

                # Case-insensitive tab lookup (handle "GAGAL LOGIN" vs "Gagal Login")
                worksheet = None
                try:
                    all_ws = spreadsheet.worksheets()
                    for ws in all_ws:
                        if ws.title.strip().upper() == target_tab_upper:
                            worksheet = ws
                            break
                except Exception:
                    pass

                if worksheet is None:
                    # Belum ada → buat baru dengan nama upper-case
                    worksheet = spreadsheet.add_worksheet(title=target_tab_upper, rows=1000, cols=10)
                    worksheet.append_row(["Tanggal Chat", "Jam", "UserID", "Status", "Topik", "Detail/Saran", "Filename"])
                    self.log(f"🆕 GSheet buat tab baru: {target_tab_upper}")

                # Cek duplikat berdasarkan file_id di kolom 7
                if file_id:
                    try:
                        existing = worksheet.col_values(7)
                        if file_id in existing:
                            self.log(f"⏭ Sheet skip duplikat: [{target_tab_upper}] {file_id}")
                            return
                    except Exception as dup_err:
                        self.log(f"⚠️ Cek duplikat gagal (lanjut append): {str(dup_err)[:40]}")

                worksheet.append_row(data_row)
                self.log(f"✅ GSheet tulis [{target_tab_upper}]: {file_id}")
                return  # sukses

            except Exception as e:
                last_err = e
                err_str = str(e)[:80]
                if attempt < 3:
                    self.log(f"⚠️ GSheet attempt {attempt}/3 gagal [{target_tab_upper}]: {err_str} — retry 3s...")
                    time.sleep(3)
                else:
                    self.log(f"❌ GSheet GAGAL 3x [{target_tab_upper}]: {err_str} | Data: {data_row[:3]}")

        # Semua retry gagal → simpan ke backup
        try:
            backup_file = os.path.join(self.app_path, "gsheet_backup.json")
            backup_data = []
            if os.path.exists(backup_file):
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)
            backup_data.append({"tab": target_tab_upper, "row": data_row, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "error": str(last_err)[:100]})
            with open(backup_file, "w") as f:
                json.dump(backup_data, f, indent=2)
            self.log(f"💾 Disimpan ke gsheet_backup.json: {file_id}")
        except Exception as bk_err:
            self.log(f"❌ Backup JSON juga gagal: {str(bk_err)[:40]}")

    def save_config_silent(self):
        """Simpan config tanpa popup."""
        c = {
            "api_key": self.entry_api.get(), "tele_token": self.entry_tele_token.get(), "tele_chatid": self.entry_tele_chatid.get(),
            "lc_email": self.entry_lc_email.get(), "lc_pass": self.entry_lc_password.get(),
            "gsheet_name": self.entry_gsheet_name.get(), "gsheet_json": self.entry_gsheet_json.get(),
            "audit_mode": self.combo_audit_mode.get(), "headless": self.headless_var.get(),
            "saran_ai_enabled": self.saran_ai_enabled
        }
        with open(self.config_file, "w") as f: json.dump(c, f)

    def save_config(self):
        c = {
            "api_key": self.entry_api.get(), "tele_token": self.entry_tele_token.get(), "tele_chatid": self.entry_tele_chatid.get(),
            "lc_email": self.entry_lc_email.get(), "lc_pass": self.entry_lc_password.get(),
            "gsheet_name": self.entry_gsheet_name.get(), "gsheet_json": self.entry_gsheet_json.get(),
            "audit_mode": self.combo_audit_mode.get(), "headless": self.headless_var.get(),
            "saran_ai_enabled": self.saran_ai_enabled
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
                    # Load saran AI setting
                    self.saran_ai_enabled = c.get("saran_ai_enabled", True)
                    if not self.saran_ai_enabled:
                        self.btn_saran_ai.config(text="💡 SARAN AI: OFF", bg="#9E9E9E")
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
        # Sinkronkan dengan file yang sudah ada di disk
        self.sync_history_from_files()

    def sync_history_from_files(self):
        """Scan file di Data_Chat_Masuk dan tambahkan ke history jika belum tercatat.
        Ini mencegah re-download dan memperbaiki history yang hilang/kosong."""
        try:
            added = 0
            for root_dir, dirs, files in os.walk(self.local_in):
                for filename in files:
                    if not filename.endswith(".txt"):
                        continue
                    # Extract chat_id dari nama file
                    chat_id = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                    if chat_id and chat_id not in self.processed_history:
                        self.processed_history.add(chat_id)
                        added += 1
            # Scan juga Data_Chat_Selesai
            for root_dir, dirs, files in os.walk(self.local_out):
                for filename in files:
                    if not filename.endswith(".txt"):
                        continue
                    chat_id = filename.replace("LiveChat_transcript_", "").replace(".txt", "")
                    if chat_id and chat_id not in self.processed_history:
                        self.processed_history.add(chat_id)
                        added += 1
            if added > 0:
                self.save_history()
                self.log(f"🔄 History sync: {added} file ditemukan di disk, ditambahkan ke history. Total: {len(self.processed_history)}")
        except Exception as e:
            self.log(f"⚠️ Sync history error: {str(e)[:60]}")

    def save_history(self):
        try:
            with open(self.history_file, "w") as f: json.dump(list(self.processed_history), f)
            self.lbl_history_count.config(text=f"History: {len(self.processed_history)}")
        except: pass

    def reset_history(self):
        if messagebox.askyesno("Reset", "Hapus history?"):
            self.processed_history = set(); self.save_history(); self.log("History direset.")

    def load_audited_history(self):
        if os.path.exists(self.audited_history_file):
            try:
                with open(self.audited_history_file, "r") as f: self.audited_history = set(json.load(f))
            except: pass

    def save_audited_history(self):
        try:
            with open(self.audited_history_file, "w") as f: json.dump(list(self.audited_history), f)
        except: pass

if __name__ == "__main__":
    root = tk.Tk()
    app = BrowserAuditApp(root)
    # Cek update di background setelah UI siap
    threading.Thread(target=check_for_update_on_start, daemon=True).start()
    root.mainloop()