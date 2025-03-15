from flask import Flask, render_template, request, redirect, url_for, flash, session
import pandas as pd
import os
import time
import uuid
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from werkzeug.utils import secure_filename
import threading
import logging

app = Flask(__name__)
app.secret_key = 'whatsapp_bulk_sender_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64MB max upload size
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls', 'mp4', 'avi', 'mov', 'wmv'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Set up logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# Global variables to track progress
running_tasks = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def process_excel_file(file_path):
    try:
        df = pd.read_excel(file_path)
        if 'name' not in df.columns or 'number' not in df.columns:
            return None, "Excel file must contain 'name' and 'number' columns"
        return df, None
    except Exception as e:
        logger.error(f"Error processing Excel file: {e}")
        return None, f"Error processing Excel file: {str(e)}"

def send_whatsapp_messages(task_id, excel_file, video_file, message_template):
    # Initialize status
    running_tasks[task_id] = {
        'status': 'initializing',
        'progress': 0,
        'total': 0,
        'successful': 0,
        'failed': 0,
        'log': []
    }
    
    # Process Excel file
    df, error = process_excel_file(excel_file)
    if error:
        running_tasks[task_id]['status'] = 'failed'
        running_tasks[task_id]['log'].append(error)
        return
    
    running_tasks[task_id]['total'] = len(df)
    running_tasks[task_id]['log'].append(f"Successfully loaded {len(df)} contacts from Excel file")
    
    # Check if video file exists
    if not os.path.exists(video_file):
        running_tasks[task_id]['status'] = 'failed'
        running_tasks[task_id]['log'].append(f"Error: Video file not found")
        return
    
    # Setup Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--user-data-dir=./whatsapp_session")
    
    # Initialize the driver
    try:
        running_tasks[task_id]['status'] = 'starting_browser'
        running_tasks[task_id]['log'].append("Starting Chrome browser...")
        driver = webdriver.Chrome(options=chrome_options)
        driver.get("https://web.whatsapp.com/")
        running_tasks[task_id]['log'].append("Waiting for WhatsApp Web to load...")
        
        # Wait for WhatsApp Web to load
        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//div[@data-testid='chat-list']"))
            )
            running_tasks[task_id]['log'].append("WhatsApp Web loaded successfully")
        except Exception as e:
            running_tasks[task_id]['log'].append("Please scan the QR code to log in to WhatsApp Web")
            # Give more time for QR code scan
            time.sleep(30)
        
        # Send messages to each contact
        running_tasks[task_id]['status'] = 'sending'
        
        for index, row in df.iterrows():
            try:
                name = str(row['name'])
                phone_number = str(row['number'])
                
                # Format phone number (remove any non-digit characters)
                phone_number = ''.join(filter(str.isdigit, phone_number))
                
                # Create personalized message
                personalized_message = message_template.replace("{name}", name)
                
                running_tasks[task_id]['log'].append(f"Sending message to {name} ({phone_number})...")
                
                # Navigate to the chat
                chat_url = f"https://web.whatsapp.com/send?phone={phone_number}"
                driver.get(chat_url)
                
                # Wait for the chat to load
                try:
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true']"))
                    )
                except Exception:
                    running_tasks[task_id]['log'].append(f"Failed to load chat for {name} ({phone_number}). Number might be invalid.")
                    running_tasks[task_id]['failed'] += 1
                    running_tasks[task_id]['progress'] += 1
                    continue
                
                # Send text message
                message_box = driver.find_element(By.XPATH, "//div[@contenteditable='true']")
                message_box.send_keys(personalized_message)
                message_box.send_keys("\n")  # Send message
                
                time.sleep(2)  # Wait for message to send
                
                # Attach video
                try:
                    # Click on attachment icon
                    attachment_btn = driver.find_element(By.XPATH, "//div[@title='Attach']")
                    attachment_btn.click()
                    time.sleep(1)
                    
                    # Click on document/media option
                    media_btn = driver.find_element(By.XPATH, "//input[@accept='*']")
                    media_btn.send_keys(os.path.abspath(video_file))
                    time.sleep(3)  # Wait for video to upload
                    
                    # Send the video
                    send_btn = WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[@data-icon='send']"))
                    )
                    send_btn.click()
                    
                    running_tasks[task_id]['log'].append(f"Message and video sent successfully to {name}")
                    running_tasks[task_id]['successful'] += 1
                    
                    time.sleep(5)  # Wait between messages to avoid being blocked
                    
                except Exception as e:
                    running_tasks[task_id]['log'].append(f"Error sending video to {name}: {str(e)}")
                    running_tasks[task_id]['failed'] += 1
            
            except Exception as e:
                running_tasks[task_id]['log'].append(f"Error processing contact {name}: {str(e)}")
                running_tasks[task_id]['failed'] += 1
            
            running_tasks[task_id]['progress'] += 1
        
        # Close the browser
        driver.quit()
        
        # Print summary
        running_tasks[task_id]['status'] = 'completed'
        running_tasks[task_id]['log'].append("\nSummary:")
        running_tasks[task_id]['log'].append(f"Total contacts: {running_tasks[task_id]['total']}")
        running_tasks[task_id]['log'].append(f"Successful: {running_tasks[task_id]['successful']}")
        running_tasks[task_id]['log'].append(f"Failed: {running_tasks[task_id]['failed']}")
        
    except Exception as e:
        running_tasks[task_id]['status'] = 'failed'
        running_tasks[task_id]['log'].append(f"Critical error: {str(e)}")
        logger.error(f"Critical error in task {task_id}: {str(e)}")
        try:
            driver.quit()
        except:
            pass

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'excel-file' not in request.files or 'video-file' not in request.files:
        flash('Both Excel file and video file are required', 'error')
        return redirect(request.url)
    
    excel_file = request.files['excel-file']
    video_file = request.files['video-file']
    message_template = request.form.get('message-template', '')
    
    if excel_file.filename == '' or video_file.filename == '':
        flash('No selected file', 'error')
        return redirect(request.url)
    
    if not allowed_file(excel_file.filename) or not allowed_file(video_file.filename):
        flash('File type not allowed', 'error')
        return redirect(request.url)
    
    # Save files
    task_id = str(uuid.uuid4())
    excel_filename = secure_filename(excel_file.filename)
    video_filename = secure_filename(video_file.filename)
    
    excel_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}_{excel_filename}")
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}_{video_filename}")
    
    excel_file.save(excel_path)
    video_file.save(video_path)
    
    # Start processing in a separate thread
    thread = threading.Thread(
        target=send_whatsapp_messages, 
        args=(task_id, excel_path, video_path, message_template)
    )
    thread.daemon = True
    thread.start()
    
    return redirect(url_for('status', task_id=task_id))

@app.route('/status/<task_id>')
def status(task_id):
    if task_id not in running_tasks:
        flash('Task not found', 'error')
        return redirect(url_for('index'))
    
    task = running_tasks[task_id]
    return render_template('status.html', task_id=task_id, task=task)

@app.route('/api/status/<task_id>')
def api_status(task_id):
    if task_id not in running_tasks:
        return {'status': 'not_found'}
    
    return running_tasks[task_id]

@app.route('/clear/<task_id>')
def clear_task(task_id):
    if task_id in running_tasks:
        del running_tasks[task_id]
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)