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

# user_rank 시트에 rank_name 찾아서 rank_cnt 업데이트
def update_rank_cnt(rank_name, new_cnt):
    try:
        worksheet = get_worksheet('user_rank')

        # rank_name으로 데이터 찾기
        find_data = worksheet.find(rank_name)

        if find_data:
            worksheet.update_cell(cell.row, 2, new_cnt)
            msg = f'{rank_name}등급의 수가 {new_cnt}로 업데이트 되었습니다!'
            print(msg)
            return True, msg
        else:
            err_msg = f'{rank_name}을(를) 찾을 수 없습니다'
            print(err_msg)
            return False, err_msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg

# 길드원 추가
def add_user(user_name):
    msg = ''
    try:
        worksheet = get_worksheet('user')

        # 길드원 중복 검사
        cell = worksheet.find(user_name)
        if cell:
            msg = f'{user_name}은(는) 이미 존재합니다.'
            print(msg)
            return False, msg

        # 새 길드원 추가
        worksheet.append_row([user_name, 0, '일반'])
        msg = f'{user_name}이(가) 성공적으로 추가되었습니다.'
        print(msg)
        return True, msg

    except Exception as e:
        msg = f'오류: {e}'
        print(msg)
        return False, msg