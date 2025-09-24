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

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# --- 1. Settings ---
HEALTH_STANDARDS_FILE = "health_standards.json"

# --- 2. Initialize Services ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-2.5-flash")
    logging.debug("Gemini API initialized successfully.")
except Exception as e:
    logging.error(f"Gemini API initialization failed: {e}")
    raise Exception("Gemini API initialization failed.")

# Global variables to store health standards and alias mappings
HEALTH_STANDARDS = {}
HEALTH_ALIASES = {}

def load_health_standards():
    """Load health standards from a JSON file and create an alias mapping."""
    global HEALTH_STANDARDS, HEALTH_ALIASES
    try:
        with open(HEALTH_STANDARDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            HEALTH_STANDARDS = data.get("health_standards", {})
        
        # Create a reverse mapping from aliases to standard keys
        for key, value in HEALTH_STANDARDS.items():
            if 'aliases' in value and isinstance(value['aliases'], list):
                for alias in value['aliases']:
                    HEALTH_ALIASES[alias.strip().lower()] = key
        
        logging.debug(f"Health standards loaded: {list(HEALTH_STANDARDS.keys())}")
        logging.debug(f"Alias mapping created: {list(HEALTH_ALIASES.keys())}")
    except FileNotFoundError:
        logging.error(f"Failed to load health standards: {HEALTH_STANDARDS_FILE} not found.")
        raise
    except Exception as e:
        logging.error(f"Failed to load health standards: {e}")
        raise

load_health_standards()

# --- 3. Core Function Modules ---
def extract_pdf_text(pdf_data):
    """從 PDF 檔案提取文本"""
    try:
        with pdfplumber.open(BytesIO(pdf_data)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
        logging.debug(f"Extracted PDF text: {text[:100]}...")
        return text
    except Exception as e:
        logging.error(f"Failed to extract PDF text: {str(e)}")
        return None

def get_gemini_prompt(user_uid, file_type):
    """根據文件類型生成 Gemini 提示"""
    base_prompt = f"""
你是個專業的醫療數據分析師，請你從這份健檢報告中，精準地提取出重要的健康數據。
請你務必使用繁體中文，並以 JSON 格式回傳。

請你務必嘗試尋找並回傳以下所有欄位。如果報告中沒有某個數值，請將其值設定為 null。
報告日期請使用當前日期（格式：yyyy/mm/dd）。

注意：請特別關注每個欄位的別名，並將其數值對應到正確的標準欄位名稱。
例如：如果報告中出現 "SGPT"，請將其數值填入 "alt"。如果出現 "TG"，請填入 "triglycerides"。

{{
  "user_uid": "{user_uid}",
  "report_date": "{datetime.datetime.now().strftime('%Y/%m/%d')}",
  "vital_stats": {{
    "glucose": null,
    "hemoglobin_a1c": null,
    "total_cholesterol": null,
    "triglycerides": null,
    "ldl_cholesterol": null,
    "hdl_cholesterol": null,
    "bmi": null,
    "alt": null,
    "ast": null,
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
    "blood_pressure_diastolic": null,
    "HBsAg": null,
    "urine_ob": null
  }}
}}

請你只回傳 JSON 格式的內容，不要包含任何額外的文字或說明。
"""
    if file_type == "pdf":
        return f"{base_prompt}\n以下是健檢報告的文本內容："
    return base_prompt

def analyze_image_with_gemini(image_data, user_uid):
    """分析圖片並返回健康數據"""
    logging.info("Sending image to Gemini for analysis...")
    prompt = get_gemini_prompt(user_uid, "image")
    
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

    prompt = get_gemini_prompt(user_uid, "pdf")
    
    try:
        response = gemini_model.generate_content(
            [prompt, text],
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

def calculate_health_score(vital_stats, gender=None):
    """
    根據健檢數據與分級標準計算分數。
    A級扣5分、B級扣10分、C級扣15分，滿分100分最低1分。
    
    Args:
        vital_stats (dict): 從健檢報告提取的數據。
        gender (str, optional): 使用者的性別，'male' 或 'female'。預設為 'female'。
    """
    score = 100
    warnings = []
    
    # Set default gender to 'female' if not provided
    gender_key = gender.lower() if gender in ['male', 'female'] else 'female'

    # helper function to get numeric value
    def get_numeric_value(val):
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            val_lower = val.strip().lower()
            if val_lower in ["負", "negative", "(-)", "-"]:
                return 0
            # Convert qualitative results to comparable values
            if val_lower in ["+/-", "+"]: return 1
            if val_lower in ["++", "+++"]: return 2
            if val_lower == "++++": return 3
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # Process all vital stats
    for key, value in vital_stats.items():
        standard_info = HEALTH_STANDARDS.get(key)
        if value is None or not standard_info:
            continue
        
        # Get the correct reference value based on gender, if available
        ref_value_key = f"reference_value_{gender_key}" if f"reference_value_{gender_key}" in standard_info else "reference_value"
        ref_value_str = standard_info.get(ref_value_key, "").strip()

        # Qualitative checks (HBsAg, urine_ob)
        if '(-)' in ref_value_str and value != '(-)':
            score -= 5
            warnings.append(f"{standard_info.get('name', key)} 超出正常範圍 ({value})")
            continue
            
        numeric_value = get_numeric_value(value)
        if numeric_value is None:
            continue
        
        # Quantitative checks
        grade = None
        
        # Handle ranges with hyphens
        if "-" in ref_value_str:
            lower, upper = map(float, ref_value_str.split("-"))
            if not (lower <= numeric_value <= upper):
                grade = "A" # A-level default for out of range
        
        # Handle < and > operators
        elif "<" in ref_value_str:
            upper = float(ref_value_str.replace("<", "").strip())
            if numeric_value >= upper:
                grade = "A"
        elif ">" in ref_value_str:
            lower = float(ref_value_str.replace(">", "").strip())
            if numeric_value <= lower:
                grade = "A"

        # Apply specific grading logic based on gender
        gender_grades = standard_info.get('grades', {}).get(gender_key, {})
        if gender_grades:
            for g, boundaries in gender_grades.items():
                lower, upper = boundaries
                if lower <= numeric_value <= upper:
                    grade = g
                    break
        elif grade is None:
            # Fallback to the generic grading from previous version if no gender-specific grades
            if key == "glucose" and numeric_value >= 100:
                if 100 <= numeric_value <= 126: grade = "A"
                elif 126 < numeric_value <= 180: grade = "B"
                else: grade = "C"
            elif key == "ldl_cholesterol" and numeric_value >= 130:
                if 130 <= numeric_value <= 160: grade = "A"
                elif 160 < numeric_value <= 200: grade = "B"
                else: grade = "C"
            elif key == "bmi" and numeric_value >= 24:
                if 24 <= numeric_value < 27: grade = "A"
                elif 27 <= numeric_value < 30: grade = "B"
                else: grade = "C"
            elif key == "alt" and numeric_value >= 41:
                if 41 <= numeric_value <= 80: grade = "A"
                elif 80 < numeric_value <= 200: grade = "B"
                else: grade = "C"
            elif key == "ast" and numeric_value >= 31:
                if 31 <= numeric_value <= 80: grade = "A"
                elif 80 < numeric_value <= 200: grade = "B"
                else: grade = "C"
            elif key == "creatinine" and numeric_value >= 1.3:
                if 1.3 <= numeric_value <= 2.0: grade = "A"
                elif 2.0 < numeric_value <= 3.0: grade = "B"
                else: grade = "C"
            elif key == "uric_acid" and numeric_value >= 7:
                if 7 <= numeric_value <= 8: grade = "A"
                elif 8 < numeric_value <= 10: grade = "B"
                else: grade = "C"
            elif key == "urine_protein":
                numeric_val = get_numeric_value(value)
                if numeric_val == 1: grade = "A"
                elif numeric_val == 2: grade = "B"
                elif numeric_val == 3: grade = "C"
        
        # Apply deductions based on grade
        if grade == "A":
            score -= 5
            warnings.append(f"{standard_info.get('name', key)} 數值為 A 級 ({value})")
        elif grade == "B":
            score -= 10
            warnings.append(f"{standard_info.get('name', key)} 數值為 B 級 ({value})")
        elif grade == "C":
            score -= 15
            warnings.append(f"{standard_info.get('name', key)} 數值為 C 級 ({value})")

    # Handle blood pressure separately
    systolic_val = get_numeric_value(vital_stats.get('blood_pressure_systolic'))
    diastolic_val = get_numeric_value(vital_stats.get('blood_pressure_diastolic'))
    if systolic_val is not None and diastolic_val is not None:
        if (systolic_val >= 160 or diastolic_val >= 100):
            score -= 15
            warnings.append(f"血壓數值為 C 級 ({systolic_val}/{diastolic_val} mmHg)")
        elif (systolic_val >= 140 or diastolic_val >= 90):
            score -= 10
            warnings.append(f"血壓數值為 B 級 ({systolic_val}/{diastolic_val} mmHg)")
        elif (systolic_val >= 130 or diastolic_val >= 80):
            score -= 5
            warnings.append(f"血壓數值為 A 級 ({systolic_val}/{diastolic_val} mmHg)")
    elif systolic_val is not None:
        if systolic_val >= 160:
            score -= 15
            warnings.append(f"血壓收縮壓為 C 級 ({systolic_val} mmHg)")
        elif systolic_val >= 140:
            score -= 10
            warnings.append(f"血壓收縮壓為 B 級 ({systolic_val} mmHg)")
        elif systolic_val >= 130:
            score -= 5
            warnings.append(f"血壓收縮壓為 A 級 ({systolic_val} mmHg)")
    elif diastolic_val is not None:
        if diastolic_val >= 100:
            score -= 15
            warnings.append(f"血壓舒張壓為 C 級 ({diastolic_val} mmHg)")
        elif diastolic_val >= 90:
            score -= 10
            warnings.append(f"血壓舒張壓為 B 級 ({diastolic_val} mmHg)")
        elif diastolic_val >= 80:
            score -= 5
            warnings.append(f"血壓舒張壓為 A 級 ({diastolic_val} mmHg)")


    # --- 三高複合條件判斷 ---
    high_count = 0
    if get_numeric_value(vital_stats.get('glucose')) is not None and get_numeric_value(vital_stats.get('glucose')) >= 100: high_count += 1
    if get_numeric_value(vital_stats.get('ldl_cholesterol')) is not None and get_numeric_value(vital_stats.get('ldl_cholesterol')) >= 130: high_count += 1
    
    systolic_high = get_numeric_value(vital_stats.get('blood_pressure_systolic'))
    diastolic_high = get_numeric_value(vital_stats.get('blood_pressure_diastolic'))
    if (systolic_high is not None and systolic_high >= 130) or \
       (diastolic_high is not None and diastolic_high >= 80):
        high_count += 1
    
    if high_count == 1:
        score -= 5
        warnings.append("符合「一高」條件，額外扣 5 分。")
    elif high_count == 2:
        score -= 10
        warnings.append("符合「兩高」條件，額外扣 10 分。")
    elif high_count == 3:
        score -= 15
        warnings.append("符合「三高」條件，額外扣 15 分。")

    if score < 1:
        score = 1

    logging.debug(f"Health score: {score}, Warnings: {warnings}")
    return score, warnings

def analyze_health_report(file_data, user_uid, file_type, gender=None):
    """
    執行完整的健檢報告分析流程，支援圖片和 PDF。
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

    # Example: In a real app, 'gender' would come from the user's profile
    health_score, health_warnings = calculate_health_score(
        gemini_data.get("vital_stats", {}), gender=gender
    )
    logging.debug(
        f"Health score calculated: {health_score}, warnings: {health_warnings}"
    )

    return gemini_data, health_score, health_warnings
