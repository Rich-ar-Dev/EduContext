# app.py
from flask import Flask, request, jsonify, render_template
from mysql.connector import Error
import mysql.connector
import os
from dotenv import load_dotenv
from transformers import pipeline

# --- Lightweight AI backend ---
# Install: pip install optimum onnxruntime
from optimum.onnxruntime import ORTModelForSeq2SeqLM
from transformers import AutoTokenizer

# Load environment variables
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
db_config = {
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD')
}

# IntaSend Configuration
from intasend import APIService

INTASEND_PUBLISHABLE_KEY = os.getenv('INTASEND_PUBLISHABLE_KEY')
INTASEND_SECRET_KEY = os.getenv('INTASEND_SECRET_KEY')
PRICE_PREMIUM_ACCESS = int(os.getenv('PRICE_PREMIUM_ACCESS', 20))

intasend_service = None
try:
    if INTASEND_PUBLISHABLE_KEY and INTASEND_SECRET_KEY:
        intasend_service = APIService(
            token=INTASEND_SECRET_KEY,
            publishable_key=INTASEND_PUBLISHABLE_KEY,
            test=False  # LIVE ENV
        )
        print("IntaSend initialized successfully.")
    else:
        print("Warning: Missing IntaSend keys.")
except Exception as e:
    print(f"IntaSend error: {e}")
    intasend_service = None

# --- AI Model (Lazy Load) ---
generator = None

def get_ai_model():
    global generator
    if generator is None:
        print("Loading AI model (flan-t5-nano, ONNX)...")
        model_id = "google/flan-t5-nano"
        model = ORTModelForSeq2SeqLM.from_pretrained(model_id, from_transformers=True)
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        generator = pipeline(
            "text2text-generation",
            model=model,
            tokenizer=tokenizer,
            framework="onnxruntime"
        )
        print("AI model loaded successfully.")
    return generator

# --- Database Connection ---
def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"DB connection error: {e}")
    return None

# --- AI Explanation ---
def get_ai_explanation(topic):
    try:
        gen = get_ai_model()
        prompt = (
            f"Explain the real-world importance of {topic} in one engaging paragraph. "
            f"Connect it to modern careers, hobbies, or global challenges. "
            f"Write in a motivational tone for high school students."
        )
        result = gen(
            prompt,
            max_length=100,  # reduced for memory
            do_sample=True,
            temperature=0.7,
            num_return_sequences=1
        )
        return result[0]['generated_text'].strip()
    except Exception as e:
        print(f"AI error: {e}")
        return "Sorry, I encountered an error while generating the explanation."

# --- Save to DB ---
def save_to_database(topic, ai_response):
    conn = get_db_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO relevance_queries (topic, ai_response) VALUES (%s, %s)",
            (topic, ai_response)
        )
        conn.commit()
    except Error as e:
        print(f"DB error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# --- Routes ---
@app.route('/')
def index():
    return render_template(
        'index.html',
        intasend_publishable_key=INTASEND_PUBLISHABLE_KEY,
        price=PRICE_PREMIUM_ACCESS
    )

@app.route('/get_relevance', methods=['POST'])
def get_relevance():
    data = request.get_json()
    topic = data.get('topic')
    if not topic:
        return jsonify({'error': 'No topic provided'}), 400
    ai_response = get_ai_explanation(topic)
    save_to_database(topic, ai_response)
    return jsonify({'topic': topic, 'relevance': ai_response})

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'services': {
            'ai_model_loaded': generator is not None,
            'intasend_configured': intasend_service is not None,
            'database_configured': all(db_config.values())
        }
    })

# --- Test Endpoints ---
@app.route('/test_db')
def test_db():
    conn = get_db_connection()
    if conn:
        conn.close()
        return "Database connection successful!"
    return "Database connection failed!"

@app.route('/test_ai')
def test_ai():
    return get_ai_explanation("mathematics")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
