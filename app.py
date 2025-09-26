from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import firebase_admin
from firebase_admin import credentials, firestore, storage, auth
from firebase_admin.exceptions import FirebaseError
import os
from datetime import datetime
import logging
import requests
from health_report_module import analyze_health_report
from google.cloud.firestore import SERVER_TIMESTAMP
from dotenv import load_dotenv
import json
import re

def extract_json_from_response(text):
    """
    從 Gemini 的回應中提取 JSON 內容
    處理包含 ```json 代碼塊的情況
    """
    if not text:
        return None
    
    # 嘗試直接解析 JSON
    try:
        return json.loads(text)
    except:
        pass
    
    # 嘗試從 markdown 代碼塊中提取 JSON
    json_pattern = r'```json\s*(\{.*?\})\s*```'
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass
    
    # 嘗試從文字中找到 JSON 對象
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    
    return None

# 載入 .env 檔案
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key")  # 從 .env 載入或使用預設值
logging.basicConfig(level=logging.DEBUG)

# 初始化 Firebase
try:
    cred = credentials.Certificate("firebase_credentials/service_account.json")
    firebase_admin.initialize_app(
        cred, {"storageBucket": "health-emo-cat-guide.firebasestorage.app"}
    )
    logging.debug("Firebase initialized successfully")
except FileNotFoundError as e:
    logging.error(f"Firebase credential file not found: {e}")
    raise
except ValueError as e:
    logging.error(f"Firebase initialization failed: {e}")
    raise

db = firestore.client()
try:
    bucket = storage.bucket()
    logging.debug(f"Storage bucket initialized: {bucket.name}")
except Exception as e:
    logging.error(f"Storage bucket initialization failed: {str(e)}")
    raise

# Gemini API key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY not found in .env file")
    raise ValueError("GEMINI_API_KEY is required")

# 🟢 修改：啟動時列印路由表（Flask 3 不支援 before_first_request，故保留註解）  
# @app.before_first_request
# def _print_url_map():
#    logging.debug("URL Map:\n" + "\n".join([str(r) for r in app.url_map.iter_rules()]))

# 首頁
@app.route("/")
def home():
    is_logged_in = "user_id" in session
    return render_template("home.html", is_logged_in=is_logged_in)

# 註冊
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        session.pop("_flashes", None)

    if request.method == "POST":
        logging.debug(f"Received POST request with form data: {request.form}")
        email = request.form.get("email")
        password = request.form.get("password")
        # 🟢 修改開始：新增生理性別欄位
        gender = request.form.get("gender")
        logging.debug(
            f"Parsed form data: email={email}, password={'*' * len(password) if password else None}, gender={gender}"
        )

        if not email or not password or not gender:
            flash("請輸入電子郵件、密碼和生理性別！", "error")
            logging.warning("Missing email, password, or gender in form submission")
            return render_template("register.html", error="請輸入電子郵件、密碼和生理性別")
        # 🟢 修改結束
        try:
            user = auth.create_user(email=email, password=password)
            logging.debug(f"User created: uid={user.uid}, email={email}")
            db.collection("users").document(user.uid).set(
                {
                    "email": email,
                    # 🟢 修改開始：Firestore 儲存生理性別
                    "gender": gender,
                    # 🟢 修改結束
                    "created_at": SERVER_TIMESTAMP,
                    "last_login": None,
                }
            )
            logging.debug(f"User document created in Firestore for uid: {user.uid}")
            session["user_id"] = user.uid
            flash("註冊成功！請上傳健康報告。", "success")
            return redirect(url_for("upload_health"))
        except FirebaseError as e:
            error_message = str(e)
            logging.error(f"Firebase error during registration: {error_message}")
            flash(f"註冊失敗：{error_message}", "error")
            return render_template("register.html", error=f"註冊失敗：{error_message}")
        except Exception as e:
            logging.error(f"Unexpected error during registration: {str(e)}")
            flash(f"註冊失敗：{str(e)}", "error")
            return render_template("register.html", error=f"註冊失敗：{str(e)}")

    return render_template("register.html")

