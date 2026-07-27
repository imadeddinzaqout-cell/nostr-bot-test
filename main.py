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

# جلب الإعدادات من GitHub Secrets
NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 10  # الحد الأقصى: 10 ردود فقط لكل تشغيلة

# تنويع عبارات الدعوة (CTA) لضمان عدم التكرار وتبدو حركة بشرية
CTA_VARIANTS = [
    "\n\n(Btw, check my pinned post if you'd like to support my family in Gaza 🙏)",
    "\n\n(Feel free to take a look at my pinned post for our story in Gaza.)",
    "\n\n(Shared more about my family's situation in Gaza on my pinned post if you have a sec.)",
    "",  # في بعض الأحيان بدون أي دعوة لتبدو المشاركة طبيعية 100%
]

def is_spam(text):
    """فحص المنشورات لتجنب الرد على السبام وإعلانات الكريبتو البوتات"""
    text_lower = text.lower()
    
    url_count = len(re.findall(r'https?://\S+', text))
    if url_count >= 2:
        return True
        
    for kw in ["solana", "trycloudflare", "kill-fee", "moneymaker", "airdrop", "presale"]:
        if kw in text_lower:
            return True
            
    return False

def generate_ai_reply(prompt_text):
    if not prompt_text or len(prompt_text.strip()) < 5:
        return None

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "You are Imad, a genuine human user on Nostr reacting naturally to posts.\n\n"
        "RULES:\n"
        "1. Keep your reply to 1 concise, direct, and empathetic human sentence reacting to what the author wrote.\n"
        "2. NEVER use generic bot phrases like 'I completely agree', 'Great perspective', 'Thank you for sharing', or rigid corporate language.\n"
        "3. Do NOT mention Gaza or donations in your direct response text (a separate system handles that dynamically).\n"
        "4. If the post is empty, automated code, pure media links without text, or total nonsense, respond ONLY with the exact word: SKIP"
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
                print("DeepSeek suggested SKIPPING this post.")
                return None
            
            selected_cta = random.choice(CTA_VARIANTS)
            return f"{res_text}{selected_cta}"
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
    return None

