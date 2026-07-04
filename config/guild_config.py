'''
* 서버(길드)별 역할 매핑 저장/조회 — 역할 ID 하드코딩 제거
* author ENI
* date   2026.07.01
*
* 논리 역할(길마/서마/명예/우수/일반/입장대기) <-> 각 서버의 실제 역할 ID 를
* guild_config.json 에 길드별로 저장한다. 봇을 어느 서버에 붙여도 관리자가
* /역할설정 으로 매핑만 잡으면 그대로 동작한다.
'''
import json
import os
import threading

# 명령/권한에서 쓰는 논리 역할 키 (순서 = /역할확인 출력 순서)
ROLE_KEYS = ['길마', '서마', '명예', '우수', '일반', '입장대기']

_PATH = os.path.join(os.path.dirname(__file__), '..', 'guild_config.json')
_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is None:
        # 본파일이 없거나 깨졌으면 .bak(직전 정상본)으로 폴백 — 부분쓰기/손상 시 설정 통째 유실 방지
        for path in (_PATH, _PATH + '.bak'):
            try:
                with open(path, encoding='utf-8') as f:
                    _cache = json.load(f)
                break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        if _cache is None:
            _cache = {}
    return _cache


def _save():
    '''원자적 저장 + 직전 정상본을 .bak 으로 보존.
       temp 에 쓴 뒤 os.replace 로 커밋해 부분쓰기로 파일이 깨지지 않게, 그리고 교체 직전
       기존 파일을 .bak 으로 남겨 실수/손상 시 되살릴 수 있게 한다 (설정=DB라 유실=봇 전체 마비).'''
    tmp = _PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)
    if os.path.exists(_PATH):
        try:
            os.replace(_PATH, _PATH + '.bak')
        except OSError:
            pass
    os.replace(tmp, _PATH)


def get_guild_config(guild_id):
    '''해당 길드의 {역할키: role_id} dict 반환 (없으면 빈 dict)'''
    return _load().get(str(guild_id), {})


def get_role_id(guild_id, key):
    '''길드+논리역할키 -> 서버 역할 ID (미설정이면 None)'''
    return get_guild_config(guild_id).get(key)


def _set_key(guild_id, key, value):
    '''한 키 저장. _save() 가 실패하면 인메모리 캐시를 원복해 디스크와의 desync 를 막고 예외를 다시 던진다
       (안 그러면 get_* 이 재시작 전까지 저장 안 된 값을 반환하다 재시작 시 조용히 롤백됨).'''
    with _lock:
        g = _load().setdefault(str(guild_id), {})
        had, old = key in g, g.get(key)
        g[key] = value
        try:
            _save()
        except Exception:
            if had:
                g[key] = old
            else:
                g.pop(key, None)
            raise


def set_role_id(guild_id, key, role_id):
    '''매핑 저장 (즉시 파일 반영)'''
    _set_key(guild_id, key, int(role_id))


def missing_keys(guild_id):
    '''아직 설정 안 된 논리 역할 키 목록'''
    cfg = get_guild_config(guild_id)
    return [k for k in ROLE_KEYS if k not in cfg]


# ── 범용 설정 (역할 매핑 외: 대시보드 채널/메시지, 배치 실행 주차 등) ──
#   역할 키와 안 겹치게 '_' 접두 키를 쓴다. 전역값은 guild_id='_global' 로 저장.
def get_setting(guild_id, key, default=None):
    return get_guild_config(guild_id).get(key, default)


def set_setting(guild_id, key, value):
    _set_key(guild_id, key, value)
