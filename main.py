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

MAX_REPLIES = 5  
SLEEP_BETWEEN_CYCLES = 60  

CTA_VARIANTS = [
    "\n\n(If you'd like to support my family in Gaza, even a small zap means the world to us 🙏⚡)",
    "\n\n(Every small zap helps my family rebuild and stay safe ❤️⚡)",
    "\n\n(Feel free to send a small zap if you wish to support my family in Gaza 🙏⚡)",
    "\n\n(A small zap can make a huge difference for my family right now ⚡❤️)",
    "\n\n(If you feel moved to help, any small zap is deeply appreciated 🙏⚡)"
]

def is_clean_text(text):
    if not text or len(text.strip()) < 6:
        return False, "Too short"
    latin_chars = len(re.findall(r'[a-zA-Z0-9\s.,!?\'"\-]', text))
    total_chars = len(text)
    if total_chars > 0 and (latin_chars / total_chars) < 0.40:
        return False, "Non-English/Complex symbols"
    return True, "OK"

def generate_ai_reply(prompt_text):
    if not prompt_text:
        return None

    if len(prompt_text) > 280:
        prompt_text = prompt_text[:280] + "..."

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    system_instruction = (
        "You are Imad, a human user on Nostr. Write a friendly, 1-sentence reply in natural English to this post.\n"
        "Do NOT mention Gaza, zaps, or donations in this main sentence.\n"
        "If the post is unreadable, total spam, non-English, or pure code, reply ONLY with: SKIP"
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
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip()
            if "SKIP" in res_text or len(res_text) < 4:
                return None
            return res_text
    except Exception as e:
        print(f"DeepSeek API Exception: {e}")
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
    # إضافة خوادم سريعة للتمرير المباشر
    relay_list = [
        "wss://relay.damus.io", 
        "wss://nos.lol", 
        "wss://relay.nostr.band",
        "wss://nostr.mom"
    ]
    for r in relay_list:
        try:
            await client.add_relay(RelayUrl.parse(r))
        except Exception:
            await client.add_relay(r)

    await client.connect()

    try:
        bot_pk = await signer.public_key()
        bot_hex = bot_pk.to_hex().lower() if bot_pk else ""
    except Exception:
        bot_hex = ""

    f = Filter().kind(Kind(1)).limit(50)
    try:
        events_obj = await client.fetch_events(f, timedelta(seconds=8))
        events_list = events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj)
    except Exception as e:
        print(f"Fetch error: {e}")
        return

    if not events_list:
        print("No events found.")
        return

    print(f"Fetched {len(events_list)} events. Processing...")

    replies_count = 0
    session_authors = set()

    for event in events_list:
        if replies_count >= MAX_REPLIES:
            break

        try:
            event_id_obj = event.id() if callable(event.id) else event.id
            author_pk = event.author() if callable(event.author) else event.author
            author_hex = author_pk.to_hex().lower()

            if bot_hex and author_hex == bot_hex:
                continue
            if author_hex in session_authors:
                continue

            content = event.content() if callable(event.content) else event.content
            clean_content = content.strip() if content else ""

            is_valid, reason = is_clean_text(clean_content)
            if not is_valid:
                continue

            reply_text = await asyncio.to_thread(generate_ai_reply, clean_content)
            if not reply_text:
                continue

            reply_text += random.choice(CTA_VARIANTS)

            tags = [Tag.event(event_id_obj), Tag.public_key(author_pk)]
            try:
                builder = EventBuilder.text_note(reply_text).tags(tags)
            except Exception:
                builder = EventBuilder(Kind(1), reply_text, tags)

            # النشر الفوري دون انتظار تعليق الـ Relay
            print(f"Publishing reply to post: '{clean_content[:25]}...'")
            
            # نرسل الحدث في الخفاء لنمنع الـ Timeout إطلاقاً
            asyncio.create_task(client.send_event_builder(builder))
            
            replies_count += 1
            session_authors.add(author_hex)
            print(f"-> SENT REPLY #{replies_count} SUCCESSFULLY!")
            await asyncio.sleep(2)

        except Exception as loop_err:
            continue

    print(f"Cycle finished. Sent {replies_count} replies.")

async def main():
    print("Starting optimized Nostr bot...")
    max_cycles = 20
    for cycle in range(1, max_cycles + 1):
        print(f"\n--- Cycle {cycle}/{max_cycles} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Cycle error: {e}")
        
        if cycle < max_cycles:
            print(f"Sleeping {SLEEP_BETWEEN_CYCLES}s...")
            await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
