# This standalone script will run securely via your terminal to scrape messages from your channel, 
# chunk the text, and store them directly inside your local Chroma vector store.

import os
import asyncio
from telethon import TelegramClient
import chromadb

# -----------------------------------------------------------------------------
# 1. TELEGRAM API CREDENTIALS & CHANNEL CONFIGURATION
# -----------------------------------------------------------------------------
# Replace these strings with your real credentials from my.telegram.org
API_ID = 22156048                 # Must be an integer
API_HASH = '608cd527c76651a9675bbacd54537ceb'   # Must be a string

# The username or link of your custom Telegram channel
# e.g., 'my_ece_announcements_channel' or 'https://t.me/joinchat/...'
CHANNEL_TARGET = 'https://t.me/ece_updates' 

# -----------------------------------------------------------------------------
# 2. CHROMADB VECTOR SYSTEM SETUP
# -----------------------------------------------------------------------------
# Initializes a local vector database directory on your machine
chroma_client = chromadb.PersistentClient(path="chroma_vectors")

# Create or fetch the text collection (uses Chroma's built-in default embedding utility)
collection = chroma_client.get_or_create_collection(name="ece_telegram_announcements")

async def main():
    print("🚀 Initializing Telethon Client session...")
    # 'session_scraper' saves your login token locally so you only log in once via phone verification
    client = TelegramClient('session_scraper', API_ID, API_HASH)
    await client.start()
    print("✅ Authenticated successfully with Telegram APIs.")

    print(f"📡 Fetching text entries from channel: {CHANNEL_TARGET}...")
    
    count = 0
    # Iterates through messages from the channel target
    async for message in client.iter_messages(CHANNEL_TARGET, limit=100):
        # Grounding Constraint: Filter out service messages, empty blocks, or media-only structures
        if not message.text or message.text.strip() == "":
            continue
            
        text_content = message.text.strip()
        message_id = str(message.id)
        timestamp = str(message.date)

        print(f"\n--- Ingesting Message ID #{message_id} ({timestamp}) ---")
        print(text_content[:100] + "..." if len(text_content) > 100 else text_content)

        # Upsert the text message into our local vector database storage
        collection.upsert(
            documents=[text_content],
            metadatas=[{"message_id": message_id, "timestamp": timestamp, "source": "telegram"}],
            ids=[message_id]
        )
        count += 1

    print(f"\n✨ Operation Complete. Successfully synchronized {count} text updates to vector memory.")
    await client.disconnect()

if __name__ == '__main__':
    # Execute the asynchronous scraping loop execution environment safely
    asyncio.run(main())