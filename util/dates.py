'''
* 주간(월~일) 경계 · 날짜 헬퍼 — 여러 모듈이 공유
* 날짜는 시트 저장 형식과 동일하게 int YYYYMMDD 로 다룬다.
* '오늘'은 배포 서버 타임존과 무관하게 항상 KST(UTC+9) 기준 (kst_today).
'''
from datetime import date, datetime, timedelta, timezone

# 배포 서버가 UTC 등 KST가 아니면 date.today()/datetime.now() 는 하루가 어긋난다
#   (KST 00:00~09:00 = 시스템 UTC 로는 전날). 그래서 모든 '오늘'을 KST 로 고정한다.
KST = timezone(timedelta(hours=9))


def kst_today():
    '''KST(UTC+9) 기준 오늘 (date). 시스템 타임존과 무관.'''
    return datetime.now(KST).date()


def _to_int(d):
    return int(d.strftime('%Y%m%d'))


def today_int():
    '''오늘 (YYYYMMDD int, KST)'''
    return _to_int(kst_today())


def week_range(d=None):
    '''이번 주 월요일~일요일 (YYYYMMDD int) 튜플 — 일요일 포인트도 주간 집계에 포함 (LO 확정)'''
    if d is None:
        d = kst_today()
    monday = d - timedelta(days=d.weekday())      # weekday: 월=0 … 일=6
    sunday = monday + timedelta(days=6)
    return _to_int(monday), _to_int(sunday)


def this_saturday(d=None):
    '''이번 주 토요일 (YYYYMMDD int) — /점령 의 point_date'''
    if d is None:
        d = kst_today()
    monday = d - timedelta(days=d.weekday())
    return _to_int(monday + timedelta(days=5))


def is_sunday(d=None):
    '''오늘이 일요일인가 — /길퀘불가사유 차단 · 주간 배치 트리거'''
    if d is None:
        d = kst_today()
    return d.weekday() == 6


def is_saturday(d=None):
    '''오늘이 토요일인가 '''
    if d is None:
        d = kst_today()
    return d.weekday() == 5

def is_friday(d=None):
    '''오늘이 금요일인가 — 주 후반 길퀘 미완료 리마인드 트리거'''
    if d is None:
        d = kst_today()
    return d.weekday() == 4


def is_monday(d=None):
    '''오늘이 월요일인가 — 지난 주 경고 정산 배치 트리거'''
    if d is None:
        d = kst_today()
    return d.weekday() == 0


def last_week_range(d=None):
    '''저번 주 월요일~일요일 (YYYYMMDD int) 튜플 — 월요일 경고 정산의 집계 구간'''
    if d is None:
        d = kst_today()
    last_monday = d - timedelta(days=d.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return _to_int(last_monday), _to_int(last_sunday)


def last_saturday(d=None):
    '''저번 주 토요일 (YYYYMMDD int) — 점령(capture_yn=Y) 참여 판정용'''
    if d is None:
        d = kst_today()
    last_monday = d - timedelta(days=d.weekday() + 7)
    return _to_int(last_monday + timedelta(days=5))


def parse_date_arg(s):
    '''사용자 입력 날짜 → YYYYMMDD int. 허용: '0701'(MMDD), '20260701', '2026-07-01', '07-01'.
       달력상 실제 존재하는 날짜만 통과(2/31·13월·00000000 등은 None).
       MMDD 는 소급 지급용이라, 올해로 계산한 날짜가 미래면 작년으로 본다(연초 전년 보정, KST 기준).'''
    if not s:
        return None
    digits = ''.join(ch for ch in str(s) if ch.isdigit())
    if len(digits) == 8:
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    elif len(digits) == 4:
        m, d = int(digits[:2]), int(digits[2:4])
        y = kst_today().year
        try:
            if date(y, m, d) > kst_today():
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
