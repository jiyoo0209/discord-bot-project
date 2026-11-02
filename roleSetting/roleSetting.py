'''
* 권한 체크(has_role) · 닉네임 자동완성 · 명예우수 역할 동기화 — 명령(/증가 /감소 /등급변경 /역할설정 /역할확인)은 제거됨
* author HDG
* date   2026.06.30
'''
import asyncio
import discord
from discord import app_commands
from sheet.sheet import list_active_users
from config.guild_config import get_role_id


# 닉네임 파라미터 자동완성 — 등록 길드원 목록에서 입력값 포함 항목 (최대 25).
#   이 자동완성이 붙는 명령(/길퀘체크제외)은 길마·서마 전용이므로, 권한 없는 사용자에게는
#   빈 목록을 줘 로스터(길드원 명단) 열람을 막는다.
async def user_autocomplete(interaction: discord.Interaction, current: str):
    if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
        return []
    names = await asyncio.to_thread(list_active_users)
    cur = (current or '').lower()
    return [app_commands.Choice(name=n, value=n) for n in names if cur in n.lower()][:25]


# 여러 명(공백 구분) 입력 자동완성 — 마지막 토큰을 시트 닉으로 채워 누적. /체크·/점령 용.
#   현재값 "바보 냥" → "바보 냥달" 제안. 고르면 이어서 다음 이름을 또 자동완성.
async def members_autocomplete(interaction: discord.Interaction, current: str):
    if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
        return []
    names = await asyncio.to_thread(list_active_users)
    text = current or ''
    parts = text.split()
    if text.endswith(' ') or not parts:      # 공백으로 끝 → 새 이름 시작
        chosen, partial = parts, ''
    else:
        chosen, partial = parts[:-1], parts[-1]
    prefix = (' '.join(chosen) + ' ') if chosen else ''
    chosen_set = set(chosen)
    p = partial.lower()
    out = []
    for n in names:
        if n in chosen_set:
            continue
        if p in n.lower():
            val = prefix + n
            if len(val) <= 100:              # Choice value 최대 100자
                out.append(app_commands.Choice(name=val, value=val))
        if len(out) >= 25:
            break
    return out

async def sync_rank_roles(guild, changes):
    '''명예우수집계 등급 변경을 디스코드 역할에 반영.
       changes: [(닉, discord_id, 이전등급, 새등급)] → 명예/우수/일반 역할 중 새 등급만 남기고 나머지 제거.
       반환: 실패 문구 목록'''
    fails = []
    ids = {k: get_role_id(guild.id, k) for k in ('명예', '우수', '일반')}
    roles = {k: guild.get_role(v) for k, v in ids.items() if v}
    for name, did, old, new in changes:
        if new not in roles:
            fails.append(f'{name}: `{new}` 역할 매핑 없음')
            continue
        member = guild.get_member(int(did)) if str(did).isdigit() else None
        if member is None and str(did).isdigit():
            try:
                member = await guild.fetch_member(int(did))
            except Exception:
                member = None
        if member is None:
            fails.append(f'{name}: 서버에서 찾을 수 없음')
            continue
        try:
            remove = [r for k, r in roles.items() if k != new and r in member.roles]
            if remove:
                await member.remove_roles(*remove, reason='명예우수집계')
            if roles[new] not in member.roles:
                await member.add_roles(roles[new], reason='명예우수집계')
        except discord.Forbidden:
            fails.append(f'{name}: 권한 부족 (봇 역할 위치 확인)')
        except Exception as e:
            fails.append(f'{name}: {e}')
    return fails


def rank_change_text(changes, fails):
    '''집계 결과 메시지에 붙일 등급 변경 요약'''
    if not changes:
        return '\n-# 등급 변경 없음'
    text = '\n**등급 변경**: ' + ', '.join(f'{n} {o or "미지정"}→{w}' for n, _d, o, w in changes)
    if fails:
        text += '\n⚠️ 역할 변경 실패: ' + ', '.join(fails)
    return text


def has_role(interaction: discord.Interaction, key: str) -> bool:
    """명령을 친 사람이 이 서버에서 key(길마/서마/…)에 매핑된 역할을 가졌는지.
    서버 소유자는 역할 매핑 전에도 '모든' 게이트를 통과한다 → 역할 매핑 전 부트스트랩 편의."""
    if interaction.guild is None:
        return False  # DM 등 길드 밖 호출 방어 (반드시 역참조보다 먼저)
    if interaction.user.id == interaction.guild.owner_id:
        return True
    role_id = get_role_id(interaction.guild.id, key)
    if role_id is None:
        return False
    return any(role.id == role_id for role in interaction.user.roles)
