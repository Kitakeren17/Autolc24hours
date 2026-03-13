import google.generativeai as genai
import os
import sys  # <--- INI YANG KURANG TADI
import json

# --- SETUP PATH OTOMATIS ---
if getattr(sys, 'frozen', False):
    app_path = os.path.dirname(sys.executable)
else:
    app_path = os.path.dirname(os.path.abspath(__file__))

config_file = os.path.join(app_path, "config.json")

def cek_model():
    api_key = ""
    
    # 1. Coba ambil dari Config
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                data = json.load(f)
                api_key = data.get("api_key", "")
                if api_key:
                    print(f"ℹ️ Menggunakan API Key dari: {config_file}")
        except: pass
    
    # 2. Jika tidak ada di config, minta input manual
    if not api_key:
        print("API Key tidak ditemukan di config.json")
        api_key = input("Masukkan Google Gemini API Key Anda: ").strip()

    if not api_key:
        print("API Key kosong. Program berhenti.")
        return

    # 3. Mulai Pengecekan
    print(f"\nMenghubungi Google dengan API Key: {api_key[:5]}...*****")
    genai.configure(api_key=api_key)

    try:
        print("\n=== DAFTAR MODEL YANG TERSEDIA ===")
        found_any = False
        
        # List semua model
        for m in genai.list_models():
            # Filter hanya model yang bisa generate text (bukan model embedding)
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ {m.name}")
                print(f"   (Deskripsi: {m.display_name})")
                print("-" * 30)
                found_any = True
        
        if not found_any:
            print("❌ Tidak ada model yang ditemukan. Cek apakah API Key valid atau ada kuota.")
        else:
            print("\nSUKSES! Gunakan salah satu nama model di atas (misal: gemini-2.5-pro) ke dalam aplikasi.")

    except Exception as e:
        print(f"\n❌ TERJADI ERROR: {str(e)}")
        print("Saran: Cek koneksi internet atau API Key Anda.")

if __name__ == "__main__":
    cek_model()
    input("\nTekan Enter untuk keluar...")