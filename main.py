import os
import re
import random
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind,
    PublicKey
)
import sys
sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 10
SLEEP_BETWEEN_CYCLES = 300

# مكتبة متنوعة جداً من الخواتم لمنع أي تكرار
DYNAMIC_CLOSINGS = [
    "🕊️ Documenting our family's daily reality in Gaza on my pinned post if you'd like to check it out.",
    "✨ Sharing raw updates & our personal story from Gaza on my pinned note.",
    "🍉 If you have a moment, our story and daily struggle in Gaza are pinned at the top of my profile.",
    "🤍 Holding onto hope amidst the ruins—our journey is shared on my pinned post if you feel like reading.",
    "🕊️ I share honest dispatches of our life in Gaza on my pinned post. Appreciate kind eyes on it.",
    "🌱 Documenting what survival looks like for our family here on my pinned note if you wish to see.",
    "✨ Sharing our lived reality from Gaza on my pinned thread. Wishing you peace.",
    "🕊️ Pinned our personal story & updates on my profile if you'd like to connect with our reality in Gaza.",
    "🍉 Sharing glimpses of our days and hope from Gaza on my pinned note.",
    "🤍 Our story and family updates from northern Gaza are pinned on my profile if you care to read.",
    "🕊️ Sharing raw reflections from our tent in Gaza on my pinned post if you'd like to take a look.",
    "✨ Pinned our journey of resilience & survival here in Gaza at the top of my page.",
    "🌱 Documenting our daily life amidst hardships on my pinned note. Sending warmth your way.",
    "🕊️ Our personal diary and story from Gaza are pinned on my profile if you'd like to see.",
    "🤍 Quietly sharing our family updates from Gaza on my pinned note if you ever want to check."
]

def get_event_tags_list(event):
    """استخراج قائمة الـ Tags بأمان متوافق مع كافة الإصدارات"""
    try:
        raw_tags = event.tags() if callable(event.tags) else event.tags
        if hasattr(raw_tags, "to_vec"):
            return raw_tags.to_vec()
        return list(raw_tags)
    except Exception:
        return []

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
    return any(kw in text_lower for kw in ["solana", "trycloudflare", "kill-fee", "moneymaker", "airdrop", "presale", "telegram", "crypto"])

def is_reply_or_quote(event):
    try:
        tags_iter = get_event_tags_list(event)
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
        "You are Imad, a genuine human being living through the hardships in Gaza, replying authentically on Nostr.\n\n"
        "TASK:\n"
        "Write exactly ONE authentic, conversational, and direct sentence reacting specifically to what the author posted.\n\n"
        "RULES:\n"
        "1. Write ONLY the 1-sentence reaction. Do NOT add any closing note, footer, or link (it will be added dynamically).\n"
        "2. Do NOT use cliché chatbot openings like 'I agree', 'Great perspective', 'Thanks for sharing', 'It is interesting'.\n"
        "3. Sound like a real, thoughtful human on social media.\n"
        "4. If the post is non-English, pure spam, gibberish, or code, respond ONLY with: SKIP"
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Post to reply to: '{prompt_text}'"}
        ],
        "temperature": 0.85
    }
    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if "SKIP" in res_text or "can't react" in res_text.lower() or len(res_text) < 5:
                return None
            if not is_clean_english(res_text):
                return None
            
            # اختيار خاتمة عشوائية متنوعة تماماً
            chosen_closing = random.choice(DYNAMIC_CLOSINGS)
            
            # دمج الرد مع الخاتمة عبر سطرين فارغين
            full_reply = f"{res_text}\n\n{chosen_closing}"
            return full_reply
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
    return None

async def fetch_existing_following(client, bot_pk):
    """جلب قائمة المفاتيح التي يتابعها البوت حالياً لعدم تكرار المتابعة"""
    following_hex = set()
    try:
        f = Filter().author(bot_pk).kind(Kind(3)).limit(1)
        events = await client.fetch_events([f], timedelta(seconds=5))
        ev_list = events.to_vec() if hasattr(events, "to_vec") else list(events)
        if ev_list:
            latest_ev = ev_list[0]
            tags_iter = get_event_tags_list(latest_ev)
            for t in tags_iter:
                vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
                if len(vec) >= 2 and str(vec[0]).lower() == 'p':
                    following_hex.add(str(vec[1]).lower())
    except Exception as e:
        print(f"Error fetching existing following list: {e}")
    return following_hex

