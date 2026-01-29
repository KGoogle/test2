import json
import os
import requests
import feedparser
import re
import sqlite3
from typing import List, Dict
from datetime import datetime
import time

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    print("google-generativeai 라이브러리가 없습니다.")

NASA_API_KEY = os.environ.get('NASA_API_KEY')
SPRINGER_API_KEY = os.environ.get("SPRINGER_API_KEY")

GOOGLE_API_KEY = None      # os.environ.get("GOOGLE_API_KEY") 
TRANSLATE_API_KEY = None   # os.environ.get("TRANSLATE_API_KEY")
PAPER_API_KEY = None       # os.environ.get("PAPER_API_KEY")

MODEL_NAME = 'gemini-2.5-flash-lite' 

classify_model = None
translate_model = None
paper_model = None

if HAS_GENAI and GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    classify_model = genai.GenerativeModel(MODEL_NAME)
else:
    print("ℹ️ 알림: GOOGLE_API_KEY가 설정되지 않아 AI 분류를 건너뜁니다.")

if HAS_GENAI and TRANSLATE_API_KEY:
    translate_model = genai.GenerativeModel(MODEL_NAME)
else:
    print("ℹ️ 알림: TRANSLATE_API_KEY가 설정되지 않아 AI 번역을 건너뜁니다.")

if HAS_GENAI and PAPER_API_KEY:
    paper_model = genai.GenerativeModel(MODEL_NAME)
else:
    print("ℹ️ 알림: PAPER_API_KEY가 설정되지 않아 논문 번역을 건너뜁니다.")

def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

SCIENCE_FIELDS = ["천문·우주", "인지·신경", "물리학", "생명과학", "기타"]
DB_FILE = "science_data.db"

RSS_SOURCES = [
    {"url": "https://www.sciencedaily.com/rss/top.xml", "fixed_category": None},
    {"url": "https://phys.org/rss-feed/breaking/", "fixed_category": None},
    {"url": "https://www.space.com/feeds/articletype/news", "fixed_category": "천문·우주"},
    {"url": "https://www.scientificamerican.com/platform/syndication/rss/", "fixed_category": None},
    {"url": "https://www.quantamagazine.org/feed/", "fixed_category": None}
]

YOUTUBE_SOURCES = [
    {"type": "channel", "id": "UCsXVk37bltHxD1rDPwtNM8Q"}, 
    {"type": "channel", "id": "UCHnyfMqiRRG1u-2MsSQLbXA"}, 
    {"type": "channel", "id": "Csooa4yRKGN_zEE8iknghZA"}, 
    {"type": "playlist", "id": "PLYeXRzoBwGeHVguBktW327fxb1tKqLXrR"}, 
    {"type": "playlist", "id": "PLkKcqR2KGxgzqeKZo1Rx93kJFokuVkpye"}, 
    {"type": "channel", "id": "UCMc4EmuDxnHPc6pgGW-QWvQ"}, 
    {"type": "channel", "id": "UCrBpV_pG2kyMMEHCMTNzjAQ"}, 
    {"type": "channel", "id": "UCIk1-yPCTnFuzfgu4gyfWqw"}  
]

def call_gemini_with_retry(model, prompt, api_key, retries=2):
    if not api_key or not model:
        return None
    genai.configure(api_key=api_key)
    for attempt in range(retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                time.sleep(5) 
            else:
                return None
    return None

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    link TEXT,
                    thumbnail TEXT,
                    pub_date TEXT,
                    category TEXT,
                    source TEXT
                )''')
    conn.commit()
    conn.close()

def is_video_exists(video_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def save_video_to_db(video_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    category = video_data['tags'][0]
    c.execute('''INSERT OR REPLACE INTO videos (id, title, link, thumbnail, pub_date, category, source)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (video_data['id'], video_data['title'], video_data['link'], 
               video_data['thumbnail'], video_data['date'], category, video_data['source']))
    conn.commit()
    conn.close()

def get_latest_videos(category=None, limit=8):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if category:
        query = "SELECT title, link, thumbnail, pub_date, source FROM videos WHERE category LIKE ? ORDER BY pub_date DESC LIMIT ?"
        c.execute(query, (f'%{category}%', limit))
    else:
        c.execute("SELECT title, link, thumbnail, pub_date, source FROM videos ORDER BY pub_date DESC LIMIT ?", (limit,))
        
    rows = c.fetchall()
    conn.close()
    return [{"title": r[0], "link": r[1], "thumbnail": r[2], "date": r[3], "source": r[4]} for r in rows]

