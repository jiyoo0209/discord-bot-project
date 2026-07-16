'''
* 배치 작업
*   [매시간]      설치된 대시보드 메시지 자동 갱신 (명세 #10) + 요약 탭 갱신
*   [금요일 자정] 길퀘 미완료자 리마인드 — 개인 DM + 리마인드 채널 멘션 (사유 등록자 제외)
*   [일요일 자정] 길퀘 미완료자 셋업 채널 리마인드 (기존 유지)
*   [월요일 자정] 지난 주 경고 정산 — 매일완주(7회) -1 · 점령 -1 차감 후, 길퀘 미달 +1
* + /주간경고처리 : 수동 실행 (테스트/보정용)
* author ENI
* date   2026.07.16
'''
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks

from datetime import time as dtime

from sheet.sheet import process_weekly_warnings, get_incomplete_members
from dashboard.dashboard import build_embed, WEEKLY_QUEST_LIMIT, write_summary_sheet
from roleSetting.roleSetting import has_role
from config.guild_config import get_setting, set_setting
from util.dates import is_sunday, is_friday, is_monday, week_range, KST

# 리마인드/정산은 시스템 타임존과 무관하게 **KST 00:00** 에 고정 발송 (KST = util.dates 공용 tz)

# 금요일 리마인드 발송 채널 (멘션 + 남은 횟수 공개 게시)
REMIND_CHANNEL_ID = 1414434882439872553

FRIDAY_DM = ('이번주 길퀘가 {n}번 남았습니다! 일요일까지 완료해주세요. '
             '개인사정이 있을 경우에는 토요일까지 길퀘불가사유를 입력해주세요!')