async def process_follow_backs(client, bot_pk):
    """متابعة أي شخص تفاعل مع الحساب (Reply, Repost, Zap, Reaction)"""
    if not bot_pk:
        return

    print("Checking for new user interactions (Replies, Reposts, Zaps, Reactions)...")
    existing_follows = await fetch_existing_following(client, bot_pk)
    interacted_authors = set()

    interaction_filter = Filter().pubkey(bot_pk).kinds([Kind(1), Kind(6), Kind(7), Kind(9735)]).limit(100)
    
    try:
        events_obj = await client.fetch_events([interaction_filter], timedelta(seconds=10))
        events_list = events_obj.to_vec() if hasattr(events_obj, "to_vec") else list(events_obj)
        
        for ev in events_list:
            author_pk = ev.author() if callable(ev.author) else ev.author
            author_hex = author_pk.to_hex().lower()
            
            if author_hex != bot_pk.to_hex().lower() and author_hex not in existing_follows:
                interacted_authors.add(author_pk)

        if interacted_authors:
            print(f"Found {len(interacted_authors)} new user(s) who interacted with your profile! Processing Follow Back...")
            
            contacts = [PublicKey.parse(hex_str) for hex_str in existing_follows]
            for new_author in interacted_authors:
                contacts.append(new_author)

            builder = EventBuilder.contact_list(contacts)
            await client.send_event_builder(builder)
            print(f"-> Successfully followed back {len(interacted_authors)} user(s)!")
        else:
            print("No new interaction accounts to follow back at this time.")

    except Exception as e:
        print(f"Error processing follow-backs: {e}")

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
        return

    try:
        keys = Keys.parse(NOSTR_SECRET)
        signer = NostrSigner.keys(keys)
    except Exception as e:
        print(f"Error parsing keys: {e}")
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
            await client.add_relay(r)
        except Exception as e:
            print(f"Error adding relay {r}: {e}")

    await client.connect()
    print("Connected to Nostr Relays!")

    try:
        bot_pk = await signer.public_key()
    except Exception:
        bot_pk = keys.public_key()

    if bot_pk:
        await process_follow_backs(client, bot_pk)

    bot_hex = bot_pk.to_hex().lower() if bot_pk else ""
    already_replied_events = set()
    already_replied_authors = set()

    if bot_pk:
        history_filter = Filter().author(bot_pk).kind(Kind(1)).limit(500)
        try:
            history_obj = await client.fetch_events([history_filter], timedelta(seconds=12))
            history_list = history_obj.to_vec() if hasattr(history_obj, "to_vec") else list(history_obj)
        except Exception:
            history_list = []

        for h_event in history_list:
            try:
                tags_iter = get_event_tags_list(h_event)
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
        events_obj = await client.fetch_events([f], timedelta(seconds=10))
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
            try:
                like_builder = EventBuilder.reaction(event, "+")
                await client.send_event_builder(like_builder)
                print(f"-> Liked post: {event_id_hex[:8]}...")
            except Exception as like_err:
                print(f"Could not send like: {like_err}")

            try:
                t_event = Tag.parse(["e", event_id_hex, "", "reply"])
                t_pubkey = Tag.parse(["p", author_hex])
                builder = EventBuilder(Kind(1), reply_text, [t_event, t_pubkey])
            except Exception:
                try:
                    builder = EventBuilder.text_note(reply_text).tags([Tag.event(event_id_obj), Tag.public_key(author_pk)])
                except Exception:
                    builder = EventBuilder(Kind(1), reply_text, [Tag.event(event_id_obj), Tag.public_key(author_pk)])

            try:
                print(f"Publishing reply #{replies_count + 1} to Nostr network...")
                output = await asyncio.wait_for(client.send_event_builder(builder), timeout=15)
                
                replies_count += 1
                session_authors.add(author_hex)
                already_replied_authors.add(author_hex)
                already_replied_events.add(event_id_hex)

                print(f"-> CONFIRMED & PUBLISHED reply #{replies_count}:\n{reply_text}\n---")
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
    max_cycles = 60
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

    print("Completed all 5-hour cycles. Ready for the next workflow trigger.")

if __name__ == "__main__":
    asyncio.run(main())