# 登入
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        session.pop("_flashes", None)

    if request.method == "POST":
        logging.debug(f"Received POST request with form data: {request.form}")
        email = request.form.get("email")
        password = request.form.get("password")
        logging.debug(
            f"Login attempt: email={email}, password={'*' * len(password) if password else None}"
        )

        if not email or not password:
            flash("請輸入電子郵件和密碼！", "error")
            logging.warning("Missing email or password in login submission")
            return render_template("login.html", error="請輸入電子郵件和密碼")

        try:
            user = auth.get_user_by_email(email)
            db.collection("users").document(user.uid).update(
                {"last_login": SERVER_TIMESTAMP}
            )
            logging.debug(f"User login updated in Firestore for uid: {user.uid}")
            session["user_id"] = user.uid
            flash("登入成功！", "success")
            return redirect(url_for("home"))
        except FirebaseError as e:
            error_message = str(e)
            logging.error(f"Login failed: {error_message}")
            flash(f"登入失敗：{error_message}", "error")
            return render_template("login.html", error=f"登入失敗：{error_message}")
        except Exception as e:
            logging.error(f"Unexpected login error: {str(e)}")
            flash(f"登入失敗：{str(e)}", "error")
            return render_template("login.html", error=f"登入失敗：{str(e)}")

    return render_template("login.html")

# 登出
@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("_flashes", None)
    flash("已成功登出！", "success")
    return redirect(url_for("home"))

# 九宮格貓咪頁面
@app.route("/featured_cats")
def featured_cats():
    is_logged_in = "user_id" in session
    return render_template("featured_cats.html", is_logged_in=is_logged_in)

