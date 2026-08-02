import os
import re
import random
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind,
    NostrConnect, NostrConnectUri, RelayUrl, Event
)
import sys
sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 10
SLEEP_BETWEEN_CYCLES = 300

# عنوان محفظتك المباشر لاستقبال Zaps
LIGHTNING_ADDR = "solemngreece21@walletofsatoshi.com"

# عبارات الـ CTA المحدثة والمحتوية على عنوان المحفظة
CTA_VARIANTS = [
    f"\n\n(Even a small zap of 1,000 sats helps ease my family's hardship here in Gaza 🙏⚡ LN: {LIGHTNING_ADDR})",
    f"\n\n(Every small zap makes a real difference and lifts some of our heavy burden ❤️⚡ LN: {LIGHTNING_ADDR})",
    f"\n\n(If you can send even 1,000 zaps, it truly helps my family survive and stay safe 🙏⚡ Lightning Address: {LIGHTNING_ADDR})",
    f"\n\n(A small zap of 1,000 sats goes a long way in easing my family's daily struggle ⚡❤️ LN: {LIGHTNING_ADDR})",
    f"\n\n(If you feel moved to support, any small zap brings warmth and relief to my family 🙏⚡ {LIGHTNING_ADDR})"
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

def is_reply_or_quote(event):
    try:
        tags_iter = event.tags() if callable(event.tags) else event.tags
        for t in tags_iter:
            vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
            if len(vec) >= 1 and str(vec[0]).lower() == 'e':
                return True
    except Exception:
        pass
    return False

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
        "4. Do NOT mention Gaza, donations, or zaps in your main response text (this will be attached automatically).\n"
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
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=15)
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
    relay_list = [
        "wss://relay.damus.io",
        "wss://nos.lol",
        "wss://relay.primal.net",
        "wss://relay.nostr.band"
    ]
    for r in relay_list:
        try:
            await client.add_relay(RelayUrl.parse(r))
        except Exception:
            await client.add_relay(r)

    await client.connect()
    print("Connected to Nostr Relays!")

    try:
        bot_pk = await signer.public_key()
    except Exception:
        bot_pk = None

    bot_hex = bot_pk.to_hex().lower() if bot_pk else ""
    already_replied_events = set()
    already_replied_authors = set()

    if bot_pk:
        history_filter = Filter().author(bot_pk).kind(Kind(1)).limit(500)
        try:
            history_obj = await client.fetch_events(history_filter, timedelta(seconds=12))
            history_list = history_obj.to_vec() if hasattr(history_obj, "to_vec") else list(history_obj)
        except Exception:
            history_list = []

        for h_event in history_list:
            try:
                tags_iter = h_event.tags() if callable(h_event.tags) else h_event.tags
                for t in tags_iter:
                    vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
                    if len(vec) >= 2:
                        tag_type, tag_val = str(vec[0]).lower(), str(vec[1]).lower()
                        if tag_type == 'e': already_replied_events.add(tag_val)
                        elif tag_type == 'p': already_replied_authors.add(tag_val)
            except Exception:
                pass

    f = Filter().kind(Kind(1)).limit(300)
    try:
        events_obj = await client.fetch_events(f, timedelta(seconds=10))
        events_list = events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj)
    except Exception as e:
        print(f"Error fetching events: {e}")
        return

    if not events_list:
        print("No events found.")
        return

    def get_event_time(ev):
        try:
            return ev.created_at().as_secs() if callable(ev.created_at) else getattr(ev, 'created_at', 0)
        except Exception:
            return 0

    events_list.sort(key=get_event_time, reverse=True)

    replies_count = 0
    session_authors = set()

    for event in events_list:
        if replies_count >= MAX_REPLIES:
            break

        event_id_obj = event.id() if callable(event.id) else event.id
        event_id_hex = (event_id_obj.to_hex() if hasattr(event_id_obj, "to_hex") else str(event_id_obj)).lower()

        author_pk = event.author() if callable(event.author) else event.author
        author_hex = author_pk.to_hex().lower()

        if bot_hex and author_hex == bot_hex: continue
        if event_id_hex in already_replied_events: continue
        if author_hex in session_authors or author_hex in already_replied_authors: continue
        if is_reply_or_quote(event): continue

        content = event.content() if callable(event.content) else event.content
        clean_content = content.strip() if content else ""

        if not clean_content or len(clean_content) < 8: continue
        if not is_clean_english(clean_content): continue
        if contains_video(clean_content) or is_spam(clean_content): continue

        reply_text = await asyncio.to_thread(generate_ai_reply, clean_content)
        if reply_text:
            reply_text += random.choice(CTA_VARIANTS)

            # --- التعديل الجوهري: ربط الرد بالمنشور كـ Reply ليتصل بالـ Thread بشكل صحيح ---
            try:
                # محاولة استخدام الدالة المباشرة للرد من مكتبة nostr-sdk
                builder = EventBuilder.reply(event, reply_text)
            except Exception:
                # كود احتياطي متوافق مع كافة الإصدارات لبناء الـ NIP-10 Reply tags
                t_event = Tag.custom(["e", event_id_hex, "", "reply"])
                t_pubkey = Tag.custom(["p", author_hex])
                builder = EventBuilder(Kind(1), reply_text, [t_event, t_pubkey])

            try:
                print(f"Publishing reply #{replies_count + 1} to Nostr network...")
                output = await asyncio.wait_for(client.send_event_builder(builder), timeout=15)
                
                replies_count += 1
                session_authors.add(author_hex)
                already_replied_authors.add(author_hex)
                already_replied_events.add(event_id_hex)

                print(f"-> CONFIRMED & PUBLISHED reply #{replies_count}: {reply_text[:60]}...")
            except asyncio.TimeoutError:
                print("Relay publish timeout. Skipping to maintain speed...")
            except Exception as pub_err:
                print(f"Publish error: {pub_err}")

            if replies_count < MAX_REPLIES:
                fast_sleep = random.randint(5, 10)
                print(f"Waiting {fast_sleep}s for next reply...")
                await asyncio.sleep(fast_sleep)

    print(f"Completed cycle! Successfully published {replies_count} replies.")

async def main():
    print("Starting Nostr bot loop...")
    max_cycles = 30
    current_cycle = 0

    while current_cycle < max_cycles:
        current_cycle += 1
        print(f"\n--- Starting Cycle {current_cycle}/{max_cycles} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Error in cycle execution: {e}")
        
        if current_cycle < max_cycles:
            print(f"Waiting 5 minutes ({SLEEP_BETWEEN_CYCLES}s) before next batch of latest posts...")
            await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

    print("Completed all cycles successfully.")

if __name__ == "__main__":
    asyncio.run(main())
