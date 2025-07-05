import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain_openai import ChatOpenAI

# === 1. Load environment variables from .env file ===
load_dotenv()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise EnvironmentError(
        "❌ OPENAI_API_KEY not found in environment. Please set it in your .env file."
    )

# === 2. Initialize LLM (OpenAI GPT-4o) ===
llm = ChatOpenAI(
    openai_api_key=OPENAI_API_KEY,
    model="gpt-4o",
    temperature=0.7
)

# === 3. Load and process PDF documents ===
pdf_folder = r"C:\Users\aksha\EZTax-INDIA\PlakshaChatbot\tax_pdfs"
if not os.path.exists(pdf_folder):
    raise FileNotFoundError(f"❌ Folder not found: {pdf_folder}")

pdf_files = [os.path.join(pdf_folder, f) for f in os.listdir(pdf_folder) if f.endswith('.pdf')]
if not pdf_files:
    raise FileNotFoundError(f"❌ No PDF files found in {pdf_folder}")

docs = []
for pdf_file in pdf_files:
    loader = PyPDFLoader(pdf_file)
    docs.extend(loader.load())

print(f"✅ Loaded {len(docs)} documents from {pdf_folder}")

# === 4. Split and index documents ===
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
chunks = text_splitter.split_documents(docs)
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vectorstore = FAISS.from_documents(chunks, embeddings)
vectorstore.save_local("faiss_index")

# === 5. Conversation memory (per session) ===
# For stateless API, this is shared. For per-user memory, use session/cookies.
memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True, max_token_limit=500)

def is_finance_tax_related(query):
    keywords = [
        "tax", "gst", "income", "itr", "capital gain", "deduction",
        "filing", "assessment", "refund", "audit", "financial year",
        "finance", "exemption", "tds", "income tax", "taxable", "section", "investment"
    ]
    query_lower = query.lower()
    return any(kw in query_lower for kw in keywords)

def call_llm_with_retry(query, max_retries=3):
    for attempt in range(max_retries):
        try:
            return llm.predict(query)
        except Exception as e:
            if "429" in str(e):
                wait_time = 2 ** attempt
                print(f"⏳ Rate limit reached. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"❌ API Error: {e}")
                return "Sorry, I encountered an error processing your request."
    return "I am currently experiencing issues. Please try again later."

def get_answer(query):
    retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 5})
    try:
        retrieved_docs = retriever.invoke(query)
    except Exception as e:
        print(f"❌ Retrieval error: {e}")
        retrieved_docs = []

    if retrieved_docs and any(doc.page_content.strip() for doc in retrieved_docs):
        qa_chain = ConversationalRetrievalChain.from_llm(llm, retriever=retriever, memory=memory)
        try:
            response = qa_chain.invoke({"question": query, "chat_history": memory.chat_memory})
            faiss_answer = response.get("answer", "").strip()
            if "not mention" in faiss_answer.lower() or "does not contain" in faiss_answer.lower():
                if is_finance_tax_related(query):
                    llm_answer = call_llm_with_retry(query)
                    return f"{faiss_answer}\n\n🔹 Additional info from LLM:\n{llm_answer}"
                else:
                    return "⚠️ I can only answer finance or tax-related queries. Please try again with a relevant question."
            return faiss_answer
        except Exception as e:
            print(f"⚠️ FAISS chain error: {e}")
            if is_finance_tax_related(query):
                return call_llm_with_retry(query)
            else:
                return "⚠️ I can only answer finance or tax-related queries."
    else:
        if is_finance_tax_related(query):
            return call_llm_with_retry(query)
        else:
            return "⚠️ I can only answer finance or tax-related queries. Please ask something related to tax or finance."

# === 6. Flask App ===

app = Flask(__name__)
CORS(app)  # Enable CORS for all domains on all routes

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        user_message = data.get("message")
        if not user_message:
            return jsonify({"error": "No message provided."}), 400
        answer = get_answer(user_message)
        return jsonify({
            "answer": answer,
            "source": "OpenAI/LangChain"
        })
    except Exception as e:
        print(f"❌ Error in /chat: {e}")
        return jsonify({ "error": str(e) }), 500

if __name__ == "__main__":
    # Optionally, use env vars for host/port (see [6])
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '5000'))
    app.run(debug=True, host=host, port=port)
