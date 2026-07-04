'''
* 구글 시트 연동 및 데이터 CRUD
* author LJY
* date   2026.06.30
'''
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
import time
import threading
import functools
from collections import defaultdict
from dotenv import load_dotenv
from util.dates import today_int, week_range, this_saturday, is_sunday

load_dotenv()

# 대시보드/패널 데이터 캐시 — 빌드마다 시트 4장을 매번 읽으면 '분당 읽기 쿼터'(429)를 넘긴다.
#   짧은 TTL 로 연속 조회(버튼 페이지 넘김 등)를 재사용하고, 시트를 '변경'하면 즉시 무효화한다.
_DASH_TTL = 20  # seconds
_dash_cache = {'ts': 0.0, 'data': None}

# 모든 슬래시 핸들러는 asyncio.to_thread 로 이 모듈을 호출한다. gspread 는 read-modify-write
#   (cell 읽고 update_cell) 를 두 번의 네트워크 왕복으로 하므로, 동시 호출이 겹치면
#   카운트가 lost-update 되거나(캡처한 행 인덱스가 delete_rows 로 밀려) 엉뚱한 행을 쓴다.
#   → 시트 '변경' 함수들을 전역 락으로 직렬화한다(길드봇이라 저빈도, 성능 영향 없음).
_write_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _write_lock:
            try:
                return fn(*args, **kwargs)
            finally:
                _dash_cache['ts'] = 0.0  # 변경 발생 → 대시보드 캐시 무효화(다음 조회는 최신)
    return wrapper


def _int(v, default=0):
    '''문자열/빈칸 → int 안전 변환 ('5'->5, ''->0, '-1'->-1)'''
    try:
        return int(str(v).strip())
    except (ValueError, AttributeError):
        return default

# 유효 등급 목록
VALID_RANKS = ['길마', '서마', '명예', '우수', '일반']

# 이 등급들만 증감 시 '일반'을 반대로 자동 조정 (명세: 명예/우수 <-> 일반 정원 교환)
RANK_TRADES_GENERAL = ('명예', '우수')

# user 탭 표시 순서 (LO 요청): 이 순서로 정렬, 목록 밖/빈 등급은 맨 뒤(가나다).
RANK_SORT = ['길마', '서마', '명예', '우수', '일반', '입장대기']

# 인증/스프레드시트/워크시트 캐시 — 매 호출마다 재인증+재오픈하던 걸 1회로.
#   gspread+oauth2client 는 액세스 토큰 만료 시 자동 갱신하므로 핸들 재사용이 안전하다.
_spreadsheet = None
_worksheets = {}

# Google Sheets 인증 (최초 1회만 인증+오픈, 이후 캐시 반환)
def get_sheet():
    global _spreadsheet
    if _spreadsheet is None:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = json.loads(os.getenv('GOOGLE_CREDENTIALS'))
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        _spreadsheet = client.open_by_key(os.getenv('SPREADSHEET_ID'))
    return _spreadsheet


# '시트' 버튼용 — 스프레드시트 편집 링크. SPREADSHEET_ID 미설정(공백 .env)이면 None.
def get_spreadsheet_url():
    sid = os.getenv('SPREADSHEET_ID')
    return f'https://docs.google.com/spreadsheets/d/{sid}/edit' if sid else None

# 자동 생성 시 넣을 헤더 — 모든 reader 가 row1=헤더로 get_all_values()[1:] 슬라이스하므로,
#   헤더 없이 만들면 첫 데이터 행이 통째로 무시된다(주1회 제한 우회 등). 반드시 헤더 심는다.
SHEET_HEADERS = {
    'user': ['user_name', 'warning_point', 'rank_name', 'discord_id'],
    'user_rank': ['rank_name', 'rank_cnt'],
    'user_point': ['user_name', 'date', 'point', 'capture_yn', 'reflection_yn'],
    'quest_deny_reason': ['user_name', 'date', 'reason'],
}

