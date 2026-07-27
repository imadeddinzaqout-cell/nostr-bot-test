import os
import sys
import time
import random
import requests
from nostr_sdk import Keys, Client, Filter, Kind, EventBuilder

# ----------------------------------------------------
# 1. الإعدادات والمتغيرات البيئية
# ----------------------------------------------------
PRIVATE_KEY = os.getenv("NOSTR_PRIVATE_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not PRIVATE_KEY or not DEEPSEEK_API_KEY:
    print("خطأ: يرجى إدخال NOSTR_PRIVATE_KEY و DEEPSEEK_API_KEY في المتغيرات البيئية.")
    sys.exit(1)

REST_BETWEEN_CYCLES = 600  # الانتظار 10 دقائق بين كل دورة وأخرى
BATCH_SIZE = 10           # عدد الردود القصوى لكل دورة

# تنويع عبارات الدعوة (CTA)
CTA_VARIANTS = [
    "\n\n(Btw, check my pinned post if you'd like to support my family in Gaza 🙏)",
    "\n\n(Feel free to take a look at my pinned post for our story in Gaza.)",
    "\n\n(Shared more about my family's situation in Gaza on my pinned post if you have a sec.)",
    "",
]

# تعليمات الذكاء الاصطناعي
SYSTEM_PROMPT = """You are a genuine human user on Nostr reacting naturally to posts.
Rules:
1. Keep your reply to 1 concise, direct, human sentence.
2. Be empathetic, casual, and down-to-earth.
3. NEVER start with generic AI phrases like "I completely agree", "Great perspective", etc.
4. Do NOT mention Gaza or donations in the comment body itself; just respond naturally to what the author wrote.
5. If the post is offensive, spam, or incoherent, respond with 'SKIP'."""

# ----------------------------------------------------
# 2. الدوال المساعدة
# ----------------------------------------------------

def generate_ai_reply(post_content):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Post: \"{post_content}\""}
        ],
        "temperature": 0.7,
        "max_tokens": 80
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            reply = response.json()["choices"][0]["message"]["content"].strip()
            if "SKIP" in reply or len(reply) < 5:
                return None
            return reply
        else:
            print(f"DeepSeek API Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"DeepSeek Connection Error: {e}")
        return None


def is_valid_event(event_content, author, replied_authors):
    content = event_content.strip()
    if len(content) < 15:
        return False
    if author in replied_authors:
        return False
    if content.startswith("http://") or content.startswith("https://"):
        return False
    bot_keywords = ["as an ai", "i cannot", "language model", "bot"]
    if any(kw in content.lower() for kw in bot_keywords):
        return False
    return True


# ----------------------------------------------------
# 3. حلقة التشغيل الرئيسية
# ----------------------------------------------------

def main():
    try:
        keys = Keys.parse(PRIVATE_KEY.strip())
        client = Client(keys)
        print("✅ تم إعداد المفتاح الخاص بنجاح.")
    except Exception as e:
        print(f"❌ خطأ في قراءة المفتاح الخاص: {e}")
        sys.exit(1)

    # السيرفرات المستخدمة (Relays)
    relays = [
        "wss://relay.damus.io",
        "wss://nos.lol",
        "wss://relay.nostr.band",
        "wss://purplepag.es"
    ]
    for relay in relays:
        client.add_relay(relay)

    client.connect()
    print("تم الاتصال بسيرفرات Nostr بنجاح.")

    replied_authors = set()

    while True:
        try:
            print("\n--- بدء دورة معالجة جديدة ---")
            
            filter_req = Filter().kind(Kind(1)).limit(30)
            events = client.get_events_of([filter_req], timeout=10)

            processed_count = 0

            for event in events:
                if processed_count >= BATCH_SIZE:
                    break

                author = event.author().to_hex()
                content = event.content()

                if not is_valid_event(content, author, replied_authors):
                    continue

                print(f"\nمعالجة منشور من {author[:8]}...: {content[:40]}...")

                ai_reply = generate_ai_reply(content)
                if not ai_reply:
                    print("تخطي (الرد غير مناسب أو رفضه النظام).")
                    continue

                selected_cta = random.choice(CTA_VARIANTS)
                final_reply = f"{ai_reply}{selected_cta}"

                builder = EventBuilder.text_note_reply(
                    content=final_reply,
                    reply_to=event,
                    relay_url=None
                )
                
                client.send_event_builder(builder)
                print(f"✅ تم نشر الرد: {final_reply}")

                replied_authors.add(author)
                processed_count += 1

                time.sleep(random.randint(5, 15))

            print(f"\nاكتملت الدورة. تم نشر {processed_count} ردود.")
            print(f"الانتظار لمدة {REST_BETWEEN_CYCLES} ثانية ({REST_BETWEEN_CYCLES // 60} دقائق) قبل الدورة القادمة...")
            time.sleep(REST_BETWEEN_CYCLES)

        except KeyboardInterrupt:
            print("\nتم إيقاف البوت يدوياً.")
            sys.exit(0)
        except Exception as e:
            print(f"\n⚠️ حدث خطأ غير متوقع في الحلقة: {e}")
            print("إعادة المحاولة بعد 60 ثانية...")
            time.sleep(60)


if __name__ == "__main__":
    main()
