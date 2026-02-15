# main.py - نسخة مطورة مع أمان وأداء أفضل

import feedparser
import re
import socket
import sqlite3
import requests
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, session, make_response
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from user_agents import parse
import bleach
import logging
from logging.handlers import RotatingFileHandler

# --- إعدادات السيرفر المحسنة ---
socket.setdefaulttimeout(15)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-super-secret-key-change-in-production-2024'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# --- إعداد التخزين المؤقت ---
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 دقائق
})

# --- إعداد تحديد المعدل (Rate Limiting) ---
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# --- إعداد التسجيل (Logging) ---
if not app.debug:
    handler = RotatingFileHandler('gamezone.log', maxBytes=10000, backupCount=3)
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)

# --- إعدادات قاعدة البيانات المحسنة ---
DB_PATH = 'radar.db'

def init_db():
    """تهيئة قاعدة البيانات مع تحسينات الأداء"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # جدول التفاعلات
    c.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            news_id TEXT PRIMARY KEY, 
            swords INTEGER DEFAULT 0, 
            shields INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول تتبع التصويتات (لمنع السبام)
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            news_id TEXT,
            vote_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, news_id)
        )
    ''')
    
    # جدول الكاش المحلي للصور
    c.execute('''
        CREATE TABLE IF NOT EXISTS image_cache (
            url TEXT PRIMARY KEY,
            image_url TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # إنشاء الفهارس لتحسين الأداء
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_votes_session ON user_votes(session_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_image_cache_fetched ON image_cache(fetched_at)')
    
    conn.commit()
    conn.close()

init_db()

# --- مصادر RSS محدثة ومصنفة ---
RSS_SOURCES = [
    # المواقع الرسمية الرئيسية
    {"name": "IGN", "url": "https://feeds.feedburner.com/ign/all", "type": "official", "priority": 1},
    {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/game-news/", "type": "official", "priority": 1},
    {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed/news", "type": "official", "priority": 1},
    {"name": "Kotaku", "url": "https://kotaku.com/rss", "type": "official", "priority": 1},
    
    # مصادر التسريبات
    {"name": "Gematsu", "url": "https://www.gematsu.com/feed", "type": "leak", "priority": 2},
    {"name": "VGC", "url": "https://www.videogameschronicle.com/feed/", "type": "leak", "priority": 2},
    {"name": "Insider Gaming", "url": "https://insider-gaming.com/feed/", "type": "leak", "priority": 2},
    
    # Reddit (مصدر تسريبات مهم)
    {"name": "Reddit Leaks", "url": "https://www.reddit.com/r/GamingLeaksAndRumors/new/.rss", "type": "leak", "priority": 2},
    
    # مصادر Twitter عبر Nitter (بديل لتويتر)
    {"name": "IGN", "url": "https://nitter.net/IGN/rss", "type": "official", "priority": 1},
    {"name": "GameSpot", "url": "https://nitter.net/GameSpot/rss", "type": "official", "priority": 1},
]

# --- دوال مساعدة للأمان ---

def generate_session_id():
    """توليد معرف جلسة فريد"""
    if 'user_id' not in session:
        session['user_id'] = hashlib.sha256(
            (str(time.time()) + str(request.remote_addr)).encode()
        ).hexdigest()[:16]
    return session['user_id']

def validate_input(text, max_length=200):
    """تنظيف المدخلات"""
    if not text:
        return ""
    cleaned = bleach.clean(text, tags=[], strip=True)
    return cleaned[:max_length]

def get_client_ip():
    """الحصول على IP العميل الحقيقي"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

# --- دالة محسنة لجلب الصور مع كاش ---

def fetch_main_image(url):
    """جلب الصورة الرئيسية للمقال مع كاش محلي"""
    try:
        # التحقق من الكاش أولاً
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT image_url FROM image_cache WHERE url = ? AND fetched_at > datetime('now', '-1 day')",
            (url,)
        )
        cached = c.fetchone()
        
        if cached:
            conn.close()
            return cached[0]
        
        # جلب الصورة
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # محاولة جلب الصورة بعدة طرق
        img_url = None
        
        # 1. Open Graph image
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get('content'):
            img_url = og_img['content']
        
        # 2. Twitter image
        if not img_url:
            twitter_img = soup.find("meta", {"name": "twitter:image"})
            if twitter_img and twitter_img.get('content'):
                img_url = twitter_img['content']
        
        # 3. أول صورة في المقال
        if not img_url:
            first_img = soup.find("article").find("img") if soup.find("article") else None
            if first_img and first_img.get('src'):
                img_url = first_img['src']
        
        if not img_url:
            img_url = "https://via.placeholder.com/500x280/0a0a0a/D4AF37?text=GAME+ZONE"
        
        # حفظ في الكاش
        c.execute(
            "INSERT OR REPLACE INTO image_cache (url, image_url) VALUES (?, ?)",
            (url, img_url)
        )
        conn.commit()
        conn.close()
        
        return img_url
        
    except Exception as e:
        app.logger.error(f"Image fetch error for {url}: {e}")
        return "https://via.placeholder.com/500x280/0a0a0a/D4AF37?text=GAME+ZONE"