# 上傳健康報告
@app.route("/upload_health", methods=["GET", "POST"])
def upload_health():
    if "user_id" not in session:
        flash("請先登錄！", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    logging.debug(f"Current user_id from session: {user_id}")

    # 🟢 修改開始：取得使用者生理性別
    user_gender = None
    try:
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            flash("找不到使用者資料！", "error")
            logging.warning(f"User document not found for uid: {user_id}")
            return redirect(url_for("register"))
        user_data = user_doc.to_dict()
        user_gender = user_data.get("gender")
        if not user_gender:
            flash("請先完成註冊並提供生理性別資料！", "error")
            logging.warning(f"User gender missing for uid: {user_id}")
            return redirect(url_for("register"))
        logging.debug(f"Retrieved user gender from Firestore: {user_gender}")
    except Exception as e:
        logging.error(f"Failed to retrieve user gender: {str(e)}")
        flash(f"取得使用者資料失敗：{str(e)}", "error")
        return redirect(url_for("login"))
    # 🟢 修改結束

    # 🟢 修改開始：已有健檢報告時自動導向心理測驗
    reupload_requested = request.args.get("reupload") == "1"
    try:
        existing_reports = list(
            db.collection("health_reports")
            .where("user_uid", "==", user_id)
            .limit(1)
            .stream()
        )
    except Exception as e:
        logging.error(f"Failed to check existing health reports: {str(e)}")
        existing_reports = []

    has_existing_report = bool(existing_reports)

    auto_redirect = False
    if has_existing_report and not reupload_requested and request.method == "GET":
        logging.debug("Existing health report found; enabling auto redirect to psychology_test")
        auto_redirect = True
    # 🟢 修改結束

    if request.method == "POST":
        if "health_report" not in request.files:
            flash("未選擇檔案！", "error")
            return redirect(request.url)

        file = request.files["health_report"]
        if file.filename == "":
            flash("未選擇檔案！", "error")
            return redirect(request.url)

        logging.debug(
            f"Received POST request with form data: {request.form}, files: {request.files}"
        )

        # 檢查檔案類型
        is_image = file.mimetype in ["image/jpeg", "image/png"]
        is_pdf = file.mimetype == "application/pdf"
        if not (is_image or is_pdf):
            flash("僅支援 JPEG、PNG 或 PDF 檔案！", "error")
            return redirect(request.url)

        # 上傳檔案到 Firebase Storage
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{file.filename}"
        blob_path = f"health_reports/{user_id}/{filename}"
        logging.debug(f"Uploading file: {file.filename}")
        logging.debug(f"Uploading to Storage: {blob_path}")

        blob = bucket.blob(blob_path)
        blob.upload_from_file(file, content_type=file.mimetype)
        #blob.make_public()
        file_url = blob.public_url
        logging.debug(f"File uploaded successfully to Storage: {file_url}")

        # 分析健康報告
        logging.debug("Starting health report analysis...")
        try:
            file.seek(0)  # 重置檔案指針
            file_data = file.read()
            file_type = "image" if is_image else "pdf"
            analysis_data, health_score, health_warnings = analyze_health_report(
                file_data, user_id, file_type, gender=user_gender  # 🟢 修改：將生理性別傳遞至分析模組
            )
            logging.debug(
                f"Analysis result - data: {analysis_data is not None}, score: {health_score}, warnings: {len(health_warnings)}"
            )
            if not analysis_data:
                logging.warning("Health report analysis returned no data")
                flash("健康報告分析失敗，請確保檔案包含清晰數據！", "warning")
        except Exception as analysis_e:
            logging.error(f"Health report analysis failed: {str(analysis_e)}")
            flash(f"健康報告分析失敗：{str(analysis_e)}", "warning")
            analysis_data, health_score, health_warnings = None, 0, []

        # 準備 Firestore 文檔
        health_report_doc = {
            "user_uid": user_id,
            "report_date": datetime.now().strftime("%Y/%m/%d"),
            "filename": file.filename,
            "url": file_url,
            "file_type": file_type,
            "created_at": SERVER_TIMESTAMP,
        }
        if analysis_data:
            health_report_doc.update(
                {
                    "vital_stats": analysis_data.get("vital_stats", {}),
                    "health_score": health_score,
                    "health_warnings": health_warnings,
                }
            )
            logging.debug(
                f"Adding analysis data to doc: score={health_score}, warnings={health_warnings}"
            )

        # 儲存到 Firestore
        doc_ref = db.collection("health_reports").add(health_report_doc)
        report_id = doc_ref[1].id
        logging.debug(
            f"Health report SAVED to Firestore for user: {user_id}, report_id: {report_id}"
        )
        logging.debug(f"Saved document content: {health_report_doc}")

        # 驗證寫入
        saved_doc = db.collection("health_reports").document(report_id).get()
        if saved_doc.exists:
            logging.debug(
                f"Firestore write verified - document exists: {saved_doc.to_dict()}"
            )
        else:
            logging.error("Firestore write failed - document does not exist")

        flash(
            f"上傳成功！健康分數：{health_score}，警告：{'; '.join(health_warnings) if health_warnings else '無'}",
            "success",
        )
        return redirect(url_for("psychology_test"))

    return render_template(
        "upload_health.html",
        force_reupload=reupload_requested,
        has_existing_report=has_existing_report,
        auto_redirect=auto_redirect,
        psychology_url=url_for("psychology_test"),
    )

# 心理測驗
@app.route("/psychology_test", methods=["GET", "POST"])  # 🟢 修改：允許 POST 以處理心理測驗提交
def psychology_test():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    try:
        # 🟢 修改：改為查詢頂層 health_reports 並依 user_uid 過濾，避免找不到文件
        health_reports = list(
            db.collection("health_reports")
              .where("user_uid", "==", user_id)
              .stream()
        )  # 🟢 修改：原本是 users/{uid}/health_reports
        logging.debug(
            f"Psychology test check - existing reports: {len(health_reports)}"
        )
        if not health_reports:
            flash("請先上傳健康報告！", "error")
            return redirect(url_for("upload_health"))
    except Exception as e:
        logging.error(f"Error checking health reports: {str(e)}")
        flash(f"檢查健康報告失敗：{str(e)}", "error")
        return redirect(url_for("upload_health"))

    # 🟢 修改開始：支援心理測驗表單提交流程
    if request.method == "GET":
        session.pop("_flashes", None)
        return render_template("psychology_test.html", is_logged_in=True)

    question1 = request.form.get("question1")
    question2 = request.form.get("question2")
    if not question1 or not question2:
        flash("請回答所有問題！", "error")
        return render_template(
            "psychology_test.html", error="請回答所有問題", is_logged_in=True
        )

    try:
        db.collection("users").document(user_id).collection("psychology_tests").add(
            {
                "question1": question1,
                "question2": question2,
                "submit_time": SERVER_TIMESTAMP,
            }
        )
        logging.debug(f"Psychology test saved to Firestore for uid: {user_id}")
        flash("測驗提交成功！請生成貓咪圖卡。", "success")
        return redirect(url_for("generate_card"))
    except Exception as e:
        logging.error(f"Psychology test error: {str(e)}")
        flash(f"提交失敗：{str(e)}", "error")
        return render_template(
            "psychology_test.html", error=f"提交失敗：{str(e)}", is_logged_in=True
        )
    # 🟢 修改結束

# 聊天 API 端點（代理 Gemini API）
@app.route("/chat_api", methods=["POST"])
def chat_api():
    if "user_id" not in session:
        return jsonify({"error": "未登入"}), 401

    data = request.get_json()
    if not data or "conversationHistory" not in data or "systemInstruction" not in data:
        logging.error(f"Invalid request data: {data}")
        return jsonify({"error": "缺少必要的參數"}), 400

    try:
        logging.debug(f"Received conversationHistory: {data['conversationHistory']}")

        # 格式化 Gemini API 的請求內容
        contents = []
        for msg in data["conversationHistory"]:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])
            if not parts:
                logging.warning(f"Empty parts in message: {msg}")
                continue
            text = parts[0].get("text", "") if isinstance(parts[0], dict) else str(parts[0])
            if not text:
                logging.warning(f"Empty text in message: {msg}")
                continue
            gemini_role = "model" if role == "model" else "user"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": text}]
            })

        if not contents:
            return jsonify({"error": "conversationHistory 為空或格式無效"}), 400

        # 插入系統指令作為第一個使用者訊息
        if data["systemInstruction"]:
            contents.insert(0, {
                "role": "user",
                "parts": [{"text": data["systemInstruction"]}]
            })

        # 呼叫 Gemini API - 嘗試不同的模型端點
        headers = {"Content-Type": "application/json"}
        payload = {"contents": contents}
        
        # 嘗試不同的模型端點
        model_endpoints = [
            f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
        ]
        
        response = None
        for url in model_endpoints:
            try:
                logging.debug(f"Trying endpoint: {url}")
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code == 200:
                    logging.debug(f"Success with endpoint: {url}")
                    break
                else:
                    logging.warning(f"Endpoint failed with status {response.status_code}: {url}")
                    if response.text:
                        logging.warning(f"Response text: {response.text[:200]}")
            except Exception as e:
                logging.warning(f"Endpoint error: {url}, {str(e)}")
                continue
        
        if not response or response.status_code != 200:
            logging.error("All Gemini API endpoints failed")
            return jsonify({"nextPrompt": "AI 助手暫時無法回應，請稍後再試。"}), 500
        
        # 處理 Gemini API 回應
        response_data = response.json()
        logging.debug(f"Gemini API response: {response_data}")
        
        if not response_data.get("candidates") or not response_data["candidates"][0].get("content", {}).get("parts"):
            logging.error("Gemini API returned invalid response structure")
            return jsonify({"nextPrompt": "無法取得回應，請稍後再試。"}), 500
        
        # 提取回應內容
        reply = response_data["candidates"][0]["content"]["parts"][0]["text"]
        logging.debug(f"Raw reply: {reply}")
        
        # 嘗試解析 JSON 回應
        parsed_json = extract_json_from_response(reply)
        if parsed_json and isinstance(parsed_json, dict):
            logging.debug(f"Successfully parsed JSON: {parsed_json}")
            # 確保回應包含必要的字段
            if "nextPrompt" in parsed_json:
                return jsonify(parsed_json)
            else:
                return jsonify({"nextPrompt": parsed_json.get("nextPrompt", reply)})
        else:
            # 如果無法解析 JSON，則返回原文字作為 nextPrompt
            logging.warning(f"Could not parse JSON from reply, returning as plain text: {reply}")
            return jsonify({"nextPrompt": reply})
    
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response:
            error_msg += f", response: {e.response.text[:200]}"
        logging.error(f"Gemini API request failed: {error_msg}")
        return jsonify({"nextPrompt": "網路好像不太穩定，請檢查連線後再試一次。"}), 500
    except Exception as e:
        logging.error(f"Unexpected error in chat_api: {str(e)}, data: {data}")
        return jsonify({"error": f"伺服器錯誤：{str(e)}"}), 500

