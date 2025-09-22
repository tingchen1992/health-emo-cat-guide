from flask import Flask, render_template, request, redirect, url_for, session, flash
import firebase_admin
from firebase_admin import credentials, firestore, storage, auth
from firebase_admin.exceptions import FirebaseError
import os
from datetime import datetime
import logging

app = Flask(__name__)
app.secret_key = "your-secret-key"  # 請替換為安全的隨機密鑰
logging.basicConfig(level=logging.DEBUG)

# 初始化 Firebase
cred = credentials.Certificate("firebase_credentials/service_account.json")
try:
    firebase_admin.initialize_app(
        cred, {"storageBucket": "health-emo-cat-guide.firebasestorage.app"}
    )
    logging.debug("Firebase initialized successfully")
except ValueError as e:
    logging.error(f"Firebase initialization failed: {e}")
db = firestore.client()
try:
    bucket = storage.bucket()
    logging.debug(f"Storage bucket initialized: {bucket.name}")
except Exception as e:
    logging.error(f"Storage bucket initialization failed: {str(e)}")
    raise


# 首頁
@app.route("/")
def home():
    is_logged_in = "user_id" in session
    return render_template("home.html", is_logged_in=is_logged_in)


# 註冊
@app.route("/register", methods=["GET", "POST"])
def register():
    # 清除舊的 flash 訊息
    if request.method == "GET":
        session.pop("_flashes", None)

    if request.method == "POST":
        logging.debug(f"Received POST request with form data: {request.form}")
        email = request.form.get("email")
        password = request.form.get("password")
        logging.debug(
            f"Parsed form data: email={email}, password={'*' * len(password) if password else None}"
        )

        if not email or not password:
            flash("請輸入電子郵件和密碼！", "error")
            logging.warning("Missing email or password in form submission")
            return render_template("register.html", error="請輸入電子郵件和密碼")

        try:
            # 使用 Firebase Authentication 創建用戶
            user = auth.create_user(email=email, password=password)
            logging.debug(f"User created: uid={user.uid}, email={email}")
            # 儲存用戶資訊到 Firestore
            db.collection("users").document(user.uid).set(
                {
                    "email": email,
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "last_login": None,
                }
            )
            session["user_id"] = user.uid
            flash("註冊成功！請上傳健康報告。", "success")
            return redirect(url_for("upload_health"))
        except FirebaseError as e:
            error_message = str(e)
            logging.error(f"Firebase error: {error_message}")
            flash(f"註冊失敗：{error_message}", "error")
            return render_template("register.html", error=f"註冊失敗：{error_message}")
        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
            flash(f"註冊失敗：{str(e)}", "error")
            return render_template("register.html", error=f"註冊失敗：{str(e)}")

    return render_template("register.html")


# 登入
@app.route("/login", methods=["GET", "POST"])
def login():
    # 清除舊的 flash 訊息，避免顯示不相關的成功訊息
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
            # 注意：Admin SDK 不直接驗證密碼，僅檢查 email 存在
            user = auth.get_user_by_email(email)
            # 模擬登入成功，實際應使用 Firebase Client SDK 驗證密碼
            db.collection("users").document(user.uid).update(
                {"last_login": firestore.SERVER_TIMESTAMP}
            )
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


# 九宮格貓咪頁面 (已修改)
@app.route("/featured_cats")
def featured_cats():
    is_logged_in = "user_id" in session
    return render_template("featured_cats.html", is_logged_in=is_logged_in)