# --- دالة محسنة لجلب الأخبار ---

@cache.memoize(timeout=300)  # 5 دقائق
def get_gaming_news(category=None):
    """جلب الأخبار مع تصنيف اختياري"""
    all_articles = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # ترتيب المصادر حسب الأولوية
    sources = sorted(RSS_SOURCES, key=lambda x: x['priority'])
    
    for source in sources:
        try:
            feed = feedparser.parse(source["url"], request_headers=headers)
            
            # التحقق من وجود feed.entries
            if not hasattr(feed, 'entries') or not feed.entries:
                continue
                
            for entry in feed.entries[:10]:  # تقليل العدد لتحسين الأداء
                # توليد ID فريد
                news_id = hashlib.md5(entry.link.encode()).hexdigest()
                
                # تنظيف العنوان
                title = validate_input(entry.title, 150)
                
                # جلب التفاعلات من قاعدة البيانات
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT swords, shields FROM reactions WHERE news_id=?", (news_id,))
                res = c.fetchone()
                swords, shields = res if res else (0, 0)
                conn.close()
                
                # جلب الصورة
                img_url = ""
                
                # محاولة جلب الصورة من RSS
                if hasattr(entry, 'media_content') and entry.media_content:
                    img_url = entry.media_content[0].get('url', '')
                elif hasattr(entry, 'links'):
                    for link in entry.links:
                        if link.get('type', '').startswith('image/'):
                            img_url = link.get('href', '')
                            break
                
                # إذا لم نجد صورة، نبحث في الوصف
                if not img_url and hasattr(entry, 'description'):
                    img_match = re.search(r'<img.+?src=["\'](.+?)["\']', entry.description)
                    if img_match:
                        img_url = img_match.group(1)
                
                # آخر خيار: جلب الصورة من المقال
                if not img_url:
                    img_url = fetch_main_image(entry.link)
                
                # تصنيف الخبر حسب المصدر
                news_category = source["type"]
                if "playstation" in title.lower() or "ps5" in title.lower() or "sony" in title.lower():
                    news_category = "ps"
                elif "xbox" in title.lower():
                    news_category = "xb"
                elif "pc" in title.lower() or "steam" in title.lower() or "nvidia" in title.lower():
                    news_category = "pc"
                elif "leak" in title.lower() or "rumor" in title.lower():
                    news_category = "leak"
                
                # فلترة حسب التصنيف المطلوب
                if category and category != "all" and news_category != category:
                    continue
                
                # حساب النتيجة (score)
                total_votes = swords + shields
                if total_votes > 0:
                    score = (swords * 2) - shields  # السيف له وزن أكبر
                else:
                    score = 0
                
                all_articles.append({
                    'id': news_id,
                    'title': title,
                    'link': entry.link,
                    'source': source["name"],
                    'image': img_url,
                    'type': news_category,
                    'swords': swords,
                    'shields': shields,
                    'score': score,
                    'published': entry.get('published', datetime.now().isoformat())
                })
                
        except Exception as e:
            app.logger.error(f"Error fetching {source['name']}: {e}")
            continue
    
    # ترتيب حسب الأحدث أولاً ثم حسب النتيجة
    all_articles.sort(key=lambda x: (x['published'], x['score']), reverse=True)
    
    return all_articles

# --- الصفحة الرئيسية المحسنة ---

@app.route('/')
@limiter.limit("30 per minute")  # تحديد المعدل
def index():
    # الحصول على معاملات البحث
    query = request.args.get('q', '').strip().lower()
    category = request.args.get('cat', 'all').strip().lower()
    
    # تنظيف المدخلات
    query = validate_input(query, 50)
    valid_categories = ['all', 'leak', 'ps', 'xb', 'pc']
    category = category if category in valid_categories else 'all'
    
    # جلب الأخبار
    all_news = get_gaming_news(category if category != 'all' else None)
    
    # فلترة حسب البحث
    display_news = all_news
    if query:
        display_news = [
            item for item in all_news 
            if query in item['title'].lower() or query in item['source'].lower()
        ]
    
    # إعداد ترويسات الأمان
    response = make_response(render_template(
        'index.html',
        news=display_news,
        ticker=all_news[:30],
        query=query,
        current_category=category
    ))
    
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    return response

# --- نقطة نهاية API للتصويت المحسنة ---

