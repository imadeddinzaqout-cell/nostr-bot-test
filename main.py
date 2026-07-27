import os
import re
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind,
    NostrConnect, NostrConnectUri, RelayUrl
)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 10
DELAY_BETWEEN_REPLIES = 10
REST_BETWEEN_CYCLES = 180  # انتظار 3 دقائق بين كل دورة

def is_spam(text):
    if not text or len(text.strip()) < 5:  # تجاهل المنشورات الفارغة أو القصيرة جداً
        return True
    
    spam_keywords = ["solana", "toolkit", "trycloudflare", "invoice-generator", "airdrop", "presale", "http://", "https://"]
    text_lower = text.lower()
    url_count = len(re.findall(r'https?://\S+', text))
    if url_count >= 2:
        return True
    for kw in ["solana", "trycloudflare", "kill-fee", "moneymaker"]:
        if kw in text_lower:
            return True
    return False

def generate_ai_reply(prompt_text):
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # صياغة محسنة تتجنب فلاتر الأمان مع الحفاظ على اللهجة الإنسانية
    system_instruction = (
        "You are writing short, warm, empathetic replies on Nostr in Arabic or English (match the post's language).\n"
        "1. Comment naturally on the content of the post in 1-2 friendly sentences.\n"
        "2. Add a very brief, polite concluding sentence asking the author/readers to check your pinned post for solidarity with Gaza.\n"
        "3. Maintain a natural, human, and humble tone. Avoid corporate jargon or robotic phrasing.\n"
        "4. If the input post is meaningless, automated code, or pure spam, respond ONLY with the single word: SKIP"
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
            
            # فلتر لالتقاط رسائل الرفض من الذكاء الاصطناعي لمنع نشرها
            refusal_keywords = ["أعتذر", "لا يمكنني", "سياسات", "تقمص", "I cannot", "I am sorry", "as an AI", "policy"]
            if any(kw in res_text for kw in refusal_keywords) or "SKIP" in res_text:
                print("Skipping: AI declined or returned SKIP/Refusal.")
                return None
                
            return res_text
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
    return None

async def run_batch():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub (NOSTR_NSEC or DEEPSEEK_API_KEY).")
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
            break

        try:
            content = event.content()
        except TypeError:
            content = event.content

        # التحقق من السبام والمنشورات الفارغة
        if is_spam(content):
            continue

        try:
            author_hex = event.author().to_hex()
            author_pk = event.author()
        except TypeError:
            author_hex = event.author.to_hex()
            author_pk = event.author

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
            print(f"Successfully posted reply #{replies_count}!")

            if replies_count < MAX_REPLIES:
                await asyncio.sleep(DELAY_BETWEEN_REPLIES)

    print(f"Finished current batch: Posted {replies_count} replies.")

async def main():
    cycle = 1
    while True:
        print(f"\n================ STARTING CYCLE #{cycle} ================")
        try:
            await run_batch()
        except Exception as e:
            print(f"Error during cycle #{cycle}: {e}")
        
        print(f"Cycle #{cycle} complete. Sleeping for {REST_BETWEEN_CYCLES} seconds before next cycle...")
        await asyncio.sleep(REST_BETWEEN_CYCLES)
        cycle += 1

if __name__ == "__main__":
    asyncio.run(main())
