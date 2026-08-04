import os
import sys
import json
import re
import urllib.parse
import urllib.request
import subprocess

# 1. Get board URL and output folder from user input or arguments
if len(sys.argv) > 1:
    url = sys.argv[1].strip()
else:
    try:
        url = input("Enter Pinterest board URL: ").strip()
    except EOFError:
        url = ""

if not url:
    print("No URL provided. Aborting.")
    sys.exit(1)

if len(sys.argv) > 2:
    base_dir = sys.argv[2].strip()
else:
    try:
        base_dir = input("Enter output folder (e.g. download): ").strip()
    except EOFError:
        base_dir = ""

if not base_dir:
    base_dir = "."


# 2. Fetch index.html using wget
script_dir = os.path.dirname(os.path.abspath(__file__))
html_path = os.path.join(script_dir, 'index.html')

user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
print(f"\nFetching index.html from [{url}] via wget...")

wget_cmd = ["wget", "-O", html_path, f"--user-agent={user_agent}", url]
res = subprocess.run(wget_cmd)

if res.returncode != 0 or not os.path.exists(html_path):
    print(f"Error: Failed to fetch HTML via wget (exit code: {res.returncode})")
    sys.exit(1)

# 3. Load HTML
with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
    html = f.read()

# 4. Extract board name and create output directory
def clean_folder_name(name):
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', name).strip()
    return cleaned if cleaned else 'downloaded_images'

def get_board_name(html_content, target_url):
    # Try to get board name from JSON data
    pws_match = re.search(r'<script id="__PWS_INITIAL_PROPS__"[^>]*>(.*?)</script>', html_content, re.DOTALL)
    if pws_match:
        try:
            data = json.loads(pws_match.group(1))
            redux_state = data.get('initialReduxState', {})
            boards = redux_state.get('boards', {})
            for b_id, b_info in boards.items():
                if isinstance(b_info, dict):
                    b_name = b_info.get('name') or b_info.get('slug')
                    if b_name:
                        return clean_folder_name(b_name)
        except Exception:
            pass

    # Extract from URL path (fallback)
    parsed = urllib.parse.urlparse(target_url)
    path_parts = [p for p in parsed.path.split('/') if p]
    if path_parts:
        board_part = urllib.parse.unquote(path_parts[-1])
        return clean_folder_name(board_part)

    return 'downloaded_images'

board_folder_name = get_board_name(html, url)
output_dir = os.path.join(base_dir, board_folder_name)
os.makedirs(output_dir, exist_ok=True)

print(f"Output directory: {output_dir}")

# 5. Extract pin data
pws_initial_props_match = re.search(r'<script id="__PWS_INITIAL_PROPS__"[^>]*>(.*?)</script>', html, re.DOTALL)

if not pws_initial_props_match:
    print("Error: __PWS_INITIAL_PROPS__ script tag not found.")
    sys.exit(1)

data = json.loads(pws_initial_props_match.group(1))
redux_state = data.get('initialReduxState', {})
board_feed = redux_state.get('resources', {}).get('BoardFeedResource', {})

pins_list = []
for subk, subval in board_feed.items():
    if isinstance(subval, dict) and 'data' in subval and isinstance(subval['data'], list):
        pins_list = subval['data']
        break

if not pins_list and 'pins' in redux_state and isinstance(redux_state['pins'], dict):
    pins_list = list(redux_state['pins'].values())

if not pins_list:
    print("No pin image data found. If this is a secret board, please change its visibility to public.")
    sys.exit(0)

# 6. Download images
downloaded_count = 0

for idx, pin in enumerate(pins_list, 1):
    pin_id = pin.get('id', f'pin_{idx}')
    images = pin.get('images', {})
    
    if not isinstance(images, dict):
        continue
        
    best_url = None
    for res_key in ['orig', '736x', '474x', '236x']:
        if res_key in images and isinstance(images[res_key], dict) and images[res_key].get('url'):
            best_url = images[res_key]['url']
            break
            
    if best_url:
        ext = os.path.splitext(best_url)[1] or '.jpg'
        filename = f"{idx:02d}_{pin_id}{ext}"
        filepath = os.path.join(output_dir, filename)
        
        print(f"[{idx:02d}/{len(pins_list)}] Downloading {pin_id} -> {filename}...")
        req = urllib.request.Request(best_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as resp, open(filepath, 'wb') as out_f:
                out_f.write(resp.read())
            downloaded_count += 1
        except Exception as e:
            print(f"   Failed to download {best_url}: {e}")

print(f"\nDone: Saved {downloaded_count} images to '{output_dir}'.")