@app.route('/api/react', methods=['POST'])
@limiter.limit("10 per minute")  # تحديد معدل التصويت
def react():
    """التعامل مع التصويت بالسيف/الدرع"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid data"}), 400
        
        news_id = data.get("id")
        vote_type = data.get("type")
        
        # التحقق من صحة المدخلات
        if not news_id or vote_type not in ["sword", "shield"]:
            return jsonify({"error": "Invalid parameters"}), 400
        
        # تنظيف المدخلات
        news_id = validate_input(news_id, 64)
        
        # الحصول على معرف الجلسة
        session_id = generate_session_id()
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # التحقق من التصويت السابق
        c.execute(
            "SELECT vote_type FROM user_votes WHERE session_id = ? AND news_id = ?",
            (session_id, news_id)
        )
        existing = c.fetchone()
        
        if existing:
            # إذا كان التصويت السابق نفس النوع، نقوم بالإلغاء (undo)
            if existing[0] == vote_type:
                # إلغاء التصويت
                c.execute(
                    "DELETE FROM user_votes WHERE session_id = ? AND news_id = ?",
                    (session_id, news_id)
                )
                
                # تحديث العداد
                col = "swords" if vote_type == "sword" else "shields"
                c.execute(
                    f"UPDATE reactions SET {col} = {col} - 1 WHERE news_id = ?",
                    (news_id,)
                )
                
                conn.commit()
                conn.close()
                
                # حذف الكاش
                cache.delete_memoized(get_gaming_news)
                
                return jsonify({
                    "status": "success",
                    "action": "undo",
                    "message": "تم إلغاء التصويت"
                })
            else:
                # تغيير نوع التصويت
                # حذف القديم
                old_col = "swords" if existing[0] == "sword" else "shields"
                c.execute(
                    f"UPDATE reactions SET {old_col} = {old_col} - 1 WHERE news_id = ?",
                    (news_id,)
                )
                
                # تحديث النوع
                c.execute(
                    "UPDATE user_votes SET vote_type = ? WHERE session_id = ? AND news_id = ?",
                    (vote_type, session_id, news_id)
                )
                
                # إضافة الجديد
                new_col = "swords" if vote_type == "sword" else "shields"
                c.execute(
                    f"UPDATE reactions SET {new_col} = {new_col} + 1 WHERE news_id = ?",
                    (news_id,)
                )
        else:
            # تصويت جديد
            c.execute(
                "INSERT INTO user_votes (session_id, news_id, vote_type) VALUES (?, ?, ?)",
                (session_id, news_id, vote_type)
            )
            
            col = "swords" if vote_type == "sword" else "shields"
            c.execute(
                f"INSERT OR REPLACE INTO reactions (news_id, {col}) VALUES (?, COALESCE((SELECT {col} FROM reactions WHERE news_id = ?), 0) + 1)",
                (news_id, news_id)
            )
        
        conn.commit()
        conn.close()
        
        # حذف الكاش لتحديث التفاعلات
        cache.delete_memoized(get_gaming_news)
        
        return jsonify({
            "status": "success",
            "action": "vote",
            "message": "تم تسجيل التصويت"
        })
        
    except Exception as e:
        app.logger.error(f"Vote error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- نقطة نهاية لجلب التفاعلات الحالية ---

@app.route('/api/reactions/<news_id>')
@limiter.limit("60 per minute")
def get_reactions(news_id):
    """جلب التفاعلات لخبر معين"""
    try:
        news_id = validate_input(news_id, 64)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT swords, shields FROM reactions WHERE news_id=?", (news_id,))
        res = c.fetchone()
        conn.close()
        
        swords, shields = res if res else (0, 0)
        
        # التحقق من تصويت المستخدم الحالي
        session_id = generate_session_id()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT vote_type FROM user_votes WHERE session_id = ? AND news_id = ?",
            (session_id, news_id)
        )
        user_vote = c.fetchone()
        conn.close()
        
        return jsonify({
            "swords": swords,
            "shields": shields,
            "user_vote": user_vote[0] if user_vote else None
        })
        
    except Exception as e:
        app.logger.error(f"Reactions fetch error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- نقطة نهاية لتحديث الأخبار يدوياً (للمسؤولين) ---

@app.route('/admin/refresh-cache', methods=['POST'])
def refresh_cache():
    """تحديث الكاش يدوياً (محمي بكلمة سر)"""
    auth_key = request.headers.get('X-Admin-Key')
    if not auth_key or not hmac.compare_digest(auth_key, app.config.get('ADMIN_KEY', 'admin-key')):
        return jsonify({"error": "Unauthorized"}), 401
    
    cache.delete_memoized(get_gaming_news)
    return jsonify({"status": "success", "message": "Cache cleared"})

# --- معالج الأخطاء ---

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(429)
def rate_limit_exceeded(error):
    return jsonify({"error": "Too many requests. Please try again later."}), 429

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