# 上傳健康報告
@app.route("/upload_health", methods=["GET", "POST"])
def upload_health():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    # 清除舊的 flash 訊息，避免 GET 請求時顯示不必要的成功訊息
    if request.method == "GET":
        session.pop("_flashes", None)

    if request.method == "POST":
        logging.debug(
            f"Received POST request with form data: {request.form}, files: {request.files}"
        )

        # 檢查是否已經上傳過健康報告
        user_id = session["user_id"]
        existing_reports = list(
            db.collection("users")
            .document(user_id)
            .collection("health_reports")
            .stream()
        )

        if existing_reports:
            flash("您已經上傳過健康報告了！請繼續進行心理測驗。", "info")
            return redirect(url_for("psychology_test"))

        if (
            "health_report" not in request.files
            or request.files["health_report"].filename == ""
        ):
            flash("請選擇一個檔案或拍照！", "error")
            logging.warning("No file uploaded or empty filename")
            return redirect(url_for("upload_health"))

        file = request.files["health_report"]
        file_name = file.filename.lower()
        logging.debug(f"Uploading file: {file_name}")
        allowed_extensions = [".pdf", ".jpg", ".jpeg", ".png"]
        if not any(file_name.endswith(ext) for ext in allowed_extensions):
            flash("請上傳 PDF 或圖片（JPG、PNG）格式的檔案！", "error")
            logging.warning(f"Invalid file extension: {file_name}")
            return redirect(url_for("upload_health"))

        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        if file_size > 10 * 1024 * 1024:
            flash("檔案大小超過 10MB 限制！", "error")
            logging.warning(f"File size too large: {file_size} bytes")
            return redirect(url_for("upload_health"))
        file.seek(0)

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            blob = bucket.blob(f"health_reports/{user_id}/{timestamp}_{file.filename}")
            logging.debug(f"Uploading to Storage: {blob.name}")
            blob.upload_from_file(file)
            blob.make_public()
            file_url = blob.public_url
            logging.debug(f"File uploaded successfully: {file_url}")

            db.collection("users").document(user_id).collection("health_reports").add(
                {
                    "filename": file.filename,
                    "url": file_url,
                    "file_type": (
                        "image"
                        if file_name.endswith((".jpg", ".jpeg", ".png"))
                        else "pdf"
                    ),
                    "upload_time": firestore.SERVER_TIMESTAMP,
                }
            )
            logging.debug(f"Health report saved to Firestore for user: {user_id}")

            flash("檔案上傳成功！請完成心理測驗。", "success")
            return redirect(url_for("psychology_test"))
        except Exception as e:
            logging.error(f"Upload error: {str(e)}")
            flash(f"上傳失敗：{str(e)}", "error")
            return redirect(url_for("upload_health"))

    return render_template("upload_health.html", is_logged_in=True)


# 心理測驗
@app.route("/psychology_test", methods=["GET", "POST"])
def psychology_test():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    # 清除舊的 flash 訊息
    if request.method == "GET":
        session.pop("_flashes", None)

    if request.method == "POST":
        question1 = request.form.get("question1")
        question2 = request.form.get("question2")
        if not question1 or not question2:
            flash("請回答所有問題！", "error")
            return render_template(
                "psychology_test.html", error="請回答所有問題", is_logged_in=True
            )

        try:
            user_id = session["user_id"]
            db.collection("users").document(user_id).collection("psychology_tests").add(
                {
                    "question1": question1,
                    "question2": question2,
                    "submit_time": firestore.SERVER_TIMESTAMP,
                }
            )
            flash("測驗提交成功！請生成貓咪圖卡。", "success")
            return redirect(url_for("generate_card"))
        except Exception as e:
            logging.error(f"Psychology test error: {str(e)}")
            flash(f"提交失敗：{str(e)}", "error")
            return render_template(
                "psychology_test.html", error=f"提交失敗：{str(e)}", is_logged_in=True
            )

    return render_template("psychology_test.html", is_logged_in=True)


# 生成貓咪圖卡
@app.route("/generate_card")
def generate_card():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    # 清除舊的 flash 訊息
    session.pop("_flashes", None)

    try:
        user_id = session["user_id"]
        health_reports = (
            db.collection("users")
            .document(user_id)
            .collection("health_reports")
            .stream()
        )
        reports = [report.to_dict() for report in health_reports]
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
            flash("請先完成心理測驗！", "error")
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


# 登出
@app.route("/logout")
def logout():
    session.pop("user_id", None)
    # 清除所有 flash 訊息，然後只顯示登出訊息
    session.pop("_flashes", None)
    flash("已成功登出！", "success")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