# 워크시트 가져오기 (핸들 캐시 — 셀 데이터가 아니라 시트 위치만 캐시하므로 안전)
def get_worksheet(sheet_name):
    if sheet_name in _worksheets:
        return _worksheets[sheet_name]
    spreadsheet = get_sheet()
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=500, cols=10)
        header = SHEET_HEADERS.get(sheet_name)
        if header:
            worksheet.append_row(header, value_input_option='RAW')
    _worksheets[sheet_name] = worksheet
    return worksheet

# 대시보드용: 4개 시트를 '단일' 배치 요청(values_batch_get)으로 한 번에 조회 (헤더 제외)
#   기존엔 worksheet()×4 + get_all_values()×4 = 최대 8 read/빌드였다. 배치로 1 read.
#   + TTL 캐시로 연속 빌드(버튼 넘김/대시보드 재조회)를 재사용해 429(쿼터)를 방지한다.
# 반환: (users, points, denies, ranks) — 각각 list[list[str]]
def get_dashboard_records():
    now = time.time()
    if _dash_cache['data'] is not None and (now - _dash_cache['ts']) < _DASH_TTL:
        return _dash_cache['data']
    try:
        ss = get_sheet()
        resp = ss.values_batch_get(['user', 'user_point', 'quest_deny_reason', 'user_rank'])
        vr = resp.get('valueRanges', [])

        def body(i):
            vals = vr[i].get('values', []) if i < len(vr) else []
            return vals[1:] if len(vals) > 1 else []  # 헤더(1행) 제외

        data = (body(0), body(1), body(2), body(3))
        _dash_cache['data'] = data
        _dash_cache['ts'] = now
        return data
    except Exception as e:
        print(f'오류: {e}')
        # 실패 시 (있으면) 마지막 캐시라도 반환해 대시보드가 완전히 깨지지 않게
        return _dash_cache['data'] or ([], [], [], [])


# user 탭을 등급 순(RANK_SORT, 동급은 닉 가나다)으로 재정렬. **락 안에서** 호출 전제.
#   한 번의 update_cells(batch) 로 2행부터 덮어씀. discord_id(18~19자리)는 문자로,
#   warning_point 는 숫자로 써 '숫자가 텍스트로 저장됨' 경고(초록 삼각형)와 정밀도 손실을 둘 다 피한다.
def _sort_users():
    ws = get_worksheet('user')
    vals = ws.get_all_values()
    if len(vals) <= 2:                       # 헤더 + 데이터 1행 이하 → 정렬 불필요
        return
    data_rows = vals[1:]
    n_phys = len(data_rows)                   # 물리 데이터행 수 (아래 잔여행 비우기용)
    rows = [(r + [''] * 4)[:4] for r in data_rows if r and r[0].strip()]
    order = {name: i for i, name in enumerate(RANK_SORT)}
    rows.sort(key=lambda r: (order.get(r[2], len(RANK_SORT)), r[0]))
    padded = rows + [['', '', '', '']] * (n_phys - len(rows))   # 정렬로 남는 아래행은 공란 → 중복 방지
    cells = []
    for i, (name, warn, rank, did) in enumerate(padded, start=2):
        cells.append(gspread.Cell(row=i, col=1, value=name))
        cells.append(gspread.Cell(row=i, col=2, value=(_int(warn) if str(warn).strip() != '' else '')))
        cells.append(gspread.Cell(row=i, col=3, value=rank))
        cells.append(gspread.Cell(row=i, col=4, value=did))   # RAW → 큰 숫자 문자 그대로
    ws.update_cells(cells, value_input_option='RAW')


