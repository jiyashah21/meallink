from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import sqlite3
from datetime import datetime, timedelta
import threading
import os
from werkzeug.utils import secure_filename
import requests
from math import radians, sin, cos, sqrt, atan2

app = Flask(__name__)
app.secret_key = "meal_link_secret_key"

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ---------------------------
# DATABASE CONNECTION SETUP
# ---------------------------
db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect('food_waste_db.sqlite', timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    with db_lock:
        conn = get_db_connection()
        cur = conn.cursor()

        # Users table
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        role TEXT CHECK(role IN ('restaurant', 'ngo', 'admin')) NOT NULL,
                        phone TEXT,
                        location TEXT,
                        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );''')

        # Donations table
        cur.execute('''CREATE TABLE IF NOT EXISTS donations (
                        donation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        meal_title TEXT,
                        food_type TEXT,
                        quantity TEXT,
                        expiry_time DATETIME,
                        location TEXT,
                        image_path TEXT,
                        status TEXT CHECK(status IN ('available','claimed','expired')) DEFAULT 'available',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    );''')

        # Claims table
        cur.execute('''CREATE TABLE IF NOT EXISTS claims (
                        claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        donation_id INTEGER,
                        ngo_id INTEGER,
                        status TEXT CHECK(status IN ('pending','delivered')) DEFAULT 'pending',
                        claim_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        quantity INTEGER Not null,
                        FOREIGN KEY (donation_id) REFERENCES donations(donation_id),
                        FOREIGN KEY (ngo_id) REFERENCES users(user_id)
                    );''')

        # Feedback Table
        cur.execute('''CREATE TABLE IF NOT EXISTS feedback (
                        feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        message TEXT,
                        rating INTEGER CHECK(rating BETWEEN 1 AND 5),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    );''')

        

        conn.commit()
        conn.close()

create_tables()

# ---------------------------
# ROUTES
# ---------------------------

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/help')
def help():
    return render_template('help.html')

# ---------- SIGN IN / SIGN UP ----------
@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        action = request.form.get('action')

        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()

            if action == 'register':
                name = request.form['name']
                email = request.form['email']
                password = request.form['password']
                phone = request.form['phone']
                location = request.form['location']
                role = request.form['role']

                if not phone.isdigit() or len(phone) != 10:
                    conn.close()
                    return render_template('signin.html', error="Please enter a valid 10-digit phone number.", active_tab='register')

                existing_user = cur.execute("SELECT * FROM users WHERE email = ? OR phone = ?", (email, phone)).fetchone()

                if existing_user:
                    conn.close()
                    return render_template('signin.html', error="Email or phone number already registered.", active_tab='register')

                cur.execute('''INSERT INTO users (name, email, password, role, phone, location)
                               VALUES (?, ?, ?, ?, ?, ?)''', (name, email, password, role, phone, location))
                conn.commit()
                conn.close()
                flash('Account created successfully! Please log in.')
                return render_template('signin.html', message="Registration successful! Please log in.", active_tab='login')

            elif action == 'login':
                email = request.form['email']
                password = request.form['password']

                user = cur.execute('SELECT * FROM users WHERE email = ? AND password = ?', (email, password)).fetchone()
                conn.close()

                if user:
                    session['user_id'] = user['user_id']
                    session['role'] = user['role']
                    session['name'] = user['name']
                    session['location'] = user['location'] or ''  # ✅ ensure non-None
                    flash(f"Welcome {user['name']}!")
                    return redirect(url_for('dashboard'))
                else:
                    return render_template('signin.html', error="Invalid email or password.", active_tab='login')

    return render_template('signin.html', active_tab='login')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.')
    return redirect(url_for('signin'))

@app.route('/claim_or_donate')
def claim_or_donate():
    if 'user_id' not in session:
        return redirect(url_for('signin'))

    role = session['role']

    if role == 'ngo':
        return redirect(url_for('ngo_dashboard'))
    elif role == 'restaurant':
        return redirect(url_for('dashboard'))  # your donation form dashboard
    else:
        return redirect(url_for('signin'))


# ---------- DASHBOARD ----------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('signin'))

    role = session['role']
    conn = get_db_connection()

    if role == 'restaurant':
        donations = conn.execute('SELECT * FROM donations WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],)).fetchall()

        summary = {
            'available': sum(1 for d in donations if d['status'] == 'available'),
            'claimed': sum(1 for d in donations if d['status'] == 'claimed'),
            'expired': sum(1 for d in donations if d['status'] == 'expired')
        }

        # Collect all claims (including partial claims)
        claims_overview = []
        for d in donations:
            claims = conn.execute('''
                SELECT u.name, c.quantity, strftime('%d-%m-%Y %H:%M', c.claim_time) AS claimed_at
                FROM claims c
                JOIN users u ON c.ngo_id = u.user_id
                WHERE c.donation_id = ?
                ORDER BY c.claim_time ASC
            ''', (d['donation_id'],)).fetchall()

            for c in claims:
                claims_overview.append({
                    'meal_title': d['meal_title'],
                    'ngo_name': c['name'],
                    'quantity': c['quantity'],
                    'claimed_at': c['claimed_at']
                })

        conn.close()
        return render_template(
            'donationform.html',
            donations=donations,
            summary=summary,
            claims_overview=claims_overview
        )


    elif role == 'ngo':
        donations = conn.execute("SELECT * FROM donations WHERE status='available' ORDER BY created_at DESC").fetchall()
        conn.close()
        return render_template('ngoform.html', donations=donations)

    conn.close()
    return redirect(url_for('signin'))



# ---------- ADD DONATION ----------
@app.route('/add_donation', methods=['POST'])
def add_donation():
    if 'user_id' not in session or session['role'] != 'restaurant':
        return redirect(url_for('signin'))

    meal_title = request.form.get('meal_title')
    food_type = request.form.get('food_type')
    quantity = request.form.get('quantity')
    expiry_time = request.form.get('expiry_time')
    location = request.form.get('location')
    image = request.files.get('image')
    image_path = None

    if image and image.filename:
        filename = secure_filename(image.filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)

    with db_lock:
        conn = get_db_connection()
        conn.execute('''INSERT INTO donations (user_id, meal_title, food_type, quantity, expiry_time, location, image_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''', (session['user_id'], meal_title, food_type, quantity, expiry_time, location, image_path))
        conn.commit()
        conn.close()

    flash('Donation listed successfully!')
    return redirect(url_for('dashboard'))

# ---------- EDIT DONATION ----------
@app.route('/edit_donation', methods=['POST'])
def edit_donation():
    if 'user_id' not in session or session['role'] != 'restaurant':
        return redirect(url_for('signin'))

    donation_id = request.form.get('donation_id')
    meal_title = request.form.get('meal_title')
    food_type = request.form.get('food_type')
    quantity = request.form.get('quantity')
    expiry_time = request.form.get('expiry_time')
    location = request.form.get('location')
    image = request.files.get('image')
    image_path = None

    conn = get_db_connection()
    existing = conn.execute("SELECT image_path FROM donations WHERE donation_id=? AND user_id=?", (donation_id, session['user_id'])).fetchone()
    if existing:
        image_path = existing['image_path']

    if image and image.filename:
        filename = secure_filename(image.filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)

    with db_lock:
        conn.execute('''UPDATE donations 
                        SET meal_title=?, food_type=?, quantity=?, expiry_time=?, location=?, image_path=? 
                        WHERE donation_id=? AND user_id=?''', (meal_title, food_type, quantity, expiry_time, location, image_path, donation_id, session['user_id']))
        conn.commit()
    conn.close()

    flash("Donation updated successfully!")
    return redirect(url_for('dashboard'))

# ---------- TOGGLE STATUS ----------
@app.route('/toggle_status/<int:donation_id>', methods=['POST'])
def toggle_status(donation_id):
    if 'user_id' not in session or session['role'] != 'restaurant':
        return redirect(url_for('signin'))

    with db_lock:
        conn = get_db_connection()
        donation = conn.execute("SELECT status FROM donations WHERE donation_id=? AND user_id=?", (donation_id, session['user_id'])).fetchone()
        if donation:
            new_status = 'claimed' if donation['status'] == 'available' else 'available'
            conn.execute("UPDATE donations SET status=? WHERE donation_id=? AND user_id=?", (new_status, donation_id, session['user_id']))
            conn.commit()
        conn.close()

    flash("Donation status updated!")
    return redirect(url_for('dashboard'))

# ---------- FEEDBACK ----------
@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if 'user_id' not in session:
        return redirect(url_for('signin'))

    if request.method == 'POST':
        message = request.form['message']
        rating = int(request.form['rating'])

        with db_lock:
            conn = get_db_connection()
            conn.execute(
                'INSERT INTO feedback (user_id, message, rating) VALUES (?, ?, ?)',
                (session['user_id'], message, rating)
            )
            conn.commit()
            conn.close()

        flash('Feedback submitted successfully!')
        return redirect(url_for('feedback'))
    return render_template('feedback.html')




# ---------------------------
# NGO DASHBOARD
# ---------------------------
@app.route('/ngo_dashboard')
def ngo_dashboard():
    if 'user_id' not in session or session['role'] != 'ngo':
        return redirect(url_for('signin'))

    conn = get_db_connection()
    donations = conn.execute('''
        SELECT d.*, u.name AS restaurant_name, d.location AS restaurant_location
        FROM donations d
        JOIN users u ON d.user_id = u.user_id
        WHERE d.status = 'available'
        ORDER BY d.created_at DESC
    ''').fetchall()
    conn.close()

    total_batches = len(donations)
    total_portions = sum(int(d['quantity']) for d in donations if str(d['quantity']).isdigit())

    now = datetime.now()
    expiring_soon = 0
    for d in donations:
        expiry_str = d['expiry_time']
        if expiry_str:
            try:
                expiry = datetime.strptime(expiry_str.replace("T", " "), "%Y-%m-%d %H:%M")
                if expiry <= now + timedelta(hours=2):
                    expiring_soon += 1
            except Exception:
                pass

    claimed_today = 0  # can update later if you track claims timestamp

    return render_template(
        'ngoform.html',
        donations=donations,
        total_batches=total_batches,
        total_portions=total_portions,
        expiring_soon=expiring_soon,
        claimed_today=claimed_today
    )


# ---------------------------
# CLAIM DONATION
# ---------------------------
@app.route('/claim/<int:donation_id>', methods=['POST'])
def claim_donation(donation_id):
    if 'user_id' not in session or session['role'] != 'ngo':
        return redirect(url_for('signin'))

    claim_qty = int(request.form.get('claim_qty', 0))
    if claim_qty <= 0:
        flash('Invalid quantity selected.')
        return redirect(url_for('ngo_dashboard'))

    conn = get_db_connection()
    donation = conn.execute(
        "SELECT quantity FROM donations WHERE donation_id = ?", (donation_id,)
    ).fetchone()

    if not donation:
        conn.close()
        flash('Donation not found.')
        return redirect(url_for('ngo_dashboard'))

    available_qty = int(donation['quantity'])
    if claim_qty >= available_qty:
        conn.execute(
            "UPDATE donations SET quantity = 0, status = 'claimed' WHERE donation_id = ?",
            (donation_id,)
        )
    else:
        conn.execute(
            "UPDATE donations SET quantity = ?, status = 'available' WHERE donation_id = ?",
            (available_qty - claim_qty, donation_id)
        )

    conn.execute(
        "INSERT INTO claims (donation_id, ngo_id, quantity, status) VALUES (?, ?, ?, 'pending')",
        (donation_id, session['user_id'], claim_qty)
    )


    conn.commit()
    conn.close()

    flash('Food claimed successfully!', 'success')
    return redirect(url_for('ngo_dashboard'))


# ---------------------------
# API: FILTER DONATIONS
# ---------------------------
@app.route('/api/donations')
def api_donations():
    search = request.args.get('search', '').strip()
    food_type = request.args.get('type', '').strip()
    sort_by = request.args.get('sortBy', 'expiry_asc')

    conn = get_db_connection()
    cur = conn.cursor()

    # Base query
    query = '''
        SELECT d.donation_id, d.meal_title, d.food_type, d.quantity,
               d.expiry_time, d.location AS restaurant_location,
               u.name AS restaurant_name, d.image_path
        FROM donations d
        JOIN users u ON d.user_id = u.user_id
        WHERE d.status = 'available'
    '''
    params = []

    if search:
        query += ' AND (u.name LIKE ? OR d.meal_title LIKE ?)'
        params += [f'%{search}%', f'%{search}%']

    if food_type:
        query += ' AND LOWER(d.food_type) = ?'
        params.append(food_type.lower())

    # Sorting with CAST to INTEGER
    if sort_by == 'expiry_asc':
        query += ' ORDER BY d.expiry_time ASC'
    elif sort_by == 'expiry_desc':
        query += ' ORDER BY d.expiry_time DESC'
    elif sort_by == 'quantity_asc':
        query += ' ORDER BY CAST(d.quantity AS INTEGER) ASC'
    elif sort_by == 'quantity_desc':
        query += ' ORDER BY CAST(d.quantity AS INTEGER) DESC'

    donations = cur.execute(query, params).fetchall()

    # Claims today
    claimed_dict = {}
    claim_rows = cur.execute('''
        SELECT donation_id, SUM(CAST(quantity AS INTEGER)) AS claimed_today
        FROM claims
        WHERE DATE(claim_time) = DATE('now')
        GROUP BY donation_id
    ''').fetchall()

    for row in claim_rows:
        claimed_dict[row['donation_id']] = row['claimed_today']

    conn.close()

    result = [
        {
            'donation_id': d['donation_id'],
            'restaurant_name': d['restaurant_name'],
            'meal_title': d['meal_title'],
            'restaurant_location': d['restaurant_location'],
            'food_type': d['food_type'],
            'quantity': int(d['quantity']),  # make sure frontend gets integer
            'expiry_time': d['expiry_time'].replace(' ', 'T') if d['expiry_time'] else '',
            'image_path': url_for('static', filename='uploads/' + d['image_path'].split('/')[-1]) if d['image_path'] else url_for('static', filename='uploads/default.jpg'),
            'claimed_today': claimed_dict.get(d['donation_id'], 0)
        }
        for d in donations
    ]

    return jsonify(result)


@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('signin'))

    conn = get_db_connection()
    user_id = session['user_id']
    role = session['role']

    # Fetch user info using correct column
    user = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()

    if not user:
        return redirect(url_for('signin'))

    user_data = {
        'user_id': user['user_id'],
        'name': user['name'],
        'email': user['email'],
        'phone': user['phone'],
        'location': user['location'],
        'role': user['role'],
        'joined': user['created_at'] if 'created_at' in user.keys() else 'N/A'
    }

    # Role-specific summary
    if role == 'restaurant':
        stats = conn.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = "claimed" THEN 1 ELSE 0 END) as claimed,
                SUM(CASE WHEN status = "expired" THEN 1 ELSE 0 END) as expired
            FROM donations WHERE user_id = ?
        ''', (user_id,)).fetchone()

        summary = {
            'total': stats['total'] or 0,
            'claimed': stats['claimed'] or 0,
            'expired': stats['expired'] or 0
        }

    elif role == 'ngo':
        stats = conn.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = "delivered" THEN 1 ELSE 0 END) as delivered
            FROM claims WHERE ngo_id = ?
        ''', (user_id,)).fetchone()

        summary = {
            'total': stats['total'] or 0,
            'delivered': stats['delivered'] or 0
        }

    else:
        summary = {}

    conn.close()
    return render_template('profile.html', user=user_data, account_summary=summary)


# ---------- MAIN ----------
if __name__ == '__main__':
    app.run(debug=True)
















# # ---------------------------
# # Helper: Improved distance estimation
# # ---------------------------
# def estimate_distance(ngo_location, restaurant_location):
#     """Estimate distance between NGO and restaurant in km based on area similarity."""
#     if not ngo_location or not restaurant_location:
#         return None
#     ngo = ngo_location.strip().lower()
#     rest = restaurant_location.strip().lower()
#     if ngo in rest or rest in ngo:
#         return 1  # same or nearby area
#     return 5  # assume roughly 5 km apart

# def geocode(location_name):
#     """Return (lat, lon) for a given location string using Nominatim"""
#     if not location_name:
#         return None, None
#     url = f"https://nominatim.openstreetmap.org/search?q={location_name}&format=json&limit=1"
#     try:
#         response = requests.get(url, headers={"User-Agent": "Meal-Link-App"}).json()
#         if response:
#             return float(response[0]['lat']), float(response[0]['lon'])
#     except:
#         pass
#     return None, None

# def haversine(lat1, lon1, lat2, lon2):
#     """Calculate distance in km between two coordinates"""
#     R = 6371  # Earth radius in km
#     dlat = radians(lat2 - lat1)
#     dlon = radians(lon2 - lon1)
#     a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
#     c = 2*atan2(sqrt(a), sqrt(1-a))
#     return R * c

# def is_within_5km(ngo_loc, rest_loc):
#     """Return True if distance < 5 km"""
#     lat1, lon1 = geocode(ngo_loc)
#     lat2, lon2 = geocode(rest_loc)
#     if None in (lat1, lon1, lat2, lon2):
#         return True  # fallback to showing if geocoding fails
#     return haversine(lat1, lon1, lat2, lon2) <= 5