# testing and verification script

# rag_query.py
import chromadb

# Initialize connection to the local database repository directory
chroma_client = chromadb.PersistentClient(path="chroma_vectors")
collection = chroma_client.get_collection(name="ece_telegram_announcements")

# Simulate a question a student would ask
test_query = "when is the presentation for internship?"

print(f"🔍 Testing retrieval match query for: '{test_query}'")

results = collection.query(
    query_texts=[test_query],
    n_results=2 # Retrieve top 2 most semantically relevant text messages
)

# Print matching findings out explicitly
for idx, doc in enumerate(results['documents'][0]):
    meta = results['metadatas'][0][idx]
    print(f"\nMatch #{idx+1} (Telegram Message ID: {meta['message_id']}):")
    print(f"Context: {doc}")