# user_rank 시트의 rank_cnt 업데이트 (mode: 'set' 덮어쓰기 / 'delta' 증감)
@_locked
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

        # 새 인원수 계산 (find_data 로 현재값 직접 읽어 재조회 왕복 제거)
        current = _int(worksheet.cell(find_data.row, 2).value)
        if mode == 'delta':
            new_cnt = max(0, current + amount)
        else:  # set
            new_cnt = max(0, amount)

        worksheet.update_cell(find_data.row, 2, new_cnt)

        # 명예/우수를 증감(delta)할 때만 '일반'을 반대로 조정 (명세).
        #   ⚠️ amount 가 아니라 *실제 적용된 변화량*(0 clamp 반영)으로 교환해야 총원이 안 깨진다.
        #   예: 명예 3에서 /감소 5 → 명예는 3만 빠지므로 일반도 +3 이어야 함(+5 아님).
        if mode == 'delta' and rank_name in RANK_TRADES_GENERAL:
            actual_delta = new_cnt - current
            find_general = worksheet.find('일반', in_column=1)
            if find_general:
                general_cur = _int(worksheet.cell(find_general.row, 2).value)
                worksheet.update_cell(find_general.row, 2, max(0, general_cur - actual_delta))

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
            cnt = _int(worksheet.cell(find_data.row, 2).value)
            return True, cnt
        else:
            err_msg = f'{rank_name}을(를) 찾을 수 없습니다'
            print(err_msg)
            return False, err_msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg

# user 시트에 신규 길드원 추가 (탈퇴자는 재가입 처리). discord_id 저장(자동 닉 인식용).
@_locked
def add_user(user_name, discord_id=None):
    try:
        # 유저 시트
        worksheet = get_worksheet('user')
        # 유저 랭크 시트
        rank_worksheet = get_worksheet('user_rank')

        did = str(discord_id) if discord_id is not None else ''

        # 같은 디스코드 계정이 이미 활동 중이면(다른 테런 닉이라도) 중복 → 거부
        if did:
            dcell = worksheet.find(did, in_column=4)
            if dcell and worksheet.cell(dcell.row, 3).value != '탈퇴':
                existing_name = worksheet.cell(dcell.row, 1).value
                msg = f'이 디스코드 계정은 이미 "{existing_name}" 으로 등록되어 있습니다!'
                print(msg)
                return False, msg

        # A열에서 닉네임 검색
        find_data = worksheet.find(user_name, in_column=1)

        if find_data:
            current_rank = worksheet.cell(find_data.row, 3).value
            # 활동 중인 길드원이면 진짜 중복 (재가입 불가)
            if current_rank != '탈퇴':
                msg = '이미 등록된 유저입니다!'  # 명세 문구
                print(msg)
                return False, msg
            # 탈퇴자 재가입: 기존 행을 일반으로 되살림 (warning 리셋 + discord_id 갱신)
            worksheet.update_cell(find_data.row, 2, 0)
            worksheet.update_cell(find_data.row, 3, '일반')
            if did:
                worksheet.update_cell(find_data.row, 4, did)
        else:
            # 신규: [user_name, warning_point=0, rank_name=일반, discord_id]
            #   RAW = 사용자 입력 닉을 문자 그대로 저장 (=..., @... 수식 인젝션 차단)
            worksheet.append_row([user_name, 0, '일반', did], value_input_option='RAW')

        # 일반 길드원 수 +1
        find_general = rank_worksheet.find('일반', in_column=1)
        if find_general:
            general_cur = _int(rank_worksheet.cell(find_general.row, 2).value)
            rank_worksheet.update_cell(find_general.row, 2, max(0, general_cur + 1))

        try:
            _sort_users()   # 새 길드원(일반)을 등급순 위치로 정돈
        except Exception as e:
            print(f'[sort] 건너뜀: {e}')
        msg = f'{user_name}님이 길드원으로 추가되었습니다!'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg


