import streamlit as st
import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Imports for document loading and text splitting
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Import database manager
import db_manager

st.set_page_config(page_title="Personal RAG AI Assistant", page_icon="🤖", layout="wide")

api_key = os.getenv("GROQ_API_KEY", "")

# 1. Initialize Embedding Model
@st.cache_resource
def get_embedding_model():
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

embedding_model = get_embedding_model()

# 2. Load Vector Database
vectorstore = Chroma(persist_directory="./vector_db", embedding_function=embedding_model)

# --- SESSION STATE INITIALIZATION ---
if "user" not in st.session_state:
    st.session_state.user = None

if "active_chat_id" not in st.session_state:
    st.session_state.active_chat_id = None


# --- AUTHENTICATION VIEW ---
if st.session_state.user is None:
    st.title("📄 Personal RAG AI Assistant")
    st.subheader("Please log in or register to access your private chats and documents.")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tab_login, tab_register = st.tabs(["🔑 Login", "📝 Register"])

        with tab_login:
            st.write("### Login")
            login_username = st.text_input("Username", key="login_user")
            login_password = st.text_input("Password", type="password", key="login_pass")
            
            if st.button("Log In", use_container_width=True):
                user = db_manager.authenticate_user(login_username, login_password)
                if user:
                    st.session_state.user = user
                    chats = db_manager.get_user_chats(user["id"])
                    if chats:
                        st.session_state.active_chat_id = chats[0]["id"]
                    else:
                        st.session_state.active_chat_id = db_manager.create_chat(user["id"], "New Chat")
                    st.success("Successfully logged in!")
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

        with tab_register:
            st.write("### Create an Account")
            reg_username = st.text_input("Username", key="reg_user")
            reg_password = st.text_input("Password", type="password", key="reg_pass")
            
            if st.button("Register", use_container_width=True):
                success, msg = db_manager.register_user(reg_username, reg_password)
                if success:
                    st.success("Account created successfully! You can now log in.")
                else:
                    st.error(msg)
    st.stop()


# --- LOGGED IN USER INTERFACE ---
user = st.session_state.user
user_id = user["id"]

# Ensure active chat exists
user_chats = db_manager.get_user_chats(user_id)
if not user_chats:
    st.session_state.active_chat_id = db_manager.create_chat(user_id, "New Chat")
    user_chats = db_manager.get_user_chats(user_id)

if not st.session_state.active_chat_id and user_chats:
    st.session_state.active_chat_id = user_chats[0]["id"]


# --- SIDEBAR UI ---
with st.sidebar:
    st.markdown(f"👤 **Logged in as:** `{user['username']}`")
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.user = None
        st.session_state.active_chat_id = None
        st.rerun()
    
    st.divider()

    # --- CHATS SECTION ---
    st.header("💬 Conversations")
    if st.button("➕ New Chat", use_container_width=True):
        new_chat_id = db_manager.create_chat(user_id, "New Chat")
        st.session_state.active_chat_id = new_chat_id
        st.rerun()

    st.write("")
    for chat in user_chats:
        chat_id = chat["id"]
        title = chat["title"]
        
        is_active = (chat_id == st.session_state.active_chat_id)
        btn_label = f"💬 {title}" if not is_active else f"👉 {title}"
        
        col_chat, col_del = st.columns([5, 1])
        with col_chat:
            if st.button(btn_label, key=f"chat_{chat_id}", use_container_width=True):
                st.session_state.active_chat_id = chat_id
                st.rerun()
        with col_del:
            if st.button("🗑️", key=f"del_{chat_id}"):
                db_manager.delete_chat(chat_id)
                remaining = db_manager.get_user_chats(user_id)
                st.session_state.active_chat_id = remaining[0]["id"] if remaining else None
                st.rerun()

    st.divider()

    # --- DOCUMENT UPLOAD SECTION ---
    st.header("📂 Upload & Train Document")
    uploaded_file = st.file_uploader("Upload Word or PDF", type=["pdf", "docx"], key="doc_uploader")
    
    if uploaded_file is not None:
        if "processed_files" not in st.session_state:
            st.session_state.processed_files = set()

        if uploaded_file.name not in st.session_state.processed_files:
            with st.spinner(f"Vectorizing '{uploaded_file.name}'..."):
                os.makedirs("data/raw_docs", exist_ok=True)
                file_path = os.path.join("data/raw_docs", f"{user_id}_{uploaded_file.name}")
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                if uploaded_file.name.endswith(".pdf"):
                    loader = PyPDFLoader(file_path)
                elif uploaded_file.name.endswith(".docx"):
                    loader = Docx2txtLoader(file_path)
                    
                documents = loader.load()
                
                text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
                chunks = text_splitter.split_documents(documents)
                
                # Tag chunks with user_id for isolation
                for chunk in chunks:
                    chunk.metadata["user_id"] = str(user_id)
                    chunk.metadata["filename"] = uploaded_file.name
                    
                vectorstore.add_documents(chunks)
                db_manager.add_user_document(user_id, uploaded_file.name, len(chunks))
                
                st.session_state.processed_files.add(uploaded_file.name)
                st.success(f"Processed: {len(chunks)} chunks!")
                st.rerun()

    # List User Documents with Delete option
    user_docs = db_manager.get_user_documents(user_id)
    if user_docs:
        st.subheader("Your Trained Documents:")
        for doc in user_docs:
            fname = doc["filename"]
            col_doc, col_doc_del = st.columns([4, 1])
            with col_doc:
                st.caption(f"📄 **{fname}** ({doc['chunk_count']} chunks)")
            with col_doc_del:
                if st.button("🗑️", key=f"deldoc_{fname}"):
                    # Remove from DB
                    db_manager.delete_user_document(user_id, fname)
                    # Remove from Chroma
                    try:
                        vectorstore._collection.delete(where={"$and": [{"user_id": str(user_id)}, {"filename": fname}]})
                    except Exception:
                        pass
                    if "processed_files" in st.session_state and fname in st.session_state.processed_files:
                        st.session_state.processed_files.remove(fname)
                    st.success(f"Deleted '{fname}'")
                    st.rerun()