@app_commands.guild_only()
class Batch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.hourly.start()
        self.midnight.start()

    def cog_unload(self):
        self.hourly.cancel()
        self.midnight.cancel()

    @tasks.loop(hours=1)
    async def hourly(self):
        await self._refresh_dashboards()
        try:
            await asyncio.to_thread(write_summary_sheet)  # 요약 탭도 매시간 최신화
        except Exception as e:
            print(f'[batch] 요약 탭 갱신 실패: {e}')
        # 자정 정각 발송이 실패했어도 그날 안에 매시간 재시도되도록 (가드키로 중복 방지)
        await self._weekly_warning()
        await self._friday_reminder()

    @hourly.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # 매일 KST 자정 정각 실행 — 요일별 주간 배치 트리거
    @tasks.loop(time=dtime(hour=0, minute=0, tzinfo=KST))
    async def midnight(self):
        if is_friday():
            await self._friday_reminder()
        if is_sunday():
            await self._sunday_reminder()
        if is_monday():
            await self._weekly_warning()

    @midnight.before_loop
    async def _before_midnight(self):
        await self.bot.wait_until_ready()

    # 설치된 상주 대시보드(/대시보드설치) 를 최신 데이터로 edit.
    #   신버전 = V2 페이지 카드(현재 페이지 유지) / 구버전 설치분 = embed 폴백
    #   (Discord 는 보낸 뒤 일반 메시지 ↔ V2 전환이 안 되므로 모양별로 맞춰 edit 해야 함).
    async def _refresh_dashboards(self):
        from panel.panel import DashboardCard, current_page   # 지역 import — 순환 방지
        from sheet.sheet import get_dashboard_records
        records = None
        for guild in self.bot.guilds:
            ch_id = get_setting(guild.id, '_dash_ch')
            msg_id = get_setting(guild.id, '_dash_msg')
            if not ch_id or not msg_id:
                continue
            try:
                channel = guild.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
                message = await channel.fetch_message(int(msg_id))
                if message.flags.components_v2:
                    if records is None:   # 여러 길드여도 시트 조회는 1회
                        records = await asyncio.to_thread(get_dashboard_records)
                    await message.edit(view=DashboardCard(current_page(message), records, live=True))
                else:
                    embed = await asyncio.to_thread(build_embed)
                    await message.edit(embed=embed)
            except discord.NotFound:
                # 채널/메시지가 삭제됨 → 등록 해제해서 매시간 404 반복 안 나게
                set_setting(guild.id, '_dash_ch', '')
                set_setting(guild.id, '_dash_msg', '')
                print(f'[batch] 대시보드 대상 삭제됨 → 등록 해제 (다시 /대시보드설치 하면 재등록)')
            except Exception as e:
                print(f'[batch] 대시보드 갱신 실패(guild {guild.id}): {e}')

    # 월요일 1회: '저번 주(월~일)' 기준 경고 정산.
    #   차감(주7회 매일완주 -1, 점령 참여 -1, 경고 0이면 미적용) 후 → 길퀘 미달(사유 미등록) +1
    #   이번 주 월요일 int 를 가드키로 중복 실행 방지. 시트 실패 시엔 가드 미설정 → 다음 tick 재시도.
    async def _weekly_warning(self):
        if not is_monday():
            return
        week_id = week_range()[0]
        if get_setting('_global', '_warn_week') == week_id:
            return
        ok, msg = await asyncio.to_thread(process_weekly_warnings, WEEKLY_QUEST_LIMIT)
        if ok:
            set_setting('_global', '_warn_week', week_id)
        print(f'[batch] {msg}')

    # 금요일 1회: 이번 주 길퀘 미완료자(사유 등록자 제외)에게
    #   1) 개인 DM 리마인드 2) 리마인드 채널에 멘션 + 남은 횟수 목록 게시 (주 1회 가드)
    async def _friday_reminder(self):
        if not is_friday():
            return
        week_id = week_range()[0]
        if get_setting('_global', '_fri_remind_week') == week_id:
            return
        # get_incomplete_members: 탈퇴 제외 + 이번 주 길퀘불가사유 등록자 제외 + left>0 만 반환
        members = await asyncio.to_thread(get_incomplete_members, WEEKLY_QUEST_LIMIT)
        if members is None:
            return  # 시트 조회 실패 → 가드 안 세팅, 다음 tick 재시도
        set_setting('_global', '_fri_remind_week', week_id)  # 정상 조회됨 → 주1회 확정
        if not members:
            return

        # 1) 개인 DM — discord_id 등록자만. DM 차단(Forbidden) 등 개별 실패는 건너뛰고 계속.
        dm_ok = dm_fail = 0
        for m in members:
            if not m['discord_id']:
                continue
            try:
                user = self.bot.get_user(int(m['discord_id'])) or await self.bot.fetch_user(int(m['discord_id']))
                await user.send(FRIDAY_DM.format(n=m['left']))
                dm_ok += 1
            except Exception as e:
                dm_fail += 1
                print(f"[batch] 금요 DM 실패({m['name']}): {e}")

        # 2) 리마인드 채널 — 멘션 + 각자 남은 횟수 목록 (2000자 초과 방지 줄 단위 분할)
        lines = [(f"<@{m['discord_id']}>" if m['discord_id'] else m['name']) + f" — **{m['left']}회** 남음"
                 for m in members]
        pings = discord.AllowedMentions(users=True)
        try:
            channel = (self.bot.get_channel(REMIND_CHANNEL_ID)
                       or await self.bot.fetch_channel(REMIND_CHANNEL_ID))
            cur = '⏰ **이번주 길퀘 리마인드** — 일요일까지 완료해주세요!'
            for line in lines:
                if len(cur) + 1 + len(line) > 1900:
                    await channel.send(cur, allowed_mentions=pings)
                    cur = line
                else:
                    cur += '\n' + line
            await channel.send(cur, allowed_mentions=pings)
        except Exception as e:
            print(f'[batch] 금요 리마인드 채널 발송 실패: {e}')
        print(f'[batch] 금요 리마인드 완료 — 대상 {len(members)}명 (DM 성공 {dm_ok} / 실패 {dm_fail})')

    # 일요일 자정 1회: 이번 주(월~일, 마지막 날 시작 시점) 길퀘 미완료자에게 셋업 채널로 자동 리마인드
    #   주간이 일요일까지라 '오늘(일요일) 안에 마저 하라'는 의미가 됨 (주 1회 가드)
    async def _sunday_reminder(self):
        week_id = week_range()[0]
        if get_setting('_global', '_remind_week') == week_id:
            return
        members = await asyncio.to_thread(get_incomplete_members, WEEKLY_QUEST_LIMIT)
        if members is None:
            return  # 시트 조회 실패 → 가드 안 세팅, 다음 tick 재시도 (그 주 통째 스킵 방지)
        set_setting('_global', '_remind_week', week_id)  # 정상 조회됨 → 주1회 확정
        if not members:
            return
        tags = [(f"<@{m['discord_id']}>" if m['discord_id'] else m['name']) for m in members]
        pings = discord.AllowedMentions(users=True)
        # 데이터는 시트 1개(=한 길드) → 여러 서버 중복 발송 금지. 설정된 '첫' 채널에만.
        for guild in self.bot.guilds:
            ch_id = get_setting(guild.id, '_setup_ch')
            if not ch_id:
                continue
            try:
                channel = guild.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
                cur = '⏰ **이번주 남은 길퀘가 있어요!**'
                for t in tags:                       # 2000자 초과 방지 분할
                    if len(cur) + 1 + len(t) > 1900:
                        await channel.send(cur, allowed_mentions=pings)
                        cur = t
                    else:
                        cur += ' ' + t
                await channel.send(cur, allowed_mentions=pings)
            except Exception as e:
                print(f'[batch] 리마인드 실패(guild {guild.id}): {e}')
            break  # 첫 설정 길드에만 발송

    # 수동 실행 (월요일 안 기다리고 테스트/보정) — 저번 주 기준으로 정산됨에 유의
    @app_commands.command(name='주간경고처리', description='(길마) 지난 주 경고 정산(부여/차감) 수동 실행')
    async def run_warning(self, interaction: discord.Interaction):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(process_weekly_warnings, WEEKLY_QUEST_LIMIT)
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Batch(bot))