def translate_content(text_list: List[str]) -> List[str]:
    if not translate_model or not text_list or not TRANSLATE_API_KEY:
        return text_list
    prompt = f""" 
    당신은 전문 과학 번역가입니다. 아래 텍스트 리스트를 자연스럽고 학술적인 한국어로 번역하세요.
    - JSON 배열 형식으로만 응답: ["번역1", "번역2", ...]
    [텍스트 리스트]
    {json.dumps(text_list, ensure_ascii=False)}
    """
    response = call_gemini_with_retry(translate_model, prompt, TRANSLATE_API_KEY)
    if response:
        try:
            match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
    return text_list

def classify_data_batch(items: List[Dict]) -> List[Dict]:
    if not items or not classify_model or not GOOGLE_API_KEY:
        return []
    context = ""
    for i, item in enumerate(items):
        desc = item.get('desc', '')
        context += f"ID: {i}\n제목: {item['title']}\n내용: {desc[:150]}\n---\n"
    prompt = f"""
    당신은 과학 전문 큐레이터입니다. 아래 콘텐츠를 분석하여 [카테고리 후보] 중 관련된 것을 모두 선택해 태그를 다세요.
    
    [중요 규칙]
    1. 내용은 여러 분야에 걸쳐 있을 수 있으므로 관련된 카테고리는 모두 나열하세요.
    2. 단, **가장 핵심이 되는(가장 관련도가 높은) 카테고리를 반드시 배열의 첫 번째**에 두세요. 이 첫 번째 태그가 분류 기준이 됩니다.
    3. JSON 리스트 형식으로만 응답하세요.
    
    [카테고리 후보] {', '.join(SCIENCE_FIELDS)}
    
    [응답 예시]
    [ {{"id": 0, "tags": ["가장관련된분야", "부차적분야"]}} ]
    
    [데이터]
    {context}
    """
    response = call_gemini_with_retry(classify_model, prompt, GOOGLE_API_KEY)
    if response:
        try:
            match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if match:
                results = json.loads(match.group())
                result_map = {res['id']: res.get('tags', []) for res in results}
                for i, item in enumerate(items):
                    item['tags'] = result_map.get(i, [])
                return items
        except Exception:
            pass
    return []

def get_nasa_data():
    url = f"https://api.nasa.gov/planetary/apod?api_key={NASA_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            translated = translate_content([data.get('title', ''), data.get('explanation', '')])
            data['title'] = translated[0]
            data['explanation'] = translated[1]
            return data
    except Exception:
        pass
    return None

def fetch_rss_news() -> List[Dict]:
    all_news = []
    print("RSS 뉴스 피드 읽는 중...")
    for source_info in RSS_SOURCES:
        try:
            feed = feedparser.parse(source_info["url"])
            if "sciencedaily" in source_info["url"]: source_name = "ScienceDaily"
            elif "space.com" in source_info["url"]: source_name = "Space.com"
            elif "phys.org" in source_info["url"]: source_name = "Phys.org"
            elif "scientificamerican" in source_info["url"]: source_name = "Scientific American"
            elif "quantamagazine" in source_info["url"]: source_name = "Quanta Magazine"
            else: source_name = "Science News"
            
            for entry in feed.entries[:5]:
                all_news.append({
                    "title": entry.title,
                    "desc": entry.get('summary', entry.get('description', '내용 없음')),
                    "link": entry.link,
                    "date": entry.get('published', datetime.now().strftime("%Y-%m-%d")),
                    "source": source_name,
                    "fixed_category": source_info["fixed_category"]
                })
        except Exception:
            continue
    return all_news

