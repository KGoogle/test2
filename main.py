import json
import os
import requests

SCIENCE_FIELDS = ["천문·우주", "물리학", "화학", "생명과학"]

def get_nasa_data():
    api_key = os.environ.get('NASA_API_KEY', 'DEMO_KEY')
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"NASA API 에러: {response.status_code}")
            return None
    except Exception as e:
        print(f"연결 에러: {e}")
        return None

def collect_test_data():
    all_data = {}
    for field in SCIENCE_FIELDS:
        news_list = []
        for i in range(1, 13):
            news_list.append({
                "title": f"[{field}] 뉴스 {i}",
                "link": "#",
                "date": "2026-01-26"
            })
        
        papers_list = []
        for i in range(1, 9):
            papers_list.append({
                "title": f"Research in {field} {i}",
                "url": "#",
                "authors": "Dr. Scientist",
                "abstract": "연구 논문 요약 내용입니다. 이 논문은 해당 분야의 획기적인 발전을 다루고 있으며 상세한 실험 데이터와 결과를 포함하고 있습니다."
            })
            
        videos_list = []
        for i in range(1, 7):
            videos_list.append({
                "title": f"{field} 영상 콘텐츠 {i}",
                "link": "#",
                "thumbnail": f"https://via.placeholder.com/320x180/222/fff?text={field}+{i}"
            })

        all_data[field] = {
            "news": news_list,
            "papers": papers_list,
            "videos": videos_list,
            "data": f"{field} 지표 모니터링 중",
            "events": [
                {"date": "2025-10-10", "title": f"{field} 컨퍼런스"},
                {"date": "2025-11-20", "title": f"{field} 성과 발표"}
            ]
        }
    return all_data

