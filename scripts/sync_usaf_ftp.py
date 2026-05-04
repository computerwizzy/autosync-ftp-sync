import csv
import os
import ftplib
import io
import time
import requests
from collections import defaultdict
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

SRC_HOST  = os.environ.get('USAUTOFORCE_FTP_HOST')
SRC_USER  = os.environ.get('USAUTOFORCE_FTP_USER')
SRC_PASS  = os.environ.get('USAUTOFORCE_FTP_PASS')
SRC_DIR   = '/uploads/usautoforce'
SRC_FILE  = '1290215.csv'

DST_HOST  = os.environ.get('FTP_HOST')
DST_USER  = os.environ.get('FTP_USER')
DST_PASS  = os.environ.get('FTP_PASS')
DST_FILE  = 'US_AutoForce_Inventory.csv'

SHOPIFY_STORE_URL    = os.environ.get('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN')
SHOPIFY_LOCATION_ID  = 'gid://shopify/Location/91693121771'  # US Auto Force Birmingham

OUT_FILE = os.path.join(BASE_DIR, 'scripts', 'scratch', DST_FILE)


def download_usaf_csv():
    print(f"Connecting to {SRC_HOST} as {SRC_USER}...", flush=True)
    buf = io.BytesIO()
    ftp = ftplib.FTP(SRC_HOST, timeout=120)
    ftp.login(SRC_USER, SRC_PASS)
    ftp.cwd(SRC_DIR)
    size = ftp.size(SRC_FILE)
    print(f"Downloading {SRC_FILE} ({size/1024/1024:.1f} MB)...", flush=True)
    ftp.retrbinary(f'RETR {SRC_FILE}', buf.write)
    ftp.quit()
    buf.seek(0)
    print("Download complete.", flush=True)
    return buf


def process(buf):
    text = buf.read().decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(text))

    agg = defaultdict(lambda: {
        'BrandCode': '', 'Pattern': '', 'TireSize': '',
        'Cost': '', 'RetailPrice': '', 'Map': '',
        'Inventory': 0
    })

    row_count = 0
    for row in reader:
        row_count += 1
        pn = row.get('PartNumber', '').strip()
        if not pn:
            continue
        qty = int(float(row.get('QuantityAvailable', 0) or 0))
        entry = agg[pn]
        if not entry['BrandCode']:
            entry['BrandCode']   = row.get('BrandCode', '').strip()
            entry['Pattern']     = row.get('Pattern', '').strip()
            entry['TireSize']    = row.get('TireSize', '').strip()
            entry['Cost']        = row.get('Cost', '').strip()
            entry['RetailPrice'] = row.get('RetailPrice', '').strip()
            entry['Map']         = row.get('Map', '').strip()
        entry['Inventory'] += qty

    print(f"Processed {row_count:,} rows -> {len(agg):,} unique part numbers.", flush=True)
    return agg


def write_csv(agg):
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    fields = ['BrandCode', 'PartNumber', 'Pattern', 'TireSize', 'Cost', 'RetailPrice', 'Map', 'Inventory']
    with open(OUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for pn, v in sorted(agg.items()):
            writer.writerow({
                'BrandCode':   v['BrandCode'],
                'PartNumber':  pn,
                'Pattern':     v['Pattern'],
                'TireSize':    v['TireSize'],
                'Cost':        v['Cost'],
                'RetailPrice': v['RetailPrice'],
                'Map':         v['Map'],
                'Inventory':   v['Inventory'],
            })
    print(f"Saved {len(agg):,} rows to {OUT_FILE}", flush=True)


def upload_csv():
    print(f"Uploading {DST_FILE} to {DST_HOST}...", flush=True)
    ftp = ftplib.FTP(DST_HOST, timeout=60)
    ftp.login(DST_USER, DST_PASS)
    with open(OUT_FILE, 'rb') as f:
        ftp.storbinary(f'STOR {DST_FILE}', f)
    ftp.quit()
    print(f"Done: {DST_FILE} uploaded to {DST_HOST}", flush=True)


def shopify_graphql_request(query, variables=None):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-01/graphql.json"
    headers = {'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN, 'Content-Type': 'application/json'}
    while True:
        try:
            r = requests.post(url, headers=headers, json={'query': query, 'variables': variables})
            data = r.json()
            if 'errors' in data:
                for error in data['errors']:
                    if error.get('extensions', {}).get('code') == 'THROTTLED':
                        time.sleep(5)
                        break
                else:
                    return data
            else:
                cost = data.get('extensions', {}).get('cost', {})
                if cost.get('throttleStatus', {}).get('currentlyAvailable', 1000) < 500:
                    time.sleep(2)
                return data
        except Exception as e:
            print(f"Shopify request error: {e}, retrying...", flush=True)
            time.sleep(5)


def sync_shopify(agg):
    print("Fetching Shopify variants...", flush=True)
    shopify_data = {}
    has_next_page = True
    cursor = None
    while has_next_page:
        query = """
        query getVariants($cursor: String) {
          productVariants(first: 250, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node { id sku inventoryItem { id } } }
          }
        }
        """
        res = shopify_graphql_request(query, {'cursor': cursor})
        if not res or 'data' not in res:
            break
        for edge in res['data']['productVariants']['edges']:
            node = edge['node']
            if node['sku']:
                shopify_data[node['sku']] = node
        page_info = res['data']['productVariants']['pageInfo']
        has_next_page = page_info.get('hasNextPage', False)
        cursor = page_info.get('endCursor')

    print(f"Shopify variants loaded: {len(shopify_data):,}", flush=True)

    matched = [(node, agg[sku]['Inventory']) for sku, node in shopify_data.items() if sku in agg]

    if not matched:
        print("No matching SKUs found in Shopify.", flush=True)
        return

    print(f"Matched {len(matched):,} SKUs. Syncing quantities...", flush=True)

    items_to_set = [
        {'inventoryItemId': node['inventoryItem']['id'], 'locationId': SHOPIFY_LOCATION_ID, 'quantity': qty}
        for node, qty in matched
    ]
    for i in range(0, len(items_to_set), 100):
        batch = items_to_set[i:i+100]
        shopify_graphql_request("""
            mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
              inventorySetOnHandQuantities(input: $input) { userErrors { message } }
            }
        """, {"input": {"reason": "correction", "setQuantities": batch}})
    print("Shopify inventory sync complete.", flush=True)


def main():
    if not all([SRC_HOST, SRC_USER, SRC_PASS, DST_HOST, DST_USER, DST_PASS]):
        print("ERROR: Missing FTP credentials in .env")
        return

    print("=== US Auto Force FTP -> FTP + Shopify Sync ===", flush=True)
    buf = download_usaf_csv()
    agg = process(buf)

    if not agg:
        print("No data found. Aborting.")
        return

    write_csv(agg)
    upload_csv()
    sync_shopify(agg)
    print("All done.", flush=True)


if __name__ == "__main__":
    main()