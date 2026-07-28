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

# تقليل عدد الردود إلى 3 فقط لكل تشغيلة لضمان تصرف إنساني طبيعي
MAX_REPLIES = 3

def contains_video(text):
    """فحص ما إذا كان المنشور يحتوي على فيديو بأي شكل من الأشكال"""
    text_lower = text.lower()
    
    video_extensions = r'\.(mp4|m3u8|mov|webm|avi|mkv|flv|wmv)(\?|\s|$)'
    if re.search(video_extensions, text_lower):
        return True
        
    video_domains = [
        "youtube.com", "youtu.be", "vimeo.com", "tiktok.com",
        "rumble.com", "bitchute.com", "nostr.build/av/", "video/", "video"
    ]
    for domain in video_domains:
        if domain in text_lower:
            return True
            
    return False

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
        "1. Keep your reply to 1 concise, direct, and empathetic human sentence reacting strictly to what the author wrote.\n"
        "2. NEVER use generic bot phrases like 'I completely agree', 'Great perspective', 'Thank you for sharing', or rigid corporate language.\n"
        "3. NEVER mention Gaza, donations, or pinned posts in your reply text. Just be a normal, friendly community member.\n"
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
            
            # إرجاع الرد البشري الخالص دون إضافة أي رابط أو دعوة مكررة
            return res_text
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

    # 1️⃣ جلب هوية البوت لمنع الرد على نفسك
    try:
        bot_pk = await client.signer().public_key()
    except Exception:
        try:
            bot_pk = signer.public_key()
        except Exception:
            bot_pk = None

    bot_hex = bot_pk.to_hex().lower() if bot_pk else ""

    # 2️⃣ جلب تاريخ آخر 300 رد لمنع التكرار تماماً
    already_replied_events = set()
    already_replied_authors = set()

    if bot_pk:
        print("Fetching recent reply history from Nostr relays...")
        history_filter = Filter().author(bot_pk).kind(Kind(1)).limit(300)
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
                tags_iter = h_event.tags() if callable(h_event.tags) else h_event.tags
                for t in tags_iter:
                    vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
                    if len(vec) >= 2:
                        if vec[0] == 'e':
                            already_replied_events.add(vec[1].lower())
                        elif vec[0] == 'p':
                            already_replied_authors.add(vec[1].lower())
            except Exception:
                pass

        print(f"Loaded {len(already_replied_events)} replied posts and {len(already_replied_authors)} unique authors from history.")

    # 3️⃣ جلب أحدث المنشورات
    f = Filter().kind(Kind(1)).limit(60)
    
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

    # 🔀 خلط القائمة عشوائياً لعدم معالجة المنشورات بنفس الترتيب في كل مرة
    random.shuffle(events_list)

    replies_count = 0
    processed_authors = set()

    for event in events_list:
        if replies_count >= MAX_REPLIES:
            print(f"Reached limit of {MAX_REPLIES} replies. Stopping run.")
            break

        try:
            event_id_obj = event.id()
        except TypeError:
            event_id_obj = event.id

        event_id_hex = (event_id_obj.to_hex() if hasattr(event_id_obj, "to_hex") else str(event_id_obj)).lower()

        try:
            author_hex = event.author().to_hex().lower()
            author_pk = event.author()
        except TypeError:
            author_hex = event.author.to_hex().lower()
            author_pk = event.author

        # 🛑 فحص 1: منع الرد على حسابك
        if bot_hex and author_hex == bot_hex:
            continue

        # 🛑 فحص 2: استبعاد المنشور إن تم الرد عليه سابقاً
        if event_id_hex in already_replied_events:
            print(f"Skipping post {event_id_hex[:8]} (Already replied).")
            continue

        # 🛑 فحص 3: استبعاد صاحب المنشور إن تم التفاعل معه سابقاً
        if author_hex in processed_authors or author_hex in already_replied_authors:
            print(f"Skipping author {author_hex[:8]} (Already interacted with this author).")
            continue

        try:
            content = event.content()
        except TypeError:
            content = event.content

        clean_content = content.strip() if content else ""
        
        if not clean_content or len(clean_content) < 6:
            continue

        # 🛑 فحص 4: استبعاد الفيديوهات
        if contains_video(clean_content):
            print(f"Skipping post {event_id_hex[:8]} (Contains video).")
            continue

        # فحص السبام
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
            already_replied_authors.add(author_hex)
            print(f"Successfully posted reply #{replies_count}: {reply_text}")

            if replies_count < MAX_REPLIES:
                # ⏱️ وقت الانتظار البشري: من 45 إلى 120 ثانية بين كل رد
                sleep_time = random.randint(45, 120)
                print(f"Waiting {sleep_time} seconds before next reply to maintain human pacing...")
                await asyncio.sleep(sleep_time)

    print(f"Finished! Posted {replies_count} organic replies in this run.")

if __name__ == "__main__":
    asyncio.run(main())
