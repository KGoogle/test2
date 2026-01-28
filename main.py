import json
import os
import requests
import google.generativeai as genai
import feedparser
import re
import sqlite3
from typing import List, Dict
from datetime import datetime
import time

NASA_API_KEY = os.environ.get('NASA_API_KEY', 'DEMO_KEY')

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") 
TRANSLATE_API_KEY = os.environ.get("TRANSLATE_API_KEY")

MODEL_NAME = 'gemini-2.5-flash-lite'

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    classify_model = genai.GenerativeModel(MODEL_NAME)
else:
    classify_model = None
    print("⚠️ 경고: GOOGLE_API_KEY가 없습니다.")

if TRANSLATE_API_KEY:
    translate_model = genai.GenerativeModel(MODEL_NAME)
else:
    translate_model = None
    print("⚠️ 경고: TRANSLATE_API_KEY가 없습니다.")

SCIENCE_FIELDS = ["천문·우주", "물리학", "인지/신경", "생명과학", "기타"]
DB_FILE = "science_data.db"

RSS_SOURCES = [
    {"url": "https://www.sciencedaily.com/rss/top.xml", "fixed_category": None},
    {"url": "https://phys.org/rss-feed/breaking/", "fixed_category": None},
    {"url": "https://www.space.com/feeds.xml", "fixed_category": "천문·우주"},
    {"url": "https://www.scientificamerican.com/platform/syndication/rss/", "fixed_category": None},
    {"url": "https://www.quantamagazine.org/feed/", "fixed_category": None}
]

YOUTUBE_SOURCES = [
    {"type": "channel", "id": "UCsXVk37bltHxD1rDPwtNM8Q"}, # Kurzgesagt
    {"type": "channel", "id": "UCHnyfMqiRRG1u-2MsSQLbXA"}, # Veritasium
    {"type": "channel", "id": "Csooa4yRKGN_zEE8iknghZA"}, # TED-Ed
    {"type": "playlist", "id": "PLYeXRzoBwGeHVguBktW327fxb1tKqLXrR"}, # 과학을 보다
    {"type": "playlist", "id": "PLkKcqR2KGxgzqeKZo1Rx93kJFokuVkpye"}, # 취미는 과학
    {"type": "channel", "id": "UCMc4EmuDxnHPc6pgGW-QWvQ"}, # 안될 과학
    {"type": "channel", "id": "UCrBpV_pG2kyMMEHCMTNzjAQ"}, # 리뷰엉이
    {"type": "channel", "id": "UCIk1-yPCTnFuzfgu4gyfWqw"}  # 과학드림
]