# 報告 API 端點（代理 Gemini API）
@app.route("/report_api", methods=["POST"])
def report_api():
    if "user_id" not in session:
        return jsonify({"error": "未登入"}), 401

    data = request.get_json()
    if not data or "conversationHistory" not in data or "systemInstruction" not in data:
        logging.error(f"Invalid request data: {data}")
        return jsonify({"error": "缺少必要的參數"}), 400

    try:
        logging.debug(f"Received conversationHistory for report: {len(data['conversationHistory'])} messages")

        # 格式化 Gemini API 的請求內容
        contents = []
        for msg in data["conversationHistory"]:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])
            if not parts:
                logging.warning(f"Empty parts in message for report: {msg}")
                continue
            text = parts[0].get("text", "") if isinstance(parts[0], dict) else str(parts[0])
            if not text:
                logging.warning(f"Empty text in message for report: {msg}")
                continue
            gemini_role = "model" if role == "model" else "user"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": text}]
            })

        if not contents:
            return jsonify({"error": "conversationHistory 為空或格式無效"}), 400

        # 插入系統指令作為第一個使用者訊息
        if data["systemInstruction"]:
            contents.insert(0, {
                "role": "user",
                "parts": [{"text": data["systemInstruction"]}]
            })

        # 呼叫 Gemini API - 使用相同的多端點嘗試策略
        headers = {"Content-Type": "application/json"}
        payload = {"contents": contents}
        
        model_endpoints = [
            f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
        ]
        
        response = None
        for url in model_endpoints:
            try:
                logging.debug(f"Trying report endpoint: {url}")
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code == 200:
                    logging.debug(f"Success with report endpoint: {url}")
                    break
                else:
                    logging.warning(f"Report endpoint failed with status {response.status_code}: {url}")
                    if response.text:
                        logging.warning(f"Report response text: {response.text[:200]}")
            except Exception as e:
                logging.warning(f"Report endpoint error: {url}, {str(e)}")
                continue
        
        if not response or response.status_code != 200:
            logging.error("All Gemini API report endpoints failed")
            return jsonify({"summary": "無法生成報告，請稍後再試。"}), 500
        
        # 處理 Gemini API 回應
        response_data = response.json()
        logging.debug(f"Gemini API report response: {response_data}")
        
        # 🟢 修改：容忍沒有 candidates 或沒有 parts 的情況，回傳安全的 fallback JSON
        candidates = response_data.get("candidates")
        if not candidates:
            logging.warning("Gemini report: no candidates, fallback to empty summary")
            report_json = {
                "summary": "模型沒有產生報告內容，請稍後再試。",
                "keywords": [],
                "emotionVector": {"valence": 50, "arousal": 50, "dominance": 50}
            }
            return jsonify(report_json), 200  # 🟢 修改：避免 500，改為可渲染的預設值

        parts = candidates[0].get("content", {}).get("parts")
        if not parts:
            logging.warning("Gemini report: candidates present but no parts")
            report_json = {
                "summary": "模型沒有提供完整內容。",
                "keywords": [],
                "emotionVector": {"valence": 50, "arousal": 50, "dominance": 50}
            }
            return jsonify(report_json), 200  # 🟢 修改：同上

        # 提取回應內容（維持原邏輯）
        summary_text = parts[0]["text"]
        logging.debug(f"Raw report summary: {summary_text}")
        
        # 嘗試解析回應為 JSON（維持原邏輯）
        parsed_json = extract_json_from_response(summary_text)
        if parsed_json and isinstance(parsed_json, dict):
            logging.debug(f"Successfully parsed report JSON: {parsed_json}")
            return jsonify(parsed_json)
        else:
            # 如果不是 JSON，返回純文字總結
            logging.warning(f"Could not parse report JSON, returning as plain text: {summary_text}")
            report_json = {
                "summary": summary_text,
                "keywords": [],
                "emotionVector": {"valence": 50, "arousal": 50, "dominance": 50}
            }
            return jsonify(report_json)
    
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response:
            error_msg += f", response: {e.response.text[:200]}"
        logging.error(f"Gemini API report request failed: {error_msg}")
        return jsonify({"summary": "抱歉，整理總結時出了點小差錯，請稍後再試。"}), 500
    except Exception as e:
        logging.error(f"Unexpected error in report_api: {str(e)}, data: {data}")
        return jsonify({"error": f"伺服器錯誤：{str(e)}"}), 500

