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
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # توجيهات الذكاء الاصطناعي للتفاعل الطبيعي فقط دون تكرار نفس الرسالة الترويجية
    system_instruction = (
        "You are Imad, a genuine human user on Nostr reacting naturally to posts.\n\n"
        "RULES:\n"
        "1. Keep your reply to 1 concise, direct, and empathetic human sentence reacting to what the author wrote.\n"
        "2. NEVER use generic bot phrases like 'I completely agree', 'Great perspective', 'Thank you for sharing', or rigid corporate language.\n"
        "3. Do NOT mention Gaza or donations in your direct response text (a separate system handles that dynamically).\n"
        "4. If the post is pure spam, automated code, or total nonsense, respond ONLY with the exact word: SKIP"
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
            if "SKIP" in res_text or len(res_text) < 5:
                return None
            
            # دمج الرد مع صيغة CTA عشوائية
            selected_cta = random.choice(CTA_VARIANTS)
            return f"{res_text}{selected_cta}"
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
    return None

async def main():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub (NOSTR_NSEC or DEEPSEEK_API_KEY).")
        return

    # المصادقة عبر NIP-46 أو nsec
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

    # جلب أحدث 50 منشوراً لاختيار أفضل 10 منها
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
            content = event.content()
        except TypeError:
            content = event.content

        if is_spam(content):
            continue

        try:
            author_hex = event.author().to_hex()
            author_pk = event.author()
        except TypeError:
            author_hex = event.author.to_hex()
            author_pk = event.author

        # تجنب الرد على نفس الشخص مرتين في نفس التشغيلة
        if author_hex in processed_authors:
            continue

        try:
            event_id = event.id()
        except TypeError:
            event_id = event.id

        print(f"[{replies_count + 1}/{MAX_REPLIES}] Processing post from {author_hex[:8]}...")
        
        reply_text = generate_ai_reply(content)
        if reply_text:
            tags = [Tag.event(event_id), Tag.public_key(author_pk)]
            
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
            print(f"Successfully posted reply #{replies_count}: {reply_text}")

            # انتظار عشوائي بين 5 و 15 ثانية لتبدو الحركة بشرية
            if replies_count < MAX_REPLIES:
                sleep_time = random.randint(5, 15)
                print(f"Waiting {sleep_time} seconds before next reply...")
                await asyncio.sleep(sleep_time)

    print(f"Finished! Posted {replies_count} replies in this run.")

if __name__ == "__main__":
    asyncio.run(main())
