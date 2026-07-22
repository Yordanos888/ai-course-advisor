# rag_service.py
import chromadb

def get_educational_context(student_question, max_results=1, distance_threshold=0.6):
    """
    Queries the local Chroma vector database to find relevant announcements.
    Returns the text block if a close match is found, otherwise returns None.
    """
    try:
        # 1. Connect to the local vector repository
        chroma_client = chromadb.PersistentClient(path="chroma_vectors")
        collection = chroma_client.get_collection(name="ece_telegram_announcements")
        
        # 2. Query the database for the single best semantic match
        results = collection.query(
            query_texts=[student_question],
            n_results=max_results
        )
        
        # Check if we actually got any documents back
        if not results or not results['documents'] or len(results['documents'][0]) == 0:
            return None
            
        # 3. Apply a safety threshold (Distance measure)
        # Chroma calculates distance (how 'different' the text is). 
        # Lower distance means a closer, more accurate match.
        distance = results['distances'][0][0]
        matched_text = results['documents'][0][0]
        
        print(f"📡 RAG Search Distance: {distance:.4f}")
        
        if distance <= distance_threshold:
            return matched_text
        
        # If the closest match is too mathematically different, ignore it
        return None

    except Exception as e:
        print(f"⚠️ RAG Retrieval Error: {e}")
        return None