# 儲存心理測驗分數
# 🟢 修改：明確指定 endpoint 名稱，避免因函式名或載入順序造成的註冊差異
@app.route("/save_psychology_scores", methods=["POST"], endpoint="save_psychology_scores")  # 🟢 修改
def save_psychology_scores():
    if "user_id" not in session:
        return jsonify({"error": "未登入"}), 401

    data = request.get_json()
    if not data or not all(key in data for key in ["mindScore", "bodyScore", "combinedScore"]):
        return jsonify({"error": "缺少必要的分數參數"}), 400

    try:
        user_id = session["user_id"]
        test_id = db.collection("users").document(user_id).collection("psychology_tests").document().id
        db.collection("users").document(user_id).collection("psychology_tests").document(test_id).set(
            {
                "mind_score": data["mindScore"],
                "body_score": data["bodyScore"],
                "combined_score": data["combinedScore"],
                "summary": data.get("summary", ""),
                "keywords": data.get("keywords", []),
                "emotion_vector": data.get("emotionVector", {}),
                "conversation_history": data.get("conversationHistory", []),
                "submit_time": SERVER_TIMESTAMP
            }
        )
        logging.debug(f"Psychology scores saved for user {user_id}, test {test_id}")
        return jsonify({"status": "success", "test_id": test_id})
    except Exception as e:
        logging.error(f"Error saving psychology scores: {str(e)}")
        return jsonify({"error": f"儲存分數失敗：{str(e)}"}), 500

