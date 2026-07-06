'''
* 등급 인원수 관리 (증가/감소/설정)
* author HDG
* date   2026.06.30
'''
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from sheet.sheet import update_rank_cnt, change_user_rank, VALID_RANKS, list_active_users
from config.guild_config import (
    ROLE_KEYS, get_role_id, set_role_id, get_guild_config,
)


# 닉네임 파라미터 자동완성 — 등록 길드원 목록에서 입력값 포함 항목 (최대 25).
#   이 자동완성이 붙는 명령(/탈퇴·/등급변경)은 길마·서마 전용이므로, 권한 없는 사용자에게는
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

# 등급 선택지 (VALID_RANKS에서 자동 생성 -> 등급 추가하면 세 명령어 모두 반영)
RANK_CHOICES = [app_commands.Choice(name=r, value=r) for r in VALID_RANKS]
# 논리 역할 선택지 (/역할설정 용)
ROLE_KEY_CHOICES = [app_commands.Choice(name=k, value=k) for k in ROLE_KEYS]


def has_role(interaction: discord.Interaction, key: str) -> bool:
    """명령을 친 사람이 이 서버에서 key(길마/서마/…)에 매핑된 역할을 가졌는지.
    서버 소유자는 역할 매핑 전에도 '모든' 게이트를 통과한다 → 최초 /셋업 부트스트랩 편의
    (매핑이 없으면 /셋업 이 막혀 /역할설정 을 먼저 해야 하는 닭-달걀 문제 해소)."""
    if interaction.guild is None:
        return False  # DM 등 길드 밖 호출 방어 (반드시 역참조보다 먼저)
    if interaction.user.id == interaction.guild.owner_id:
        return True
    role_id = get_role_id(interaction.guild.id, key)
    if role_id is None:
        return False
    return any(role.id == role_id for role in interaction.user.roles)


@app_commands.guild_only()
class RoleSetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── 서버별 역할 매핑 설정 (봇을 새 서버에 붙였을 때 최초 1회) ──
    @app_commands.command(name='역할설정', description='(서버 소유자/관리자) 논리 역할 ↔ 서버 역할 매핑')
    @app_commands.describe(구분='설정할 역할 종류', 역할='매핑할 서버 역할')
    @app_commands.choices(구분=ROLE_KEY_CHOICES)
    async def set_role(self, interaction: discord.Interaction,
                       구분: app_commands.Choice[str], 역할: discord.Role):
        # 길마 매핑을 탈취해 전권을 얻는 걸 막으려, 흔한 '서버 관리'가 아니라
        #   서버 소유자 또는 관리자(Administrator) 로만 제한한다.
        is_owner = interaction.user.id == interaction.guild.owner_id
        if not (is_owner or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message('서버 소유자 또는 관리자만 사용 가능합니다!', ephemeral=True)
            return
        set_role_id(interaction.guild.id, 구분.value, 역할.id)
        await interaction.response.send_message(
            f'`{구분.value}` → {역할.mention} 로 설정되었습니다.', ephemeral=True)

    @app_commands.command(name='역할확인', description='이 서버의 역할 매핑 현황')
    async def show_roles(self, interaction: discord.Interaction):
        cfg = get_guild_config(interaction.guild.id)
        lines = []
        for k in ROLE_KEYS:
            rid = cfg.get(k)
            role = interaction.guild.get_role(rid) if rid else None
            lines.append(f'- {k}: {role.mention if role else "❌ 미설정"}')
        await interaction.response.send_message('\n'.join(lines), ephemeral=True)

    @app_commands.command(name='증가', description='등급 인원수를 증가')
    @app_commands.describe(등급='대상 등급', 숫자='증가시킬 인원 수 (예: 3)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def increase(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 1:
            await interaction.response.send_message('1 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success, message = await asyncio.to_thread(update_rank_cnt, 등급.value, 숫자, 'delta')
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name='감소', description='등급 인원수를 감소')
    @app_commands.describe(등급='대상 등급', 숫자='감소시킬 인원 수 (예: 3)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def decrease(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 1:
            await interaction.response.send_message('1 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success, message = await asyncio.to_thread(update_rank_cnt, 등급.value, -숫자, 'delta')
        await interaction.followup.send(message, ephemeral=True)

    '''
    2026.07.01 : 주석처리
    등급 설정은 증감으로 사용 - 필요하면 추후 개발 예정

    @app_commands.command(name='설정', description='등급 인원수를 설정')
    @app_commands.describe(등급='대상 등급', 숫자='설정할 인원 수 (예: 5)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def set_cnt(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role_level(interaction, 1):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 0:
            await interaction.response.send_message('0 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        success, message = update_rank_cnt(등급.value, 숫자, 'set')
        await interaction.response.send_message(message, ephemeral=not success)
    '''

    # 개인 등급 변경 (가입 시 일반 → 명예/우수/길마/서마 등).
    #   user_rank(rank_cnt=다음 달 정원)는 건드리지 않음 — 정원은 /증가·/감소 로만 관리.
    @app_commands.command(name='등급변경', description='길드원 개인 등급 변경')
    @app_commands.describe(닉네임='대상 테런 닉네임', 등급='새 등급')
    @app_commands.choices(등급=RANK_CHOICES)
    @app_commands.autocomplete(닉네임=user_autocomplete)
    async def change_rank(self, interaction: discord.Interaction,
                          닉네임: str, 등급: app_commands.Choice[str]):
        # 등급 버튼(길마·서마)과 권한 통일 — 버튼이 25명 초과 시 이 명령으로 안내하므로 서마도 허용
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success, message = await asyncio.to_thread(change_user_rank, 닉네임, 등급.value)
        await interaction.followup.send(message, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RoleSetting(bot))