# 개인 현황 (버튼 '내 현황' 용). 캐시된 대시보드 레코드를 재사용해 추가 읽기 없음.
#   반환: {name, rank, warning, week_points, quest_done} 또는 None(미등록)
def get_user_status(discord_id):
    try:
        users, points, _denies, _ranks = get_dashboard_records()
        name = rank = None
        warning = 0
        for r in users:
            if len(r) > 3 and r[3] == str(discord_id):
                name = r[0]
                rank = r[2] if len(r) > 2 else ''
                warning = _int(r[1]) if len(r) > 1 else 0
                break
        if not name:
            return None
        wk_start, wk_end = week_range()
        week_points = quest_done = 0
        for r in points:
            if not r or r[0] != name:
                continue
            d = _int(r[1]) if len(r) > 1 else 0
            cap = (r[3].strip().upper() if len(r) > 3 else '')
            if wk_start <= d <= wk_end and cap == 'N':   # 길퀘(capture_yn=N)만
                week_points += _int(r[2]) if len(r) > 2 else 0
                quest_done += 1
        return {'name': name, 'rank': rank or '-', 'warning': warning,
                'week_points': week_points, 'quest_done': quest_done}
    except Exception as e:
        print(f'오류: {e}')
        return None


# discord_id(D열) 로 테런 닉네임(A열) 조회 — 자동 본인 인식 (/길퀘불가사유 등)
def get_user_by_discord(discord_id):
    try:
        worksheet = get_worksheet('user')
        cell = worksheet.find(str(discord_id), in_column=4)
        if not cell:
            return None
        rank = worksheet.cell(cell.row, 3).value
        if rank == '탈퇴':
            return None
        return worksheet.cell(cell.row, 1).value
    except Exception as e:
        print(f'오류: {e}')
        return None


# 활동 길드원 닉 목록 (자동완성용). 캐시된 레코드 재사용 → 빠름.
def list_active_users():
    try:
        users, _p, _d, _r = get_dashboard_records()
        return [r[0] for r in users if r and r[0] and (len(r) < 3 or r[2] != '탈퇴')]
    except Exception:
        return []


# 이번주 길퀘 미완료(남은>0) & 불가사유 미등록 활동 길드원 — 리마인드용.
#   반환: [{name, discord_id, left}]
def get_incomplete_members(limit=2):
    try:
        users, points, denies, _ranks = get_dashboard_records()
        wk_start, wk_end = week_range()
        did, active = {}, []
        for r in users:
            name = r[0] if r else ''
            rank = r[2] if len(r) > 2 else ''
            if not name or rank == '탈퇴':
                continue
            active.append(name)
            did[name] = r[3] if len(r) > 3 else ''
        done = {}
        for r in points:
            if not r:
                continue
            d = _int(r[1]) if len(r) > 1 else 0
            cap = (r[3].strip().upper() if len(r) > 3 else '')
            if wk_start <= d <= wk_end and cap == 'N':
                done[r[0]] = done.get(r[0], 0) + 1
        denied = {r[0] for r in denies if r and len(r) > 1 and wk_start <= _int(r[1]) <= wk_end}
        out = []
        for name in active:
            if name in denied:
                continue
            left = limit - done.get(name, 0)
            if left > 0:
                out.append({'name': name, 'discord_id': did.get(name, ''), 'left': left})
        return out
    except Exception as e:
        print(f'오류: {e}')
        return None  # 조회 실패 — 호출측이 '완료(빈 리스트)'와 구분하게


# 원클릭 시트 준비 (/초기화): 4탭 존재 보장 + user_rank 등급 시드(비어있을 때만).
@_locked
def init_sheets():
    try:
        for name in ('user', 'user_rank', 'user_point', 'quest_deny_reason'):
            get_worksheet(name)  # 없으면 헤더와 함께 생성
        rank_ws = get_worksheet('user_rank')
        vals = rank_ws.get_all_values()
        # 헤더가 없으면(완전 빈 시트) 먼저 헤더부터 — 등급이 항상 2행에서 시작하도록
        if not vals or not any(vals[0]):
            rank_ws.append_row(['rank_name', 'rank_cnt'], value_input_option='RAW')
            vals = [['rank_name', 'rank_cnt']]
        seeded = False
        if len(vals) <= 1:  # 헤더만 = 등급 없음
            start = len(vals) + 1  # 첫 데이터 행 (헤더 1행이면 2)
            rank_ws.append_rows([['길마', 0], ['서마', 0], ['명예', 0], ['우수', 0], ['일반', 0]],
                                value_input_option='RAW')
            # 전체 = 방금 넣은 5개 등급 합 (하드코딩 대신 실제 행 범위로)
            rank_ws.append_row(['전체', f'=SUM(B{start}:B{start + 4})'], value_input_option='USER_ENTERED')
            seeded = True
        return True, ('시트 준비 완료 — 4탭 확인'
                      + (' + 등급 6종 시드' if seeded else ' (user_rank 등급 이미 있음, 유지)'))
    except Exception as e:
        return False, f'오류: {e}'
    