def fetch_springer_papers(subject_query) -> List[Dict]:

    if not SPRINGER_API_KEY:
        print("ℹ️ 알림: SPRINGER_API_KEY가 설정되지 않아 논문 수집을 건너뜁니다.")
        return []

    print("Springer API로 논문 검색 중...")
    
    base_url = "http://api.springernature.com/meta/v1/json"
    
    query = (
        f'subject:"{subject_query}" '
        f'AND type:Journal '
        f'AND (journal:"Nature" OR journal:"Nature {subject_query}") '
        f'sort:date'
    )
    
    params = {
        "q": query, 
        "p": 5,
        "s": 1,
        "api_key": SPRINGER_API_KEY
    }

    papers = []
    try:
        response = requests.get(base_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            records = data.get('records', [])
            
            for record in records:
                title = record.get('title', '제목 없음')
                
                cleaned_desc = "" 

                link = ""
                urls = record.get('url', [])
                for u in urls:
                    if u.get('format') == 'html':
                        link = u.get('value')
                        break
                if not link and urls:
                    link = urls[0].get('value')

                pub_date = record.get('publicationDate', datetime.now().strftime("%Y-%m-%d"))
                source = record.get('publicationName', 'Springer Nature')

                papers.append({
                    "title": title,
                    "desc": cleaned_desc,
                    "link": link,
                    "date": pub_date,
                    "source": source
                })
        else:
            print(f"Springer API Error ({subject_query}): {response.status_code}")
            print(f"Error Message: {response.text}")

    except Exception as e:
        print(f"Error fetching papers for {subject_query}: {e}")

    return papers

def fetch_and_process_videos():
    print("유튜브 영상 확인 중...")
    new_videos = []
    
    for source in YOUTUBE_SOURCES:
        try:
            url = f"https://www.youtube.com/feeds/videos.xml?{'playlist_id' if source.get('type')=='playlist' else 'channel_id'}={source['id']}"
            feed = feedparser.parse(url)
            if not feed.entries: continue
            
            for entry in feed.entries[:]:
                video_id = entry.yt_videoid
                
                if is_video_exists(video_id): continue 
                
                thumbnail = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                if 'media_thumbnail' in entry: thumbnail = entry.media_thumbnail[0]['url']
                
                new_videos.append({
                    "id": video_id,
                    "title": entry.title,
                    "link": entry.link,
                    "desc": entry.get('summary', ''),
                    "thumbnail": thumbnail,
                    "date": entry.published,
                    "source": entry.get('author', 'Unknown'),
                    "tags": [] 
                })
        except Exception:
            continue
    
    MAX_PROCESS_LIMIT = 10
    if new_videos:
        print(f"새로운 영상 총 {len(new_videos)}개 발견.")
        videos_to_process = new_videos[:MAX_PROCESS_LIMIT]
        
        titles = [v['title'] for v in videos_to_process]
        translated_titles = translate_content(titles)
        for i, v in enumerate(videos_to_process):
            if i < len(translated_titles): v['title'] = translated_titles[i]
        classified_videos = classify_data_batch(videos_to_process)
        
        saved_count = 0
        for video in classified_videos:
            if video.get('tags') and len(video['tags']) > 0:
                save_video_to_db(video)
                saved_count += 1
        if not GOOGLE_API_KEY:
            print("ℹ️ 알림: API 키가 없어 분류 및 저장을 스킵했습니다.")
        else:
            print(f"영상 처리 완료: {saved_count}개 DB 저장됨.")
    else:
        print("새로운 영상이 없습니다.")

def collect_and_process_data():
    init_db()
    fetch_and_process_videos()
    
    raw_news = fetch_rss_news()
    print(f"뉴스 {len(raw_news)}개 처리 중...")
    
    texts = [item['title'] for item in raw_news]
    translated_all = []
    for i in range(0, len(texts), 20):
        translated_all.extend(translate_content(texts[i:i+20]))
    for i, item in enumerate(raw_news):
        if i < len(translated_all): item['title'] = translated_all[i]
   
    to_classify = [i for i in raw_news if not i["fixed_category"]]
    if to_classify:
        for i in range(0, len(to_classify), 5):
            batch = to_classify[i:i+5]
            classify_data_batch(batch)
   
    all_data = {field: {"news": [], "videos": [], "papers": [], "data": []} for field in SCIENCE_FIELDS}
    
    for item in raw_news:
        tags = item.get('tags')
        category_candidate = item.get('fixed_category')
        
        if not category_candidate:
            if tags and len(tags) > 0:
                category_candidate = tags[0]
            else:
                category_candidate = "기타"
        
        matched = "기타"
        for field in SCIENCE_FIELDS:
            if category_candidate in field or field in category_candidate:
                matched = field
                break
        all_data[matched]["news"].append(item)
        
    for field in SCIENCE_FIELDS:
        all_data[field]["videos"] = get_latest_videos(category=field, limit=5)
    
    print("논문 데이터 처리 및 번역 중...")
    
    field_map = {
        "천문·우주": "Astronomy",
        "인지·신경": "Neuroscience",
        "물리학": "Physics",
        "생명과학": "Biology",
        "기타": "Science"
    }

    for field_kr, field_en in field_map.items():
        field_papers = fetch_springer_papers(field_en)
        
        if field_papers:
            if HAS_GENAI and PAPER_API_KEY and paper_model:
                paper_titles = [p['title'] for p in field_papers]
                
                prompt = f""" 
                당신은 전문 과학 번역가입니다. 아래 논문 제목들을 자연스럽고 학술적인 한국어로 번역하세요.
                JSON 배열 형식으로만 응답: ["번역1", "번역2", ...]
                {json.dumps(paper_titles, ensure_ascii=False)}
                """
                response = call_gemini_with_retry(paper_model, prompt, PAPER_API_KEY)
                
                if response:
                    try:
                        match = re.search(r'\[.*\]', response.text, re.DOTALL)
                        if match:
                            translated_titles = json.loads(match.group())
                            for i, p in enumerate(field_papers):
                                if i < len(translated_titles):
                                    p['title'] = translated_titles[i]
                    except Exception:
                        print(f"{field_kr} 논문 번역 파싱 실패, 원문 유지")

            all_data[field_kr]["papers"] = field_papers

    static_papers = [
        {"title": "네이처", "desc": "임시", "link": "https://www.nature.com/", "source": "Nature"},
        {"title": "네이처 천문학", "desc": "임시", "link": "https://www.nature.com/natastron/", "source": "Nature"},
        {"title": "사이언스", "desc": "임시", "link": "https://www.science.org/topic/category/astronomy", "source": "Science"},
        {"title": "왕립학회", "desc": "임시", "link": "https://royalsociety.org/", "source": "Royal Society"}
    ]
    all_data["천문·우주"]["papers"].extend(static_papers)

    all_data["천문·우주"]["data"] = [
        {"title": "NASA", "desc": "미 항공우주국", "link": "https://www.nasa.gov/", "source": "NASA"},
        {"title": "ESA", "desc": "유럽 우주국", "link": "https://www.esa.int/", "source": "ESA"},
    ]
    
    return all_data

def generate_html(science_data, nasa_data):
    full_payload = json.dumps({"science": science_data, "nasa": nasa_data}, ensure_ascii=False)
    field_buttons_html = "".join([f'<button class="tab-btn" onclick="window.showField(\'{f}\')">{f}</button>' for f in SCIENCE_FIELDS])
    universe_quote = """
    "삶에 별빛을 섞으세요. <br>하찮은 일에 마음이 괴롭지 않을 겁니다." <br>
    <span style="font-size:14px; margin-top:15px; display:block; opacity: 0.8;">- 마리아 미첼 -</span>
    """

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>과학 정보</title>
    <link href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {{ 
            --bg: #000000; 
            --card-bg: #0a0a0a; 
            --text-main: #ffffff;
            --text-sub: #aaaaaa; 
            --accent: #ffffff; 
            --border: #222222;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            background-color: var(--bg); color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0; padding: 0; line-height: 1.6;
            overflow-x: hidden;
        }}
        
        header {{ 
            position: relative; text-align: center; padding: 80px 20px;
            overflow: hidden; background: #000; height: 350px; display: flex; align-items: center; justify-content: center;
        }}
        
        #universe {{ 
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; 
            z-index: 0; display: block; 
        }}
        
        #brain-container {{
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; 
            z-index: 0; display: none;
            background-color: #000000; 
        }}

        .header-content {{ position: relative; z-index: 1; pointer-events: none; transition: opacity 0.5s; }}
        
        header h1 {{
            margin: 0; font-size: 19px; color: #ffffff;
            font-family: 'Gowun Batang', serif;
            text-shadow: 0 0 10px rgba(0,0,0,0.8);
            word-break: keep-all; line-height: 1.8; font-weight: 400;
        }}
        
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; min-height: 100vh; position: relative; z-index: 1; }}
    
        .tabs-field {{ 
            display: flex; gap: 10px; margin: 0 auto 20px auto; 
            border-bottom: 1px solid var(--border); padding: 0 15px 15px 15px; 
            overflow-x: auto; justify-content: flex-start;
            scrollbar-width: none; -webkit-overflow-scrolling: touch;
        }}
        .tabs-field::-webkit-scrollbar {{ display: none; }}
        @media (min-width: 600px) {{ .tabs-field {{ justify-content: center; }} }}

        .tab-btn {{ 
            background: transparent; border: 1px solid var(--border); color: var(--text-sub); 
            padding: 10px 24px; cursor: pointer; border-radius: 4px; font-weight: 500; 
            font-size: 0.95rem; transition: all 0.3s; white-space: nowrap; flex-shrink: 0;
        }}
        .tab-btn.active {{ background: #ffffff; color: #000000; border-color: #ffffff; font-weight: bold; }}
        .tab-btn:hover {{ border-color: #666; color: #fff; }}
        .tab-btn.active:hover {{ color: #000000; border-color: #ffffff; }}
        
        .sub-tabs {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
        .sub-btn {{ background: none; border: none; color: #666; cursor: pointer; font-size: 0.9rem; font-weight: bold; padding: 5px 0; border-bottom: 2px solid transparent; transition: 0.3s; }}
        .sub-btn:hover {{ color: #aaa; }}
        .sub-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}

        .nasa-hero {{ margin-bottom: 40px; border-radius: 4px; overflow: hidden; background: #000000; border: 1px solid var(--border); animation: fadeIn 1s; }}
        .nasa-img {{ width: 100%; height: auto; max-height: 750px; object-fit: contain; display: block; margin: 0 auto; background: #000; }}
        .nasa-info {{ padding: 40px; border-top: 1px solid var(--border); }}
        .nasa-header-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; flex-wrap: wrap; gap: 15px; }}
        .nasa-tag {{ background: #fff; color: #000; padding: 5px 12px; border-radius: 2px; font-size: 0.75rem; font-weight: 800; }}
        .nasa-actions {{ display: flex; gap: 8px; }}
        .btn-mini {{ border: 1px solid #444; color: #888; padding: 4px 12px; text-decoration: none; font-size: 0.7rem; border-radius: 2px; transition: 0.3s; }}
        .btn-mini:hover {{ border-color: #fff; color: #fff; }}
        .nasa-title {{ font-size: 1.8rem; font-weight: bold; margin-bottom: 15px; font-family: 'Gowun Batang', serif; color: #fff; }}
        .nasa-desc {{ color: #bbbbbb; font-size: 1rem; line-height: 1.8; text-align: justify; letter-spacing: -0.01em; }}
        .nasa-credit {{ font-size: 0.85rem; color: #666; margin-top: 20px; padding-top: 20px; border-top: 1px solid #222; }}

        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; animation: fadeIn 0.4s; }}
        .card {{ background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 4px; padding: 25px; transition: all 0.3s; display: flex; flex-direction: column; text-decoration: none; color: inherit; position: relative; overflow: hidden; cursor: pointer; }}
        .card:hover {{ border-color: #ffffff; background-color: #111111; transform: translateY(-3px); }}
        .source-tag {{ font-size: 10px; background: #fff; color: #000; padding: 2px 6px; border-radius: 2px; position: absolute; top: 15px; right: 15px; font-weight: bold; z-index: 2; }}
        .ai-tag {{ font-size: 10px; color: #888; border: 1px solid #333; padding: 2px 8px; border-radius: 12px; display: inline-block; margin-bottom: 12px; align-self: flex-start; }}
        .card-title {{ font-size: 1.1rem; font-weight: 600; color: #ffffff; margin-bottom: 12px; line-height: 1.4; padding-right: 10px; }}
        .card-desc {{ font-size: 0.9rem; color: #888; margin-bottom: 15px; word-break: keep-all; line-height: 1.6; }}
        .card-meta {{ font-size: 0.8rem; color: #666; margin-top: auto; letter-spacing: 0.05em; }}
        
        .video-card .thumb-wrapper {{ width: 100%; padding-top: 56.25%; position: relative; margin: -25px -25px 15px -25px; background: #222; }}
        .video-card .thumb-img {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.8; transition: 0.3s; }}
        .video-card:hover .thumb-img {{ opacity: 1; transform: scale(1.05); }}
        .video-card .play-icon {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 40px; height: 40px; background: rgba(0,0,0,0.6); border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 2px solid #fff; }}
        .video-card .play-icon::after {{ content:''; display: block; width: 0; height: 0; border-top: 8px solid transparent; border-bottom: 8px solid transparent; border-left: 14px solid #fff; margin-left: 4px; }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    </style>
    
    <script type="importmap">
        {{ "imports": {{ "three": "https://unpkg.com/three@0.160.0/build/three.module.js", "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/" }} }}
    </script>
</head>
<body>
    <header id="header-container">
        <canvas id="universe"></canvas>
        <div id="universe-content" class="header-content">
            <h1>{universe_quote}</h1>
        </div>

        <div id="brain-container"></div>
    </header>

    <div class="container">
        <nav class="tabs-field">{field_buttons_html}</nav>
        <nav id="sub-tabs-container" class="sub-tabs"></nav>
        <main id="main-content"></main>
    </div>

    <script type="module">
        import * as THREE from 'three';
        import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
        import {{ EffectComposer }} from 'three/addons/postprocessing/EffectComposer.js';
        import {{ RenderPass }} from 'three/addons/postprocessing/RenderPass.js';
        import {{ UnrealBloomPass }} from 'three/addons/postprocessing/UnrealBloomPass.js';

        const fullData = {full_payload};
        let currentField = "천문·우주";
        let currentType = "apod";

        const universeCanvas = document.getElementById('universe');
        const universeCtx = universeCanvas.getContext('2d');
        const headerContainer = document.getElementById('header-container');
        
        let universeW, universeH, universeDpr = Math.max(1, window.devicePixelRatio || 1);
        let stars = [];
        let animationIdUniverse = null;
        let lastWidth = window.innerWidth;

        function initUniverse() {{
            resizeUniverse(true);
            window.addEventListener('resize', () => resizeUniverse(false));
            animateUniverse();
        }}

        function resizeUniverse(force) {{
            if (!force && window.innerWidth === lastWidth) {{
                return;
            }}
            lastWidth = window.innerWidth;

            const currentWidth = headerContainer.offsetWidth;
            const currentHeight = headerContainer.offsetHeight;
            
            universeW = currentWidth; 
            universeH = currentHeight;
            
            universeCanvas.width = universeW * universeDpr; 
            universeCanvas.height = universeH * universeDpr;
            universeCtx.setTransform(universeDpr, 0, 0, universeDpr, 0, 0);
            
            createStars(Math.round((universeW * universeH) / 1000)); 
        }}

        function createStars(count) {{
            stars = [];
            for (let i = 0; i < count; i++) {{
                const colorRand = Math.random();
                let color;
                if (colorRand < 0.7) {{
                    color = '#ffffff';
                }} else if (colorRand < 0.82) {{
                    color = '#aabfff';
                }} else if (colorRand < 0.94) {{
                    color = '#ffd2a1';
                }} else {{
                    color = '#ffcc6f';
                }}

                stars.push({{ 
                    x: Math.random() * universeW, 
                    y: Math.random() * universeH, 
                    r: Math.pow(Math.random(), 3) * 1.8 + 0.2, 
                    tw: Math.random() * Math.PI * 2, 
                    twSpeed: Math.random() * 0.01 + 0.005, 
                    c: color 
                }});
            }}
        }}

        function animateUniverse() {{
            universeCtx.clearRect(0, 0, universeW, universeH);
            
            stars.forEach(s => {{
                s.tw += s.twSpeed; 
                
                const baseAlpha = (s.r / 2.0) * 0.7 + 0.3;
                const twinkleAlpha = 0.5 + Math.sin(s.tw) * 0.5;
                universeCtx.globalAlpha = baseAlpha * twinkleAlpha;
                
                const gradient = universeCtx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r);
                const starColorHex = s.c.substring(1);
                const r = parseInt(starColorHex.slice(0, 2), 16);
                const g = parseInt(starColorHex.slice(2, 4), 16);
                const b = parseInt(starColorHex.slice(4, 6), 16);
                
                gradient.addColorStop(0, `rgba(${{r}}, ${{g}}, ${{b}}, 1)`);
                gradient.addColorStop(0.5, `rgba(${{r}}, ${{g}}, ${{b}}, 0.5)`);
                gradient.addColorStop(1, `rgba(${{r}}, ${{g}}, ${{b}}, 0)`);
                
                universeCtx.fillStyle = gradient;
                universeCtx.beginPath(); 
                universeCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2); 
                universeCtx.fill();
            }});
            
            universeCtx.globalAlpha = 1;
            animationIdUniverse = requestAnimationFrame(animateUniverse);
        }}

        let brainScene, brainCamera, brainRenderer, brainControls, brainGroup, brainComposer, brainInitialized = false;
        let animationIdBrain;

        async function initBrain() {{
            if (brainInitialized) return;
            brainInitialized = true;

            const container = document.getElementById('brain-container');
            const width = container.clientWidth;
            const height = container.clientHeight;

            brainScene = new THREE.Scene();
            brainScene.background = new THREE.Color(0x000000);
            brainScene.fog = new THREE.FogExp2(0x000000, 0.007);

            brainCamera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
            brainCamera.position.set(0, 0, 200);

            brainRenderer = new THREE.WebGLRenderer({{ antialias: true }});
            brainRenderer.setSize(width, height);
            brainRenderer.setPixelRatio(window.devicePixelRatio);
            container.appendChild(brainRenderer.domElement);

            brainControls = new OrbitControls(brainCamera, brainRenderer.domElement);
            brainControls.enableDamping = true;
            brainControls.dampingFactor = 0.05;
            brainControls.minDistance = 50;
            brainControls.maxDistance = 300;
            brainControls.enablePan = false;

            const renderScene = new RenderPass(brainScene, brainCamera);
            
            const bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), 1.5, 0.4, 0.85);
            bloomPass.threshold = 0;
            bloomPass.strength = 0.5;
            bloomPass.radius = 0.1;

            brainComposer = new EffectComposer(brainRenderer);
            brainComposer.addPass(renderScene);
            brainComposer.addPass(bloomPass);

            try {{
                const response = await fetch('brain.json');
                const data = await response.json();
                createDigitalBrain(data);
            }} catch (e) {{ console.error("Brain load fail", e); }}

            window.addEventListener('resize', onBrainResize);
            animateBrain();
        }}

        function createCircleTexture() {{
            const canvas = document.createElement('canvas');
            canvas.width = 128;
            canvas.height = 128;
            const context = canvas.getContext('2d');
            const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
            gradient.addColorStop(0, 'rgba(255,255,255,1)');
            gradient.addColorStop(0.2, 'rgba(200,200,255,0.8)');
            gradient.addColorStop(0.8, 'rgba(150,150,255,0.1)');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');
            context.fillStyle = gradient;
            context.fillRect(0, 0, 128, 128);
            return new THREE.CanvasTexture(canvas);
        }}

        function createDigitalBrain(data) {{
            const tempGeo = new THREE.BufferGeometry();
            tempGeo.setAttribute('position', new THREE.Float32BufferAttribute(data.vertices.flat(), 3));
            tempGeo.computeBoundingBox();
            const center = new THREE.Vector3();
            tempGeo.boundingBox.getCenter(center);
            
            for (let i = 0; i < data.vertices.length; i++) {{
                const vec = new THREE.Vector3().fromArray(data.vertices[i]);
                vec.sub(center);
                data.vertices[i] = vec.toArray();
            }}

            const CEREBELLUM_ID = 6;
            const CEREBELLUM_SCALE = 0.85;
            const cerebellumCenter = new THREE.Vector3();
            let cerebellumVertexCount = 0;
            const cerebellumIndices = [];

            if (data.types) {{
                for (let i = 0; i < data.types.length; i++) {{
                    if (data.types[i] === CEREBELLUM_ID) {{
                        const vertex = data.vertices[i];
                        cerebellumCenter.add(new THREE.Vector3(vertex[0], vertex[1], vertex[2]));
                        cerebellumVertexCount++;
                        cerebellumIndices.push(i);
                    }}
                }}
                if (cerebellumVertexCount > 0) {{
                    cerebellumCenter.divideScalar(cerebellumVertexCount);
                    for (const i of cerebellumIndices) {{
                        const vertexVec = new THREE.Vector3().fromArray(data.vertices[i]);
                        const newPosition = vertexVec.sub(cerebellumCenter).multiplyScalar(CEREBELLUM_SCALE).add(cerebellumCenter);
                        data.vertices[i] = newPosition.toArray();
                    }}
                }}
            }}

            brainGroup = new THREE.Group();
            brainScene.add(brainGroup);

            const vertices = new Float32Array(data.vertices.flat());
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
            
            const numVertices = vertices.length / 3;
            const colors = new Float32Array(numVertices * 3);
            const baseColor = new THREE.Color().setHSL(0.6, 0.9, 0.6); 
            for(let i=0; i<numVertices; i++) {{ colors.set([baseColor.r, baseColor.g, baseColor.b], i*3); }}
            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

            const material = new THREE.PointsMaterial({{
                size: 0.8,
                sizeAttenuation: true, 
                map: createCircleTexture(),
                vertexColors: true, 
                transparent: true,
                blending: THREE.AdditiveBlending, 
                depthWrite: false
            }});

            const points = new THREE.Points(geometry, material);
            brainGroup.add(points);
            
            const lineGeo = new THREE.BufferGeometry();
            lineGeo.setAttribute('position', geometry.getAttribute('position'));
            lineGeo.setIndex(data.faces.flat());
            
            const lineMaterial = new THREE.LineBasicMaterial({{ 
                color: 0x99bbff, 
                transparent: true, 
                opacity: 0.15, 
                blending: THREE.AdditiveBlending, 
                depthWrite: false 
            }});
            
            const wireframe = new THREE.WireframeGeometry(lineGeo);
            const lines = new THREE.LineSegments(wireframe, lineMaterial);
            brainGroup.add(lines);

            brainGroup.rotation.x = -Math.PI / 2;
            brainGroup.rotation.z = Math.PI / 2;
        }}

        function onBrainResize() {{
            if (!brainContainer.style.display === 'none') return;
            const container = document.getElementById('brain-container');
            const w = container.clientWidth; const h = container.clientHeight;
            brainCamera.aspect = w / h; brainCamera.updateProjectionMatrix();
            brainRenderer.setSize(w, h); brainComposer.setSize(w, h);
        }}

        function animateBrain() {{
            animationIdBrain = requestAnimationFrame(animateBrain);
            if(brainControls) brainControls.update();
            if(brainGroup) brainGroup.rotation.z += 0.005;
            if(brainComposer) brainComposer.render();
        }}

        const brainContainer = document.getElementById('brain-container');
        const universeContainer = document.getElementById('universe');
        const universeContent = document.getElementById('universe-content');

        window.showField = function(f) {{
            currentField = f;
            currentType = (f === "천문·우주") ? "apod" : "news";
            
            if (animationIdUniverse) cancelAnimationFrame(animationIdUniverse);
            if (animationIdBrain) cancelAnimationFrame(animationIdBrain);
            universeContainer.style.display = 'none';
            brainContainer.style.display = 'none';

            if (f === "천문·우주") {{
                universeContainer.style.display = 'block';
                universeContent.style.display = 'block';
                animateUniverse();
            
            }} else if (f === "인지·신경") {{
                universeContent.style.display = 'none';
                brainContainer.style.display = 'block';
                if (!brainInitialized) {{
                    initBrain();
                }} else {{
                    onBrainResize();
                    animateBrain(); 
                }}

            }} else {{
                universeContent.style.display = 'none';
            }}

            document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.innerText === f));
            
            renderSubTabs();
            render();
        }};

        window.showType = function(t) {{
            currentType = t;
            renderSubTabs();
            render();
        }};

        function renderSubTabs() {{
            const container = document.getElementById('sub-tabs-container');
            let tabs = [];
            if (currentField === "천문·우주") tabs.push({{ id: 'apod', name: '오늘의 천문 사진' }});
            
            tabs.push(
                {{ id: 'news', name: '뉴스' }}, 
                {{ id: 'videos', name: '콘텐츠' }}, 
                {{ id: 'papers', name: '논문' }},
                {{ id: 'data', name: '데이터' }}
            );

            container.innerHTML = tabs.map(t => `
                <button class="sub-btn ${{currentType === t.id ? 'active' : ''}}" onclick="window.showType('${{t.id}}')">${{t.name}}</button>
            `).join('');
        }}

        function render() {{
            const science = fullData.science[currentField] || {{ news: [], videos: [], papers: [], data: [] }};
            const nasa = fullData.nasa;
            const container = document.getElementById('main-content');
            let html = '';

            if (currentType === 'apod') {{
                if (nasa) {{
                    html += `
                    <div class="nasa-hero">
                        <img src="${{nasa.url}}" class="nasa-img" alt="NASA APOD">
                        <div class="nasa-info">
                            <div class="nasa-header-row">
                                <span class="nasa-tag">NASA APOD TODAY</span>
                                <div class="nasa-actions">
                                    <a href="${{nasa.hdurl || nasa.url}}" target="_blank" class="btn-mini">HD 보기</a>
                                    <a href="https://apod.nasa.gov/apod/astropix.html" target="_blank" class="btn-mini">NASA 원본</a>
                                </div>
                            </div>
                            <div class="nasa-title">${{nasa.title}}</div>
                            <p class="nasa-desc">${{nasa.explanation}}</p>
                            <div class="nasa-credit">
                                <strong>Image Credit & Copyright:</strong> ${{nasa.copyright || 'Public Domain'}} | <strong>Date:</strong> ${{nasa.date}}
                            </div>
                        </div>
                    </div>`;
                }} else html += `<div style="text-align:center; padding:50px; color:#666;">NASA 데이터를 불러올 수 없습니다.</div>`;
            
            }} else if (currentType === 'news') {{
                const newsList = science.news || [];
                if (newsList.length === 0) html = '<div style="text-align:center; padding:50px;">관련 뉴스가 없습니다.</div>';
                else html = '<div class="card-grid">' + newsList.map(n => `
                    <a href="${{n.link}}" target="_blank" class="card">
                        <span class="source-tag">${{n.source}}</span>
                        <span class="ai-tag">#${{currentField}}</span>
                        <div class="card-title">${{n.title}}</div>
                        <div class="card-meta">${{n.date}}</div>
                    </a>`).join('') + '</div>';
            
            }} else if (currentType === 'videos') {{
                const videoList = science.videos || [];
                if (videoList.length === 0) html = '<div style="text-align:center; padding:50px;">관련 영상이 없습니다.</div>';
                else html = '<div class="card-grid">' + videoList.map(v => `
                    <a href="${{v.link}}" target="_blank" class="card video-card">
                        <div class="thumb-wrapper">
                            <img src="${{v.thumbnail}}" class="thumb-img">
                            <div class="play-icon"></div>
                        </div>
                        <span class="source-tag">${{v.source}}</span>
                        <div class="card-title">${{v.title}}</div>
                        <div class="card-meta">${{new Date(v.date).toISOString().split('T')[0]}}</div>
                    </a>`).join('') + '</div>';
            
            }} else if (currentType === 'papers') {{
                const list = science.papers || [];
                if (list.length > 0) {{
                     html = '<div class="card-grid">' + list.map(p => `
                        <a href="${{p.link}}" target="_blank" class="card">
                            <span class="source-tag">${{p.source}}</span>
                            <span class="ai-tag">#Journal</span>
                            <div class="card-title">${{p.title}}</div>
                            <div class="card-meta">${{p.date || ''}}</div>
                        </a>`).join('') + '</div>';
                }} else {{
                    html = `<div style="padding:100px; text-align:center; color:#666;">${{currentField}} 분야의 논문 정보를 준비 중입니다.<br>(API 키 확인 필요)</div>`;
                }}

            }} else if (currentType === 'data') {{
                const list = science.data || [];
                if (list.length > 0) {{
                     html = '<div class="card-grid">' + list.map(p => `
                        <a href="${{p.link}}" target="_blank" class="card">
                            <span class="source-tag">${{p.source}}</span>
                            <div class="card-title">${{p.title}}</div>
                            <div class="card-desc" style="font-size:0.9rem; color:#888;">${{p.desc}}</div>
                        </a>`).join('') + '</div>';
                }} else {{
                    html = `<div style="padding:100px; text-align:center; color:#666;">데이터 정보를 준비 중입니다.</div>`;
                }}
            }}
            container.innerHTML = html;
        }}

        initUniverse();
        window.showField('천문·우주');
        
    </script>
</body>
</html>
    """

if __name__ == "__main__":
    nasa_info = get_nasa_data()
    science_info = collect_and_process_data()
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(generate_html(science_info, nasa_info))
    
    print("성공: index.html이 생성되었습니다.")
