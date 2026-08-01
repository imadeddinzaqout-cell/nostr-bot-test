import os
import re
import random
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind,
    NostrConnect, NostrConnectUri, RelayUrl
)
import sys
sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 10  
SLEEP_BETWEEN_CYCLES = 300  

CTA_VARIANTS = [
    "\n\n(If you'd like to support my family in Gaza, even a small zap means the world to us 🙏⚡)",
    "\n\n(Every small zap helps my family rebuild and stay safe ❤️⚡)",
    "\n\n(Feel free to send a small zap if you wish to support my family in Gaza 🙏⚡)",
    "\n\n(A small zap can make a huge difference for my family right now ⚡❤️)",
    "\n\n(If you feel moved to help, any small zap is deeply appreciated 🙏⚡)"
]

def is_clean_english(text):
    if not text:
        return False
    cjk_pattern = re.compile(r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf\uac00-\ud7af]')
    if cjk_pattern.search(text):
        return False
    latin_chars = len(re.findall(r'[a-zA-Z0-9\s.,!?\'"\-]', text))
    total_chars = len(text)
    return total_chars > 0 and (latin_chars / total_chars) >= 0.65

def contains_video(text):
    text_lower = text.lower()
    video_extensions = r'\.(mp4|m3u8|mov|webm|avi|mkv|flv|wmv)(\?|\s|$)'
    if re.search(video_extensions, text_lower):
        return True
    video_domains = ["youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "rumble.com", "bitchute.com", "nostr.build/av/", "video/"]
    return any(domain in text_lower for domain in video_domains)

def is_spam(text):
    text_lower = text.lower()
    if len(re.findall(r'https?://\S+', text)) >= 2:
        return True
    return any(kw in text_lower for kw in ["solana", "trycloudflare", "kill-fee", "moneymaker", "airdrop", "presale", "telegram"])

def generate_ai_reply(prompt_text):
    if not prompt_text or len(prompt_text.strip()) < 5:
        return None

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    system_instruction = (
        "You are Imad, a genuine human user on Nostr reacting naturally to posts.\n\n"
        "RULES:\n"
        "1. MUST respond ONLY in natural English. NEVER use Japanese, Chinese, or non-English scripts.\n"
        "2. Keep your reply to 1 concise, direct, and empathetic human sentence reacting strictly to what the author wrote.\n"
        "3. NEVER use generic bot phrases like 'I completely agree', 'Great perspective', 'Thank you for sharing'.\n"
        "4. Do NOT mention Gaza, donations, or zaps in your main response text.\n"
        "5. If the post is non-English, empty, automated code, pure media links without text, or total nonsense, respond ONLY with: SKIP"
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Post to reply to: '{prompt_text}'"}
        ],
        "temperature": 0.7
    }
    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip()
            if "SKIP" in res_text or "can't react" in res_text.lower() or len(res_text) < 5:
                return None
            if not is_clean_english(res_text):
                return None
            return res_text
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
    return None

