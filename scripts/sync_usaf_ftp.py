import requests
import csv
import os
import ftplib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

API_KEY   = os.environ.get('AUTOSYNC_KEY')
BASE_URL  = "https://api.autosyncstudio.com"
OUT_FILE  = os.path.join(BASE_DIR, 'scripts', 'scratch', 'USAF_Inventory.csv')
FTP_HOST  = os.environ.get('FTP_HOST')
FTP_USER  = os.environ.get('FTP_USER')
FTP_PASS  = os.environ.get('FTP_PASS')

TIRE_BRANDS = [
    'Hercules', 'Ironman', 'Accelera', 'Advanta', 'Adventure', 'Alliance',
    'Americus', 'Atturo', 'BFGoodrich', 'Bridgestone', 'Carlstar', 'Continental',
    'Cooper', 'Countrywide', 'Deestone', 'Delinte', 'Dunlop', 'Eurogrip',
    'Falken', 'Firestone', 'Fuzion', 'Galaxy', 'General', 'Gladiator',
    'Goodyear', 'GT Radial', 'Hankook', 'Harvest King', 'Kelly', 'Kenda',
    'Kumho', 'Laufenn', 'Mastercraft', 'Michelin', 'Mickey Thompson', 'Mitas',
    'Multi Mile', 'Nexen', 'Nitto', 'Nokian', 'Omni', 'Pirelli', 'Power King',
    'Retreads', 'Roadmaster', 'Sailun', 'SOLIDMAX', 'Starfire', 'Sumitomo',
    'TAG Nexen', 'Towmax', 'Toyo', 'Trelleborg', 'Uniroyal', 'Yokohama'
]

WHEEL_BRANDS = [
    'TIS', 'Dropstars', 'Motiv', 'Motiv Offroad', 'Edge Off Road',
    'OEP', 'Pacer', 'Konig', 'Revenge Offroad', 'Xtreme Force', 'Hardrock Offroad',
    'Fuel Off-Road', 'Moto Metal', 'KMC', 'American Racing', 'XD', 'Niche',
    'Rotiform', 'Black Rhino', 'Asanti', 'US Mags', 'Dub', 'Foose',
    'Cali Offroad', 'Mayhem', '4PLAY', 'Allied', 'Axe', 'Azara',
    'American Truxx', 'Arena', 'ATX', 'Bad Roads Offroad', 'Ballistic',
    'Black Rock', 'BMF', 'Boss', 'Cavallo', 'Centerline',
    'Cragar', 'Defense', 'Delta', 'Dick Cepek', 'DWT', 'Eagle Alloy',
    'Enkei', 'Fast', 'Gear Alloy', 'Grid', 'Helo', 'ION Alloy',
    'Katana', 'Level 8', 'Lexani', 'Liquid Metal', 'Luxxx', 'MB Wheels',
    'Method', 'MHT', 'MSA', 'Nutek', 'Panther', 'Platinum', 'Pro Comp',
    'Raceline', 'RBP', 'Ridler', 'Rock Star', 'Rosso', 'Rovos',
    'Sendel', 'Sota', 'Touren', 'Tuff', 'Ultra', 'US Wheels',
    'Valor', 'Verde', 'Versante', 'Vision', 'Vossen', 'Weld',
    'XF Off-Road', 'Fittipaldi Offroad', 'Lock Offroad', 'Revenge Luxury',
    'American Force', 'Amani Forged', 'American Design Factory'
]


def fetch(endpoint, params):
    params['key'] = API_KEY
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=45)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2)
    return None


def crawl(endpoint, json_key, brand):
    items = []
    offset = 0
    while True:
        data = fetch(endpoint, {
            'limit': 10, 'offset': offset,
            'f-brand': brand, 'i-inventory': 'true', 'i-price': 'true'
        })
        if not data or json_key not in data or not data[json_key]:
            break
        items.extend(data[json_key])
        if not data.get('MoreItems', False):
            break
        offset += 10
    return brand, items


def pull_all(endpoint, json_key, brands, item_type, workers=10):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(crawl, endpoint, json_key, brand): brand for brand in brands}
        for future in as_completed(futures):
            brand, items = future.result()
            if items:
                print(f"  {brand}: {len(items)}", flush=True)
                for item in items:
                    if item_type == 'Tire':
                        results.append({
                            'Type': 'Tire',
                            'Brand': item.get('Brand', brand),
                            'Model': item.get('Model', ''),
                            'PartNumber': item.get('PartNumber', ''),
                            'Price': f"{float(item.get('Price') or 0):.2f}",
                            'Inventory': item.get('Inventory', 0)
                        })
                    else:
                        results.append({
                            'Type': 'Wheel',
                            'Brand': item.get('Brand', brand),
                            'Model': item.get('Model', ''),
                            'PartNumber': item.get('Pn', ''),
                            'Price': f"{float(item.get('Price') or 0):.2f}",
                            'Inventory': item.get('Inventory', 0)
                        })
    return results


def main():
    if not API_KEY:
        print("ERROR: AUTOSYNC_KEY not set")
        return

    print("=== AutoSync FTP Inventory Sync ===")

    # --- TIRES (10 brands in parallel) ---
    print(f"Pulling {len(TIRE_BRANDS)} tire brands (parallel)...", flush=True)
    results = pull_all('tires', 'Tires', TIRE_BRANDS, 'Tire', workers=10)

    # --- WHEELS (10 brands in parallel) ---
    print(f"Pulling {len(WHEEL_BRANDS)} wheel brands (parallel)...", flush=True)
    results += pull_all('wheels', 'Wheels', WHEEL_BRANDS, 'Wheel', workers=10)

    if not results:
        print("No items found. Aborting.")
        return

    print(f"\nTotal items: {len(results)}", flush=True)

    # --- SAVE ---
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['Type', 'Brand', 'Model', 'PartNumber', 'Price', 'Inventory'])
        writer.writeheader()
        writer.writerows(results)

    # --- FTP UPLOAD ---
    print(f"Uploading to {FTP_HOST}...", flush=True)
    try:
        ftp = ftplib.FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)
        with open(OUT_FILE, 'rb') as f:
            ftp.storbinary("STOR USAF_Inventory.csv", f)
        ftp.quit()
        print("Done: USAF_Inventory.csv uploaded to ftp.wheelsbelowretail.com")
    except Exception as e:
        print(f"FTP error: {e}")


if __name__ == "__main__":
    main()