def call_gemini_with_retry(model, prompt, api_key, retries=3):
    if not api_key:
        return None

    genai.configure(api_key=api_key)
    
    try:
        return model.generate_content(prompt)
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "quota" in error_msg.lower():
            print(f"⚠️ API 할당량 초과 (키 확인 필요)")
        else:
            print(f"API 에러: {e}")
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
    category = video_data['tags'][0] if video_data.get('tags') else "기타"
    c.execute('''INSERT OR REPLACE INTO videos (id, title, link, thumbnail, pub_date, category, source)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (video_data['id'], video_data['title'], video_data['link'], 
               video_data['thumbnail'], video_data['date'], category, video_data['source']))
    conn.commit()
    conn.close()

def get_latest_videos(category=None, limit=8):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if category and category != "기타":
        query = f"SELECT title, link, thumbnail, pub_date, source FROM videos WHERE category LIKE ? ORDER BY pub_date DESC LIMIT ?"
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
    - 이미 한국어인 경우 그대로 두세요.
    - 반드시 JSON 배열 형식으로만 응답하세요: ["번역1", "번역2", ...]
    [텍스트 리스트]
    {json.dumps(text_list, ensure_ascii=False)}
    """

    response = call_gemini_with_retry(translate_model, prompt, TRANSLATE_API_KEY)
    if response:
        try:
            match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            print(f"번역 파싱 오류: {e}")
            
    return text_list

def classify_data_batch(items: List[Dict]) -> List[Dict]:
    if not items or not classify_model or not GOOGLE_API_KEY:
        print("분류 모델 미설정 또는 키 없음: 분류를 건너뛰며, DB에 저장하지 않습니다.")
        return []

    context = ""
    for i, item in enumerate(items):
        desc = item.get('desc', '')
        context += f"ID: {i}\n제목: {item['title']}\n내용: {desc[:150]}\n---\n"

    prompt = f"""
    당신은 과학 전문 큐레이터입니다. 아래 콘텐츠를 분석하여 가장 적합한 카테고리 하나를 선택하세요.
    [카테고리 후보] {', '.join(SCIENCE_FIELDS)}
    - '우주', '행성', 'Space' 관련은 "천문·우주"
    - '뇌', '신경', '심리', 'Brain', 'Neuroscience', 'Psychology' 관련은 "인지/신경"
    - 명확하지 않으면 "기타"
    - 반드시 JSON 리스트 형식으로만 응답: [ {{"id": 0, "tags": ["선택된카테고리"]}} ]
    [데이터]
    {context}
    """

    response = call_gemini_with_retry(classify_model, prompt, GOOGLE_API_KEY)
    
    if response:
        try:
            match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if match:
                results = json.loads(match.group())
                result_map = {res['id']: res.get('tags', ["기타"]) for res in results}
                
                for i, item in enumerate(items):
                    item['tags'] = result_map.get(i, ["기타"])
                
                return items
        except Exception as e:
            print(f"분류 파싱 오류: {e}")

    print("AI 분류 실패: 해당 배치를 DB에 저장하지 않습니다.")
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
    except Exception as e:
        print(f"NASA 연결 에러: {e}")
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

def fetch_and_process_videos():
    print("유튜브 영상 가져오는 중...")
    new_videos = []
    
    for source in YOUTUBE_SOURCES:
        source_id = source['id']
        url = f"https://www.youtube.com/feeds/videos.xml?{'playlist_id' if source.get('type')=='playlist' else 'channel_id'}={source_id}"
        
        try:
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
        except Exception as e:
            print(f"유튜브 파싱 에러: {e}")
    
    if new_videos:
        print(f"새로운 영상 {len(new_videos)}개 처리 시작 (Batch 작업)...")
        
        titles = [v['title'] for v in new_videos]
        translated_titles = []
        batch_size = 10
        for i in range(0, len(titles), batch_size):
            translated_titles.extend(translate_content(titles[i:i+batch_size]))
            time.sleep(5) 
        
        for i, v in enumerate(new_videos):
            if i < len(translated_titles): v['title'] = translated_titles[i]
        
        classified_videos = []
        for i in range(0, len(new_videos), batch_size):
            batch = new_videos[i:i+batch_size]
            classified_videos.extend(classify_data_batch(batch))
            time.sleep(5) 

        for video in classified_videos:
            save_video_to_db(video)
        print("영상 처리 완료.")
    else:
        print("새로운 영상이 없습니다.")

def collect_and_process_data():
    init_db()
    
    raw_news = fetch_rss_news()
    print(f"뉴스 {len(raw_news)}개 처리 중...")
    
    texts = []
    for item in raw_news: 
        texts.append(item['title'])
    
    translated_all = []
    for i in range(0, len(texts), 30):
        translated_all.extend(translate_content(texts[i:i+30]))
        time.sleep(5)

    for i, item in enumerate(raw_news):
        if i < len(translated_all):
            item['title'] = translated_all[i]

    to_classify = [i for i in raw_news if not i["fixed_category"]]
    if to_classify:
        classify_data_batch(to_classify)

    fetch_and_process_videos()
    
    all_data = {field: {"news": [], "videos": [], "papers": [], "data": []} for field in SCIENCE_FIELDS}
    
    for item in raw_news:
        tag = item.get('tags', [item.get('fixed_category', "기타")])[0]
        matched = "기타"
        for field in SCIENCE_FIELDS:
            if tag in field or field in tag:
                matched = field
                break
        all_data[matched]["news"].append(item)
    
    for field in SCIENCE_FIELDS:
        all_data[field]["videos"] = get_latest_videos(category=field, limit=5)

    all_data["천문·우주"]["papers"] = [
        {"title": "네이처 - Astronomy", "desc": "최신 천문학 연구", "link": "https://www.nature.com/natastron/", "source": "Nature"},
        {"title": "사이언스 - Space", "desc": "우주 과학 섹션", "link": "https://www.science.org/topic/category/astronomy", "source": "Science"},
        {"title": "왕립학회", "desc": "임시", "link": "https://royalsociety.org/", "source": "Royal Society"}
    ]
    all_data["천문·우주"]["data"] = [
        {"title": "나사", "desc": "임시", "link": "https://www.nasa.gov/", "source": "NASA"},
        {"title": "유럽 우주국", "desc": "임시", "link": "https://www.esa.int/", "source": "ESA"}
    ]

    return all_data

def generate_html(science_data, nasa_data):
    full_payload = json.dumps({"science": science_data, "nasa": nasa_data}, ensure_ascii=False)
    
    field_buttons_html = "".join([f'<button class="tab-btn" onclick="showField(\'{f}\')">{f}</button>' for f in SCIENCE_FIELDS])

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
            overflow: hidden; background: #000;
        }}
        #universe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; display: block; }}
        .header-content {{ position: relative; z-index: 1; pointer-events: none; }}
        header h1 {{
            margin: 0; font-size: 19px; color: #ffffff;
            font-family: 'Gowun Batang', serif;
            text-shadow: 0 0 10px rgba(255,255,255,0.3);
            word-break: keep-all; line-height: 1.8; font-weight: 400;
        }}
        
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; min-height: 100vh; position: relative; z-index: 1; }}
    
        .tabs-field {{ 
            display: flex; 
            gap: 10px; 
            margin: 0 auto 20px auto; 
            border-bottom: 1px solid var(--border); 
            padding: 0 15px 15px 15px; 
            overflow-x: auto; 
            justify-content: flex-start;
            scrollbar-width: none;
            -webkit-overflow-scrolling: touch;
        }}
        .tabs-field::-webkit-scrollbar {{ display: none; }}

        @media (min-width: 600px) {{
            .tabs-field {{ justify-content: center; }}
        }}

        .tab-btn {{ 
            background: transparent; border: 1px solid var(--border); color: var(--text-sub); 
            padding: 10px 24px; cursor: pointer; border-radius: 4px; font-weight: 500; 
            font-size: 0.95rem; transition: all 0.3s; white-space: nowrap;
            flex-shrink: 0;
        }}
        .tab-btn:hover {{ border-color: #666; color: #fff; }}
        .tab-btn.active {{ background: #ffffff; color: #000000; border-color: #ffffff; font-weight: bold; }}
        
        .sub-tabs {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
        .sub-btn {{ background: none; border: none; color: #666; cursor: pointer; font-size: 0.9rem; font-weight: bold; padding: 5px 0; border-bottom: 2px solid transparent; transition: 0.3s; }}
        .sub-btn:hover {{ color: #aaa; }}
        .sub-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}

        .nasa-hero {{ margin-bottom: 40px; border-radius: 4px; overflow: hidden; background: #000000; border: 1px solid var(--border); animation: fadeIn 1s; }}
        .nasa-img {{ width: 100%; height: auto; max-height: 750px; object-fit: contain; display: block; margin: 0 auto; background: #000; }}
        .nasa-info {{ padding: 40px; border-top: 1px solid var(--border); }}
        .nasa-header-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; flex-wrap: wrap; gap: 15px; }}
        .nasa-tag {{ background: #fff; color: #000; padding: 5px 12px; border-radius: 2px; font-size: 0.75rem; font-weight: 800; text-decoration: none; }}
        .nasa-actions {{ display: flex; gap: 8px; }}
        .btn-mini {{ border: 1px solid #444; color: #888; padding: 4px 12px; text-decoration: none; font-size: 0.7rem; border-radius: 2px; transition: 0.3s; }}
        .btn-mini:hover {{ border-color: #fff; color: #fff; }}
        .nasa-title {{ font-size: 1.8rem; font-weight: bold; margin-bottom: 15px; font-family: 'Gowun Batang', serif; color: #fff; }}
        .nasa-desc {{ color: #bbbbbb; font-size: 1rem; line-height: 1.8; text-align: justify; letter-spacing: -0.01em; }}
        .nasa-credit {{ font-size: 0.85rem; color: #666; margin-top: 20px; padding-top: 20px; border-top: 1px solid #222; }}

        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; animation: fadeIn 0.4s; }}
        .card {{ background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 4px; padding: 25px; transition: all 0.3s; display: flex; flex-direction: column; text-decoration: none; color: inherit; position: relative; overflow: hidden; }}
        .card:hover {{ border-color: #ffffff; background-color: #111111; transform: translateY(-3px); }}
        .source-tag {{ font-size: 10px; background: #fff; color: #000; padding: 2px 6px; border-radius: 2px; position: absolute; top: 15px; right: 15px; font-weight: bold; z-index: 2; }}
        .ai-tag {{ font-size: 10px; color: #888; border: 1px solid #333; padding: 2px 8px; border-radius: 12px; display: inline-block; margin-bottom: 12px; align-self: flex-start; }}
        .card-title {{ font-size: 1.1rem; font-weight: 600; color: #ffffff; margin-bottom: 12px; line-height: 1.4; padding-right: 10px; }}
        .card-desc {{ font-size: 0.9rem; color: #888; margin-bottom: 15px; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
        .card-meta {{ font-size: 0.8rem; color: #666; margin-top: auto; letter-spacing: 0.05em; }}
        
        .video-card .thumb-wrapper {{ width: 100%; padding-top: 56.25%; position: relative; margin: -25px -25px 15px -25px; background: #222; }}
        .video-card .thumb-img {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.8; transition: 0.3s; }}
        .video-card:hover .thumb-img {{ opacity: 1; transform: scale(1.05); }}
        .video-card .play-icon {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 40px; height: 40px; background: rgba(0,0,0,0.6); border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 2px solid #fff; }}
        .video-card .play-icon::after {{ content:''; display: block; width: 0; height: 0; border-top: 8px solid transparent; border-bottom: 8px solid transparent; border-left: 14px solid #fff; margin-left: 4px; }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    </style>
</head>
<body>
    <header id="header-container">
        <canvas id="universe"></canvas>
        <div class="header-content">
            <h1>"삶에 별빛을 섞으세요. <br>하찮은 일에 마음이 괴롭지 않을 겁니다." <br><span style="font-size:14px; margin-top:15px; display:block; opacity: 0.8;">- 마리아 미첼 -</span></h1>
        </div>
    </header>

    <div class="container">
        <nav class="tabs-field">{field_buttons_html}</nav>
        <nav id="sub-tabs-container" class="sub-tabs"></nav>
        <main id="main-content"></main>
    </div>

    <script>
        const universeCanvas = document.getElementById('universe');
        const universeCtx = universeCanvas.getContext('2d');
        const headerContainer = document.getElementById('header-container');
        let universeW, universeH, universeDpr = Math.max(1, window.devicePixelRatio || 1);
        let stars = [];
        let lastWidth = 0;

        function resizeUniverse() {{
            const currentWidth = headerContainer.offsetWidth;
            const currentHeight = headerContainer.offsetHeight;
            if (currentWidth !== lastWidth) {{
                universeW = currentWidth; 
                universeH = currentHeight;
                universeCanvas.width = universeW * universeDpr; 
                universeCanvas.height = universeH * universeDpr;
                universeCtx.setTransform(universeDpr, 0, 0, universeDpr, 0, 0);
                
                createStars(Math.round((universeW * universeH) / 800)); 
                lastWidth = currentWidth;
            }}
        }}

        function createStars(count) {{
            stars = [];
            for (let i = 0; i < count; i++) {{
                const colorRand = Math.random();
                let color;
                if (colorRand < 0.7) color = '#ffffff';
                else if (colorRand < 0.82) color = '#aabfff';
                else if (colorRand < 0.94) color = '#ffd2a1';
                else color = '#ffcc6f';

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

        function drawUniverse() {{
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
            requestAnimationFrame(drawUniverse);
        }}

        window.addEventListener('resize', resizeUniverse); 
        resizeUniverse(); 
        drawUniverse();

        const fullData = {full_payload};
        let currentField = "천문·우주";
        let currentType = "apod";

        function showField(f) {{
            currentField = f;
            currentType = (f === "천문·우주") ? "apod" : "news";
            
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.innerText === f));
            
            renderSubTabs();
            render();
        }}

        function showType(t) {{
            currentType = t;
            renderSubTabs();
            render();
        }}

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
                <button class="sub-btn ${{currentType === t.id ? 'active' : ''}}" onclick="showType('${{t.id}}')">${{t.name}}</button>
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
                                </div>
                            </div>
                            <div class="nasa-title">${{nasa.title}}</div>
                            <p class="nasa-desc">${{nasa.explanation}}</p>
                            <div class="nasa-credit">
                                <strong>Copyright:</strong> ${{nasa.copyright || 'Public Domain'}} | <strong>Date:</strong> ${{nasa.date}}
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
            
            }} else if (currentType === 'papers' || currentType === 'data') {{
                const list = science[currentType] || [];
                if (list.length > 0) {{
                     html = '<div class="card-grid">' + list.map(p => `
                        <a href="${{p.link}}" target="_blank" class="card">
                            <span class="source-tag">${{p.source}}</span>
                            <div class="card-title">${{p.title}}</div>
                            <div class="card-desc">${{p.desc}}</div>
                        </a>`).join('') + '</div>';
                }} else {{
                    html = `<div style="padding:100px; text-align:center; color:#666;">${{currentField}} 분야의 ${{currentType === 'papers' ? '논문' : '데이터'}} 정보를 준비 중입니다.</div>`;
                }}
            }}
            container.innerHTML = html;
        }}

        window.onload = () => {{
            showField('천문·우주');
            const tabField = document.querySelector('.tabs-field');
            if(tabField) tabField.scrollLeft = 0;
        }};
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