def generate_html(science_data, nasa_data):
    full_payload = {
        "science": science_data,
        "nasa": nasa_data
    }
    json_data = json.dumps(full_payload, ensure_ascii=False)
    
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
            --bg: #111111; --card-bg: #1c1c1c; --text-main: #f0f0f0;
            --text-sub: #888888; --accent: #ffffff; --border: #333;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            background-color: var(--bg); color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0; padding: 0; line-height: 1.6;
        }}
        header {{ 
            position: relative; text-align: center; padding: 60px 20px;
            overflow: hidden; background: #000;
        }}
        #universe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; }}
        .header-content {{ position: relative; z-index: 1; }}
        header h1 {{
            margin: 0; font-size: 18px; color: #ffffff;
            font-family: 'Gowun Batang', serif;
            text-shadow: 0 0 5px rgba(255,255,255,0.5);
            word-break: keep-all; line-height: 1.6; font-weight: 400;
        }}
        .author {{ display: block; font-size: 14px; margin-top: 10px; color: #ffffff; }}
        
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; min-height: 100vh; }}
    
        .tabs-field {{ 
            display: flex; 
            gap: 12px; 
            margin: 0 auto 20px auto; 
            border-bottom: 1px solid var(--border); 
            padding-bottom: 15px; 
            overflow-x: auto; 
            justify-content: center;
            width: 100%;
            scrollbar-width: none;
        }}
        .tabs-field::-webkit-scrollbar {{ display: none; }}

        .tab-btn {{ 
            background: transparent; border: 1px solid var(--border); color: var(--text-sub); 
            padding: 10px 25px; cursor: pointer; border-radius: 8px; font-weight: 600; 
            font-size: 0.95rem; transition: all 0.2s; white-space: nowrap;
        }}
        .tab-btn.active {{ background: var(--accent); color: #000; border-color: var(--accent); }}
        
        .sub-tabs {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
        .sub-btn {{ background: none; border: none; color: #555; cursor: pointer; font-size: 0.9rem; font-weight: bold; padding: 5px 0; border-bottom: 2px solid transparent; }}
        .sub-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
        
        .nasa-hero {{ margin-bottom: 40px; border-radius: 15px; overflow: hidden; background: #0a0a0a; border: 1px solid #333; animation: fadeIn 0.8s; }}
        .nasa-img {{ width: 100%; height: auto; max-height: 700px; object-fit: contain; display: block; margin: 0 auto; background: #000; }}
        .nasa-info {{ padding: 30px; }}
        .nasa-tag {{ background: #fff; color: #000; padding: 3px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-bottom: 15px; display: inline-block; }}
        .nasa-title {{ font-size: 1.6rem; font-weight: bold; margin-bottom: 10px; font-family: 'Gowun Batang', serif; }}
        .nasa-desc {{ color: #ccc; font-size: 0.95rem; line-height: 1.8; text-align: justify; }}

        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; animation: fadeIn 0.3s; }}
        .card {{ background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 20px; transition: transform 0.2s; display: flex; flex-direction: column; text-decoration: none; color: inherit; }}
        .card:hover {{ transform: translateY(-3px); border-color: #555; }}
        .card-title {{ font-size: 1.05rem; font-weight: 600; color: #fff; margin-bottom: 10px; line-height: 1.4; }}
        .card-meta {{ font-size: 0.75rem; color: var(--text-sub); margin-top: auto; }}
        
        .card-abstract {{ 
            font-size: 0.85rem; color: #777; margin-bottom: 15px; 
            display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; 
        }}
        
        .video-thumb {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: 6px; margin-bottom: 12px; background: #222; }}
        .data-box {{ background: var(--card-bg); border: 1px dashed #444; padding: 80px 20px; border-radius: 15px; text-align: center; }}
        .event-item {{ background: var(--card-bg); border-left: 4px solid var(--accent); margin-bottom: 15px; padding: 20px; border-radius: 4px; }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    </style>
</head>
<body>
    <header id="header-container">
        <canvas id="universe"></canvas>
        <div class="header-content">
            <h1>"삶에 별빛을 섞으세요. <br>하찮은 일에 마음이 괴롭지 않을 겁니다." <span class="author">- 마리아 미첼 -</span></h1>
        </div>
    </header>

    <div class="container">
        <nav class="tabs-field" id="main-tabs">{field_buttons_html}</nav>
        <nav id="sub-tabs-container" class="sub-tabs"></nav>

        <main id="main-content"></main>
    </div>

    <script>
        const universeCanvas = document.getElementById('universe');
        const universeCtx = universeCanvas.getContext('2d');
        const headerContainer = document.getElementById('header-container');
        let universeW, universeH, universeDpr = Math.max(1, window.devicePixelRatio || 1);
        let stars = [];

        function resizeUniverse() {{
            universeW = headerContainer.offsetWidth; universeH = headerContainer.offsetHeight;
            universeCanvas.width = universeW * universeDpr; universeCanvas.height = universeH * universeDpr;
            universeCtx.setTransform(universeDpr, 0, 0, universeDpr, 0, 0);
            createStars(Math.round((universeW * universeH) / 3000)); 
        }}
        function createStars(count) {{
            stars = [];
            for (let i = 0; i < count; i++) {{
                const colorRand = Math.random();
                let color = colorRand < 0.8 ? '#ffffff' : colorRand < 0.9 ? '#aabfff' : '#ffd2a1';
                stars.push({{ x: Math.random() * universeW, y: Math.random() * universeH, r: Math.pow(Math.random(), 3) * 2 + 0.5, tw: Math.random() * Math.PI * 2, twSpeed: Math.random() * 0.005 + 0.002, c: color }});
            }}
        }}
        function drawUniverse() {{
            universeCtx.clearRect(0, 0, universeW, universeH);
            stars.forEach(s => {{
                s.tw += s.twSpeed; const twinkleAlpha = 0.3 + Math.sin(s.tw) * 0.4;
                const grad = universeCtx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r);
                grad.addColorStop(0, s.c); grad.addColorStop(1, 'transparent');
                universeCtx.globalAlpha = twinkleAlpha; universeCtx.fillStyle = grad;
                universeCtx.beginPath(); universeCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2); universeCtx.fill();
            }});
            requestAnimationFrame(drawUniverse);
        }}
        window.addEventListener('resize', resizeUniverse); resizeUniverse(); drawUniverse();

        const fullData = {json_data};
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
            document.querySelectorAll('.sub-btn').forEach(b => {{
                const onClickStr = b.getAttribute('onclick');
                if (onClickStr.includes(`'${{t}}'`)) b.classList.add('active');
                else b.classList.remove('active');
            }});
            render();
        }}

        function renderSubTabs() {{
            const container = document.getElementById('sub-tabs-container');
            let tabs = [];
            if (currentField === "천문·우주") {{
                tabs.push({{ id: 'apod', name: '오늘의 천문 사진' }});
            }}
            tabs.push(
                {{ id: 'news', name: '뉴스' }},
                {{ id: 'papers', name: '논문' }},
                {{ id: 'comm', name: '콘텐츠' }},
                {{ id: 'data', name: '데이터' }},
                {{ id: 'events', name: '일정' }}
            );

            container.innerHTML = tabs.map(t => `
                <button class="sub-btn ${{currentType === t.id ? 'active' : ''}}" onclick="showType('${{t.id}}')">${{t.name}}</button>
            `).join('');
        }}

        function render() {{
            const science = fullData.science[currentField];
            const nasa = fullData.nasa;
            const container = document.getElementById('main-content');
            let html = '';

            if (currentType === 'apod') {{
                if (nasa) {{
                    html += `
                    <div class="nasa-hero">
                        <img src="${{nasa.hdurl || nasa.url}}" class="nasa-img" alt="NASA APOD">
                        <div class="nasa-info">
                            <span class="nasa-tag">NASA APOD TODAY</span>
                            <div class="nasa-title">${{nasa.title}}</div>
                            <p class="nasa-desc">${{nasa.explanation}}</p>
                            <div style="font-size: 0.8rem; color: #555; margin-top:20px; border-top: 1px solid #333; padding-top: 15px;">
                                <strong>Copyright:</strong> ${{nasa.copyright || 'Public Domain'}} | <strong>Date:</strong> ${{nasa.date}}
                            </div>
                        </div>
                    </div>
                    `;
                }} else {{
                    html += `<div class="data-box">NASA 데이터를 불러올 수 없습니다.</div>`;
                }}
            }} else if (currentType === 'news') {{
                html += '<div class="card-grid">' + science.news.map(n => `<a href="${{n.link}}" class="card"><div class="card-title">${{n.title}}</div><div class="card-meta">${{n.date}}</div></a>`).join('') + '</div>';
            }} else if (currentType === 'papers') {{
                html += '<div class="card-grid">' + science.papers.map(p => `<a href="${{p.url}}" class="card"><div class="card-title">${{p.title}}</div><div class="card-abstract">${{p.abstract}}</div><div class="card-meta">${{p.authors}}</div></a>`).join('') + '</div>';
            }} else if (currentType === 'comm') {{
                html += '<div class="card-grid">' + science.videos.map(v => `<a href="${{v.link}}" class="card"><img src="${{v.thumbnail}}" class="video-thumb"><div class="card-title">${{v.title}}</div></a>`).join('') + '</div>';
            }} else if (currentType === 'data') {{
                html += `<div class="data-box"><div style="font-size:1.8rem; font-weight:bold; color:var(--accent);">${{science.data}}</div></div>`;
            }} else if (currentType === 'events') {{
                html += '<div>' + science.events.map(e => `<div class="event-item"><div style="color:var(--text-sub);">${{e.date}}</div><div>${{e.title}}</div></div>`).join('') + '</div>';
            }}

            container.innerHTML = html;
        }}

        showField('천문·우주');
    </script>
</body>
</html>
    """

if __name__ == "__main__":
    nasa_data = get_nasa_data()
    science_data = collect_test_data()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(generate_html(science_data, nasa_data))
    print("index.html 생성 완료.")
