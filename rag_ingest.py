# This standalone script will run securely via your terminal to scrape messages from your channel,
# chunk the text, and store them directly inside your local Chroma vector store.

import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
import chromadb

# -----------------------------------------------------------------------------
# 1. TELEGRAM API CREDENTIALS & CHANNEL CONFIGURATION
# -----------------------------------------------------------------------------
load_dotenv()
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
CHANNEL_TARGET = os.getenv("CHANNEL_TARGET")

# -----------------------------------------------------------------------------
# 2. CHROMADB VECTOR SYSTEM SETUP
# -----------------------------------------------------------------------------
chroma_client = chromadb.PersistentClient(path="chroma_vectors")
collection = chroma_client.get_or_create_collection(name="ece_telegram_announcements")

# Safety cap for how many NEW messages a single run will pull, even after
# min_id filtering -- protects against an unbounded fetch if the channel had
# an unusually large burst of posts since the last sync.
MAX_NEW_MESSAGES_PER_RUN = 101


def get_last_synced_message_id():
    """
    Finds the highest Telegram message ID already stored in the vector database,
    so this run only fetches messages newer than that. This is what makes
    ingestion incremental instead of re-walking the same recent history every time.
    Returns 0 if the collection is empty (first run ever).
    """
    all_entries = collection.get(include=[])  # ids are always returned; no need to pull documents/metadatas here
    if not all_entries["ids"]:
        return 0
    return max(int(mid) for mid in all_entries["ids"])


async def main():
    print("🚀 Initializing Telethon Client session...")
    client = TelegramClient('session_scraper', API_ID, API_HASH)
    await client.start()
    print("✅ Authenticated successfully with Telegram APIs.")

    last_synced_id = get_last_synced_message_id()
    print(f"📌 Last synced message ID: {last_synced_id} -- only fetching messages newer than this.")

    print(f"📡 Fetching new text entries from channel: {CHANNEL_TARGET}...")

    count = 0
    # FIX: min_id makes Telethon return only messages posted AFTER the last one
    # we already ingested, instead of always re-fetching the newest 100
    # regardless of what's already in the vector store.
    async for message in client.iter_messages(CHANNEL_TARGET, min_id=last_synced_id,
                                                limit=MAX_NEW_MESSAGES_PER_RUN):
        if not message.text or message.text.strip() == "":
            continue

        text_content = message.text.strip()
        message_id = str(message.id)
        timestamp = str(message.date)

        print(f"\n--- Ingesting Message ID #{message_id} ({timestamp}) ---")
        print(text_content[:100] + "..." if len(text_content) > 100 else text_content)

        collection.upsert(
            documents=[text_content],
            metadatas=[{"message_id": message_id, "timestamp": timestamp, "source": "telegram"}],
            ids=[message_id]
        )
        count += 1

    if count == 0:
        print("\n✨ No new messages since last sync -- nothing to do.")
    else:
        print(f"\n✨ Operation Complete. Successfully synchronized {count} NEW text updates to vector memory.")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())