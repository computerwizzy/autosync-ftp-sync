import requests
import csv
import os
import ftplib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

API_KEY  = os.environ.get('AUTOSYNC_KEY')
BASE_URL = "https://api.autosyncstudio.com"
OUT_FILE = os.path.join(BASE_DIR, 'scripts', 'scratch', 'Tires_Inventory.csv')
FTP_HOST = os.environ.get('FTP_HOST')
FTP_USER = os.environ.get('FTP_USER')
FTP_PASS = os.environ.get('FTP_PASS')

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


def fetch(endpoint, params):
    params['key'] = API_KEY
    for attempt in range(5):
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=60)
            if r.status_code == 200:
                data = r.json()
                if 'Error' not in data:
                    return data
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    return None


def crawl(brand):
    items = []
    offset = 0
    consecutive_failures = 0
    while True:
        data = fetch('tires', {
            'limit': 10, 'offset': offset,
            'f-brand': brand, 'i-inventory': 'true', 'i-price': 'true'
        })
        if not data or 'Tires' not in data or not data['Tires']:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                break
            time.sleep(5)
            continue
        consecutive_failures = 0
        items.extend(data['Tires'])
        if not data.get('MoreItems', False):
            break
        offset += 10
    return brand, items


def main():
    if not API_KEY:
        print("ERROR: AUTOSYNC_KEY not set")
        return

    print("=== AutoSync Tires FTP Sync ===")
    results = []

    print(f"Pulling {len(TIRE_BRANDS)} tire brands (5 parallel workers)...", flush=True)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(crawl, brand): brand for brand in TIRE_BRANDS}
        for future in as_completed(futures):
            brand, items = future.result()
            if items:
                print(f"  {brand}: {len(items)}", flush=True)
                for t in items:
                    results.append({
                        'Brand': t.get('Brand', brand),
                        'DisplayName': t.get('DisplayName', ''),
                        'PartNumber': t.get('PartNumber', ''),
                        'Price': f"{float(t.get('Price') or 0):.2f}",
                        'Inventory': t.get('Inventory', 0)
                    })

    if not results:
        print("No items found. Aborting.")
        return

    print(f"\nTotal tires: {len(results)}", flush=True)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['Brand', 'DisplayName', 'PartNumber', 'Price', 'Inventory'])
        writer.writeheader()
        writer.writerows(results)

    print(f"Uploading to {FTP_HOST}...", flush=True)
    try:
        ftp = ftplib.FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)
        with open(OUT_FILE, 'rb') as f:
            ftp.storbinary("STOR Tires_Inventory.csv", f)
        ftp.quit()
        print("Done: Tires_Inventory.csv uploaded to ftp.wheelsbelowretail.com")
    except Exception as e:
        print(f"FTP error: {e}")


if __name__ == "__main__":
    main()
