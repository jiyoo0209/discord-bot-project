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

# 유효 등급 목록
VALID_RANKS = ['길마', '서마', '명예', '우수', '일반']

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

# user_rank 시트의 rank_cnt 업데이트 (mode: 'set' 덮어쓰기 / 'delta' 증감)
def update_rank_cnt(rank_name, amount, mode='set'):
    try:
        # 전체는 공식으로 자동 계산되므로 코드로 수정 금지
        if rank_name == '전체':
            err_msg = '전체 인원은 자동 계산되어 직접 수정할 수 없습니다'
            print(err_msg)
            return False, err_msg

        worksheet = get_worksheet('user_rank')

        # rank_name으로 데이터 찾기 (A열)
        find_data = worksheet.find(rank_name, in_column=1)

        if not find_data:
            err_msg = f'{rank_name}을(를) 찾을 수 없습니다'
            print(err_msg)
            return False, err_msg

        # 증감 모드: 현재값 + amount
        if mode == 'delta':
            ok, current = get_rank_cnt(rank_name)
            if not ok:
                return False, current
            new_cnt = max(0, current + amount)
        else:
            new_cnt = amount

        worksheet.update_cell(find_data.row, 2, new_cnt)
        msg = f'{rank_name}등급 인원이 {new_cnt}명으로 설정되었습니다!'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg

# user_rank 시트에서 rank_name의 현재 rank_cnt 조회 [핼퍼 함수]
def get_rank_cnt(rank_name):
    try:
        worksheet = get_worksheet('user_rank')

        # rank_name으로 데이터 찾기 (A열에서만)
        find_data = worksheet.find(rank_name, in_column=1)

        if find_data:
            value = worksheet.cell(find_data.row, 2).value
            cnt = int(value) if value else 0
            return True, cnt
        else:
            err_msg = f'{rank_name}을(를) 찾을 수 없습니다'
            print(err_msg)
            return False, err_msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg

# user 시트에 신규 길드원 추가 (탈퇴자는 재가입 처리)
def add_user(user_name):
    try:
        worksheet = get_worksheet('user')

        # A열에서 닉네임 검색
        find_data = worksheet.find(user_name, in_column=1)

        if find_data:
            current_rank = worksheet.cell(find_data.row, 3).value

            # 재가입: 과거 경고 이력 남기기 위해 일단 주석처리. 경고 현황도 초기화하고 싶다면 아래의 코드 사용
            if current_rank == '탈퇴':
                # worksheet.update_cell(find_data.row, 2, 0)       # warning_point
                worksheet.update_cell(find_data.row, 3, '일반')  # rank_name
                msg = f'{user_name}님이 재가입 처리되었습니다!'
                print(msg)
                return True, msg

            # 활동 중인 길드원이면 중복
            msg = f'{user_name}님은 이미 등록되어 있습니다'
            print(msg)
            return False, msg

        # 신규: [user_name, warning_point=0, rank_name=일반]
        worksheet.append_row([user_name, 0, '일반'])
        msg = f'{user_name}님이 길드원으로 추가되었습니다!'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg
    
# user 시트에서 해당 길드원을 탈퇴 처리 (행 삭제 X, rank_name -> 탈퇴)
def remove_user(user_name):
    try:
        worksheet = get_worksheet('user')

        # A열에서 닉네임 찾기
        find_data = worksheet.find(user_name, in_column=1)

        if not find_data:
            msg = f'{user_name}님을 찾을 수 없습니다'
            print(msg)
            return False, msg

        # rank_name(C열, 3번째)을 탈퇴로 변경
        worksheet.update_cell(find_data.row, 3, '탈퇴')

        msg = f'{user_name}님이 탈퇴 처리되었습니다'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg

    
# user 시트에서 해당 길드원의 등급(rank_name) 변경
def update_user(user_name, rank_name):
    try:
        # 등급 유효성 검증
        if rank_name not in VALID_RANKS:
            msg = f'잘못된 등급입니다. (가능: {", ".join(VALID_RANKS)})'
            print(msg)
            return False, msg

        worksheet = get_worksheet('user')

        # A열에서 닉네임 찾기
        find_data = worksheet.find(user_name, in_column=1)

        if not find_data:
            msg = f'{user_name}님을 찾을 수 없습니다'
            print(msg)
            return False, msg

        # rank_name(C열, 3번째) 변경
        worksheet.update_cell(find_data.row, 3, rank_name)

        msg = f'{user_name}님의 등급이 {rank_name}(으)로 변경되었습니다!'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg