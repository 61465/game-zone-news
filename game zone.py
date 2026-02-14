import feedparser
from flask import Flask, render_template
from googletrans import Translator
import threading

app = Flask(__name__)
translator = Translator()

# مصادر الأخبار (يمكنك إضافة المزيد هنا)
RSS_SOURCES = [
    {"name": "IGN", "url": "https://feeds.feedburner.com/ign/all"},
    {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/game-news/"}
]

def get_gaming_news():
    all_articles = []
    for source in RSS_SOURCES:
        feed = feedparser.parse(source["url"])
        # نأخذ أول 4 أخبار من كل مصدر لضمان سرعة التحميل
        for entry in feed.entries[:4]:
            try:
                # ترجمة العنوان فقط لتوفير الوقت
                translated = translator.translate(entry.title, src='en', dest='ar')
                title_ar = translated.text
            except Exception:
                title_ar = entry.title # في حال فشل الترجمة يظهر النص الأصلي

            all_articles.append({
                'title': title_ar,
                'link': entry.link,
                'source': source["name"],
                'date': entry.published[:16] # قص التاريخ ليكون شكله أرتب
            })
    return all_articles

@app.route('/')
def index():
    news_data = get_gaming_news()
    return render_template('index.html', news=news_data)

if __name__ == '__main__':
    app.run(debug=True)