# 生成貓咪圖卡
@app.route("/generate_card")
def generate_card():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    session.pop("_flashes", None)

    try:
        user_id = session["user_id"]
        # 🟢 修改：同樣改為查詢頂層 health_reports
        health_reports = (
            db.collection("health_reports")
              .where("user_uid", "==", user_id)
              .stream()
        )  # 🟢 修改：原本是 users/{uid}/health_reports
        reports = [report.to_dict() for report in health_reports]
        logging.debug(f"Generate card - reports found: {len(reports)}")
        if not reports:
            flash("請先上傳健康報告！", "error")
            return redirect(url_for("upload_health"))

        psych_tests = (
            db.collection("users")
            .document(user_id)
            .collection("psychology_tests")
            .stream()
        )
        tests = [test.to_dict() for test in psych_tests]
        if not tests:
            flash("請先完成心理測驗！」", "error")
            return redirect(url_for("psychology_test"))

        card_url = "https://images.unsplash.com/photo-1526336024174-e58f5cdd8e13?crop=entropy&cs=tinysrgb&fit=max&fm=jpg"

        return render_template(
            "generate_card.html", card_image_url=card_url, is_logged_in=True
        )
    except Exception as e:
        logging.error(f"Generate card error: {str(e)}")
        flash(f"生成圖卡失敗：{str(e)}", "error")
        return render_template(
            "generate_card.html", error=f"生成圖卡失敗：{str(e)}", is_logged_in=True
        )

if __name__ == "__main__":
    # 若要列印路由表，可在這裡印出（避免 Flask 3 的 before_first_request）
    # logging.debug("URL Map:\n" + "\n".join([str(r) for r in app.url_map.iter_rules()]))
    app.run(debug=True)
