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

    # اختصار النص تلقائياً إذا كان طويلاً جداً لضمان سرعة الاستجابة وعدم حدوث Timeout
    if len(prompt_text) > 280:
        prompt_text = prompt_text[:280] + "..."

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
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=25)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip()
            if "SKIP" in res_text or "can't react" in res_text.lower() or len(res_text) < 5:
                return None
            if not is_clean_english(res_text):
                return None
            return res_text
    except Exception as e:
        print(f"DeepSeek API notice: {e}")
    return None

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
        return

    if NOSTR_SECRET.startswith("nsec1"):
        keys = Keys.parse(NOSTR_SECRET)
        signer = NostrSigner.keys(keys)
    elif NOSTR_SECRET.startswith("bunker://") or NOSTR_SECRET.startswith("nostrconnect://"):
        app_keys = Keys.generate()
        try:
            uri = NostrConnectUri.parse(NOSTR_SECRET)
        except Exception:
            uri = NOSTR_SECRET
        try:
            from nostr_sdk import NostrConnectOptions
            opts = NostrConnectOptions()
        except Exception:
            opts = None
        nc = NostrConnect(uri, app_keys, timedelta(seconds=30), opts)
        signer = NostrSigner.nostr_connect(nc)
    else:
        print("Error: Invalid NOSTR_NSEC format.")
        return

    client = Client(signer)
    
    relay_list = ["wss://relay.damus.io", "wss://nos.lol"]
    for r_url in relay_list:
        try:
            await client.add_relay(RelayUrl.parse(r_url))
        except Exception:
            await client.add_relay(r_url)

    print("Connecting to Nostr Relays...")
    try:
        await asyncio.wait_for(client.connect(), timeout=12)
        print("Connected to Nostr Relays successfully!")
    except Exception as e:
        print(f"Connection notice: {e}")

    print("Fetching latest global timeline...")
    f = Filter().kind(Kind(1)).limit(60)
    
    events_list = []
    try:
        events_obj = await asyncio.wait_for(client.fetch_events(f, timedelta(seconds=15)), timeout=20)
        events_list = events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj)
    except Exception as e:
        print(f"Fetch notice: {e}")

    if not events_list:
        print("No events fetched in this cycle.")
        return

    def get_event_time(ev):
        try:
            return ev.created_at().as_secs() if callable(ev.created_at) else getattr(ev, 'created_at', 0)
        except Exception:
            return 0

    events_list.sort(key=get_event_time, reverse=True)

    try:
        bot_pk = await signer.public_key()
        bot_hex = bot_pk.to_hex().lower() if bot_pk else ""
    except Exception:
        bot_hex = ""

    replies_count = 0
    session_authors = set()

    for event in events_list:
        if replies_count >= MAX_REPLIES:
            break

        try:
            event_id_obj = event.id() if callable(event.id) else event.id
            event_id_hex = (event_id_obj.to_hex() if hasattr(event_id_obj, "to_hex") else str(event_id_obj)).lower()

            author_pk = event.author() if callable(event.author) else event.author
            author_hex = author_pk.to_hex().lower()

            if bot_hex and author_hex == bot_hex: continue
            if author_hex in session_authors: continue

            content = event.content() if callable(event.content) else event.content
            clean_content = content.strip() if content else ""

            if not clean_content or len(clean_content) < 8: continue
            if not is_clean_english(clean_content): continue
            if contains_video(clean_content) or is_spam(clean_content): continue

            reply_text = await asyncio.to_thread(generate_ai_reply, clean_content)
            if reply_text:
                reply_text += random.choice(CTA_VARIANTS)

                tags = [Tag.event(event_id_obj), Tag.public_key(author_pk)]
                try:
                    builder = EventBuilder.text_note(reply_text).tags(tags)
                except Exception:
                    builder = EventBuilder(Kind(1), reply_text, tags)

                await client.send_event_builder(builder)
                replies_count += 1
                session_authors.add(author_hex)

                print(f"Posted FAST reply #{replies_count}: {reply_text}")

                if replies_count < MAX_REPLIES:
                    fast_sleep = random.randint(5, 10)
                    print(f"Waiting {fast_sleep}s for next reply...")
                    await asyncio.sleep(fast_sleep)
        except Exception as loop_err:
            print(f"Skipping event due to processing error: {loop_err}")
            continue

    print(f"Completed fast cycle! Posted {replies_count} replies.")

async def main():
    print("Starting fast Nostr bot loop...")
    max_cycles = 30
    current_cycle = 0

    while current_cycle < max_cycles:
        current_cycle += 1
        print(f"\n--- Starting Cycle {current_cycle}/{max_cycles} ---")
        try:
            await asyncio.wait_for(run_single_cycle(), timeout=120)
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
