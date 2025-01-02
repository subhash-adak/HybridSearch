import os
import nltk
from langchain_community.retrievers import PineconeHybridSearchRetriever
from langchain_huggingface import HuggingFaceEmbeddings
from pinecone import Pinecone, ServerlessSpec
from pinecone_text.sparse import BM25Encoder
from dotenv import load_dotenv
from langchain_groq import ChatGroq 
from bs4 import BeautifulSoup
import requests
import PyPDF2
import concurrent.futures

# Load environment variables
load_dotenv()

# Initialize API keys
api_key = os.getenv("PINECONE_API_KEY")
groq_api_key = os.getenv("GROQ_API_KEY")
hf_token = os.getenv("HF_TOKEN")

if not api_key or not hf_token:
    raise ValueError("Please ensure PINECONE_API_KEY and HF_TOKEN are set in your .env file.")

# Initialize Pinecone
os.environ["PINECONE_API_KEY"] = api_key
os.environ["GROQ_API_KEY"] = groq_api_key
os.environ["HF_TOKEN"] = hf_token
index_name = "hybrid-search"
pc = Pinecone(api_key=api_key)

# Create index if it doesn't exist
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=384,
        metric="dotproduct",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )

index = pc.Index(index_name)

# Set up embeddings and BM25 encoder
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
bm25_encoder = BM25Encoder().default()

# Check if punkt is already downloaded
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    # Download the 'punkt' tokenizer if not already available
    nltk.download("punkt")

# Initialize PineconeHybridSearchRetriever
retriever = PineconeHybridSearchRetriever(
    embeddings=embeddings,
    sparse_encoder=bm25_encoder,
    index=index,
)


def scrape_webpage(url):
    """Scrape and extract text from a webpage."""
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    return " ".join([p.text for p in soup.find_all('p')])


def scrape_pdf(file_path):
    """Extract text from a PDF file."""
    text = []
    with open(file_path, 'rb') as pdf_file:
        reader = PyPDF2.PdfReader(pdf_file)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:  # Ensure the page has text
                text.append(page_text)
    return " ".join(text)


def preprocess_text(text):
    """Preprocess text by cleaning and splitting into manageable chunks."""
    # Remove special characters and excessive whitespace
    text = " ".join(text.split())
    sentences = nltk.sent_tokenize(text)
    # Ensure we skip empty or invalid sentences
    sentences = [sentence for sentence in sentences if len(sentence.strip()) > 0]
    return sentences


def add_documents_to_retriever(source):
    """Add documents from a PDF or webpage to the retriever."""
    if source.lower().endswith('.pdf'):
        text = scrape_pdf(source)
    elif source.startswith("http://") or source.startswith("https://"):
        text = scrape_webpage(source)
    else:
        raise ValueError("Unsupported source format. Please provide a PDF file or a valid URL.")
    
    # Preprocess text
    sentences = preprocess_text(text)
    
    if not sentences:
        raise ValueError("No valid content extracted from the source.")
    
    # Add texts to the retriever in chunks
    retriever.add_texts(sentences)
    bm25_encoder.fit(sentences)


def query_vector_database(user_query):
    """Query the Pinecone vector database."""
    results = retriever.invoke(user_query)
    if results:
        # Extract text content from the results
        context = "\n".join([doc.page_content for doc in results])
        llm = ChatGroq(
            groq_api_key=groq_api_key,
            model_name="llama3-8b-8192",  # Model name for Groq
            temperature=0.7 
        )
        response = llm.invoke(context + f"\nQuestion: {user_query}\nAnswer:")
        return response.content
    else:
        return "No relevant information found in the database."


def add_documents_batch(sources):
    """Batch add documents from a list of sources using parallel processing."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Execute add_documents_to_retriever concurrently for each source
        executor.map(add_documents_to_retriever, sources)


# Main loop
while True:
    print("\n1. Add PDFs or Webpages\n2. Ask Questions\n3. Quit")
    choice = input("Enter your choice: ").strip()
    
    if choice == '1':
        sources = []
        while True:
            source = input("\nEnter file path or URL (or 'done' to finish): ").strip()
            if source.lower() == 'done':
                break
            sources.append(source)
        
        if sources:
            try:
                # Use batch processing to add documents
                add_documents_batch(sources)
                print("Documents successfully added to the retriever.")
            except Exception as e:
                print(f"Error adding documents: {e}")
        else:
            print("No sources provided.")
    
    elif choice == '2':
        while True:
            user_question = input("\nEnter your question (or 'done' to finish): ").strip()
            if user_question.lower() == 'done':
                break
            try:
                answer = query_vector_database(user_question)
                print(f"\n {answer}")
            except Exception as e:
                print(f"Error retrieving answer: {e}")
    
    elif choice == '3':
        print("Goodbye...")
        break
    
    else:
        print("Invalid choice. Please try again.")
