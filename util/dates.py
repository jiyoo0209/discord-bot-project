'''
* 주간(월~토) 경계 · 날짜 헬퍼 — 여러 모듈이 공유
* 날짜는 시트 저장 형식과 동일하게 int YYYYMMDD 로 다룬다.
'''
from datetime import date, timedelta


def _to_int(d):
    return int(d.strftime('%Y%m%d'))


def today_int():
    '''오늘 (YYYYMMDD int)'''
    return _to_int(date.today())


def week_range(d=None):
    '''이번 주 월요일~토요일 (YYYYMMDD int) 튜플'''
    if d is None:
        d = date.today()
    monday = d - timedelta(days=d.weekday())      # weekday: 월=0 … 일=6
    saturday = monday + timedelta(days=5)
    return _to_int(monday), _to_int(saturday)


def this_saturday(d=None):
    '''이번 주 토요일 (YYYYMMDD int) — /점령 의 point_date'''
    if d is None:
        d = date.today()
    monday = d - timedelta(days=d.weekday())
    return _to_int(monday + timedelta(days=5))


def is_sunday(d=None):
    '''오늘이 일요일인가 — /길퀘불가사유 차단 · 주간 배치 트리거'''
    if d is None:
        d = date.today()
    return d.weekday() == 6


def is_saturday(d=None):
    '''오늘이 토요일인가 — 주 후반 길퀘 미완료 리마인드 트리거'''
    if d is None:
        d = date.today()
    return d.weekday() == 5


def parse_date_arg(s):
    '''사용자 입력 날짜 → YYYYMMDD int. 허용: '0701'(MMDD), '20260701', '2026-07-01', '07-01'.
       달력상 실제 존재하는 날짜만 통과(2/31·13월·00000000 등은 None).
       MMDD 는 소급 지급용이라, 올해로 계산한 날짜가 미래면 작년으로 본다(연초 전년 보정).'''
    if not s:
        return None
    digits = ''.join(ch for ch in str(s) if ch.isdigit())
    if len(digits) == 8:
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    elif len(digits) == 4:
        m, d = int(digits[:2]), int(digits[2:4])
        y = date.today().year
        try:
            if date(y, m, d) > date.today():
                y -= 1
        except ValueError:
            return None
    else:
        return None
    try:
        date(y, m, d)          # 달력 유효성 최종 검증
    except ValueError:
        return None
    return y * 10000 + m * 100 + d