async def main():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub (NOSTR_NSEC or DEEPSEEK_API_KEY).")
        return

    # المصادقة
    if NOSTR_SECRET.startswith("nsec1"):
        print("Authenticating using Direct Private Key (nsec)...")
        keys = Keys.parse(NOSTR_SECRET)
        signer = NostrSigner.keys(keys)
    elif NOSTR_SECRET.startswith("bunker://") or NOSTR_SECRET.startswith("nostrconnect://"):
        print("Authenticating using Nostr Connect (NIP-46 Remote Signer)...")
        app_keys = Keys.generate()
        
        try:
            uri = NostrConnectUri.parse(NOSTR_SECRET)
        except Exception:
            uri = NOSTR_SECRET

        opts = None
        try:
            from nostr_sdk import NostrConnectOptions
            opts = NostrConnectOptions()
        except Exception:
            try:
                from nostr_sdk import Options
                opts = Options()
            except Exception:
                opts = None

        timeout = timedelta(seconds=30)
        try:
            nc = NostrConnect(uri, app_keys, timeout, opts)
        except Exception:
            nc = NostrConnect(uri, app_keys, 30, opts)

        signer = NostrSigner.nostr_connect(nc)
    else:
        print("Error: Invalid NOSTR_NSEC format.")
        return

    client = Client(signer)
    relay_list = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net"]
    for r in relay_list:
        try:
            parsed_url = RelayUrl.parse(r)
            await client.add_relay(parsed_url)
        except Exception:
            try:
                parsed_url = RelayUrl(r)
                await client.add_relay(parsed_url)
            except Exception:
                await client.add_relay(r)

    await client.connect()
    print("Successfully connected to Nostr Relays!")

    # 1️⃣ جلب هوية البوت الخاصة لمنع الرد على نفسك
    try:
        bot_pk = await client.signer().public_key()
    except Exception:
        try:
            bot_pk = signer.public_key()
        except Exception:
            bot_pk = None

    bot_hex = bot_pk.to_hex() if bot_pk else ""

    # 2️⃣ جلب تاريخ آخر 100 رد قام بها الحساب لمنع التكرار عبر التشغيلات المتقاطعة
    already_replied_events = set()
    already_replied_authors = set()

    if bot_pk:
        print("Fetching recent reply history from Nostr relays to prevent duplicates...")
        history_filter = Filter().author(bot_pk).kind(Kind(1)).limit(100)
        try:
            history_obj = await client.fetch_events(history_filter, timedelta(seconds=10))
        except Exception:
            try:
                history_obj = await client.fetch_events(history_filter, 10)
            except Exception:
                history_obj = []

        if hasattr(history_obj, "to_vec"):
            history_list = history_obj.to_vec()
        elif hasattr(history_obj, "to_list"):
            history_list = history_obj.to_list()
        else:
            try:
                history_list = list(history_obj)
            except Exception:
                history_list = []

        for h_event in history_list:
            try:
                for t in h_event.tags():
                    vec = t.as_vec()
                    if len(vec) >= 2:
                        if vec[0] == 'e':
                            already_replied_events.add(vec[1])
                        elif vec[0] == 'p':
                            already_replied_authors.add(vec[1])
            except Exception:
                pass

        print(f"Loaded {len(already_replied_events)} previously replied posts and {len(already_replied_authors)} previous authors.")

    # 3️⃣ جلب أحدث المنشورات للرد عليها
    f = Filter().kind(Kind(1)).limit(50)
    
    try:
        events_obj = await client.fetch_events(f, timedelta(seconds=10))
    except Exception:
        try:
            events_obj = await client.fetch_events(f, 10)
        except Exception as e:
            print(f"Error fetching events: {e}")
            return

    if hasattr(events_obj, "to_vec"):
        events_list = events_obj.to_vec()
    elif hasattr(events_obj, "to_list"):
        events_list = events_obj.to_list()
    else:
        try:
            events_list = list(events_obj)
        except Exception:
            events_list = []

    if not events_list:
        print("No events found.")
        return

    replies_count = 0
    processed_authors = set()

    for event in events_list:
        if replies_count >= MAX_REPLIES:
            print(f"Reached limit of {MAX_REPLIES} replies. Stopping execution.")
            break

        try:
            event_id_obj = event.id()
        except TypeError:
            event_id_obj = event.id

        event_id_hex = event_id_obj.to_hex() if hasattr(event_id_obj, "to_hex") else str(event_id_obj)

        try:
            author_hex = event.author().to_hex()
            author_pk = event.author()
        except TypeError:
            author_hex = event.author.to_hex()
            author_pk = event.author

        # 🛑 فحص 1: منع الرد على حسابك أنت
        if bot_hex and author_hex == bot_hex:
            continue

        # 🛑 فحص 2: استبعاد المنشور إذا تم الرد عليه سابقاً في أي تشغيلة
        if event_id_hex in already_replied_events:
            print(f"Skipping post {event_id_hex[:8]} (Already replied to in a previous run).")
            continue

        # 🛑 فحص 3: استبعاد صاحب المنشور إذا تم الرد عليه حديثاً
        if author_hex in processed_authors or author_hex in already_replied_authors:
            print(f"Skipping author {author_hex[:8]} (Already interacted with recently).")
            continue

        try:
            content = event.content()
        except TypeError:
            content = event.content

        clean_content = content.strip() if content else ""
        
        if not clean_content or len(clean_content) < 6:
            continue

        if re.match(r'^https?://\S+\.(jpg|jpeg|png|gif|mp4|webm)$', clean_content, re.IGNORECASE):
            continue

        if is_spam(clean_content):
            continue

        print(f"[{replies_count + 1}/{MAX_REPLIES}] Processing post from {author_hex[:8]}...")
        
        reply_text = generate_ai_reply(clean_content)
        if reply_text:
            tags = [Tag.event(event_id_obj), Tag.public_key(author_pk)]
            
            try:
                builder = EventBuilder.text_note(reply_text).tags(tags)
            except Exception:
                try:
                    builder = EventBuilder.text_note(reply_text)
                except Exception:
                    builder = EventBuilder(Kind(1), reply_text, tags)

            await client.send_event_builder(builder)
            replies_count += 1
            processed_authors.add(author_hex)
            already_replied_events.add(event_id_hex)
            print(f"Successfully posted reply #{replies_count}: {reply_text}")

            if replies_count < MAX_REPLIES:
                sleep_time = random.randint(5, 15)
                print(f"Waiting {sleep_time} seconds before next reply...")
                await asyncio.sleep(sleep_time)

    print(f"Finished! Posted {replies_count} new unique replies in this run.")

if __name__ == "__main__":
    asyncio.run(main())
