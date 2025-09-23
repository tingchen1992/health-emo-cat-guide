import json
import logging
import os
from io import BytesIO
from PIL import Image
import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory
from dotenv import load_dotenv
import datetime
import pdfplumber

# 載入環境變數
load_dotenv()

# 設定日誌
logging.basicConfig(level=logging.DEBUG)

# --- 1. 設定區 ---
HEALTH_STANDARDS_FILE = "health_standards.json"

# --- 2. 初始化服務 ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    logging.debug("Gemini API initialized successfully.")
except Exception as e:
    logging.error(f"Gemini API initialization failed: {e}")
    raise Exception("Gemini API initialization failed.")

# 全域變數來儲存健康標準值
HEALTH_STANDARDS = {}


def load_health_standards():
    """載入健康標準值的 JSON 檔案"""
    global HEALTH_STANDARDS
    try:
        with open(HEALTH_STANDARDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            HEALTH_STANDARDS = data.get("health_standards", {})
        logging.debug(f"Health standards loaded: {list(HEALTH_STANDARDS.keys())}")
    except FileNotFoundError:
        logging.warning(
            f"Warning: {HEALTH_STANDARDS_FILE} not found. Trying health_standards.json."
        )
        try:
            with open("health_standards.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                HEALTH_STANDARDS = data.get("health_standards", {})
            logging.debug(
                f"Fallback health_standards.json loaded: {list(HEALTH_STANDARDS.keys())}"
            )
        except FileNotFoundError:
            logging.error(
                "Both health_standards_full.json and health_standards.json not found."
            )
            raise
    except Exception as e:
        logging.error(f"Failed to load health standards: {e}")
        raise


load_health_standards()


# --- 3. 核心功能模組 ---
def extract_pdf_text(pdf_data):
    """從 PDF 檔案提取文本"""
    try:
        with pdfplumber.open(BytesIO(pdf_data)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
        logging.debug(f"Extracted PDF text: {text[:100]}...")  # 僅記錄前100字元
        return text
    except Exception as e:
        logging.error(f"Failed to extract PDF text: {str(e)}")
        return None


def analyze_image_with_gemini(image_data, user_uid):
    """分析圖片並返回健康數據"""
    logging.info("Sending image to Gemini for analysis...")

    prompt = f"""
你是個專業的醫療數據分析師，請你從這張健檢報告圖片中，精準地提取出重要的健康數據。
請你務必使用繁體中文，並以 JSON 格式回傳。

請你務必嘗試尋找並回傳以下所有欄位。如果報告中沒有某個數值，請將其值設定為 null。
報告日期請使用當前日期（格式：yyyy/mm/dd）。

注意：以下欄位可能以不同名稱出現，請識別並對應：
- glucose: Glu-AC, Glucose (A.C.), 血糖
- hemoglobin_a1c: HbA1c, 糖化血紅蛋白
- cholesterol: Total Cholesterol, T-CHO, 總膽固醇
- triglycerides: TG, Triglyceride, 三酸甘油酯
- ldl_cholesterol: LDL-C, LDL-Cholesterol, 低密度脂蛋白膽固醇
- hdl_cholesterol: HDL-C, HDL-Cholesterol, 高密度脂蛋白膽固醇
- alt_or_sgpt: ALT(GPT), 丙氨酸氨基轉移酶
- ast_or_sgot: AST(GOT), 天門冬氨酸氨基轉移酶
- creatinine: CRE, 肌酐
- uric_acid: U.A, 尿酸
- wbc: WBC, 白血球計數
- rbc: RBC, 紅血球計數
- hemoglobin: Hb, 血紅蛋白
- platelet: PLT, Platelet, 血小板計數
- urine_protein: Protein (Dipstick), 尿蛋白

{{
  "user_uid": "{user_uid}",
  "report_date": "{datetime.datetime.now().strftime('%Y/%m/%d')}",
  "vital_stats": {{
    "glucose": null,
    "hemoglobin_a1c": null,
    "cholesterol": null,
    "t_cho": null,
    "triglycerides": null,
    "ldl_cholesterol": null,
    "hdl_cholesterol": null,
    "bmi": null,
    "alt_or_sgpt": null,
    "ast_or_sgot": null,
    "creatinine": null,
    "egfr": null,
    "uric_acid": null,
    "wbc": null,
    "rbc": null,
    "hemoglobin": null,
    "platelet": null,
    "urine_glucose": null,
    "urine_protein": null,
    "blood_pressure_systolic": null,
    "blood_pressure_diastolic": null
  }}
}}

請你只回傳 JSON 格式的內容，不要包含任何額外的文字或說明。
"""

    try:
        img = Image.open(BytesIO(image_data))
        if img.format not in ["JPEG", "PNG"]:
            logging.error(f"Unsupported image format: {img.format}")
            return None
        if img.size[0] < 100 or img.size[1] < 100:
            logging.error(f"Image resolution too low: {img.size}")
            return None

        response = gemini_model.generate_content(
            [prompt, img],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json", temperature=0.0
            ),
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        logging.info("Gemini image analysis complete, processing returned data...")
        gemini_output_str = (
            response.text.strip().replace("```json", "").replace("```", "")
        )
        logging.debug(f"Gemini raw output: {gemini_output_str}")

        try:
            vital_stats_json = json.loads(gemini_output_str)
            if (
                not isinstance(vital_stats_json, dict)
                or "vital_stats" not in vital_stats_json
            ):
                logging.error("Invalid JSON structure from Gemini")
                return None
            return vital_stats_json
        except json.JSONDecodeError as json_e:
            logging.error(f"Failed to parse Gemini JSON output: {str(json_e)}")
            return None

    except Exception as e:
        logging.error(f"Failed to analyze image with Gemini: {str(e)}")
        return None


def analyze_pdf_with_gemini(pdf_data, user_uid):
    """分析 PDF 並返回健康數據"""
    logging.info("Sending PDF text to Gemini for analysis...")
    text = extract_pdf_text(pdf_data)
    if not text:
        logging.error("No text extracted from PDF")
        return None

    prompt = f"""
你是個專業的醫療數據分析師，請你從以下健檢報告文本中，精準地提取出重要的健康數據。
請你務必使用繁體中文，並以 JSON 格式回傳。

請你務必嘗試尋找並回傳以下所有欄位。如果報告中沒有某個數值，請將其值設定為 null。
報告日期請使用當前日期（格式：yyyy/mm/dd）。

注意：以下欄位可能以不同名稱出現，請識別並對應：
- glucose: Glu-AC, Glucose (A.C.), 血糖
- hemoglobin_a1c: HbA1c, 糖化血紅蛋白
- cholesterol: Total Cholesterol, T-CHO, 總膽固醇
- triglycerides: TG, Triglyceride, 三酸甘油酯
- ldl_cholesterol: LDL-C, LDL-Cholesterol, 低密度脂蛋白膽固醇
- hdl_cholesterol: HDL-C, HDL-Cholesterol, 高密度脂蛋白膽固醇
- alt_or_sgpt: ALT(GPT), 丙氨酸氨基轉移酶
- ast_or_sgot: AST(GOT), 天門冬氨酸氨基轉移酶
- creatinine: CRE, 肌酐
- uric_acid: U.A, 尿酸
- wbc: WBC, 白血球計數
- rbc: RBC, 紅血球計數
- hemoglobin: Hb, 血紅蛋白
- platelet: PLT, Platelet, 血小板計數
- urine_protein: Protein (Dipstick), 尿蛋白

{{
  "user_uid": "{user_uid}",
  "report_date": "{datetime.datetime.now().strftime('%Y/%m/%d')}",
  "vital_stats": {{
    "glucose": null,
    "hemoglobin_a1c": null,
    "cholesterol": null,
    "t_cho": null,
    "triglycerides": null,
    "ldl_cholesterol": null,
    "hdl_cholesterol": null,
    "bmi": null,
    "alt_or_sgpt": null,
    "ast_or_sgot": null,
    "creatinine": null,
    "egfr": null,
    "uric_acid": null,
    "wbc": null,
    "rbc": null,
    "hemoglobin": null,
    "platelet": null,
    "urine_glucose": null,
    "urine_protein": null,
    "blood_pressure_systolic": null,
    "blood_pressure_diastolic": null
  }}
}}

以下是健檢報告的文本內容：
{text}

請你只回傳 JSON 格式的內容，不要包含任何額外的文字或說明。
"""

    try:
        response = gemini_model.generate_content(
            [prompt],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json", temperature=0.0
            ),
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        logging.info("Gemini PDF analysis complete, processing returned data...")
        gemini_output_str = (
            response.text.strip().replace("```json", "").replace("```", "")
        )
        logging.debug(f"Gemini raw output: {gemini_output_str}")

        try:
            vital_stats_json = json.loads(gemini_output_str)
            if (
                not isinstance(vital_stats_json, dict)
                or "vital_stats" not in vital_stats_json
            ):
                logging.error("Invalid JSON structure from Gemini")
                return None
            return vital_stats_json
        except json.JSONDecodeError as json_e:
            logging.error(f"Failed to parse Gemini JSON output: {str(json_e)}")
            return None

    except Exception as e:
        logging.error(f"Failed to analyze PDF with Gemini: {str(e)}")
        return None


def calculate_health_score(vital_stats):
    """根據健檢數據與 JSON 檔案標準比對計算分數，並記錄超標原因"""
    score = 100
    warnings = []
    logging.debug(f"Processing vital_stats: {vital_stats}")

    for key, value in vital_stats.items():
        if value is None:  # 只檢查 value 是否為 None
            logging.debug(f"Skipping {key}: Value is None")
            continue
        if key not in HEALTH_STANDARDS:
            logging.debug(f"Skipping {key}: No standard")
            continue

        standard_info = HEALTH_STANDARDS[key]
        ref_value_str = standard_info.get("reference_value", "")
        name = standard_info.get("name", key)
        logging.debug(f"Checking {key} ({name}): value={value}, ref={ref_value_str}")

        is_abnormal = False
        try:
            if isinstance(value, str):
                if value.lower() in ["負", "negative"]:
                    numeric_value = 0
                else:
                    numeric_value = float(value.strip())
            else:
                numeric_value = float(value)

            # 解析參考範圍
            if "-" in ref_value_str:
                lower, upper = map(float, ref_value_str.split("-"))
                if numeric_value < lower or numeric_value > upper:
                    is_abnormal = True
            elif "<" in ref_value_str:
                upper = float(ref_value_str.replace("<", "").strip())
                if numeric_value >= upper:
                    is_abnormal = True
            elif ">" in ref_value_str:
                lower = float(ref_value_str.replace(">", "").strip())
                if numeric_value <= lower:
                    is_abnormal = True
            elif ref_value_str.lower() in ["負", "negative"] and value.lower() not in [
                "負",
                "negative",
            ]:
                is_abnormal = True

            if is_abnormal:
                warning_msg = f"{name} 超出正常範圍 ({value} vs {ref_value_str})"
                if numeric_value < lower:
                    warning_msg = f"{name} 低於正常範圍 ({value} vs {ref_value_str})"
                elif numeric_value > upper:
                    warning_msg = f"{name} 高於正常範圍 ({value} vs {ref_value_str})"
                logging.info(f"Warning: {warning_msg}")
                score -= 5
                warnings.append(warning_msg)

        except (ValueError, TypeError) as e:
            logging.warning(f"Unable to process {name} ({value}): {str(e)}")
            continue

    if score < 1:
        score = 1

    logging.debug(f"Health score: {score}, Warnings: {warnings}")
    return score, warnings


def analyze_health_report(file_data, user_uid, file_type):
    """
    執行完整的健檢報告分析流程，支援圖片和 PDF。

    Args:
        file_data: 檔案的二進制數據。
        user_uid: 使用者 ID。
        file_type: 檔案類型 ('image' 或 'pdf')。

    Returns:
        (dict, int, list) - 包含 (分析數據, 健康分數, 警告列表) 的元組。
        如果分析失敗，返回 (None, 0, [])。
    """
    if file_type == "image":
        gemini_data = analyze_image_with_gemini(file_data, user_uid)
    elif file_type == "pdf":
        gemini_data = analyze_pdf_with_gemini(file_data, user_uid)
    else:
        logging.error(f"Unsupported file type: {file_type}")
        return None, 0, []

    if not gemini_data:
        logging.warning("No data returned from Gemini analysis")
        return None, 0, []

    health_score, health_warnings = calculate_health_score(
        gemini_data.get("vital_stats", {})
    )
    logging.debug(
        f"Health score calculated: {health_score}, warnings: {health_warnings}"
    )

    return gemini_data, health_score, health_warnings
