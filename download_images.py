import os
import sys
import json
import re
import time
import urllib.parse
import urllib.request

# ── Playwright のインポート ───────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Error: playwright is not installed.")
    print("Run: uv pip install playwright && playwright install chromium")
    sys.exit(1)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── 1. URL取得 ────────────────────────────────────────────────
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

if not url.startswith(("http://", "https://")):
    url = "https://" + url

print(f"\nTarget: {url}")

# ── 2. ヘルパー関数 ───────────────────────────────────────────
def clean_folder_name(name):
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return cleaned if cleaned else "downloaded_images"

def extract_pins_from_state(redux_state: dict) -> list:
    """redux_state から Pin オブジェクトを全て抽出する"""
    seen = set()
    pins = []

    # BoardFeedResource.data
    for _, val in redux_state.get("resources", {}).get("BoardFeedResource", {}).items():
        if isinstance(val, dict):
            for pin in val.get("data", []):
                pid = pin.get("id")
                if pid and pid not in seen and isinstance(pin.get("images"), dict):
                    seen.add(pid)
                    pins.append(pin)

    # redux_state.pins（正規化ストア）
    for pid, pin in redux_state.get("pins", {}).items():
        if pid not in seen and isinstance(pin, dict) and isinstance(pin.get("images"), dict):
            seen.add(pid)
            pins.append(pin)

    return pins

def get_best_image_url(images: dict) -> str | None:
    for quality in ["orig", "736x", "474x", "236x"]:
        img = images.get(quality)
        if isinstance(img, dict) and img.get("url"):
            return img["url"]
    return None

# ── 3. Playwright でボードを開いてスクロール ──────────────────
print("Launching headless browser...")

all_pins = []
seen_ids = set()
board_name = "downloaded_images"
total_pins = None

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="ja-JP",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()

    # ページ読み込み中のネットワークレスポンスを傍受して Pin データを収集
    def on_response(response):
        global all_pins, seen_ids
        if "BoardFeedResource" in response.url and response.status == 200:
            try:
                body = response.json()
                new_pins = body.get("resource_response", {}).get("data", [])
                added = 0
                for pin in new_pins:
                    pid = pin.get("id")
                    if pid and pid not in seen_ids and isinstance(pin.get("images"), dict):
                        seen_ids.add(pid)
                        all_pins.append(pin)
                        added += 5
                if added:
                    print(f"  [API intercept] +{added} pins (total: {len(all_pins)})")
            except Exception:
                pass

    page.on("response", on_response)

    # ボードページを開く
    print("Opening board page...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PWTimeout:
        print("Warning: page load timed out, continuing anyway...")

    # 少し待ってから PWS データを取得
    time.sleep(2.0)

    # __PWS_INITIAL_PROPS__ からも Pin を収集
    try:
        pws_text = page.evaluate(
            "() => document.getElementById('__PWS_INITIAL_PROPS__')?.textContent || ''"
        )
        if pws_text:
            pws_data = json.loads(pws_text)
            redux_state = pws_data.get("initialReduxState", {})

            # ボード名・総 Pin 数
            for bid, binfo in redux_state.get("boards", {}).items():
                if isinstance(binfo, dict):
                    n = binfo.get("name") or binfo.get("slug")
                    if n:
                        board_name = clean_folder_name(n)
                    total_pins = binfo.get("pin_count")
                    break

            # 初期 Pin を収集
            for pin in extract_pins_from_state(redux_state):
                pid = pin.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_pins.append(pin)
            print(f"Initial page: {len(all_pins)} pins  (board total: {total_pins})")
    except Exception as e:
        print(f"Warning: could not parse PWS data: {e}")

    # ── スクロールして残り全件を読み込む ──
    if total_pins is None or len(all_pins) < (total_pins or 9999):
        print("Scrolling to load all pins...")
        scroll_attempts = 0
        max_scrolls = 50  # 最大50回スクロール（大きなボード対応）
        prev_count = 0
        stall_count = 0

        while scroll_attempts < max_scrolls:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(5.0)  # ロード待機
            scroll_attempts += 1

            cur_count = len(all_pins)
            if cur_count == prev_count:
                stall_count += 1
                if stall_count >= 4:
                    print(f"  No new pins after 4 scrolls. Stopping.")
                    break
            else:
                stall_count = 0
                print(f"  Scroll {scroll_attempts}: {cur_count} pins total")

            prev_count = cur_count

            # 全件取得済みかチェック
            if total_pins and cur_count >= total_pins:
                print(f"  All {total_pins} pins loaded!")
                break

    browser.close()

# ── 4. 出力ディレクトリ ──────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))

# URL パスからフォールバック
if board_name == "downloaded_images":
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if parts:
        board_name = clean_folder_name(urllib.parse.unquote(parts[-1]))

output_dir = os.path.join(script_dir, "out", board_name)
os.makedirs(output_dir, exist_ok=True)

total_str = str(total_pins) if total_pins else "?"
print(f"\nBoard: {board_name}  ({len(all_pins)}/{total_str} pins collected)")
print(f"Output: {output_dir}")

if not all_pins:
    print("No pins found. If board is secret, make it public first.")
    sys.exit(0)

# ── 5. 画像ダウンロード ──────────────────────────────────────
downloaded = 0
skipped = 0

for idx, pin in enumerate(all_pins, 1):
    pin_id = pin.get("id", f"pin_{idx}")
    images = pin.get("images", {})

    if not isinstance(images, dict):
        skipped += 1
        continue

    img_url = get_best_image_url(images)
    if not img_url:
        skipped += 1
        continue

    ext = os.path.splitext(img_url)[1].split("?")[0] or ".jpg"
    filename = f"{idx:03d}_{pin_id}{ext}"
    filepath = os.path.join(output_dir, filename)

    print(f"[{idx:03d}/{len(all_pins)}] {pin_id} -> {filename}")
    req = urllib.request.Request(img_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp, \
             open(filepath, "wb") as out_f:
            out_f.write(resp.read())
        downloaded += 1
    except Exception as e:
        print(f"   Failed: {e}")
        skipped += 1

# ── 6. 結果レポート ──────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Done: {downloaded} downloaded, {skipped} skipped")
print(f"Saved to: '{output_dir}'")
if total_pins and downloaded < total_pins:
    print(f"Note: {total_pins - downloaded} pin(s) could not be retrieved.")
print(f"{'='*50}")