# 특정 시트에서 A열 == name 인 모든 행 삭제 (아래에서 위로 삭제해 인덱스 밀림 방지)
def _delete_rows_by_name(worksheet, name):
    cells = worksheet.findall(name, in_column=1)
    rows = sorted({c.row for c in cells}, reverse=True)
    for r in rows:
        worksheet.delete_rows(r)
    return len(rows)


# 길드원 탈퇴 — 하드 삭제 (명세 #6): 등급 -1 후 user/user_point/quest_deny_reason 행 삭제
@_locked
def remove_user(user_name):
    try:
        worksheet = get_worksheet('user')
        find_data = worksheet.find(user_name, in_column=1)
        if not find_data:
            msg = f'{user_name}은(는) 존재하지 않는 유저입니다!'  # 명세 문구
            print(msg)
            return False, msg

        # 1) 해당 유저의 등급 인원수 -1 (명세 #6-2)
        current_rank = worksheet.cell(find_data.row, 3).value
        rank_worksheet = get_worksheet('user_rank')
        find_rank = rank_worksheet.find(current_rank, in_column=1) if current_rank else None
        if find_rank:
            rank_cur = _int(rank_worksheet.cell(find_rank.row, 2).value)
            rank_worksheet.update_cell(find_rank.row, 2, max(0, rank_cur - 1))

        # 2) user 행 삭제 + user_point / quest_deny_reason 관련 행 전부 삭제 (명세 #6-3)
        worksheet.delete_rows(find_data.row)
        _delete_rows_by_name(get_worksheet('user_point'), user_name)
        _delete_rows_by_name(get_worksheet('quest_deny_reason'), user_name)

        msg = f'{user_name}님이 탈퇴 처리되었습니다 (데이터 삭제)'
        print(msg)
        return True, msg

    except Exception as e:
        err_msg = f'오류: {e}'
        print(err_msg)
        return False, err_msg


# ── 퀘스트 포인트/사유 ──────────────────────────────────────────

# user_point 에 여러 명 일괄 INSERT (/체크·/점령 공용). 등록된 활동 길드원만 반영.
#   반환: (성공여부, 반영된 닉 목록, 미등록으로 건너뛴 닉 목록)
@_locked
def add_points(names, point, capture_yn, point_date, reflection_yn='N'):
    try:
        user_ws = get_worksheet('user')
        point_ws = get_worksheet('user_point')

        active = set()
        for r in user_ws.get_all_values()[1:]:
            name = r[0] if r else ''
            rank = r[2] if len(r) > 2 else ''
            if name and rank != '탈퇴':
                active.add(name)

        known, unknown, seen = [], [], set()
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            (known if n in active else unknown).append(n)

        if not known:
            return True, known, unknown

        # 같은 (닉, 날짜, capture_yn) 의 '미반영(reflection_yn=N)' 행이 있으면 점수를 합산 업데이트.
        #   같은 날 추가 지급(예: 0701 2점 뒤 0701 3점)이 두 줄로 안 쌓이고 0701 5점 한 줄이 되게.
        cap = str(capture_yn).strip().upper()
        pdate = str(point_date).strip()
        rows = point_ws.get_all_values()
        existing = {}  # (name, date, cap) -> (row_index, current_point)
        for i, r in enumerate(rows[1:], start=2):
            if len(r) >= 5 and r[4].strip().upper() == 'N':
                key = (r[0], str(r[1]).strip(), r[3].strip().upper())
                existing[key] = (i, _int(r[2]))

        to_append = []
        for n in known:
            hit = existing.get((n, pdate, cap))
            if hit:
                row_i, cur = hit
                point_ws.update_cell(row_i, 3, cur + point)   # 합산
            else:
                to_append.append([n, point_date, point, capture_yn, reflection_yn])
        if to_append:
            point_ws.append_rows(to_append, value_input_option='RAW')  # 닉 수식 인젝션 차단

        # 날짜(B열) 오름차순 자동 정렬 (헤더 제외)
        total = len(point_ws.get_all_values())
        if total > 2:
            point_ws.sort((2, 'asc'), range=f'A2:E{total}')
        return True, known, unknown
    except Exception as e:
        print(f'오류: {e}')
        return False, [], names


