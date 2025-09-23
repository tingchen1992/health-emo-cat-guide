from flask import Flask, render_template, request, redirect, url_for, session, flash
import firebase_admin
from firebase_admin import credentials, firestore, storage, auth
from firebase_admin.exceptions import FirebaseError
import os
from datetime import datetime
import logging
from health_report_module import analyze_health_report
from google.cloud.firestore import SERVER_TIMESTAMP

app = Flask(__name__)
app.secret_key = "your-secret-key"
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
        logging.debug(
            f"Parsed form data: email={email}, password={'*' * len(password) if password else None}"
        )

        if not email or not password:
            flash("請輸入電子郵件和密碼！", "error")
            logging.warning("Missing email or password in form submission")
            return render_template("register.html", error="請輸入電子郵件和密碼")

        try:
            user = auth.create_user(email=email, password=password)
            logging.debug(f"User created: uid={user.uid}, email={email}")
            db.collection("users").document(user.uid).set(
                {
                    "email": email,
                    "created_at": firestore.SERVER_TIMESTAMP,
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
                {"last_login": firestore.SERVER_TIMESTAMP}
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


# 九宮格貓咪頁面
@app.route("/featured_cats")
def featured_cats():
    is_logged_in = "user_id" in session
    return render_template("featured_cats.html", is_logged_in=is_logged_in)


# 上傳健康報告（修正版本）


@app.route("/upload_health", methods=["GET", "POST"])
def upload_health():
    if "user_id" not in session:
        flash("請先登錄！", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    logging.debug(f"Current user_id from session: {user_id}")

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
        blob.make_public()
        file_url = blob.public_url
        logging.debug(f"File uploaded successfully to Storage: {file_url}")

        # 分析健康報告
        logging.debug("Starting health report analysis...")
        try:
            file.seek(0)  # 重置檔案指針
            file_data = file.read()
            file_type = "image" if is_image else "pdf"
            analysis_data, health_score, health_warnings = analyze_health_report(
                file_data, user_id, file_type
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

    return render_template("upload_health.html")


# 心理測驗
@app.route("/psychology_test", methods=["GET", "POST"])
def psychology_test():
    if "user_id" not in session:
        flash("請先登入！", "error")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    try:
        health_reports = list(
            db.collection("users")
            .document(user_id)
            .collection("health_reports")
            .stream()
        )
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
            db.collection("users").document(user_id).collection("psychology_tests").add(
                {
                    "question1": question1,
                    "question2": question2,
                    "submit_time": firestore.SERVER_TIMESTAMP,
                }
            )
            logging.debug("Psychology test saved to Firestore")
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
    session.pop("_flashes", None)
    flash("已成功登出！", "success")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
