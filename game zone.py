import feedparser
from flask import Flask, render_template
from googletrans import Translator

app = Flask(__name__)
translator = Translator()

RSS_SOURCES = [
    {"name": "IGN Games", "url": "https://feeds.feedburner.com/ign/games-all"},
    {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/game-news/"},
    {"name": "PC Gamer", "url": "https://www.pcgamer.com/rss/"},
    {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed/news"},
    {"name": "Gematsu", "url": "https://www.gematsu.com/feed"}
]

def get_gaming_news():
    all_articles = []
    game_keywords = ['game', 'playstation', 'xbox', 'nintendo', 'steam', 'remake', 'rpg', 'fps', 'trailer']
    
    for source in RSS_SOURCES:
        feed = feedparser.parse(source["url"])
        count = 0
        for entry in feed.entries:
            if count >= 3: break 
            title_en = entry.title
            if any(word in title_en.lower() for word in game_keywords):
                try:
                    translated = translator.translate(title_en, src='en', dest='ar')
                    title_ar = translated.text
                except:
                    title_ar = title_en

                all_articles.append({
                    'title_ar': title_ar,
                    'title_en': title_en,
                    'link': entry.link,
                    'source': source["name"],
                    'date': entry.published[:16] if hasattr(entry, 'published') else "مؤخراً"
                })
                count += 1
    return all_articles

@app.route('/')
def index():
    news_data = get_gaming_news()
    return render_template('index.html', news=news_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
