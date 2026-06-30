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
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(os.getenv('SPREADSHEET_ID'))
    return spreadsheet

# 워크시트 가져오기
def get_worksheet(sheet_name):
    spreadsheet = get_sheet()
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, row=500, cols=10)
    return worksheet