# app.py
from flask import Flask, request, jsonify, render_template, redirect
from mysql.connector import Error
import mysql.connector
import os
from dotenv import load_dotenv
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM
from intasend import APIService

# Load environment variables
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
db_config = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD')
}

# IntaSend Configuration
INTASEND_PUBLISHABLE_KEY = os.getenv('INTASEND_PUBLISHABLE_KEY')
INTASEND_SECRET_KEY = os.getenv('INTASEND_SECRET_KEY')
PRICE_PREMIUM_ACCESS = int(os.getenv('PRICE_PREMIUM_ACCESS', 20))

intasend_service = None
try:
    if INTASEND_PUBLISHABLE_KEY and INTASEND_SECRET_KEY:
        intasend_service = APIService(
            token=INTASEND_SECRET_KEY,
            publishable_key=INTASEND_PUBLISHABLE_KEY,
            test=False
        )
        print("✅ IntaSend initialized successfully (LIVE).")
    else:
        print("⚠️ Warning: Missing IntaSend keys.")
except Exception as e:
    print(f"❌ IntaSend error: {e}")
    intasend_service = None

# --- AI Model (Lazy Load) ---
generator = None

def get_ai_model():
    global generator
    if generator is None:
        try:
            print("⏳ Loading AI model (flan-t5-small, Transformers)...")
            model_id = "google/flan-t5-small"
            tokenizer = AutoTokenizer.from_pretrained(model_id)
            model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
            generator = pipeline(
                "text2text-generation",
                model=model,
                tokenizer=tokenizer
            )
            print("✅ AI model loaded successfully.")
        except Exception as e:
            print(f"❌ AI load error: {e}")
            generator = None
    return generator

# --- Database Connection ---
def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"❌ DB connection error: {e}")
    return None

# --- AI Explanation ---
def get_ai_explanation(topic):
    try:
        gen = get_ai_model()
        if gen is None:
            return "AI model could not be loaded."
        prompt = (
            f"Explain the real-world importance of {topic} in one engaging paragraph. "
            f"Connect it to modern careers, hobbies, or global challenges. "
            f"Write in a motivational tone for high school students."
        )
        result = gen(
            prompt,
            max_length=100,
            do_sample=True,
            temperature=0.7,
            num_return_sequences=1
        )
        return result[0]['generated_text'].strip()
    except Exception as e:
        print(f"❌ AI error: {e}")
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
        print(f"❌ DB error: {e}")
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
        return "✅ Database connection successful!"
    return "❌ Database connection failed!"

@app.route('/test_ai')
def test_ai():
    return get_ai_explanation("mathematics")

# --- Payment Endpoints ---
@app.route('/initiate-payment', methods=['POST'])
def initiate_payment():
    if not intasend_service:
        return jsonify({'success': False, 'error': 'Payment service not configured'}), 500

    data = request.get_json()
    email = data.get('email')
    phone = data.get('phone')

    if not email or not phone:
        return jsonify({'success': False, 'error': 'Email and phone are required'}), 400

    try:
        invoice = intasend_service.invoice.create({
            "amount": PRICE_PREMIUM_ACCESS,
            "currency": "KES",
            "description": "Premium Access for EduContext",
            "customer": {"email": email, "phone": phone},
            "callback_url": ""  # optional webhook
        })
        return jsonify({
            'success': True,
            'invoice_id': invoice['id'],
            'message': 'Payment initiated. Please check your phone to complete the transaction.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/check-payment-status/<invoice_id>')
def check_payment_status(invoice_id):
    if not intasend_service:
        return jsonify({'state': 'ERROR', 'error': 'Payment service not configured'}), 500
    try:
        invoice = intasend_service.invoice.retrieve(invoice_id)
        return jsonify({
            'state': invoice['status'],  # PENDING, COMPLETE, FAILED
            'amount': invoice.get('amount')
        })
    except Exception as e:
        return jsonify({'state': 'ERROR', 'error': str(e)}), 500

@app.route('/success')
def payment_success():
    transaction_id = request.args.get('transaction_id')
    amount = request.args.get('amount')
    return f"✅ Payment successful! Transaction ID: {transaction_id}, Amount: {amount} KES"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
