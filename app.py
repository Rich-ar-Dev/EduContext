# app.py
from flask import Flask, request, jsonify, render_template, redirect, url_for
from mysql.connector import Error
import mysql.connector
import os
from dotenv import load_dotenv
from transformers import pipeline
import torch
from intasend import APIService

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

# IntaSend Configuration - LIVE KEYS
INTASEND_PUBLISHABLE_KEY = os.getenv('INTASEND_PUBLISHABLE_KEY')
INTASEND_SECRET_KEY = os.getenv('INTASEND_SECRET_KEY')
PRICE_PREMIUM_ACCESS = int(os.getenv('PRICE_PREMIUM_ACCESS', 20))

# Initialize IntaSend service - LIVE MODE
intasend_service = APIService(
    token=INTASEND_SECRET_KEY,
    publishable_key=INTASEND_PUBLISHABLE_KEY,
    test=False  # LIVE ENVIRONMENT
)

# --- Initialize AI Model ---
print("Loading AI model...")
device = 0 if torch.cuda.is_available() else -1
print(f"Using device: {'GPU' if device == 0 else 'CPU'}")

try:
    generator = pipeline(
        'text2text-generation',
        model='google/flan-t5-base',
        device=device,
        torch_dtype=torch.float16 if device == 0 else torch.float32
    )
    print("AI model loaded successfully!")
except Exception as e:
    print(f"Error loading AI model: {e}")
    generator = None

# --- Database Connection Function ---
def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            print("Successfully connected to the database")
            return connection
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
    return None

# --- AI Explanation Function ---
def get_ai_explanation(topic):
    if generator is None:
        return "Error: AI model is not available. Please check the server logs."

    prompt = f"""
    Explain the real-world importance of {topic} in one engaging paragraph.
    Connect it to three different modern careers, hobbies, or global challenges.
    Write in a motivational tone for high school students.
    """

    try:
        print(f"Generating AI explanation for topic: {topic}")
        result = generator(
            prompt,
            max_length=300,
            do_sample=True,
            temperature=0.7,
            num_return_sequences=1
        )
        
        ai_response = result[0]['generated_text'].strip()
        print(f"AI response generated: {ai_response}")
        return ai_response

    except Exception as e:
        print(f"Error generating AI response: {e}")
        return "Sorry, I encountered an error while generating the explanation."

# --- Database Functions ---
def save_to_database(topic, ai_response):
    connection = get_db_connection()
    if connection is None:
        print("ERROR: Could not connect to database. Save failed.")
        return

    try:
        cursor = connection.cursor()
        sql_query = "INSERT INTO relevance_queries (topic, ai_response) VALUES (%s, %s)"
        cursor.execute(sql_query, (topic, ai_response))
        connection.commit()
        print(f"SUCCESS: Saved topic '{topic}' to the database.")
    except Error as e:
        print(f"DATABASE ERROR: {e}")
        connection.rollback()
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def create_user_payment(user_email, transaction_id, amount, currency='KES'):
    """Save payment information to database"""
    connection = get_db_connection()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        sql_query = """
            INSERT INTO payments (user_email, transaction_id, amount, currency, status)
            VALUES (%s, %s, %s, %s, 'completed')
        """
        cursor.execute(sql_query, (user_email, transaction_id, amount, currency))
        connection.commit()
        return True
    except Error as e:
        print(f"Payment save error: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html', 
                         intasend_publishable_key=INTASEND_PUBLISHABLE_KEY,
                         price=PRICE_PREMIUM_ACCESS)

@app.route('/get_relevance', methods=['POST'])
def get_relevance():
    data = request.get_json()
    topic = data.get('topic')

    if not topic:
        return jsonify({'error': 'No topic provided'}), 400

    print(f"Received topic: {topic}")
    ai_response = get_ai_explanation(topic)
    save_to_database(topic, ai_response)

    return jsonify({'topic': topic, 'relevance': ai_response})

@app.route('/initiate-payment', methods=['POST'])
def initiate_payment():
    try:
        data = request.json
        email = data.get('email')
        phone = data.get('phone')
        
        if not email or not phone:
            return jsonify({
                'success': False,
                'error': 'Email and phone number are required'
            }), 400
        
        # Format phone number (ensure it starts with 254)
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif not phone.startswith('254'):
            phone = '254' + phone
        
        # Initiate M-Pesa STK push - LIVE TRANSACTION
        response = intasend_service.collect.mpesa_stk_push(
            email=email,
            phone_number=phone,
            amount=PRICE_PREMIUM_ACCESS,
            currency='KES',
            narrative='Premium Educational Access'
        )
        
        return jsonify({
            'success': True,
            'invoice_id': response['invoice']['invoice_id'],
            'message': 'M-Pesa payment initiated. Check your phone to complete the payment.'
        })
        
    except Exception as e:
        print(f"Payment initiation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/check-payment-status/<invoice_id>')
def check_payment_status(invoice_id):
    try:
        status = intasend_service.collect.status(invoice_id=invoice_id)
        
        # Check if payment is completed
        if status.get('state') == 'COMPLETE':
            # Save to database
            create_user_payment(
                status.get('email', ''),
                invoice_id,
                status.get('amount', 0),
                'KES'
            )
            
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/success')
def success():
    transaction_id = request.args.get('transaction_id', '')
    amount = request.args.get('amount', '')
    return render_template('success.html', 
                         transaction_id=transaction_id,
                         amount=amount)

@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

@app.route('/payment-page')
def payment_page():
    """Page where user enters payment details"""
    return render_template('payment.html', price=PRICE_PREMIUM_ACCESS)

@app.route('/webhook', methods=['POST'])
def intasend_webhook():
    """Handle IntaSend payment webhooks"""
    try:
        data = request.json
        
        if data.get('event') == 'payment.completed':
            transaction_id = data.get('transaction_id')
            amount = data.get('amount')
            email = data.get('customer', {}).get('email', '')
            
            # Save successful payment to database
            create_user_payment(email, transaction_id, amount, 'KES')
            
            print(f"Payment completed via webhook: {transaction_id} for {amount}")
            
        return jsonify({'status': 'webhook received'})
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 400

@app.route('/test_db')
def test_db():
    connection = get_db_connection()
    if connection:
        connection.close()
        return "Database connection successful!"
    else:
        return "Database connection failed!"

@app.route('/test_ai')
def test_ai():
    if generator is None:
        return "AI model is not loaded. Check server logs."
    
    test_response = get_ai_explanation("mathematics")
    return f"AI test response: {test_response}"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