# --- MAIN CHAT AREA ---
st.title("📄 Private Document Search (Groq AI API)")

if not api_key:
    st.error("⚠️ **GROQ_API_KEY** not found. Please specify your API key in the `.env` file.")
    st.stop()

if not st.session_state.active_chat_id:
    st.info("Select or create a chat thread from the left sidebar.")
    st.stop()

active_chat_id = st.session_state.active_chat_id

# Set up user-filtered retriever
retriever = vectorstore.as_retriever(
    search_kwargs={
        "k": 3,
        "filter": {"user_id": str(user_id)}
    }
)

# Groq LLM
llm = ChatGroq(model_name="openai/gpt-oss-120b", groq_api_key=api_key)

prompt_template = """You are a helpful assistant. Answer the question based STRICTLY on the provided context.
If the answer is not present in the context, state 'The document does not contain this information', do not invent anything.

Conversation history:
{chat_history}

Context from documents: 
{context}

New question: {question}"""

prompt = ChatPromptTemplate.from_template(prompt_template)

def format_docs(docs):
    if not docs:
        return "No relevant user documents found."
    return "\n\n".join(doc.page_content for doc in docs)

# Load existing chat messages from database
messages = db_manager.get_chat_messages(active_chat_id)

# Render messages
for msg in messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# User Chat Input
if user_input := st.chat_input("Ask a question about your documents..."):
    
    # Save user message to database
    db_manager.add_message(active_chat_id, "user", user_input)
    
    # Render user message
    with st.chat_message("user"):
        st.markdown(user_input)
        
    # Auto-update chat title if it's currently "New Chat"
    current_chat_messages = db_manager.get_chat_messages(active_chat_id)
    if len(current_chat_messages) <= 2:
        new_title = user_input[:30] + ("..." if len(user_input) > 30 else "")
        db_manager.update_chat_title(active_chat_id, new_title)
        
    # Build conversation history string
    chat_history_str = ""
    for msg in current_chat_messages[:-1]:
        role = "User" if msg["role"] == "user" else "AI"
        chat_history_str += f"{role}: {msg['content']}\n"
        
    # Retrieve relevant documents for source citation display
    retrieved_docs = retriever.invoke(user_input)
    context_str = format_docs(retrieved_docs)

    # Stream AI Response
    with st.chat_message("assistant"):
        chain = prompt | llm | StrOutputParser()
        response_stream = chain.stream({
            "context": context_str,
            "question": user_input,
            "chat_history": chat_history_str
        })
        full_response = st.write_stream(response_stream)
        
        # Display Source Citations expander
        if retrieved_docs:
            with st.expander("🔍 Source Citations (Retrieved Context)"):
                for idx, doc in enumerate(retrieved_docs, 1):
                    src_name = doc.metadata.get("filename", "Unknown file")
                    st.markdown(f"**Source [{idx}] ({src_name}):**")
                    st.caption(doc.page_content[:400] + ("..." if len(doc.page_content) > 400 else ""))
        
    # Save assistant response to database
    db_manager.add_message(active_chat_id, "assistant", full_response)
    st.rerun()