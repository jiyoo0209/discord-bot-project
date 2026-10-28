'''
* 퀘스트 포인트 명령 — /체크 /점령 /길퀘불가사유 /명예우수계산
* author ENI
* date   2026.07.01
'''
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from sheet.sheet import (
    add_points, add_deny_reason, calc_honor_excellent, get_user_by_discord,
    get_incomplete_members, get_week_daily_points, add_deny_weeks,
)
from dashboard.dashboard import WEEKLY_QUEST_LIMIT, _range_date
from roleSetting.roleSetting import has_role, members_autocomplete, user_autocomplete
from util.dates import today_int, this_saturday, parse_date_arg, kst_today
from datetime import timedelta


def _fmt_result(known, unknown):
    lines = []
    if known:
        lines.append('✅ 반영: ' + ', '.join(known))
    if unknown:
        lines.append('⚠️ 미등록(건너뜀): ' + ', '.join(unknown))
    return '\n'.join(lines) or '반영된 길드원이 없습니다.'


# 사용자 입력 닉을 공개 메시지로 되비칠 때 @everyone/역할 핑이 실제로 울리지 않게
_NO_PINGS = discord.AllowedMentions.none()

# /이번주길퀘확인 — 점수 → 키캡 이모지 (0~10). 범위 밖은 숫자 그대로.
_KEYCAPS = ['0️⃣', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
_WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']
_WEEK_ACCENT = discord.Colour(0x3FB8F3)   # 길드 대표 이미지(꽃)의 하늘색


def _week_table(daily):
    '''이번 주 월~일 세로 목록 (요일 한 줄씩). 오늘·과거는 점수(행 없으면 0), 미래는 요일만.'''
    today = kst_today()
    monday = today - timedelta(days=today.weekday())
    cells = []
    for i in range(7):
        d = monday + timedelta(days=i)
        if d > today:
            cells.append(f'**{_WEEKDAYS[i]}**')
            continue
        pt = daily.get(int(d.strftime('%Y%m%d')), 0)
        cells.append(f'**{_WEEKDAYS[i]}** ' + (_KEYCAPS[pt] if 0 <= pt < len(_KEYCAPS) else str(pt)))
    return '\n'.join(cells)


@app_commands.guild_only()
class Quest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # /체크 <점수> <길드원들> — 여러 명에게 길퀘 점수 부여 (capture_yn=N)
    @app_commands.command(name='체크', description='길드원들에게 길퀘 점수 부여')
    @app_commands.describe(점수='부여할 점수', 길드원들='공백으로 구분한 테런 닉네임들 (자동완성)',
                           날짜='선택: 소급 지급 날짜 (예: 0701). 비우면 오늘. 같은 날짜는 마지막 입력값으로 덮어씀')
    @app_commands.autocomplete(길드원들=members_autocomplete)
    async def check(self, interaction: discord.Interaction, 점수: int, 길드원들: str, 날짜: str = None):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        names = 길드원들.split()
        if not names:
            await interaction.response.send_message('길드원을 1명 이상 입력하세요!', ephemeral=True)
            return
        pdate = today_int()
        if 날짜:
            pdate = parse_date_arg(날짜)
            if pdate is None:
                await interaction.response.send_message(
                    '날짜 형식 오류입니다. 예: `0701` 또는 `20260701`', ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        ok, known, unknown = await asyncio.to_thread(add_points, names, 점수, 'N', pdate)
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했습니다.', ephemeral=True)
            return
        ds = str(pdate)
        head = f'**{점수}점** 체크' + (f' ({ds[4:6]}/{ds[6:8]} 소급)' if 날짜 else '')
        await interaction.followup.send(head + '\n' + _fmt_result(known, unknown),
                                        ephemeral=True, allowed_mentions=_NO_PINGS)

    # /점령 <길드원들> — 점령 참여 기록 (point 8, capture_yn=Y, 날짜=이번주 토요일)
    @app_commands.command(name='점령', description='점령 참여 기록 (8점)')
    @app_commands.describe(길드원들='공백으로 구분한 테런 닉네임들 (자동완성)')
    @app_commands.autocomplete(길드원들=members_autocomplete)
    async def occupy(self, interaction: discord.Interaction, 길드원들: str):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        names = 길드원들.split()
        if not names:
            await interaction.response.send_message('길드원을 1명 이상 입력하세요!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, known, unknown = await asyncio.to_thread(
            add_points, names, 8, 'Y', this_saturday())
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했습니다.', ephemeral=True)
            return
        await interaction.followup.send('**점령 참여** 기록 (8점)\n' + _fmt_result(known, unknown),
                                        ephemeral=True, allowed_mentions=_NO_PINGS)

    # /이번주길퀘확인 — 본인 이번 주(월~일) 요일별 길퀘 점수 (capture_yn=N 만). 전체 길드원, 채널에 공개.
    @app_commands.command(name='이번주길퀘확인', description='내 이번주 요일별 길퀘 점수 확인')
    async def week_check(self, interaction: discord.Interaction):
        await interaction.response.defer()
        res = await asyncio.to_thread(get_week_daily_points, interaction.user.id)
        if not res:
            await interaction.followup.send(
                '등록된 길드원이 아닙니다. 먼저 관리자에게 `/가입` 을 요청하세요.')
            return
        name, daily = res
        # 다른 버튼/카드와 같은 Components V2 카드로 표시 (닉은 마크다운이라 멘션 핑 없음)
        card = discord.ui.LayoutView(timeout=None)
        card.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## 🗡️ {name} 님의 이번주 길퀘'),
            discord.ui.Separator(),
            discord.ui.TextDisplay(_week_table(daily)),
            accent_colour=_WEEK_ACCENT,
        ))
        await interaction.followup.send(view=card, allowed_mentions=_NO_PINGS)

    # /길퀘체크제외 <닉네임> <기간(주)> <사유> [날짜] — 관리자(길마·서마)가 대신 체크제외(=길퀘불가사유) 등록.
    #   날짜가 속한 주부터 N주. 날짜 없으면 오늘이 속한 주부터. 이미 사유가 있는 주는 건너뜀.
    @app_commands.command(name='길퀘체크제외', description='(길마/서마) 길드원 길퀘 체크제외 등록 (N주)')
    @app_commands.describe(닉네임='대상 테런 닉네임 (자동완성)', 기간='제외할 주 수 (예: 3 = 3주)',
                           사유='체크제외 사유 (예: 개인사정)',
                           날짜='선택: 시작 날짜 (예: 1103). 그 날짜가 속한 주부터. 비우면 이번 주부터')
    @app_commands.autocomplete(닉네임=user_autocomplete)
    async def deny_weeks(self, interaction: discord.Interaction, 닉네임: str,
                         기간: app_commands.Range[int, 1, 52],
                         사유: app_commands.Range[str, 1, 100], 날짜: str = None):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        start = today_int()
        if 날짜:
            start = _range_date(날짜)   # MMDD 는 올해 기준 (미래 시작일 허용)
            if start is None:
                await interaction.response.send_message(
                    '날짜 형식 오류입니다. 예: `1103` 또는 `20261103`', ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        ok, msg, added, skipped = await asyncio.to_thread(add_deny_weeks, 닉네임.strip(), start, 기간,
                                                          (사유 or '').strip() or '관리자 체크제외')
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        def _md(d):  # 20261102 → 11/02
            s = str(d)
            return f'{s[4:6]}/{s[6:8]}'
        lines = [f'**{닉네임}** 길퀘 체크제외 — {기간}주 ({_md(start)} 주부터)',
                 f'사유: {(사유 or "").strip() or "관리자 체크제외"}']
        if added:
            lines.append('✅ 등록: ' + ', '.join(f'{_md(d)}주' for d in added))
        if skipped:
            lines.append('⚠️ 이미 사유 있음(건너뜀): ' + ', '.join(f'{_md(d)}주' for d in skipped))
        await interaction.followup.send('\n'.join(lines), ephemeral=True, allowed_mentions=_NO_PINGS)

    # /길퀘불가사유 <사유> — 본인(디스코드↔테런닉 매핑)으로 등록. 주 1회, 일요일 불가.
    @app_commands.command(name='길퀘불가사유', description='이번주 길퀘 불가 사유 등록')
    @app_commands.describe(사유='길퀘 불가 사유')
    async def deny_reason(self, interaction: discord.Interaction, 사유: str):
        await interaction.response.defer(ephemeral=True)
        name = await asyncio.to_thread(get_user_by_discord, interaction.user.id)
        if not name:
            await interaction.followup.send(
                '등록된 길드원이 아닙니다. 먼저 관리자에게 `/가입` 을 요청하세요.', ephemeral=True)
            return
        ok, message = await asyncio.to_thread(add_deny_reason, name, 사유)
        await interaction.followup.send(message, ephemeral=True)

    # /명예우수계산 — reflection_yn=N 포인트로 명예/우수 산정 후 Y 로 마감
    @app_commands.command(name='명예우수계산', description='명예/우수 예상 계산 및 반영 마감')
    async def calc(self, interaction: discord.Interaction):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, honor, excel, n = await asyncio.to_thread(calc_honor_excellent)
        if not ok:
            await interaction.followup.send('시트 오류로 처리하지 못했습니다.', ephemeral=True)
            return
        embed = discord.Embed(title='명예/우수 계산 결과', color=0x5865F2)
        embed.add_field(name='명예', value='\n'.join(f'- {x}' for x in honor) or '- 없음', inline=False)
        embed.add_field(name='우수', value='\n'.join(f'- {x}' for x in excel) or '- 없음', inline=False)
        embed.set_footer(text=f'{n}개 포인트 반영 완료 (reflection_yn → Y)')
        await interaction.followup.send(embed=embed, ephemeral=True)


    # 이번주 길퀘 미완료자에게 공개 멘션 리마인드 + 개인 DM (핑 울림 — 의도적 공개)
    @app_commands.command(name='리마인드', description='이번주 길퀘 미완료자에게 리마인드 (채널 멘션 + 개인 DM)')
    async def remind(self, interaction: discord.Interaction):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer()
        members = await asyncio.to_thread(get_incomplete_members, WEEKLY_QUEST_LIMIT)
        if members is None:
            await interaction.followup.send('시트 조회 실패 — 잠시 후 다시 시도하세요.', ephemeral=True)
            return
        if not members:
            await interaction.followup.send('🎉 이번주 모두 길퀘 완료!')
            return
        tags = [(f"<@{m['discord_id']}>" if m['discord_id'] else m['name']) + f"({m['left']})"
                for m in members]
        # 인원 많으면 2000자 초과로 전송 실패 → 1900자 단위로 나눠서 여러 번 전송
        pings = discord.AllowedMentions(users=True)
        cur = '⏰ **이번주 남은 길퀘가 있어요!**'
        for t in tags:
            if len(cur) + 1 + len(t) > 1900:
                await interaction.followup.send(cur, allowed_mentions=pings)
                cur = t
            else:
                cur += ' ' + t
        await interaction.followup.send(cur, allowed_mentions=pings)
        # 개인 DM 도 함께 발송 — 문구는 금요일 자정 배치(FRIDAY_DM)와 동일, 결과는 실행자에게만 표시
        from batch.batch import FRIDAY_DM   # 지역 import — 순환 방지 (문구 단일 관리)
        dm_ok = dm_fail = 0
        for m in members:
            if not m['discord_id']:
                continue
            try:
                user = (interaction.client.get_user(int(m['discord_id']))
                        or await interaction.client.fetch_user(int(m['discord_id'])))
                await user.send(FRIDAY_DM.format(n=m['left']))
                dm_ok += 1
            except Exception as e:
                dm_fail += 1
                print(f"[remind] DM 실패({m['name']}): {e}")
        note = f'📩 개인 DM 발송 — 성공 {dm_ok}명' + (f' / 실패 {dm_fail}명 (DM 차단 등)' if dm_fail else '')
        await interaction.followup.send(note, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Quest(bot))