def fetch_events_via_http():
    """جلب أحدث المنشورات باستخدام HTTP REST API لتجنب مشاكل WebSocket Timeout في السيرفرات"""
    url = "https://relay.dam.io" # بديل سريع أو استخدام nostr.band
    # استخدام nostr.band للبحث السريع عبر الـ API
    api_url = "https://api.nostr.band/v0/posts/trending" # أو استعلام مباشر
    # سنستخدم استعلام مباشر لـ nostr.band أو ريلاي يدعم ن一般的 القراءة
    try:
        # محاولة جلب أحدث المنشورات عبر Nostr.band API المخصص للسرعة
        resp = requests.get("https://api.nostr.band/v0/stats/profiles", timeout=10)
        # الطريقة الأضمن والأسرع: استخدام Nostr.band search API للمنشورات النصية الطازجة
        res = requests.get("https://nostr.band/api/v0/top/contacts", timeout=10)
    except Exception:
        pass

    # بديل مباشر وبسيط عبر نداء HTTP لـ nostr.band queries
    events = []
    try:
        headers = {'Accept': 'application/json'}
        # جلب أحدث الـ notes عبر Nostr.band endpoint المخصص
        r = requests.get("https://api.nostr.band/v0/posts/new", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "posts" in data:
                for p in data["posts"]:
                    events.append({
                        "id": p.get("id"),
                        "pubkey": p.get("pubkey"),
                        "content": p.get("content"),
                        "created_at": p.get("created_at")
                    })
    except Exception as e:
        print(f"HTTP fetch error: {e}")
    return events

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
        return

    print("Fetching latest global timeline via HTTP API...")
    raw_posts = fetch_events_via_http()
    
    if not raw_posts:
        print("No events found via HTTP, falling back to direct relay connection...")
        # الطريقة التقليدية السريعة في حال لم يعمل الـ API
        if NOSTR_SECRET.startswith("nsec1"):
            keys = Keys.parse(NOSTR_SECRET)
            signer = NostrSigner.keys(keys)
        else:
            return
        client = Client(signer)
        await client.add_relay(RelayUrl.parse("wss://nos.lol"))
        await client.connect()
        f = Filter().kind(Kind(1)).limit(30)
        events_obj = await client.fetch_events(f, timedelta(seconds=5))
        # تحويل سريع
        raw_posts = []
        for ev in events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj):
            try:
                raw_posts.append({
                    "id": ev.id().to_hex() if callable(ev.id) else str(ev.id),
                    "pubkey": ev.author().to_hex() if callable(ev.author) else str(ev.author),
                    "content": ev.content() if callable(ev.content) else ev.content,
                    "created_at": ev.created_at().as_secs() if callable(ev.created_at) else 0
                })
            except Exception:
                continue

    if not raw_posts:
        print("No events found at all.")
        return

    # تهيئة العميل للإرسال فقط (بدون انتظار جلب معقد)
    if NOSTR_SECRET.startswith("nsec1"):
        keys = Keys.parse(NOSTR_SECRET)
        signer = NostrSigner.keys(keys)
    else:
        return

    client = Client(signer)
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.connect()

    try:
        bot_pk = await signer.public_key()
        bot_hex = bot_pk.to_hex().lower() if bot_pk else ""
    except Exception:
        bot_hex = ""

    replies_count = 0
    session_authors = set()

    for p in raw_posts:
        if replies_count >= MAX_REPLIES:
            break

        event_id_hex = str(p.get("id", "")).lower()
        author_hex = str(p.get("pubkey", "")).lower()
        clean_content = str(p.get("content", "")).strip()

        if not event_id_hex or not author_hex: continue
        if bot_hex and author_hex == bot_hex: continue
        if author_hex in session_authors: continue

        if not clean_content or len(clean_content) < 8: continue
        if not is_clean_english(clean_content): continue
        if contains_video(clean_content) or is_spam(clean_content): continue

        reply_text = generate_ai_reply(clean_content)
        if reply_text:
            reply_text += random.choice(CTA_VARIANTS)

            try:
                from nostr_sdk import EventId, PublicKey
                event_id_obj = EventId.parse(event_id_hex)
                author_pk_obj = PublicKey.parse(author_hex)
                tags = [Tag.event(event_id_obj), Tag.public_key(author_pk_obj)]
                builder = EventBuilder.text_note(reply_text).tags(tags)
            except Exception:
                builder = EventBuilder(Kind(1), reply_text, [])

            await client.send_event_builder(builder)
            replies_count += 1
            session_authors.add(author_hex)

            print(f"Posted FAST reply #{replies_count}: {reply_text}")

            if replies_count < MAX_REPLIES:
                fast_sleep = random.randint(5, 10)
                print(f"Waiting {fast_sleep}s for next reply...")
                await asyncio.sleep(fast_sleep)

    print(f"Completed fast cycle! Posted {replies_count} replies.")

async def main():
    print("Starting fast Nostr bot loop...")
    max_cycles = 30
    current_cycle = 0

    while current_cycle < max_cycles:
        current_cycle += 1
        print(f"\n--- Starting Cycle {current_cycle}/{max_cycles} ---")
        try:
            await asyncio.wait_for(run_single_cycle(), timeout=90)
        except asyncio.TimeoutError:
            print("Cycle timed out! Skipping to next wait period...")
        except Exception as e:
            print(f"Error in cycle execution: {e}")
        
        if current_cycle < max_cycles:
            print(f"Waiting 5 minutes ({SLEEP_BETWEEN_CYCLES}s) before next batch of latest posts...")
            await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

    print("Completed all cycles successfully.")

if __name__ == "__main__":
    asyncio.run(main())
