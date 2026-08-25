import os
import sys
import re
import glob
import csv
import time
import logging
from urllib.parse import urlparse
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import numpy as np
import pandas as pd
import joblib
import requests
import pymysql
import streamlit as st
import streamlit.components.v1 as components
import plotly.express as px
import plotly.graph_objects as go

# ==============================================================================
# 1. 페이지 기본 설정 및 스타일 정의
# ==============================================================================
st.set_page_config(
    page_title="라이브 트랙맨 실시간 대시보드",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main .block-container {
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 100% !important;
    }
    .metric-card {
        background-color: #ffffff;
        border: 2px solid #dcdcdc;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: left;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        margin-bottom: 10px;
    }
    .metric-title {
        color: #555555;
        font-size: 0.95rem;
        font-weight: bold;
        margin-bottom: 4px;
    }
    .metric-value {
        color: #111111;
        font-size: 1.8rem;
        font-weight: 800;
    }
    .login-container {
        background: linear-gradient(135deg, #0b2545 0%, #134074 100%);
        padding: 35px;
        border-radius: 14px;
        color: white;
        box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        margin-top: 50px;
    }
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 0.85rem;
    }
    .status-live {
        background-color: #28a745;
        color: white;
    }
    .status-offline {
        background-color: #6c757d;
        color: white;
    }
    .pitch-pill {
        display: inline-block;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: bold;
        color: white;
        font-size: 0.9rem;
        margin-right: 6px;
        margin-bottom: 6px;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. 사용자 인증 시스템 (st.empty() Placeholder 기반 로그인 창 완전 소멸)
# ==============================================================================
try:
    VALID_USERS = dict(st.secrets.get("users", {}))
except Exception:
    VALID_USERS = {}

# URL 파라미터를 통한 보호된 자동 로그인 (토큰 검증 필수)
# 사용: ?token=YOUR_SECRET_TOKEN&user=ncdata
query_params = st.query_params
autologin_token = st.secrets.get("autologin_token", "")
provided_token = query_params.get('token', '')
provided_user = query_params.get('user', '')

# 토큰이 설정되어 있고, 제공된 토큰이 일치하며, 사용자가 유효한 경우에만 자동 로그인
if autologin_token and provided_token == autologin_token and provided_user in VALID_USERS:
    st.session_state['authenticated'] = True
    st.session_state['user_id'] = provided_user

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

login_placeholder = st.empty()

if not st.session_state['authenticated']:
    with login_placeholder.container():
        col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
        with col_l2:
            st.markdown("""
            <div class="login-container">
                <h2 style="color: #00a6fb; margin-bottom: 5px; text-align: center;">⚾ 라이브 대시보드 시스템</h2>
                <p style="color: #8d99ae; font-size: 14px; text-align: center; margin-bottom: 25px;">
                    실시간 투구 데이터 및 구종 예측
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            login_id = st.text_input("👤 아이디 (ID)", key="login_id_field")
            login_pw = st.text_input("🔒 비밀번호 (Password)", type="password", key="login_pw_field")
            
            if st.button("🔓 로그인 (Sign In)", type="primary", use_container_width=True):
                if login_id.strip() in VALID_USERS and VALID_USERS[login_id.strip()] == login_pw.strip():
                    st.session_state['authenticated'] = True
                    st.session_state['user_id'] = login_id.strip()
                    login_placeholder.empty()
                    st.rerun()
                else:
                    st.error("❌ 아이디 또는 비밀번호가 일치하지 않습니다.")
            st.stop()
else:
    login_placeholder.empty()

# ==============================================================================
# 3. 설정 및 구장별 웹주소 매핑 & 구종 색상/마커 매핑
# ==============================================================================
try:
    raw_urls = dict(st.secrets.get("stadium_urls", {}))
    _sec_urls = {str(k).lower(): str(v) for k, v in raw_urls.items()}
except Exception:
    raw_urls = {}
    _sec_urls = {}

def _get_url(key_str, default=""):
    return raw_urls.get(key_str) or _sec_urls.get(key_str.lower(), default)

STADIUM_URL_MAP = {
    "마산": _get_url("Masan"),
    "창원": _get_url("NCDinosMajors") or _get_url("Changwon"),
    "고척": _get_url("Gocheok"),
    "잠실": _get_url("Jamsil"),
    "인천": _get_url("Incheon"),
    "대구": _get_url("DaeguPark") or _get_url("Daegu"),
    "대전": _get_url("Daejeon"),
    "사직": _get_url("Sajik"),
    "수원": _get_url("Suwon"),
    "광주": _get_url("Gwangju"),
    "목동": _get_url("Mokdong"),
    "경산": _get_url("SamsungMinor") or _get_url("Gyeongsan"),
    "이천 (두산)": _get_url("DoosanMinors"),
    "⚙️ 사용자 직접 URL 입력": "CUSTOM"
}
try:
    USERNAME = os.getlogin()
except Exception:
    USERNAME = os.environ.get('USERNAME') or os.environ.get('USER') or 'user'
POSSIBLE_MODEL_DIRS = [
    os.path.join(os.path.expanduser("~"), "models", "pitchermodel"),
    os.path.join(os.getcwd(), "models", "pitchermodel"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "pitchermodel")
]

PITCH_TYPE_MAP = {
    0: "Fastball",
    1: "Curveball",
    2: "Slider",
    3: "ChangeUp",
    4: "Sinker",
    5: "Cutter",
    6: "Splitter",
    7: "Unknown"
}

PITCH_KOR_MAP = {
    'Fastball': '직구', 'Four-Seam': '직구', 'FourSeam': '직구',
    'Sinker': '투심', 'Two-Seam': '투심',
    'Slider': '슬라', 'Cutter': '커터',
    'ChangeUp': '체인', 'Changeup': '체인',
    'Curveball': '커브', 'Curve': '커브',
    'Splitter': '포크', 'Forkball': '포크',
    'Sweeper': '스위', 'Knuckleball': '너클',
    'Other': '미확', 'Undefined': '미확', 'Unknown': '미확'
}

def get_pitch_kor(val: Any) -> str:
    v_str = str(val).strip()
    return PITCH_KOR_MAP.get(v_str, v_str if v_str else '미확')

PITCH_COLOR_MAP = {
    "직구": "#FF0000",        # 빨강 (🔴)
    "투심": "#E67E22",        # 주황 (▼)
    "커터": "#FF8099",        # 핑크/살구 (★)
    "슬라": "#F1C40F",        # 노랑 (⧓)
    "커브": "#95A5A6",        # 회색 (▲)
    "스위": "#E040FB",        # 보라 (+)
    "체인": "#2ECC71",        # 초록 (◆)
    "포크": "#2980B9",        # 파랑 (■)
    "너클": "#5D6D7E",        # 진회색 (✖)
    "미확": "#BDC3C7",        # 연회색 (●)
    # 원본 영문 fallback
    "Fastball": "#FF0000",
    "Four-Seam": "#FF0000",
    "Sinker": "#E67E22",
    "Two-Seam": "#E67E22",
    "Cutter": "#FF8099",
    "Slider": "#F1C40F",
    "Curveball": "#95A5A6",
    "Curve": "#95A5A6",
    "Sweeper": "#E040FB",
    "ChangeUp": "#2ECC71",
    "Changeup": "#2ECC71",
    "Splitter": "#2980B9",
    "Forkball": "#2980B9",
    "Knuckleball": "#5D6D7E",
    "Other": "#BDC3C7",
    "Undefined": "#BDC3C7",
    "Unknown": "#BDC3C7"
}

PITCH_SYMBOL_MAP = {
    "직구": "circle",
    "투심": "triangle-down",
    "커터": "star",
    "슬라": "bowtie",
    "커브": "triangle-up",
    "스위": "cross",
    "체인": "diamond",
    "포크": "square",
    "너클": "x",
    "미확": "circle",
    # 원본 영문 fallback
    "Fastball": "circle",
    "Four-Seam": "circle",
    "Sinker": "triangle-down",
    "Two-Seam": "triangle-down",
    "Cutter": "star",
    "Slider": "bowtie",
    "Curveball": "triangle-up",
    "Curve": "triangle-up",
    "Sweeper": "cross",
    "ChangeUp": "diamond",
    "Changeup": "diamond",
    "Splitter": "square",
    "Forkball": "square",
    "Knuckleball": "x",
    "Other": "circle",
    "Undefined": "circle",
    "Unknown": "circle"
}

PLOTLY_JPEG_CONFIG = {
    'toImageButtonOptions': {
        'format': 'jpeg',
        'filename': 'masan_pitch_chart',
        'height': 800,
        'width': 1200,
        'scale': 2
    },
    'displayModeBar': True
}

# ==============================================================================
# 4. 선수 마스터 한글 이름 변환 매퍼 (DB & CSV 기반)
# ==============================================================================
class PlayerMasterResolver:
    def __init__(self):
        self.id_to_kor = {}
        self.eng_to_kor = {}
        self.load_player_master()

    def load_player_master(self):
        mysql_sec = st.secrets.get("mysql", {}) if hasattr(st, "secrets") else {}
        db_user = mysql_sec.get("user")
        db_pass = mysql_sec.get("password")
        db_port = int(mysql_sec.get("port", 3333))
        extra_hosts = mysql_sec.get("hosts", [])
        hosts = [h for h in [mysql_sec.get("host"), mysql_sec.get("host2")] + extra_hosts if h]

        if db_user and db_pass:
            for h in hosts:
                try:
                    con = pymysql.connect(
                        host=h, port=db_port, user=db_user, password=db_pass,
                        db="dinos_dw", charset="utf8mb4", connect_timeout=2
                    )
                    with con.cursor() as cur:
                        cur.execute("SELECT player_id, player_name, player_name_eng FROM dim_player")
                        rows = cur.fetchall()
                        for p_id, p_name, p_eng in rows:
                            if p_name:
                                name_str = str(p_name).strip()
                                if p_id:
                                    self.id_to_kor[str(p_id).strip()] = name_str
                                if p_eng:
                                    self.eng_to_kor[str(p_eng).strip().lower()] = name_str
                    con.close()
                    break
                except Exception:
                    continue

        candidate_paths = [
            "C:/SynologyDrive/Rawdata/KBO_ID_all.csv",
            "C:/SynologyDrive/_imsi/KBO_ID_all.csv",
            "C:/SynologyDrive/Rawdata/Cleaningdata/KBO_ID_all.csv",
            "D:/SynologyDrive/Rawdata/KBO_ID_all.csv"
        ]
        for path in candidate_paths:
            if os.path.exists(path):
                try:
                    for enc in ['cp949', 'euc-kr', 'utf-8-sig']:
                        try:
                            df_p = pd.read_csv(path, encoding=enc)
                            if 'P_ID' in df_p.columns and 'P_NM' in df_p.columns:
                                for _, r in df_p.iterrows():
                                    pid = str(r['P_ID']).split('.')[0].strip()
                                    pnm = str(r['P_NM']).strip()
                                    if pid and pnm and pid not in self.id_to_kor:
                                        self.id_to_kor[pid] = pnm
                            break
                        except Exception:
                            continue
                except Exception:
                    pass

    def get_korean_name(self, player_id: Any, english_name: Any) -> str:
        pid_str = str(player_id).strip() if player_id is not None else ""
        eng_str = str(english_name).strip() if english_name is not None else ""
        
        if re.search(r'[가-힣]', eng_str):
            return eng_str

        if pid_str and pid_str in self.id_to_kor:
            return self.id_to_kor[pid_str]

        if eng_str and eng_str.lower() in self.eng_to_kor:
            return self.eng_to_kor[eng_str.lower()]

        return eng_str if eng_str else "Unknown"

@st.cache_resource(show_spinner=False)
def get_player_resolver():
    return PlayerMasterResolver()

# ==============================================================================
# 5. 머신러닝 구종 예측 모델 관리 클래스
# ==============================================================================
class PitchPredictionModel:
    def __init__(self):
        self.models = {}
        self.pitch_type_map = PITCH_TYPE_MAP.copy()
        self.model_dir = self.find_model_directory()
        if self.model_dir:
            self.load_models()

    def find_model_directory(self) -> Optional[str]:
        for d in POSSIBLE_MODEL_DIRS:
            if os.path.exists(d):
                return d
        return None

    def load_models(self):
        if not self.model_dir:
            return
        
        map_path = os.path.join(self.model_dir, "pitch_type_map.joblib")
        if os.path.exists(map_path):
            try:
                loaded_map = joblib.load(map_path)
                if isinstance(loaded_map, dict):
                    self.pitch_type_map.update(loaded_map)
            except Exception as e:
                logging.error(f"pitch_type_map 로드 오류: {e}")

        model_files = glob.glob(os.path.join(self.model_dir, "*.pkl")) + glob.glob(os.path.join(self.model_dir, "*.joblib"))
        for filepath in model_files:
            if filepath.endswith("pitch_type_map.joblib"):
                continue
            filename = os.path.basename(filepath)
            try:
                self.models[filename] = joblib.load(filepath)
            except Exception as e:
                logging.error(f"모델 로드 실패 ({filename}): {e}")

    def preprocess_pitch_data(self, input_data: Dict[str, Any]) -> Dict[str, float]:
        rel_speed = input_data.get('RelSpeed', 0.0)
        avg_fb_velo = 143.0
        velo_diff = rel_speed - avg_fb_velo
        raw_throws = input_data.get('PitcherThrows', 'Right')
        pitcher_throws = 0.0 if raw_throws in ['Right', 'R', 0, 0.0] else 1.0

        vert_break = input_data.get('VertBreak', 0.0)
        horz_break = input_data.get('HorzBreak', 0.0)
        spin_rate = input_data.get('SpinRate', 0.0)

        break_ratio = abs(vert_break) / (abs(horz_break) + 1e-6)
        spin_efficiency = spin_rate / (rel_speed if rel_speed > 0 else 1e-6)

        return {
            'RelSpeed': rel_speed,
            'Velo_Diff': velo_diff,
            'SpinRate': spin_rate,
            'VAA': input_data.get('VertApprAngle', 0.0),
            'HAA': input_data.get('HorzApprAngle', 0.0),
            'VertBreak': vert_break,
            'HorzBreak': horz_break,
            'SpinAxis': input_data.get('SpinAxis', 0.0),
            'BreakRatio': break_ratio,
            'SpinEfficiency': spin_efficiency,
            'BallCount': float(input_data.get('Balls', 0)),
            'StrikeCount': float(input_data.get('Strikes', 0)),
            'PitcherThrows': pitcher_throws
        }

    def predict(self, pitch_features: Dict[str, Any]) -> tuple[str, float]:
        if not self.models:
            return "No Model", 0.0

        try:
            processed = self.preprocess_pitch_data(pitch_features)
            X = pd.DataFrame([processed])

            predictions = {}
            for model_name, model in self.models.items():
                try:
                    pred = model.predict(X)
                    probs = model.predict_proba(X)[0]
                    confidence = float(np.max(probs))
                    raw_label = pred[0]
                    
                    if isinstance(raw_label, (int, float, np.integer, np.floating)) or (isinstance(raw_label, str) and raw_label.isdigit()):
                        label_key = int(raw_label)
                        pred_label = self.pitch_type_map.get(label_key, f"Unknown-{label_key}")
                    else:
                        pred_label = str(raw_label)
                    
                    if confidence < 0.3:
                        pred_label = "Unknown"
                        confidence = 0.0
                    predictions[model_name] = (pred_label, confidence)
                except Exception as e:
                    continue

            if not predictions:
                return "Unknown", 0.0

            best_pred = max(predictions.values(), key=lambda x: x[1])
            return best_pred[0], round(best_pred[1] * 100, 1)
        except Exception as e:
            return "Error", 0.0

@st.cache_resource(show_spinner=False)
def get_prediction_model():
    return PitchPredictionModel()

# ==============================================================================
# 6. 구장별 트랙맨 REST API 동적 파싱
# ==============================================================================
class DataProcessor:
    @staticmethod
    def safe_get(data: Dict, path: List[str], default: Any = "") -> Any:
        if data is None:
            return default
        for key in path:
            if not isinstance(data, dict):
                return default
            data = data.get(key, default)
            if data is None:
                return default
        return data

    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def safe_str(value: Any, default: str = "") -> str:
        return str(value) if value is not None else default

def get_base_origin(url: str) -> str:
    """/livedashboard 같은 하위 경로가 붙어 있어도 http://host:port 형태의 루트 Origin만 추출"""
    if not url:
        return ""
    url = url.strip()
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "http://" + url
        parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def fetch_live_session_id(base_url: str) -> Optional[str]:
    if not base_url:
        return None
    
    origin = get_base_origin(base_url)
    if not origin:
        return None

    headers = {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    params = {'_': int(time.time() * 1000)}

    # 1. 표준 sessionmode API 호출
    try:
        url = f"{origin}/sessionmanager/api/sessionmode/"
        response = requests.get(url, headers=headers, params=params, timeout=4)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                sid = data.get("sessionId") or data.get("id") or data.get("currentSessionId")
                if sid:
                    return str(sid)
    except Exception:
        pass

    # 2. metadata API 또는 active session API 호출
    for endpoint in ["/sessionmanager/api/active/", "/sessionmanager/api/metadata/"]:
        try:
            url = f"{origin}{endpoint}"
            response = requests.get(url, headers=headers, params=params, timeout=3)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    sid = data.get("sessionId") or data.get("id")
                    if sid:
                        return str(sid)
        except Exception:
            pass

    # 3. HTML/JS 웹페이지(/livedashboard 등) 소스 코드에서 UUID 패턴 정규식 탐색
    for page_path in ["/livedashboard", "/"]:
        try:
            page_url = f"{origin}{page_path}"
            response = requests.get(page_url, headers=headers, params=params, timeout=4)
            if response.status_code == 200:
                uuids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', response.text, re.I)
                if uuids:
                    return uuids[0]
        except Exception:
            pass

    return None

def fetch_review_data(base_url: str, session_id: str, retries: int = 3) -> Optional[Dict]:
    if not base_url or not session_id:
        return None
    origin = get_base_origin(base_url)
    url = f"{origin}/taggingapp/api/review/{session_id}"
    
    headers = {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    params = {'_': int(time.time() * 1000)}

    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=6)
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5)
    return None

def extract_session_id_from_input(user_input: str) -> str:
    match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', user_input)
    if match:
        return match.group(1)
    return user_input.strip()

def parse_trackman_json(raw_json: Dict, model_engine: PitchPredictionModel, session_id: str = "") -> pd.DataFrame:
    plays = raw_json.get("plays", [])
    if "missingPlays" in raw_json and isinstance(raw_json["missingPlays"], list):
        plays.extend(raw_json["missingPlays"])

    if not plays:
        return pd.DataFrame()

    cache_session_key = "live_cache_session_id"
    cache_ids_key = "live_cache_processed_ids"
    cache_df_key = "live_cache_df"

    if st.session_state.get(cache_session_key) != session_id or cache_ids_key not in st.session_state:
        st.session_state[cache_session_key] = session_id
        st.session_state[cache_ids_key] = set()
        st.session_state[cache_df_key] = pd.DataFrame()

    processed_ids = st.session_state[cache_ids_key]
    cached_df = st.session_state[cache_df_key]

    dp = DataProcessor()
    resolver = get_player_resolver()
    current_time = datetime.now()

    def get_preferred_id(foreign_ids):
        if not foreign_ids:
            return ''
        for fid in foreign_ids:
            if 'KBO-' in fid:
                return fid.split('-')[-1]
        for fid in foreign_ids:
            if 'MLB-' in fid:
                return fid.split('-')[-1]
        return foreign_ids[0].split('-')[-1]

    new_rows = []
    for play_idx, play_item in enumerate(plays, start=1):
        play = dp.safe_get(play_item, ['play'], {})
        pitch_tag = dp.safe_get(play, ['pitchTag'], {})
        pitch_call = dp.safe_get(pitch_tag, ['pitchCall'], '')
        
        if pitch_call == 'Undefined':
            continue

        play_id = dp.safe_get(play, ['id'], '')
        if play_id and play_id in processed_ids:
            continue

        tagger_behavior = dp.safe_get(play, ['taggerBehavior'], {})
        players = dp.safe_get(play, ['players'], {})
        pitcher = dp.safe_get(players, ['pitcher'], {})
        batter = dp.safe_get(players, ['batter'], {})
        catcher = dp.safe_get(players, ['catcher'], {})
        game_state = dp.safe_get(play, ['gameState'], {})
        hit_tag = dp.safe_get(play, ['hitTag'], {})
        play_result = dp.safe_get(play, ['playResult'], {})
        strikezonedecision = dp.safe_get(play, ['strikeZoneDecision'], {})
        playeratbat = dp.safe_get(strikezonedecision, ['playerAtBat'], {})

        track_time = play.get('trackStartTimeLocal', '')
        try:
            date_obj = datetime.strptime(track_time.split('+')[0], "%Y-%m-%dT%H:%M:%S")
        except:
            date_obj = current_time

        ball_finals_list = play_item.get("ballFinals", [])
        ball_finals1 = ball_finals_list[0] if len(ball_finals_list) > 0 else {}
        ball_finals2 = ball_finals_list[1] if len(ball_finals_list) > 1 else {}

        pitch_data = {} if ball_finals1 is None else ball_finals1.get("pitch", {}) or {}
        start_data = {} if ball_finals1 is None else ball_finals1.get("start", {}) or {}
        release_data = {} if pitch_data is None else pitch_data.get("release", {}) or {}
        movement_data = {} if pitch_data is None else pitch_data.get("movement", {}) or {}
        location_data = {} if pitch_data is None else pitch_data.get("location", {}) or {}
        position_data = {} if location_data is None else location_data.get("position", {}) or {}
        velocity_data = {} if location_data is None else location_data.get("velocity", {}) or {}
        hit_data = {} if ball_finals2 is None else ball_finals2.get("hit", {}) or {}

        strike_locations = dp.safe_get(strikezonedecision, ['strikeZoneDecisionLocations'], [])
        middle_zone = next((loc for loc in strike_locations if dp.safe_get(loc, ['strikeZoneLocationType'], '') == 'Middle'), {})
        back_zone = next((loc for loc in strike_locations if dp.safe_get(loc, ['strikeZoneLocationType'], '') == 'Back'), {})

        rel_speed = dp.safe_float(start_data.get("speed", 0.0)) * 3.6
        spin_rate = dp.safe_float(start_data.get("spinRate", 0.0))
        spin_axis = dp.safe_float(movement_data.get("spinAxis", 0.0))
        rel_height = dp.safe_float(release_data.get("height", 0.0)) * 100
        rel_side = dp.safe_float(release_data.get("side", 0.0)) * 100
        extension = dp.safe_float(release_data.get("extension", 0.0)) * 100
        vert_break = dp.safe_float(movement_data.get("vertical", 0.0)) * 100
        horz_break = dp.safe_float(movement_data.get("horizontal", 0.0)) * 100
        induced_vert_break = dp.safe_float(movement_data.get("inducedVertical", 0.0)) * 100
        
        # ─── Front, Middle, Back 공 위치 (좌우: X, 높이: Y) ───
        f_ball_x = dp.safe_get(strikezonedecision, ['ballX'], None)
        f_ball_y = dp.safe_get(strikezonedecision, ['ballY'], None)

        if f_ball_x is not None:
            plate_loc_side = dp.safe_float(f_ball_x) * 100.0
        elif location_data.get("side") is not None:
            plate_loc_side = dp.safe_float(location_data.get("side")) * 100.0
        elif position_data.get("z") is not None:
            plate_loc_side = dp.safe_float(position_data.get("z")) * 100.0
        else:
            plate_loc_side = 0.0

        if f_ball_y is not None:
            plate_loc_height = dp.safe_float(f_ball_y) * 100.0
        elif location_data.get("height") is not None:
            plate_loc_height = dp.safe_float(location_data.get("height")) * 100.0
        elif position_data.get("y") is not None:
            plate_loc_height = dp.safe_float(position_data.get("y")) * 100.0
        else:
            plate_loc_height = 0.0

        m_ball_x = dp.safe_get(middle_zone, ['ballX'], None)
        m_ball_y = dp.safe_get(middle_zone, ['ballY'], None)
        mid_loc_side = dp.safe_float(m_ball_x) * 100.0 if m_ball_x is not None else plate_loc_side
        mid_loc_height = dp.safe_float(m_ball_y) * 100.0 if m_ball_y is not None else plate_loc_height

        b_ball_x = dp.safe_get(back_zone, ['ballX'], None)
        b_ball_y = dp.safe_get(back_zone, ['ballY'], None)
        back_loc_side = dp.safe_float(b_ball_x) * 100.0 if b_ball_x is not None else plate_loc_side
        back_loc_height = dp.safe_float(b_ball_y) * 100.0 if b_ball_y is not None else plate_loc_height

        vert_appr_angle = dp.safe_float(velocity_data.get("verticalAngle", 0.0))
        horz_appr_angle = dp.safe_float(location_data.get("horizontalAngle", 0.0))

        pitcher_id = dp.safe_str(get_preferred_id(pitcher.get('foreignIds', [])))
        batter_id = dp.safe_str(get_preferred_id(batter.get('foreignIds', [])))
        catcher_id = dp.safe_str(get_preferred_id(catcher.get('foreignIds', [])))

        pitcher_name = resolver.get_korean_name(pitcher_id, pitcher.get('nameRef', 'Unknown'))
        batter_name = resolver.get_korean_name(batter_id, batter.get('nameRef', 'Unknown'))
        catcher_name = resolver.get_korean_name(catcher_id, catcher.get('nameRef', ''))

        pitcher_throws = pitcher.get('pitchingHandedness', 'Right')

        tilt_str = movement_data.get("tilt", "")
        tilt_display = tilt_str if tilt_str else "0:00"

        input_features = {
            'RelSpeed': rel_speed,
            'SpinRate': spin_rate,
            'SpinAxis': spin_axis,
            'VertBreak': vert_break,
            'HorzBreak': horz_break,
            'VertApprAngle': vert_appr_angle,
            'HorzApprAngle': horz_appr_angle,
            'PitcherThrows': pitcher_throws,
            'Balls': game_state.get('balls', 0),
            'Strikes': game_state.get('strikes', 0)
        }
        
        pred_pitch, pred_conf = model_engine.predict(input_features)

        batter_height = playeratbat.get("height", 0.0)
        if isinstance(batter_height, (int, float)) and batter_height > 0:
            batter_height = batter_height * 2.54
        else:
            batter_height = 180.0
        
        sz_bottom = batter_height * 0.2704
        sz_top = batter_height * 0.5575

        # ─── Front, Middle, Back S-Zone dimensions (left, right, top, bottom) ───
        sz_dim = dp.safe_get(strikezonedecision, ['dimensions'], {})
        sz_m_dim = dp.safe_get(middle_zone, ['dimensions'], {})
        sz_b_dim = dp.safe_get(back_zone, ['dimensions'], {})

        f_top = dp.safe_float(sz_dim.get('top', 0.0)) * 100.0 if sz_dim.get('top') else sz_top
        f_bot = dp.safe_float(sz_dim.get('bottom', 0.0)) * 100.0 if sz_dim.get('bottom') else sz_bottom
        f_left = dp.safe_float(sz_dim.get('left', 0.0)) * 100.0 if sz_dim.get('left') else -23.59
        f_right = dp.safe_float(sz_dim.get('right', 0.0)) * 100.0 if sz_dim.get('right') else 23.59

        m_top = dp.safe_float(sz_m_dim.get('top', 0.0)) * 100.0 if sz_m_dim.get('top') else f_top
        m_bot = dp.safe_float(sz_m_dim.get('bottom', 0.0)) * 100.0 if sz_m_dim.get('bottom') else f_bot
        m_left = dp.safe_float(sz_m_dim.get('left', 0.0)) * 100.0 if sz_m_dim.get('left') else f_left
        m_right = dp.safe_float(sz_m_dim.get('right', 0.0)) * 100.0 if sz_m_dim.get('right') else f_right

        b_top = dp.safe_float(sz_b_dim.get('top', 0.0)) * 100.0 if sz_b_dim.get('top') else f_top
        b_bot = dp.safe_float(sz_b_dim.get('bottom', 0.0)) * 100.0 if sz_b_dim.get('bottom') else f_bot
        b_left = dp.safe_float(sz_b_dim.get('left', 0.0)) * 100.0 if sz_b_dim.get('left') else f_left
        b_right = dp.safe_float(sz_b_dim.get('right', 0.0)) * 100.0 if sz_b_dim.get('right') else f_right

        row_dict = {
            "PitchNo": play_idx,
            "Date": date_obj.strftime("%Y-%m-%d"),
            "Time": date_obj.strftime("%H:%M:%S"),
            "PAofInning": dp.safe_str(tagger_behavior.get('pAofInning', '')),
            "PitchofPA": dp.safe_str(tagger_behavior.get('pitchOfPA', '')),
            "Pitcher": pitcher_name,
            "PitcherId": pitcher_id,
            "PitcherThrows": pitcher_throws,
            "PitcherTeam": dp.safe_get(play, ['pitchingTeam', 'shortName'], ''),
            "Batter": batter_name,
            "BatterId": batter_id,
            "BatterSide": batter.get('battingHandedness', 'Right'),
            "BatterTeam": dp.safe_get(play, ['battingTeam', 'shortName'], ''),
            "Catcher": catcher_name,
            "PitcherSet": pitch_tag.get('pitcherSet', ''),
            "Inning": dp.safe_str(game_state.get('inning', '1')),
            "TopBottom": game_state.get('topBottom', ''),
            "Outs": dp.safe_str(game_state.get('outs', '0')),
            "Balls": dp.safe_str(game_state.get('balls', '0')),
            "Strikes": dp.safe_str(game_state.get('strikes', '0')),
            "TaggedPitchType": pitch_tag.get('pitchType', 'Undefined'),
            "AutoPitchType": pitch_tag.get('pitchType', 'Undefined'),
            "PitchCall": pitch_call,
            "KorBB": play.get('kOrBB', ''),
            "TaggedHitType": hit_tag.get('hitType', ''),
            "PlayResult": play_result.get('result', 'Undefined'),
            "OutsOnPlay": dp.safe_str(play_result.get('outs', '0')),
            "RunsScored": dp.safe_str(play_result.get('runs', '0')),
            "Notes": play.get('note', ''),
            "RelSpeed": rel_speed,
            "SpinRate": spin_rate,
            "SpinAxis": int(spin_axis),
            "Tilt": tilt_display,
            "RelHeight": rel_height,
            "RelSide": rel_side,
            "Extension": extension,
            "VertBreak": vert_break,
            "InducedVertBreak": induced_vert_break,
            "HorzBreak": horz_break,
            "PlateLocHeight": plate_loc_height,
            "PlateLocSide": plate_loc_side,
            "MiddlePlateLocHeight": mid_loc_height,
            "MiddlePlateLocSide": mid_loc_side,
            "BackPlateLocHeight": back_loc_height,
            "BackPlateLocSide": back_loc_side,
            "FrontStrikeZoneLeft": f_left,
            "FrontStrikeZoneRight": f_right,
            "MiddleStrikeZoneTop": m_top,
            "MiddleStrikeZoneBottom": m_bot,
            "MiddleStrikeZoneLeft": m_left,
            "MiddleStrikeZoneRight": m_right,
            "BackStrikeZoneTop": b_top,
            "BackStrikeZoneBottom": b_bot,
            "BackStrikeZoneLeft": b_left,
            "BackStrikeZoneRight": b_right,
            "StrikeZoneDecision": pitch_call,
            "ExitSpeed": dp.safe_float(dp.safe_get(ball_finals2, ["start", "speed"], 0.0)) * 3.6,
            "Angle": dp.safe_float(dp.safe_get(hit_data, ["launch", "verticalAngle"], 0.0)),
            "Distance": dp.safe_float(dp.safe_get(ball_finals2, ["hit", "landingFlat", "distance"], 0.0)),
            "Batterheight": batter_height,
            "strikeZoneBottom": sz_bottom,
            "strikeZoneTop": sz_top,
            "PredictedPitchType": pred_pitch,
            "PredictionConfidence": pred_conf
        }
        new_rows.append(row_dict)
        if play_id:
            processed_ids.add(play_id)

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if not cached_df.empty:
            combined_df = pd.concat([cached_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
    else:
        combined_df = cached_df

    if not combined_df.empty:
        combined_df["PitchNo"] = list(range(1, len(combined_df) + 1))

    st.session_state[cache_df_key] = combined_df
    return combined_df

def process_uploaded_csv(df_raw: pd.DataFrame, model_engine: PitchPredictionModel) -> pd.DataFrame:
    df = df_raw.copy()
    resolver = get_player_resolver()
    
    rename_map = {
        "Top/Bottom": "TopBottom",
        "In.": "Inning",
        "O.": "Outs",
        "B": "Balls",
        "S": "Strikes",
        "무브_상하": "InducedVertBreak",
        "무브_좌우": "HorzBreak",
        "타점_상하": "RelHeight",
        "타점_좌우": "RelSide",
        "익스": "Extension",
        "구속": "RelSpeed",
        "회전수": "SpinRate",
        "회전축": "SpinAxis",
        "태깅구종": "TaggedPitchType",
        "예측구종": "PredictedPitchType"
    }
    df = df.rename(columns=rename_map)

    if "Pitcher" in df.columns:
        df["Pitcher"] = df.apply(lambda r: resolver.get_korean_name(r.get("PitcherId"), r.get("Pitcher")), axis=1)
    if "Batter" in df.columns:
        df["Batter"] = df.apply(lambda r: resolver.get_korean_name(r.get("BatterId"), r.get("Batter")), axis=1)

    # 원본 CSV의 PitchNo가 역순(최신구가 1행)으로 작성되어 있으면 정방향(초구 -> 최신구)으로 전환
    if "PitchNo" in df.columns:
        p_no = pd.to_numeric(df["PitchNo"], errors='coerce')
        if p_no.notnull().sum() > 1 and p_no.iloc[0] > p_no.iloc[-1]:
            df = df.iloc[::-1].reset_index(drop=True)

    # 경기 투구 원본 발생 순서(초구 -> 최신구) 보존하여 PitchNo (1 ~ N) 연속 부여
    df["PitchNo"] = list(range(1, len(df) + 1))

    for loc_col in ["PlateLocSide", "PlateLocHeight", "MiddlePlateLocSide", "MiddlePlateLocHeight", "BackPlateLocSide", "BackPlateLocHeight"]:
        if loc_col in df.columns:
            df[loc_col] = pd.to_numeric(df[loc_col], errors='coerce').fillna(0.0)
            if df[loc_col].abs().max() < 5.0:
                df[loc_col] = df[loc_col] * 100.0

    if "MiddlePlateLocSide" not in df.columns:
        df["MiddlePlateLocSide"] = df.get("PlateLocSide", 0.0)
    if "MiddlePlateLocHeight" not in df.columns:
        df["MiddlePlateLocHeight"] = df.get("PlateLocHeight", 0.0)
    if "BackPlateLocSide" not in df.columns:
        df["BackPlateLocSide"] = df.get("PlateLocSide", 0.0)
    if "BackPlateLocHeight" not in df.columns:
        df["BackPlateLocHeight"] = df.get("PlateLocHeight", 0.0)

    if "InducedVertBreak" not in df.columns:
        df["InducedVertBreak"] = df.get("VertBreak", 0.0)

    if "strikeZoneBottom" not in df.columns:
        if "Batterheight" in df.columns:
            df["strikeZoneBottom"] = pd.to_numeric(df["Batterheight"], errors='coerce').fillna(180.0) * 0.2704
            df["strikeZoneTop"] = pd.to_numeric(df["Batterheight"], errors='coerce').fillna(180.0) * 0.5575
        else:
            df["strikeZoneBottom"] = 48.6
            df["strikeZoneTop"] = 100.3

    for col_def, default_val in [
        ("FrontStrikeZoneLeft", -21.59), ("FrontStrikeZoneRight", 21.59),
        ("MiddleStrikeZoneTop", df["strikeZoneTop"].iloc[0] if not df.empty else 100.3),
        ("MiddleStrikeZoneBottom", df["strikeZoneBottom"].iloc[0] if not df.empty else 48.6),
        ("MiddleStrikeZoneLeft", -21.59), ("MiddleStrikeZoneRight", 21.59),
        ("BackStrikeZoneTop", df["strikeZoneTop"].iloc[0] if not df.empty else 100.3),
        ("BackStrikeZoneBottom", df["strikeZoneBottom"].iloc[0] if not df.empty else 48.6),
        ("BackStrikeZoneLeft", -21.59), ("BackStrikeZoneRight", 21.59)
    ]:
        if col_def not in df.columns:
            df[col_def] = default_val

    if "Tilt" not in df.columns:
        df["Tilt"] = "0:00"
    df["Tilt"] = df["Tilt"].fillna("0:00").astype(str)

    for num_col in ["RelSpeed", "SpinRate", "SpinAxis", "RelHeight", "RelSide", "Extension", "ExitSpeed", "Angle", "Distance", "HorzBreak", "InducedVertBreak"]:
        if num_col not in df.columns:
            df[num_col] = 0.0
        else:
            df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0.0)

    for str_col in ["Pitcher", "Batter", "Inning", "PitchCall", "PlayResult", "TaggedPitchType"]:
        if str_col not in df.columns:
            df[str_col] = "Unknown" if str_col in ["Pitcher", "Batter"] else ""
        else:
            df[str_col] = df[str_col].fillna("").astype(str)

    if "PredictedPitchType" not in df.columns or df["PredictedPitchType"].isnull().all():
        pred_pitches = []
        pred_confs = []
        for _, row in df.iterrows():
            features = {
                'RelSpeed': row.get('RelSpeed', 0.0),
                'SpinRate': row.get('SpinRate', 0.0),
                'SpinAxis': row.get('SpinAxis', 0.0),
                'VertBreak': row.get('InducedVertBreak', row.get('VertBreak', 0.0)),
                'HorzBreak': row.get('HorzBreak', 0.0),
                'VertApprAngle': row.get('VertApprAngle', 0.0),
                'HorzApprAngle': row.get('HorzApprAngle', 0.0),
                'PitcherThrows': row.get('PitcherThrows', 'Right'),
                'Balls': row.get('Balls', 0),
                'Strikes': row.get('Strikes', 0)
            }
            p_type, p_conf = model_engine.predict(features)
            if p_type in ["No Model", "Unknown", "Error"] and row.get("TaggedPitchType"):
                p_type = row.get("TaggedPitchType")
                p_conf = 100.0
            pred_pitches.append(p_type)
            pred_confs.append(p_conf)
        df["PredictedPitchType"] = pred_pitches
        df["PredictionConfidence"] = pred_confs
    else:
        df["PredictedPitchType"] = df["PredictedPitchType"].fillna(df["TaggedPitchType"]).astype(str)
        if "PredictionConfidence" not in df.columns:
            df["PredictionConfidence"] = 100.0
        else:
            df["PredictionConfidence"] = pd.to_numeric(df["PredictionConfidence"], errors='coerce').fillna(100.0)

    return df

# ==============================================================================
# 7. 3D & 2D 스트라이크 존 시각화 생성 함수 (MLB/KBO 17in x 17in 홈플레이트 규격 100% 정밀 반영)
# ==============================================================================
def create_3d_strike_zone(target_row: pd.Series) -> tuple[go.Figure, bool]:
    """
    공식 MLB/KBO 홈플레이트 규격 반영 (가로 43.2cm, 총깊이 43.1cm, 평행변 21.6cm, 사선변 30.5cm)
    - Apex(꼭지점): (0, 0)
    - Middle(중간 지점): Y = 21.5cm (8.5인치)
    - Front(전면 평평한 면): Y = 43.1cm (17인치)
    """
    sz_b = target_row.get("strikeZoneBottom", 48.6)
    sz_t = target_row.get("strikeZoneTop", 100.3)

    m_top = target_row.get("MiddleStrikeZoneTop", sz_t)
    m_bot = target_row.get("MiddleStrikeZoneBottom", sz_b)
    m_left = target_row.get("MiddleStrikeZoneLeft", -21.59)
    m_right = target_row.get("MiddleStrikeZoneRight", 21.59)

    f_top = target_row.get("FrontStrikeZoneTop", m_top)
    f_bot = target_row.get("FrontStrikeZoneBottom", m_bot)
    f_left = target_row.get("FrontStrikeZoneLeft", -21.59)
    f_right = target_row.get("FrontStrikeZoneRight", 21.59)

    b_top = target_row.get("BackStrikeZoneTop", sz_t)
    b_bot = target_row.get("BackStrikeZoneBottom", sz_b)
    b_left = target_row.get("BackStrikeZoneLeft", -21.59)
    b_right = target_row.get("BackStrikeZoneRight", 21.59)

    fig = go.Figure()

    # 1. 3D 정밀 홈플레이트 테두리 (가로 43.2cm x 총깊이 43.1cm)
    # Apex(0,0) -> Back-Right(21.59, 21.515) -> Front-Right(21.59, 43.105) -> Front-Left(-21.59, 43.105) -> Back-Left(-21.59, 21.515) -> Apex(0,0)
    hp_x = [0, 21.59, 21.59, -21.59, -21.59, 0]
    hp_y = [0, 21.515, 43.105, 43.105, 21.515, 0]
    hp_z = [0, 0, 0, 0, 0, 0]
    
    fig.add_trace(go.Scatter3d(
        x=hp_x, y=hp_y, z=hp_z,
        mode='lines',
        line=dict(color='#ffffff', width=5),
        name='Home Plate (17 in)'
    ))

    proj_corners = [(0, 0), (21.59, 21.515), (21.59, 43.105), (-21.59, 43.105), (-21.59, 21.515)]
    for cx, cy in proj_corners:
        fig.add_trace(go.Scatter3d(
            x=[cx, cx], y=[cy, cy], z=[0, sz_b],
            mode='lines',
            line=dict(color='#555555', width=1.5, dash='dash'),
            showlegend=False
        ))

    # 2. 3개의 빨간색 수직 단면 면 (Front: Y=43.1cm, Middle: Y=21.5cm, Back: Y=0cm)
    def add_red_strike_plane(x_min, x_max, z_min, z_max, y_val, opacity=0.55, name="Zone Plane"):
        fig.add_trace(go.Surface(
            x=[[x_min, x_max], [x_min, x_max]],
            y=[[y_val, y_val + 0.5], [y_val, y_val + 0.5]],
            z=[[z_min, z_min], [z_max, z_max]],
            colorscale=[[0, '#E74C3C'], [1, '#E74C3C']],
            showscale=False,
            opacity=opacity,
            name=name,
            showlegend=False
        ))
        fig.add_trace(go.Scatter3d(
            x=[x_min, x_max, x_max, x_min, x_min],
            y=[y_val, y_val, y_val, y_val, y_val],
            z=[z_min, z_min, z_max, z_max, z_min],
            mode='lines',
            line=dict(color='#ffffff', width=3),
            showlegend=False
        ))

    add_red_strike_plane(b_left, b_right, b_bot, b_top, 0, opacity=0.50, name="Back Plane (Apex)")
    add_red_strike_plane(m_left, m_right, m_bot, m_top, 21.515, opacity=0.65, name="Middle Plane")
    add_red_strike_plane(f_left, f_right, sz_b, sz_t, 43.105, opacity=0.50, name="Front Plane")

    # 3D 외곽 박스 모서리 테두리 연결선
    box_corners = [
        (b_left, b_bot, m_left, m_bot, f_left, sz_b),
        (b_right, b_bot, m_right, m_bot, f_right, sz_b),
        (b_right, b_top, m_right, m_top, f_right, sz_t),
        (b_left, b_top, m_left, m_top, f_left, sz_t),
    ]
    for x1, z1, x2, z2, x3, z3 in box_corners:
        fig.add_trace(go.Scatter3d(
            x=[x1, x2, x3], y=[0, 21.515, 43.105], z=[z1, z2, z3],
            mode='lines',
            line=dict(color='#ffffff', width=2),
            showlegend=False
        ))

    # 3. Front, Middle, Back 3개 공 위치 노출 (원본 X 좌표 그대로 사용)
    f_px = target_row.get("PlateLocSide", 0.0)
    f_pz = target_row.get("PlateLocHeight", 0.0)

    m_px = target_row.get("MiddlePlateLocSide", f_px)
    m_pz = target_row.get("MiddlePlateLocHeight", f_pz)

    b_px = target_row.get("BackPlateLocSide", f_px)
    b_pz = target_row.get("BackPlateLocHeight", f_pz)

    pitch_type = str(target_row["PredictedPitchType"])
    pitch_color = PITCH_COLOR_MAP.get(pitch_type, "#FF0000")

    # Front -> Middle -> Back 3개 존 통과 연결선
    fig.add_trace(go.Scatter3d(
        x=[b_px, m_px, f_px],
        y=[0, 21.515, 43.105],
        z=[b_pz, m_pz, f_pz],
        mode='lines',
        line=dict(color=pitch_color, width=5),
        showlegend=False
    ))

    balls_x = [b_px, m_px, f_px]
    balls_y = [0, 21.515, 43.105]
    balls_z = [b_pz, m_pz, f_pz]
    labels = ["Back", "Middle", "Front"]

    # 3.6cm 반지름 (지름 7.2cm) 3D 실물 좌표 구체 생성
    u_vals = np.linspace(0, 2 * np.pi, 10)
    v_vals = np.linspace(0, np.pi, 10)

    for bx, by, bz, lbl in zip(balls_x, balls_y, balls_z, labels):
        sx = bx + 3.6 * np.outer(np.cos(u_vals), np.sin(v_vals))
        sy = by + 3.6 * np.outer(np.sin(u_vals), np.sin(v_vals))
        sz = bz + 3.6 * np.outer(np.ones(np.size(u_vals)), np.cos(v_vals))

        fig.add_trace(go.Surface(
            x=sx, y=sy, z=sz,
            colorscale=[[0, pitch_color], [1, pitch_color]],
            showscale=False,
            opacity=1.0,
            name=f"Ball-{lbl}",
            showlegend=False
        ))

        fig.add_trace(go.Scatter3d(
            x=[bx], y=[by], z=[bz + 4],
            mode='text',
            text=[f"{lbl} [{bx:+.0f}, {bz:.0f}]"],
            textposition="top center",
            textfont=dict(color="white", size=10, family="Arial"),
            showlegend=False
        ))

    decision = target_row.get("StrikeZoneDecision", "")
    if decision:
        is_strike = "Strike" in decision or "In" in decision
    else:
        is_strike = (-23.58 <= f_px <= 23.58) and (sz_b <= f_pz <= sz_t)

    fig.update_layout(
        title=dict(
            text="<span style='background-color:#00a6fb;color:white;padding:2px 8px;border-radius:10px;font-size:0.75rem;'>New</span> <b>3D STRIKE ZONE (Front / Middle / Back)</b>",
            font=dict(size=16, color="#FF7700")
        ),
        scene=dict(
            xaxis=dict(range=[-60, 60], backgroundcolor="#121212", gridcolor="#333333", title="X (좌우 cm)"),
            yaxis=dict(range=[-50, 100], backgroundcolor="#121212", gridcolor="#333333", title="Y (깊이 cm)", dtick=20),
            zaxis=dict(range=[0, 200], backgroundcolor="#121212", gridcolor="#333333", title="Z (높이 cm)"),
            aspectmode='data', # 실물 1:1:1 비율 고정 (Y축 비대칭 왜곡 방지)
            camera=dict(
                eye=dict(x=-1.5, y=-1.5, z=1.0)
            )
        ),
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=10, r=10, t=40, b=10),
        height=540,
        showlegend=False
    )

    return fig, is_strike

def create_2d_szone_plots(target_row: pd.Series):
    """
    2D S-Zone Front, Middle, Back 3개 하늘색 사각형 그림 생성 함수 (미터/cm 자동 정규화 및 3px 고대비 실선 적용)
    """
    pitch_type = str(target_row["PredictedPitchType"])
    pitch_color = PITCH_COLOR_MAP.get(pitch_type, "#8c4366")
    pitch_symbol = PITCH_SYMBOL_MAP.get(pitch_type, "circle")

    def safe_num(val, default_val):
        v = pd.to_numeric(val, errors='coerce')
        if pd.isna(v) or v == 0:
            return float(default_val)
        v = float(v)
        if abs(v) < 5.0:  # 미터 단위(m)인 경우 cm 단위로 자동 100배 수정!
            v = v * 100.0
        return v

    sz_b = safe_num(target_row.get("strikeZoneBottom"), 48.6)
    sz_t = safe_num(target_row.get("strikeZoneTop"), 100.3)

    m_left = safe_num(target_row.get("MiddleStrikeZoneLeft"), -23.59)
    m_right = safe_num(target_row.get("MiddleStrikeZoneRight"), 23.59)
    m_bot = safe_num(target_row.get("MiddleStrikeZoneBottom"), sz_b)
    m_top = safe_num(target_row.get("MiddleStrikeZoneTop"), sz_t)

    f_left = safe_num(target_row.get("FrontStrikeZoneLeft"), -21.59)
    f_right = safe_num(target_row.get("FrontStrikeZoneRight"), 21.59)
    f_bot = safe_num(target_row.get("FrontStrikeZoneBottom"), m_bot)
    f_top = safe_num(target_row.get("FrontStrikeZoneTop"), m_top)
    f_x = safe_num(target_row.get("PlateLocSide"), 0.0)
    f_z = safe_num(target_row.get("PlateLocHeight"), 0.0)

    m_x = safe_num(target_row.get("MiddlePlateLocSide"), f_x)
    m_z = safe_num(target_row.get("MiddlePlateLocHeight"), f_z)

    b_left = safe_num(target_row.get("BackStrikeZoneLeft"), -23.59)
    b_right = safe_num(target_row.get("BackStrikeZoneRight"), 23.59)
    b_bot = safe_num(target_row.get("BackStrikeZoneBottom"), m_bot)
    b_top = safe_num(target_row.get("BackStrikeZoneTop"), m_top)
    b_x = safe_num(target_row.get("BackPlateLocSide"), f_x)
    b_z = safe_num(target_row.get("BackPlateLocHeight"), f_z)

    def build_fig(x_val, z_val, l_left, l_right, l_bot, l_top, title_text, subtitle_text):
        fig = go.Figure()
        
        # 1. 하늘색 사각형 반투명 영역 (add_shape)
        fig.add_shape(
            type="rect", x0=l_left, x1=l_right, y0=l_bot, y1=l_top,
            line=dict(color="#00a6fb", width=3),
            fillcolor="rgba(0, 166, 251, 0.18)"
        )

        # 2. 선명한 3px 하늘색 실선 사각형 테두리 (go.Scatter 선명 렌더링)
        fig.add_trace(go.Scatter(
            x=[l_left, l_right, l_right, l_left, l_left],
            y=[l_bot, l_bot, l_top, l_top, l_bot],
            mode='lines',
            line=dict(color='#00a6fb', width=3),
            showlegend=False,
            name='S-Zone Box'
        ))

        # 3. 중앙 0cm 가이드 점선
        fig.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)

        # 4. 투구 통과 위치 마커 (좌우: X, 높이: Y)
        fig.add_trace(go.Scatter(
            x=[x_val], y=[z_val],
            mode='markers+text',
            marker=dict(size=14, color=pitch_color, symbol=pitch_symbol),
            text=[f"[{x_val:+.0f}, {z_val:.0f}]"],
            hovertemplate=f"<b>{pitch_type}</b><br>좌우(Side): %{{x:+.1f}} cm<br>높이(Height): %{{y:.1f}} cm<extra></extra>",
            textposition="top right",
            name=pitch_type
        ))

        # 5. 축 범위 넉넉하게 확장 (축소 1단계 수준: 좌우 -80~80cm, 높이 -15~175cm)
        x_min = min(-80.0, x_val - 10) if x_val is not None else -80.0
        x_max = max(80.0, x_val + 10) if x_val is not None else 80.0
        z_min = min(-15.0, z_val - 10) if z_val is not None else -15.0
        z_max = max(175.0, z_val + 10) if z_val is not None else 175.0

        fig.update_layout(
            title=dict(text=f"<b>{title_text}</b> <span style='font-size:0.8rem;color:gray;'>{subtitle_text}</span>", font=dict(size=13)),
            xaxis=dict(
                range=[x_min, x_max],
                tickvals=[-80, -60, -40, -20, 0, 20, 40, 60, 80],
                ticktext=["-80", "-60", "-40", "-20", "0", "20", "40", "60", "80"],
                showgrid=True,
                gridcolor='#eeeeee',
                title="좌우 (cm)"
            ),
            yaxis=dict(
                range=[z_min, z_max],
                showgrid=True,
                gridcolor='#eeeeee',
                title="높이 (cm)"
            ),
            height=290,
            margin=dict(l=20, r=20, t=30, b=20),
            showlegend=False
        )
        return fig

    fig_front = build_fig(f_x, f_z, f_left, f_right, f_bot, f_top, "s존_front", "<포수시점>")
    fig_mid = build_fig(m_x, m_z, m_left, m_right, m_bot, m_top, "s존_mid", "<포수시점>")
    fig_back = build_fig(b_x, b_z, b_left, b_right, b_bot, b_top, "s존_back", "<포수시점>")

    return fig_front, fig_mid, fig_back

def format_meter(val):
    """
    미터(m) 단위 치수 변환 및 None / NaN / 미작업 예외 처리 안전 함수
    연습 경기나 수동 입력 미작업 상태에서도 오류 없이 Pass 하여 대시보드가 정상 출력되도록 함.
    """
    if val is None or pd.isna(val):
        return 0.0
    try:
        v = float(val)
        if abs(v) > 10.0:  # cm 단위로 전달되었을 경우 m 단위로 변환
            return v / 100.0
        return v
    except (ValueError, TypeError):
        return 0.0

def safe_num(val, default=0.0):
    """NaN, None, 예외 발생 시 기본값으로 안전 반환 (연습 수동 미작업 데이터 pass)"""
    if val is None or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    """NaN, None, 예외 발생 시 정수 기본값 반환 (연습 수동 미작업 데이터 pass)"""
    if val is None or pd.isna(val):
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

STADIUM_RENAME_MAP = {
    "마산": "Masan",
    "창원": "NCDinosMajors",
    "고척": "Gocheok",
    "잠실": "Jamsil",
    "인천": "Incheon",
    "수원": "Suwon",
    "대전": "Daejeon",
    "대구": "DaeguPark",
    "부산": "Sajik",
    "광주": "Gwangju",

    "고양":"HeroesMinors",
    "이천(두산)": "DoosanMinors",
    "이천(LG)": "LGMinor",
    "강화":"SKFuturesPark",
    "익산":"IksanStadium",
    "서산":"HanwhaMinors",
    "경산": "SamsungMinor",
    "상동":"Gimhae",
    "함평":"Hampyeong",

    "문경":"Mungyeong",
    "울산":"Ulsan",
    "목동": "Mokdong",
    '청주': 'Cheongju',
    '포항': 'Pohang'
}

# ==============================================================================
# 7.5. DB (dinosdash.schedules) & CSV 경기 일정 자동 조회 및 5분 전 자동 시작 로직
# ==============================================================================
@st.cache_data(ttl=300)
def fetch_today_game_schedule_from_db_or_csv(stadium_name: str = "마산") -> Optional[Dict]:
    """
    1) dinosdash DB의 schedules 테이블에서 오늘 경기 시간/정보를 구장별 자동 조회
    2) DB 접속 불가 시 local CSV (gameinfo_cd_2026.csv) fallback 조회
    3) 선택 구장 경기 없을 시 기본 시작 시간 '마산(Masan)' 구장 일정에 조준
    """
    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    today_str_nodash = today_dt.strftime("%Y%m%d")

    target_code = STADIUM_RENAME_MAP.get(stadium_name, stadium_name)

    mysql_sec = st.secrets.get("mysql", {}) if hasattr(st, "secrets") else {}
    db_user = mysql_sec.get("user")
    db_pass = mysql_sec.get("password")
    db_port = int(mysql_sec.get("port", 3306))
    extra_hosts = mysql_sec.get("hosts", [])
    db_hosts = [h for h in [mysql_sec.get("host2"), mysql_sec.get("host")] + extra_hosts if h]

    if db_user and db_pass:
        for host in db_hosts:
            try:
                conn = pymysql.connect(
                    host=host,
                    port=db_port,
                    user=db_user,
                    password=db_pass,
                    db="dinosdash",
                    charset="utf8",
                    connect_timeout=2,
                    cursorclass=pymysql.cursors.DictCursor
                )
                with conn.cursor() as cursor:
                    cursor.execute("SHOW TABLES LIKE 'schedules'")
                    if cursor.fetchone():
                        query = "SELECT * FROM schedules WHERE (date = %s OR date = %s OR gdate = %s OR gdate = %s)"
                        cursor.execute(query, (today_str, today_str_nodash, today_str, today_str_nodash))
                        rows = cursor.fetchall()
                        conn.close()

                        if rows:
                            for r in rows:
                                std_raw = str(r.get("stadium") or r.get("Stadium") or r.get("stadium_name") or "")
                                std_mapped = STADIUM_RENAME_MAP.get(std_raw, std_raw)
                                if target_code.lower() in std_mapped.lower() or stadium_name.lower() in std_raw.lower():
                                    d_val = str(r.get("date") or r.get("gdate") or today_str)
                                    t_val = str(r.get("time") or r.get("gtime") or "18:30")
                                    away_val = str(r.get("away") or r.get("Away") or "Away")
                                    home_val = str(r.get("home") or r.get("Home") or "Home")
                                    return {
                                        "source": "DB (dinosdash.schedules)",
                                        "date": d_val,
                                        "time": t_val,
                                        "away": away_val,
                                        "home": home_val,
                                        "stadium": std_mapped,
                                        "info": f"{away_val} vs {home_val}"
                                    }
                            for r in rows:
                                std_raw = str(r.get("stadium") or r.get("Stadium") or r.get("stadium_name") or "")
                                std_mapped = STADIUM_RENAME_MAP.get(std_raw, std_raw)
                                if "masan" in std_mapped.lower() or "마산" in std_raw.lower():
                                    d_val = str(r.get("date") or r.get("gdate") or today_str)
                                    t_val = str(r.get("time") or r.get("gtime") or "18:30")
                                    away_val = str(r.get("away") or r.get("Away") or "Away")
                                    home_val = str(r.get("home") or r.get("Home") or "Home")
                                    return {
                                        "source": "DB (dinosdash.schedules)",
                                        "date": d_val,
                                        "time": t_val,
                                        "away": away_val,
                                        "home": home_val,
                                        "stadium": std_mapped,
                                        "info": f"{away_val} vs {home_val}"
                                    }
                            r0 = rows[0]
                            return {
                                "source": "DB (dinosdash.schedules)",
                                "date": str(r0.get("date") or r0.get("gdate") or today_str),
                                "time": str(r0.get("time") or r0.get("gtime") or "18:30"),
                                "away": str(r0.get("away") or r0.get("Away") or "Away"),
                                "home": str(r0.get("home") or r0.get("Home") or "Home"),
                                "stadium": r0.get("stadium") or stadium_name,
                                "info": f"{r0.get('away', 'Away')} vs {r0.get('home', 'Home')}"
                            }
            except Exception:
                continue

    # 2. Local CSV (gameinfo_cd_2026.csv) Fallback 시도
    csv_paths = [
        os.path.join(os.path.expanduser("~"), "data", "gameinfo_cd_2026.csv"),
        os.path.join(os.getcwd(), "gameinfo_cd_2026.csv"),
        os.path.join(os.path.dirname(__file__), "gameinfo_cd_2026.csv"),
        os.path.join(os.getcwd(), "data", "gameinfo_cd_2026.csv"),
    ]
    for csv_file in csv_paths:
        if os.path.exists(csv_file):
            for enc in ['cp949', 'utf-8', 'euc-kr']:
                try:
                    df_s = pd.read_csv(csv_file, encoding=enc)
                    if "Date" in df_s.columns and "Stadium" in df_s.columns:
                        # 구장명 rename 매핑 적용
                        df_s['Stadium_Mapped'] = df_s['Stadium'].replace(STADIUM_RENAME_MAP)
                        df_s["Date_str"] = df_s["Date"].astype(str).str.replace("-", "")
                        match_df = df_s[df_s["Date_str"] == today_str_nodash]

                        if not match_df.empty:
                            # 1) 선택된 구장에 매칭되는 당일 경기 검색
                            std_match = match_df[
                                (match_df["Stadium_Mapped"].astype(str).str.lower() == target_code.lower()) |
                                (match_df["Stadium"].astype(str).str.lower() == stadium_name.lower())
                            ]
                            # 2) 선택 구장 경기 없으면 기본 '마산(Masan)' 경기 검색
                            if std_match.empty:
                                std_match = match_df[match_df["Stadium_Mapped"].astype(str).str.lower() == "masan"]
                            # 3) 마산도 없으면 당일 첫 번째 경기 선택
                            if std_match.empty:
                                std_match = match_df

                            row = std_match.iloc[0]
                            t_val = str(row.get("Time", "18:30"))
                            if len(t_val) == 4 and t_val.isdigit():
                                t_fmt = f"{t_val[:2]}:{t_val[2:]}"
                            else:
                                t_fmt = t_val
                            return {
                                "source": "CSV (gameinfo_cd_2026.csv)",
                                "date": today_str,
                                "time": t_fmt,
                                "away": str(row.get("Away", "Away")),
                                "home": str(row.get("Home", "Home")),
                                "stadium": str(row.get("Stadium", stadium_name)),
                                "info": f"{row.get('Away', 'Away')} vs {row.get('Home', 'Home')}"
                            }
                except Exception:
                    continue
    return None

# ==============================================================================
# 8. 실시간 데이터 자동 갱신 및 사이드바 제어판 (구장 선택 기능 포함)
# ==============================================================================
model_engine = get_prediction_model()

st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/e/e0/logo.svg", width=170)
st.sidebar.title("⚡ 실시간 대시보드 제어")

user_id_str = st.session_state.get('user_id', 'User')
st.sidebar.caption(f"👤 로그인 계정: `{user_id_str}`")
if st.sidebar.button("🚪 로그아웃", key="logout_btn"):
    st.session_state['authenticated'] = False
    st.session_state.pop('user_id', None)
    st.rerun()

st.sidebar.subheader("🏟️ 구장 선택")
selected_stadium_name = st.sidebar.selectbox("구장 (Stadium)", list(STADIUM_URL_MAP.keys()), index=0)

if STADIUM_URL_MAP[selected_stadium_name] == "CUSTOM":
    custom_stadium_url = st.sidebar.text_input("구장 Trackman 웹주소 (URL) 입력", placeholder="http://trackman-changwon.iptime.org:1408")
    active_stadium_url = custom_stadium_url.strip()
else:
    active_stadium_url = STADIUM_URL_MAP[selected_stadium_name]
    if not active_stadium_url:
        st.sidebar.info(f"ℹ️ {selected_stadium_name} 구장은 기본 웹주소가 미등록되어 있습니다. 사용자 직접 URL 입력 모드를 선택하세요.")

data_source_mode = st.sidebar.radio(
    "🌐 데이터 소스 선택",
    ["🔴 실시간 연결", "📂 CSV 파일 업로드"],
    index=0
)

# ------------------------------------------------------------------------------
# DB / CSV 경기 일정 자동 수신 및 5분 전 자동 감지 스케줄러
# ------------------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.subheader("📅 경기 일정 & 5분 전 자동 시작")

auto_scheduler_enabled = st.sidebar.checkbox("⏰ 경기 5분 전 자동 시작 (Auto-Start)", value=True)

sched_stadium_name = selected_stadium_name if selected_stadium_name != "⚙️ 사용자 직접 URL 입력" else "마산"
game_sched_info = fetch_today_game_schedule_from_db_or_csv(sched_stadium_name)

auto_start_triggered = False
auto_start_time_display = ""

if game_sched_info:
    st.sidebar.markdown(f"**⚔️ {game_sched_info['info']}**")
    st.sidebar.caption(f"🏟️ 구장: `{game_sched_info['stadium']}` | 출처: `{game_sched_info['source']}`")
    st.sidebar.markdown(f"⏰ 경기 예정 시간: `{game_sched_info['time']}`")

    try:
        t_parts = game_sched_info['time'].split(":")
        g_h, g_m = int(t_parts[0]), int(t_parts[1])
        sched_dt = datetime.now().replace(hour=g_h, minute=g_m, second=0, microsecond=0)
        auto_start_dt = sched_dt - timedelta(minutes=5)
        auto_start_time_display = auto_start_dt.strftime("%H:%M")
        
        now_dt = datetime.now()
        if auto_scheduler_enabled:
            mins_left = 0
            if now_dt >= auto_start_dt:
                auto_start_triggered = True
                st.sidebar.success(f"🟢 [{auto_start_time_display}] 5분 전 도달! 라이브 감지 자동 가동 중")
                
                # 💡 5분 전 도달 시 ncdata 계정 자동 로그인 처리 & 웹 브라우저 창 자동 팝업
                if not st.session_state.get('authenticated'):
                    st.session_state['authenticated'] = True
                    st.session_state['user_id'] = 'ncdata'
                
                if not st.session_state.get('browser_opened', False):
                    st.session_state['browser_opened'] = True
                    try:
                        import webbrowser
                        webbrowser.open("http://localhost:8501/?autologin=true")
                    except Exception:
                        pass
            else:
                mins_left = int((auto_start_dt - now_dt).total_seconds() / 60)
                st.sidebar.warning(f"⏳ 경기 5분 전 자동 가동 대기 중\n(약 {mins_left}분 후 {auto_start_time_display} 시작)")

            # 💡 PowerShell 콘솔 터미널 (stdout) 출력
            mins_str = str(mins_left) if not auto_start_triggered else "0"
            log_key = f"{game_sched_info['info']}_{game_sched_info['time']}_{auto_start_triggered}_{mins_str}"
            if st.session_state.get('_last_ps_schedule_log') != log_key:
                st.session_state['_last_ps_schedule_log'] = log_key
                now_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print("\n" + "=" * 80, flush=True)
                print(f"[{now_time_str}] 📅 오늘 경기 일정: {game_sched_info['info']} ({game_sched_info['stadium']}구장)", flush=True)
                print(f"[{now_time_str}] ⏰ 경기 예정 시각: {game_sched_info['time']} | 5분 전 자동 시작 예정: {auto_start_time_display}", flush=True)
                if auto_start_triggered:
                    print(f"[{now_time_str}] 🟢 [상태] 경기 5분 전 도달! 라이브 감지 및 브라우저 창 자동 오픈 완료", flush=True)
                else:
                    print(f"[{now_time_str}] ⏳ [상태] 경기 5분 전 자동 가동 대기 중 (약 {mins_left}분 후 {auto_start_time_display} 가동 예정)", flush=True)
                print("=" * 80 + "\n", flush=True)
    except Exception:
        pass
else:
    st.sidebar.caption("ℹ️ 오늘 등록된 경기 일정 정보를 찾지 못했습니다.")
    if st.session_state.get('_last_ps_schedule_log') != "no_sched":
        st.session_state['_last_ps_schedule_log'] = "no_sched"
        now_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[{now_time_str}] ℹ️ 오늘 등록된 경기 일정 정보를 DB/CSV에서 찾지 못했습니다.\n", flush=True)

auto_refresh_enabled = False
refresh_interval = 5

if "🔴 실시간" in data_source_mode:
    st.sidebar.subheader("🔄 실시간 감지 (Auto-Refresh)")
    default_auto_ref = True if auto_start_triggered else True
    auto_refresh_enabled = st.sidebar.checkbox("⚡ 데이터 들어올 때마다 자동 반영", value=default_auto_ref)
    refresh_interval = st.sidebar.select_slider("감지 주기 (초)", options=[1, 2, 3, 5, 10], value=5)
    only_today_pitches = st.sidebar.checkbox("📅 오늘(Today) 경기 투구만 보기", value=False)

df = pd.DataFrame()
session_id_input = ""
is_live_active = False

if "🔴 실시간" in data_source_mode:
    auto_fetch = st.sidebar.checkbox("세션 ID 자동 감지", value=True)
    if auto_fetch or auto_start_triggered:
        live_id = fetch_live_session_id(active_stadium_url)
        if live_id:
            st.sidebar.success(f"🟢 [{selected_stadium_name}] 라이브 세션 감지: `{live_id[:8]}...`")
            session_id_input = live_id
            is_live_active = True
        else:
            if auto_start_triggered:
                st.sidebar.info(f"ℹ️ [{selected_stadium_name}] 5분 전 감지 시작됨 (세션 연결 대기 중...)")
            else:
                st.sidebar.info(f"ℹ️ [{selected_stadium_name}] 현재 진행 중인 라이브 경기 세션이 없습니다.")
    
    if not session_id_input:
        user_id_url = st.sidebar.text_input("Session ID / Review URL 직접 입력", placeholder="http://.../review/{session_id}")
        if user_id_url:
            session_id_input = extract_session_id_from_input(user_id_url)
            is_live_active = True

    if session_id_input:
        raw_json = fetch_review_data(active_stadium_url, session_id_input)
        if raw_json:
            df = parse_trackman_json(raw_json, model_engine, session_id=session_id_input)
            
            # 💡 동일 세션에 이전 날짜 데이터가 누적된 경우 '오늘(Today) 투구만 보기' 적용
            if only_today_pitches and not df.empty and "Date" in df.columns:
                today_str = datetime.now().strftime("%Y-%m-%d")
                df_today = df[df["Date"] == today_str]
                if not df_today.empty:
                    df = df_today

            sess_count_key = f"prev_count_{session_id_input}"
            if sess_count_key in st.session_state:
                prev_cnt = st.session_state[sess_count_key]
                if len(df) > prev_cnt:
                    added_cnt = len(df) - prev_cnt
                    st.toast(f"⚡ [{selected_stadium_name}] 신규 투구 수신! (+{added_cnt}구 / 총 {len(df)}구)", icon="⚾")
            st.session_state[sess_count_key] = len(df)
            st.sidebar.info(f"📊 [{selected_stadium_name}] 총 {len(df)}개 투구 데이터 수신")
        elif "live_cache_df" in st.session_state and not st.session_state["live_cache_df"].empty:
            # 💡 일시적 통신 순간 지연/타임아웃 발생 시 기존 캐시 데이터 유지 (F5 새로고침 없이 자동 복구)
            df = st.session_state["live_cache_df"]
            st.sidebar.caption(f"⚡ 네트워크 재시도 중... (기존 {len(df)}개 데이터 유지)")
        else:
            st.sidebar.warning("세션 데이터를 불러오지 못했습니다. 구장 웹주소 및 Session ID를 확인하세요.")

# ------------------------------------------------------------------------------
# 비활성 상태 안내 (실시간 감지는 중단 없이 계속 유지)
# ------------------------------------------------------------------------------
st.sidebar.markdown("---")
now_time = datetime.now()
if 'last_pitch_change_time' not in st.session_state:
    st.session_state['last_pitch_change_time'] = now_time

sess_tracker_key = f"last_pitch_count_{session_id_input}" if session_id_input else 'last_pitch_count_tracker'
if sess_tracker_key not in st.session_state:
    st.session_state[sess_tracker_key] = 0

current_pitch_cnt = len(df) if not df.empty else 0

# 신규 투구가 들어왔을 때 타임스탬프 갱신
if current_pitch_cnt > st.session_state[sess_tracker_key]:
    st.session_state[sess_tracker_key] = current_pitch_cnt
    st.session_state['last_pitch_change_time'] = now_time

# 타임스탬프 기반 비활성 시간 계산 (분 단위)
if current_pitch_cnt > 0:
    inactive_minutes = (now_time - st.session_state['last_pitch_change_time']).total_seconds() / 60.0
    st.sidebar.caption(f"⏱️ 마지막 투구 경과: `{inactive_minutes:.1f}분` 전")
    if inactive_minutes >= 10.0:
        st.sidebar.info(f"⏳ 이닝 교대/대기 중 (감지 루프 계속 실행 중: {inactive_minutes:.1f}분)")

else:
    uploaded_file = st.sidebar.file_uploader("Trackman CSV 데이터 파일 업로드", type=["csv"])
    if uploaded_file is not None:
        try:
            df_raw = pd.read_csv(uploaded_file)
            df = process_uploaded_csv(df_raw, model_engine)
            st.sidebar.success(f"CSV 파일 처리 완료: 총 {len(df)}개 투구")
        except Exception as e:
            st.sidebar.error(f"CSV 파일 로드 중 오류 발생: {e}")

col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title(f"⚾ [{selected_stadium_name}] 라이브 투구 실시간")

with col_h2:
    status_class = "status-live" if is_live_active else "status-offline"
    status_text = f"🟢 [{selected_stadium_name}] LIVE READY" if is_live_active else "⚪ GAME INACTIVE / CSV MODE"
    st.markdown(f"""
    <div style="text-align: right; padding-top: 10px;">
        <span class="status-badge {status_class}">{status_text}</span><br>
        <small style="color: gray;">업데이트: {datetime.now().strftime('%H:%M:%S')}</small>
    </div>
    """, unsafe_allow_html=True)

st.divider()

if df.empty:
    st.info(f"👈 사이드바에서 [{selected_stadium_name}] 구장 웹주소 및 실시간 Session ID를 확인하거나 CSV 파일을 업로드하세요.")
    st.stop()

filtered_df = df.copy()

# ==============================================================================
# 10. 3개 메인 시트 구현 (1. 투구별 / 2. graph / 3. log)
# ==============================================================================
sheet_tab1, sheet_tab2, sheet_tab3 = st.tabs(["📊 투구별", "📈 graph", "📋 game log"])

# ------------------------------------------------------------------------------
# 시트 1: 투구별 (Single Pitch Inspection & 3D/2D S-Zones)
# ------------------------------------------------------------------------------
with sheet_tab1:
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        st.subheader("⚾ 개별 투구 상세 지표 & 3D/2D 스트라이크 존 시각화")
    with col_t2:
        # 📸 상단 페이지 전체 원클릭 캡처/다운로드 버튼 (html2canvas)
        components.html("""
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
        <button onclick="captureFullPage()" style="background-color: #0b2545; color: white; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; font-size: 13px; cursor: pointer; float: right;">
            📸 현재 화면 전체 JPEG 저장
        </button>
        <script>
        function captureFullPage() {
            var el = window.parent.document.querySelector('.main .block-container');
            if (!el) el = window.parent.document.body;
            html2canvas(el, {
                scale: 2,
                useCORS: true,
                allowTaint: true,
                backgroundColor: '#ffffff'
            }).then(function(canvas) {
                var link = document.createElement('a');
                link.download = 'Pitch_Report.jpeg';
                link.href = canvas.toDataURL('image/jpeg', 0.95);
                link.click();
            });
        }
        </script>
        """, height=40)

    st.caption("💡 12개 수치 지표 카드, 3D 스트라이크 존, 2D 1x3 분할 존을 한 번에 확인하실 수 있습니다. (상단 📸 버튼 누르면 화면 전체 캡처 이미지 즉시 저장)")
    
    # 드롭다운 선택 옵션: 최신구가 맨 위에 오도록 역순(No.N -> No.1) 배치 & 한글 구종 명칭 포함
    reversed_filtered_df = filtered_df.iloc[::-1]
    pitch_options = [f"No.{row['PitchNo']} | {row['Pitcher']} vs {row['Batter']} ({row['Inning']}회, {row['RelSpeed']:.1f}km/h, {get_pitch_kor(row['PredictedPitchType'])})" for _, row in reversed_filtered_df.iterrows()]
    pitch_options.insert(0, "🔥 최신 투구 (자동 반영)")
    
    selected_pitch_str = st.selectbox("🎯 분석할 투구 선택", pitch_options, index=0)
    
    if "최신 투구" in selected_pitch_str:
        target_row = filtered_df.iloc[-1]
    else:
        pitch_no_val = int(selected_pitch_str.split("|")[0].replace("No.", "").strip())
        target_row = filtered_df[filtered_df["PitchNo"] == pitch_no_val].iloc[0]

    # 상단 2열 레이아웃: 좌측 12개 KPI 카드 (width 5) + 우측 3D Strike Zone (width 5) - 사진 1 스타일
    col_cards, col_3d = st.columns([5, 5])
    
    with col_cards:
        rel_speed_val = safe_num(target_row.get("RelSpeed", 0.0))
        spin_rate_val = safe_int(target_row.get("SpinRate", 0))
        spin_axis_val = safe_num(target_row.get("SpinAxis", 0.0))
        tilt_val = str(target_row.get("Tilt", "-")) if target_row.get("Tilt") and not pd.isna(target_row.get("Tilt")) else "-"
        ind_vert_val = safe_num(target_row.get("InducedVertBreak", 0.0))
        horz_brk_val = safe_num(target_row.get("HorzBreak", 0.0))
        exit_spd_val = safe_num(target_row.get("ExitSpeed", 0.0))
        angle_val = safe_num(target_row.get("Angle", 0.0))
        dist_val = safe_num(target_row.get("Distance", 0.0))

        r1_c1, r1_c2 = st.columns(2)
        with r1_c1:
            st.markdown(f'<div class="metric-card"><div class="metric-title">구속</div><div class="metric-value">{rel_speed_val:.1f} <small style="font-size:1rem;color:gray;">km/h</small></div></div>', unsafe_allow_html=True)
        with r1_c2:
            st.markdown(f'<div class="metric-card"><div class="metric-title">회전수</div><div class="metric-value">{spin_rate_val:,} <small style="font-size:1rem;color:gray;">rpm</small></div></div>', unsafe_allow_html=True)

        r2_c1, r2_c2 = st.columns(2)
        with r2_c1:
            st.markdown(f'<div class="metric-card"><div class="metric-title">회전축</div><div class="metric-value">{round(spin_axis_val):.0f}°</div></div>', unsafe_allow_html=True)
        with r2_c2:
            st.markdown(f'<div class="metric-card"><div class="metric-title">회전 방향 (Tilt)</div><div class="metric-value">{tilt_val}</div></div>', unsafe_allow_html=True)

        r3_c1, r3_c2 = st.columns(2)
        with r3_c1:
            st.markdown(f'<div class="metric-card"><div class="metric-title">상하 무브</div><div class="metric-value">{round(ind_vert_val):.0f} <small style="font-size:1rem;color:gray;">cm</small></div></div>', unsafe_allow_html=True)
        with r3_c2:
            st.markdown(f'<div class="metric-card"><div class="metric-title">좌우 무브</div><div class="metric-value">{round(horz_brk_val):.0f} <small style="font-size:1rem;color:gray;">cm</small></div></div>', unsafe_allow_html=True)

        r4_c1, r4_c2, r4_c3 = st.columns(3)
        with r4_c1:
            st.markdown(f'<div class="metric-card"><div class="metric-title">상하 타점</div><div class="metric-value">{format_meter(target_row.get("RelHeight", 0.0)):.2f} <small style="font-size:0.9rem;color:gray;">m</small></div></div>', unsafe_allow_html=True)
        with r4_c2:
            st.markdown(f'<div class="metric-card"><div class="metric-title">좌우 타점</div><div class="metric-value">{format_meter(target_row.get("RelSide", 0.0)):.2f} <small style="font-size:0.9rem;color:gray;">m</small></div></div>', unsafe_allow_html=True)
        with r4_c3:
            st.markdown(f'<div class="metric-card"><div class="metric-title">익스텐션</div><div class="metric-value">{format_meter(target_row.get("Extension", 0.0)):.2f} <small style="font-size:0.9rem;color:gray;">m</small></div></div>', unsafe_allow_html=True)

        r5_c1, r5_c2, r5_c3 = st.columns(3)
        with r5_c1:
            st.markdown(f'<div class="metric-card"><div class="metric-title">타구 속도</div><div class="metric-value">{exit_spd_val:.0f} <small style="font-size:0.9rem;color:gray;">km/h</small></div></div>', unsafe_allow_html=True)
        with r5_c2:
            st.markdown(f'<div class="metric-card"><div class="metric-title">타구 각도</div><div class="metric-value">{round(angle_val):.0f}°</div></div>', unsafe_allow_html=True)
        with r5_c3:
            st.markdown(f'<div class="metric-card"><div class="metric-title">비거리</div><div class="metric-value">{dist_val:.0f} <small style="font-size:0.9rem;color:gray;">m</small></div></div>', unsafe_allow_html=True)

    with col_3d:
        col_3d_chart, col_3d_legend = st.columns([4.2, 0.8])
        with col_3d_chart:
            # 🧊 1. 3D 스트라이크 존 (첨부 사진 1 스타일: 3개 수직 빨간 단면 + 3D 공 궤적 파이프)
            fig_3d, is_strike_3d = create_3d_strike_zone(target_row)
            st.plotly_chart(fig_3d, use_container_width=True, config=PLOTLY_JPEG_CONFIG)

            if is_strike_3d:
                st.markdown('<div style="display:inline-block;border:2px solid #2ECC71;color:#2ECC71;padding:4px 16px;border-radius:6px;font-weight:bold;font-size:1.1rem;background-color:#1e382b;margin-bottom:10px;">IN (STRIKE)</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="display:inline-block;border:2px solid #FF3333;color:#FF3333;padding:4px 16px;border-radius:6px;font-weight:bold;font-size:1.1rem;background-color:#3a1e1e;margin-bottom:10px;">OUT (BALL)</div>', unsafe_allow_html=True)
        
        with col_3d_legend:
            st.markdown("""
            <div style="background-color: #121212; border: 1px solid #2d3748; border-radius: 8px; padding: 8px 6px; margin-top: 45px; font-size: 0.75rem; color: #e0e0e0;">
                <div style="font-weight: bold; color: #00a6fb; margin-bottom: 6px; border-bottom: 1px solid #2d3748; padding-bottom: 4px; font-size: 0.78rem; text-align: center;">구종</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#FF0000;">●</span> 직구</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#E67E22;">▼</span> 투심</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#FF8099;">★</span> 커터</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#F1C40F;">⧓</span> 슬라</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#95A5A6;">▲</span> 커브</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#E040FB;">+</span> 스위</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#2ECC71;">◆</span> 체인</div>
                <div style="margin-bottom: 5px; white-space: nowrap;"><span style="color:#2980B9;">■</span> 포크</div>
                <div style="margin-bottom: 4px; white-space: nowrap;"><span style="color:#5D6D7E;">✖</span> 너클</div>
                <div style="margin-bottom: 4px; white-space: nowrap;"><span style="color:#BDC3C7;">●</span> 미확</div>
            </div>
            """, unsafe_allow_html=True)

    # 📐 2. 2D S-Zone 3개 차트 1x3 분할 하단 배치 (첨부 사진 2 스타일)
    st.divider()
    st.markdown("#### 📐 2D S-Zone (Front / Middle / Back)")
    fig_front, fig_mid, fig_back = create_2d_szone_plots(target_row)
    
    col_2d_1, col_2d_2, col_2d_3 = st.columns(3)
    with col_2d_1:
        st.plotly_chart(fig_front, use_container_width=True, config=PLOTLY_JPEG_CONFIG)
    with col_2d_2:
        st.plotly_chart(fig_mid, use_container_width=True, config=PLOTLY_JPEG_CONFIG)
    with col_2d_3:
        st.plotly_chart(fig_back, use_container_width=True, config=PLOTLY_JPEG_CONFIG)

# ------------------------------------------------------------------------------
# 시트 2: graph (Sequential Metric Plot & Dedicated Data Filters)
# ------------------------------------------------------------------------------
with sheet_tab2:
    st.subheader("📈 동적 투구 번호별 메트릭 시퀀스 그래프")
    st.caption("💡 그래프 우측 상단의 카메라인 아이콘(📸)을 누르면 고해상도 .JPEG 이미지로 바로 저장됩니다.")
    
    col_g1, col_g2 = st.columns([1.2, 3.8])
    with col_g1:
        st.markdown("##### ⚙️ 측정값 & 구종 설정")
        metric_choice = st.radio(
            "선택 측정값",
            ["구속", "회전수", "상하무브", "좌우무브", "상하타점", "익스텐션", "타구속도", "비거리"],
            index=0
        )
        color_by_mode = st.radio("색상 구분 기준", ["예측구종", "태깅구종"], index=0)

        st.markdown("---")
        st.markdown("##### 🔍 그래프 전용 데이터 필터")
        
        g_pitcher_options = ["전체"] + sorted(df["Pitcher"].dropna().unique().tolist())
        g_selected_pitcher = st.selectbox("Pitcher (투수)", g_pitcher_options, key="g_pitcher")
        
        g_batter_options = ["전체"] + sorted(df["Batter"].dropna().unique().tolist())
        g_selected_batter = st.selectbox("Batter (타자)", g_batter_options, key="g_batter")
        
        g_inning_options = ["전체"] + sorted(df["Inning"].dropna().unique().tolist())
        g_selected_inning = st.selectbox("Inning (이닝)", g_inning_options, key="g_inning")
        
        # 태깅구종 체크박스 필터
        st.markdown("**🏷️ 태깅구종**")
        g_tagged_types = sorted(df["TaggedPitchType"].dropna().unique().tolist())
        if "g_chk_tagged_all" not in st.session_state:
            st.session_state["g_chk_tagged_all"] = True
        for pt in g_tagged_types:
            if f"g_chk_tagged_{pt}" not in st.session_state:
                st.session_state[f"g_chk_tagged_{pt}"] = True

        def g_on_change_tagged_all():
            nv = st.session_state["g_chk_tagged_all"]
            for pt in g_tagged_types:
                st.session_state[f"g_chk_tagged_{pt}"] = nv

        def g_on_change_tagged_item():
            st.session_state["g_chk_tagged_all"] = all(st.session_state.get(f"g_chk_tagged_{pt}", False) for pt in g_tagged_types)

        st.checkbox("☑ (전체)", key="g_chk_tagged_all", on_change=g_on_change_tagged_all)
        g_selected_tagged = []
        for pt in g_tagged_types:
            pt_ko = get_pitch_kor(pt)
            label = f"{pt_ko} ({pt})" if pt_ko != pt else pt
            if st.checkbox(label, key=f"g_chk_tagged_{pt}", on_change=g_on_change_tagged_item):
                g_selected_tagged.append(pt)

        # 예측구종 체크박스 필터
        st.markdown("**🤖 예측구종**")
        g_pred_types = sorted(df["PredictedPitchType"].dropna().unique().tolist()) if "PredictedPitchType" in df.columns else []
        if "g_chk_pred_all" not in st.session_state:
            st.session_state["g_chk_pred_all"] = True
        for pt in g_pred_types:
            if f"g_chk_pred_{pt}" not in st.session_state:
                st.session_state[f"g_chk_pred_{pt}"] = True

        def g_on_change_pred_all():
            nv = st.session_state["g_chk_pred_all"]
            for pt in g_pred_types:
                st.session_state[f"g_chk_pred_{pt}"] = nv

        def g_on_change_pred_item():
            st.session_state["g_chk_pred_all"] = all(st.session_state.get(f"g_chk_pred_{pt}", False) for pt in g_pred_types)

        st.checkbox("☑ (전체)", key="g_chk_pred_all", on_change=g_on_change_pred_all)
        g_selected_pred = []
        for pt in g_pred_types:
            pt_ko = get_pitch_kor(pt)
            label = f"{pt_ko} ({pt})" if pt_ko != pt else pt
            if st.checkbox(label, key=f"g_chk_pred_{pt}", on_change=g_on_change_pred_item):
                g_selected_pred.append(pt)

    # 그래프 전용 필터 적용
    filtered_df_graph = df.copy()
    if g_selected_pitcher != "전체":
        filtered_df_graph = filtered_df_graph[filtered_df_graph["Pitcher"] == g_selected_pitcher]
    if g_selected_batter != "전체":
        filtered_df_graph = filtered_df_graph[filtered_df_graph["Batter"] == g_selected_batter]
    if g_selected_inning != "전체":
        filtered_df_graph = filtered_df_graph[filtered_df_graph["Inning"] == g_selected_inning]

    filtered_df_graph = filtered_df_graph[filtered_df_graph["TaggedPitchType"].isin(g_selected_tagged)]
    if "PredictedPitchType" in filtered_df_graph.columns:
        filtered_df_graph = filtered_df_graph[filtered_df_graph["PredictedPitchType"].isin(g_selected_pred)]

    metric_col_map = {
        "구속": "RelSpeed",
        "회전수": "SpinRate",
        "상하무브": "InducedVertBreak",
        "좌우무브": "HorzBreak",
        "상하타점": "RelHeight",
        "익스텐션": "Extension",
        "타구속도": "ExitSpeed",
        "비거리": "Distance"
    }
    
    target_metric_col = metric_col_map[metric_choice]
    
    filtered_df_graph["PredictedPitchType_KO"] = filtered_df_graph["PredictedPitchType"].apply(get_pitch_kor)
    filtered_df_graph["TaggedPitchType_KO"] = filtered_df_graph["TaggedPitchType"].apply(get_pitch_kor)
    color_col = "PredictedPitchType_KO" if color_by_mode == "예측구종" else "TaggedPitchType_KO"

    with col_g2:
        fig_graph = px.scatter(
            filtered_df_graph,
            x="PitchNo",
            y=target_metric_col,
            color=color_col,
            symbol=color_col,
            color_discrete_map=PITCH_COLOR_MAP,
            symbol_map=PITCH_SYMBOL_MAP,
            hover_data=["Pitcher", "Batter", "Inning", "PitchCall", "PlayResult"],
            title=f"동적번호 / {metric_choice} 변화",
            labels={"PitchNo": "동적번호 / No", target_metric_col: metric_choice, color_col: "구종"}
        )

        avg_val = filtered_df_graph[target_metric_col].mean()
        fig_graph.add_hline(
            y=avg_val,
            line_dash="solid",
            line_color="gray",
            annotation_text=f"평균 {metric_choice}: {avg_val:.1f}",
            annotation_position="top left"
        )
        
        fig_graph.update_layout(
            height=580,
            xaxis=dict(showgrid=True, gridcolor='#e0e0e0', tickmode='linear', dtick=5),
            yaxis=dict(showgrid=True, gridcolor='#e0e0e0'),
            legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02)
        )
        st.plotly_chart(fig_graph, use_container_width=True, config=PLOTLY_JPEG_CONFIG)

# ------------------------------------------------------------------------------
# 시트 3: log (Detailed Pitch Log Table)
# ------------------------------------------------------------------------------
with sheet_tab3:
    st.subheader("📋 전체 투구 데이터 로그 (log)")
    
    log_columns = {
        "PitchNo": "No",
        "Pitcher": "Pitcher",
        "Batter": "Batter",
        "Inning": "In.",
        "Outs": "O.",
        "Balls": "B",
        "Strikes": "S",
        "PitchCall": "PitchCall",
        "PlayResult": "PlayResult",
        "TaggedPitchType": "태깅구종",
        "PredictedPitchType": "예측구종",
        "RelSpeed": "구속",
        "SpinRate": "회전수",
        "SpinAxis": "회전축",
        "Tilt": "Tilt",
        "InducedVertBreak": "무브_상하",
        "HorzBreak": "무브_좌우",
        "RelHeight": "타점_상하",
        "RelSide": "타점_좌우",
        "Extension": "익스",
        "ExitSpeed": "타구속도",
        "Angle": "타구각도",
        "Distance": "비거리"
    }

    available_log_cols = {k: v for k, v in log_columns.items() if k in filtered_df.columns}
    display_df = filtered_df[list(available_log_cols.keys())].rename(columns=available_log_cols)
    
    st.dataframe(
        display_df,
        use_container_width=True,
        height=520,
        column_config={
            "구속": st.column_config.NumberColumn(format="%.1f km/h"),
            "회전수": st.column_config.NumberColumn(format="%d rpm"),
            "무브_상하": st.column_config.NumberColumn(format="%.1f cm"),
            "무브_좌우": st.column_config.NumberColumn(format="%.1f cm"),
        }
    )

    st.markdown("##### 📥 필터링된 게임로그 CSV 데이터 다운로드")
    st_prefix = STADIUM_RENAME_MAP.get(selected_stadium_name, selected_stadium_name)
    if st_prefix == "CUSTOM" or not st_prefix:
        st_prefix = "Live"
    csv_filename = f"{st_prefix}_gamelog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    st.download_button(
        label=f"📥 {selected_stadium_name} 게임로그 CSV 다운로드 (CP949)",
        data=filtered_df.to_csv(index=False).encode('cp949', errors='ignore'),
        file_name=csv_filename,
        mime="text/csv",
        use_container_width=True
    )

# ==============================================================================
# 11. 실시간 자동 갱신 루프
# ==============================================================================
if auto_refresh_enabled and is_live_active:
    time.sleep(refresh_interval)
    st.rerun()