# quest_deny_reason INSERT — 일요일 차단, 이번주(월~토) 1회 제한
@_locked
def add_deny_reason(user_name, reason):
    try:
        if is_sunday():
            return False, '토요일까지만 등록 가능합니다!'
        ws = get_worksheet('quest_deny_reason')
        wk_start, wk_end = week_range()
        for r in ws.get_all_values()[1:]:
            if r and r[0] == user_name and len(r) > 1 and wk_start <= _int(r[1]) <= wk_end:
                return False, '이번주에는 이미 등록된 사유가 있습니다!'
        # RAW = 사유를 문자 그대로 저장. 전체 길드원이 쓰는 명령이라 =IMAGE/=IMPORTDATA 등
        #   수식 인젝션(서비스계정 권한으로 서버측 평가 → 데이터 유출) 을 원천 차단한다.
        ws.append_row([user_name, today_int(), reason], value_input_option='RAW')
        return True, f'길퀘 불가 사유가 등록되었습니다: {reason}'
    except Exception as e:
        return False, f'오류: {e}'


# 명예/우수 계산 (명세 #7): reflection_yn=N 포인트 합산 → 현재 명예/우수 인원수만큼 상위 배정,
#   계산에 쓴 reflection_yn=N 행 전부 Y 로 마감. 길마/서마 제외. 반환: (ok, 명예리스트, 우수리스트, 처리행수)
@_locked
def calc_honor_excellent():
    try:
        user_ws = get_worksheet('user')
        point_ws = get_worksheet('user_point')
        rank_ws = get_worksheet('user_rank')

        rank_cnt = {r[0]: _int(r[1]) for r in rank_ws.get_all_values()[1:] if r and r[0]}
        honor_n, excel_n = rank_cnt.get('명예', 0), rank_cnt.get('우수', 0)

        rank_of = {}
        for r in user_ws.get_all_values()[1:]:
            name = r[0] if r else ''
            rank = r[2] if len(r) > 2 else ''
            if name and rank != '탈퇴':
                rank_of[name] = rank

        refl = defaultdict(int)
        pending_rows = []  # reflection_yn=N 인 1-based 행 인덱스
        for i, r in enumerate(point_ws.get_all_values()[1:], start=2):
            reflect = (r[4].strip().upper() if len(r) > 4 else '')
            if reflect == 'N':
                pending_rows.append(i)
                name = r[0] if r else ''
                if name:
                    refl[name] += _int(r[2]) if len(r) > 2 else 0

        EXCLUDE = ('길마', '서마')
        cand = sorted(
            ((p, n) for n, p in refl.items()
             if p > 0 and n in rank_of and rank_of[n] not in EXCLUDE),
            key=lambda x: (-x[0], x[1]))
        honor = [n for _, n in cand[:honor_n]]
        excel = [n for _, n in cand[honor_n:honor_n + excel_n]]

        # 계산에 반영된 행 전부 reflection_yn=Y 로 마감 (E열=5).
        #   ⚠️ 행별 update_cell 루프는 중간 실패 시 일부만 Y 로 커밋돼 포인트가 영구 유실된다.
        #   update_cells 는 단일 batch 요청이라 성공/실패가 전부-아니면-전무(원자적).
        if pending_rows:
            cells = [gspread.Cell(row=r, col=5, value='Y') for r in pending_rows]
            point_ws.update_cells(cells)

        return True, honor, excel, len(pending_rows)
    except Exception as e:
        print(f'오류: {e}')
        return False, [], [], 0


