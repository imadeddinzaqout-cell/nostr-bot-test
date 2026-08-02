import os
import re
import random
import asyncio
import requests
from datetime import timedelta
import sys
sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 5  
SLEEP_BETWEEN_CYCLES = 30  

CTA_VARIANTS = [
    "\n\n(If you'd like to support my family in Gaza, even a small zap means the world to us 🙏⚡)",
    "\n\n(Every small zap helps my family rebuild and stay safe ❤️⚡)",
    "\n\n(Feel free to send a small zap if you wish to support my family in Gaza 🙏⚡)",
    "\n\n(A small zap can make a huge difference for my family right now ⚡❤️)",
    "\n\n(If you feel moved to help, any small zap is deeply appreciated 🙏⚡)"
]

def is_valid_post(text):
    if not text or len(text.strip()) < 5:
        return False
    # استبعاد الفيديوهات المباشرة أو الروابط الكثيرة فقط
    text_lower = text.lower()
    if "http://kubus" in text_lower or "https://" in text_lower and len(text.split()) < 3:
        return False
    return True

def generate_ai_reply(prompt_text):
    if not prompt_text:
        return None

    if len(prompt_text) > 280:
        prompt_text = prompt_text[:280] + "..."

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    system_instruction = (
        "You are Imad, a genuine human user on Nostr. Read the post and write a short, friendly, empathetic, and natural 1-sentence reply in English.\n"
        "Never use robotic phrases like 'I completely agree' or 'Great perspective'.\n"
        "Do NOT mention Gaza or donations in the main reply text.\n"
        "If the post is totally unreadable or pure code/spam, reply ONLY with: SKIP"
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Post: '{prompt_text}'"}
        ],
        "temperature": 0.7
    }

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=20)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip()
            if "SKIP" in res_text or len(res_text) < 4:
                return None
            return res_text
    except Exception:
        pass
    return None

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
        return

    from nostr_sdk import Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind, RelayUrl, NostrConnect, NostrConnectUri
    
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
    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))

    print("Connecting to Relays...")
    try:
        await asyncio.wait_for(client.connect(), timeout=10)
    except Exception:
        pass

    print("Fetching timeline events...")
    f = Filter().kind(Kind(1)).limit(40)
    
    events_list = []
    try:
        events_obj = await asyncio.wait_for(client.fetch_events(f, timedelta(seconds=10)), timeout=12)
        events_list = events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj)
    except Exception:
        print("Fetch timeout or empty stream, continuing...")

    if not events_list:
        print("No events found in this fetch.")
        return

    print(f"Fetched {len(events_list)} events successfully. Processing...")

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
            author_pk = event.author() if callable(event.author) else event.author
            author_hex = author_pk.to_hex().lower()

            if bot_hex and author_hex == bot_hex: continue
            if author_hex in session_authors: continue

            content = event.content() if callable(event.content) else event.content
            clean_content = content.strip() if content else ""

            if not is_valid_post(clean_content): continue

            reply_text = await asyncio.to_thread(generate_ai_reply, clean_content)
            if reply_text:
                reply_text += random.choice(CTA_VARIANTS)

                tags = [Tag.event(event_id_obj), Tag.public_key(author_pk)]
                builder = EventBuilder(Kind(1), reply_text, tags)

                await client.send_event_builder(builder)
                replies_count += 1
                session_authors.add(author_hex)

                print(f"Successfully posted reply #{replies_count}: {reply_text[:60]}...")
                await asyncio.sleep(3)
        except Exception:
            continue

    print(f"Cycle finished. Posted {replies_count} replies.")

async def main():
    print("Starting streamlined Nostr bot...")
    max_cycles = 30
    for cycle in range(1, max_cycles + 1):
        print(f"\n--- Cycle {cycle}/{max_cycles} ---")
        try:
            await asyncio.wait_for(run_single_cycle(), timeout=50)
        except asyncio.TimeoutError:
            print("Cycle timeout, skipping to next...")
        
        if cycle < max_cycles:
            print(f"Sleeping {SLEEP_BETWEEN_CYCLES}s...")
            await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
