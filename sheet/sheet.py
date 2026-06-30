'''
* 구글 시트 연동 및 데이터 CRUD
* author LJY
* date   2026.06.30
'''
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Google Sheets 인증
def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.getenv('GOOGLE_CREDENTIALS'))