# 주간 배치 (명세 #4): 이번주 점령(capture_yn=Y) 미참여 & warning>0 인 길드원 warning -1
#   ⚠️ 명세 그대로 '미참여자 차감' 구현. 반대(미참여자 +1)를 원하면 아래 부호만 바꾸면 됨.
@_locked
def decrement_warnings_nonparticipants():
    try:
        user_ws = get_worksheet('user')
        point_ws = get_worksheet('user_point')
        wk_start, wk_end = week_range()

        participated = set()
        for r in point_ws.get_all_values()[1:]:
            name = r[0] if r else ''
            cap = (r[3].strip().upper() if len(r) > 3 else '')
            d = _int(r[1]) if len(r) > 1 else 0
            if name and cap == 'Y' and wk_start <= d <= wk_end:
                participated.add(name)

        changed = 0
        for i, r in enumerate(user_ws.get_all_values()[1:], start=2):
            name = r[0] if r else ''
            rank = r[2] if len(r) > 2 else ''
            if not name or rank == '탈퇴':
                continue
            warning = _int(r[1]) if len(r) > 1 else 0
            if name not in participated and warning > 0:
                user_ws.update_cell(i, 2, warning - 1)
                changed += 1
        return True, f'주간 경고차감 완료 — {changed}명'
    except Exception as e:
        print(f'오류: {e}')
        return False, f'오류: {e}'

    
# user 시트에서 해당 길드원의 등급(rank_name) 변경
@_locked
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


# 개인 등급 변경 + 인원수 자동 반영 (/등급변경). old 등급 -1, new 등급 +1 로 rank_cnt 정합 유지.
@_locked
def change_user_rank(user_name, new_rank):
    try:
        if new_rank == '전체':
            return False, '전체는 지정할 수 없습니다 (자동 계산)'
        if new_rank not in VALID_RANKS:
            return False, f'잘못된 등급입니다. (가능: {", ".join(VALID_RANKS)})'

        user_ws = get_worksheet('user')
        find = user_ws.find(user_name, in_column=1)
        if not find:
            return False, f'{user_name}은(는) 존재하지 않는 유저입니다!'

        old_rank = user_ws.cell(find.row, 3).value
        if old_rank == '탈퇴':
            return False, f'{user_name}님은 탈퇴 상태입니다'
        if old_rank == new_rank:
            return False, f'{user_name}님은 이미 {new_rank}등급입니다'

        # 개인 rank_name 변경
        user_ws.update_cell(find.row, 3, new_rank)

        # 인원수 정합: old -1, new +1 (전체/미존재 등급은 건너뜀)
        rank_ws = get_worksheet('user_rank')
        for rank_name, delta in ((old_rank, -1), (new_rank, +1)):
            if not rank_name or rank_name == '전체':
                continue
            fr = rank_ws.find(rank_name, in_column=1)
            if fr:
                cur = _int(rank_ws.cell(fr.row, 2).value)
                rank_ws.update_cell(fr.row, 2, max(0, cur + delta))

        try:
            _sort_users()   # 등급이 바뀌었으니 시트를 등급순으로 정돈 (LO: 뒤죽박죽 방지)
        except Exception as e:
            print(f'[sort] 건너뜀: {e}')
        return True, f'{user_name}님의 등급이 {old_rank} → {new_rank}(으)로 변경되었습니다!'
    except Exception as e:
        return False, f'오류: {e}'